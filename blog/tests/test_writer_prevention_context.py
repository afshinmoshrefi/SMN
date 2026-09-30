import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import engine_edition_workflow as workflow
from subscription_writer import sha256


class WriterPreventionContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);(self.root/'primary').mkdir();(self.root/'production').mkdir()
        self.fact='Customers shifted their capital spending toward hardware.'
        self.cause='Failure to adapt and delayed large deals drove the majority of our shortfall.'
        self.spec={'brief':self.fact,'sources':[{'id':'release','url':'https://issuer.example/release'},
                    {'id':'letter','url':'https://issuer.example/letter'}],
                   'material_context':[{'id':'demand-fact','kind':'fact','source_id':'release','quote':self.fact,
                     'summary':self.fact,'required':True,'event_date':''}]}
        raw='';rows=[]
        for url,text in [('https://issuer.example/release',self.fact),
                         ('https://issuer.example/letter',self.fact+'\r\n'+self.cause)]:
            hashed=sha256(text.encode());raw+='TEXT_SHA256: '+hashed+'\nTEXT:\n'+text+'\n'
            rows.append({'url':url,'final_url':url+'/final','date':'2026-09-25',
                         'page_text_sha256':hashed,'page_text_chars':len(text),'truncated':False})
        self.raw=self.root/'primary/ABC.txt';self.raw.write_bytes(raw.encode())
        self.receipt=self.root/'primary/ABC.receipt.json'
        self.write(self.receipt,{'symbol':'ABC','edition_date':'2026-09-30',
            'text_sha256':sha256(self.raw.read_bytes()),'sources':rows})
        self.write(self.root/'production/posts.json',[{'symbol':'ABC','published_date':'2026-09-30'}])
        self.write(self.root/'sources.json',{'ABC':self.spec})

    def write(self,path,value):path.write_text(json.dumps(value),encoding='utf-8')

    def test_raw_management_cause_omitted_from_brief_reaches_first_writer_context(self):
        pages=workflow.writer_primary_context(self.root,'ABC','2026-09-30',self.spec)
        self.assertNotIn(self.cause,self.spec['brief'])
        self.assertTrue(any(self.cause in page['text'] for page in pages))
        self.assertEqual(len(pages),2)  # Requested/final URL aliases never double source text.
        self.assertEqual({source['publication_date'] for p in pages for source in p['sources']},{'2026-09-25'})
        self.assertEqual({source['url'] for p in pages for source in p['sources']},
            {'https://issuer.example/release','https://issuer.example/release/final',
             'https://issuer.example/letter','https://issuer.example/letter/final'})
        self.assertTrue(all(page['capture_may_be_truncated'] is False for page in pages))
        self.assertEqual({sid for p in pages for sid in p['source_ids']},{'release','letter'})

    def test_review_serialization_avoids_raw_page_duplication_without_changing_binding(self):
        out=self.root/'results/ABC';out.mkdir(parents=True)
        evidence={'material_context':self.spec['material_context'],
                  'captured_primary_pages':workflow.writer_primary_context(self.root,'ABC','2026-09-30',self.spec)}
        path=out/'writer-evidence.json';self.write(path,evidence);before=path.read_bytes()
        result=workflow.review_writer_evidence(out)
        self.assertNotIn('captured_primary_pages',result)
        self.assertEqual(result['material_context'],self.spec['material_context'])
        self.assertEqual(path.read_bytes(),before)

    def test_wrong_date_and_changed_primary_hold_before_model_job(self):
        roles={'write':{'provider':'codex','model':'gpt-test','effort':'low'}}
        for defect in ('date','bytes'):
            with self.subTest(defect=defect),patch.object(workflow.smn_models,'prepare') as model:
                before_raw=self.raw.read_bytes();before_proof=self.receipt.read_bytes()
                if defect=='date':
                    proof=json.loads(before_proof);proof['edition_date']='2026-09-29';self.write(self.receipt,proof)
                else:self.raw.write_bytes(before_raw+b'changed')
                edition=workflow.Edition(self.root,'2026-09-30',roles=roles)
                with self.assertRaisesRegex(ValueError,'source contract invalid'):edition.prepare('ABC')
                model.assert_not_called()
                self.raw.write_bytes(before_raw);self.receipt.write_bytes(before_proof)

    def test_unverified_contract_holds_before_commissioning(self):
        self.spec['material_context'][0]['quote']='A guessed cause absent from the captured primary account.'
        with self.assertRaisesRegex(ValueError,'not verbatim'):
            workflow.writer_primary_context(self.root,'ABC','2026-09-30',self.spec)

    def test_repair_prompt_has_actual_remaining_allowance_and_metric_scope(self):
        out=self.root/'results/ABC';out.mkdir(parents=True)
        bundle={'as_of':'2026-09-30','evidence_sha256':'engine','sources':[
            {'id':'issuer','title':'Official release','max_derived_words':200}]}
        article={'title':'Title','title_source_ids':['issuer'],'dek':'Dek','dek_source_ids':['issuer'],
            'sections':[{'heading':'Current context','paragraphs':[{'text':'word '*184,'source_ids':['issuer']}]}],
            'takeaways':[]}
        for name,value in [('bundle.json',bundle),('article.json',article),('chart-words.json',{'issuer':10}),
                           ('writer-evidence.json',{}),('mechanical-checks.json',{}),('article.schema.json',{})]:self.write(out/name,value)
        issue=self.root/'issue.txt';issue.write_text('Correct the causal metric scope.',encoding='utf-8')
        edition=workflow.Edition(self.root,'2026-09-30',roles={'write':{'provider':'codex','model':'gpt-test','effort':'low'}})
        with patch('editorial_gate.context',return_value={}),patch.object(edition,'_prepare') as prepare:
            edition.repair('ABC',issue,'repair-two')
        prompt=prepare.call_args.args[3]
        allowances=json.loads(prompt.split('SOURCE PROSE ALLOWANCES:\n',1)[1].split('\n',1)[0])[0]
        self.assertEqual(allowances['maximum_all_surfaces'],200)
        self.assertEqual(allowances['current_total'],200)
        self.assertEqual(allowances['remaining_additional_words'],0)
        self.assertEqual(allowances['maximum_prose_with_current_headings'],186)
        self.assertEqual(allowances['recommended_prose_target'],166)
        self.assertIn('Do not transfer causality by proximity',prompt)


if __name__=='__main__':unittest.main()
