"""Run pending private promotion work once; systemd owns repetition and timeout."""
import json
from contextlib import ExitStack

import membership_runtime as runtime
import promotion_jobs
import promotion_worker


def run():
    with ExitStack() as stack:
        try:
            stack.enter_context(promotion_jobs.locked(runtime.jobs_root(), name='.daemon.lock', timeout=0))
        except promotion_jobs.Conflict:
            return []  # Another daemon owns provider work; do not reconcile its live claims.
        return _run_claimed()


def _run_claimed():
    with runtime.article_index.posts_lock():
        runtime.publication.recover()
        runtime.recover_operations()
        runtime.reconcile_article_media()
    configuration = runtime._configuration()
    results = []
    for job in reversed(promotion_jobs.list_jobs(runtime.jobs_root())):
        if job['status'] == 'running' and not (job['kind'] == 'daily_avatar' and job['stage'] == 'avatar_provider'):
            try:
                result = promotion_jobs.update(runtime.jobs_root(), job['id'], job['version'],
                    status='held', generation_status='unknown_outcome',
                    holds=['Interrupted worker: inspect the existing provider and credit receipts before reconciliation.'])
                results.append({k: result.get(k) for k in ('id', 'kind', 'status', 'holds')})
            except promotion_jobs.Conflict:
                pass
            continue
        if (job['status'] in {'draft', 'queued'} or
                (job['kind'] == 'daily_avatar' and job['status'] == 'running' and job['stage'] == 'avatar_provider')):
            try:
                result = promotion_worker.run_one(runtime.jobs_root(), job['id'], configuration)
                results.append({k: result.get(k) for k in ('id', 'kind', 'status', 'holds')})
            except promotion_jobs.Conflict:
                continue
    return results


if __name__ == '__main__':
    print(json.dumps(run()))
