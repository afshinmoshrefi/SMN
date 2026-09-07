"""Private visual workflow regressions: units, baselines, mutation and holds."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from visual_evidence import chart_data, digest, format_value, validate_bundle
from visual_charts import figure_html, render_catalog
from visual_editorial import (check_article, generate_visual_private_article,
    hero_request, install_hero, render_edition, run_visual_edition, REVIEW_CHECKS)
from visual_review import inspect_edition


def seal(b):
    b.pop('evidence_sha256', None)
    b['evidence_sha256'] = digest(b)
    return b


def bundle():
    return seal({'schema_version':1,'story_id':'fixture','as_of':'2026-09-06T12:00:00Z',
        'source_article_sha256':hashlib.sha256(b'<article>Qualified fixture</article>').hexdigest(),
        'sources':[{'id':'source','url':'https://example.org/verified','title':'Fictional evidence',
                    'excerpt':'Fictional data for offline verification only.'}],
        'records':[{'id':'old','value':-23,'unit':'thousand_jobs','period':'2026-07',
                    'source_id':'source','locator':'row 1','status':'previous_estimate'},
                   {'id':'new','value':162,'unit':'thousand_jobs','period':'2026-08',
                    'source_id':'source','locator':'row 2','status':'reported'}],
        'charts':[{'id':'comparison','kind':'bars','title':'Fixture comparison','subtitle':'Monthly changes',
                   'question':'How much did the value change?','note':'Seasonally adjusted fixture, not real data.',
                   'unit':'thousand_jobs','rows':[{'record_id':'old','label':'July previous'}, {'record_id':'new','label':'August initial'}]}],
        'primary_chart_id':'comparison','required_context':[]})


def article():
    # Enough real prose to exercise the renderer and review binding, not a
    # fixture that can pass with empty paragraphs or schema-only placeholders.
    texts=[
      'The fictional August report changed the picture after a weak July estimate. For readers assessing the next policy meeting, the useful question is whether that improvement persists. The chart shows the scale of the change and retains the earlier estimate rather than silently replacing the old figure.',
      'The increase is one observation in a changing series. It offers information about the latest month but does not establish the direction of the next report. Readers should watch subsequent releases to see whether the improvement broadens or is revised away when more complete information becomes available.',
      'A comparison needs the same units on both sides. The chart measures changes in thousands of jobs and shows the zero line so the negative observation remains visibly distinct. Its labels identify the dates and estimate status, and the accessible table preserves the exact values supplied for this fictional example.',
      'The available evidence describes an increase in reported activity without establishing its cause. It cannot reveal the intentions of individual participants or support a prediction about an unrelated market. Keeping that distinction clear allows the reader to use the information without asking the graphic to answer a question it cannot resolve.',
      'The next scheduled release offers a chance to reassess the interpretation with new evidence. A stronger observation would add context, while a downward revision would change the comparison. Neither outcome can be inferred from the current chart, which is a description of supplied history rather than a forecast of what happens next.'
    ]
    para=lambda t:{'text':t,'source_ids':['source'],'kind':'analysis'}
    return {'title':'Fictional report changes the comparison','title_source_ids':['source'],
        'dek':'A stronger observation raises a useful question about persistence.','dek_source_ids':['source'],
        'sections':[{'heading':'','paragraphs':[para(texts[0])],'chart_id':'comparison'},
                    {'heading':'What the comparison adds','paragraphs':[para(t) for t in texts[1:3]],'chart_id':None},
                    {'heading':'What comes next','paragraphs':[para(t) for t in texts[3:]],'chart_id':None}],
        'takeaways':[{'text':'This fictional chart preserves the earlier estimate so the change is visible.','source_ids':['source']},
                     {'text':'The next report is a useful checkpoint for the interpretation.','source_ids':['source']}],
        'visual_decisions':[{'chart_id':'comparison','reader_value':'Makes the size and direction of the revision visible.'}],
        'omitted_chart_reasons':{}}


def review(passed=True):
    return {'passed':passed,'checks':[{'check':c,'verdict':'pass' if passed else 'fail',
        'observation':'Offline fixture: checked the exact passage against the supplied evidence ledger.'} for c in REVIEW_CHECKS],
        'issues':[] if passed else ['The wording overstates what the supplied evidence establishes.']}


class EvidenceTests(unittest.TestCase):
    def test_half_up_rounding_and_units(self):
        self.assertEqual(format_value(5.135,'percent'),'+5.14%')
        self.assertEqual(format_value(-3.015,'percent'),'-3.02%')
        self.assertEqual(format_value(-0.001,'percent'),'+0%')
        self.assertEqual(format_value(162,'thousand_jobs'),'+162')
        self.assertEqual(format_value(20.5,'thousand_jobs'),'+20.5')
        self.assertEqual(format_value(1005000,'shares'),'1.01m')

    def test_model_cannot_insert_inline_numbers(self):
        b=bundle();b['charts'][0]['rows'][0]['value']=99;seal(b)
        with self.assertRaises(ValueError):validate_bundle(b)

    def test_mutation_detected(self):
        b=bundle();b['records'][0]['value']=23
        with self.assertRaises(ValueError):validate_bundle(b)

    def test_unknown_source_and_mixed_units_rejected(self):
        for key,value in [('source_id','unknown'),('unit','percent')]:
            b=bundle();b['records'][0][key]=value;seal(b)
            with self.assertRaises(ValueError):validate_bundle(b)

    def test_nonfinite_and_boolean_values_rejected(self):
        for value in [True,float('nan'),float('inf'),None,'162']:
            b=bundle();b['records'][0]['value']=value
            with self.assertRaises((ValueError,TypeError)):validate_bundle(b)

    def test_untrusted_local_source_rejected(self):
        b=bundle();b['sources'][0]['url']='../../secret.json';seal(b)
        with self.assertRaises(ValueError):validate_bundle(b)

    def test_forecast_not_silently_rendered_as_actual(self):
        b=bundle();b['records'][0]['status']='forecast';seal(b)
        with self.assertRaises(ValueError):validate_bundle(b)

    def test_volume_excludes_latest_and_keeps_zero(self):
        b=bundle();b['records']=[];rows=[]
        for i in range(21):
            b['records'].append({'id':f'r{i}','value':100 if i<20 else 1200,'unit':'shares',
                'period':f'2026-08-{i+1:02}','source_id':'source','locator':f'row {i}','status':'reported'})
            rows.append({'record_id':f'r{i}','label':str(i)})
        b['charts'][0].update(kind='volume',unit='shares',rows=rows,adjustment='provider_split_adjusted_volume');seal(b)
        data=chart_data(validate_bundle(b)['charts'][0],b)
        self.assertEqual(data['prior_median'],100)
        self.assertEqual(data['relative_volume'],12)
        b['records'][-1]['value']=0;seal(b)
        self.assertEqual(chart_data(b['charts'][0],b)['relative_volume'],0)
        b['records'][-1]['period']=b['records'][-2]['period'];seal(b)
        with self.assertRaises(ValueError):validate_bundle(b)
        b['records'][-1]['period']='2027-01-01';seal(b)
        with self.assertRaises(ValueError):validate_bundle(b)


class WorkflowTests(unittest.TestCase):
    def test_required_early_chart_and_source_citations(self):
        a=article();check_article(a,bundle())
        a['sections'][0]['chart_id']=None
        with self.assertRaises(ValueError):check_article(a,bundle())

    def test_final_pixel_gate_rejects_changed_charts_and_untrusted_reviewer(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);b=bundle();req=hero_request(b,article()['title'])
            hero=root/'fixture.png';Image.new('RGB',(1200,675),'#224466').save(hero)
            r=run_visual_edition({'status':'ready','text_ready':True,'html':'<article>Qualified fixture</article>'},
               b,output_dir=root/'edition',send=lambda _:json.dumps(article()),review_send=lambda _:json.dumps(review()),
               hero_asset={'path':str(hero),'prompt_sha256':req['prompt_sha256']})
            edition=root/'edition'
            manifest=json.loads((edition/'chart-manifest.json').read_text(encoding='utf-8'))
            views=[]
            for kind,width in [('desktop',1200),('mobile',390)]:
                path=root/(kind+'.png');Image.new('RGB',(width,850),'white').save(path)
                views.append({'kind':kind,'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
            vr={'article_sha256':hashlib.sha256(r['article_html'].encode()).hexdigest(),
                'evidence_sha256':b['evidence_sha256'],'asset_sha256':r['hero']['sha256'],
                'reviewer_id':'trusted-fixture','method':'vision','reviewed_at':'2026-09-07T02:00:00Z','views':views,
                'checks':[{'check':c,'verdict':'pass','observation':'Offline fixture observations, not an actual visual approval.'}
                          for c in ['lettering','identity','provenance','crop','factual_implication']],
                'chart_reviews':[{'chart_id':'comparison','verdict':'pass',
                    'numeric_observation':'Fixture values remain -23 and +162 thousand jobs.',
                    'desktop_observation':'Fixture desktop rendering is recorded for hash validation.',
                    'mobile_observation':'Fixture mobile rendering is recorded for hash validation.',
                    'file_sha256':manifest['comparison']['file_sha256']}]}
            self.assertTrue(inspect_edition(r,b,edition,vr,trusted_reviewers=['trusted-fixture'])['ready'])
            self.assertFalse(inspect_edition(r,b,edition,vr)['ready'])
            chartpath=edition/'assets'/manifest['comparison']['paths']['mobile_png']
            chartpath.write_bytes(chartpath.read_bytes()+b'changed')
            failed=inspect_edition(r,b,edition,vr,trusted_reviewers=['trusted-fixture'])
            self.assertFalse(failed['ready'])
            self.assertTrue(any(i.startswith('chart_file_changed:') for i in failed['issues']))
        a=article();a['sections'][0]['paragraphs'][0]['source_ids']=['invented']
        with self.assertRaises(ValueError):check_article(a,bundle())

    def test_missing_hero_never_becomes_visual_ready(self):
        with tempfile.TemporaryDirectory() as d:
            result=run_visual_edition({'status':'ready','text_ready':True,'html':'<article>Qualified fixture</article>'},
               bundle(),output_dir=d,send=lambda _:json.dumps(article()),review_send=lambda _:json.dumps(review()))
            self.assertEqual(result['status'],'text_ready_visual_pending')
            self.assertFalse(result['visual_ready']);self.assertFalse(result['publishable'])
            self.assertEqual(result['provider_calls'],2)
            h=hashlib.sha256(result['article_html'].encode()).hexdigest()
            self.assertEqual(result['reviews'][-1]['article_sha256'],h)
            self.assertIn('media="(max-width: 600px)"',result['article_html'])
            self.assertIn('thousand jobs',result['article_html'])
            self.assertNotIn('+162,000',result['article_html'])
            with self.assertRaises(FileExistsError):
                run_visual_edition({'status':'ready','text_ready':True,'html':'<article>Qualified fixture</article>'},
                    bundle(),output_dir=d,send=lambda _:self.fail('rerun spent money'))

    def test_editorial_failure_is_bounded_and_visible(self):
        with tempfile.TemporaryDirectory() as d:
            r=run_visual_edition({'status':'ready','text_ready':True,'html':'<article>Qualified fixture</article>'},
               bundle(),output_dir=d,send=lambda _:json.dumps(article()),review_send=lambda _:json.dumps(review(False)))
            self.assertEqual(r['provider_calls'],4);self.assertEqual(r['revisions'],1)
            self.assertFalse(r['text_ready']);self.assertIn('Editorial review has not passed',r['article_html'])

    def test_source_article_binding_and_upstream_hold(self):
        with tempfile.TemporaryDirectory() as d:
            for source in [{'status':'hold','text_ready':False}, {'status':'ready','text_ready':True,'html':'changed'}]:
                with self.assertRaises(ValueError):run_visual_edition(source,bundle(),output_dir=d)
        with patch('private_selection.generate_private_article',return_value={'status':'hold','text_ready':False}):
            r=generate_visual_private_article({},visual_bundle=lambda _:self.fail('bypassed selection'),output_dir='unused')
            self.assertEqual(r['hold_reason'],'upstream_text_not_ready')

    def test_chart_exports_and_no_negative_clipping_configuration(self):
        with tempfile.TemporaryDirectory() as d:
            assets=render_catalog(bundle(),d)
            a=assets['comparison']
            for file,sha in a['file_sha256'].items():
                self.assertEqual(hashlib.sha256((Path(d)/file).read_bytes()).hexdigest(),sha)
            svg=(Path(d)/a['paths']['mobile_svg']).read_text(encoding='utf-8')
            self.assertIn('Thousands of jobs',svg)
            self.assertIn('July previous',svg)
            self.assertIn('+162',svg)
            self.assertIn('-23',svg)


if __name__=='__main__':unittest.main()
