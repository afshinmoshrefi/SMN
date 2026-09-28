import io, json, subprocess, sys, tarfile, tempfile, unittest
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

    def remote_tree(self, d, finished=True):
        site=Path(d)/'smn'; blog=Path(d)/'blog'; date='2026-09-28'
        (site/'datasets').mkdir(parents=True); (blog/'article_ideas').mkdir(parents=True)
        (blog/('article_ideas/article_queue_'+date+'.csv')).write_text('rank\n')
        audit=blog/'audit/2026/09/28'; posts=[]
        runs=[('TTWO','a1','success'),('XLK','a2','success'),('MCD','a3','success'),
              ('QQQ','a4','error'),('SPX','a5','success'),('COST','a6','success')]
        for sym,aid,status in runs:
            folder=audit/(sym+'_2026-10-25_184_6_'+aid); folder.mkdir(parents=True)
            run={'status':status,'finished_at':'2026-09-28T03:09:00Z' if finished else None}
            (folder/'manifest.json').write_text(json.dumps({'article_id':aid,'run':run}))
            (folder/'prompt.txt').write_text('{"meta":{}}\n')
            if status=='success': posts.append((sym,aid))
        posts.append(('SPX','manual1'))  # manually added lead: no daily audit folder
        records=[]
        for sym,aid in posts:
            page=site/'articles'/(sym+aid+'.html'); page.parent.mkdir(exist_ok=True); page.write_text('<p>x</p>')
            rec={'symbol':sym,'published_date':date+'T03:00:00Z','path':str(page),'hero_image':'/articles/hero_'+sym+'_'+aid+'.jpg',
                 'pattern_start_date':'2026-10-25','pattern_days':184,'lookback_years':'pe2-6'}
            (site/'datasets'/(sym+'_2026-10-25_184_pe2-6_dataset.json')).write_text('{}')
            records.append(rec)
        (site/'posts.json').write_text(json.dumps(records))
        code=sc.REMOTE_CAPTURE.replace("/var/www/smn",str(site)).replace("/home/flask/blog",str(blog))
        return subprocess.run([sys.executable,'-',date],input=code.encode(),capture_output=True)

    def test_remote_capture_takes_only_finished_daily_articles(self):
        with tempfile.TemporaryDirectory() as d:
            p=self.remote_tree(d)
            self.assertEqual(p.returncode,0,p.stderr.decode())
            with tarfile.open(fileobj=io.BytesIO(p.stdout),mode='r:gz') as t:
                posts=json.loads(t.extractfile('posts.json').read())
            self.assertEqual(sorted(x['symbol'] for x in posts),['COST','MCD','SPX','TTWO','XLK'])
            self.assertNotIn('manual1',json.dumps(posts))

    def test_remote_capture_waits_for_unfinished_daily_run(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self.remote_tree(d,finished=False).returncode,75)

    def test_five_article_day_is_captured(self):
        posts=[{'symbol':s,'published_date':'2026-09-28T03:00:00Z'} for s in ('TTWO','XLK','MCD','SPX','COST')]
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); (root/'production').mkdir(); (root/'production/posts.json').write_text(json.dumps(posts))
            self.assertEqual(len(sc._posts(root,'2026-09-28')),5)
            (root/'production/posts.json').write_text(json.dumps(posts+[posts[0]]))
            with self.assertRaises(sc.Held): sc._posts(root,'2026-09-28')

if __name__=='__main__': unittest.main()
