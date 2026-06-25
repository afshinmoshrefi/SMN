import requests
import time

APPSERVER_URL = 'https://app1pp.trxstat.com'


def try_login(wp_userid, call_timeout=30):
    print('========================================')
    print('TRYING wp_userid=%s' % wp_userid)
    print('========================================')

    url1 = '%s/login/%s/7/4/5/6' % (APPSERVER_URL, wp_userid)
    t0 = time.time()
    try:
        r1 = requests.get(url1, timeout=call_timeout)
    except Exception as e:
        print('CALL 1 EXCEPTION after %.1fs: %s' % (time.time() - t0, e))
        return
    print('CALL 1 status=%s elapsed=%.2fs' % (r1.status_code, time.time() - t0))
    print('CALL 1 body=%s' % repr(r1.text[:300]))

    try:
        kp_token = r1.json()['message'].split(' ')[4]
    except Exception as e:
        print('FAILED to parse kp_token: %s' % e)
        return
    print('kp_token=%s' % kp_token)

    url2 = '%s/login/%s/7/4/5/%s' % (APPSERVER_URL, wp_userid, kp_token)
    t0 = time.time()
    try:
        r2 = requests.get(url2, timeout=call_timeout)
    except Exception as e:
        print('CALL 2 EXCEPTION after %.1fs: %s' % (time.time() - t0, e))
        return
    print('CALL 2 status=%s elapsed=%.2fs' % (r2.status_code, time.time() - t0))
    print('CALL 2 body=%s' % repr(r2.text[:500]))


# wp_userid=0 short-circuits BEFORE the WordPress/IHC call in get_user_levels
try_login('0')
print()
# wp_userid=16 hits the WordPress/IHC call
try_login('16')
