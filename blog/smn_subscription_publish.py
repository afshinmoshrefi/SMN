"""Production adapter for the existing snapshot/activate/verify/rollback publisher.

The human-installed activation record is required by configure_production().
Claude comparison roots are never accepted by the publication package builder.
"""
from pathlib import Path
import subprocess
import sys

from subscription_publication import package, read, write
import install_smn_primary_edition as installer

ORIGIN = 'https://seasonalmarketnews.com'


def publish_edition(root, date):
    root = Path(root).resolve()
    installer.configure_production()
    installer.guard()
    receipt_path = root/'production-publication-receipt.json'
    if receipt_path.exists():
        receipt = read(receipt_path)
        if receipt.get('status') != 'live_verified':
            raise ValueError('Production receipt is not live verified')
        return receipt
    state = read(root/'smn-daily-state.json')
    stages = {symbol: row['review_stage'] for symbol, row in state['articles'].items()
              if row.get('finalized') and not row.get('held')}
    repo = Path(__file__).resolve().parent.parent
    commit = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
    if subprocess.check_output(['git', '-C', str(repo), 'status', '--porcelain', '--untracked-files=no'], text=True).strip():
        raise ValueError('Publisher requires unchanged committed source')
    if not (root/'publication-package/manifest.json').exists():
        package(root, date, commit, stages, target_origin=ORIGIN)
    from subscription_publication import validate_staged_reviews
    validate_staged_reviews(root)
    stage_path = root/'production-stage.json'
    if not stage_path.exists():
        prepared = installer.prepare(root/'publication-package')
        write(stage_path, prepared)
    prepared = read(stage_path)
    if prepared['source_commit'] != commit:
        raise ValueError('Prepared edition source differs from active release')
    record = Path(prepared['record'])
    current = read(record/'receipt.json')
    if current['status'] == 'live_verified':
        current['production_written'] = True
        write(receipt_path, current)
        return current
    if current['status'] == 'rolled_back':
        raise ValueError('Previous publication rolled back; inspect retained record before reactivation: '+str(record))
    if current['status'] == 'prepared':
        current = installer.activate(record)
    write(root/'primary-activation.json', current)
    if current['status'] != 'active_pending_live_verification':
        raise ValueError('Unexpected publication state; inspect '+str(record))
    try:
        subprocess.run(['node', str(repo/'blog/subscription_primary_live.cjs'), str(root)], check=True)
        from smn_daily import Day, CLIS
        from smn_visual import landing
        day = Day(root, date, profile='chatgpt', roles=state['roles'], publication_origin=ORIGIN)
        check = landing(root, date, state['roles'], CLIS, day.run_job)
        if not check.get('passed'):
            raise ValueError('Live landing visual review failed')
        proof = read(root/'live-verification.json')
        if proof.get('origin') != ORIGIN:
            raise ValueError('Live proof has wrong origin')
        write(record/'live-verification.json', proof)
        receipt = installer.finish(record)
        receipt['production_written'] = True
        write(receipt_path, receipt)
        return receipt
    except BaseException:
        installer.rollback(record)
        raise


if __name__ == '__main__':
    if len(sys.argv) != 3:
        raise SystemExit('usage: smn_subscription_publish.py EDITION_ROOT YYYY-MM-DD')
    print(publish_edition(Path(sys.argv[1]), sys.argv[2]))
