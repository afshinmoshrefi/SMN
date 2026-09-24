#!/usr/bin/env python3
"""
pin_sweeper.py — the dashboard's timer job (pub_dashboard_sweep.timer, every
minute).

1. Runs scheduled publishes / unpublishes that are due.
2. Ends expired pins and rebuilds the news home page.
3. Finishes any background site refresh left pending.

Does nothing, and rebuilds nothing, when there is no work, so it is cheap to
run every minute.
"""

import sys

# Append, not insert: sibling files must win over any other copy.
for _extra in ("/home/flask", "/home/flask/blog"):
    if _extra not in sys.path:
        sys.path.append(_extra)

import pub_dashboard


def main() -> int:
    failed = False
    with pub_dashboard.app.test_request_context("/"):
        scheduled = pub_dashboard.run_due_schedules()
        for item in scheduled["ran"]:
            print(f"schedule {item['id']}: {item['action']} {item['slug']} -> "
                  f"{item['status']} {item.get('error', '')}".rstrip())
            failed |= item["status"] == "failed"

        result = pub_dashboard.sweep()
        if result["expired"]:
            print(f"expired pins: {[p.get('slug') for p in result['expired']]}  "
                  f"republished: {result['republished']}")
            # A schedule run already rebuilt the site; only rebuild if not.
            if not scheduled["refresh"]:
                rebuilt = pub_dashboard.rebuild_home()
                print(f"rebuild: ran={rebuilt.get('ran')} rc={rebuilt.get('returncode')}")
                failed |= not (rebuilt.get("ran") and rebuilt.get("returncode") == 0)
        # Finish a background site refresh the dashboard queued but could not
        # complete (e.g. its worker restarted).
        refreshed = False
        if pub_dashboard.REFRESH_FLAG.exists():
            refreshed = pub_dashboard.drain_refresh()
            print(f"pending site refresh: {'done' if refreshed else 'already running elsewhere'}")
        if not scheduled["ran"] and not result["expired"] and not refreshed:
            print("nothing due")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
