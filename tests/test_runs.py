import json
import unittest
from dataclasses import replace
from unittest.mock import Mock
from runs import Run, Model, Offers, normalize, build_state, validate, diff
from sources import Sources


def run(status='run', id='1', started=100, ended=0):
    return Run('github',id,'my-org/example','Tests','main',status,started,ended,
               'https://github.com/my-org/example/actions/runs/1','tests')


class CoreTests(unittest.TestCase):
    def test_empty(self):
        self.assertNotIn('chip',Model().state(100))

    def test_single_and_several(self):
        m = Model()
        state = m.ingest([run(),run('queued','2')],110)
        self.assertEqual(state['chip']['dots'],['run','queued'])
        self.assertIn('1 queued',state['chip']['label'])
        self.assertEqual(state['panel']['items'][1]['subtitle'],'main · queued')
        self.assertNotIn('offer',state)
        validate(state)

    def test_failure_linger_and_clear(self):
        m = Model()
        m.ingest([run()],200)
        state = m.ingest([run('low',ended=210)],210)
        self.assertEqual(state['offer']['tone'],'low')
        self.assertIn('chip',m.state(809))
        self.assertNotIn('chip',m.state(810))
        m.panel_open()
        self.assertNotIn('chip',m.state(211))

    def test_pass_duration_and_linger(self):
        for duration in (60,121):
            m = Model()
            m.ingest([run()],100)
            state = m.ingest([run('good',ended=100+duration)],100+duration)
            self.assertEqual('offer' in state,duration > 120)
            self.assertIn('chip',state)
            self.assertNotIn('chip',m.state(130+duration))

    def test_queue_priority_and_stale(self):
        offers = Offers()
        r = run()
        failed = run('low','2',ended=200)
        rows = {r.key:r,failed.key:failed}
        card = offers.update([('start',r),('low',failed)],rows,200,{})
        self.assertEqual(card['tone'],'low')
        self.assertEqual(len(offers.queue),1)
        self.assertIsNone(offers.update([],{},210,{}))
        self.assertFalse(offers.queue)
        offers.update([('start',run(id='3'))],{run(id='3').key:run(id='3')},211,{})
        self.assertEqual(offers.last,211)

    def test_progress_and_unique_attempt(self):
        m = Model()
        m.ingest([run('good','old',ended=300)],400)
        state = m.ingest([run(started=400)],500)
        self.assertEqual(state['panel']['items'][0]['ring'],.5)
        self.assertEqual(m.state(1000)['panel']['items'][0]['ring'],.95)
        self.assertTrue(diff({run().key:run()},{replace(run(),attempt=2).key:replace(run(),attempt=2)},200))

    def test_vocabularies(self):
        for source in ('github','gitea'):
            for raw,status in [('in_progress','run'),('running','run'),('waiting','queued'),('queued','queued'),('pending','queued'),('success','good'),('failure','low'),('cancelled','done')]:
                self.assertEqual(normalize(source,{'id':1,'status':raw}).status,status)
        self.assertEqual(normalize('github',{'id':1,'status':'completed','conclusion':'failure'}).status,'low')

    def test_limits(self):
        rows = [replace(run(id=str(i)),repo='😀'*10000,workflow='x'*10000,branch='y'*10000) for i in range(50)]
        state = build_state({r.key:r for r in rows},[],200,errors=['gitea'])
        validate(state)
        self.assertEqual(len(state['chip']['dots']),8)
        self.assertLessEqual(len(state['panel']['items']),12)
        self.assertLessEqual(len(json.dumps(state).encode()),16384)
        for bad in ({'chip':{'text':'x'*13}}, {'chip':{'dots':['run']*9}}, {'panel':{'items':[{}]*13}}, {'actions':[{}]*9}):
            with self.assertRaises(ValueError): validate(bad)

    def test_source_failure_preserves_other_and_previous(self):
        from unittest.mock import patch
        with patch('sources.gh_token', return_value='placeholder'):
            sources = Sources({'github':{'repos':['my-org/example']}})
        good,bad = Mock(),Mock()
        good.poll.return_value = [run()]
        good.errors = []
        good.snapshots = {}
        bad.poll.side_effect = RuntimeError('private detail')
        bad.snapshots = {}
        old = replace(run(),source='gitea')
        sources.clients = {'github':good,'gitea':bad}
        sources.last = {'gitea':[old]}
        sources.last_success = {'gitea':100}
        with patch('sources.time.time', return_value=110):
            rows,errors = sources.poll()
        self.assertEqual(len(rows),2)
        self.assertEqual(errors,['gitea'])
        with patch('sources.time.time', return_value=401):
            rows,errors = sources.poll()
        self.assertEqual(len(rows),1)

    def test_gitea_documented_fixture(self):
        with open('tests/gitea.json') as f: data = json.load(f)
        r = normalize('gitea',data['workflow_runs'][0])
        self.assertEqual(r.status,'run')
        self.assertGreater(r.started,0)
        self.assertEqual(r.repo,'my-org/example')
        validate(Model().ingest([r],r.started+10))


if __name__ == '__main__': unittest.main()
