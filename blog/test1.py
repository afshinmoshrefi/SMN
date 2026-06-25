import sys, json, requests
sys.path.insert(0, '/home/flask')
sys.path.insert(0, '/home/flask/blog')
import config
from m_daily_ai_pick_social import publer_headers

# Try a text-only post (no image) to see if that works
body = {
    'bulk': {
        'state': 'scheduled',
        'posts': [{
            'networks': {
                'twitter': {
                    'type': 'status',
                    'text': 'Production test - will delete shortly.',
                }
            },
            'accounts': [{'id': str(config.PUBLER_X_ACCOUNT_ID)}],
        }]
    }
}
r = requests.post('https://app.publer.com/api/v1/posts/schedule/publish', headers=publer_headers(json_ct=True),json=body, timeout=60)
print('Status:', r.status_code)
print(json.dumps(r.json(), indent=2))
import time; time.sleep(10)
job_id = r.json().get('job_id')
if job_id:
    r2 = requests.get('https://app.publer.com/api/v1/job_status/%s' % job_id, headers=publer_headers(json_ct=False), timeout=30)
    print(json.dumps(r2.json(), indent=2))