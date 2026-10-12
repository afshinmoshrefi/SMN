"""A file/dimension receipt is not proof that a hero is usable artwork."""
from pathlib import Path
import hashlib
import json

KNOWN_BLANK_JPEG = 'b40c88ee7cd2b0c360fbc2d16199b4a323e2f292188337d862f54483e1a02d76'


def placeholder(record):
    words=' '.join(str(record.get(k,'')) for k in ('provider','asset_kind','method'))
    words+=' '+str(record.get('provenance',{}).get('kind',''))
    return 'placeholder' in words.lower() or record.get('sha256')==KNOWN_BLANK_JPEG


def available_hero(result):
    result=Path(result).resolve();record_path=result/'hero-asset.json'
    if not record_path.is_file():return None
    record=json.loads(record_path.read_text(encoding='utf-8'))
    if placeholder(record):return None
    relative=Path(record.get('url',''))
    if relative.is_absolute() or len(relative.parts)!=2 or relative.parts[0]!='assets':
        raise ValueError('Unsafe hero asset URL')
    file=result/relative
    if file.is_symlink() or result not in file.resolve().parents or not file.is_file():
        raise ValueError('Missing/unsafe hero asset')
    if hashlib.sha256(file.read_bytes()).hexdigest()!=record.get('sha256'):
        raise ValueError('Hero asset changed')
    from PIL import Image
    with Image.open(file) as image:
        image.verify()
    with Image.open(file) as image:
        if image.width<600 or image.height<300:return None
        colors=image.convert('RGB').getcolors(1024)
        if colors and max(count for count,_ in colors)>image.width*image.height*0.90:
            return None  # Mostly empty raster, including the observed gray stripe.
    return record
