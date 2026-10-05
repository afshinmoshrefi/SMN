"""Deterministic presentation derivative; never rewrites source article approvals."""
from pathlib import Path
import hashlib
import json

from html import escape
from html.parser import HTMLParser

VERSION = 1
CSS = '''*{box-sizing:border-box}html{color:#183140;background:#fff;font:18px/1.65 system-ui,sans-serif}
body{margin:0 auto;max-width:1050px;padding:20px}article,header,footer,section,aside,nav,figure{display:block;position:static;float:none;max-width:100%;height:auto;margin:20px 0}
h1{font-size:clamp(28px,5vw,44px);line-height:1.2}h2{font-size:25px;line-height:1.3}p,li,td,th,a{overflow-wrap:anywhere}
img,svg,picture{max-width:100%;height:auto}img{display:block}figure{margin:24px 0}figcaption,small{font-size:14px}
table{display:block;max-width:100%;overflow-x:auto;border-collapse:collapse}td,th{padding:8px;text-align:left;border-bottom:1px solid #ddd}
a{color:#065ea8}.stats,.stat-grid,.metrics,.comparison-grid{display:block}.brand{font-weight:bold}.source-list{font-size:15px}
@media(max-width:500px){body{padding:16px}h2{font-size:22px}}
'''


def sha(data):
    return hashlib.sha256(data).hexdigest()


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _chart_assets(result):
    for chart in _read(result/'chart-manifest.json').values():
        for name, expected in chart.get('file_sha256', {}).items():
            if Path(name).name != name:
                raise ValueError('Unsafe chart asset path')
            path = result/'assets'/name
            if path.is_symlink() or sha(path.read_bytes()) != expected:
                raise ValueError('Reviewed chart asset changed')


class _ReadableHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.parts = []
        self.hero_depth = 0
        self.hero_images = 0
        self.in_style = False
        self.articles = 0
        self.head = self.body = False

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'iframe'}:
            raise ValueError('Unexpected active content in article')
        values = dict(attrs)
        if self.hero_depth:
            if tag not in {'img', 'figcaption'}:
                raise ValueError('Hero contains unexpected content')
            if tag == 'img':
                self.hero_images += 1
            else:
                self.hero_depth += 1
            return
        if tag == 'figure' and 'hero' in values.get('class', '').split():
            self.hero_depth = 1
            self.hero_images = 0
            return
        if tag == 'style':
            self.in_style = True
            return
        if tag == 'article':
            self.articles += 1
        self.head |= tag == 'head'
        self.body |= tag == 'body'
        attributes = ''.join(' '+name + ('' if value is None else '="'+escape(value, quote=True)+'"')
                             for name, value in attrs if name.lower() != 'style')
        self.parts.append('<'+tag+attributes+'>')

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in {'img', 'meta', 'link', 'br', 'hr', 'input', 'source', 'wbr'}:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.hero_depth:
            self.hero_depth -= 1
            if self.hero_depth == 0 and (tag != 'figure' or self.hero_images != 1):
                raise ValueError('Hero contains unexpected content')
            return
        if tag == 'style':
            self.in_style = False
            return
        if tag == 'head':
            self.parts.append('<style>'+CSS+'</style><meta name="smn-presentation" content="deterministic-readable-v1">')
        self.parts.append('</'+tag+'>')

    def handle_data(self, data):
        if not self.hero_depth and not self.in_style:
            self.parts.append(data)

    def handle_entityref(self, name):
        self.handle_data('&'+name+';')

    def handle_charref(self, name):
        self.handle_data('&#'+name+';')

    def handle_decl(self, decl):
        self.parts.append('<!'+decl+'>')

    def handle_comment(self, data):
        if not self.hero_depth and not self.in_style:
            self.parts.append('<!--'+data+'-->')


def derive(original):
    """Change styling and omit only the renderer's decorative hero figure."""
    parser = _ReadableHTML()
    parser.feed(original)
    parser.close()
    if not parser.head or not parser.body or parser.articles != 1 or parser.hero_depth or parser.in_style:
        raise ValueError('Unrecognized article presentation')
    return ''.join(parser.parts)


def prepare_fallback(result):
    """Return HTML and a content-bound derivative proof; browser checks still required."""
    from subscription_publication import reviewed
    result = Path(result)
    root = result.parent.parent
    binding_path = result/'review-binding.json'
    if not binding_path.exists():
        binding_path = result/'review-binding.held.json'
    binding = _read(binding_path)
    state_path = root/'smn-daily-state.json'
    if not state_path.exists():
        state_path = root/'daily-state.json'
    edition = _read(state_path)['date']
    review = root/'jobs'/(result.name+'-'+edition.replace('-', '')+'-'+binding['review_stage'])/'output.json'
    reviewed(result, review, require_visual=False)
    content = binding['editorial_audit']
    _chart_assets(result)
    original = (result/'article.html').read_bytes()
    rendered = derive(original.decode('utf-8'))
    proof = {'version':VERSION, 'kind':'deterministic_readable',
             'content_proof':content, 'original_html_sha256':sha(original),
             'article_html_sha256':sha(rendered.encode()),
             'transform_sha256':sha(Path(__file__).read_bytes()),
             'omitted_decorative_hero':True, 'browser_verification_required':True}
    return rendered, proof


def verify_fallback(result, rendered, proof):
    expected_html, expected = prepare_fallback(result)
    if rendered != expected_html or proof != expected:
        raise ValueError('Presentation derivative differs from bound content')
    return proof
