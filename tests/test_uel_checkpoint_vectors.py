"""Same-author reference validators, deterministic offline receipt examples."""
import copy
import hashlib
import json
from pathlib import Path
import unittest
import subprocess
import sys
import tempfile

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.exceptions import InvalidSignature
from xblago.core.security_journal import JournalUnavailable
from xblago.core import uel_checkpoint as runtime
from tools import uel_checkpoint_verify as offline


def vectors():
    # Public synthetic vector key only. Runtime enrollment generates fresh keys.
    key = Ed25519PrivateKey.from_private_bytes(hashlib.sha256(b'UEL03-OFFLINE-VECTORS-ONLY').digest())
    stream = hashlib.sha256(b'UEL03-OFFLINE-STREAM-ONLY').hexdigest()
    chain = []
    previous = runtime.ZERO
    for generation, count in enumerate((0, 1, 3)):
        body = {'profile': runtime.PROFILE, 'stream': stream, 'generation': generation, 'previous': previous,
                'journal_seq': count, 'journal_root': runtime.root(['1' * 64] * count),
                'uel_seq': min(count, 1), 'uel_root': runtime.root(['2' * 64] * min(count, 1))}
        receipt = runtime.signed(body, key, 'checkpoint')
        previous = runtime.digest(runtime.encode(receipt))
        chain.append(receipt)
    cases = [{'id': 'C01', 'expected': 'OFFLINE_INTEGRITY_ONLY', 'receipts': chain}]
    changed = copy.deepcopy(chain)
    changed[-1]['body']['journal_root'] = '3' * 64
    cases.append({'id': 'C02', 'expected': 'REJECT', 'receipts': changed})
    cases.append({'id': 'C03', 'expected': 'REJECT', 'receipts': chain[:-1]})
    cases.append({'id': 'C04', 'expected': 'REJECT', 'receipts': [chain[0], chain[2]]})
    cases.append({'id': 'C05', 'expected': 'REJECT', 'receipts': chain[::-1]})
    return {'profile': runtime.PROFILE, 'synthetic_public_key': runtime.public(key),
            'stream': stream, 'pinned_tip': previous, 'cases': cases}


class VectorTests(unittest.TestCase):
    def test_frozen_checkpoint_vectors_and_both_validators(self):
        fixture = vectors()
        path = Path(__file__).resolve().parents[1] / 'docs/uel03/checkpoint-vectors.json'
        self.assertEqual(json.loads(path.read_text()), fixture)
        for case in fixture['cases']:
            with self.subTest(case=case['id']):
                if case['expected'] == 'REJECT':
                    with self.assertRaises((ValueError, InvalidSignature, JournalUnavailable)):
                        offline.verify(case['receipts'], fixture['synthetic_public_key'], fixture['stream'], fixture['pinned_tip'])
                    # A strict prefix is internally authentic but stale relative
                    # to the pinned tip. Runtime freshness is nonce-bound online.
                    if case['id'] != 'C03':
                        with self.assertRaises((ValueError, InvalidSignature, JournalUnavailable)):
                            runtime.verify_chain(case['receipts'], fixture['synthetic_public_key'], fixture['stream'])
                else:
                    result = offline.verify(case['receipts'], fixture['synthetic_public_key'], fixture['stream'], fixture['pinned_tip'])
                    self.assertEqual(result['status'], case['expected'])
                    self.assertEqual(result['uel_seq'], runtime.verify_chain(case['receipts'], fixture['synthetic_public_key'], fixture['stream'])['uel_seq'])

    def test_offline_current_tip_is_mandatory(self):
        fixture = vectors()
        with self.assertRaises(ValueError):
            offline.verify(fixture['cases'][0]['receipts'], fixture['synthetic_public_key'], fixture['stream'], '')

    def test_public_key_substitution_fails_both(self):
        fixture = vectors()
        wrong = runtime.public(Ed25519PrivateKey.generate())
        for verify in (
            lambda: runtime.verify_chain(fixture['cases'][0]['receipts'], wrong, fixture['stream']),
            lambda: offline.verify(fixture['cases'][0]['receipts'], wrong, fixture['stream'], fixture['pinned_tip']),
        ):
            with self.assertRaises((ValueError, InvalidSignature, JournalUnavailable)):
                verify()

    def test_standalone_process_without_application_imports(self):
        fixture = vectors()
        with tempfile.TemporaryDirectory() as directory:
            proof = Path(directory) / 'proof.json'
            proof.write_bytes(offline.wire(fixture['cases'][0]['receipts']))
            result = subprocess.run(
                [sys.executable, str(Path(offline.__file__)), str(proof),
                 '--key', fixture['synthetic_public_key'], '--stream', fixture['stream'],
                 '--tip', fixture['pinned_tip']], check=True, capture_output=True, text=True)
            self.assertEqual(json.loads(result.stdout)['status'], 'OFFLINE_INTEGRITY_ONLY')
