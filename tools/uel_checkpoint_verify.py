"""Standalone offline Ed25519 checkpoint-chain verifier.

No application imports, private keys, database, sessions or network access.
An authenticated current pinned frontier is required to detect omitted suffixes.
"""
import argparse
import hashlib
import json
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

PROFILE = 'uel-checkpoint-0.3-lab.1'
ZERO = '0' * 64


def wire(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def verify(receipts, pinned_key, stream, expected_tip):
    if type(receipts) is not list or not 1 <= len(receipts) <= 65537:
        raise ValueError('Invalid proof size')
    if len(pinned_key) != 64 or len(stream) != 64 or len(expected_tip) != 64:
        raise ValueError('Pinned evidence required')
    key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(pinned_key))
    previous = ZERO
    last = None
    for generation, receipt in enumerate(receipts):
        if type(receipt) is not dict or set(receipt) != {'body', 'signature'}:
            raise ValueError('Invalid receipt')
        body, signature = receipt['body'], receipt['signature']
        if type(signature) is not str or len(signature) != 128 or signature != signature.lower():
            raise ValueError('Invalid signature encoding')
        key.verify(bytes.fromhex(signature), (PROFILE + '/checkpoint\0').encode() + wire(body))
        if type(body) is not dict or set(body) != {
                'profile', 'stream', 'generation', 'previous', 'journal_seq', 'journal_root', 'uel_seq', 'uel_root'}:
            raise ValueError('Invalid body')
        if body['profile'] != PROFILE or body['stream'] != stream or body['generation'] != generation or body['previous'] != previous:
            raise ValueError('Invalid checkpoint chain')
        for field in ('generation', 'journal_seq', 'uel_seq'):
            if type(body[field]) is not int or not 0 <= body[field] <= 65536:
                raise ValueError('Invalid counter')
        for field in ('previous', 'journal_root', 'uel_root'):
            value = body[field]
            if type(value) is not str or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
                raise ValueError('Invalid root')
        if body['uel_seq'] > body['journal_seq']:
            raise ValueError('Invalid UEL count')
        if last and (body['journal_seq'] <= last['journal_seq'] or body['uel_seq'] < last['uel_seq']
                     or (body['uel_seq'] == last['uel_seq'] and body['uel_root'] != last['uel_root'])):
            raise ValueError('Non-monotonic checkpoint')
        if generation == 0 and (body['journal_seq'] or body['uel_seq'] or body['journal_root'] != ZERO or body['uel_root'] != ZERO):
            raise ValueError('Invalid genesis')
        previous = hashlib.sha256(wire(receipt)).hexdigest()
        last = body
    if previous != expected_tip:
        raise ValueError('Missing, stale or substituted pinned tip')
    return {'status': 'OFFLINE_INTEGRITY_ONLY', 'checkpoints': len(receipts),
            'journal_seq': last['journal_seq'], 'uel_seq': last['uel_seq'], 'tip': previous,
            'authority_or_source_truth_proven': False, 'current_freshness_proven': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('proof', type=Path)
    parser.add_argument('--key', required=True)
    parser.add_argument('--stream', required=True)
    parser.add_argument('--tip', required=True)
    args = parser.parse_args()
    if args.proof.stat().st_size > 10 * 1024 * 1024:
        parser.error('Proof exceeds bound')
    raw = args.proof.read_bytes()
    receipts = json.loads(raw)
    if wire(receipts) != raw:
        parser.error('Canonical encoding required')
    print(json.dumps(verify(receipts, args.key, args.stream, args.tip), sort_keys=True))


if __name__ == '__main__':
    main()
