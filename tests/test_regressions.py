import json
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch
from runs import Model, normalize, progress, validate
from sources import Sources, GitHub, Gitea
from actions import Service
from tests.test_runs import run


class Regressions(unittest.TestCase):
    def test_startup_recovery_new_repo_and_fast_failure(self):
        m = Model()
        self.assertNotIn('offer', m.ingest([run()],100))
        self.assertNotIn('offer', m.ingest([run(),replace(run(id='2'),repo='my-org/other')],110))
        state = m.ingest([run(),run('low','3',started=111,ended=140)],150)
        self.assertEqual(state['offer']['tone'],'low')
        self.assertNotIn('offer',Model().ingest([run('low',ended=99)],100))

    def test_empty_endpoint_primes_fast_run(self):
        m=Model()
        m.ingest([],100,{('github','my-org/example'):100})
        self.assertEqual(m.ingest([run('low',started=101,ended=130)],160,
                                 {('github','my-org/example'):160})['offer']['tone'],'low')

    def test_rerun_replaces_failure(self):
        m=Model(); m.ingest([run('low',ended=100)],101); m.panel_open()
        state=m.ingest([replace(run(started=110),attempt=2)],111)
        self.assertEqual(state['chip']['dots'],['run'])
        self.assertEqual(len([i for i in state['panel']['items'] if i['type']=='row']),1)
        self.assertTrue(state['offer']['id'].endswith(':2'))
        self.assertFalse(m.cleared)

    def test_newest_dots_and_active_priority(self):
        rows=[run('low',str(i),started=i,ended=200) for i in range(10)]
        state=Model().ingest(rows+[run(id='active',started=1)],210)
        self.assertEqual(state['chip']['dots'],['run']+['low']*7)
        from runs import build_state
        # Newest success remains visible even when eight older failures exist.
        state=build_state({r.key:r for r in rows+[run('good','new',started=205,ended=220)]},[],221)
        self.assertEqual(state['chip']['dots'][0],'good')

    def test_epoch_queue_conclusions_and_clock_skew(self):
        r=normalize('gitea',{'id':1,'path':'.gitea/workflows/ci.yaml@refs/heads/main','status':'queued',
          'started_at':'1970-01-01T00:00:00Z','completed_at':'1970-01-01T00:00:00Z',
          'created_at':'2026-01-01T00:00:00Z'})
        self.assertEqual((r.workflow,r.workflow_id,r.ended),('ci','ci.yaml',0))
        self.assertGreater(r.started,0)
        self.assertEqual(progress(r,r.started+10,{('gitea',r.repo,r.workflow_id):100}),'busy')
        for conclusion in ('failure','timed_out','startup_failure'):
            self.assertEqual(normalize('github',{'id':1,'conclusion':conclusion}).status,'low')
        for conclusion in ('cancelled','skipped','neutral','action_required','stale'):
            self.assertEqual(normalize('github',{'id':1,'conclusion':conclusion}).status,'done')
        self.assertIn('chip',Model().ingest([run('low',ended=220)],210))

    def test_endpoint_expiry_and_independent_backoff(self):
        with patch('sources.gh_token',return_value='placeholder'), patch('sources.time.time',return_value=100):
            sources=Sources({'github':{'repos':['my-org/good','my-org/bad']}})
            gh=sources.clients['github']; gh.http=Mock(token='placeholder',last_lookup=float('-inf'))
            gh.http.get.return_value={'workflow_runs':[{'id':1,'status':'in_progress','repository':{'full_name':'my-org/bad'}}]}
            sources.poll()
        def response(url):
            if '/bad/' in url: raise RuntimeError('unavailable')
            return {'workflow_runs':[{'id':2,'status':'in_progress','repository':{'full_name':'my-org/good'}}]}
        gh.http.get.side_effect=response
        with patch('sources.time.time',return_value=401):
            rows,errors=sources.poll(force=True)
        self.assertEqual([r.id for r in rows],['2'])
        self.assertIn('github',errors)
        self.assertGreater(sources.schedule['github'][1],sources.schedule['gitea'][1])
        with patch('sources.time.time',return_value=402):
            gh.http.get.reset_mock(); sources.poll(panel=True)
            gh.http.get.assert_not_called()

    def test_missing_lists_credentials_and_bad_config(self):
        with patch('sources.gh_token',return_value='placeholder'):
            sources=Sources({'gitea':{'url':'http://gitea.example.com'}})
            self.assertIn('github',sources.clients)
            self.assertEqual(sources.config_errors,['gitea'])
            gh=GitHub({'repos':['my-org/example']})
        gh.http=Mock(token='placeholder',last_lookup=float('-inf'))
        for data in ({},{'workflow_runs':None}):
            gh.http.get.return_value=data
            self.assertEqual(gh.poll(),[])
        gh.http.token=None
        gh.token_checked=0
        with patch('sources.gh_token',return_value=None) as lookup:
            for now in (100,120,161):
                with patch('sources.time.time',return_value=now):
                    with self.assertRaises(RuntimeError): gh.poll()
            self.assertEqual(lookup.call_count,2)

    def test_gitea_partial_failure_and_other_source_progress(self):
        with patch('sources.gh_token',return_value='placeholder'), patch('sources.keyring_token',return_value='placeholder'), patch('sources.time.time',return_value=100):
            sources=Sources({'github':{'repos':['my-org/example']},'gitea':{'url':'https://gitea.example.com','orgs':['my-org']}})
            gh=sources.clients['github']; gt=sources.clients['gitea']
            gh.http.get=Mock(return_value={'workflow_runs':[]})
            gt.http.get=Mock(return_value={'workflow_runs':[{'id':1,'status':'in_progress'}]})
            sources.poll()
        def gitea_data(url):
            if '/orgs/' in url: raise RuntimeError('unavailable')
            return {'workflow_runs':[{'id':2,'status':'in_progress'}]}
        gt.http.get.side_effect=gitea_data
        gh.http.get.return_value={'workflow_runs':[{'id':3,'status':'in_progress'}]}
        with patch('sources.time.time',return_value=401):
            rows,errors=sources.poll(force=True)
        self.assertEqual({r.id for r in rows},{'2','3'})
        self.assertEqual(errors,['gitea'])
        self.assertEqual(sources.schedule['github'][1],0)
        self.assertEqual(sources.schedule['gitea'][1],1)
        m=Model(); m.ingest([],100,{('gitea','*'):100})
        state=m.ingest([replace(run('low',started=101,ended=140),source='gitea')],150,{('gitea','*'):150})
        self.assertEqual(state['offer']['tone'],'low')

    def test_completed_between_polls_success_duration(self):
        for duration in (40,121):
            m=Model();m.ingest([],100,{('github','my-org/example'):100})
            state=m.ingest([run('good',started=101,ended=101+duration)],250,
                           {('github','my-org/example'):250})
            self.assertEqual('offer' in state,duration>120)

    def test_validation_safe_state_and_activation(self):
        service=Service(True); original=service.state
        with patch('actions.sys.stderr'):
            service.publish({'chip':{'where':'invalid'}})
            self.assertEqual(service.state,original)
            with patch.object(service.model,'state',side_effect=ValueError):
                invocation=Mock()
                service.activate(None,None,None,None,None,Mock(unpack=lambda:('refresh','')),invocation)
                invocation.return_value.assert_called_once_with(None)
        self.assertEqual(service.state,original)
        with self.assertRaises(ValueError): validate({'actions':[{}]*9})

    def test_closed_clock_throttle_and_pruning(self):
        s=Service(True); s.model.ingest([run()],100)
        s.render(110); first=s.state
        s.render(111); self.assertEqual(s.state,first)
        s.panel=True; s.render(112); self.assertNotEqual(s.state,first)
        m=Model(); m.offers.seen={'old':1}; m.cleared={('github','old'):1}
        m.ingest([],90000)
        self.assertFalse(m.offers.seen); self.assertFalse(m.cleared)
