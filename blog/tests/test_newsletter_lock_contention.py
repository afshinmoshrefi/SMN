"""Offline regressions for the October 14 scheduled publisher/newsletter race."""
from contextlib import contextmanager
import errno
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import newsletter_reader_snapshot as snapshot
import production_continuity as continuity
from tests.test_newsletter_safe_retry import NewsletterSafeRetryTests,DAY
from datetime import date


class Clock:
    def __init__(self):self.elapsed=0.;self.sleeps=[]
    def monotonic(self):return self.elapsed
    def sleep(self,delay):self.sleeps.append(delay);self.elapsed+=delay


class PublicationLeaseTests(unittest.TestCase):
    def test_brief_live_owner_contention_waits_then_enters_body_once(self):
        clock=Clock();events=[];attempts=[]
        @contextmanager
        def lock(root):
            attempts.append(root)
            if len(attempts)<=2:
                events.append('busy')
                raise BlockingIOError(errno.EAGAIN,'Live publisher owns lease')
            events.append('acquired')
            try:yield
            finally:events.append('released')
        with patch.object(continuity,'_lock',lock),patch.object(snapshot.time,'monotonic',clock.monotonic),patch.object(snapshot.time,'sleep',clock.sleep):
            with snapshot._publication_lease(Path('edition')):events.append('body')
        self.assertEqual(events,['busy','busy','acquired','body','released'])
        self.assertEqual(len(attempts),3)
        self.assertEqual(clock.sleeps,[.1,.1])

    def test_timeout_preserves_live_owner_and_never_enters_body(self):
        clock=Clock();attempts=[]
        @contextmanager
        def lock(root):
            attempts.append(root)
            raise BlockingIOError(errno.EAGAIN,'Still owned')
            yield
        with patch.object(continuity,'_lock',lock),patch.object(snapshot,'PUBLICATION_LOCK_TIMEOUT',.25),patch.object(snapshot.time,'monotonic',clock.monotonic),patch.object(snapshot.time,'sleep',clock.sleep):
            with self.assertRaises(BlockingIOError):
                with snapshot._publication_lease(Path('edition')):self.fail('Lease was never acquired')
        self.assertEqual(len(attempts),4)
        self.assertAlmostEqual(clock.elapsed,.25)
        self.assertAlmostEqual(clock.sleeps[-1],.05)

    def test_body_error_is_propagated_without_reacquisition_or_replay(self):
        clock=Clock();events=[]
        @contextmanager
        def lock(root):
            events.append('acquired')
            try:yield
            finally:events.append('released')
        with patch.object(continuity,'_lock',lock),patch.object(snapshot.time,'monotonic',clock.monotonic),patch.object(snapshot.time,'sleep',clock.sleep):
            with self.assertRaisesRegex(BlockingIOError,'Protected body failed'):
                with snapshot._publication_lease(Path('edition')):
                    events.append('body')
                    raise BlockingIOError(errno.EAGAIN,'Protected body failed')
        self.assertEqual(events,['acquired','body','released'])
        self.assertEqual(clock.sleeps,[])

    def test_permission_error_is_not_mistaken_for_live_owner_contention(self):
        clock=Clock()
        @contextmanager
        def lock(root):
            raise PermissionError('Unreadable lease')
            yield
        with patch.object(continuity,'_lock',lock),patch.object(snapshot.time,'monotonic',clock.monotonic),patch.object(snapshot.time,'sleep',clock.sleep):
            with self.assertRaises(PermissionError):
                with snapshot._publication_lease(Path('edition')):self.fail('Cannot enter')
        self.assertEqual(clock.sleeps,[])


class NewsletterContentionTests(NewsletterSafeRetryTests):
    """Reuse the real publication fixture and mocked provider used by retry tests."""
    def test_released_publication_lease_creates_exactly_one_bound_campaign(self):
        clock=Clock();attempts=[];original=continuity._lock
        @contextmanager
        def lock(root):
            attempts.append(root)
            if len(attempts)<=2:raise BlockingIOError(errno.EAGAIN,'Minute-boundary publisher')
            with original(root):yield
        self.sender.get_email_groups.side_effect=None
        self.sender.get_email_groups.return_value={'SMN-DAILY':'group'}
        self.sender.create_campaign.side_effect=None
        self.sender.create_campaign.return_value=('123','now')
        with patch.object(continuity,'_lock',lock),patch.object(snapshot.time,'monotonic',clock.monotonic),patch.object(snapshot.time,'sleep',clock.sleep),patch.object(self.sender,'_build_email_html',return_value='<html/>'),patch.object(self.sender,'_schedule_campaign_explicit',return_value={'data':{'id':'123','status':'ready'}}):
            self.sender.daily_send(verified_urls=self.urls,edition_date=date.fromisoformat(DAY))
            # An interrupted/repeated invocation still cannot create another campaign.
            self.sender.daily_send(verified_urls=self.urls,edition_date=date.fromisoformat(DAY))
        self.sender.create_campaign.assert_called_once()
        self.assertEqual(clock.sleeps,[.1,.1])
        import json
        record=json.loads(self.sender.STATE_FILE.read_text())['campaigns']['daily:'+DAY]
        self.assertEqual(record['campaign_id'],'123')
        self.assertEqual({row['url'] for row in record['reader_snapshot']['article_data']},self.urls)

    def test_contended_capture_times_out_before_intent_or_provider_call(self):
        clock=Clock();saved=self.sender.STATE_FILE.read_bytes()
        @contextmanager
        def lock(root):
            raise BlockingIOError(errno.EAGAIN,'Publisher still active')
            yield
        with patch.object(continuity,'_lock',lock),patch.object(snapshot,'PUBLICATION_LOCK_TIMEOUT',.25),patch.object(snapshot.time,'monotonic',clock.monotonic),patch.object(snapshot.time,'sleep',clock.sleep):
            with self.assertRaises(BlockingIOError):
                self.sender.daily_send(verified_urls=self.urls,edition_date=date.fromisoformat(DAY))
        self.assertEqual(self.sender.STATE_FILE.read_bytes(),saved)
        self.sender.get_email_groups.assert_not_called()
        self.sender.create_campaign.assert_not_called()


def load_tests(loader,tests,pattern):
    # The existing retry module owns its inherited cases; run only six new cases.
    suite=loader.loadTestsFromTestCase(PublicationLeaseTests)
    for name in ['test_released_publication_lease_creates_exactly_one_bound_campaign',
                 'test_contended_capture_times_out_before_intent_or_provider_call']:
        suite.addTest(NewsletterContentionTests(name))
    return suite

if __name__=='__main__':unittest.main()
