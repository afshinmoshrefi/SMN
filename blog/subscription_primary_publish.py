"""Local controller for the primary .180 Dev publisher (no API generation)."""
from pathlib import Path
import argparse
import json
import os
import subprocess
import re
import uuid
import subscription_dev_publish as shared

HOST = 'root@192.168.1.180'
PYTHON = '/home/flask/venv-smn-integrity-20260711T205457Z/bin/python'
PUBLICATION_PYTHON = 'SMN_MEMBERSHIP_ENV_FILE=/etc/SMN/membership.env ' + PYTHON


def remote(command, **kwargs):
    return subprocess.check_output(['ssh','-o','BatchMode=yes',HOST,command], text=True, **kwargs)


def call(receipt, action):
    return json.loads(remote(PUBLICATION_PYTHON+' '+receipt['remote']+'/source/blog/install_smn_primary_edition.py '+action+' '+receipt['record']))


def candidate_source(repo, expected_base):
    """An explicit pushed Dev candidate may be qualified before main advances."""
    if not re.fullmatch('[0-9a-f]{40}',str(expected_base)):
        raise ValueError('Exact main base commit required for candidate qualification')
    repo=Path(repo).resolve()
    shared.run(['git','-C',str(repo),'fetch','origin','main'])
    if subprocess.check_output(['git','-C',str(repo),'status','--porcelain'],text=True).strip():
        raise ValueError('Candidate source must be clean')
    head=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
    base=subprocess.check_output(['git','-C',str(repo),'rev-parse','origin/main'],text=True).strip()
    branch=subprocess.check_output(['git','-C',str(repo),'branch','--show-current'],text=True).strip()
    merge_base=subprocess.check_output(['git','-C',str(repo),'merge-base','HEAD','origin/main'],text=True).strip()
    if base!=expected_base or merge_base!=base or not re.fullmatch(r'codex/[a-zA-Z0-9._/-]+',branch):
        raise ValueError('Candidate branch or exact main base changed')
    remote_head=subprocess.check_output(['git','-C',str(repo),'ls-remote','--heads','origin',branch],text=True).strip()
    if not remote_head or remote_head.split()[0]!=head or remote_head.split()[1]!='refs/heads/'+branch:
        raise ValueError('Candidate commit is not pushed to its exact remote branch')
    return repo,head,branch


def source_guard(repo, receipt):
    if receipt.get('candidate_base_main'):
        _,head,branch=candidate_source(repo,receipt['candidate_base_main'])
        if head!=receipt['source_commit'] or branch!=receipt['candidate_branch']:
            raise ValueError('Qualified candidate source moved')
    else:
        _,head=shared.clean_main(repo)
        if head!=receipt['source_commit']:
            raise ValueError('main moved during publication')


def stage(root, repo):
    extra = ('blog/install_smn_primary_edition.py','blog/subscription_primary_publish.py','blog/subscription_primary_live.cjs','blog/cloudflare_email_bytes.cjs','blog/claude_subscription_writer.py',
             'blog/membership_pipeline.py','blog/membership_publication.py','blog/article_content_store.py',
             'blog/article_index.py','blog/pin_store.py','blog/reader_app.py','blog/reader_auth.py',
             'blog/public_derivative.py','blog/promotion_jobs.py','blog/daily_briefing.py',
             'blog/presentation_fallback.py','blog/publication_continuity.py')
    shared.SOURCE_FILES += tuple(f for f in extra if f not in shared.SOURCE_FILES)
    receipt = shared.stage(root, repo)
    receipt['target_host'] = HOST
    state = root/'daily-state.json'
    receipt['writer_source_commit'] = shared.read(state).get('source_commit') if state.exists() else receipt['source_commit']
    receipt['record'] = '/var/lib/tradewave/release-state/smn-primary-'+shared.edition_date(root)+'-'+receipt['source_commit'][:10]
    receipt['remote'] = '/var/tmp/smn-primary-'+shared.edition_date(root)+'-'+receipt['source_commit'][:10]
    shared.write(root/'primary-stage.json', receipt)
    return receipt


def stage_continuity(root, repo, date, stages, revision, *, candidate_base=None):
    """Stage an immutable Dev coverage revision without entering the legacy full-edition path."""
    from subscription_publication import package
    root=Path(root).resolve()
    extra=('blog/install_smn_primary_edition.py','blog/subscription_primary_publish.py','blog/subscription_primary_live.cjs','blog/cloudflare_email_bytes.cjs',
           'blog/membership_pipeline.py','blog/membership_publication.py','blog/article_content_store.py',
           'blog/article_index.py','blog/pin_store.py','blog/reader_app.py','blog/reader_auth.py',
           'blog/public_derivative.py','blog/promotion_jobs.py','blog/daily_briefing.py',
           'blog/presentation_fallback.py','blog/publication_continuity.py')
    shared.SOURCE_FILES += tuple(f for f in extra if f not in shared.SOURCE_FILES)
    if candidate_base:
        repo,commit,branch=candidate_source(repo,candidate_base)
    else:
        repo,commit=shared.clean_main(repo);branch=None
    manifest=package(root,date,commit,stages,continuity=revision)
    source=shared.source_tree(root,repo,commit)
    for name,folder in (('committed-source.tar',source),('publication-package.tar',root/'publication-package')):
        shared.archive(folder,root/name,'source' if name=='committed-source.tar' else 'publication-package')
    ident=manifest['transaction_id'][:16]
    receipt={'status':'staged','source_commit':commit,'writer_source_commit':shared.read(root/'daily-state.json').get('source_commit') if (root/'daily-state.json').exists() else commit,
             'record':'/var/lib/tradewave/release-state/smn-primary-'+date+'-'+ident,
             'remote':'/var/tmp/smn-primary-'+date+'-'+ident,
             'target_host':HOST,'review_stages':stages,'manifest':manifest,
             'publication_policy':'continuity-v1','revision_id':manifest['revision_id'],'transaction_id':manifest['transaction_id'],
             'candidate_base_main':candidate_base,'candidate_branch':branch,
             'source_tar_sha256':shared.sha(root/'committed-source.tar'),
             'package_tar_sha256':shared.sha(root/'publication-package.tar')}
    shared.write(root/'primary-stage.json',receipt)
    return receipt


def activate(root, repo, node, playwright):
    from subscription_publication import validate_staged_reviews
    validate_staged_reviews(root)
    receipt = shared.read(root/'primary-stage.json')
    source_guard(repo,receipt)
    for name,key in [('committed-source.tar','source_tar_sha256'),('publication-package.tar','package_tar_sha256')]:
        if shared.sha(root/name) != receipt[key]:
            raise ValueError('Staged archive changed')
    remote('mkdir -p -m 700 '+receipt['remote'])
    for name in ('committed-source.tar','publication-package.tar'):
        path=receipt['remote']+'/'+name
        code="from pathlib import Path;import hashlib;p=Path(%r);print(hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else 'missing')" % path
        existing=remote(PYTHON+' -',input=code).strip()
        digest=shared.sha(root/name)
        if existing not in ('missing',digest):
            raise ValueError('Interrupted remote upload differs from immutable stage')
        if existing=='missing':
            temporary=path+'.upload-'+uuid.uuid4().hex
            shared.run(['scp',str(root/name),HOST+':'+temporary])
            finalize="""from pathlib import Path
import hashlib,os
source=Path(%r);final=Path(%r);expected=%r
digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
if not source.is_file() or digest(source)!=expected:raise ValueError('Interrupted archive upload')
if final.exists():
    if digest(final)!=expected:raise ValueError('Remote archive differs from immutable stage')
    source.unlink()
else:os.replace(source,final)
print(digest(final))""" % (temporary,path,digest)
            if remote(PYTHON+' -',input=finalize).strip()!=digest:
                raise ValueError('Remote archive promotion failed')
    code = "import tarfile;from pathlib import Path;p=Path(%r);[tarfile.open(p/n).extractall(p,filter='data') for n in ('committed-source.tar','publication-package.tar')]" % receipt['remote']
    remote(PYTHON+' -',input=code)
    prepared = remote(PUBLICATION_PYTHON+' '+receipt['remote']+'/source/blog/install_smn_primary_edition.py prepare '+receipt['remote']+'/publication-package')
    record = json.loads(prepared)
    if record['record'] != receipt['record']:
        raise ValueError('Unexpected primary receipt path')
    source_guard(repo,receipt)
    shared.write(root/'primary-activation-attempt.json',
                 {'record':receipt['record'],'source_commit':receipt['source_commit'],
                  'phase':'activation_call_pending'})
    active = call(receipt,'activate')
    shared.write(root/'primary-activation.json',active)
    try:
        source_guard(repo,receipt)
        env = {**os.environ,'SMN_PLAYWRIGHT':str(playwright)} if playwright else None
        shared.run([str(node),str(root/'committed-source/blog/subscription_primary_live.cjs'),str(root)],env=env)
    except BaseException:
        call(receipt,'rollback')
        raise
    return active


def finish(root, repo):
    receipt = shared.read(root/'primary-stage.json')
    try:
        source_guard(repo,receipt)
    except ValueError:
        call(receipt,'rollback')
        raise ValueError('main moved; primary publication rolled back')
    proof = shared.read(root/'live-verification.json')
    from subscription_publication import sha256_equal
    if receipt.get('publication_policy') == 'continuity-v1':
        if (not proof.get('passed') or proof.get('deterministic_landing') is not True or
                proof.get('revision_id') != receipt.get('revision_id') or
                set(proof.get('landing_screenshots') or {}) != {'live-edition-desktop.png','live-edition-mobile.png'}):
            raise ValueError('Bound deterministic landing/browser proof required')
        screenshots=proof['landing_screenshots']
    else:
        pixels = shared.read(root/'live-landing-visual-checks.json')
        if not proof.get('passed') or not pixels.get('passed') or not pixels.get('inspected_images'):
            raise ValueError('Live browser and actual landing inspection required')
        screenshots=pixels['inspected_images']
    for name,digest in screenshots.items():
        if not sha256_equal(shared.sha(root/name),digest):
            raise ValueError('Landing screenshot changed')
    shared.run(['scp',str(root/'live-verification.json'),HOST+':'+receipt['record']+'/live-verification.json'])
    try:
        result = call(receipt,'finish')
    except BaseException:
        call(receipt,'rollback')
        raise
    result['writer_source_commit'] = receipt['writer_source_commit']
    shared.write(root/'dev-publication-receipt.json',result)
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('action',choices=['stage','activate','finish','rollback'])
    p.add_argument('--root',required=True,type=Path)
    p.add_argument('--repo',required=True,type=Path)
    p.add_argument('--node')
    p.add_argument('--playwright')
    a = p.parse_args()
    if a.action == 'activate':
        if not a.node: p.error('--node required')
        result = activate(a.root,a.repo,a.node,a.playwright)
    elif a.action == 'rollback':
        result = call(shared.read(a.root/'primary-stage.json'),'rollback')
    else:
        result = globals()[a.action](a.root,a.repo)
    print(json.dumps(result))
