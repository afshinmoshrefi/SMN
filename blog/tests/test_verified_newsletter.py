import importlib.util
import json
import logging
import sys
import tempfile
import types
import unittest
from datetime import date
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
            day = date.today().isoformat()
            selected = 'https://seasonalmarketnews.com/editions/'+day+'/AAA/article.html'
            extra = 'https://seasonalmarketnews.com/articles/other.html'
            module.POSTS_JSON.write_text(json.dumps([
                {'url': selected, 'slug': 'aaa', 'published_date': day+'T12:00:00Z'},
                {'url': extra, 'slug': 'other', 'published_date': day+'T12:00:00Z'}]))
            with patch.object(module, 'get_email_groups', return_value={'SMN-DAILY': 'group'}), \
                    patch.object(module, '_generate_daily_narrative', return_value=('Subject', 'Narrative')), \
                    patch.object(module, '_build_email_html', return_value='<html/>'), \
                    patch.object(module, '_create_and_schedule', return_value='campaign') as send:
                module.daily_send(verified_urls={selected})
                self.assertEqual(send.call_count, 1)
                self.assertEqual(json.loads(module.STATE_FILE.read_text())['daily_sent'], ['aaa'])
                send.reset_mock()
                module.daily_send(verified_urls={selected})
                send.assert_not_called()
                module.STATE_FILE.write_text(json.dumps({'daily_sent': ['aaa']}))
                with self.assertRaisesRegex(ValueError, 'partly emailed'):
                    module.daily_send(verified_urls={selected, extra})


if __name__ == '__main__':
    unittest.main()
