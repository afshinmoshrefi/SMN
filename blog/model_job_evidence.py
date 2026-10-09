"""Conservative dispatch accounting and immutable completed-job verification.

No provider calls. Prepared job directories reserve one attempt; each archived
retry reserves another, including transient and authentication failures.
"""
from pathlib import Path

from subscription_writer import load_json

ATTEMPT_PREFIXES = ('failed-attempt-', 'transient-attempt-', 'authentication-retry-')
DAILY_JOB_LIMIT = 60


def jobs_used(root, date=None):
    from daily_budget import jobs_used as shared_jobs_used
    return shared_jobs_used(root, date)


def completed_receipt(job):
    """Reuse successful output only while its input/output/provenance still bind.

    Assignment expiry controls new dispatch, not retained successful evidence.
    No failed or stale receipt is rewritten into a passing one.
    """
    job = Path(job)
    receipt = load_json(job/'receipt.json')
    manifest = load_json(job/'job.json')
    if (receipt.get('status') != 'output_ready_for_smn_validation' or
            receipt.get('provider') != manifest.get('provider', 'openai') or
            receipt.get('publish') is not False or manifest.get('publish') is not False):
        raise ValueError('Completed model receipt provenance changed')
    # Reuse the existing source/receipt/schema/assignment-time gate.
    from editorial_gate import completed_job
    completed_job(job)
    return receipt
