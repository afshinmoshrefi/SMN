import io, json, tarfile, tempfile, unittest
from unittest.mock import patch
from pathlib import Path
import subscription_capture as sc

class CaptureTests(unittest.TestCase):
    def archive(self):
        posts=[{'symbol':s,'published_date':'2026-09-18T10:00:00'} for s in ('AMD','XLK','SPX','GC','QQQ','HRL')]
        files={'posts.json':json.dumps(posts).encode()}
        files.update({p['symbol']+'/audit/prompt.txt':b'header\n{"meta":{}}\n' for p in posts})
        b=io.BytesIO()
        with tarfile.open(fileobj=b,mode='w:gz') as t:
            for name,data in files.items():
                x=tarfile.TarInfo(name); x.size=len(data); t.addfile(x,io.BytesIO(data))
        return b.getvalue()

    def test_capture_reuse_is_read_only_and_tamper_holds(self):
        with tempfile.TemporaryDirectory() as d, patch.object(sc,'_remote_capture',return_value=self.archive()) as remote:
            root=Path(d)
            self.assertEqual(sc.production(root,'2026-09-18')['status'],'captured')
            self.assertTrue(sc.production(root,'2026-09-18')['idempotent'])
            remote.assert_called_once()
            (root/'production/AMD/engine-payload.json').write_text('changed')
            with self.assertRaises(sc.Held): sc.production(root,'2026-09-18')
            remote.assert_called_once()

    def test_missing_batch_waits_without_partial_capture(self):
        with tempfile.TemporaryDirectory() as d, patch.object(sc,'_remote_capture',side_effect=sc.Waiting):
            root=Path(d)
            self.assertEqual(sc.production(root,'2026-09-19')['status'],'waiting_for_production')
            self.assertEqual(list(root.iterdir()),[])

    def test_partial_capture_holds_before_remote_read(self):
        with tempfile.TemporaryDirectory() as d, patch.object(sc,'_remote_capture') as remote:
            root=Path(d); (root/'production').mkdir()
            with self.assertRaises(sc.Held): sc.production(root,'2026-09-18')
            remote.assert_not_called()

    def test_invalid_calendar_date(self):
        with self.assertRaises(ValueError): sc._date('2026-09-31')

    def test_path_traversal_rejected(self):
        b=io.BytesIO()
        with tarfile.open(fileobj=b,mode='w:gz') as t:
            x=tarfile.TarInfo('../escape'); x.size=1; t.addfile(x,io.BytesIO(b'x'))
        with tempfile.TemporaryDirectory() as d, self.assertRaises(sc.Held): sc._safe_extract(b.getvalue(),Path(d))

    def test_backslash_rejected(self):
        b=io.BytesIO()
        with tarfile.open(fileobj=b,mode='w:gz') as t:
            x=tarfile.TarInfo('a\\b'); x.size=1; t.addfile(x,io.BytesIO(b'x'))
        with tempfile.TemporaryDirectory() as d, self.assertRaises(sc.Held): sc._safe_extract(b.getvalue(),Path(d))

if __name__=='__main__': unittest.main()
