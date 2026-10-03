"""Inventory legacy exposure and stage explicitly mapped reviewed revisions; never delete/activate."""
import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

from article_content_store import ContentStore, ContentError


def inventory_public(public_roots, posts_file=None):
    rows = []
    for root in map(lambda p: Path(p).resolve(), public_roots):
        for file in sorted(root.rglob('*')):
            if not file.is_file():
                continue
            if file.is_symlink() or root not in file.resolve().parents:
                raise ContentError('Public inventory contains a symlink or escaped file')
            relative = file.relative_to(root).as_posix()
            rows.append({'root': str(root), 'path': str(file), 'url_path': '/' + relative,
                         'sha256': hashlib.sha256(file.read_bytes()).hexdigest(),
                         'kind': 'html' if file.suffix.lower() == '.html' else 'asset_or_export'})
    catalog = []
    if posts_file:
        posts = json.loads(Path(posts_file).read_text(encoding='utf-8-sig'))
        for post in posts:
            path = urlsplit(post.get('url', '')).path
            catalog.append({'canonical_path': path, 'source_path': post.get('path'),
                            'slug': post.get('slug'), 'published': post.get('publish_status')})
    return {'files': rows, 'catalog': catalog, 'publish': False,
            'note': 'Inventory only. Canonical equivalence, asset rewrites, withdrawal and proxy deny require root review.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    inventory = sub.add_parser('inventory')
    inventory.add_argument('--public-root', action='append', required=True)
    inventory.add_argument('--posts')
    inventory.add_argument('--output', required=True)
    prepare = sub.add_parser('prepare')
    prepare.add_argument('--store-root', required=True)
    prepare.add_argument('--public-root', action='append', required=True)
    prepare.add_argument('--manifest', required=True)
    args = parser.parse_args()
    if args.command == 'inventory':
        output = Path(args.output)
        with output.open('x', encoding='utf-8') as stream:
            json.dump(inventory_public(args.public_root, args.posts), stream, indent=2)
        print(json.dumps({'inventory': str(output), 'publish': False}))
    else:
        source = json.loads(Path(args.manifest).read_text(encoding='utf-8'))
        store = ContentStore(args.store_root, args.public_root)
        preview = json.loads(Path(source['preview_path']).read_text(encoding='utf-8'))
        approval = json.loads(Path(source['approval_path']).read_text(encoding='utf-8'))
        result = store.prepare_revision(source['canonical_path'], source['revision'],
            Path(source['full_html_path']).read_text(encoding='utf-8'), preview, approval,
            assets=source.get('assets', {}), public_asset_ids=source.get('public_asset_ids', []),
            aliases=source.get('verified_aliases', []))
        print(json.dumps({'canonical_path': result['canonical_path'], 'revision': result['revision'],
                          'active': False, 'publish': False}))


if __name__ == '__main__':
    main()
