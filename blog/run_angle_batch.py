#!/usr/bin/env python3
"""
run_angle_batch.py -- generate + publish angle-engine articles on DEV.

Reads a JSON job list so no shell quoting is involved:
  [{"symbol":"JNJ","rid":"2","headline":"","date":"","direction":""}, ...]

Usage:
  run_angle_batch.py jobs.json [--no-publish]
"""
import sys, json, datetime, traceback

sys.path.insert(0, '/home/flask/blog')
sys.path.insert(0, '/home/flask')

import angle_pipeline


def main():
    jobs_path = sys.argv[1]
    publish = '--no-publish' not in sys.argv
    jobs = json.load(open(jobs_path))
    anchor = datetime.date.today().isoformat()

    print('anchor=%s  publish=%s  jobs=%d\n' % (anchor, publish, len(jobs)))
    summary = []
    for j in jobs:
        sym = j['symbol']
        rid = str(j.get('rid', '2'))
        print('=' * 70)
        print('RUN %s (rid %s)  peg=%r' % (sym, rid, j.get('headline', '')[:60]))
        sys.stdout.flush()
        try:
            r = angle_pipeline.generate_angle_news_article(
                rid, sym,
                anchor=anchor,
                news_headline=j.get('headline', ''),
                news_date=j.get('date', ''),
                news_direction=j.get('direction', ''),
                publish=publish,
            )
        except Exception as e:
            traceback.print_exc()
            summary.append({'symbol': sym, 'status': 'exception', 'detail': str(e)})
            continue

        card = r.get('card') or {}
        angle = (card.get('angle') or {})
        angle_name = angle.get('name') if isinstance(angle, dict) else angle
        pub = r.get('publish_result') or {}
        row = {
            'symbol': sym,
            'status': r.get('status'),
            'angle': angle_name,
            'seo_title': r.get('seo_title', ''),
            'url': pub.get('url') or pub.get('web_url') or '',
            'publish_skipped': r.get('publish_skipped', ''),
            'detail': r.get('detail', ''),
            'seconds': r.get('duration_seconds'),
        }
        summary.append(row)
        print('-> %s' % json.dumps(row, indent=2))
        sys.stdout.flush()

    print('\n' + '=' * 70)
    print('SUMMARY')
    for row in summary:
        print(json.dumps(row))
    out = '/home/flask/blog/angle_out/batch_summary.json'
    try:
        json.dump(summary, open(out, 'w'), indent=2)
        print('\nwrote %s' % out)
    except Exception as e:
        print('could not write summary: %s' % e)


if __name__ == '__main__':
    main()
