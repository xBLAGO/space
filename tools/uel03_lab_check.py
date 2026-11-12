"""Reproducible synthetic UEL 0.3 evidence without keys, payloads or sessions."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
import unittest


class EvidenceResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = []

    def addSuccess(self, test):
        super().addSuccess(test)
        self.records.append({'test': test.id(), 'expected': 'PASS', 'actual': 'PASS'})

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.records.append({'test': test.id(), 'expected': 'PASS', 'actual': 'FAIL'})

    def addError(self, test, err):
        super().addError(test, err)
        self.records.append({'test': test.id(), 'expected': 'PASS', 'actual': 'ERROR'})

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.records.append({'test': test.id(), 'expected': 'PASS', 'actual': 'NOT EXECUTED'})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--full', action='store_true')
    parser.add_argument('--repeat', type=int, choices=(1, 2), default=1)
    args = parser.parse_args()
    attempts = []
    successful = True
    for number in range(1, args.repeat + 1):
        suite = (unittest.defaultTestLoader.discover('tests') if args.full else
                 unittest.defaultTestLoader.loadTestsFromNames(['tests.test_uel_checkpoint', 'tests.test_uel_checkpoint_vectors']))
        wall_start, start = time.time(), time.perf_counter()
        runner = unittest.TextTestRunner(verbosity=2, resultclass=EvidenceResult)
        result = runner.run(suite)
        attempts.append({'attempt': number, 'executed': result.testsRun, 'errors': len(result.errors),
                         'failures': len(result.failures), 'skipped': len(result.skipped),
                         'wall_seconds': round(time.time() - wall_start, 3),
                         'elapsed_seconds': round(time.perf_counter() - start, 3),
                         'status': 'PASS' if result.wasSuccessful() and not result.skipped else 'FAIL',
                         'records': result.records})
        successful = successful and result.wasSuccessful() and not result.skipped
    evidence = {'format': 'uel03-local-lab-evidence-1', 'base': 'aa92d96bca0ae8a07e27f8efa724131afb32ad57',
                'checkout_sha': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
                'mode': 'SYNTHETIC_LOCAL_ONLY', 'status': 'PASS' if successful else 'FAIL',
                'scope': 'full regression' if args.full else 'checkpoint and offline verifier',
                'attempts': attempts, 'independent_operator_test': 'NOT EXECUTED',
                'source_truth_or_human_signature_proven': False, 'real_users': 'NO-GO',
                'production_changed': False}
    args.output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
    sys.exit(0 if successful else 1)


if __name__ == '__main__':
    main()
