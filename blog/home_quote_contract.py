"""Allow the existing quote renderer's typed display modes; bind other bytes."""
import hashlib
import re

VERSION = 3
KIND = 'market-bar-renderer-quotes-v3'
SPAN = re.compile(rb'<div class="market-bar">.*?</div>\s*</div>', re.S)
NUMBER = rb'[-+]?(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?'


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
    price=rb'(<span class="market-price"(?: style="color:#818cf8")?>)('+NUMBER+rb')(<sup style=\x27font-size:9px;color:#6366f1;margin-left:1px\x27>F</sup>)?(</span>)'
    change=rb"(<span class='market-change )(up|down|flat)('>)("+NUMBER+rb"%?)(</span>)"
    for field in re.findall(rb'<span\b[^>]*class="market-price"[^>]*>.*?</span>',span,re.S):
        if not re.fullmatch(price,field):raise ValueError('Invalid quote price field')
    for field in re.findall(rb"<span\b[^>]*class='market-change [^']*'[^>]*>.*?</span>",span,re.S):
        if not re.fullmatch(change,field):raise ValueError('Invalid quote change field')
    for direction in re.findall(rb'class="market-item ([^"]*)"',span):
        if direction not in {b'up',b'down',b'flat'}:raise ValueError('Invalid quote item direction')
    items=list(re.finditer(rb'<(a|span)\b[^>]*class="market-item (up|down|flat)"[^>]*>',span))
    prices=list(re.finditer(price,span));changes=list(re.finditer(change,span))
    if not items or len(prices)!=len(items):raise ValueError('Invalid quote item/price count')
    owned_changes=0
    for index,item in enumerate(items):
        depth=1;end=None
        for tag in re.finditer(rb'</?'+item[1]+rb'\b[^>]*>',span[item.end():]):
            depth += -1 if tag.group().startswith(b'</') else 1
            if depth==0:
                end=item.end()+tag.start();break
        if end is None or (index+1<len(items) and end>items[index+1].start()):raise ValueError('Invalid quote item boundary')
        group=span[item.end():end]
        fields=list(re.finditer(price,group));moves=list(re.finditer(change,group))
        if len(fields)!=1 or len(moves)>1:raise ValueError('Invalid quote field count')
        field=fields[0]
        if (b'style=' in field[1])!=bool(field[3]):raise ValueError('Invalid quote futures marker')
        if field[3] and not re.search(rb'href="/markets/(?:sp500|dow|nasdaq)\.html"',item.group()):raise ValueError('Invalid quote futures instrument')
        if not moves and item[2]!=b'flat':raise ValueError('Invalid quote missing change')
        if moves and (moves[0][2]!=item[2] or moves[0].start()<field.end()):raise ValueError('Invalid quote change direction or order')
        owned_changes+=len(moves)
    if owned_changes!=len(changes):raise ValueError('Invalid quote unowned change field')
    # The unchanged quote writer can switch cash index values to labelled
    # futures values and omit an unavailable flat change. Retain all symbols,
    # links and surrounding bytes; no quote number is calculated here.
    stable = re.sub(price, b'<span class="market-price">PRICE</span>', span)
    stable = re.sub(change, b'', stable)
    stable = re.sub(rb'class="market-item (?:up|down|flat)"', b'class="market-item DIRECTION"', stable)
    return {'version': VERSION, 'kind': KIND,
            'outside_sha256': _sha(prefix + suffix),
            'markup_sha256': _sha(stable),
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
