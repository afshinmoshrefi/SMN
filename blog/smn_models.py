"""Named subscription model profiles and immutable per-job dispatch."""
from pathlib import Path
import json

import claude_subscription_writer
import subscription_writer

DEFAULT = Path(__file__).with_name('smn_models.json')
ROLES = ('write', 'review', 'research', 'visual', 'hero_check')
MODULES = {'claude': claude_subscription_writer, 'codex': subscription_writer}
ASTRA = {role: {'provider': 'codex', 'model': 'gpt-6-astra', 'effort': 'xhigh'} for role in ROLES}
IMAGE_ROLES = {'visual', 'hero_check'}
PROFILES = {
    'claude': DEFAULT,
    'chatgpt': Path(__file__).with_name('smn_models_chatgpt.json'),
}


def load(path=None, profile='claude'):
    if path is not None and profile != 'claude':
        raise ValueError('Use a named profile or a custom --models file, not both')
    if profile not in PROFILES:
        raise ValueError('Unknown SMN model profile: ' + str(profile))
    data = json.loads(Path(path or PROFILES[profile]).read_text(encoding='utf-8'))
    roles = {}
    for role in ROLES:
        cfg = data.get(role)
        if not isinstance(cfg, dict) or cfg.get('provider') not in MODULES:
            raise ValueError('smn_models: role %s needs provider claude or codex' % role)
        module = MODULES[cfg['provider']]
        if cfg['provider'] == 'codex' and cfg.get('model') not in subscription_writer.SUPPORTED_MODELS:
            raise ValueError('smn_models: unsupported Codex model for ' + role)
        if cfg['provider'] == 'claude' and cfg.get('model') not in claude_subscription_writer.MODELS:
            raise ValueError('smn_models: unsupported Claude model for ' + role)
        if cfg.get('effort') not in claude_subscription_writer.EFFORTS:
            raise ValueError('smn_models: bad effort for ' + role)
        roles[role] = {k: cfg[k] for k in ('provider', 'model', 'effort')}
    return roles


def role_of(stage):
    return 'review' if stage.endswith('review') else 'write'


def prepare(roles, role, *args, **kwargs):
    cfg = roles[role]
    extra = {'effort': cfg['effort'], 'model': cfg['model']}
    return MODULES[cfg['provider']].prepare_job(*args, **kwargs, **extra)


def module_for_job(job):
    manifest = json.loads((Path(job)/'job.json').read_text(encoding='utf-8-sig'))
    return claude_subscription_writer if manifest.get('provider') == 'anthropic' else subscription_writer


def run(job, clis):
    module = module_for_job(job)
    return module.run_job(job, clis['claude' if module is claude_subscription_writer else 'codex'])


def verify(job):
    return module_for_job(job).verify_job(job)
