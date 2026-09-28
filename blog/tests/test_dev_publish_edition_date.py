import json, tempfile, unittest
from pathlib import Path
import subscription_dev_publish as p


class EditionDateTests(unittest.TestCase):
    def test_comparison_root_uses_smn_daily_state_date(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)/'2026-09-28-claude'; root.mkdir()
            with self.assertRaises(ValueError): p.edition_date(root)
            (root/'smn-daily-state.json').write_text(json.dumps({'date': '2026-09-28'}))
            self.assertEqual(p.edition_date(root), '2026-09-28')

    def test_dated_root_name_still_works(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)/'2026-09-28'; root.mkdir()
            self.assertEqual(p.edition_date(root), '2026-09-28')


if __name__ == '__main__': unittest.main()
