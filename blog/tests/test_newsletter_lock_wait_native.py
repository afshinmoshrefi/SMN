"""Competing-process regressions for the October 19 pre-POST lease race."""
from contextlib import contextmanager
from datetime import date, timedelta
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import newsletter_reader_snapshot as snapshot
import production_continuity as continuity
from tests.test_newsletter_safe_retry import NewsletterSafeRetryTests, DAY, NOW


HOLDER = """
from pathlib import Path
import sys
from production_continuity import _lock
with _lock(Path(sys.argv[1])):
    print('held', flush=True)
    sys.stdin.readline()
print('released', flush=True)
"""


@unittest.skipUnless(sys.platform.startswith('linux'),
                     'Production uses Linux flock; Windows msvcrt contention reports EACCES')
class NativeNewsletterLeaseTests(NewsletterSafeRetryTests):
    def setUp(self):
        super().setUp()
        self.holders = []
        self.release_queue = []
        self.released_phases = []
        self.addCleanup(self.close_holders)
        self.sender.get_email_groups.side_effect = None
        self.sender.get_email_groups.return_value = {'SMN-DAILY': 'group'}
        self.sender.create_campaign.side_effect = None
        self.sender.create_campaign.return_value = ('123', 'created')
        self.stack.enter_context(patch.object(self.sender, '_build_email_html', return_value='<html/>'))
        self.provider_schedule = self.stack.enter_context(patch.object(
            self.sender, '_schedule_campaign_explicit',
            return_value={'data': {'id': '123', 'status': 'ready'}}))
        self.stack.enter_context(patch.object(snapshot, 'PUBLICATION_LOCK_TIMEOUT', 3.0))

    def hold_publication(self):
        edition = self.root/'controller'/DAY/'chatgpt'
        process = subprocess.Popen([sys.executable, '-B', '-u', '-c', HOLDER, str(edition)],
            cwd=Path(snapshot.__file__).parent, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.holders.append(process)
        self.assertEqual(process.stdout.readline().strip(), 'held')
        self.assertIsNone(process.poll())
        return process

    def release(self, process):
        if process.poll() is None:
            process.stdin.write('release\n')
            process.stdin.flush()

    def close_holders(self):
        for process in self.holders:
            try:
                self.release(process)
                process.wait(timeout=5)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                for stream in [process.stdin, process.stdout, process.stderr]:
                    stream.close()

    def release_on_contention(self, process, phase):
        self.release_queue.append((process, phase))

    @contextmanager
    def count_contention(self):
        original = continuity._lock
        busy = []
        @contextmanager
        def observed(root):
            # Only entry is counted; the protected body is never retried.
            with __import__('contextlib').ExitStack() as stack:
                try:
                    stack.enter_context(original(root))
                except BlockingIOError:
                    busy.append(str(root))
                    if self.release_queue:
                        process, phase = self.release_queue.pop(0)
                        self.released_phases.append(phase)
                        self.release(process)
                    raise
                yield
        with patch.object(continuity, '_lock', observed):
            yield busy

    def test_real_publisher_overlaps_capture_and_reservation_then_restart_reuses_intent(self):
        first = self.hold_publication()
        self.release_on_contention(first, 'capture')
        def groups():
            second = self.hold_publication()
            self.release_on_contention(second, 'reservation')
            return {'SMN-DAILY': 'group'}
        self.sender.get_email_groups.side_effect = groups
        with self.count_contention() as busy:
            self.sender.daily_send(verified_urls=self.urls, edition_date=date.fromisoformat(DAY))
        self.assertGreaterEqual(len(busy), 2)
        self.assertEqual(self.released_phases, ['capture', 'reservation'])
        journal = self.sender.STATE_FILE.read_bytes()
        # daily_send reloads the disk journal, as a fresh invocation does.
        result = self.sender.daily_send(verified_urls=self.urls, edition_date=date.fromisoformat(DAY))
        self.assertEqual(result['status'], 'already_reserved')
        self.assertEqual(self.sender.STATE_FILE.read_bytes(), journal)
        self.sender.create_campaign.assert_called_once()
        self.provider_schedule.assert_called_once()
        record = json.loads(journal)['campaigns']['daily:'+DAY]
        self.assertEqual(record['phase'], 'scheduled')
        self.assertEqual({row['url'] for row in record['reader_snapshot']['article_data']}, self.urls)

    def test_reservation_timeout_keeps_live_owner_and_writes_no_intent(self):
        saved = self.sender.STATE_FILE.read_bytes()
        def groups():
            self.hold_publication()
            return {'SMN-DAILY': 'group'}
        self.sender.get_email_groups.side_effect = groups
        with patch.object(snapshot, 'PUBLICATION_LOCK_TIMEOUT', .15):
            with self.assertRaises(BlockingIOError):
                self.sender.daily_send(verified_urls=self.urls, edition_date=date.fromisoformat(DAY))
        self.assertEqual(self.sender.STATE_FILE.read_bytes(), saved)
        self.assertIsNone(self.holders[-1].poll())
        self.sender.create_campaign.assert_not_called()
        self.provider_schedule.assert_not_called()

    def test_uncertain_provider_creation_after_contention_blocks_restart(self):
        process = self.hold_publication()
        self.release_on_contention(process, 'capture')
        self.sender.create_campaign.side_effect = RuntimeError('POST outcome unknown')
        with self.count_contention(), self.assertRaisesRegex(RuntimeError, 'outcome unknown'):
            self.sender.daily_send(verified_urls=self.urls, edition_date=date.fromisoformat(DAY))
        saved = self.sender.STATE_FILE.read_bytes()
        self.assertEqual(json.loads(saved)['campaigns']['daily:'+DAY]['phase'], 'create_unknown')
        result = self.sender.daily_send(verified_urls=self.urls, edition_date=date.fromisoformat(DAY))
        self.assertEqual(result['status'], 'already_reserved')
        self.assertEqual(self.sender.STATE_FILE.read_bytes(), saved)
        self.sender.create_campaign.assert_called_once()
        self.provider_schedule.assert_not_called()

    def test_timed_out_capture_then_native_retry_after_release_creates_one_campaign(self):
        process = self.hold_publication()
        saved = self.sender.STATE_FILE.read_bytes()
        def child(*args, **kwargs):
            try:
                self.sender.daily_send(verified_urls=self.urls, edition_date=date.fromisoformat(DAY))
            except BlockingIOError:
                return __import__('types').SimpleNamespace(returncode=1)
            return __import__('types').SimpleNamespace(returncode=0)
        self.child.side_effect = child
        with patch.object(snapshot, 'PUBLICATION_LOCK_TIMEOUT', .05):
            self.assertEqual(self.tick(), 1)
        failed = self.marker.read_bytes()
        self.assertEqual(self.sender.STATE_FILE.read_bytes(), saved)
        self.sender.create_campaign.assert_not_called()
        self.release(process)
        process.wait(timeout=5)
        self.assertEqual(self.tick(NOW+timedelta(minutes=6)), 0)
        marker = json.loads(self.marker.read_text())
        self.assertEqual((marker['status'], marker['attempt']), ('completed', 2))
        self.assertEqual((self.marker.parent/marker['retry_history'][0]['archive']).read_bytes(), failed)
        self.assertEqual(self.tick(NOW+timedelta(minutes=12)), 0)
        self.assertEqual(self.child.call_count, 2)
        self.sender.create_campaign.assert_called_once()
        self.provider_schedule.assert_called_once()

    def test_persistent_publisher_exhausts_native_scheduler_bound_without_campaign(self):
        process = self.hold_publication()
        saved = self.sender.STATE_FILE.read_bytes()
        def child(*args, **kwargs):
            try:
                self.sender.daily_send(verified_urls=self.urls, edition_date=date.fromisoformat(DAY))
            except BlockingIOError:
                return __import__('types').SimpleNamespace(returncode=1)
            self.fail('A persistent live owner should prevent a campaign')
        self.child.side_effect = child
        with patch.object(snapshot, 'PUBLICATION_LOCK_TIMEOUT', .05):
            for minutes in [0, 6, 12]:
                self.assertEqual(self.tick(NOW+timedelta(minutes=minutes)), 1)
        marker = json.loads(self.marker.read_text())
        self.assertEqual((marker['status'], marker['attempt']), ('failed', 3))
        self.assertEqual(len(marker['retry_history']), 2)
        old_marker = self.marker.read_bytes()
        self.assertEqual(self.tick(NOW+timedelta(minutes=30)), 0)
        self.assertEqual(self.child.call_count, 3)
        self.assertEqual(self.marker.read_bytes(), old_marker)
        self.assertEqual(self.sender.STATE_FILE.read_bytes(), saved)
        self.assertIsNone(process.poll())
        self.sender.create_campaign.assert_not_called()
        self.provider_schedule.assert_not_called()


def load_tests(loader, tests, pattern):
    names = [name for name in NativeNewsletterLeaseTests.__dict__ if name.startswith('test_')]
    return unittest.TestSuite(NativeNewsletterLeaseTests(name) for name in names)


if __name__ == '__main__':
    unittest.main()
