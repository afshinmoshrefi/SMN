"""Separate SMN reader process. Administrator authentication is not imported."""
import os
import re
import json
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from flask import Flask, Response, jsonify, redirect, render_template, request, send_file

from article_content_store import ContentStore, ContentError
from reader_auth import ReaderAuth, ReaderError, _expiry


COOKIE = 'smn_reader'


def asset_url(article, revision, name, public=False):
    return ('/member/public-assets' if public else '/member/assets') + '?' + urlencode({
        'article': article, 'revision': revision, 'name': name})


def create_app(config=None, store=None, auth=None):
    app = Flask(__name__, static_folder=None)
    cfg = {key: os.environ.get('SMN_READER_' + key, '') for key in
           ('CLIENT_ID', 'CALLBACK_URL', 'AUTHORITY_URL', 'SERVICE_KEY', 'ENV', 'SHARED_DEV_CALLBACK')}
    key_file = os.environ.get('SMN_READER_SERVICE_KEY_FILE')
    if key_file:
        cfg['SERVICE_KEY'] = Path(key_file).read_text(encoding='utf-8').strip()
    cfg.update(config or {})
    private_root = cfg.get('PRIVATE_ROOT') or os.environ.get('SMN_READER_PRIVATE_ROOT', '/var/lib/smn/reader')
    public_roots = cfg.get('PUBLIC_ROOTS') or [os.environ.get('SMN_NEWS_ROOT', '/var/www/smn')]
    store = store or ContentStore(private_root, public_roots)
    auth = auth or ReaderAuth(Path(private_root) / 'sessions', cfg)
    app.extensions.update(smn_content_store=store, smn_reader_auth=auth)
    secure = True

    def cookie():
        return request.cookies.get(COOKIE)

    def set_cookie(response, sid, max_age):
        response.set_cookie(COOKIE, sid, max_age=max_age, secure=secure,
                            httponly=True, samesite='Lax', path='/')
        return response

    def clear_cookie(response):
        response.delete_cookie(COOKIE, path='/', secure=secure, httponly=True, samesite='Lax')
        return response

    @app.after_request
    def private_cache(response):
        # Conservative first implementation: no representation or offer can be cached.
        response.headers['Cache-Control'] = 'private, no-store'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Vary'] = 'Cookie'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        return response

    @app.errorhandler(ReaderError)
    def reader_error(exc):
        if request.path in ('/member/checkout', '/member/cancel'):
            return jsonify(error=exc.code, message=str(exc)), exc.status
        response = Response(render_template('reader_error.html', message=str(exc)), status=exc.status)
        return clear_cookie(response) if exc.code == 'invalid_login' or (exc.code == 'access_required' and exc.status == 401) else response

    @app.errorhandler(ContentError)
    def content_error(exc):
        return render_template('reader_error.html', message='This article is unavailable.'), 404

    @app.get('/member/health')
    def health():
        return jsonify(service='smn-reader', registry_enabled=store.enabled())

    @app.get('/member/login')
    def login():
        if not store.enabled():
            raise ReaderError('not_configured', 'Reader access is not available yet.')
        sid, url = auth.begin(request.args.get('return_to', '/'))
        return set_cookie(redirect(url), sid, 600)

    @app.get('/member/callback')
    def callback():
        sid, target = auth.complete(cookie(), request.args)
        local = auth.session(sid)
        age = max(1, int(_expiry(local['identity']['expires_at']) - auth.clock()))
        # Central authority expiry is checked server-side on every request.
        return set_cookie(redirect(target), sid, age)

    @app.post('/member/logout')
    def logout():
        try:
            target = auth.logout(cookie(), request.form.get('csrf') or request.headers.get('X-SMN-CSRF'))
        except ReaderError as exc:
            if exc.code == 'invalid_request':
                raise
            return clear_cookie(Response(render_template('reader_error.html',
                message='You are signed out locally. Provider sign-out is temporarily unavailable.'), status=503))
        return clear_cookie(redirect(target))

    @app.get('/member/signed-out')
    def signed_out():
        return clear_cookie(Response(render_template('reader_error.html', message='You are signed out.')))

    @app.get('/member/account')
    def account():
        result, local = auth.account(cookie())
        return render_template('reader_account.html', account=result, csrf=local['csrf'])

    @app.post('/member/checkout')
    def checkout():
        data = request.get_json(silent=True) or request.form
        payload = {'interval': data.get('interval'), 'offer_version': data.get('offer_version')}
        result = auth.action(cookie(), request.headers.get('X-SMN-CSRF') or data.get('csrf'), '/smn-reader/checkout', payload)
        # Checkout destinations are provider-issued, never caller-selected redirects.
        destination = urlsplit(result.get('url', ''))
        if destination.scheme != 'https' or destination.hostname != 'checkout.stripe.com' or destination.username or destination.password:
            raise ReaderError('provider_unavailable', 'Checkout could not be verified.')
        return redirect(result['url'], 303) if not request.is_json else jsonify(url=result['url'])

    @app.post('/member/cancel')
    def cancel():
        data = request.get_json(silent=True) or request.form
        result = auth.action(cookie(), request.headers.get('X-SMN-CSRF') or data.get('csrf'),
                             '/smn-reader/cancel', {'cancel_at_period_end': True})
        return jsonify(result) if request.is_json else redirect('/member/account', 303)

    def get_asset(public):
        if not public:
            entitlement, _ = auth.entitlement(cookie())
            if entitlement.get('can_read') is not True:
                raise ReaderError('access_required', 'Sign in with reader access to view this asset.', 403)
        path = store.private_asset_path(request.args.get('article', ''), request.args.get('revision', ''),
                                        request.args.get('name', ''), public=public)
        return send_file(str(path), conditional=False, etag=False)

    @app.get('/member/assets')
    def private_asset():
        return get_asset(False)

    @app.get('/member/public-assets')
    def public_asset():
        return get_asset(True)

    @app.get('/')
    def index():
        entries = []
        if store.enabled():
            for entry in store.inventory()['articles']:
                if entry['revision']:
                    content = store.read_revision(entry['path'])
                    entries.append({'path': entry['path'], 'title': content['headline']['text']})
        return render_template('reader_index.html', entries=entries)

    @app.get('/posts.json')
    def public_catalog():
        # Preserve existing archive search without serving the internal catalog.
        if not store.enabled():
            return jsonify([])
        catalog = Path(public_roots[0]) / 'posts.json'
        metadata = {}
        if catalog.is_file():
            metadata = {urlsplit(p.get('url', '')).path: p for p in json.loads(catalog.read_text('utf-8'))}
        result = []
        for row in store.inventory()['articles']:
            if not row['revision']:
                continue
            manifest = store.resolve(row['path'])
            copy = store.read_revision(row['path'])
            public = {key: metadata.get(row['path'], {}).get(key, '') for key in
                      ('slug', 'symbol', 'category', 'published_date', 'date', 'direction')}
            public.update(url=row['path'], title=copy['headline']['text'], dek=copy['preview'][0]['text'])
            if manifest.get('public_asset_ids'):
                public['hero_image'] = asset_url(row['path'], row['revision'], manifest['public_asset_ids'][0], True)
            result.append(public)
        return jsonify(result)

    @app.get('/briefings/')
    def briefings():
        import public_briefing
        return render_template('reader_briefings.html', entries=public_briefing.listing(private_root))

    @app.get('/briefings/<identifier>')
    def briefing(identifier):
        import public_briefing
        item = public_briefing.load(private_root, identifier)
        return render_template('reader_briefing.html', item=item, has_video=bool(public_briefing.video(private_root, item)))

    @app.get('/briefings/<identifier>/video')
    def briefing_video(identifier):
        import public_briefing
        item = public_briefing.load(private_root, identifier)
        path = public_briefing.video(private_root, item)
        if not path:
            raise ContentError('Video is unavailable')
        return send_file(str(path), mimetype='video/mp4', conditional=True, etag=False)

    @app.get('/<path:path>')
    def article(path):
        path = '/' + path
        if re.fullmatch(r'/editions/\d{4}-\d{2}-\d{2}/(?:index.html)?', path) and store.enabled():
            prefix = path.rsplit('/', 1)[0] + '/'
            entries = []
            for entry in store.inventory()['articles']:
                if entry['revision'] and entry['path'].startswith(prefix):
                    content = store.read_revision(entry['path'])
                    entries.append({'path': entry['path'], 'title': content['headline']['text']})
            return render_template('reader_index.html', entries=entries)
        manifest = store.resolve(path)
        if path != manifest['canonical_path']:
            return redirect(manifest['canonical_path'], 308)
        notice = None
        try:
            entitlement, local = auth.entitlement(cookie())
        except ReaderError:
            entitlement, local = {'can_read': False, 'reason': 'provider_unavailable'}, None
            notice = 'Reader access is temporarily unavailable. The public preview remains available; please retry.'
        if entitlement.get('can_read') is True:
            return Response(store.read_revision(path, 'full'), mimetype='text/html')
        return render_template('reader_preview.html', copy=manifest['preview']['content'],
                               canonical=path, notice=notice, signed_in=bool(local),
                               hero=asset_url(path, manifest['revision'], manifest['public_asset_ids'][0], True)
                               if manifest.get('public_asset_ids') else None)

    return app
