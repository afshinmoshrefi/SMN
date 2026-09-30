"""September30 regressions: copy meaning, with no financial recalculation."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from seasonal_edition import check_temporal_instrument_copy
from engine_edition_workflow import publication_head,selected_hero
from subscription_publication import edition_section,write,digest_bytes,package,digest


class EditorialEvidenceGuards(unittest.TestCase):
    def bundle(self,symbol='VIX',start='2026-10-10'):
        return {'story_id':symbol,'as_of':'2026-09-30T00:30:03-04:00',
                'sources':[{'source_type':'engine_export','payload':{'window':{'start_date':start}}}]}

    def test_spot_vix_decline_cannot_be_presented_as_attainable_profit(self):
        with self.assertRaisesRegex(ValueError,'not attainable'):
            check_temporal_instrument_copy(['The VIX finished lower, so a bet on falling prices worked in all eight.'],self.bundle())
        check_temporal_instrument_copy(['The VIX level finished lower. Engine short-direction statistics describe index changes, not derivative returns.'],self.bundle())
        check_temporal_instrument_copy(['A bet on falling prices worked in the historical AMD study.'],self.bundle('AMD'))

    def test_current_level_is_not_future_entry_condition(self):
        with self.assertRaisesRegex(ValueError,'before its start'):
            check_temporal_instrument_copy(['The VIX starts near the low end of its 52-week range.'],self.bundle())
        check_temporal_instrument_copy(['The VIX closed near the low end of its range on September29; the window starts October10.'],self.bundle())
        check_temporal_instrument_copy(['The stock starts near the low end of its range.'],self.bundle('AMD','2026-09-30'))

    def test_production_head_is_bound_before_visual_review(self):
        raw='<html><head><meta name="robots" content="noindex,nofollow"></head></html>'
        self.assertEqual(publication_head(raw,'https://smn-dev.trxstat.com','2026-09-30','AMD'),raw)
        rendered=publication_head(raw,'https://seasonalmarketnews.com','2026-09-30','AMD')
        self.assertIn('content="index,follow"',rendered)
        self.assertIn('https://seasonalmarketnews.com/editions/2026-09-30/AMD/article.html',rendered)
        with self.assertRaisesRegex(ValueError,'Unexpected'):
            publication_head(raw,'https://example.com','2026-09-30','AMD')
        with self.assertRaisesRegex(ValueError,'marker missing'):
            publication_head('<head></head>','https://seasonalmarketnews.com','2026-09-30','AMD')

    def test_selected_input_landing_accepts_exact_production_without_fake_original(self):
        origin='https://seasonalmarketnews.com'
        entry={'url':origin+'/editions/2026-09-30/AMD/article.html',
               'hero_image':origin+'/editions/2026-09-30/AMD/assets/hero.png',
               'hero_alt':'Chip illustration','symbol':'AMD','market_family':'US',
               'title':'Seasonal article','dek':'Current business and unchanged history.',
               'production_original':''}
        rendered=edition_section([entry],'2026-09-30',origin)
        self.assertIn(entry['url'],rendered)
        self.assertNotIn('Original publication',rendered)
        with self.assertRaisesRegex(ValueError,'outside exact'):
            edition_section([entry],'2026-09-30')
        entry['hero_image']='https://seasonalmarketnews.com.evil.example/hero.png'
        with self.assertRaisesRegex(ValueError,'outside exact'):
            edition_section([entry],'2026-09-30',origin)

    def test_selected_hero_reads_outer_receipt_and_rejects_changed_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);relative='production/AMD/assets/hero.png'
            hero=root/relative;hero.parent.mkdir(parents=True);hero.write_bytes(b'inspected image')
            write(root/'input-selection.json',{'date':'2026-09-30'})
            post={'hero_image':'https://seasonalmarketnews.com/hero.png'}
            image_hash=digest_bytes(hero.read_bytes())
            write(root/'input-heroes.json',{'date':'2026-09-30',
                  'selection_sha256':digest_bytes((root/'input-selection.json').read_bytes()),
                  'files':{relative:image_hash},'heroes':{'AMD':{'path':relative,
                  'url':post['hero_image'],'sha256':image_hash,'provider':'retained API receipt'}}})
            self.assertEqual(selected_hero(root,'AMD',post,'2026-09-30'),(hero,'retained API receipt'))
            hero.write_bytes(b'changed image')
            with self.assertRaisesRegex(ValueError,'custody changed'):
                selected_hero(root,'AMD',post,'2026-09-30')

    def test_production_package_requires_actual_chatgpt_writer_provenance(self):
        for provider in ('openai','anthropic',None):
            with self.subTest(provider=provider),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);result=root/'results/AMD';result.mkdir(parents=True)
                (result/'assets').mkdir();(result/'evidence').mkdir()
                origin='https://seasonalmarketnews.com';url=origin+'/editions/2026-09-30/AMD/article.html'
                article={'title':'Reviewed article','dek':'Current context and unchanged historical evidence.'}
                write(root/'smn-daily-state.json',{'profile':'chatgpt','publication_origin':origin})
                write(result/'commission.json',{'history_status':'verified_selected_engine','production_article':{
                    'symbol':'AMD','market_family':'US','lookback_years':'pe2-10',
                    'published_date':'2026-09-30T00:00:00Z','source_mode':'selected_inputs'}})
                write(result/'bundle.json',{});write(result/'hero-asset.json',{'url':'assets/hero.png','alt':'Chip illustration'})
                (result/'article.html').write_text('<head><meta name="robots" content="index,follow"><meta name="smn-generation" content="receipt"><link rel="canonical" href="'+url+'"></head>',encoding='utf-8')
                if provider:
                    role={'provider':provider,'model':'gpt-6-astra','billing_source':'subscription','api_fallback':False}
                    write(result/'generation.json',{'article_sha256':digest(article),'summary':role,'writers':[role],'reviewer':role})
                with patch('subscription_publication.reviewed',return_value=article):
                    if provider=='openai':
                        manifest=package(root,'2026-09-30','a'*40,{'AMD':'review'},target_origin=origin)
                        self.assertTrue(manifest['production_allowed'])
                    else:
                        with self.assertRaisesRegex(ValueError,'provenance'):
                            package(root,'2026-09-30','a'*40,{'AMD':'review'},target_origin=origin)


if __name__=='__main__':unittest.main()
