import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from briefing_sources import Document,capture, write_draft,writing_schema
from daily_briefing import digest
from subscription_writer import load_json,validate_schema


class Sources(unittest.TestCase):
    def manifest(self):
        return {'edition_date':'2026-10-02','cutoff':'2026-10-02T23:59:59-04:00','sources':[
            {'id':'agency','title':'Release','publisher':'BLS','origin_id':'bls',
             'url':'https://www.bls.gov/news.release/archives/empsit_10022026.htm',
             'access':'primary_release','access_verified':True}]}

    def test_capture_keeps_offsetless_time_uncertain_and_binds_observation(self):
        html=b'<html><meta property="article:published_time" content="2026-10-02T08:30:00"><body>'+b'Release facts. '*80+b'</body></html>'
        opener=Mock(); opener.open.return_value=io.BytesIO(html)
        with tempfile.TemporaryDirectory() as d:
            result=capture(self.manifest(),Path(d)/'capture',opener=opener)
        source=result['sources'][0]
        self.assertIsNone(source['published_at'])
        self.assertEqual(source['timestamp_status'],'uncertain')
        self.assertEqual(source['capture']['observation_sha256'],digest({'url':source['url'],
            'retrieved_at':source['retrieved_at'],'text_sha256':source['capture']['text_sha256']}))

    def test_restricted_or_unverified_text_never_becomes_fact_evidence(self):
        opener=Mock(); opener.open.return_value=io.BytesIO(b'<body>Subscribe to continue reading '+b'headline '*100+b'</body>')
        with tempfile.TemporaryDirectory() as d:
            result=capture(self.manifest(),Path(d)/'capture',opener=opener)
            self.assertEqual(result['sources'][0]['access'],'metadata')
            with patch('briefing_sources.writer.run_job') as run:
                with self.assertRaises(ValueError): write_draft(result,Path(d)/'write',codex='codex',model='model',effort='low')
                run.assert_not_called()

    def test_unsafe_url_and_source_filename_never_fetch(self):
        for field,value in [('url','https://127.0.0.1/'),('id','../../secret')]:
            manifest=self.manifest();manifest['sources'][0][field]=value;opener=Mock()
            with tempfile.TemporaryDirectory() as d, self.assertRaises(ValueError):
                capture(manifest,Path(d)/'capture',opener=opener)
            opener.open.assert_not_called()

    def test_actual_jsonld_offset_is_retained_without_assuming_timezone(self):
        document=Document();document.feed('<script type="application/ld+json">'+
            '{"@type":"NewsArticle","datePublished":"2026-10-02T21:23:22.000Z","publisher":{"name":"Thomson Reuters"}}</script><p>Actual article</p>')
        self.assertEqual(document.dates['article:published_time'],'2026-10-02T21:23:22.000Z')
        self.assertNotIn('datePublished',''.join(document.parts))

    def test_writer_prompt_targets_short_spoken_script_and_broad_written_coverage(self):
        example=load_json(Path(__file__).resolve().parents[1]/'examples/daily-briefing/2026-10-02-end-of-day.json')
        with tempfile.TemporaryDirectory() as directory,patch('briefing_sources.writer.prepare_job') as prepare,\
                patch('briefing_sources.writer.run_job',return_value={'status':'held'}):
            with self.assertRaises(ValueError):write_draft(example,Path(directory)/'draft',codex='codex',model='model',effort='medium')
        prompt=prepare.call_args.args[2]
        self.assertIn('100-145 words total',prompt)
        self.assertIn('45-60 seconds',prompt)
        self.assertIn('all major captured headline clusters',prompt)
        self.assertIn('exact headline-only scope note in the spoken script',prompt)

    def test_strict_writer_schema_preserves_exact_optional_capture_shapes(self):
        schema=load_json(Path(__file__).resolve().parents[1]/'schemas/daily_briefing.schema.json')
        example=load_json(Path(__file__).resolve().parents[1]/'examples/daily-briefing/2026-10-02-end-of-day.json')
        original=digest(schema);strict=writing_schema(schema,example['sources'])
        self.assertEqual(digest(schema),original)
        variants=strict['properties']['sources']['items']['anyOf']
        for source,variant in zip(example['sources'],variants):
            self.assertEqual(set(variant['required']),set(source))
            validate_schema(source,variant)
        self.assertEqual(variants[0]['properties']['capture']['properties']['text_sha256']['enum'],[example['sources'][0]['capture']['text_sha256']])
        changed=dict(example['sources'][0],title='Changed title')
        import jsonschema
        with self.assertRaises(jsonschema.ValidationError):jsonschema.validate(changed,variants[0])
        self.assertNotIn('updated_at',variants[0]['properties'])
        self.assertNotIn('observation_sha256',variants[0]['properties']['capture']['properties'])
        def check(node):
            if 'properties' in node:
                self.assertEqual(set(node['required']),set(node['properties']))
                self.assertIs(node['additionalProperties'],False)
                for child in node['properties'].values():check(child)
            if 'items' in node:check(node['items'])
            for child in node.get('anyOf',[]):check(child)
            self.assertNotIn('const',node);self.assertNotIn('uniqueItems',node)
        check(strict)


if __name__=='__main__':unittest.main()
