"""Role -> subscription provider/model/effort, read from smn_models.json.

Switch a step between Claude and Codex by editing one line of that file.
Jobs remember their own provider, so running/verifying dispatches by job.json.
"""
from pathlib import Path
import json

import claude_subscription_writer
import subscription_writer

DEFAULT = Path(__file__).with_name('smn_models.json')
ROLES = ('write', 'review', 'research', 'visual', 'hero_check')
MODULES = {'claude': claude_subscription_writer, 'codex': subscription_writer}
ASTRA = {role: {'provider': 'codex', 'model': 'gpt-6-astra', 'effort': 'xhigh'} for role in ROLES}
IMAGE_ROLES = {'visual', 'hero_check'}


def load(path=None):
    data = json.loads(Path(path or DEFAULT).read_text(encoding='utf-8'))
    roles = {}
    for role in ROLES:
        cfg = data.get(role)
        if not isinstance(cfg, dict) or cfg.get('provider') not in MODULES:
            raise ValueError('smn_models: role %s needs provider claude or codex' % role)
        module = MODULES[cfg['provider']]
        if cfg['provider'] == 'codex' and cfg.get('model') != 'gpt-6-astra':
            raise ValueError('smn_models: the Codex adapter supports gpt-6-astra only')
        if cfg['provider'] == 'claude' and cfg.get('model') not in claude_subscription_writer.MODELS:
            raise ValueError('smn_models: unsupported Claude model for ' + role)
        if cfg['provider'] == 'codex' and role in IMAGE_ROLES:
            raise ValueError('smn_models: image roles need the claude provider')
        if cfg.get('effort') not in claude_subscription_writer.EFFORTS:
            raise ValueError('smn_models: bad effort for ' + role)
        roles[role] = {k: cfg[k] for k in ('provider', 'model', 'effort')}
    return roles


def role_of(stage):
    return 'review' if stage.endswith('review') else 'write'


def prepare(roles, role, *args, **kwargs):
    cfg = roles[role]
    extra = {'effort': cfg['effort']}
    if cfg['provider'] == 'claude':
        extra['model'] = cfg['model']
    return MODULES[cfg['provider']].prepare_job(*args, **kwargs, **extra)


def module_for_job(job):
    manifest = json.loads((Path(job)/'job.json').read_text(encoding='utf-8-sig'))
    return claude_subscription_writer if manifest.get('provider') == 'anthropic' else subscription_writer


def run(job, clis):
    module = module_for_job(job)
    return module.run_job(job, clis['claude' if module is claude_subscription_writer else 'codex'])


def verify(job):
    return module_for_job(job).verify_job(job)
