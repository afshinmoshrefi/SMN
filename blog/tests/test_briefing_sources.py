import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from briefing_sources import Document,capture, write_draft
from daily_briefing import digest


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


if __name__=='__main__':unittest.main()
