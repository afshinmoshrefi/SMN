"""Continuity regressions, including the concrete generic-news fallback defect."""
import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from seasonal_edition import bind_source, check_article, prepare, study_link, inspect_native
from visual_editorial import render_edition, run_visual_edition
from visual_evidence import digest
from test_visual_editorial import bundle as news_bundle, article as news_article, seal


def fixture():
    rows=[{'year':2023,'net':5,'mfe':8,'mae':-2},
          {'year':2024,'net':-3,'mfe':2,'mae':-6},
          {'year':2025,'net':0,'mfe':0,'mae':0}]
    card={'symbol':'FIXTURE','resource_id':'2','angle':{'name':'QUIET_EDGE'},
          'instrument':{'resource_id':'2','symbol':'FIXTURE','semantics':{'measurement':'adjusted_price_return'}},
          'story_cell':{'symbol':'FIXTURE','resource_id':'2','anchor_date':'2026-08-21',
                        'days':90,'years':'3','n':3,'per_year':rows},
          'selection_evidence':{'baseline':{'per_year':rows}}}
    b=news_bundle();b['edition_type']='seasonal'
    b['sources'].append({'id':'history','title':'Fictional test history','source_type':'derived',
        'url':'evidence/history.json','excerpt':'Fictional historical observations for offline tests.',
        'payload':card['selection_evidence'],'payload_sha256':digest(card['selection_evidence'])})
    b['seasonal_contract']={'card_sha256':digest(card),'history_source_id':'history','angle':'QUIET_EDGE',
        'viewer_url':'https://tradewave.ai/app/','methodology_url':'https://example.org/methodology'}
    seal(b)
    source={'text_ready':True,'status':'ready','html':'<article>Qualified fixture</article>','card':card}
    a=news_article();p=deepcopy(a['sections'][1]['paragraphs'][0]);p['source_ids']=['history']
    a['sections']=[{'role':'opening','heading':'','paragraphs':deepcopy(a['sections'][0]['paragraphs']), 'chart_id':None},
        {'role':'seasonal_record','heading':'Seasonal record','paragraphs':[p], 'native_chart_id':'bars'},
        {'role':'current_context','heading':'Current context','paragraphs':deepcopy(a['sections'][1]['paragraphs']), 'chart_id':'comparison'},
        {'role':'risk','heading':'The range from entry','paragraphs':[deepcopy(p)],'native_chart_id':'bars_mae_mfe'},
        {'role':'outlook','heading':'What comes next','paragraphs':deepcopy(a['sections'][2]['paragraphs'])}]
    a['angle_delivery']={'angle':'QUIET_EDGE','seasonal_question':'What does the fictional seasonal window show?',
        'seasonal_contribution':'Fictional seasonal behavior supplies a research question.',
        'current_connection':'The fictional event supplies context for the research window.'}
    return source,b,a


class SeasonalContinuityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.root=Path(cls.temp.name)
        cls.source,cls.bundle,cls.article=fixture()
        with patch('matplotlib.pyplot.rcParams', __import__('matplotlib').rcParams):
            import chartkit
            chartkit.plt.rcParams['font.family']='DejaVu Sans'
            cls.data=prepare(cls.source,cls.bundle,cls.root)

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def test_original_defect_rejected_before_model_call(self):
        s,b,a=fixture();b.pop('edition_type');b.pop('seasonal_contract');seal(b)
        def no_call(_):self.fail('A seasonal source must not reach the generic news writer')
        with self.assertRaisesRegex(ValueError,'seasonal edition contract'):
            run_visual_edition(s,b,output_dir=self.root/'never-started',send=no_call)
        self.assertFalse((self.root/'never-started').exists())

    def test_study_link_preserves_all_five_parameters_and_inclusive_end(self):
        token=parse_qs(urlparse(study_link(self.source['card'],'https://tradewave.ai/app/')).query)['o'][0]
        self.assertEqual(base64.b64decode(token).decode(),'2|FIXTURE|2026-08-21|90|3')
        self.assertEqual(self.data['evidence']['window']['end_date'],'2026-11-18')

    def test_inline_study_link_is_rendered_but_cannot_change_destination(self):
        article=deepcopy(self.article)
        paragraph=article['sections'][0]['paragraphs'][0]
        paragraph['text']='Inspect [the TradeWave study]('+self.data['study_url']+').'
        with patch('visual_charts.figure_html',return_value='chart'):
            rendered=render_edition(article,self.bundle,{'comparison':{}},seasonal=self.data)
        self.assertIn('>the TradeWave study</a>',rendered)
        self.assertNotIn('[the TradeWave study]',rendered)
        paragraph['text']='Inspect [the study](https://wrong.example/).'
        with self.assertRaisesRegex(ValueError,'inline link'):
            render_edition(article,self.bundle,{'comparison':{}},seasonal=self.data)

    def test_real_native_renderer_keeps_flat_completed_year(self):
        for im in self.data['images']:
            self.assertEqual(im['renderer'],'chartkit.record_bars')
            self.assertEqual(im['semantics']['n'],3)
            self.assertEqual(im['semantics']['observed_years'],[2023,2024,2025])
            self.assertTrue(Path(im['path']).read_bytes().startswith(b'\x89PNG'))

    def test_wrong_baseline_or_angle_is_held(self):
        for field,value in [('years','20'),('anchor_date','2026-08-22')]:
            s,b,a=fixture();s['card']['story_cell'][field]=value
            with self.assertRaisesRegex(ValueError,'not bound'):bind_source(s,b)
        s,b,a=fixture();b['seasonal_contract']['angle']='CLOCKWORK'
        with self.assertRaisesRegex(ValueError,'angle differs'):bind_source(s,b)

    def test_required_native_charts_cannot_be_omitted(self):
        for i in (1,3):
            s,b,a=fixture();a['sections'][i].pop('native_chart_id')
            with self.assertRaises(ValueError):check_article(a,b)

    def test_preserves_commissioned_angle_instead_of_replacing_it(self):
        for angle in ('CLOCKWORK','REGIME','FORK','COLLISION','TAILWIND','QUIET_EDGE','GROWTH_CHECK'):
            s,b,a=fixture();b['seasonal_contract']['angle']=angle;a['angle_delivery']['angle']=angle
            self.assertEqual(check_article(a,b)['angle'],angle)
            a['angle_delivery']['angle']='UNAUTHORIZED_REPLACEMENT'
            with self.assertRaises(ValueError):check_article(a,b)

    def test_template_cannot_render_seasonal_without_protected_evidence(self):
        with self.assertRaisesRegex(ValueError,'without TradeWave evidence'):
            render_edition(self.article,self.bundle,{},None)

    def test_integrated_order_keeps_both_visual_types_and_one_summary(self):
        with patch('visual_charts.figure_html',return_value='<figure id="extra-editorial-chart"></figure>'):
            rendered=render_edition(self.article,self.bundle,{'comparison':{}},
                 {'url':'hero.png','alt':'Fictional hero','caption':'Illustration'},seasonal=self.data)
        self.assertEqual(rendered.count('<h2>Key Takeaways</h2>'),1)
        self.assertLess(rendered.index('class="hero"'),rendered.index('<h2>Key Takeaways'))
        self.assertIn('TradeWave Key Stats',rendered)
        self.assertIn('data-native-chart="bars"',rendered)
        self.assertIn('data-native-chart="bars_mae_mfe"',rendered)
        self.assertIn('extra-editorial-chart',rendered)
        self.assertIn('Open FIXTURE in TradeWave',rendered)
        self.assertIn('About This Seasonal Analysis',rendered)
        self.assertIn('native_chart_inspection_missing',inspect_native(self.data,self.root,rendered,{},self.bundle))


if __name__=='__main__':unittest.main()
