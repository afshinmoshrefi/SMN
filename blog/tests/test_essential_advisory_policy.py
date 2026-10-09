"""Focused offline regressions: advisories never manufacture a model pass."""
import copy
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parent))
import test_editorial_gate as fixtures
import editorial_gate as gate


class EssentialAdvisoryTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.EditorialGateTests('test_valid_complete_custody_reaches_assets_and_visual')
        self.f.setUp(); self.addCleanup(self.f.doCleanups)
        renderer=patch('visual_editorial.render_edition',return_value='<html>reviewed</html>')
        renderer.start();self.addCleanup(renderer.stop)
        self.result,self.ctx,self.review = self.f.result,self.f.ctx,copy.deepcopy(self.f.review)
        self.path = self.result/'editorial-advisory-disposition.json'

    def seal(self):
        self.f.write(self.f.job/'output.json',self.review)
        receipt=json.loads((self.f.job/'receipt.json').read_text())
        receipt['output_sha256']=gate.sha256((self.f.job/'output.json').read_bytes())
        self.f.write(self.f.job/'receipt.json',receipt)

    def record(self,kind='presentation_only',ids=()):
        self.review['passed']=False
        self.review['checks']['reader_value']['passed']=False
        self.review['issues']=[{'severity':'major','category':'coverage','location':'Outlook',
            'problem':'The accurate dated checkpoints could be explained more plainly.',
            'suggested_change':'Explain their relationship to entry more directly.'}]
        self.seal()
        record={'version':1,'actor':'/root/independent_reviewer','utc':'2026-10-09T13:00:00Z',
            'human_reviewed':False,'is_model_receipt':False,'article_sha256':gate.digest(self.f.article),
            'review_render_sha256':gate.review_render_sha256(self.result,self.f.article),
            'context_sha256':gate.digest(self.ctx),'review_sha256':gate.sha256((self.f.job/'output.json').read_bytes()),
            'issues':[{'index':0,'issue_sha256':gate.digest(self.review['issues'][0]),'kind':kind,
                'material_item_ids':list(ids),'reason':'Independent AI review: facts are covered; this is a presentation recommendation.'}]}
        self.f.write(self.path,record); return record

    def disposition(self):
        return gate.advisory_disposition(self.result,self.f.article,self.ctx,self.review,self.f.job/'output.json')

    def test_exact_coverage_presentation_reuses_original_negative_report(self):
        self.record(); before=(self.f.job/'output.json').read_bytes()
        proof=gate.verify_review(self.result,self.f.job/'output.json')
        self.assertTrue(proof['passed']); self.assertFalse(proof['original_review_passed'])
        self.assertFalse(proof['advisory_disposition']['record']['human_reviewed'])
        self.assertFalse(proof['advisory_disposition']['record']['is_model_receipt'])
        self.assertEqual(proof['acceptance_policy_version'],gate.ESSENTIAL_ADVISORY_POLICY)
        self.assertEqual(proof['advisory_findings']['issues'],self.review['issues'])
        self.assertEqual((self.f.job/'output.json').read_bytes(),before)

    def test_unknown_coverage_without_explicit_disposition_stays_hard(self):
        self.record(); self.path.unlink()
        with self.assertRaisesRegex(ValueError,'Unresolved coverage'):
            gate.verify_review(self.result,self.f.job/'output.json')

    def test_existing_passing_and_style_policy_proof_shapes_are_unchanged(self):
        proof=gate.verify_review(self.result,self.f.job/'output.json')
        self.assertEqual(set(proof),{'version','article_sha256','context_sha256','review_sha256','passed'})
        self.review['passed']=False
        self.review['checks']['reader_value']['passed']=False
        self.review['issues']=[{'category':'style','severity':'major','problem':'Opening could be clearer'}]
        self.seal();proof=gate.verify_review(self.result,self.f.job/'output.json')
        self.assertEqual(proof['acceptance_policy_version'],2)
        self.assertEqual(set(proof),{'version','article_sha256','context_sha256','review_sha256','passed',
            'acceptance_policy_version','original_review_passed','advisory_checks','advisory_issue_count'})

    def test_optional_omission_requires_known_explicit_optional_missing_item(self):
        self.ctx['material_context'][0]['required']=False
        self.record('optional_omission',[self.f.material['id']])
        self.review['editorial_audit']['coverage'][0].update(status='missing',article_quote='')
        self.seal(); record=json.loads(self.path.read_text());record['review_sha256']=gate.sha256((self.f.job/'output.json').read_bytes());self.f.write(self.path,record)
        self.assertEqual(self.disposition()[1],(0,))
        for mutation in ['required','unknown','uncertain']:
            with self.subTest(mutation=mutation):
                ctx=copy.deepcopy(self.ctx);review=copy.deepcopy(self.review);rec=copy.deepcopy(record)
                if mutation=='required':ctx['material_context'][0]['required']=True
                elif mutation=='unknown':rec['issues'][0]['material_item_ids']=['invented']
                else:review['editorial_audit']['coverage'][0]['status']='uncertain'
                rec['context_sha256']=gate.digest(ctx);self.f.write(self.path,rec)
                with self.assertRaises(ValueError):gate.advisory_disposition(self.result,self.f.article,ctx,review,self.f.job/'output.json')

    def test_missing_required_coverage_cannot_be_presentation_advice(self):
        self.record();self.review['editorial_audit']['coverage'][0]['status']='missing'
        with self.assertRaisesRegex(ValueError,'missing material facts'):self.disposition()

    def test_disposition_is_not_a_factual_numeric_temporal_or_security_override(self):
        record=self.record()
        for category in ['factual','numeric','temporal','instrument','other']:
            with self.subTest(category=category):
                self.review['issues'][0]['category']=category
                record['issues'][0]['issue_sha256']=gate.digest(self.review['issues'][0]);self.f.write(self.path,record)
                with self.assertRaises(ValueError):self.disposition()
                self.assertFalse(gate.hard_review_passed(self.review,advisory_issue_ids=(0,),identity_verified=True))

    def test_factual_defect_disguised_as_coverage_stays_hard(self):
        record=self.record();self.review['issues'][0]['problem']='Unsupported event completion claim'
        record['issues'][0]['issue_sha256']=gate.digest(self.review['issues'][0]);self.f.write(self.path,record)
        with self.assertRaises(ValueError):self.disposition()
        self.assertFalse(gate.hard_review_passed(self.review,advisory_issue_ids=(0,),identity_verified=True))

    def test_all_exact_bindings_and_ai_labels_are_required(self):
        original=self.record()
        for key,value in [('article_sha256','changed'),('review_render_sha256','changed'),
                          ('context_sha256','changed'),('review_sha256','changed'),('human_reviewed',True),
                          ('is_model_receipt',True),('utc','2026-10-09T13:00:00'),('actor','human')]:
            with self.subTest(key=key):
                record=copy.deepcopy(original);record[key]=value;self.f.write(self.path,record)
                with self.assertRaises(ValueError):self.disposition()

    def test_unknown_incomplete_duplicate_and_unbound_issue_ledgers_stay_hard(self):
        original=self.record()
        for mutation in ['incomplete','duplicate','unknown_status','issue_hash','kind']:
            with self.subTest(mutation=mutation):
                review=copy.deepcopy(self.review);record=copy.deepcopy(original)
                if mutation=='incomplete':review['editorial_audit']['coverage']=[]
                elif mutation=='duplicate':review['editorial_audit']['coverage']*=2
                elif mutation=='unknown_status':review['editorial_audit']['coverage'][0]['status']='guess'
                elif mutation=='issue_hash':record['issues'][0]['issue_sha256']='changed'
                else:record['issues'][0]['kind']='automatic_override'
                self.f.write(self.path,record)
                with self.assertRaises(ValueError):gate.advisory_disposition(self.result,self.f.article,self.ctx,review,self.f.job/'output.json')

    def test_other_factual_checks_and_audit_fail_even_with_valid_disposition(self):
        self.record()
        for name in ['facts_and_sources','history_and_numeric_meaning','source_allowances']:
            review=copy.deepcopy(self.review);review['checks'][name]['passed']=False
            self.assertFalse(gate.hard_review_passed(review,advisory_issue_ids=(0,),identity_verified=True))
        review=copy.deepcopy(self.review);review['editorial_audit']['coverage'][0]['article_quote']='Invented unsupported source quotation'
        self.assertTrue(gate.problems(self.f.article,self.f.bundle,self.ctx,review,advisory_issue_ids=(0,)))

    def test_disposition_mutation_invalidates_final_binding(self):
        self.record();proof=gate.verify_review(self.result,self.f.job/'output.json')
        binding=json.loads((self.result/'review-binding.json').read_text());binding['editorial_audit']=proof;self.f.write(self.result/'review-binding.json',binding)
        record=json.loads(self.path.read_text());record['issues'][0]['reason']='A different later explanation still cannot reuse an existing exact binding.';self.f.write(self.path,record)
        with self.assertRaisesRegex(ValueError,'Final editorial binding'):gate.verify_content(self.result)

    def test_combined_visual_checkbox_cannot_pass_without_identity_proof(self):
        self.record();self.review['checks']['smn_identity_and_visuals']['passed']=False;self.seal()
        record=json.loads(self.path.read_text());record['review_sha256']=gate.sha256((self.f.job/'output.json').read_bytes());self.f.write(self.path,record)
        self.assertFalse(gate.hard_review_passed(self.review,advisory_issue_ids=(0,)))
        with patch.object(gate,'essential_identity',return_value={'passed':True,'kind':'deterministic_essential_identity'}):
            proof=gate.verify_review(self.result,self.f.job/'output.json')
        self.assertFalse(proof['original_review_passed']);self.assertIn('smn_identity_and_visuals',proof['advisory_checks'])
        binding=json.loads((self.result/'review-binding.json').read_text());binding['editorial_audit']=proof;self.f.write(self.result/'review-binding.json',binding)
        with patch.object(gate,'essential_identity',return_value=proof['essential_identity']),patch('engine_seasonal.verify_assets'),patch('smn_visual.verify_article_visual',side_effect=ValueError('real pixel approval missing')):
            with self.assertRaisesRegex(ValueError,'real pixel approval missing'):gate.verify_complete(self.result)

    def test_actual_essential_identity_checks_brand_study_link_native_fragments_and_assets(self):
        from seasonal_edition import study_link,links_html
        native={'card':{'symbol':'SPY','resource_id':'6','story_cell':{'anchor_date':'2026-10-29','days':35,'years':'pe2-7'}}}
        native['study_url']=study_link(native['card'],'https://tradewave.ai/wave-viewer')
        self.f.write(self.result/'seasonal-manifest.json',native)
        brand='<a href="/" class="logo"><span>Seasonal</span><span>Market</span><span>News</span></a>'
        text=brand+'STATS'+links_html(native)+'FIG-barsFIG-bars_mae_mfeFIG-price_projection'+''.join(
            '<p>'+unit['text']+'</p>' for unit in gate.units(self.f.article))
        bundle={'seasonal_contract':{'price_path_required':True}}
        with patch('engine_seasonal.verify_assets') as assets,patch('engine_seasonal.stats_html',return_value='STATS'),patch('engine_seasonal.figure_html',side_effect=lambda data,variant:'FIG-'+variant):
            (self.result/'article.html').write_text(text,encoding='utf-8')
            self.assertTrue(gate.essential_identity(self.result,bundle)['passed']);assets.assert_called_once()
            for broken in [text.replace(brand,''),text.replace('STATS',''),text.replace('FIG-bars_mae_mfe',''),text.replace('FIG-price_projection',''),text.replace(links_html(native),''),text.replace(self.f.text,'invented body')]:
                (self.result/'article.html').write_text(broken,encoding='utf-8')
                with self.assertRaises(ValueError):gate.essential_identity(self.result,bundle)
            (self.result/'article.html').write_text(text,encoding='utf-8')
            native['study_url']=native['study_url'].replace('tradewave.ai','evil.example');self.f.write(self.result/'seasonal-manifest.json',native)
            with self.assertRaisesRegex(ValueError,'exact TradeWave study'):gate.essential_identity(self.result,bundle)
        with patch('engine_seasonal.verify_assets',side_effect=ValueError('changed native metric or asset')):
            with self.assertRaisesRegex(ValueError,'changed native metric or asset'):gate.essential_identity(self.result,bundle)

    def test_stock_finalize_then_verify_content_keeps_disposition_and_proof_stable(self):
        import engine_edition_workflow as workflow
        native=json.loads((self.result/'seasonal-manifest.json').read_text())
        native['price_path']={'evidence_sha256':'fixture-owner-path'}
        self.f.write(self.result/'seasonal-manifest.json',native)
        self.ctx=gate.context(self.f.root,'SPY','2026-09-30')
        self.f.write(self.f.job/'editorial-context.json',self.ctx)
        (self.f.job/'prompt.txt').write_text(gate.MARKER+gate.digest(self.ctx)+'\n',encoding='utf-8')
        manifest=json.loads((self.f.job/'job.json').read_text())
        manifest['input_hashes']={name:gate.sha256((self.f.job/name).read_bytes()) for name in manifest['input_hashes']}
        self.f.write(self.f.job/'job.json',manifest)
        receipt=json.loads((self.f.job/'receipt.json').read_text());receipt['input_hashes']=manifest['input_hashes'];self.f.write(self.f.job/'receipt.json',receipt)
        self.record();self.review['checks']['smn_identity_and_visuals']['passed']=False;self.seal()
        rec=json.loads(self.path.read_text());rec['review_sha256']=gate.sha256((self.f.job/'output.json').read_bytes());self.f.write(self.path,rec)
        disposition_before=self.path.read_bytes()
        self.f.write(self.result/'commission.json',{'production_article':{'source_mode':'selected_inputs'}})
        ed=object.__new__(workflow.Edition);ed.root=self.f.root;ed.date='2026-09-30';ed.publication_origin='https://smn-dev.trxstat.com'
        ed.generation=lambda *args:{'summary':{'test_fixture':True}}
        identity={'passed':True,'kind':'deterministic_essential_identity','review_render_sha256':rec['review_render_sha256']}
        rendered='<html><head></head><body><article><header>Reviewed body</header></article></body></html>\n'
        with patch.object(gate,'essential_identity',return_value=identity),patch('engine_seasonal.verify_assets'),patch.object(workflow,'verify_assets'),patch.object(workflow,'render_edition',return_value=rendered),patch.object(workflow.se,'figure_html',return_value='fixture-owner-figure'):
            before=gate.verify_review(self.result,self.f.job/'output.json')
            ed.finalize('SPY','review')
            self.assertEqual(before,gate.verify_content(self.result))
            self.assertEqual(disposition_before,self.path.read_bytes())
            binding=json.loads((self.result/'review-binding.json').read_text())
            self.assertEqual(binding['article_html_sha256'],gate.sha256((self.result/'article.html').read_bytes()))
            self.assertIn(b'smn-generation',(self.result/'article.html').read_bytes())
            (self.result/'article.html').write_bytes((self.result/'article.html').read_bytes().replace(b'Reviewed body',b'Changed body'))
            with self.assertRaisesRegex(ValueError,'Final editorial binding'):gate.verify_content(self.result)


if __name__=='__main__':unittest.main()
