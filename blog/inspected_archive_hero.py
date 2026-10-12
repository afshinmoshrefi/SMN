"""Copy only an inspected, unchanged, same-symbol published illustration."""
from pathlib import Path
from urllib.parse import urlsplit
import base64,hashlib,json

LIBRARY=Path(__file__).with_name('approved_archived_heroes.json')
WEB=Path('/var/www/smn')

def available(post, *, web=WEB, library=LIBRARY):
    web=Path(web).resolve();library=Path(library)
    data=json.loads(library.read_text(encoding='utf8'))
    if data.get('schema_version')!=1:raise ValueError('Unknown inspected archive library')
    item=next((x for x in data['assets'] if x['symbol']==post['symbol']),None)
    if item is None or not web.is_dir():return None
    paths={}
    for kind in ['asset','article']:
        relative=Path(item[kind+'_path']);file=web/relative
        if relative.is_absolute() or '..' in relative.parts or file.is_symlink() or web not in file.resolve().parents:return None
        if not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest()!=item[kind+'_sha256']:return None
        paths[kind]=file
    catalog=web/'posts.json'
    if not catalog.is_file():return None
    posts=json.loads(catalog.read_text(encoding='utf8'))
    if not any(p.get('symbol')==post['symbol'] and urlsplit(p.get('url','')).path.lstrip('/')==item['article_path'] for p in posts):return None
    from PIL import Image
    with Image.open(paths['asset']) as image:image.verify()
    with Image.open(paths['asset']) as image:
        if image.size!=(item['width'],item['height']) or image.width<600 or image.height<300:return None
        extension={'JPEG':'.jpg','PNG':'.png','WEBP':'.webp'}.get(image.format)
        colors=image.convert('RGB').getcolors(1024)
        if extension is None or colors and max(n for n,_ in colors)>image.width*image.height*.90:return None
    raw=paths['asset'].read_bytes()
    return {'image_base64':base64.b64encode(raw).decode(),'sha256':item['asset_sha256'],
            'provider':'SMN inspected archive','asset_kind':'conceptual_illustration','method':'inspected-archive-v1',
            'api_cost_stage':False,'cost_usd':0,'asset_extension':extension,'alt':item['alt'],
            'source_asset_url':'https://seasonalmarketnews.com/'+item['asset_path'],
            'source_publication_url':'https://seasonalmarketnews.com/'+item['article_path'],
            'inspection':{k:item[k] for k in ['actor','approved_utc','asset_sha256','article_sha256','publication_receipt_sha256']}}
