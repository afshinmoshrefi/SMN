"""Allow only numeric market-bar refreshes; bind every other homepage byte."""
import hashlib
import re

VERSION = 1
KIND = 'market-bar-numeric-quotes-v1'
SPAN = re.compile(rb'<div class="market-bar">.*?</div>\s*</div>', re.S)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def split(raw):
    matches = list(SPAN.finditer(raw))
    if len(matches) != 1:
        raise ValueError('Expected one complete market quote span')
    m = matches[0]
    return raw[:m.start()], m.group(), raw[m.end():]


def contract(raw):
    prefix, span, suffix = split(raw)
    stable = re.sub(rb'<span class="market-price"(?: style="color:#818cf8")?>[-+0-9,.]*(?:<sup style=\x27font-size:9px;color:#6366f1;margin-left:1px\x27>F</sup>)?</span>', b'', span)
    stable = re.sub(rb"<span class='market-change (?:up|down|flat)'>[-+0-9,.%]*</span>", b'', stable)
    stable = re.sub(rb'class="market-item (?:up|down|flat)"', b'class="market-item DIRECTION"', stable)
    return {'version': VERSION, 'kind': KIND,
            'outside_sha256': _sha(prefix + suffix),
            'markup_sha256': _sha(re.sub(rb'\s+', b'', stable)),
            'observed_sha256': _sha(raw), 'quote_sha256': _sha(span)}


def optional_contract(raw):
    if b'class="market-bar"' not in raw:
        return None
    return contract(raw)


def matches(expected, observed):
    keys = ('version', 'kind', 'outside_sha256', 'markup_sha256')
    return bool(expected and observed and expected.get('version') == VERSION and
                expected.get('kind') == KIND and
                all(expected.get(k) == observed.get(k) for k in keys))


def restore_with_current_quotes(backup, current, expected):
    """Revert publication content while retaining a validated current quote span."""
    observed = contract(current)
    if not matches(expected, observed):
        raise ValueError('Homepage content or quote markup changed')
    prefix, _, suffix = split(backup)
    _, quotes, _ = split(current)
    if contract(backup)['markup_sha256'] != observed['markup_sha256']:
        raise ValueError('Backup quote markup differs')
    return prefix + quotes + suffix
