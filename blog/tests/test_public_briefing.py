import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from article_content_store import ContentError
import public_briefing


class PublicBriefingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / 'briefings' / '2026-10-02-wrap'; self.folder.mkdir(parents=True)
        self.value = {'title': 'Market wrap', 'edition_date': '2026-10-02', 'label': 'Market Wrap',
            'cutoff': '2026-10-02T23:59:59-04:00', 'narrative': [{'text': 'Reviewed narrative.'}],
            'groups': [{'headline': 'Reported event', 'selected': True}],
            'sources': [{'title': 'Official report', 'url': 'https://www.bls.gov/', 'publisher': 'BLS',
                         'capture': {'text': 'PRIVATE_SOURCE_CAPTURE'}}]}
        (self.folder / 'briefing.json').write_text(json.dumps(self.value))
        (self.folder / 'review.json').write_text('{"status":"pending"}')

    def test_pending_or_stale_briefing_is_not_public(self):
        with patch.object(public_briefing, 'inspect', return_value={'issues': [], 'review_status': 'pending'}):
            self.assertEqual(public_briefing.listing(self.root), [])
        with patch.object(public_briefing, 'inspect', return_value={'issues': ['stale'], 'review_status': 'approved'}):
            with self.assertRaises(ContentError): public_briefing.load(self.root, self.folder.name)

    def test_projection_excludes_retained_private_source_material(self):
        with patch.object(public_briefing, 'inspect', return_value={'issues': [], 'review_status': 'approved'}):
            result = public_briefing.load(self.root, self.folder.name)
        self.assertNotIn('PRIVATE_SOURCE_CAPTURE', json.dumps(result))
        self.assertEqual(result['narrative'], self.value['narrative'])
        self.assertIsNone(public_briefing.video(self.root, result))

    def test_public_video_discloses_ai_narration_only_when_present(self):
        from jinja2 import Environment,FileSystemLoader
        environment=Environment(loader=FileSystemLoader(Path(__file__).resolve().parents[1]/'templates'),autoescape=True)
        template=environment.get_template('reader_briefing.html')
        item=dict(self.value,id=self.folder.name,date=self.value['edition_date'])
        self.assertIn('AI-generated narration.',template.render(item=item,has_video=True))
        self.assertNotIn('AI-generated narration.',template.render(item=item,has_video=False))

    def test_short_approved_avatar_cannot_replace_complete_daily_video(self):
        import promotion_jobs as jobs
        from subscription_writer import save_json,sha256
        root=self.root/'promotion';binding='a'*64
        def reviewed(kind,name,body):
            job=jobs.create(root,kind,{'briefing_id':'edition','source_revision':binding,'source_hash':binding},'test')
            folder=jobs._folder(root)/'artifacts'/job['id'];folder.mkdir(parents=True)
            (folder/name).write_bytes(body);artifact={'name':name,'relative_path':name,'media_type':'video/mp4','sha256':sha256(body)}
            return jobs.update(root,job['id'],job['version'],status='reviewed',review_status='approved',
                artifacts=[artifact],review={'artifact_hashes':{name:artifact['sha256']}})
        full=reviewed('daily_briefing','briefing.mp4',b'complete-test-fixture')
        reviewed('daily_avatar','avatar.mp4',b'short-test-fixture')
        selected=public_briefing.video(self.root,{'sha256':binding})
        self.assertEqual(selected.read_bytes(),b'complete-test-fixture')

    def test_unavailable_and_traversal_are_closed(self):
        for identifier in ('missing', '../private', 'x/y'):
            with self.assertRaises(ContentError): public_briefing.load(self.root, identifier)


if __name__ == '__main__': unittest.main()
