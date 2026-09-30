import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import smn_research as research
import smn_primary_sources as primary


class MaterialContextTests(unittest.TestCase):
    def setUp(self):
        self.quote = 'Delayed large deals and our failure to adapt drove the majority of our shortfall.'
        self.item = {'id':'management-causes','kind':'cause','source_id':'letter',
                     'quote':self.quote,'summary':'Management identifies execution problems and delayed deals.',
                     'required':True,'event_date':''}
        self.entry = {'sources':[{'id':'letter','url':'https://issuer.example/letter'}],
                      'material_context':[self.item]}
        self.documents = {'https://issuer.example/letter':{'text':'CEO statement. '+self.quote}}

    def check(self):
        with patch('editorial_gate.primary_sources',return_value=self.documents):
            return research.check_material_context(self.entry,Path('.'),'2026-09-30','ABC')

    def test_verbatim_cause_passes_and_wrong_source_or_paraphrase_fails(self):
        self.assertEqual(self.check(),[])
        self.item['quote']='Management blamed the miss on client spending alone.'
        self.assertTrue(self.check())
        self.item['quote']=self.quote
        self.entry['sources'][0]['url']='https://secondary.example/story'
        self.assertTrue(self.check())

    def test_bad_date_duplicate_and_custody_fail_closed(self):
        self.item['event_date']='2026-09-31'
        self.assertTrue(any('event_date' in p for p in self.check()))
        self.item['event_date']='2026-10-15'
        self.assertEqual(self.check(),[])  # A verified upcoming event may follow publication.
        self.entry['material_context'].append(copy.deepcopy(self.item))
        self.assertTrue(any('unique' in p for p in self.check()))
        with patch('editorial_gate.primary_sources',side_effect=ValueError('changed bytes')):
            self.assertTrue(any('custody' in p for p in research.check_material_context(
                self.entry,Path('.'),'2026-09-30','ABC')))

    def test_contract_is_required_and_propagated_without_changes(self):
        self.assertIn('material_context',research.SCHEMA['required'])
        entry={**self.entry,**{k:'value' for k in ('company','angle','category','question','brief','hero_alt')},
               'chart':{'spec':{'rows':[]},'records':[]}}
        before=copy.deepcopy(entry)
        self.assertEqual(research.to_sources(entry)['material_context'],[self.item])
        self.assertEqual(entry,before)
        self.entry['material_context']=[None]
        self.assertTrue(self.check())

    def test_selected_evidence_uses_exact_export_without_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'production').mkdir()
            (root/'production/posts.json').write_text(json.dumps([{'symbol':'ABC',
                'source_mode':'selected_inputs','lookback_years':10}]))
            stats={'retained_marker':'engine-owned-exact','median_return':17.123456789}
            export={'studies':[{'identity':{'symbol':'ABC'},'responses':[
                {'request':{'years':10},'response':{'stats':stats}}]}]}
            (root/'production-engine-export.json').write_text(json.dumps(export))
            with patch.object(research,'saved_text',return_value='primary'):
                prompt=research.evidence(root,'2026-09-30','ABC',{})
            self.assertIn(json.dumps(stats),prompt)
            self.assertFalse((root/'production/ABC/engine-payload.json').exists())

    def test_collector_captures_all_four_discovered_pages(self):
        rows=[{'url':'https://issuer.example/'+str(i),'title':'Official letter',
               'date':'2026-09-25','publisher':'Issuer','reason':'Management context'} for i in range(4)]
        cache={'pages':{},'failed_urls':{}}
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(primary,'fetch_page',side_effect=lambda url:(url,
                 ('2026-09-25 management statement and official explanation. ')*30)) as fetched:
            primary._capture_rows(rows,cache,Path(directory)/'cache.json')
        self.assertEqual(fetched.call_count,4)
        self.assertEqual(len(cache['pages']),4)


if __name__=='__main__':unittest.main()
