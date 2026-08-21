#!/usr/bin/env python3
"""
measure_angle_mix.py -- angle distribution WHEN A FRESH PEG EXISTS.

The COLLISION/TAILWIND trigger only differs from QUIET_EDGE in the
news_fresh case, so a peg-less scan cannot show the effect of changing it.
Every symbol is therefore analysed with a fresh, DIRECTIONLESS peg (dated
today) -- exactly what lane 2 hands the engine when research finds an event
but no sentiment. Direction then comes from price momentum, or nowhere.

Usage: measure_angle_mix.py [--anchor YYYY-MM-DD] SYM:RID [...]
"""
import sys, argparse, datetime, time, collections, json

sys.path.insert(0, '/home/flask/blog')
sys.path.insert(0, '/home/flask')
import angle_engine as ae


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('pairs', nargs='+')
    ap.add_argument('--anchor', default=datetime.date.today().isoformat())
    ap.add_argument('--label', default='run')
    ap.add_argument('--sleep', type=float, default=0.3)
    args = ap.parse_args()

    token = ae.login_appserver()
    rows = []
    print(f'{"symbol":<7}{"angle":<12}{"ctx":<7}{"1M%":>7}  story cell / rationale')
    print('-' * 88)
    for pair in args.pairs:
        sym, _, rid = pair.partition(':')
        sym, rid = sym.strip().upper(), (rid.strip() or '2')
        try:
            a = ae.analyze(rid, sym, args.anchor, token=token,
                           news_headline='(research peg)',
                           news_date=args.anchor,     # fresh, no direction
                           news_direction='')
        except Exception as e:
            print(f'{sym:<7}ERROR  {e}')
            continue
        card = a['card'] or {}
        angle = (card.get('angle') or {})
        name = angle.get('name') if isinstance(angle, dict) else (angle or 'no_story')
        ctx = a['ctx']
        story = card.get('story_cell') or {}
        rat = (angle.get('rationale') or '') if isinstance(angle, dict) else ''
        om = ctx.get('one_month_return')
        print(f'{sym:<7}{str(name):<12}{str(ctx.get("ctx_source")):<7}'
              f'{(f"{om:+.1f}" if om is not None else "  -"):>7}  '
              f'{story.get("horizon_tag","")}x{story.get("years","")} {rat[:44]}')
        rows.append({'symbol': sym, 'angle': name,
                     'ctx_source': ctx.get('ctx_source'),
                     'ctx_dir': ctx.get('ctx_dir'), 'one_month': om})
        time.sleep(args.sleep)

    print('-' * 88)
    dist = collections.Counter(r['angle'] for r in rows)
    total = len(rows)
    print(f'\nANGLE MIX with a fresh peg  (n={total})  [{args.label}]')
    for nm, c in dist.most_common():
        print(f'  {str(nm):<12}{c:>3}  {100*c/max(total,1):>5.1f}%  {"#"*int(36*c/max(total,1))}')
    with open(f'/tmp/angle_mix_{args.label}.json', 'w') as fh:
        json.dump(rows, fh, indent=2)
    print(f'\nwrote /tmp/angle_mix_{args.label}.json')


if __name__ == '__main__':
    main()
