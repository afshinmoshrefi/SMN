import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import editorial_gate as gate
from subscription_publication import CHECKS


def article(text,kind='fact'):
    return {'title':'Seasonal study','title_source_ids':['letter'],
            'dek':'Historical context and current developments.','dek_source_ids':['letter'],
            'takeaways':[],'sections':[{'paragraphs':[{'text':text,'kind':kind,'source_ids':['letter']}]}]}


def card():
    return {'engine_results':{'cohort':{'years':[2006,2010,2014,2018,2022]},'comparisons':[
        {'request':{'years':10},'per_year':[{'year':y} for y in [2016,2017,2018,2019,2020,2021,2022,2023,2024,2025]]},
        {'request':{'years':20},'per_year':[{'year':y} for y in [2006,2010,2014,2018,2022]]}]}}


class EditorialGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.result=self.root/'results/SPY';self.result.mkdir(parents=True)
        self.job=self.root/'jobs/SPY-20260930-review';self.job.mkdir(parents=True)
        self.text='Management identifies delayed deals and execution problems in the shortfall.'
        self.article=article(self.text)
        self.bundle={'story_id':'SPY','as_of':'2026-09-30','evidence_sha256':'engine-evidence',
                     'sources':[{'id':'letter','url':'https://issuer.example/letter'}]}
        self.material={'id':'management-context','kind':'cause','source_id':'letter','quote':self.text,
                       'summary':'Delayed deals and execution problems matter.','required':True,'event_date':''}
        self.write(self.result/'article.json',self.article)
        self.write(self.result/'bundle.json',self.bundle)
        self.write(self.result/'writer-evidence.json',{'material_context':[self.material]})
        self.write(self.result/'seasonal-manifest.json',{'card':card()})
        for name in ('source.json','chart-manifest.json'):self.write(self.result/name,{})
        (self.result/'hero.png').write_bytes(b'hero')
        self.write(self.result/'hero-asset.json',{'url':'hero.png','sha256':gate.sha256(b'hero')})
        (self.result/'article.html').write_text('<html>reviewed</html>',encoding='utf-8')
        self.write(self.root/'daily-state.json',{'date':'2026-09-30'})
        folder=self.root/'primary';folder.mkdir();raw='';sources=[]
        for url,text in [('https://issuer.example/letter',self.text),('https://issuer.example/calendar',
                         'The next meeting is scheduled for October 15, 2026.')]:
            hashed=gate.sha256(text.encode());raw+='TEXT_SHA256: '+hashed+'\nTEXT:\n'+text+'\n'
            sources.append({'url':url,'final_url':url,'date':'2026-09-25','page_text_sha256':hashed,'page_text_chars':len(text)})
        (folder/'SPY.txt').write_bytes(raw.encode('utf-8'))
        self.write(folder/'SPY.receipt.json',{'symbol':'SPY','edition_date':'2026-09-30',
                   'text_sha256':gate.sha256((folder/'SPY.txt').read_bytes()),'sources':sources})
        self.ctx=gate.context(self.root,'SPY','2026-09-30')
        self.review={'passed':True,'checks':{k:{'passed':True} for k in CHECKS},'issues':[],
                     'editorial_audit':{'coverage':[{'item_id':self.material['id'],'status':'covered',
                         'article_quote':self.text,'reason':'Covered'}],'claims':[]}}
        self.write(self.job/'editorial-context.json',self.ctx)
        (self.job/'prompt.txt').write_text(gate.MARKER+gate.digest(self.ctx)+'\n',encoding='utf-8')
        self.write(self.job/'schema.json',{'type':'object'})
        self.write(self.job/'output.json',self.review)
        hashes={name:gate.sha256((self.job/name).read_bytes()) for name in ('prompt.txt','schema.json','editorial-context.json')}
        self.write(self.job/'job.json',{'job_id':self.job.name,'stage':'review','input_hashes':hashes,'evidence_sha256':'engine-evidence','model':'gpt-test',
                   'effort':'low','valid_until':'2026-09-30T20:00:00+00:00'})
        self.write(self.job/'receipt.json',{'job_id':self.job.name,'stage':'review','billing_source':'subscription','input_hashes':hashes,'evidence_sha256':'engine-evidence',
                   'output_sha256':gate.sha256((self.job/'output.json').read_bytes()),'api_fallback':False,
                   'model_requested':'gpt-test','effort_requested':'low','finished_utc':'2026-09-30T19:00:00+00:00'})
        proof=gate.verify_review(self.result,self.job/'output.json')
        self.write(self.result/'review-binding.json',{'review_stage':'review','editorial_audit':proof,
                   'article_html_sha256':gate.sha256((self.result/'article.html').read_bytes())})
        self.write(self.result/'mechanical-checks.json',{'passed':True,'article_sha256':proof['article_sha256'],
                   'evidence_sha256':'engine-evidence'})

    def write(self,path,value):path.write_text(json.dumps(value),encoding='utf-8')

    def complete(self):
        with patch('engine_seasonal.verify_assets') as assets,patch('smn_visual.verify_article_visual') as visual:
            result=gate.verify_complete(self.result)
            assets.assert_called_once();visual.assert_called_once()
            return result

    def test_valid_complete_custody_reaches_assets_and_visual(self):
        self.assertTrue(self.complete()['passed'])

    def test_material_quote_must_use_matching_citation_even_when_adjacent_facts_are_correct(self):
        first='The filing reports the quarter ended July 26, 2026.'
        second='For the quarter ended July 26, 2026, revenue rose 18% sequentially and 106% annually.'
        draft=article(first)
        draft['sections'][0]['paragraphs'][0]['source_ids']=['filing']
        draft['sections'][0]['paragraphs'].append({'text':second,'kind':'fact','source_ids':['release']})
        ctx=copy.deepcopy(self.ctx)
        ctx['material_context'][0].update({'id':'q2-revenue','source_id':'release',
            'summary':'The July 26 quarter rose sequentially and annually.'})
        review=copy.deepcopy(self.review)
        row=review['editorial_audit']['coverage'][0]
        row.update({'item_id':'q2-revenue','article_quote':first})
        self.assertTrue(any('Missing material context q2-revenue' in issue
            for issue in gate.problems(draft,self.bundle,ctx,review)))
        row['article_quote']=second
        self.assertFalse(any('Missing material context q2-revenue' in issue
            for issue in gate.problems(draft,self.bundle,ctx,review)))
        self.assertIn('Choose quoted text carrying the item\'s source_id citation',gate.RULES)

    def test_style_only_failure_reuses_signed_review_with_versioned_policy(self):
        review=copy.deepcopy(self.review)
        review['passed']=False
        review['checks']['why_now_and_opening']={'passed':False,'reason':'Lead could be sharper'}
        review['checks']['reader_value']={'passed':False,'reason':'More explanation would help'}
        review['issues']=[{'severity':'major','category':'style','problem':'Lead is too abstract'}]
        self.write(self.job/'output.json',review)
        receipt=json.loads((self.job/'receipt.json').read_text())
        receipt['output_sha256']=gate.sha256((self.job/'output.json').read_bytes())
        self.write(self.job/'receipt.json',receipt)
        proof=gate.verify_review(self.result,self.job/'output.json')
        self.assertEqual(proof['acceptance_policy_version'],gate.STYLE_ADVISORY_POLICY)
        self.assertFalse(proof['original_review_passed'])
        self.assertEqual(proof['review_sha256'],receipt['output_sha256'])
        review['issues'][0]['category']='coverage'
        self.assertFalse(gate.hard_review_passed(review))
        review['issues'][0].update(category='style',problem='Unsupported revenue forecast')
        self.assertFalse(gate.hard_review_passed(review))
        review['issues']=[];review['checks']['facts_and_sources']={'passed':False}
        self.assertFalse(gate.hard_review_passed(review))
        review['checks']['facts_and_sources']={'passed':True};review['checks']['why_now_and_opening']={'passed':True}
        review['checks']['reader_value']={'passed':True}
        self.assertFalse(gate.hard_review_passed(review))

    def test_exact_multi_unit_and_chart_spans_are_grounded(self):
        rows=[{'text':'The September report covers fiscal 2026.','source_ids':['letter']},
              {'text':'Seasonal history appears here.','source_ids':['history']},
              {'text':'Fourth-quarter revenue increased from 596.9 to 634.7.','source_ids':['letter']}]
        self.assertTrue(gate.quote_in_verified_units(
            'The September report covers fiscal 2026. Fourth-quarter revenue increased from 596.9 to 634.7.',rows))
        self.assertTrue(gate.quote_in_verified_units(
            'The September report covers fiscal 2026. ... Fourth-quarter revenue increased from 596.9 to 634.7.',rows))
        self.assertFalse(gate.quote_in_verified_units(
            'The September report covers fiscal 2026. ... Fourth-quarter revenue increased from 596.9 to 9999.',rows))
        self.assertFalse(gate.quote_in_verified_units('The September report covers fiscal 2026. ... invented fact',rows))

    def test_future_price_overlay_cannot_be_called_recorded_price(self):
        bad = article(self.text + ' The chart lays the seasonal path over IWM actual price for the next 60 weekdays.')
        issues = gate.problems(bad, self.bundle, self.ctx, self.review)
        self.assertTrue(any('future illustration described as recorded price' in issue for issue in issues))
        good = article(self.text + ' Recorded prices end October 1; the seasonal overlay illustrates the next 60 weekdays.')
        self.assertEqual(gate.problems(good, self.bundle, self.ctx, self.review), [])

    def test_mutations_fail_closed(self):
        paths=[self.result/'article.json',self.result/'source.json',self.root/'primary/SPY.txt',
               self.job/'prompt.txt',self.job/'schema.json',self.job/'output.json',self.job/'editorial-context.json',
               self.result/'article.html',self.result/'hero.png']
        for path in paths:
            with self.subTest(path=path.name):
                before=path.read_bytes()
                if path.name=='article.json':
                    changed=json.loads(before);changed['title']='Changed after review';self.write(path,changed)
                else:path.write_bytes(before+b' ')
                with self.assertRaises((ValueError,json.JSONDecodeError)):self.complete()
                path.write_bytes(before)

    def test_stale_receipt_and_mechanical_approval_fail(self):
        for path,key,value in [(self.job/'receipt.json','evidence_sha256','different'),
                               (self.job/'receipt.json','job_id','different'),
                               (self.job/'receipt.json','stage','different'),
                               (self.job/'receipt.json','billing_source','api'),
                               (self.job/'receipt.json','output_sha256','different'),
                               (self.job/'receipt.json','api_fallback',True),
                               (self.job/'receipt.json','finished_utc','2026-10-01T00:00:00+00:00'),
                               (self.result/'mechanical-checks.json','article_sha256','different')]:
            with self.subTest(key=key):
                before=path.read_bytes();obj=json.loads(before);obj[key]=value;self.write(path,obj)
                with self.assertRaises(ValueError):self.complete()
                path.write_bytes(before)

    def test_missing_visual_blocks_completion(self):
        with patch('engine_seasonal.verify_assets'),patch('smn_visual.verify_article_visual',side_effect=ValueError('visual approval missing')):
            with self.assertRaisesRegex(ValueError,'visual approval missing'):gate.verify_complete(self.result)

    def test_missing_material_contract_blocks_context(self):
        self.write(self.result/'writer-evidence.json',{'material_context':[]})
        with self.assertRaisesRegex(ValueError,'pre-writer material-source contract'):
            gate.context(self.root,'SPY','2026-09-30')

    def test_review_schema_restricts_ids_and_empty_claim_array(self):
        original=json.loads((Path(gate.__file__).parent/'schemas/subscription_review.schema.json').read_text())
        schema=gate.review_schema(copy.deepcopy(original),self.ctx)
        audit=schema['properties']['editorial_audit']['properties']
        self.assertEqual(audit['claims']['maxItems'],0)
        self.assertEqual(audit['coverage']['items']['properties']['item_id']['enum'],['management-context'])
        self.assertNotIn('enum',audit['coverage']['items']['properties']['reason'])
        with self.assertRaises(ValueError):
            gate.validate_schema([{'unit_id':'invented'}],audit['claims'])
        ctx={**self.ctx,'requirements':[{'id':'sections.0.paragraphs.0'}]}
        claims=gate.review_schema(copy.deepcopy(original),ctx)['properties']['editorial_audit']['properties']['claims']
        self.assertEqual(claims['minItems'],1)
        self.assertEqual(claims['items']['properties']['unit_id']['enum'],['sections.0.paragraphs.0'])

    def test_primary_crlf_page_bytes_are_preserved_and_mutations_rejected(self):
        path=self.root/'primary/SPY.txt';receipt=self.root/'primary/SPY.receipt.json'
        proof=json.loads(receipt.read_text());raw='';texts=[]
        for i,item in enumerate(proof['sources']):
            text='Official statement\r\nwith exact captured carriage returns '+str(i)+'.'
            texts.append(text);item['page_text_sha256']=gate.sha256(text.encode());item['page_text_chars']=len(text)
            newline='\n' if i==0 else '\r\n'
            raw+='TEXT_SHA256: '+item['page_text_sha256']+newline+'TEXT:'+newline+text+'\n'
        path.write_bytes(raw.encode());proof['text_sha256']=gate.sha256(path.read_bytes());self.write(receipt,proof)
        docs=gate.primary_sources(self.root,'SPY','2026-09-30')
        self.assertEqual([docs[item['url']]['text'] for item in proof['sources']],texts)
        path.write_bytes(path.read_bytes().replace(b'carriage',b'changed'))
        with self.assertRaisesRegex(ValueError,'Primary evidence changed'):
            gate.primary_sources(self.root,'SPY','2026-09-30')
        # Even rewriting the outer file hash cannot hide altered per-page bytes.
        proof['text_sha256']=gate.sha256(path.read_bytes());self.write(receipt,proof)
        with self.assertRaisesRegex(ValueError,'Primary page bytes/date differ'):
            gate.primary_sources(self.root,'SPY','2026-09-30')

    def problems(self,text,kind='fact',claim=None,coverage=None,symbol='SPY'):
        a=article(text,kind);ctx=copy.deepcopy(self.ctx);ctx['requirements']=gate.requirements(a,card())
        review=copy.deepcopy(self.review)
        review['editorial_audit']['coverage']=coverage if coverage is not None else []
        ctx['material_context']=[] if coverage is None else ctx['material_context']
        review['editorial_audit']['claims']=[] if claim is None else [claim]
        bundle={**self.bundle,'story_id':symbol,'sources':[{'source_type':'engine_export',
                'payload':{'window':{'start_date':'2026-10-10'}}}]}
        return gate.problems(a,bundle,ctx,review)

    def claim(self,**kwargs):
        return {'unit_id':'sections.0.paragraphs.0','status':'supported','source_url':'',
                'source_quote':'','event_date':'','cohorts':[],**kwargs}

    def test_amd_both_samples_overlap_is_checked_separately(self):
        errors=self.problems('Five midterm years appear in both sets.',claim=self.claim(cohorts=[
            {'sample':'10','shared_years':[2018,2022]},
            {'sample':'20','shared_years':[2006,2010,2014,2018,2022]}]),symbol='AMD')
        self.assertTrue(any('overlap count is not true for 10' in e for e in errors))

    def test_ibm_required_management_omission_blocks_even_minor_issue(self):
        coverage=[{'item_id':self.material['id'],'status':'missing','article_quote':'','reason':'Missing execution explanation'}]
        errors=self.problems('Clients shifted their capital spending.',coverage=coverage,symbol='IBM')
        self.assertTrue(any('Missing material context' in e for e in errors))
        review=copy.deepcopy(self.review);review['issues']=[{'severity':'minor','category':'factual','problem':'Partly inaccurate overlap'}]
        self.assertTrue(any('Unresolved factual' in e for e in gate.problems(self.article,self.bundle,self.ctx,review)))

    def test_vix_three_escape_types_are_blocked(self):
        for text,fragment in [('The VIX starts near the low end of its range.','before its start'),
                              ('A bet on falling prices worked in all eight.','not attainable')]:
            self.assertTrue(any(fragment in e for e in self.problems(text,symbol='VIX')))
        stale=self.claim(source_url='https://issuer.example/calendar',
            source_quote='The next meeting is scheduled for October 15, 2026.',event_date='2026-09-15')
        self.assertTrue(any('future date' in e for e in self.problems('The next tests are the ones Reuters named.',claim=stale,symbol='VIX')))

    def test_clean_spy_qqq_and_conditional_watch_boundary(self):
        for symbol in ('SPY','QQQ'):
            self.assertEqual(self.problems('The historical sample is small; price risks remain.',symbol=symbol),[])
        claim=self.claim(status='qualified_analysis')
        self.assertEqual(self.problems('The next test is whether earnings could support prices.','analysis',claim),[])
        self.assertTrue(self.problems('The next tests are the ones Reuters named.','fact',claim,symbol='VIX'))

    def test_outlook_disclaimer_does_not_assert_an_upcoming_event(self):
        text=('It covers the next 60 weekdays, September 30–December 22, a separate horizon '
              'from October 23–November 6. It illustrates historical shape, not the next '
              'earnings result or a price target.')
        self.assertFalse(gate.asserts_future_event(text))
        self.assertEqual(self.problems(text,'analysis'),[])
        for disclaimer in ('This does not predict the next earnings report.',
                           'This is not a forecast of the upcoming meeting.'):
            self.assertFalse(gate.asserts_future_event(disclaimer))

    def test_unconfirmed_future_event_is_not_asserted_but_mixed_claim_still_is(self):
        unconfirmed='Saved sources do not confirm when AMD will next report.'
        self.assertFalse(gate.asserts_future_event(unconfirmed))
        self.assertEqual(self.problems(unconfirmed),[])

        positive='The study does not indicate weakness in the upcoming earnings report.'
        self.assertTrue(gate.asserts_future_event(positive))
        self.assertTrue(any('future date' in e for
                            e in self.problems(positive,claim=self.claim())))

        mixed=unconfirmed+' The next meeting is October 15.'
        self.assertTrue(gate.asserts_future_event(mixed))
        self.assertTrue(any('future date' in e for e in
                            self.problems(mixed,claim=self.claim())))
        for conjunction in ('but', 'and', 'yet'):
            self.assertTrue(gate.asserts_future_event(
                'Sources do not confirm the timing '+conjunction+' the next meeting will happen.'))

    def test_negated_event_does_not_hide_positive_event_in_same_paragraph(self):
        for text in ('It illustrates historical shape, not the next earnings result. '
                     'The next meeting is October 15.',
                     'The next meeting is October 15; this is not the next earnings result.',
                     'It is not the next earnings result but the upcoming release that matters.',
                     'Not only the next meeting matters.'):
            self.assertTrue(gate.asserts_future_event(text))
            self.assertTrue(any('future date' in e for e in self.problems(text,claim=self.claim())))
        stale=self.claim(source_url='https://issuer.example/calendar',
                         source_quote='The next meeting is scheduled for October 15, 2026.',
                         event_date='2026-09-15')
        text='This is not the next earnings result. The next meeting is October 15.'
        self.assertTrue(any('future date' in e for e in self.problems(text,claim=stale)))

    def test_analysis_limit_on_causal_inference_is_not_a_causal_assertion(self):
        text=('This small sample shares 2018 and 2022 with the primary study, limiting '
              'independent confirmation and offering no basis for attributing returns to elections.')
        self.assertFalse(gate.asserts_causal_claim(text,'analysis'))
        self.assertEqual(self.problems(text,'analysis'),[])
        for limitation in ('There is no evidence for attributing returns to elections.',
                           'The sample offers no grounds to attribute returns to elections.',
                           'Consider historical shape without attributing returns to elections.'):
            self.assertFalse(gate.asserts_causal_claim(limitation,'analysis'))
            self.assertTrue(gate.asserts_causal_claim(limitation,'fact'))

    def test_inference_limit_does_not_hide_positive_or_negative_causal_claims(self):
        for text in ('There is no basis for attributing returns to elections. The decline was caused by demand.',
                     'The decline was driven by demand, without attributing returns to elections.',
                     'The decline was not caused by elections.',
                     'Returns cannot be attributed to elections.',
                     'There is no evidence that the decline was caused by elections.'):
            for kind in ('analysis','fact'):
                self.assertTrue(gate.asserts_causal_claim(text,kind))
                self.assertTrue(any('causal explanation' in e for e in
                                    self.problems(text,kind,claim=self.claim())))


if __name__=='__main__':unittest.main()
