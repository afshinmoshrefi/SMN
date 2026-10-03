from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import briefing_daily as daily
import briefing_sources as sources
from daily_briefing import digest
from subscription_writer import save_json,load_json


class Response:
    def __init__(self,body):self.body=body
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self,n):return self.body[:n]


class Opener:
    def __init__(self,article):self.article=article;self.calls=[]
    def open(self,request,timeout):
        self.calls.append(request.full_url)
        if request.full_url.endswith('/rss'):
            return Response(b'<rss><channel><item><title>A major economic event deserves full reporting</title>'
                b'<link>https://www.cnbc.com/2026/10/03/event.html</link>'
                b'<pubDate>Sat, 03 Oct 2026 09:00:00 GMT</pubDate></item></channel></rss>')
        return Response(self.article)


class Daily(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.cutoff='2026-10-03T07:00:00-04:00'

    def article(self,free=True,words=310):
        return ('<html><script type="application/ld+json">'+json.dumps({'@type':'NewsArticle',
            'isAccessibleForFree':free,'url':'https://www.cnbc.com/2026/10/03/event.html',
            'articleBody':' '.join(['evidence']*words)+'.',
            'datePublished':'2026-10-03T09:00:00Z'})+'</script></html>').encode()

    def test_feed_future_stale_and_cross_host_links_excluded(self):
        raw=b'<rss><channel><item><title>A real headline has several specific words</title><link>https://evil.test/article</link></item>'\
            b'<item><title>A real headline has several specific words</title><link>https://www.cnbc.com/story</link>'\
            b'<pubDate>Sat, 03 Oct 2026 12:00:00 GMT</pubDate></item></channel></rss>'
        self.assertEqual(daily.candidates(raw,'https://www.cnbc.com/rss','CNBC',datetime.fromisoformat(self.cutoff)),[])

    def test_restricted_or_short_body_never_qualified(self):
        for free,words in [(False,310),(True,30)]:
            parser=sources.Document();parser.feed(self.article(free,words).decode())
            self.assertEqual(parser.free_bodies,[])

    def test_automated_full_capture_then_subscription_draft_is_review_only(self):
        opener=Opener(self.article())
        with patch.object(daily,'PUBLISHERS',[('CNBC','https://www.cnbc.com/rss')]),\
                patch('subscription_writer.utc_now',return_value='2026-10-03T10:00:00Z'),\
                patch.object(sources,'write_draft',return_value={'issues':[]}) as draft:
            result=daily.run(self.root/'run','2026-10-03',self.cutoff,codex='codex',model='gpt-5.6-sol',effort='medium',mode='full_text',opener=opener)
            bundle=draft.call_args.args[0]
            self.assertEqual(bundle['label'],'Before the Open');self.assertEqual(bundle['sources'][0]['access'],'full_text')
            self.assertEqual(draft.call_args.kwargs['model'],'gpt-5.6-sol')
        self.assertFalse(result['publish']);self.assertEqual(result['review_status'],'pending')
        self.assertEqual(len(list((self.root/'run/capture').glob('*.html'))),1)

    def test_capture_after_cutoff_does_not_retroactively_qualify(self):
        with patch.object(daily,'PUBLISHERS',[('CNBC','https://www.cnbc.com/rss')]),\
                patch('subscription_writer.utc_now',return_value='2026-10-03T12:00:00Z'),\
                patch.object(sources,'write_draft') as draft:
            result=daily.run(self.root/'late','2026-10-03',self.cutoff,codex='codex',model='gpt-5.6-sol',effort='medium',mode='full_text',opener=Opener(self.article()))
            draft.assert_not_called()
        self.assertEqual(result['qualified_count'],0);self.assertTrue(any('after cutoff' in h for h in result['holds']))

    def test_no_restricted_facts_sent_to_writer(self):
        with patch.object(daily,'PUBLISHERS',[('CNBC','https://www.cnbc.com/rss')]),\
                patch('subscription_writer.utc_now',return_value='2026-10-03T10:00:00Z'),\
                patch.object(sources,'write_draft') as draft:
            result=daily.run(self.root/'restricted','2026-10-03',self.cutoff,codex='codex',model='gpt-5.6-sol',effort='medium',mode='full_text',opener=Opener(self.article(False)))
            draft.assert_not_called()
        self.assertEqual(result['qualified_count'],0)

    def test_embedded_different_article_body_cannot_qualify(self):
        other=self.article().replace(b'/event.html',b'/different.html')
        with patch.object(daily,'PUBLISHERS',[('CNBC','https://www.cnbc.com/rss')]),\
                patch('subscription_writer.utc_now',return_value='2026-10-03T10:00:00Z'),\
                patch.object(sources,'write_draft') as draft:
            result=daily.run(self.root/'other','2026-10-03',self.cutoff,codex='codex',model='gpt-5.6-sol',effort='medium',mode='full_text',opener=Opener(other))
            draft.assert_not_called()
        self.assertEqual(result['qualified_count'],0)

    def test_headline_default_uses_official_feed_without_fetching_restricted_story(self):
        endpoint=daily.HEADLINE_FEEDS['CNBC']
        class FeedOnly(Opener):
            def open(self,request,timeout):
                self.calls.append(request.full_url)
                if request.full_url!=endpoint:raise AssertionError('Article access attempted')
                return Response(b'<rss><channel><item><title>A major economic event deserves full reporting</title>'
                    b'<link>https://www.cnbc.com/2026/10/03/event.html</link>'
                    b'<pubDate>Sat, 03 Oct 2026 09:00:00 GMT</pubDate></item></channel></rss>')
        opener=FeedOnly(b'')
        with patch.object(daily,'PUBLISHERS',[('CNBC',endpoint)]),\
                patch('subscription_writer.utc_now',return_value='2026-10-03T10:00:00Z'),\
                patch.object(sources,'write_draft',return_value={'issues':[]}) as draft:
            result=daily.run(self.root/'headlines','2026-10-03',self.cutoff,codex='codex',model='gpt-5.6-sol',effort='medium',opener=opener)
            source=draft.call_args.args[0]['sources'][0]
            self.assertEqual(source['access'],'headline_only')
            self.assertEqual(source['capture']['scope'],'headline_record')
        self.assertEqual(opener.calls,[endpoint]);self.assertEqual(result['qualified_count'],1)
        self.assertFalse(result['publish'])

    def test_wrong_new_york_edition_rejected_before_access(self):
        with self.assertRaises(ValueError):daily.run(self.root/'bad','2026-10-04',self.cutoff,codex='codex',model='gpt-5.6-sol',effort='medium')
        self.assertFalse((self.root/'bad').exists())

    def test_failed_capture_registry_holds_remain_visible_and_immutable(self):
        output=self.root/'run';output.mkdir();bundle={'edition_date':'2026-10-03','sources':[]}
        receipt={'status':'source_review_required','source_bundle_sha256':digest(bundle),
            'holds':['Restricted full text unavailable'],'publish':False,'review_status':'pending'}
        save_json(output/'qualified-sources.json',bundle);save_json(output/'run.receipt.json',receipt)
        path=Path(daily.register(output,self.root/'registry','2026-10-03-before-open'))
        self.assertEqual(load_json(path/'capture-status.json')['holds'],receipt['holds'])
        self.assertFalse((path/'briefing.json').exists());self.assertFalse((path/'review.json').exists())
        bundle['sources']=[{'changed':'capture'}];save_json(output/'qualified-sources.json',bundle)
        receipt['source_bundle_sha256']=digest(bundle);save_json(output/'run.receipt.json',receipt)
        with self.assertRaises(ValueError):daily.register(output,self.root/'registry','2026-10-03-before-open')
        self.assertEqual(load_json(path/'sources.json')['sources'],[])


if __name__=='__main__':unittest.main()
