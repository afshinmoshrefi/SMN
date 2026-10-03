"""Admin-only membership/promotion panel routes; root supplies source-bound handlers."""
import re
from pathlib import Path
from urllib.parse import urlsplit

from flask import Blueprint, g, jsonify, request, send_file
from article_content_store import ContentError

PROMOTION_KINDS = ('derivative', 'article_video', 'daily_briefing', 'daily_avatar', 'social_export', 'substack_export')


class PanelError(ValueError):
    def __init__(self, message, status=400, code='invalid_request'):
        super().__init__(message)
        self.status, self.code = status, code


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,200}', value) or value in ('.', '..'):
        raise PanelError('Choose a valid article or job.')
    return value


def _integer(value, name, maximum=10**9):
    if type(value) is not int or not 0 <= value <= maximum:
        raise PanelError(name + ' must be a whole number in its allowed range.')
    return value


def _body(allowed, required=()):
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or set(body) - set(allowed) or set(required) - body.keys():
        raise PanelError('This form is incomplete or contains unsupported fields.')
    return body


def _offer(value):
    fields = {'mode', 'currency', 'monthly_amount', 'annual_mode', 'annual_amount',
              'annual_discount_bps', 'trial_days', 'intervals'}
    if not isinstance(value, dict) or set(value) != fields:
        raise PanelError('Complete membership offer required.')
    if value['mode'] not in ('free', 'paid') or value['currency'] != 'usd':
        raise PanelError('Choose free or paid membership in US dollars.')
    _integer(value['monthly_amount'], 'Monthly price')
    _integer(value['trial_days'], 'Trial days', 365)
    intervals = value['intervals']
    if (not isinstance(intervals, list) or any(x not in ('month', 'year') for x in intervals)
            or len(set(intervals)) != len(intervals)):
        raise PanelError('Choose supported billing intervals.')
    if value['annual_mode'] not in ('explicit', 'discount'):
        raise PanelError('Choose annual amount or annual discount.')
    if value['mode'] == 'free':
        if value['annual_amount'] is not None:
            _integer(value['annual_amount'], 'Annual price')
        if value['monthly_amount'] or value['annual_amount'] not in (None, 0) or value['annual_discount_bps'] is not None or value['trial_days'] or intervals:
            raise PanelError('Free membership has no price, billing intervals or trial countdown.')
    else:
        if not value['monthly_amount'] or not intervals:
            raise PanelError('Paid membership needs a positive price and a billing interval.')
        if value['annual_mode'] == 'discount':
            _integer(value['annual_discount_bps'], 'Annual discount', 9999)
            if value['annual_amount'] is not None:
                raise PanelError('Annual amount is supplied by the membership service in discount mode.')
        else:
            _integer(value['annual_amount'], 'Annual price')
            if value['annual_discount_bps'] is not None or ('year' in intervals and not value['annual_amount']):
                raise PanelError('Annual membership needs a positive amount and one pricing mode.')
    return value


def register(app, handlers, client=None):
    """Handlers: list/get/save/review/generate preview; promotion list/get/create/action/artifact."""
    if client is None:
        import membership_admin_client as client
    bp = Blueprint('membership_panel', __name__)

    def ok(data):
        return jsonify(ok=True, data=data, meta={})

    def actor():
        identity = getattr(g, 'identity', {}) or {}
        return str(identity.get('user_id'))

    def invoke(name, *args):
        callback = handlers.get(name)
        if not callable(callback):
            raise PanelError('This operation is not connected yet.', 503, 'not_configured')
        return callback(*args)

    @bp.before_request
    def admin_only():
        identity = getattr(g, 'identity', {}) or {}
        if identity.get('kind') != 'admin' or identity.get('user_id') is None:
            raise PanelError('Sign in as an administrator to manage membership and promotion.', 403, 'admin_required')
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            if request.headers.get('X-SMN-Dashboard') != '1':
                raise PanelError('Reload this dashboard before saving.', 403, 'csrf')
            origin = request.headers.get('Origin')
            if origin:
                parsed = urlsplit(origin)
                if parsed.scheme != request.scheme or parsed.netloc != request.host or parsed.path or parsed.query or parsed.fragment:
                    raise PanelError('Use this dashboard to make changes.', 403, 'csrf')

    @bp.errorhandler(Exception)
    def failed(exc):
        if isinstance(exc, PanelError) or isinstance(exc, getattr(client, 'MembershipError', PanelError)):
            return jsonify(ok=False, error={'code': exc.code, 'message': str(exc), 'hint': ''}), exc.status
        if isinstance(exc, ContentError):
            message = str(exc)
            if (not 1 <= len(message) <= 500 or re.search(r'[<>\x00-\x1f]|[A-Za-z]:[\\/]|(?:^|\s)/\S+|\b(?:bearer|token|secret|password)\b|sk-[A-Za-z0-9]', message, re.I)):
                message = 'The saved revision changed or this action is invalid. Refresh before retrying.'
            return jsonify(ok=False, error={'code': 'conflict', 'message': message, 'hint': ''}), 409
        if isinstance(exc, ValueError):
            return jsonify(ok=False, error={'code': 'conflict', 'message': 'The saved revision changed or this action is invalid. Refresh before retrying.', 'hint': ''}), 409
        # Provider and filesystem errors never expose tokens, paths or raw responses.
        return jsonify(ok=False, error={'code': 'unavailable', 'message': 'This operation could not be completed. Refresh and retry.', 'hint': ''}), 503

    @bp.after_request
    def private(response):
        response.headers['Cache-Control'] = 'private, no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    @bp.get('/api/membership/settings')
    def settings():
        return ok(client.request_settings(g.identity))

    @bp.put('/api/membership/settings')
    def save_settings():
        body = _body(('expected_version', 'offer'), ('expected_version', 'offer'))
        _integer(body['expected_version'], 'Settings version')
        _offer(body['offer'])
        return ok(client.request_settings(g.identity, method='POST', body=body))

    @bp.post('/api/membership/activate')
    def activate():
        body = _body(('expected_version', 'draft_id'), ('expected_version', 'draft_id'))
        _integer(body['expected_version'], 'Settings version')
        if type(body['draft_id']) is not int or body['draft_id'] <= 0:
            raise PanelError('Choose the saved draft to activate.')
        return ok(client.request_settings(g.identity, method='POST', body=body, activate=True))

    @bp.get('/api/membership/articles')
    def articles():
        return ok(invoke('list_articles'))

    @bp.get('/api/membership/articles/<slug>/preview')
    def preview(slug):
        return ok(invoke('get_preview', _identifier(slug)))

    @bp.put('/api/membership/articles/<slug>/preview')
    def save_preview(slug):
        body = _body(('expected_revision', 'content'), ('expected_revision', 'content'))
        if not isinstance(body['expected_revision'], str) or not body['expected_revision']:
            raise PanelError('Reload the current article revision before saving.')
        content = body['content']
        if not isinstance(content, dict) or set(content) != {'headline', 'preview', 'full_article_value', 'qualification', 'social', 'video'}:
            raise PanelError('Use the approved public preview form.')
        if not isinstance(content['preview'], list) or not 1 <= len(content['preview']) <= 3:
            raise PanelError('Use a lead and at most two additional public paragraphs.')
        for statement in [content['headline'], *content['preview'], content['full_article_value'], content['qualification']]:
            if (not isinstance(statement, dict) or set(statement) != {'text', 'source_ids', 'article_refs'}
                    or not isinstance(statement['text'], str) or not statement['text'].strip()
                    or len(statement['text']) > 10000 or '<' in statement['text'] or '>' in statement['text']):
                raise PanelError('Public preview fields require plain text and retained source references.')
            if any(not isinstance(statement[key], list) or (key == 'article_refs' and not statement[key])
                   or any(not isinstance(ref, str) or not ref.strip() or len(ref) > 200 for ref in statement[key])
                   for key in ('source_ids', 'article_refs')):
                raise PanelError('Retain valid source and article references.')
        return ok(invoke('save_preview', _identifier(slug), body, actor()))

    @bp.post('/api/membership/articles/<slug>/review')
    def review_preview(slug):
        body = _body(('expected_revision', 'payload_sha256', 'passed', 'note'), ('expected_revision', 'payload_sha256', 'passed'))
        _review_fields(body)
        return ok(invoke('review_preview', _identifier(slug), body, actor()))

    @bp.post('/api/membership/articles/<slug>/generate')
    def generate_preview(slug):
        body = _body(('expected_revision',), ('expected_revision',))
        if not isinstance(body['expected_revision'], str) or not body['expected_revision']:
            raise PanelError('Reload the current revision first.')
        return ok(invoke('generate_preview', _identifier(slug), body, actor()))

    @bp.get('/api/promotion/jobs')
    def jobs():
        return ok(invoke('list_jobs'))

    @bp.get('/api/promotion/briefings')
    def briefings():
        return ok(invoke('list_briefings'))

    @bp.get('/api/promotion/controls')
    def controls():
        return ok(invoke('get_controls'))

    @bp.put('/api/promotion/controls')
    def set_controls():
        body = _body(('scope', 'paused'), ('scope', 'paused'))
        if body['scope'] not in ('all', *PROMOTION_KINDS) or type(body['paused']) is not bool:
            raise PanelError('Choose a promotion scope and a pause or resume action.')
        return ok(invoke('set_controls', body, actor()))

    @bp.post('/api/promotion/jobs')
    def create_job():
        body = _body(('kind', 'slug', 'briefing_id', 'variant', 'channel'), ('kind',))
        if body['kind'] not in PROMOTION_KINDS:
            raise PanelError('Choose a supported promotion format.')
        if body.get('slug'):
            _identifier(body['slug'])
        for field in ('briefing_id', 'variant', 'channel'):
            if body.get(field):
                _identifier(body[field])
        if body['kind'] not in ('daily_briefing', 'daily_avatar') and not body.get('slug'):
            raise PanelError('Choose an article for this promotion.')
        if body['kind'] in ('daily_briefing', 'daily_avatar') and not body.get('briefing_id'):
            raise PanelError('Choose the dated briefing or source bundle for this daily job.')
        return ok(invoke('create_job', body, actor()))

    @bp.get('/api/promotion/jobs/<ident>')
    def job(ident):
        return ok(invoke('get_job', _identifier(ident)))

    @bp.post('/api/promotion/jobs/<ident>/<action>')
    def job_action(ident, action):
        if action not in ('generate', 'retry', 'cancel', 'review', 'edit', 'import'):
            raise PanelError('Choose a supported job action.')
        allowed = ('expected_version', 'data') if action in ('review', 'edit') else ('expected_version',)
        body = _body(allowed, ('expected_version',))
        _integer(body['expected_version'], 'Job version')
        if action == 'review':
            data = body.get('data')
            if (not isinstance(data, dict) or set(data) != {'decision', 'payload_sha256'}
                    or data['decision'] not in ('approved', 'rejected')
                    or not re.fullmatch(r'[a-f0-9]{64}', str(data['payload_sha256']))):
                raise PanelError('Review must match the exact generated job input hash.')
        if action == 'edit':
            data = body.get('data')
            if not isinstance(data, dict) or set(data) != {'script'} or not isinstance(data['script'], str) or not 1 <= len(data['script'].strip()) <= 10000:
                raise PanelError('Provide a plain script for a new immutable job revision.')
        return ok(invoke('job_action', _identifier(ident), action, body, actor()))

    @bp.get('/api/promotion/jobs/<ident>/artifacts/<name>')
    def artifact(ident, name):
        path, media = invoke('get_artifact', _identifier(ident), _identifier(name))
        path = Path(path)
        if not path.is_file() or path.is_symlink():
            raise PanelError('This saved artifact is unavailable.', 404)
        return send_file(str(path), mimetype=media, as_attachment=True, download_name=name, conditional=False, etag=False)

    app.register_blueprint(bp)


def _review_fields(body):
    if not isinstance(body.get('expected_revision'), str) or not body['expected_revision']:
        raise PanelError('Reload the saved revision before review.')
    if type(body.get('passed')) is not bool or not re.fullmatch(r'[a-f0-9]{64}', str(body.get('payload_sha256', ''))):
        raise PanelError('Review must match the exact saved content hash.')
    if 'note' in body and (not isinstance(body['note'], str) or len(body['note']) > 2000):
        raise PanelError('Keep the review note under 2,000 characters.')
