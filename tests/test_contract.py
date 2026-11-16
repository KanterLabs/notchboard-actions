"""Notchboard contract v1 details found by running against the real host."""
import unittest
from runs import Model, Run, uri, build_state, Offers


def run(status='run', id='1', branch='main', started=100, ended=0, url='https://github.com/my-org/example/actions/runs/1'):
    return Run('github', id, 'my-org/example', 'Tests', branch, status, started, ended, url, 'tests')


class ContractTests(unittest.TestCase):
    def test_uri_matches_host_rule(self):
        for good in ('https://github.com/o/r', 'http://localhost:3000/x', 'https://g.example/a?b=@c'):
            self.assertEqual(uri(good), good)
        for bad in ('https://user:pw@g.example/x', 'http:///x', 'https://g.example/a b',
                    'ftp://g.example/x', 'https://' + 'x' * 2050, ''):
            self.assertEqual(uri(bad), '')

    def test_bad_link_is_dropped_not_the_document(self):
        state = build_state({('github', '1'): run(url=uri('https://u@g.example/x'))}, [], 110)
        self.assertNotIn('uri', state['panel']['items'][0])

    def test_offer_gap_uses_real_time(self):
        o = Offers()
        o.update([('low', run('low', '1', ended=100))], {('github', '1'): run('low', '1', ended=100)}, 104, {})
        runs = {('github', k): run('low', k, ended=100) for k in '12'}
        # 10 quantized seconds later but only 6 real seconds: no new card yet.
        self.assertTrue(o.update([('low', run('low', '2', ended=100))], runs, 110, {})['id'].startswith('low:github:1'))
        self.assertTrue(o.update([], runs, 115, {})['id'].startswith('low:github:2'))

    def test_result_cards_have_an_icon_and_no_empty_branch(self):
        m = Model()
        m.ingest([run(branch='')], 200)
        state = m.ingest([run('low', branch='', ended=210)], 210)
        self.assertEqual(state['offer']['icon'], 'dialog-error-symbolic')
        self.assertEqual(state['offer']['title'], 'Tests failed')
        self.assertEqual(state['offer']['text'], 'example · after 1:50')
        self.assertEqual(state['panel']['items'][0]['subtitle'], '1:50')

    def test_unknown_start_time_has_no_clock(self):
        state = build_state({('github', '1'): run(started=0)}, [], 1_700_000_000)
        self.assertEqual(state['panel']['items'][0]['subtitle'], 'main · running')


class DemoTests(unittest.TestCase):
    def test_demo_cycle_produces_start_failure_and_pass(self):
        import actions
        m = Model()
        kinds = []
        for tick in range(0, 70):
            now = 1000 + tick * 0.5
            offer = m.ingest(actions.demo_runs(1000, now), int(now // 5) * 5, None, now).get('offer')
            if offer and offer['id'].split(':')[0] not in kinds:
                kinds.append(offer['id'].split(':')[0])
        self.assertEqual(kinds, ['start', 'low', 'good'])


if __name__ == '__main__':
    unittest.main()
