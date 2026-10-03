import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from daily_briefing import digest, inspect, package
from subscription_writer import sha256


def fixture():
    text = 'The agency reported unchanged output in September.'
    return {'version': 1, 'edition_date': '2026-10-02', 'timezone': 'America/New_York',
            'cutoff': '2026-10-02T17:00:00-04:00', 'label': 'Market Wrap', 'title': 'Test briefing',
            'scan': [], 'sources': [{'id': 'agency', 'title': 'Release',
                'url': 'https://example.org/release', 'publisher': 'Agency', 'origin_id': 'agency',
                'published_at': '2026-10-02T08:30:00-04:00', 'retrieved_at': '2026-10-02T09:00:00-04:00',
                'access': 'primary_release', 'capture': {'text': text, 'text_sha256': sha256(text.encode()),
                'scope': 'complete_document'}}],
            'groups': [{'id': 'economy', 'event_key': 'september-output', 'headline': 'Output unchanged',
                'selected': True, 'reason': 'Material economic release', 'source_ids': ['agency']}],
            'claims': [{'id': 'output', 'group_id': 'economy', 'text': 'Output was unchanged.',
                'kind': 'fact', 'event_at': '2026-10-02T08:30:00-04:00', 'event_status': 'reported',
                'supports': [{'source_id': 'agency', 'quote': text, 'locator': 'Opening paragraph'}]}],
            'narrative': [{'text': 'Output was unchanged.', 'claim_ids': ['output']}],
            'script': [{'text': 'Output was unchanged.', 'claim_ids': ['output']}],
            'storyboard': [{'text': 'Output headline card', 'claim_ids': ['output'], 'visual_kind': 'headline_card'}]}


def review(data):
    return {'status': 'approved', 'briefing_sha256': digest(data), 'reviewer': 'Test reviewer',
            'reviewed_at': '2026-10-02T18:00:00-04:00'}


def headline_fixture():
    from briefing_daily import headline_bundle
    from daily_briefing import HEADLINE_NOTE
    data=fixture();src=data['sources'][0]
    src.update(title='Agency output holds steady at 4.2%',publisher='CNBC',url='https://www.cnbc.com/2026/10/02/output.html')
    discovery={'retrieved_at':src['retrieved_at'],'sources':[src],'scan':[],
               'files':{'cnbc.discovery':'a'*64}}
    bundle=headline_bundle(discovery,data['edition_date'],data['cutoff'],data['label'])
    data.update(sources=bundle['sources'],mode='headline_roundup',coverage_note=HEADLINE_NOTE)
    claim=data['claims'][0];claim.update(text='CNBC headline: '+src['title'],attribution='CNBC')
    claim['supports']=[{'source_id':'agency','quote':src['title'],'locator':'official feed headline'}]
    for surface in ('narrative','script','storyboard'):
        data[surface][0]['text']='CNBC headlines describe steady output at 4.2%. '+HEADLINE_NOTE
    return data


class DailyBriefingTests(unittest.TestCase):
    def test_official_headline_evidence_is_narrow_attributed_and_pending(self):
        self.assertEqual(inspect(headline_fixture())['issues'],[])
        self.assertEqual(inspect(headline_fixture())['status'],'pending_editorial_review')

    def test_headline_mutation_fullstory_claim_and_invented_number_hold(self):
        changes=[('title','Invented revised headline'),('access','full_text')]
        for key,value in changes:
            data=headline_fixture();data['sources'][0][key]=value
            self.assertTrue(inspect(data)['issues'])
        for field,value in [('text','Output rose because of policy.'),('attribution','Reuters'),
                            ('event_at','2026-10-02T08:31:00-04:00')]:
            data=headline_fixture();data['claims'][0][field]=value
            self.assertTrue(inspect(data)['issues'])
        data=headline_fixture();data['script'][0]['text']+=' Output reached 9.9%.'
        self.assertTrue(any('number absent' in e for e in inspect(data)['issues']))
        data=headline_fixture();data['sources'][0]['capture']['feed_url']='https://evil.test/rss'
        self.assertTrue(inspect(data)['issues'])
        data=headline_fixture();data['sources'][0]['capture']['record_sha256']='b'*64
        self.assertTrue(inspect(data)['issues'])

    def test_no_ticker_chart_or_fixed_story_count_and_honest_pending_status(self):
        result = inspect(fixture())
        self.assertEqual(result['issues'], [])
        self.assertEqual(result['status'], 'pending_editorial_review')
        self.assertEqual(result['generation_status'], 'disabled')
        self.assertTrue(any('CNBC' in w for w in result['warnings']))

    def test_real_example_is_pending_and_has_three_supported_groups(self):
        path = Path(__file__).parents[1] / 'examples/daily-briefing/2026-10-02.json'
        result = inspect(json.loads(path.read_text(encoding='utf-8')))
        self.assertTrue(any('timestamp is uncertain' in e for e in result['issues']))
        self.assertEqual(len(result['event_coverage']), 3)
        self.assertEqual(result['status'], 'held')

    def test_date_and_explicit_offset_failures(self):
        for value in ('2026-10-03T17:00:00-04:00', '2026-10-02T17:00:00'):
            data = fixture(); data['cutoff'] = value
            self.assertTrue(inspect(data)['issues'])
        data = fixture(); data['cutoff'] = '2026-10-03T00:30:00Z'
        self.assertEqual(inspect(data)['issues'], [])  # still October 2 in New York

    def test_source_after_cutoff_and_changed_capture_hold(self):
        data = fixture(); data['sources'][0]['updated_at'] = '2026-10-02T18:00:00-04:00'
        self.assertTrue(any('after cutoff' in e for e in inspect(data)['issues']))
        data = fixture(); data['sources'][0]['capture']['text'] += ' changed'
        self.assertTrue(any('hash mismatch' in e for e in inspect(data)['issues']))

    def test_uncertain_timestamp_and_impossible_source_version_hold(self):
        data = fixture(); source = data['sources'][0]
        source.update(published_at=None, published_at_raw='Fri, 02nd Oct 2026 21:23', timestamp_status='uncertain')
        self.assertTrue(any('cutoff qualification held' in e for e in inspect(data)['issues']))
        data = fixture(); data['sources'][0]['updated_at'] = '2026-10-02T10:00:00-04:00'
        self.assertTrue(any('retrieved before this source version' in e for e in inspect(data)['issues']))

    def test_snippets_and_fabricated_quotes_cannot_support_claims(self):
        for access in ('snippet', 'metadata', 'unavailable'):
            data = fixture(); data['sources'][0]['access'] = access
            self.assertTrue(any('cannot support' in e for e in inspect(data)['issues']))
        for quote in ('invented result', '   '):
            data = fixture(); data['claims'][0]['supports'][0]['quote'] = quote
            self.assertTrue(inspect(data)['issues'])

    def test_syndication_is_one_origin_and_duplicate_event_is_held(self):
        data = fixture(); duplicate = copy.deepcopy(data['sources'][0])
        duplicate.update(id='republished', publisher='Second publisher', url='https://example.net/reprint')
        data['sources'].append(duplicate); data['groups'][0]['source_ids'].append('republished')
        self.assertEqual(inspect(data)['event_coverage']['economy']['distinct_reporting_origins'], 1)
        data['groups'].append(dict(data['groups'][0], id='duplicate-group'))
        self.assertTrue(any('duplicate underlying event' in e for e in inspect(data)['issues']))

    def test_upcoming_results_and_background_are_distinct(self):
        data = fixture(); data['claims'][0]['event_at'] = '2026-11-06T08:30:00-05:00'
        self.assertTrue(any('future event' in e for e in inspect(data)['issues']))
        data['claims'][0]['event_status'] = 'upcoming'
        self.assertEqual(inspect(data)['issues'], [])
        data['claims'][0]['event_at'] = '2026-10-01T08:30:00-04:00'
        self.assertTrue(inspect(data)['issues'])
        data['claims'][0]['event_status'] = 'background'
        self.assertEqual(inspect(data)['issues'], [])

    def test_unknown_refs_and_source_visual_fail(self):
        data = fixture(); data['script'][0]['claim_ids'] = ['unknown']
        self.assertTrue(inspect(data)['issues'])
        data = fixture(); data['storyboard'][0]['visual_kind'] = 'source_visual'
        self.assertTrue(inspect(data)['issues'])

    def test_stale_or_rejected_review_cannot_approve_revision(self):
        data = fixture(); approval = review(data)
        self.assertEqual(inspect(data, approval)['status'], 'reviewed')
        data['script'][0]['text'] += ' Revised.'
        self.assertTrue(any('stale' in e for e in inspect(data, approval)['issues']))
        data = fixture(); rejection = dict(review(data), status='rejected')
        self.assertEqual(inspect(data, rejection)['status'], 'held')
        self.assertEqual(inspect(data, rejection)['review_status'], 'rejected')

    def test_review_cannot_predate_evidence_capture(self):
        data = fixture(); approval = review(data)
        approval['reviewed_at'] = '2026-10-02T08:00:00-04:00'
        self.assertTrue(any('predates' in e for e in inspect(data, approval)['issues']))

    def test_private_packaging_is_immutable_and_has_no_network_side_effects(self):
        with tempfile.TemporaryDirectory() as folder, patch('socket.socket', side_effect=AssertionError('network')):
            target = Path(folder) / 'revision'
            package(fixture(), target)
            receipt = json.loads((target / 'receipt.json').read_text())
            self.assertFalse(receipt['publish']); self.assertFalse(receipt['media_generated'])
            self.assertEqual(receipt['files']['review.html'], sha256((target / 'review.html').read_bytes()))
            with self.assertRaises(FileExistsError):
                package(fixture(), target)

    def test_held_artifact_requires_explicit_option_and_stays_held(self):
        data = fixture(); data['sources'][0]['timestamp_status'] = 'uncertain'
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'held'
            with self.assertRaises(ValueError):
                package(data, target)
            result = package(data, target, allow_held=True)
            self.assertEqual(result['status'], 'held')
            self.assertIn('HELD:', (target / 'review.html').read_text())

    def test_held_export_never_links_an_unsafe_source_url(self):
        data = fixture(); data['sources'][0]['url'] = 'javascript:alert(1)'
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'held'
            package(data, target, allow_held=True)
            document = (target / 'review.html').read_text()
            self.assertNotIn('href="javascript:', document)
            self.assertIn('HELD:', document)

    def test_uncertain_time_can_use_actual_bound_capture_before_cutoff(self):
        data=fixture(); source=data['sources'][0]
        source.update(published_at=None, published_at_raw='October 2, timezone absent',
                      timestamp_status='uncertain', cutoff_basis='observed_capture')
        source['capture']['observation_sha256']=digest({'url':source['url'],
            'retrieved_at':source['retrieved_at'],'text_sha256':source['capture']['text_sha256']})
        result=inspect(data)
        self.assertEqual(result['issues'],[])
        self.assertTrue(any('remains uncertain' in w for w in result['warnings']))
        source['capture']['observation_sha256']='a'*64
        self.assertTrue(inspect(data)['issues'])
        source['retrieved_at']='2026-10-03T03:59:00Z'
        source['capture']['observation_sha256']=digest({'url':source['url'],
            'retrieved_at':source['retrieved_at'],'text_sha256':source['capture']['text_sha256']})
        self.assertTrue(inspect(data)['issues'])


if __name__ == '__main__':
    unittest.main()
