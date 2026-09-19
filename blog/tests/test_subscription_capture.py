import io, tarfile, tempfile, unittest
from pathlib import Path
import subscription_capture as sc

class CaptureTests(unittest.TestCase):
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
