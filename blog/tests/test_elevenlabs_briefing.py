import unittest
from unittest.mock import patch

from elevenlabs_briefing import plan
from tests.test_daily_briefing import fixture, review


class ElevenLabsBriefingTests(unittest.TestCase):
    def configuration(self):
        return {'provider': 'elevenlabs', 'voice_id': 'test-voice', 'presenter_reference': 'test-personal-reference',
            'model_id': 'test-model', 'settings': {'resolution': 'test-only'},
            'verification_receipt': 'test-only-account-receipt', 'route': 'manual_avatar', 'max_credits': 10,
            **{k: True for k in ('account_access_verified', 'voice_access_verified', 'likeness_verified',
                'model_access_verified', 'route_verified', 'cost_verified', 'generation_authorized')}}

    def test_missing_account_and_review_hold_without_guessing_ids(self):
        result = plan(fixture(), {})
        self.assertEqual(result['status'], 'held')
        self.assertIsNone(result['steps'][0]['voice_id'])
        self.assertIn('editorial review is pending', result['holds'])

    def test_prepared_plan_never_generates_or_certifies_automation(self):
        data = fixture()
        with patch('socket.socket', side_effect=AssertionError('network')):
            result = plan(data, self.configuration(), review(data))
        self.assertEqual(result['status'], 'offline_plan_ready')
        self.assertEqual(result['generation_status'], 'not_submitted')
        self.assertEqual(result['network_calls'], 0)
        self.assertFalse(result['automated_generation_qualified'])
        self.assertTrue(result['manual_steps'])

    def test_each_prerequisite_and_stale_script_hold(self):
        data = fixture()
        for key in ('voice_access_verified', 'likeness_verified', 'model_access_verified',
                    'route_verified', 'cost_verified', 'generation_authorized'):
            config = self.configuration(); config[key] = False
            self.assertEqual(plan(data, config, review(data))['status'], 'held', key)
        approval = review(data); data['script'][0]['text'] += ' New sentence.'
        self.assertEqual(plan(data, self.configuration(), approval)['status'], 'held')

    def test_api_candidate_requires_qualified_route_and_finite_budget(self):
        data = fixture(); config = self.configuration(); config['route'] = 'flows_video_api'
        result = plan(data, config, review(data))
        self.assertEqual(result['steps'][1]['candidate_endpoint'], '/v1/flows/video')
        self.assertIsNone(result['steps'][1]['submission_payload'])
        for value in (0, -1, True, float('inf')):
            config['max_credits'] = value
            self.assertTrue(plan(data, config, review(data))['holds'])


if __name__ == '__main__':
    unittest.main()
