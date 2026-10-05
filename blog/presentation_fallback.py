"""Deterministic presentation derivative; never rewrites source article approvals."""
from pathlib import Path
import hashlib
import json

from bs4 import BeautifulSoup

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


def derive(original):
    """Change styling and omit only the renderer's decorative hero figure."""
    soup = BeautifulSoup(original, 'html.parser')
    if soup.head is None or soup.body is None or len(soup.find_all('article')) != 1:
        raise ValueError('Unrecognized article presentation')
    if soup.find('script') or soup.find('iframe'):
        raise ValueError('Unexpected active content in article')
    for node in soup.select('figure.hero'):
        if node.find(['p', 'table', 'section']) or len(node.find_all('img')) != 1:
            raise ValueError('Hero contains unexpected content')
        node.decompose()
    for node in soup.find_all('style'):
        node.decompose()
    for node in soup.find_all(True):
        node.attrs.pop('style', None)
    style = soup.new_tag('style')
    style.string = CSS
    soup.head.append(style)
    meta = soup.new_tag('meta', attrs={'name':'smn-presentation', 'content':'deterministic-readable-v1'})
    soup.head.append(meta)
    return str(soup)


def prepare_fallback(result):
    """Return HTML and a content-bound derivative proof; browser checks still required."""
    from editorial_gate import verify_content
    result = Path(result)
    content = verify_content(result, allow_held_binding=True)
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
