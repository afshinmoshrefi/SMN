"""A narrowly bound image derivative; original factual reviews remain intact."""
from pathlib import Path
import hashlib,html,json,re
from urllib.parse import urlsplit

FIGURE=re.compile(r'<figure\b[^>]*\bclass="[^"]*\bhero\b[^"]*"[^>]*>[\s\S]*?</figure>')

def sha(raw):return hashlib.sha256(raw).hexdigest()

def override(result):
    result=Path(result);path=result/'decorative-hero-reuse.json'
    if not path.is_file():return None
    record=json.loads(path.read_text(encoding='utf-8'))
    if record.get('kind')!='previously_published_decorative_illustration' or not record.get('actor') or not record.get('utc'):
        raise ValueError('Attributable decorative image reuse required')
    for name in ('article.json','hero-asset.json','article.html'):
        if sha((result/name).read_bytes())!=record.get('original_files',{}).get(name):
            raise ValueError('Decorative recovery base changed: '+name)
    hero=record.get('hero')
    if not isinstance(hero,dict) or not record.get('source_publication_url','').startswith('https://seasonalmarketnews.com/'):
        raise ValueError('Existing publication provenance required')
    source=result/'decorative-hero-source-receipt.json'
    if sha(source.read_bytes())!=record.get('source_receipt_sha256'):
        raise ValueError('Archived publication receipt changed')
    receipt=json.loads(source.read_text(encoding='utf-8'))
    if receipt.get('status')!='live_verified' or receipt.get('production_written') is not True:
        raise ValueError('Verified publication provenance required')
    asset=urlsplit(record.get('source_asset_url',''));article=urlsplit(record['source_publication_url'])
    for url in (asset,article):
        if url.scheme!='https' or url.netloc!='seasonalmarketnews.com' or url.query or url.fragment:
            raise ValueError('Source outside exact publication origin')
    if receipt.get('retained_heroes',{}).get(asset.path.lstrip('/'))!=hero.get('sha256') or article.path.lstrip('/') not in receipt.get('retained_articles',{}):
        raise ValueError('Reusable art lacks retained publication byte proof')
    return hero

def replace(original,hero):
    matches=list(FIGURE.finditer(original))
    if len(matches)!=1:raise ValueError('One decorative hero figure required')
    found=matches[0]
    if re.search(r'<(?:table|p|section)|data-native-chart|data-figure',found.group(),re.I):
        raise ValueError('Hero figure contains nondecorative content')
    relative=hero['url']
    if not re.fullmatch(r'assets/hero-reused-[a-f0-9]{12}\.(?:jpg|png|webp)',relative):
        raise ValueError('Unsafe reused hero URL')
    replacement='<figure class="hero"><img src="'+relative+'" alt="'+html.escape(hero['alt'],quote=True)+'" width="'+str(hero['width'])+'" height="'+str(hero['height'])+'"><figcaption>Conceptual illustration from the Seasonal Market News archive.</figcaption></figure>'
    revised=original[:found.start()]+replacement+original[found.end():]
    if FIGURE.sub('',original)!=FIGURE.sub('',revised):raise ValueError('Nondecorative article bytes changed')
    return revised
