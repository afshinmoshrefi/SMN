"""Immutable public-source capture and subscription-CLI daily draft jobs."""
import argparse
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener

from daily_briefing import digest, inspect, package
from elevenlabs_client import _NoRedirect,Held
import subscription_writer as writer

HOSTS={'www.bls.gov','www.newswire.ca','investors.nike.com','www.lse.co.uk',
       'www.cnbc.com','www.bloomberg.com','www.reuters.com','www.nytimes.com','www.wsj.com','www.ft.com'}


class Document(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts=[]; self.skip=0; self.dates={};self.json_script=False;self.script_parts=[];self.free_bodies=[]
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag in {'script','style','noscript'}: self.skip+=1
        if tag=='script' and attrs.get('type')=='application/ld+json':
            self.json_script=True;self.script_parts=[]
        if tag=='meta':
            key=attrs.get('property',attrs.get('name',''))
            if key in {'article:published_time','article:modified_time','date','pubdate'}:
                self.dates[key]=attrs.get('content','')
    def handle_endtag(self,tag):
        if tag=='script' and self.json_script:
            try:
                self._dates(json.loads(''.join(self.script_parts)))
            except (ValueError,TypeError):pass
            self.json_script=False
        if tag in {'script','style','noscript'} and self.skip: self.skip-=1
    def handle_data(self,data):
        if self.json_script:self.script_parts.append(data)
        if not self.skip and data.strip(): self.parts.append(data.strip())
    def _dates(self,value):
        if isinstance(value,list):
            for item in value:self._dates(item)
        elif isinstance(value,dict):
            kinds=value.get('@type',[])
            if isinstance(kinds,str):kinds=[kinds]
            if isinstance(kinds,list) and any(t in ('NewsArticle','Article','ReportageNewsArticle') for t in kinds):
                for field,key in [('datePublished','article:published_time'),('dateModified','article:modified_time')]:
                    if isinstance(value.get(field),str):self.dates[key]=value[field]
                body=value.get('articleBody')
                if value.get('isAccessibleForFree') is True and isinstance(body,str) and len(body.split())>=300:
                    entity=value.get('mainEntityOfPage',{})
                    entity_url=entity.get('@id') if isinstance(entity,dict) else entity
                    self.free_bodies.append({'text':body,'url':value.get('url') or entity_url})
            if '@graph' in value:self._dates(value['@graph'])


def capture(manifest, output, *, opener=None, auto_full_text=False):
    """Explicit manifest is server/editor-owned. Never bypasses restricted access.

    access_verified is an editor assertion that the actual document was read in full;
    absent verification, captured page remains metadata and cannot support claims.
    """
    output=Path(output); output.mkdir(parents=True,exist_ok=False)
    sources=[]; scan=[];ids=[s['id'] for s in manifest['sources']]
    if len(ids)!=len(set(ids)):raise ValueError('Source IDs must be unique')
    for entry in manifest['sources']:
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,79}',entry['id']):
            raise ValueError('Safe unique source ID required')
        url=entry['url']; parsed=urlsplit(url)
        if parsed.scheme!='https' or parsed.hostname not in HOSTS or parsed.username or parsed.password or parsed.port:
            raise ValueError('Source URL must use an approved public HTTPS publisher')
        now=writer.utc_now()
        try:
            client=opener or build_opener(_NoRedirect())
            with client.open(Request(url,headers={'User-Agent':'SMN-private-source-review/1.0'}),timeout=30) as response:
                raw=response.read(2*1024*1024+1)
                if len(raw)>2*1024*1024: raise ValueError('Source document exceeds capture bound')
            now=writer.utc_now(); parser=Document(); parser.feed(raw.decode('utf-8',errors='replace'))
            text='\n'.join(parser.parts)
            blocked=any(p in text.lower() for p in ('subscribe to continue reading','sign in to continue reading','access denied'))
            access=entry.get('access','metadata') if entry.get('access_verified') is True and len(text)>600 and not blocked else 'metadata'
            # Automatic qualification needs an actual substantial publisher articleBody
            # explicitly marked freely accessible, not feed descriptions or page snippets.
            matching_bodies=[body['text'] for body in parser.free_bodies if isinstance(body['url'],str)
                and urlsplit(body['url'])._replace(query='',fragment='')==parsed._replace(query='',fragment='')]
            if auto_full_text and matching_bodies and not blocked:
                text=max(matching_bodies,key=len);access='full_text'
            published=entry.get('published_at') or parser.dates.get('article:published_time') or None
            # Offsetless metadata stays raw; it is never reinterpreted as UTC.
            raw_date=published or entry.get('published_at_raw','Publication timestamp unavailable')
            try:
                dt=datetime.fromisoformat(published.replace('Z','+00:00')) if published else None
                if not dt or dt.utcoffset() is None: published=None
            except ValueError: published=None
            captured={'text':text or 'No readable document text','text_sha256':writer.sha256((text or 'No readable document text').encode()),
                      'scope':'complete_document'}
            captured['observation_sha256']=digest({'url':url,'retrieved_at':now,'text_sha256':captured['text_sha256']})
            source={k:entry[k] for k in ('id','title','publisher','origin_id')}
            if auto_full_text and access=='full_text':
                # Byte-identical publisher article bodies share an origin even
                # when carried at different syndication URLs. Event-level merging
                # remains the evidence-bound editorial writer's responsibility.
                source['origin_id']='body-'+captured['text_sha256'][:24]
            source.update(url=url,published_at=published,retrieved_at=now,access=access,capture=captured,
                published_at_raw=raw_date,timestamp_status='verified' if published else 'uncertain',cutoff_basis='observed_capture')
            modified=parser.dates.get('article:modified_time')
            if modified:
                try:
                    if datetime.fromisoformat(modified.replace('Z','+00:00')).utcoffset() is not None:
                        source['updated_at']=modified
                except ValueError:pass
            (output/(entry['id']+'.html')).write_bytes(raw)
            sources.append(source)
            scan.append({'publisher':entry['publisher'],'status':'accessed' if access in {'full_text','primary_release'} else 'partial',
                'note':'Immutable document captured; '+('publisher full articleBody explicitly free' if auto_full_text and access=='full_text' else
                    'full access verified by editor' if access!='metadata' else 'full-text qualification unavailable')})
        except (HTTPError,URLError,TimeoutError,OSError,ValueError,Held):
            scan.append({'publisher':entry['publisher'],'status':'unavailable','note':'Public document access failed; no facts inferred from snippets'})
    result={'edition_date':manifest['edition_date'],'cutoff':manifest['cutoff'],'sources':sources,'scan':scan,'captured_at':writer.utc_now()}
    writer.save_json(output/'sources.json',result)
    writer.save_json(output/'capture.receipt.json',{'sources_sha256':digest(result),'publish':False,
        'files':{p.name:writer.sha256(p.read_bytes()) for p in output.iterdir() if p.is_file()}})
    return result


def write_draft(source_bundle, output, *, codex, model, effort):
    """Existing subscription CLI; source capture and review remain independent gates."""
    qualified=[s for s in source_bundle['sources'] if s['access'] in {'full_text','primary_release'}]
    if not qualified: raise ValueError('Actual full-text or primary evidence required before drafting')
    output=Path(output); output.mkdir(parents=True,exist_ok=False)
    schema=writer.load_json(Path(__file__).parent/'schemas'/'daily_briefing.schema.json')
    draft_input=dict(source_bundle,sources=qualified)
    label=source_bundle.get('label','Market Wrap')
    if label not in {'Before the Open','Intraday','Market Wrap'}:raise ValueError('Explicit supported edition label required')
    prompt=('Write an original source-bound Seasonal Market News dated '+label+' edition. '
        'Use only actual qualified captures below. Do not invent facts from headlines/snippets or claim restricted publications were read. '
        'Cover distinct true major narratives, merge syndication and underlying event duplicates, retain source origin IDs and exact metadata. '
        'No forced ticker, TradeWave seasonal angle or chart. Separate reported facts, interpretation and synthesis; preserve announced versus completed status. '
        'Every factual claim needs exact short support quotes and locators in actual capture. Total verbatim quotes per source <=25 words. '
        'Narrative and spoken script must be original, concise, qualified and useful. Keep publication-time uncertainty explicit; observed_capture cutoff basis '
        'means exact version was captured by cutoff, not that its publication timezone is known. Preserve every source record unchanged. '
        'Return required schema JSON, with version1, timezone America/New_York, label '+label+', explicit edition date and cutoff. '
        'Use neutral title/claim_text storyboard without requiring generation. No approvals, provider IDs, publish promises or generation claims.\n'+json.dumps(draft_input))
    now=datetime.now(timezone.utc)
    writer.prepare_job(output,'writer-job',prompt,schema,as_of=now.isoformat(),
        valid_until=(now+timedelta(hours=20)).isoformat(),evidence_sha256=digest(source_bundle),
        stage='daily-briefing-write',model=model,effort=effort)
    receipt=writer.run_job(output/'writer-job',codex)
    if receipt.get('status')!='output_ready_for_smn_validation': raise ValueError('Subscription writer failed; inspect immutable private receipt')
    briefing=writer.load_json(output/'writer-job'/'output.json')
    if briefing['edition_date']!=source_bundle['edition_date'] or briefing['cutoff']!=source_bundle['cutoff']:
        raise ValueError('Draft changed edition/cutoff')
    if briefing['label']!=label:raise ValueError('Draft changed edition label')
    if briefing['sources']!=qualified:
        raise ValueError('Draft changed captured source records')
    result=inspect(briefing)
    writer.save_json(output/'checks.json',result)
    package(briefing,output/'review',allow_held=True)
    return result


def main():
    parser=argparse.ArgumentParser(); commands=parser.add_subparsers(dest='action',required=True)
    collect=commands.add_parser('capture'); collect.add_argument('--manifest',required=True); collect.add_argument('--output',required=True)
    write=commands.add_parser('write'); write.add_argument('--sources',required=True); write.add_argument('--output',required=True)
    write.add_argument('--codex',required=True); write.add_argument('--model',required=True); write.add_argument('--effort',required=True)
    args=parser.parse_args()
    if args.action=='capture': result=capture(writer.load_json(args.manifest),args.output)
    else: result=write_draft(writer.load_json(args.sources),args.output,codex=args.codex,model=args.model,effort=args.effort)
    print(json.dumps({'status':result.get('status','captured'),'publish':False}))


if __name__=='__main__': main()
