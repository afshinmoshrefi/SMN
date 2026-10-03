"""Offline ElevenLabs readiness plan. Never submits a generation request."""
import argparse
import json

from daily_briefing import digest, inspect
from subscription_writer import load_json

DOCS = {
    'avatars': 'https://elevenlabs.io/docs/overview/capabilities/image-video/avatars',
    'video_api': 'https://elevenlabs.io/docs/api-reference/flows/video/create',
}


def plan(briefing, configuration, review=None):
    validation = inspect(briefing, review)
    holds = list(validation['issues'])
    if validation['review_status'] != 'approved':
        holds.append('editorial review is pending')
    if configuration.get('provider') != 'elevenlabs':
        holds.append('selected provider must be ElevenLabs')
    for name in ('account_access_verified', 'voice_access_verified', 'likeness_verified',
                 'model_access_verified', 'route_verified', 'cost_verified', 'generation_authorized'):
        if configuration.get(name) is not True:
            holds.append(name + ' is unverified or not authorized')
    for name in ('voice_id', 'presenter_reference', 'model_id', 'settings', 'verification_receipt'):
        if not configuration.get(name):
            holds.append(name + ' is missing')
    route = configuration.get('route')
    if route not in {'manual_avatar', 'flows_video_api'}:
        holds.append('exact account generation route must be selected')
    budget = configuration.get('max_credits')
    if type(budget) not in (int, float) or not 0 < budget < float('inf'):
        holds.append('positive finite maximum credit budget is required')
    try:
        configuration_hash = digest(configuration)
    except ValueError:
        configuration_hash = None
        holds.append('configuration contains non-finite values')
    return {'status': 'held' if holds else 'offline_plan_ready', 'holds': holds,
            'briefing_sha256': validation.get('briefing_sha256'),
            'configuration_sha256': configuration_hash,
            'provider': 'elevenlabs', 'route': route,
            'generation_status': 'not_submitted', 'network_calls': 0,
            'automated_generation_qualified': False,
            'manual_steps': ['Select verified personal avatar/style and existing voice in ElevenLabs']
                if route == 'manual_avatar' else [],
            'steps': [{'stage': 'speech', 'text': ' '.join(b['text'] for b in briefing.get('script', [])),
                       'voice_id': configuration.get('voice_id')},
                      {'stage': 'presenter', 'model_id': configuration.get('model_id'),
                       'reference': configuration.get('presenter_reference'),
                       'candidate_endpoint': '/v1/flows/video' if route == 'flows_video_api' else None,
                       'submission_payload': None},
                      {'stage': 'compose_and_review', 'required': ['captions', 'AI presenter disclosure',
                       'full listening', 'likeness and lip sync', 'phone readability', 'provider receipt']}],
            'documentation': DOCS,
            'limitation': 'Reusable avatar API and individual video-model API are distinct. '
                'Operator verification is not live account verification; this module never generates media.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True)
    parser.add_argument('--config', required=True)
    parser.add_argument('--review')
    args = parser.parse_args()
    result = plan(load_json(args.input), load_json(args.config), load_json(args.review) if args.review else None)
    print(json.dumps(result, indent=2))
    return 1 if result['holds'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
