"""Run pending private promotion work once; systemd owns repetition and timeout."""
import json

import membership_runtime as runtime
import promotion_jobs
import promotion_worker


def run():
    configuration = runtime._configuration()
    results = []
    for job in reversed(promotion_jobs.list_jobs(runtime.jobs_root())):
        if (job['status'] == 'draft' or
                (job['kind'] == 'daily_avatar' and job['status'] == 'running' and job['stage'] == 'avatar_provider')):
            try:
                result = promotion_worker.run_one(runtime.jobs_root(), job['id'], configuration)
                results.append({k: result.get(k) for k in ('id', 'kind', 'status', 'holds')})
            except promotion_jobs.Conflict:
                continue
    return results


if __name__ == '__main__':
    print(json.dumps(run()))
