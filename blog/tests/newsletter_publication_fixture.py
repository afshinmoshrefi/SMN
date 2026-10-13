"""Synthetic native publication custody for deterministic sender race regressions."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,os

COMMIT='a'*40;TRANSACTION='b'*64
def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,sort_keys=True)+'\n')
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def publication(root,web,state,day,posts,published=None):
    root=Path(root);web=Path(web);state=Path(state)
    published=published or [row['symbol'] for row in posts]
    selected=[row['symbol'] for row in posts]
    rows=[];files={};primary=root/day/'chatgpt/primary';primary.mkdir(parents=True,exist_ok=True)
    for item in posts:
        item.update(source_commit=COMMIT,edition_id='subscription-'+day,publish_status='true')
        if item['symbol'] not in published:continue
        row=dict(item);rows.append(row)
        rel='editions/'+day+'/'+row['symbol']+'/article.html'
        file=web/rel;file.parent.mkdir(parents=True,exist_ok=True);file.write_text('Synthetic verified article '+row['symbol'])
        files[rel]=sha(file)
        write(primary/(row['symbol']+'.receipt.json'),{'fetched_utc':day+'T12:00:00+00:00'})
    selection=root/day/'inputs/input-selection.json';write(selection,{'date':day,'symbols':selected})
    stamp=datetime.fromisoformat(day+'T12:00:00+00:00').timestamp();os.utime(selection,(stamp,stamp))
    write(root/day/'schedule-settings.json',{'date':day,'daily_generation':{'start_time':'05:30','target_time':'07:00','timezone':'America/New_York'},'weekday_newsletter':{'time':'07:00','timezone':'America/New_York'}})
    transaction=root/day/'chatgpt/publication-revisions'/TRANSACTION/'attempt-1'
    package=transaction/'publication-package';entries=package/'entries.json';write(entries,rows)
    manifest=package/'manifest.json';write(manifest,{'source_commit':COMMIT,'transaction_id':TRANSACTION,'files':{'entries.json':sha(entries)}})
    ident='smn-production-'+day+'-'+TRANSACTION[:16]
    write(state/ident/'transaction-intent.json',{'id':ident,'source_commit':COMMIT,'manifest_sha256':sha(manifest)})
    pending=[s for s in selected if s not in published]
    receipt={'id':ident,'status':'live_verified','source_commit':COMMIT,'transaction_id':TRANSACTION,'transaction_root':str(transaction),
             'production_written':True,'edition_date':day,'publication_policy':'continuity-v1',
             'complete':not pending,'coverage_status':'partial' if pending else 'complete',
             'expected_symbols':selected,'published_symbols':published,'pending_symbols':pending,
             'urls':[row['url'] for row in rows],'files':files}
    write(root/day/'chatgpt/production-publication-receipt.json',receipt)
    write(web/'posts.json',posts)
    return receipt
