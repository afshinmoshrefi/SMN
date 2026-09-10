import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import install_smn_recovery_edition as deploy


class RecoveryGuards(unittest.TestCase):
    def package(self,root):
        date='2026-09-10';commit='a'*40;entries=[];files={}
        for sym in ['HRL','TRV','IBM','KDP','KMB','JNJ']:
            rel=f'editions/{date}/{sym}/article.html';path=root/rel;path.parent.mkdir(parents=True);path.write_text(sym)
            files[rel]=hashlib.sha256(path.read_bytes()).hexdigest()
            entries.append({'symbol':sym,'url':deploy.ORIGIN+'/'+rel,'source_commit':commit})
        (root/'entries.json').write_text(json.dumps(entries))
        files['entries.json']=deploy.sha(root/'entries.json')
        m={'edition_date':date,'source_commit':commit,'target_origin':deploy.ORIGIN,'production_allowed':False,'files':files}
        (root/'manifest.json').write_text(json.dumps(m));return m

    def test_exact_dev_package_and_changed_asset(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.package(root)
            self.assertEqual(len(deploy.validate_package(root)[1]),6)
            (root/'editions/2026-09-10/HRL/article.html').write_text('substitution')
            with self.assertRaisesRegex(ValueError,'Changed'):deploy.validate_package(root)

    def test_production_origin_never_accepted(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);m=self.package(root);m['target_origin']='https://seasonalmarketnews.com'
            (root/'manifest.json').write_text(json.dumps(m))
            with self.assertRaisesRegex(ValueError,'Dev-only'):deploy.validate_package(root)

    def test_wrong_host_rejected_before_mutations(self):
        with patch.object(deploy.subprocess,'check_output',return_value='10.0.0.98'):
            with self.assertRaisesRegex(ValueError,'Exact temporary'):deploy.guard()

    def test_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);m=self.package(root);m['files']['../outside']='0'*64
            (root/'manifest.json').write_text(json.dumps(m))
            with self.assertRaisesRegex(ValueError,'unsafe'):deploy.validate_package(root)


if __name__=='__main__':unittest.main()
