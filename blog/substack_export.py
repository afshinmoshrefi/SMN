"""Private public-copy export for free Substack posts; never signs in or dispatches."""
from html import escape
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

from daily_briefing import digest
import promotion_jobs as jobs
from public_derivative import validate_derivative
from subscription_writer import load_json,save_json, sha256
from article_content_store import canonical_path


def canonical_url(identity, public_origin=None):
    def parse(value):
        if not isinstance(value, str) or any(ord(c) <= 32 or ord(c) == 127 for c in value) or '\\' in value:
            raise ValueError('Canonical HTTPS full-study URL required')
        parsed = urlsplit(value)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or not re.fullmatch(r'[A-Za-z0-9.-]+', parsed.hostname)):
            raise ValueError('Canonical HTTPS full-study URL required')
        if parsed.port is not None and parsed.port <= 0:
            raise ValueError('Invalid canonical HTTPS port')
        return parsed
    if isinstance(identity, str) and identity.startswith('/'):
        path = canonical_path(identity)
        origin = public_origin if public_origin is not None else os.environ.get('SMN_PUBLIC_ORIGIN') or os.environ.get('SMN_SITE_BASE')
        parsed = parse(origin)
        if parsed.path not in ('', '/'):
            raise ValueError('Explicit HTTPS public origin required')
        return origin.rstrip('/') + path
    parse(identity)
    return identity


def export(root, prepared, copy, actor, *, job_id=None, public_origin=None):
    validate_derivative(copy, prepared)
    for name, expected in prepared['input_hashes'].items():
        if sha256(Path(name).read_bytes()) != expected:
            raise ValueError('Retained source changed')
    p=prepared['provenance']; url=canonical_url(p['article_id'], public_origin)
    inputs={'article_id':p['article_id'],'source_revision':p['revision'],
        'source_hash':p['article_sha256'],'payload_sha256':digest(copy),'channel':'substack_free'}
    if job_id:
        stored=load_json(jobs._path(root,job_id));current=stored['inputs']
        if stored['kind'] not in {'substack_export','social_export'} or any(current.get(k)!=inputs[k] for k in ('article_id','source_revision','source_hash')):
            raise ValueError('Export differs from current source job')
        if current.get('payload_sha256',digest(copy))!=digest(copy):raise ValueError('Export copy differs')
        job=jobs.summary(stored)
    else:job=jobs.create(root,'substack_export',inputs,actor)
    if job['status'] in {'generated','reviewed','canceled','superseded'} or jobs.is_paused(root,job['kind']):
        return job
    private=jobs._folder(root)/'artifacts'/job['id']; private.mkdir(parents=True,exist_ok=True)
    # Only validated public fields enter the export, never the protected retained article.
    social = job['kind'] == 'social_export'
    if social:
        hooks = copy['social'] or copy['preview'][:1]
        fields=[item['text'] for item in hooks] + [copy['qualification']['text']]
    else:
        fields=[copy['headline']['text'],*[item['text'] for item in copy['preview']],
                copy['full_article_value']['text'],copy['qualification']['text']]
    text='\n\n'.join(fields)+'\n\nRead the full study: '+url+'\n'
    html='<h1>'+escape(fields[0])+'</h1>'+''.join('<p>'+escape(v)+'</p>' for v in fields[1:])
    html+='<p><a href="'+escape(url,quote=True)+'">Read the full study</a></p>'
    if social:
        (private/'social-public.txt').write_text(text,encoding='utf-8')
        outputs=[('social-public.txt','text/plain')]
    else:
        (private/'substack-free.txt').write_text(text,encoding='utf-8')
        (private/'substack-free.html').write_text(html,encoding='utf-8')
        outputs=[('substack-free.txt','text/plain'),('substack-free.html','text/html')]
    save_json(private/'export.receipt.json',{'status':'private_export_pending_review','copy_sha256':digest(copy),
        'source_revision':p['revision'],'canonical_id':p['article_id'],'public_url':url,
        'channel':'social_public' if social else 'substack_free',
        'protected_body_exported':False,'dispatch':'disabled','publish':False})
    artifacts=[{'name':n,'relative_path':n,'media_type':m,'sha256':sha256((private/n).read_bytes()),
        'review_status':'pending'} for n,m in outputs+[('export.receipt.json','application/json')]]
    return jobs.update(root,job['id'],job['version'],status='generated',stage='export_review',
        generation_status='generated',review_status='pending',artifacts=artifacts,dispatch_status='disabled')


def channel_readiness(configuration):
    return {'channel':configuration.get('channel'),'configured':bool(configuration.get('account_verified')),
        'dispatch_enabled':False,'reason':'External dispatch is not implemented or authorized by this export adapter'}
