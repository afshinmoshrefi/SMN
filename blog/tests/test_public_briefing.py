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

    def test_unavailable_and_traversal_are_closed(self):
        for identifier in ('missing', '../private', 'x/y'):
            with self.assertRaises(ContentError): public_briefing.load(self.root, identifier)


if __name__ == '__main__': unittest.main()
