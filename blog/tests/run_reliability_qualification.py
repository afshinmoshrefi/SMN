"""Focused offline qualification; refuses any attempted network connection.

Run with Python 3.10+ from any directory. Windows substitutes fcntl only for
single-process transaction tests; this never certifies Linux crash/lock behavior.
"""
import argparse
from contextlib import ExitStack, redirect_stdout
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import socket
import sys
import types
import unittest
from unittest.mock import patch

TEST_MODULES = (
    'test_production_continuity', 'test_production_continuity_schedule',
    'test_smn_reliability_regressions', 'test_smn_operational_alerts',
    'test_smn_daily_retry', 'test_primary_dev_publish',
    'test_verified_newsletter', 'test_smn_newsletter_state',
    'test_editorial_gate', 'test_editorial_evidence_guards',
    'test_subscription_publication', 'test_selected_publication',
    'test_smn_self_recovery','test_subscription_inputs',
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    tests = Path(__file__).resolve().parent
    sys.path[:0] = [str(tests.parent), str(tests)]
    attempts = []
    provider_attempts = []
    def refuse(*_args, **_kwargs):
        attempts.append('network connection attempted')
        raise AssertionError('Offline qualification forbids network connections')
    def refuse_provider(*_args, **_kwargs):
        provider_attempts.append('unmocked model dispatch attempted')
        raise AssertionError('Offline qualification forbids model dispatch')
    modules = []
    with ExitStack() as stack:
        if os.name == 'nt':
            fake = types.SimpleNamespace(LOCK_EX=1, LOCK_NB=2, LOCK_UN=4, flock=lambda *_: None)
            stack.enter_context(patch.dict(sys.modules, {'fcntl': fake}))
        stack.enter_context(patch.object(socket.socket, 'connect', refuse))
        stack.enter_context(patch.object(socket, 'create_connection', refuse))
        stack.enter_context(patch.object(socket, 'getaddrinfo', refuse))
        import smn_models
        stack.enter_context(patch.object(smn_models, 'run', refuse_provider))
        with (args.output/'tests.log').open('w', encoding='utf-8') as log, redirect_stdout(log):
            for name in TEST_MODULES:
                suite = unittest.defaultTestLoader.loadTestsFromName(name)
                result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
                modules.append({'module': name, 'tests': result.testsRun,
                                'failures': len(result.failures), 'errors': len(result.errors),
                                'skipped': len(result.skipped), 'passed': result.wasSuccessful()})
    record = {'utc': datetime.now(timezone.utc).isoformat(), 'platform': platform.platform(),
              'python': platform.python_version(), 'windows_fcntl_stub': os.name == 'nt',
              'network_attempts': len(attempts), 'unmocked_model_attempts': len(provider_attempts),
              'modules': modules,
              'tests': sum(row['tests'] for row in modules),
              'passed': all(row['passed'] for row in modules) and not attempts and not provider_attempts}
    (args.output/'tests.json').write_text(json.dumps(record, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({key: record[key] for key in ('tests', 'passed', 'network_attempts', 'windows_fcntl_stub')}))
    return 0 if record['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
