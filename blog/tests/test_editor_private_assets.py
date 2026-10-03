import base64
from contextlib import nullcontext
from copy import deepcopy
from html.parser import HTMLParser
from io import BytesIO
import json
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from test_pub_dashboard import DashboardFixture
import article_editor as editor
import article_editor_routes as routes
import article_index
import membership_publication as membership
import pub_dashboard


class PrivatePreviewTests(DashboardFixture):
    def setUp(self):
        super().setUp()
        for active in (patch.dict(pub_dashboard.app.config, {'TESTING': True}),
                       patch.dict('os.environ', {'SMN_READER_PRIVATE_ROOT': str(self.state / 'private')}),
                       patch.object(article_index, 'posts_lock', side_effect=lambda: nullcontext())):
            active.start(); self.addCleanup(active.stop)
        self.post = {'slug': 'MSFT', 'title': 'Preserved title',
                     'url': 'https://smn-dev.trxstat.com/editions/2026-10-02/MSFT/article.html'}
        self.folder = self.news / 'editions/2026-10-02/MSFT/assets'
        self.folder.mkdir(parents=True)
        self.images = []
        for i in range(5):
            data = BytesIO(); Image.new('RGB', (3 + i, 4 + i), (i * 30, 20, 40)).save(data, format='PNG')
            path = self.folder / ('chart-' + str(i) + '.png'); path.write_bytes(data.getvalue())
            self.images.append(data.getvalue())
        self.raw = ('<!doctype html><html><head><style>.hero{background:url(assets/chart-0.png)}</style></head>'
                    '<body><section data-role="opening"><p>Exact historical text &amp; qualifications.</p></section>'
                    '<picture><source srcset="assets/chart-0.png 1x, assets/chart-1.png 2x">'
                    '<img src="assets/chart-0.png"></picture>'
                    '<img src=assets/chart-1.png><img src="assets/chart-2.png?v=' + membership.sha(self.images[2])[:16] + '">'
                    '<img src="assets/chart-3.png"><img src="assets/chart-4.png"></body></html>')
        self.private = membership.store()
        self.updated, self.manifest = membership.prepare(self.post, self.raw)
        self.protected = self.private._private_file(self.manifest, 'full.html').read_text('utf-8')

    def render(self, raw=None, post=None, manifest=None):
        return routes.preview_assets(self.raw if raw is None else raw, post or self.updated,
                                     self.private, manifest or self.manifest)

    def test_all_five_original_bytes_and_mobile_candidates_survive_without_public_files(self):
        for path in self.folder.iterdir(): path.unlink()
        rendered = self.render()
        self.assertIn('Exact historical text &amp; qualifications.', rendered)
        self.assertIn('<!doctype html><html><head><style>.hero{background:url("data:image/png;base64,', rendered)
        found = []
        class Images(HTMLParser):
            def handle_starttag(self, tag, attrs):
                values = dict(attrs)
                if tag == 'img': found.append(base64.b64decode(values['src'].split(',', 1)[1]))
                if tag == 'source':
                    self_outer.assertIn(' 1x, data:image/png;base64,', values['srcset'])
                    self_outer.assertTrue(values['srcset'].endswith(' 2x'))
        self_outer = self
        Images().feed(rendered)
        self.assertEqual(found, self.images)
        self.assertEqual(self.raw.count('<img'), rendered.count('<img'))
        self.assertEqual(self.protected.count('<img'), self.render(self.protected).count('<img'))

    def test_pre_migration_draft_preview_preserves_all_revisions_messages_and_dirty_state(self):
        draft = editor.open_draft('test-owner', self.post, self.raw)
        draft['revisions'] = [dict(draft['revisions'][0], version=i, html=self.raw if i == 0 else self.raw.replace('Exact historical', f'Exact revision {i} historical')) for i in range(5)]
        draft.update(version=4, messages=[{'role':'user','text':'Preserved private request'}])
        with editor.database() as db: editor.save(db, draft)
        article_index.save_posts([self.updated], backup=False)
        before = json.dumps(editor.get(draft['id'], 'test-owner'), sort_keys=True)
        for i in (0, 4):
            response = self.client.get(f"/api/editor/{draft['id']}/preview?version={i}")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.text.count('<img'), 5)
            self.assertIn('Exact historical' if i == 0 else f'Exact revision {i} historical', response.text)
            self.assertNotIn('/member/assets?', response.text)
            self.assertIn('data:image/png;base64,', response.text)
            self.assertNotIn('allow-same-origin', response.headers['Content-Security-Policy'])
            self.assertIn("connect-src 'none'", response.headers['Content-Security-Policy'])
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual(json.dumps(editor.get(draft['id'], 'test-owner'), sort_keys=True), before)
        with patch.dict(pub_dashboard.app.config, {'TESTING': False}), patch.dict('os.environ', {'SMN_EDITOR_OWNER_USER_ID': 'real-owner'}):
            self.assertEqual(self.client.get(f"/api/editor/{draft['id']}/preview?version=4").status_code, 403)

    def test_frozen_protected_draft_uses_its_revision_not_current_catalog(self):
        draft = editor.open_draft('test-owner', self.updated, self.protected)
        article_index.save_posts([dict(self.updated, membership_revision='another-live-revision')], backup=False)
        response = self.client.get(f"/api/editor/{draft['id']}/preview?version=0")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn('data:image/png;base64,', response.text)

    def test_cross_article_unknown_traversal_bad_cache_and_revision_are_closed(self):
        from reader_app import asset_url
        name = next(iter(self.manifest['assets']))
        urls = ['assets/missing.png', '../MSFT/assets/chart-0.png',
                'assets/%2e%2e/chart-0.png', 'assets/chart-0.png?v=00000000',
                'assets/chart-0.png?download=1',
                asset_url('/editions/2026-10-02/XLK/article.html', self.manifest['revision'], name),
                asset_url(membership.path_for(self.updated), 'wrong-revision', name),
                asset_url(membership.path_for(self.updated), self.manifest['revision'], '../full.html')]
        for url in urls:
            with self.subTest(url=url), self.assertRaises(membership.ContentError):
                self.render('<img src="' + url.replace('&', '&amp;') + '">')
        with self.assertRaises(membership.ContentError):
            self.render(post=dict(self.updated, membership_revision='wrong'))
        with self.assertRaises(membership.ContentError):
            self.render(manifest=dict(self.manifest, canonical_path='/articles/another.html'))

    def test_existing_external_data_fragment_and_css_fonts_pass_without_fetch_or_read(self):
        raw = ('<style>@font-face{src:url(https://fonts.example.test/font.woff2)} '
               '.local{src:url(font.woff2)} .mask{filter:url(#mask)}</style>'
               '<img src="https://images.example.test/icon.png">'
               '<img src="data:image/png;base64,EXISTING">'
               '<source srcset="data:image/png;base64,ONE 1x, data:image/png;base64,TWO 2x">')
        with patch.object(self.private, '_private_file', side_effect=AssertionError('No private read')):
            self.assertEqual(self.render(raw), raw)

    def test_mixed_inline_and_private_srcset_still_checks_private_binding(self):
        raw = '<source srcset="data:image/png;base64,EXISTING 1x, assets/chart-0.png 2x">'
        rendered = self.render(raw)
        self.assertIn('data:image/png;base64,EXISTING 1x, data:image/png;base64,', rendered)
        with self.assertRaises(membership.ContentError):
            self.render(raw.replace('assets/chart-0.png', '../XLK/assets/chart-0.png'))

    def test_svg_executable_wrong_mime_changed_and_missing_private_bytes_are_closed(self):
        from reader_app import asset_url
        name = next(iter(self.manifest['assets']))
        file = self.private._private_file(self.manifest, 'assets/' + name)
        raw = '<img src="' + asset_url(membership.path_for(self.updated), self.manifest['revision'], name).replace('&', '&amp;') + '">'
        for payload in (b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', b'<html>executable</html>'):
            file.write_bytes(payload)
            manifest = deepcopy(self.manifest); manifest['assets'][name]['sha256'] = membership.sha(payload)
            with self.assertRaises((membership.ContentError, OSError)): self.render(raw, manifest=manifest)
        with self.assertRaises(membership.ContentError): self.render(raw)
        file.unlink()
        with self.assertRaises(FileNotFoundError): self.render(raw)

    def test_pre_migration_unavailable_or_cross_article_catalog_returns_private_error(self):
        draft = editor.open_draft('test-owner', self.post, self.raw)
        for posts in ([], [dict(self.updated, url='https://smn-dev.trxstat.com/articles/XLK.html')]):
            article_index.save_posts(posts, backup=False)
            response = self.client.get(f"/api/editor/{draft['id']}/preview?version=0")
            self.assertEqual(response.status_code, 409)
            self.assertNotIn('data:image/', response.text)

    def test_pre_migration_newer_source_binding_cannot_supply_different_charts(self):
        draft = editor.open_draft('test-owner', self.post, self.raw.replace('Exact historical', 'Different original'))
        article_index.save_posts([self.updated], backup=False)
        response = self.client.get(f"/api/editor/{draft['id']}/preview?version=0")
        self.assertEqual(response.status_code, 409)
        self.assertNotIn('data:image/', response.text)
