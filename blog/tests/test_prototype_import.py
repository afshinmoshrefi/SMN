import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

import prototype_import as importer
from subscription_writer import sha256


class ImportSafety(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)

    def archive(self,extra=None,corrupt=False):
        data=b'actual private bytes';manifest={'files':{'safe/source.json':{'sha256':sha256(data),'bytes':len(data)}},
            'original_path_custody':{'C:\\original\\source.json':{'relative_path':'safe/source.json','sha256':sha256(data)}},
            'publish':False,'media_review':'pending'}
        payload={'safe/source.json':b'corrupted' if corrupt else data,'import-manifest.json':json.dumps(manifest).encode()}
        path=self.root/'bundle.tar.gz'
        with tarfile.open(path,'w:gz') as tar:
            for name,body in payload.items():
                member=tarfile.TarInfo(name);member.size=len(body);tar.addfile(member,io.BytesIO(body))
            if extra is not None:tar.addfile(extra,io.BytesIO(b'x') if extra.isfile() else None)
        return path,sha256(path.read_bytes())

    def test_exact_archive_and_each_member_verified_without_extraction(self):
        path,h=self.archive();manifest,payload=importer.verify_archive(path,h)
        self.assertEqual(payload['safe/source.json'],b'actual private bytes')
        self.assertEqual(len(manifest['original_path_custody']),1)
        self.assertFalse((self.root/'safe').exists())

    def test_outer_hash_mismatch_refused(self):
        path,h=self.archive()
        with self.assertRaises(ValueError):importer.verify_archive(path,'0'*64)

    def test_individual_member_mutation_refused(self):
        path,h=self.archive(corrupt=True)
        with self.assertRaises(ValueError):importer.verify_archive(path,h)

    def test_traversal_absolute_and_windows_paths_refused_before_write(self):
        for name in ('../escape','/absolute','C:/absolute','safe\\escape','safe/../escape'):
            item=tarfile.TarInfo(name);item.size=1;path,h=self.archive(item)
            with self.assertRaises(ValueError):importer.verify_archive(path,h)
        self.assertFalse((self.root/'escape').exists())

    def test_symlink_hardlink_and_duplicate_members_refused(self):
        for kind,name in ((tarfile.SYMTYPE,'safe/link'),(tarfile.LNKTYPE,'safe/link'),(tarfile.REGTYPE,'safe/source.json')):
            item=tarfile.TarInfo(name);item.type=kind;item.linkname='../escape';item.size=1 if kind==tarfile.REGTYPE else 0
            path,h=self.archive(item)
            with self.assertRaises(ValueError):importer.verify_archive(path,h)

    def test_conflicting_existing_private_file_never_overwritten(self):
        path=self.root/'existing';path.write_bytes(b'original')
        with self.assertRaises(ValueError):importer._write(path,b'changed')
        self.assertEqual(path.read_bytes(),b'original')


if __name__=='__main__':unittest.main()
