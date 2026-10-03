"""Private public-copy export for free Substack posts; never signs in or dispatches."""
from html import escape
from pathlib import Path
from urllib.parse import urlsplit

from daily_briefing import digest
import promotion_jobs as jobs
from public_derivative import validate_derivative
from subscription_writer import load_json,save_json, sha256


def export(root, prepared, copy, actor, *, job_id=None):
    validate_derivative(copy, prepared)
    for name, expected in prepared['input_hashes'].items():
        if sha256(Path(name).read_bytes()) != expected:
            raise ValueError('Retained source changed')
    p=prepared['provenance']; url=p['article_id']; parsed=urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Canonical HTTPS full-study URL required')
    inputs={'article_id':url,'source_revision':p['revision'],
        'source_hash':p['article_sha256'],'payload_sha256':digest(copy),'channel':'substack_free'}
    if job_id:
        stored=load_json(jobs._path(root,job_id));current=stored['inputs']
        if stored['kind'] not in {'substack_export','social_export'} or any(current.get(k)!=inputs[k] for k in ('article_id','source_revision','source_hash')):
            raise ValueError('Export differs from current source job')
        if current.get('payload_sha256',digest(copy))!=digest(copy):raise ValueError('Export copy differs')
        job=jobs.summary(stored)
    else:job=jobs.create(root,'substack_export',inputs,actor)
    if job['status'] in {'generated','reviewed','canceled','superseded'} or jobs.is_paused(root,'substack_export'):
        return job
    private=jobs._folder(root)/'artifacts'/job['id']; private.mkdir(parents=True,exist_ok=True)
    # Only validated public fields enter the export, never the protected retained article.
    fields=[copy['headline']['text'],*[p['text'] for p in copy['preview']],
            copy['full_article_value']['text'],copy['qualification']['text']]
    text='\n\n'.join(fields)+'\n\nRead the full study: '+url+'\n'
    html='<h1>'+escape(fields[0])+'</h1>'+''.join('<p>'+escape(v)+'</p>' for v in fields[1:])
    html+='<p><a href="'+escape(url,quote=True)+'">Read the full study</a></p>'
    (private/'substack-free.txt').write_text(text,encoding='utf-8')
    (private/'substack-free.html').write_text(html,encoding='utf-8')
    save_json(private/'export.receipt.json',{'status':'private_export_pending_review','copy_sha256':digest(copy),
        'source_revision':p['revision'],'protected_body_exported':False,'dispatch':'disabled','publish':False})
    artifacts=[{'name':n,'relative_path':n,'media_type':m,'sha256':sha256((private/n).read_bytes()),
        'review_status':'pending'} for n,m in [('substack-free.txt','text/plain'),('substack-free.html','text/html'),
                                             ('export.receipt.json','application/json')]]
    return jobs.update(root,job['id'],job['version'],status='generated',stage='export_review',
        generation_status='generated',review_status='pending',artifacts=artifacts,dispatch_status='disabled')


def channel_readiness(configuration):
    return {'channel':configuration.get('channel'),'configured':bool(configuration.get('account_verified')),
        'dispatch_enabled':False,'reason':'External dispatch is not implemented or authorized by this export adapter'}
