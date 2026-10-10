"""Prepare and activate a complete private article corpus under the publication lock."""
import argparse
import html
import json
import os
from pathlib import Path

import article_index
import membership_publication as publication
from article_content_store import ContentError


def prepare():
    private = publication.store()
    record = private.root / 'migration.json'
    if record.exists():
        previous = json.loads(record.read_text('utf-8'))
        if previous['status'] == 'active':
            return previous
    with article_index.posts_lock():
        before = article_index.POSTS_JSON.read_bytes()
        posts = json.loads(before)
        prepared, failures = [], []
        for post in posts:
            try:
                source = Path(post.get('path', ''))
                if (not source.is_file() or source.is_symlink()
                        or article_index.NEWS_ROOT.resolve() not in source.resolve().parents):
                    raise ContentError('Source is missing or outside the public inventory')
                updated, manifest = publication.prepare(post, source.read_text('utf-8'))
                prepared.append({'old': post, 'new': updated, 'canonical': manifest['canonical_path'],
                                 'revision': manifest['revision'], 'source_sha256': publication.sha(source.read_bytes())})
            except (ValueError, OSError) as exc:
                failures.append({'slug': post.get('slug'), 'error': str(exc)})
        result = {'status': 'held' if failures else 'prepared', 'created_at': publication.now(),
            'catalog_sha256': publication.sha(before), 'count': len(posts), 'prepared': prepared,
            'failures': failures, 'policy': 'Complete articles are open unless a post is locked with access "members".'}
        publication._write(record, result)
        return result


def activate():
    private = publication.store()
    record = private.root / 'migration.json'
    result = json.loads(record.read_text('utf-8'))
    if result['status'] == 'active':
        return result
    if result['status'] not in ('prepared', 'activating') or len(result['prepared']) != result['count']:
        raise ContentError('Every catalog article must be prepared before activation')
    if os.environ.get('SMN_READER_GATE_VERIFIED') != '1':
        raise ContentError('Verify the proxy deny rules before corpus activation')
    with article_index.posts_lock():
        already_written = json.loads(article_index.POSTS_JSON.read_text('utf-8')) == [item['new'] for item in result['prepared']]
        if not already_written and publication.sha(article_index.POSTS_JSON.read_bytes()) != result['catalog_sha256']:
            raise ContentError('Catalog changed after preparation')
        for item in result['prepared']:
            old_path = Path(item['old']['path'])
            source = old_path if old_path.is_file() else private.root / 'legacy-public-corpus' / old_path.relative_to(article_index.NEWS_ROOT)
            if publication.sha(source.read_bytes()) != item['source_sha256']:
                raise ContentError('Article changed after preparation')
        archive = private.root / 'legacy-public-corpus'
        archive.mkdir(mode=0o700, exist_ok=True)
        backup = archive / 'posts.json'
        if not backup.exists():
            backup.write_bytes(article_index.POSTS_JSON.read_bytes())
        result['status'] = 'activating'
        publication._write(record, result)
        # Reversible moves remove all old bodies, raw evidence and orphan exports.
        # A rollback must keep these private; it must never reopen the old URLs.
        for name in ('articles', 'editions', 'datasets'):
            source, destination = article_index.NEWS_ROOT / name, archive / name
            if source.is_symlink() or source.resolve().parent != article_index.NEWS_ROOT.resolve():
                raise ContentError('Unexpected public corpus path')
            if source.exists():
                if destination.exists():
                    raise ContentError('Corpus move requires reconciliation')
                source.rename(destination)
        for item in result['prepared']:
            private.activate_revision(item['canonical'], item['revision'],
                'initial-migration-' + publication.sha(item['canonical'].encode()), None)
        posts = [item['new'] for item in result['prepared']]
        article_index.save_posts(posts)
        heroes = {item['old'].get('hero_image'): item['new'].get('hero_image') for item in result['prepared']
                  if item['old'].get('hero_image') and item['new'].get('hero_image')}
        for name in ('index.html', 'search.html', 'search_index.json', 'suggest.json', 'home-manifest.json'):
            target = article_index.NEWS_ROOT / name
            if not target.is_file():
                continue
            original = target.read_text('utf-8')
            if not (archive / name).exists():
                (archive / name).write_text(original, encoding='utf-8')
            changed = original
            if name.endswith('.html') and '/briefings/' not in changed:
                changed = changed.replace('<nav>', '<nav><a href="/briefings/">Market Briefing</a><a href="/member/account">Account</a>', 1)
            for old, new in heroes.items():
                changed = changed.replace(old, html.escape(new, quote=True) if name.endswith('.html') else new)
            if changed != original:
                temporary = target.with_suffix(target.suffix + '.membership-tmp')
                temporary.write_text(changed, encoding='utf-8')
                temporary.replace(target)
        private.set_enabled(True)
        result.update(status='active', activated_at=publication.now(), public_corpus_archived=True)
        publication._write(record, result)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'activate'])
    args = parser.parse_args()
    result = prepare() if args.action == 'prepare' else activate()
    print(json.dumps({key: result[key] for key in ('status', 'count', 'failures') if key in result}))
    if result['status'] == 'held':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
