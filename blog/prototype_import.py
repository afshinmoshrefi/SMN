"""Hash-verified private prototype import; no publication or live qualification."""
import argparse
import json
import os
from pathlib import Path,PurePosixPath,PureWindowsPath
import re
import tarfile

from daily_briefing import digest,inspect
from public_derivative import validate_prepared
from subscription_writer import load_json,save_json,sha256,utc_now


def _safe_name(name):
    p=PurePosixPath(name)
    if not name or len(name)>500 or p.is_absolute() or '\\' in name or ':' in name or any(x in {'','.','..'} for x in name.split('/')):
        raise ValueError('Unsafe archive member')
    return p


def _target(root,name):
    root=Path(root).absolute();p=root.joinpath(*_safe_name(name).parts)
    for ancestor in (p,*p.parents):
        if ancestor.is_symlink():raise ValueError('Symlink import target refused')
        if ancestor==root:break
    if root.resolve() not in p.resolve().parents:raise ValueError('Import containment failed')
    return p


def _write(path,data):
    path=Path(path)
    if path.exists():
        if path.read_bytes()!=data:raise ValueError('Existing private revision differs')
        return
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as f:f.write(data)


def verify_archive(archive,expected):
    archive=Path(archive)
    if not re.fullmatch('[a-f0-9]{64}',expected) or sha256(archive.read_bytes())!=expected:
        raise ValueError('Prototype archive hash differs')
    payloads={}
    with tarfile.open(archive,'r:gz') as tar:
        members=tar.getmembers()
        if len(members)>1000 or sum(m.size for m in members)>100*1024*1024:raise ValueError('Archive exceeds private import bound')
        for member in members:
            _safe_name(member.name)
            if not member.isfile() or member.name in payloads:raise ValueError('Only unique regular archive members admitted')
            if not 0<=member.size<=4*1024*1024:raise ValueError('Archive member exceeds private bound')
            payloads[member.name]=tar.extractfile(member).read()
    manifest=json.loads(payloads['import-manifest.json'])
    if set(payloads)!=set(manifest['files'])|{'import-manifest.json'}:raise ValueError('Unlisted or missing archive files')
    if manifest.get('publish') is not False or manifest.get('media_review')!='pending':raise ValueError('Private pending-review archive required')
    for name,record in manifest['files'].items():
        _safe_name(name)
        if len(payloads[name])!=record['bytes'] or sha256(payloads[name])!=record['sha256']:raise ValueError('Archive member hash differs')
    for old,record in manifest['original_path_custody'].items():
        if record['relative_path'] not in payloads or sha256(payloads[record['relative_path']])!=record['sha256']:
            raise ValueError('Original path custody hash differs')
    return manifest,payloads


def import_bundle(archive,expected,private,reader):
    manifest,payloads=verify_archive(archive,expected)
    private,reader=Path(private).absolute(),Path(reader).absolute()
    media_files={};installed=[]
    for name,data in payloads.items():
        if not re.fullmatch(r'promotion-jobs/[a-f0-9]{24}\.json',name):continue
        job=json.loads(data)
        if job['kind'] not in {'article_video','daily_briefing'}:continue
        if (job['status']!='generated' or job['review_status']!='pending' or job['dispatch_status']!='disabled'
                or job['id']!=PurePosixPath(name).stem or digest({'kind':job['kind'],'inputs':job['inputs']})[:24]!=job['id']):
            raise ValueError('Media job identity or pending state differs')
        for artifact in job['artifacts']:
            relative=artifact['relative_path'];_safe_name(relative)
            source='promotion-jobs/artifacts/'+job['id']+'/'+relative
            if sha256(payloads[source])!=artifact['sha256'] or artifact['review_status']!='pending':raise ValueError('Media artifact differs')
            media_files['promotion/'+source]=payloads[source]
        media_files['promotion/'+name]=data;installed.append(job['id'])
    if len(installed)!=4:raise ValueError('Exactly four actual pending-review media jobs required')
    briefing=json.loads(payloads['daily/briefing.json']);review=json.loads(payloads['daily/editorial-approval.json'])
    check=inspect(briefing,review)
    if check['issues'] or check['review_status']!='approved':raise ValueError('Dated source-bound briefing approval differs')
    media_files['promotion/elevenlabs-budget.json']=payloads['elevenlabs-budget.json']
    for name,data in media_files.items():
        target=_target(reader,name)
        if target.exists() and target.read_bytes()!=data:raise ValueError('Existing private media/credit ledger differs')
    # Validate every path before writing any extracted payload.
    for name in payloads:_target(private,name)
    for name,data in payloads.items():_write(_target(private,name),data)
    remaps=[]
    for symbol in ('OMC','LEN','KDP'):
        old=load_json(private/'pilot'/symbol/'prepared.json');portable=dict(old,input_hashes={})
        names=list(old['input_hashes']);normalized={n:n.replace('\\','/') for n in names}
        article_root=next(normalized[n].rsplit('/',1)[0] for n in names if PureWindowsPath(n).name=='article.json')
        review_root=next(normalized[n].rsplit('/',1)[0] for n in names if PureWindowsPath(n).name=='receipt.json')
        bindings=[]
        for original,h in old['input_hashes'].items():
            record=manifest['original_path_custody'][original]
            if record['sha256']!=h:raise ValueError('Prepared original path hash differs')
            group,base=('article',article_root) if normalized[original].startswith(article_root+'/') else ('review',review_root)
            if not normalized[original].startswith(base+'/'):raise ValueError('Unrecognized source custody folder')
            relative=normalized[original][len(base)+1:]
            destination=_target(private,'portable/'+symbol+'/'+group+'/'+relative)
            data=payloads[record['relative_path']];_write(destination,data)
            portable['input_hashes'][str(destination.resolve())]=h
            bindings.append({'original_path':original,'private_path':str(destination),'sha256':h})
        validate_prepared(portable)
        save_json(private/'portable'/symbol/'prepared.json',portable)
        remaps.append({'symbol':symbol,'original_prepared_sha256':digest(old),'portable_prepared_sha256':digest(portable),
            'provenance':old['provenance'],'bindings':bindings,'source_approval_rewritten':False,'live_article_qualified':False})
    for name,data in media_files.items():_write(_target(reader,name),data)
    for name,source in [('briefing.json','daily/briefing.json'),('review.json','daily/editorial-approval.json')]:
        _write(_target(reader,'briefings/2026-10-02-wrap/'+name),payloads[source])
    bundle={k:briefing[k] for k in ('edition_date','cutoff','label','sources','scan')}
    _write(_target(reader,'briefings/2026-10-02-wrap/sources.json'),json.dumps(bundle,indent=2).encode())
    # Keep the historical narration job's original ID resolvable without editing its identity.
    for name,source in [('briefing.json','daily/briefing.json'),('review.json','daily/editorial-approval.json')]:
        _write(_target(reader,'briefings/2026-10-02-end-of-day/'+name),payloads[source])
    receipt={'archive_sha256':expected,'file_count':len(payloads),'installed_media_jobs':sorted(installed),
        'briefing_sha256':digest(briefing),'source_bundle_sha256':digest(bundle),'portable_custody':remaps,
        'publish':False,'media_review':'pending','live_article_qualified':False,'imported_at':utc_now()}
    save_json(private/'portable-import.receipt.json',receipt);return receipt


def main():
    p=argparse.ArgumentParser();p.add_argument('--archive',required=True);p.add_argument('--sha256',required=True)
    p.add_argument('--private',required=True);p.add_argument('--reader-root',required=True);args=p.parse_args()
    receipt=import_bundle(args.archive,args.sha256,args.private,args.reader_root)
    print(json.dumps({k:receipt[k] for k in ('archive_sha256','file_count','installed_media_jobs','briefing_sha256','publish','media_review','live_article_qualified')}))


if __name__=='__main__':main()
