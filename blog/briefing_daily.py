"""Single-run publisher discovery, immutable evidence capture and private draft.

Suitable for a Dev timer; this command never approves, publishes or schedules.
All discovery endpoints and article hosts are server-owned constants.
"""
import argparse
import os
import tempfile
from datetime import datetime,timedelta,timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
import re
from urllib.parse import urljoin,urlsplit,urlunsplit
from urllib.request import Request,build_opener
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

import briefing_sources as sources
from daily_briefing import digest,HEADLINE_FEEDS,HEADLINE_NOTE,headline_record,headline_text
from elevenlabs_client import _NoRedirect
import subscription_writer as writer


PUBLISHERS=(
    ('CNBC','https://www.cnbc.com/id/100003114/device/rss/rss.html'),
    ('Bloomberg','https://www.bloomberg.com/markets'),
    ('Reuters','https://www.reuters.com/business/'),
    ('NYT Business','https://rss.nytimes.com/services/xml/rss/nyt/Business.xml'),
    ('WSJ','https://www.wsj.com/news/markets'),
    ('FT','https://www.ft.com/markets'))
HOST_BY_PUBLISHER={'CNBC':'www.cnbc.com','Bloomberg':'www.bloomberg.com','Reuters':'www.reuters.com',
                  'NYT Business':'www.nytimes.com','WSJ':'www.wsj.com','FT':'www.ft.com'}


class Links(HTMLParser):
    def __init__(self):super().__init__();self.current=None;self.text=[];self.links=[]
    def handle_starttag(self,tag,attrs):
        if tag=='a':self.current=dict(attrs).get('href');self.text=[]
    def handle_data(self,data):
        if self.current:self.text.append(data)
    def handle_endtag(self,tag):
        if tag=='a' and self.current:
            self.links.append((self.current,' '.join(' '.join(self.text).split()),None));self.current=None


def candidates(raw,endpoint,publisher,cutoff):
    """Feed/page headlines are discovery metadata only, never claim evidence."""
    found=[]
    try:
        xml=ET.fromstring(raw)
        if xml.tag.rsplit('}',1)[-1] not in {'rss','feed','RDF'}:raise ET.ParseError('HTML discovery page')
        for item in xml.findall('.//item'):
            date=item.findtext('pubDate');published=None
            if date:
                try:
                    dt=parsedate_to_datetime(date)
                    if dt.utcoffset() is not None:published=dt.isoformat()
                except (ValueError,TypeError):pass
            found.append((item.findtext('link',''),item.findtext('title',''),published))
    except ET.ParseError:
        page=Links();page.feed(raw.decode('utf-8',errors='replace'));found=page.links
    result=[];seen=set()
    for link,title,published in found:
        url=urlsplit(urljoin(endpoint,link));title=' '.join(title.split())
        if (url.scheme!='https' or url.hostname!=HOST_BY_PUBLISHER[publisher]
                or url.username or url.password or url.port or len(title.split())<5):continue
        # Limit page discovery to article-shaped URLs, rather than navigation or offers.
        if not published and not re.search(r'/\d{4}/\d{2}/\d{2}/|/news/articles/|/content/[\w-]{20,}|-\d{4}-\d{2}-\d{2}(?:/|$)|/articles/',url.path):continue
        if published:
            dt=datetime.fromisoformat(published)
            if dt>cutoff or dt<cutoff-timedelta(hours=36):continue
        canonical=urlunsplit((url.scheme,url.netloc,url.path,'',''))
        if canonical in seen:continue
        seen.add(canonical)
        identifier='source-'+digest(canonical)[:20]
        result.append({'id':identifier,'title':title,'url':canonical,'publisher':publisher,
                       'origin_id':identifier,'published_at':published})
        if len(result)>=50:break
    return result


def discover(output,cutoff,*,opener=None):
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    entries=[];scan=[];files={}
    for publisher,endpoint in PUBLISHERS:
        try:
            with (opener or build_opener(_NoRedirect())).open(Request(endpoint,headers={
                    'User-Agent':'SMN-private-source-review/1.0'}),timeout=25) as response:
                raw=response.read(2*1024*1024+1)
            if len(raw)>2*1024*1024:raise ValueError('Discovery document exceeds bound')
            name=publisher.lower().replace(' ','-')+'.discovery';(output/name).write_bytes(raw)
            files[name]=writer.sha256(raw);items=candidates(raw,endpoint,publisher,cutoff)
            entries.extend(items);scan.append({'publisher':publisher,'status':'partial',
                'note':str(len(items))+' current candidate headlines discovered; headline metadata is not evidence'})
        except Exception:
            scan.append({'publisher':publisher,'status':'unavailable','note':'Public discovery access failed; no headline facts inferred'})
    # Similar titles propose groups; the evidence-bound writer must independently
    # merge underlying events and syndication. These are not approved narratives.
    groups=[]
    for item in entries:
        words=set(re.findall(r'[a-z0-9]+',item['title'].lower()))-{'the','a','an','and','of','to','in','as','for','on'}
        group=next((g for g in groups if len(words&g['words'])/max(1,len(words|g['words']))>=.75),None)
        if group is None:group={'id':'candidate-'+digest(sorted(words))[:16],'words':words,'source_ids':[]};groups.append(group)
        group['source_ids'].append(item['id'])
    result={'sources':entries,'scan':scan,'candidate_groups':[{'id':g['id'],'source_ids':g['source_ids']} for g in groups],
            'retrieved_at':writer.utc_now(),'files':files,'publish':False}
    writer.save_json(output/'discovery.json',result);return result


def register(output,registry_root,identifier):
    """Atomically expose immutable sources and honest holds to the private dashboard."""
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',identifier):raise ValueError('Safe briefing registry ID required')
    output=Path(output);registry=Path(registry_root).absolute();registry.mkdir(parents=True,exist_ok=True)
    bundle=writer.load_json(output/'qualified-sources.json');receipt=writer.load_json(output/'run.receipt.json')
    if receipt['source_bundle_sha256']!=digest(bundle) or receipt.get('publish') is not False:
        raise ValueError('Daily run receipt/source binding differs')
    destination=registry/identifier
    if destination.exists():
        if (destination/'sources.json').exists() and digest(writer.load_json(destination/'sources.json'))==digest(bundle):return str(destination)
        raise ValueError('Existing immutable briefing capture differs; use a new registry ID')
    with tempfile.TemporaryDirectory(prefix='.briefing-stage-',dir=registry.parent) as stage:
        stage=Path(stage);writer.save_json(stage/'sources.json',bundle);writer.save_json(stage/'capture-status.json',receipt)
        drafted=output/'draft/writer-job/output.json'
        if receipt['status']=='editorial_review_required' and drafted.exists():
            writer.save_json(stage/'briefing.json',writer.load_json(drafted))
        os.rename(stage,destination)
    return str(destination)


def headline_bundle(discovery,edition_date,cutoff,label):
    entries=[];cutoff_dt=datetime.fromisoformat(cutoff.replace('Z','+00:00'))
    observed=datetime.fromisoformat(discovery['retrieved_at'].replace('Z','+00:00'))
    for candidate in discovery['sources']:
        publisher=candidate['publisher'];feed=HEADLINE_FEEDS.get(publisher)
        filename=publisher.lower().replace(' ','-')+'.discovery'
        feed_hash=discovery['files'].get(filename)
        if not feed or not feed_hash or not candidate['published_at'] or observed>cutoff_dt:continue
        published=datetime.fromisoformat(candidate['published_at'].replace('Z','+00:00'))
        if published>observed:continue
        source=dict(candidate,retrieved_at=discovery['retrieved_at'],access='headline_only',
            origin_id='feed-'+publisher.lower().replace(' ','-'),cutoff_basis='observed_capture',timestamp_status='verified',
            capture={'scope':'headline_record','feed_url':feed,'feed_sha256':feed_hash})
        text=headline_text(source);source['capture'].update(text=text,text_sha256=writer.sha256(text.encode()))
        source['capture']['record_sha256']=digest(headline_record(source))
        source['capture']['observation_sha256']=digest({'url':source['url'],'retrieved_at':source['retrieved_at'],
                                                       'text_sha256':source['capture']['text_sha256']})
        entries.append(source)
    return {'edition_date':edition_date,'cutoff':cutoff,'label':label,'sources':entries,'scan':discovery['scan'],
            'mode':'headline_roundup','coverage_note':HEADLINE_NOTE}


def edition_label(edition_date,label=None):
    weekend=datetime.fromisoformat(edition_date).weekday()>=5
    if weekend and label=='Before the Open':
        raise ValueError('Before the Open requires a Monday-Friday edition date')
    return label or ('Market Wrap' if weekend else 'Before the Open')


def run(output,edition_date,cutoff,*,codex,model,effort,opener=None,label=None,mode='headline_roundup'):
    cutoff_dt=datetime.fromisoformat(cutoff.replace('Z','+00:00'))
    if cutoff_dt.utcoffset() is None or cutoff_dt.astimezone(ZoneInfo('America/New_York')).date().isoformat()!=edition_date:
        raise ValueError('Edition requires an offset-aware cutoff on its New York date')
    label=edition_label(edition_date,label)
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    discovery=discover(output/'discovery',cutoff_dt,opener=opener)
    manifest={'edition_date':edition_date,'cutoff':cutoff,'sources':discovery['sources']}
    writer.save_json(output/'manifest.json',manifest)
    if mode=='headline_roundup':bundle=headline_bundle(discovery,edition_date,cutoff,label)
    elif mode=='full_text':
        bundle=sources.capture(manifest,output/'capture',opener=opener,auto_full_text=True)
        bundle['label']=label;bundle['scan']=discovery['scan']+bundle['scan']
    else:raise ValueError('Explicit headline_roundup or full_text mode required')
    holds=[];qualified=[]
    for source in bundle['sources']:
        if source['access'] not in {'full_text','headline_only'}:continue
        observed=datetime.fromisoformat(source['retrieved_at'].replace('Z','+00:00'))
        published=source.get('published_at')
        if observed>cutoff_dt:
            holds.append(source['id']+': source capture after cutoff');continue
        if published and not cutoff_dt-timedelta(hours=36)<=datetime.fromisoformat(published.replace('Z','+00:00'))<=cutoff_dt:
            holds.append(source['id']+': publication outside edition window');continue
        qualified.append(source)
    bundle['sources']=qualified
    basis='headline' if mode=='headline_roundup' else 'full-text'
    if len(qualified)<2:holds.append('Fewer than two actual current '+basis+' source records; coverage requires editorial attention')
    missing=sorted({p for p,_ in PUBLISHERS}-{s['publisher'] for s in qualified})
    if missing:holds.append(basis+' coverage unavailable: '+', '.join(missing))
    writer.save_json(output/'qualified-sources.json',bundle)
    status='source_review_required'
    if qualified:
        try:
            checks=sources.write_draft(bundle,output/'draft',codex=codex,model=model,effort=effort)
            holds.extend(checks['issues']);status='editorial_review_required'
        except Exception:holds.append('Actual subscription draft failed; inspect immutable writer receipt')
    else:holds.append('No qualified '+basis+' evidence; no model generation attempted')
    result={'status':status,'edition_date':edition_date,'cutoff':cutoff,'label':label,'mode':mode,
        'source_bundle_sha256':digest(bundle),'qualified_count':len(qualified),'holds':holds,
        'review_status':'pending','publish':False,'created_at':writer.utc_now()}
    writer.save_json(output/'run.receipt.json',result);return result


def main():
    p=argparse.ArgumentParser();p.add_argument('--output-root',required=True);p.add_argument('--date')
    p.add_argument('--cutoff');p.add_argument('--codex',required=True);p.add_argument('--model',required=True)
    p.add_argument('--effort',required=True);p.add_argument('--label',choices=['Before the Open','Intraday','Market Wrap'])
    p.add_argument('--registry-root');p.add_argument('--registry-id')
    p.add_argument('--mode',choices=['headline_roundup','full_text'],default='headline_roundup')
    args=p.parse_args();now=datetime.now(ZoneInfo('America/New_York'));date=args.date or now.date().isoformat()
    cutoff=args.cutoff or datetime.fromisoformat(date+'T07:00:00').replace(tzinfo=ZoneInfo('America/New_York')).isoformat()
    output=Path(args.output_root)/(date+'-'+datetime.now(timezone.utc).strftime('%H%M%S%f'))
    label=edition_label(date,args.label)
    result=run(output,date,cutoff,codex=args.codex,model=args.model,effort=args.effort,label=label,mode=args.mode)
    registry=args.registry_root or (str(Path(os.environ['SMN_READER_PRIVATE_ROOT'])/'briefings') if os.environ.get('SMN_READER_PRIVATE_ROOT') else None)
    if registry:
        identifier=args.registry_id or date+'-'+label.lower().replace(' ','-')+'-'+output.name.rsplit('-',1)[-1]
        result['registry_path']=register(output,registry,identifier)
    print(__import__('json').dumps({'output':str(output),'status':result['status'],'qualified_count':result['qualified_count'],
                                  'holds':result['holds'],'registry_path':result.get('registry_path'),'publish':False}))


if __name__=='__main__':main()
