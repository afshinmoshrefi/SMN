"""Local controller for the primary .180 Dev publisher (no API generation)."""
from pathlib import Path
import argparse
import json
import os
import subprocess
import subscription_dev_publish as shared

HOST = 'root@192.168.1.180'
PYTHON = '/home/flask/venv-smn-integrity-20260711T205457Z/bin/python'


def remote(command, **kwargs):
    return subprocess.check_output(['ssh','-o','BatchMode=yes',HOST,command], text=True, **kwargs)


def call(receipt, action):
    return json.loads(remote(PYTHON+' '+receipt['remote']+'/source/blog/install_smn_primary_edition.py '+action+' '+receipt['record']))


def stage(root, repo):
    extra = ('blog/install_smn_primary_edition.py','blog/subscription_primary_publish.py','blog/subscription_primary_live.cjs','blog/claude_subscription_writer.py')
    shared.SOURCE_FILES += tuple(f for f in extra if f not in shared.SOURCE_FILES)
    receipt = shared.stage(root, repo)
    receipt['target_host'] = HOST
    state = root/'daily-state.json'
    receipt['writer_source_commit'] = shared.read(state).get('source_commit') if state.exists() else receipt['source_commit']
    receipt['record'] = '/var/lib/tradewave/release-state/smn-primary-'+shared.edition_date(root)+'-'+receipt['source_commit'][:10]
    receipt['remote'] = '/var/tmp/smn-primary-'+shared.edition_date(root)+'-'+receipt['source_commit'][:10]
    shared.write(root/'primary-stage.json', receipt)
    return receipt


def activate(root, repo, node, playwright):
    receipt = shared.read(root/'primary-stage.json')
    _, head = shared.clean_main(repo)
    if head != receipt['source_commit']:
        raise ValueError('main moved before activation')
    for name,key in [('committed-source.tar','source_tar_sha256'),('publication-package.tar','package_tar_sha256')]:
        if shared.sha(root/name) != receipt[key]:
            raise ValueError('Staged archive changed')
    remote('mkdir -m 700 '+receipt['remote'])
    for name in ('committed-source.tar','publication-package.tar'):
        shared.run(['scp',str(root/name),HOST+':'+receipt['remote']+'/'+name])
    code = "import tarfile;from pathlib import Path;p=Path(%r);[tarfile.open(p/n).extractall(p,filter='data') for n in ('committed-source.tar','publication-package.tar')]" % receipt['remote']
    remote(PYTHON+' -',input=code)
    prepared = remote(PYTHON+' '+receipt['remote']+'/source/blog/install_smn_primary_edition.py prepare '+receipt['remote']+'/publication-package')
    record = json.loads(prepared)
    if record['record'] != receipt['record']:
        raise ValueError('Unexpected primary receipt path')
    _,head = shared.clean_main(repo)
    if head != receipt['source_commit']:
        raise ValueError('main moved after preparation')
    active = call(receipt,'activate')
    shared.write(root/'primary-activation.json',active)
    try:
        _,head = shared.clean_main(repo)
        if head != receipt['source_commit']:
            raise ValueError('main moved during activation')
        env = {**os.environ,'SMN_PLAYWRIGHT':str(playwright)} if playwright else None
        shared.run([str(node),str(root/'committed-source/blog/subscription_primary_live.cjs'),str(root)],env=env)
    except BaseException:
        call(receipt,'rollback')
        raise
    return active


def finish(root, repo):
    receipt = shared.read(root/'primary-stage.json')
    _,head = shared.clean_main(repo)
    if head != receipt['source_commit']:
        call(receipt,'rollback')
        raise ValueError('main moved; primary publication rolled back')
    proof = shared.read(root/'live-verification.json')
    pixels = shared.read(root/'live-landing-visual-checks.json')
    from subscription_publication import sha256_equal
    if not proof.get('passed') or not pixels.get('passed') or not pixels.get('inspected_images'):
        raise ValueError('Live browser and actual landing inspection required')
    for name,digest in pixels['inspected_images'].items():
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
