import importlib.util
import json
import logging
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


class VerifiedNewsletterTest(unittest.TestCase):
    def test_only_verified_reader_articles_enter_campaign(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = types.ModuleType('config')
            config.news_root_folder = str(root)
            config.news_website_url = 'https://seasonalmarketnews.com'
            config.smn_from_name = 'SMN'
            config.smn_from_email = 'noreply@example.com'
            email_tools = types.ModuleType('email_tools')
            for name in ('get_email_groups', 'create_campaign', 'schedule_campaign',
                         'future_date_hour_min', 'create_mailerlite_group',
                         'create_subscriber', 'assign_subscriber_to_a_group',
                         'get_subscriber_by_email'):
                setattr(email_tools, name, lambda *args, **kwargs: None)
            ai_tools = types.ModuleType('AI_tools')
            ai_tools.send_openai_prompt = lambda *args, **kwargs: None
            path = Path(__file__).resolve().parents[1]/'send_smn_emails.py'
            spec = importlib.util.spec_from_file_location('tested_smn_email', path)
            module = importlib.util.module_from_spec(spec)
            with patch.dict(sys.modules, {'config': config, 'email_tools': email_tools,
                                          'AI_tools': ai_tools}), patch.object(logging, 'basicConfig'):
                spec.loader.exec_module(module)
            module.STATE_FILE = root/'state.json'
            module.POSTS_JSON = root/'posts.json'
            day = module._today().isoformat()
            selected = 'https://seasonalmarketnews.com/editions/'+day+'/AAA/article.html'
            extra = 'https://seasonalmarketnews.com/articles/other.html'
            module.POSTS_JSON.write_text(json.dumps([
                {'url': selected, 'slug': 'aaa', 'symbol': 'AAA', 'published_date': day+'T12:00:00Z'},
                {'url': extra, 'slug': 'other', 'symbol': 'BBB', 'published_date': day+'T12:00:00Z'}]))
            with patch.object(module, 'get_email_groups', return_value={'SMN-DAILY': 'group'}), \
                    patch.object(module, '_generate_daily_narrative', side_effect=AssertionError('Article model budget exhausted')) as narrative_model, \
                    patch.object(module, '_build_email_html', return_value='<html/>'), \
                    patch.object(module, 'create_campaign', return_value=('campaign', 'now')) as send, \
                    patch.object(module, '_schedule_campaign_explicit', return_value={'data': {'id': 'campaign', 'status': 'ready'}}):
                module.daily_send(verified_urls={selected})
                self.assertEqual(send.call_count, 1)
                narrative_model.assert_not_called()
                subject, narrative = module._verified_daily_narrative([
                    {'symbol': symbol} for symbol in ('OMC', 'MRK', 'VIX', 'WMT', 'NG', 'HPQ')], module.date(2026, 10, 8))
                self.assertNotIn('\u2014', subject + narrative)
                self.assertIn('six articles below: OMC, MRK, VIX, WMT, NG and HPQ.', narrative)
                state = json.loads(module.STATE_FILE.read_text())
                self.assertEqual(state['daily_sent'], [])
                self.assertEqual(state['campaigns']['daily:'+day]['phase'], 'scheduled')
                send.reset_mock()
                module.daily_send(verified_urls={selected})
                send.assert_not_called()
                module.STATE_FILE.write_text(json.dumps({'daily_sent': ['aaa']}))
                with self.assertRaisesRegex(ValueError, 'partly reserved'):
                    module.daily_send(verified_urls={selected, extra})


if __name__ == '__main__':
    unittest.main()
