from datetime import datetime,timedelta,timezone
from pathlib import Path
import hashlib,json,os,sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import operational_schedule as schedule

DAY='2026-10-12';SEVEN=datetime(2026,10,12,11,tzinfo=timezone.utc)
SYMBOLS=['AAA','BBB','CCC','DDD','EEE','FFF']

class DeadlinePartialNewsletterTests(unittest.TestCase):
    def fixture(self,published):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        root=Path(temp.name)/'runs';web=Path(temp.name)/'web';web.mkdir()
        inputs=root/DAY/'inputs';primary=root/DAY/'chatgpt/primary';inputs.mkdir(parents=True);primary.mkdir(parents=True)
        selected=inputs/'input-selection.json';selected.write_text(json.dumps({'date':DAY,'symbols':SYMBOLS}))
        os.utime(selected,(SEVEN.timestamp(),SEVEN.timestamp()))
        (root/DAY/'schedule-settings.json').write_text(json.dumps({'date':DAY,'daily_generation':{'start_time':'05:30','target_time':'07:00','timezone':'America/New_York'},'weekday_newsletter':{'time':'07:00','timezone':'America/New_York'}}))
        urls=[];files={};posts=[]
        for symbol in published:
            rel=f'editions/{DAY}/{symbol}/article.html';url='https://seasonalmarketnews.com/'+rel
            file=web/rel;file.parent.mkdir(parents=True);file.write_text('Verified '+symbol)
            files[rel]=hashlib.sha256(file.read_bytes()).hexdigest();urls.append(url)
            posts.append({'url':url,'source_commit':'a'*40,'edition_id':'subscription-'+DAY,'published_date':DAY+'T09:30:00Z','publish_status':'true'})
            (primary/(symbol+'.receipt.json')).write_text(json.dumps({'fetched_utc':DAY+'T09:35:00+00:00'}))
        pending=[s for s in SYMBOLS if s not in published]
        receipt={'status':'live_verified','production_written':True,'edition_date':DAY,'source_commit':'a'*40,'publication_policy':'continuity-v1','complete':not pending,'coverage_status':'partial' if pending else 'complete','expected_symbols':SYMBOLS,'published_symbols':published,'pending_symbols':pending,'urls':urls,'files':files}
        path=root/DAY/'chatgpt/production-publication-receipt.json';path.write_text(json.dumps(receipt));(web/'posts.json').write_text(json.dumps(posts))
        return root,web,path,receipt,set(urls)

    def test_one_failed_five_good_and_multiple_holds_release_verified_subset(self):
        for subjects in [SYMBOLS[:5],SYMBOLS[:3]]:
            with self.subTest(subjects=subjects):
                root,web,_,_,urls=self.fixture(subjects)
                self.assertIsNone(schedule.verified_reader_urls(root,DAY,web,current=SEVEN-timedelta(minutes=6)))
                self.assertEqual(schedule.verified_reader_urls(root,DAY,web,current=SEVEN-timedelta(minutes=5)),urls)
                self.assertEqual(schedule.verified_reader_urls(root,DAY,web,current=SEVEN),urls)
                self.assertIsNone(schedule.verified_reader_urls(root,DAY,web,current=SEVEN+timedelta(days=1)))

    def test_stale_changed_or_falsely_complete_receipts_cannot_release(self):
        root,web,path,r,_=self.fixture(SYMBOLS[:3])
        r['complete']=True;path.write_text(json.dumps(r))
        with self.assertRaisesRegex(ValueError,'completion claim'):schedule.verified_reader_urls(root,DAY,web,current=SEVEN)
        r['complete']=False;path.write_text(json.dumps(r))
        source=root/DAY/'chatgpt/primary/AAA.receipt.json';source.write_text(json.dumps({'fetched_utc':DAY+'T08:00:00+00:00'}))
        with self.assertRaisesRegex(ValueError,'predates'):schedule.verified_reader_urls(root,DAY,web,current=SEVEN)
        source.write_text(json.dumps({'fetched_utc':DAY+'T09:35:00+00:00'}))
        (web/f'editions/{DAY}/AAA/article.html').write_text('tampered')
        with self.assertRaisesRegex(ValueError,'changed'):schedule.verified_reader_urls(root,DAY,web,current=SEVEN)

    def test_notice_without_any_qualified_article_does_not_send(self):
        root,web,_,_,_=self.fixture([])
        self.assertIsNone(schedule.verified_reader_urls(root,DAY,web,current=SEVEN))

if __name__=='__main__':unittest.main()
