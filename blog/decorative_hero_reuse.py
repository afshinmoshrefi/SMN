"""A narrowly bound image derivative; original factual reviews remain intact."""
from pathlib import Path
import hashlib,html,json,re
from urllib.parse import urlsplit

FIGURE=re.compile(r'<figure\b[^>]*\bclass="[^"]*\bhero\b[^"]*"[^>]*>[\s\S]*?</figure>')
LICENSED_PHOTO='licensed_external_decorative_photograph'
CC_BY_SA='https://creativecommons.org/licenses/by-sa/4.0/'

def sha(raw):return hashlib.sha256(raw).hexdigest()

def photo_credit(hero):
    """Only the supported unchanged CC BY-SA photo path supplies linked credit."""
    credit=hero.get('photo_credit')
    if credit is None:return None
    if not isinstance(credit,dict):raise ValueError('Photo credit required')
    source=urlsplit(credit.get('source_url',''))
    if (source.scheme!='https' or source.netloc!='commons.wikimedia.org' or
            not source.path.startswith('/wiki/File:') or source.query or source.fragment or
            not isinstance(credit.get('author'),str) or not credit['author'].strip() or
            credit.get('license_name')!='CC BY-SA 4.0' or credit.get('license_url')!=CC_BY_SA or
            credit.get('modification_note')!='Original photograph; no crop or retouch.'):
        raise ValueError('Supported source, author, license and modification disclosure required')
    return credit

def licensed_photo(result,record,receipt,hero):
    credit=photo_credit(hero)
    asset=urlsplit(receipt.get('source_asset_url',''))
    authorization=receipt.get('publication_authorization',{})
    target=urlsplit(authorization.get('target_url',''))
    if (credit is None or receipt.get('kind')!='licensed_external_photograph_v1' or
            receipt.get('symbol')!=result.name or receipt.get('sha256')!=hero.get('sha256') or
            receipt.get('photo_credit')!=credit or record.get('visual_inspection_passed') is not True or
            receipt.get('visual_inspection_passed') is not True or
            authorization.get('actor')!='Afshin' or not authorization.get('reference') or not authorization.get('utc') or
            authorization.get('asset_sha256')!=hero.get('sha256') or
            target.scheme!='https' or target.netloc!='seasonalmarketnews.com' or target.query or target.fragment or
            not re.fullmatch('/editions/[0-9]{4}-[0-9]{2}-[0-9]{2}/'+re.escape(result.name)+'/article.html',target.path) or
            receipt.get('modification_status')!='unmodified' or
            asset.scheme!='https' or asset.netloc!='upload.wikimedia.org' or
            not asset.path.startswith('/wikipedia/commons/') or asset.query or asset.fragment):
        raise ValueError('Inspected and authorized licensed photograph receipt required')
    evidence=receipt.get('license_evidence',{})
    for key in ('file_page','license_page'):
        item=evidence.get(key,{})
        name=item.get('file','')
        if not isinstance(name,str) or not name or Path(name).name!=name:
            raise ValueError('Unsafe licensed-source evidence path')
        path=result/name
        if path.is_symlink() or sha(path.read_bytes())!=item.get('sha256'):
            raise ValueError('Licensed-source evidence changed')
        if b'creativecommons.org/licenses/by-sa/4.0' not in path.read_bytes():
            raise ValueError('Archived license declaration missing')
        if key=='file_page' and credit['author'].encode() not in path.read_bytes():
            raise ValueError('Archived author declaration missing')
    return hero

def override(result):
    result=Path(result);path=result/'decorative-hero-reuse.json'
    if not path.is_file():return None
    record=json.loads(path.read_text(encoding='utf-8'))
    fresh=record.get('kind')=='fresh_generated_decorative_illustration'
    licensed=record.get('kind')==LICENSED_PHOTO
    if (not fresh and not licensed and record.get('kind')!='previously_published_decorative_illustration') or not record.get('actor') or not record.get('utc'):
        raise ValueError('Attributable decorative image reuse required')
    for name in ('article.json','hero-asset.json','article.html'):
        if sha((result/name).read_bytes())!=record.get('original_files',{}).get(name):
            raise ValueError('Decorative recovery base changed: '+name)
    hero=record.get('hero')
    if not isinstance(hero,dict):raise ValueError('Hero metadata required')
    if not fresh and not licensed and not record.get('source_publication_url','').startswith('https://seasonalmarketnews.com/'):
        raise ValueError('Existing publication provenance required')
    source=result/'decorative-hero-source-receipt.json'
    if sha(source.read_bytes())!=record.get('source_receipt_sha256'):
        raise ValueError('Archived publication receipt changed')
    receipt=json.loads(source.read_text(encoding='utf-8'))
    if licensed:return licensed_photo(result,record,receipt,hero)
    if fresh:
        if (receipt.get('symbol')!=result.name or receipt.get('sha256')!=hero.get('sha256')
                or receipt.get('provider')!='existing production hero_image_workflow'
                or receipt.get('hero_check',{}).get('passed') is not True
                or record.get('visual_inspection_passed') is not True
                or hero.get('caption')!='AI-generated conceptual illustration.'):
            raise ValueError('Inspected fresh generation receipt required')
        return hero
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
    if not re.fullmatch(r'assets/hero-(?:reused|fresh)-[a-f0-9]{12}\.(?:jpg|png|webp)',relative):
        raise ValueError('Unsafe reused hero URL')
    caption=hero.get('caption','Conceptual illustration from the Seasonal Market News archive.')
    caption=html.escape(caption)
    credit=photo_credit(hero)
    if credit:
        caption+=' Photo: <a href="'+html.escape(credit['source_url'],quote=True)+'" rel="noopener">'+html.escape(credit['author'])+' / Wikimedia Commons</a>. <a href="'+CC_BY_SA+'" rel="license">CC BY-SA 4.0</a>. '+credit['modification_note']
    replacement='<figure class="hero"><img src="'+relative+'" alt="'+html.escape(hero['alt'],quote=True)+'" width="'+str(hero['width'])+'" height="'+str(hero['height'])+'"><figcaption>'+caption+'</figcaption></figure>'
    revised=original[:found.start()]+replacement+original[found.end():]
    if FIGURE.sub('',original)!=FIGURE.sub('',revised):raise ValueError('Nondecorative article bytes changed')
    return revised
