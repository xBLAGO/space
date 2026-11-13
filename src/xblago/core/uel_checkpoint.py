"""Private synthetic checkpoint custody with separate Ed25519 keys and storage.

The socket adapter is a local laboratory transport. Independent operators must
retain the custodian store and pinned configuration outside the rollback domain.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import socketserver
import sqlite3
import struct

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, PublicFormat, NoEncryption

from xblago.core.security_journal import JournalUnavailable, canonical
from xblago.core.sqlite_safety import require_safe_sqlite, UnsafeSQLite

PROFILE = 'uel-checkpoint-0.3-lab.1'
ZERO = '0' * 64
LIMIT = 65536
MAX_BYTES = 10 * 1024 * 1024
DOMAINS = {name: (PROFILE + '/' + name + '\0').encode() for name in ('request', 'checkpoint', 'reply')}
DDL = (
    'CREATE TABLE metadata (singleton INTEGER PRIMARY KEY CHECK(singleton=1), stream TEXT NOT NULL, source_key TEXT NOT NULL)',
    'CREATE TABLE checkpoints (generation INTEGER PRIMARY KEY, receipt TEXT NOT NULL)',
)


def fail():
    raise JournalUnavailable('UEL checkpoint UNKNOWN_RECOVERY')


def digest(value):
    return hashlib.sha256(value).hexdigest()


def hex64(value):
    return type(value) is str and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def encode(value):
    return canonical(value).encode()


def decode(value):
    if len(value) > MAX_BYTES:
        fail()
    try:
        result = json.loads(value)
        if encode(result) != value:
            fail()
        return result
    except (ValueError, TypeError, RecursionError) as exc:
        raise JournalUnavailable('Invalid checkpoint encoding') from exc


def public(key):
    return key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()


def create_key(path):
    key = Ed25519PrivateKey.generate()
    data = key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    from xblago.core.journal_custody import _new_file
    _new_file(Path(path), data)
    from xblago.core.journal_witness import _sync_dir
    _sync_dir(Path(path).parent)
    return public(key)


def load_key(path):
    path = Path(path)
    if path.is_symlink() or path.stat().st_mode & 0o077:
        fail()
    return Ed25519PrivateKey.from_private_bytes(path.read_bytes())


def signed(body, key, domain):
    return {'body': body, 'signature': key.sign(DOMAINS[domain] + encode(body)).hex()}


def verify_signed(value, key, domain):
    try:
        if type(value) is not dict or set(value) != {'body', 'signature'} or type(value['signature']) is not str:
            fail()
        signature = bytes.fromhex(value['signature'])
        if len(signature) != 64 or len(value['signature']) != 128 or any(c not in '0123456789abcdef' for c in value['signature']) or not hex64(key):
            fail()
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(key)).verify(
            signature, DOMAINS[domain] + encode(value['body']))
        return value['body']
    except (InvalidSignature, ValueError, TypeError, KeyError) as exc:
        raise JournalUnavailable('Checkpoint signature invalid') from exc


def root(leaves):
    if type(leaves) is not list or len(leaves) > LIMIT:
        fail()
    head = ZERO
    for leaf in leaves:
        if not hex64(leaf):
            fail()
        head = digest((PROFILE + '/chain\0').encode() + bytes.fromhex(head) + bytes.fromhex(leaf))
    return head


def verify_checkpoint(receipt, pinned_key, stream):
    body = verify_signed(receipt, pinned_key, 'checkpoint')
    fields = {'profile', 'stream', 'generation', 'previous', 'journal_seq', 'journal_root', 'uel_seq', 'uel_root'}
    if type(body) is not dict or set(body) != fields or body['profile'] != PROFILE or body['stream'] != stream:
        fail()
    for field in ('generation', 'journal_seq', 'uel_seq'):
        if type(body[field]) is not int or not 0 <= body[field] <= LIMIT:
            fail()
    for field in ('previous', 'journal_root', 'uel_root'):
        if not hex64(body[field]):
            fail()
    if body['uel_seq'] > body['journal_seq']:
        fail()
    return body


def verify_chain(receipts, pinned_key, stream):
    """Offline integrity only. Current freshness requires a nonce-bound reply."""
    if type(receipts) is not list or not receipts or len(receipts) > LIMIT + 1:
        fail()
    previous = ZERO
    prior = None
    for generation, receipt in enumerate(receipts):
        body = verify_checkpoint(receipt, pinned_key, stream)
        if body['generation'] != generation or body['previous'] != previous:
            fail()
        if prior and (body['journal_seq'] <= prior['journal_seq'] or body['uel_seq'] < prior['uel_seq']):
            fail()
        if prior and body['uel_seq'] == prior['uel_seq'] and body['uel_root'] != prior['uel_root']:
            fail()
        if generation == 0 and (body['journal_seq'] or body['uel_seq'] or body['journal_root'] != ZERO or body['uel_root'] != ZERO):
            fail()
        previous = digest(encode(receipt))
        prior = body
    return prior




def _receipts(db):
    result = []
    for expected, (generation, encoded) in enumerate(db.execute('SELECT generation,receipt FROM checkpoints ORDER BY generation')):
        if generation != expected or expected > LIMIT:
            fail()
        result.append(decode(encoded.encode()))
    return result


def _publish_head(directory, receipt):
    from xblago.core.journal_custody import _new_file
    from xblago.core.journal_witness import _sync_dir
    temporary = directory / ('.head-' + secrets.token_hex(16))
    try:
        _new_file(temporary, encode(receipt))
        os.replace(temporary, directory / 'head.json')
        _sync_dir(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _verify_head(directory, receipts, key, stream):
    path = directory / 'head.json'
    if path.is_symlink() or path.stat().st_mode & 0o077 or path.stat().st_size > 4096:
        fail()
    receipt = decode(path.read_bytes())
    head = verify_checkpoint(receipt, key, stream)
    if head['generation'] >= len(receipts) or receipt != receipts[head['generation']]:
        fail()


def initialize(directory, stream, source_key):
    """Explicit custodian enrollment. A missing store is never auto-initialized."""
    from xblago.core.uel_schema import require_synthetic
    from xblago.core.journal_witness import _sync_dir
    require_synthetic()
    require_safe_sqlite()
    if not hex64(stream) or not hex64(source_key):
        fail()
    directory = Path(directory)
    directory.mkdir(mode=0o700, exist_ok=False)
    create_key(directory / 'signing.key')
    key = load_key(directory / 'signing.key')
    genesis = signed({'profile': PROFILE, 'stream': stream, 'generation': 0, 'previous': ZERO,
                      'journal_seq': 0, 'journal_root': ZERO, 'uel_seq': 0, 'uel_root': ZERO}, key, 'checkpoint')
    path = directory / 'custody.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA synchronous=FULL')
        for statement in DDL:
            db.execute(statement)
        db.execute('INSERT INTO metadata VALUES (1,?,?)', (stream, source_key))
        db.execute('INSERT INTO checkpoints VALUES (0,?)', (canonical(genesis),))
    os.chmod(path, 0o600)
    from xblago.core.journal_custody import _new_file
    _new_file(directory / 'head.json', encode(genesis))
    _sync_dir(directory)
    _sync_dir(directory.parent)
    return public(key)


@contextmanager
def _locked(directory):
    directory = Path(directory)
    if directory.is_symlink() or directory.stat().st_mode & 0o077:
        fail()
    fd = os.open(directory / 'custody.lock', os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def accept(directory, request):
    from xblago.core.uel_schema import require_synthetic
    require_synthetic()
    require_safe_sqlite()
    # Serialize commit and publication of the retained head across processes.
    with _locked(directory):
        return _accept(directory, request)


def _accept(directory, request):
    """Serialize prefix verification, durable checkpoint commit and signed reply."""
    from xblago.core.uel_schema import require_synthetic
    require_synthetic()
    require_safe_sqlite()
    directory = Path(directory)
    path = directory / 'custody.sqlite3'
    if directory.is_symlink() or directory.stat().st_mode & 0o077 or path.is_symlink() or path.stat().st_mode & 0o077:
        fail()
    key = load_key(directory / 'signing.key')
    with sqlite3.connect(path.resolve().as_uri() + '?mode=rw', uri=True, timeout=10) as db:
        db.execute('PRAGMA synchronous=FULL')
        db.execute('BEGIN IMMEDIATE')
        actual = dict(db.execute("SELECT name,sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'"))
        if actual != {s.split()[2]: s for s in DDL} or db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            fail()
        metadata = db.execute('SELECT stream,source_key FROM metadata WHERE singleton=1').fetchone()
        if metadata is None:
            fail()
        stream, source_key = metadata
        body = verify_signed(request, source_key, 'request')
        if type(body) is not dict or set(body) != {'profile', 'stream', 'nonce', 'journal', 'uel'}:
            fail()
        if body['profile'] != PROFILE or body['stream'] != stream or not hex64(body['nonce']):
            fail()
        journal_root, uel_root = root(body['journal']), root(body['uel'])
        receipts = _receipts(db)
        prior = verify_chain(receipts, public(key), stream)
        _verify_head(directory, receipts, public(key), stream)
        if len(body['journal']) < prior['journal_seq'] or len(body['uel']) < prior['uel_seq']:
            fail()
        if root(body['journal'][:prior['journal_seq']]) != prior['journal_root'] or root(body['uel'][:prior['uel_seq']]) != prior['uel_root']:
            fail()
        if len(body['uel']) > len(body['journal']):
            fail()
        receipt = receipts[-1]
        if len(body['journal']) > prior['journal_seq']:
            checkpoint = {'profile': PROFILE, 'stream': stream, 'generation': prior['generation'] + 1,
                          'previous': digest(encode(receipt)), 'journal_seq': len(body['journal']),
                          'journal_root': journal_root, 'uel_seq': len(body['uel']), 'uel_root': uel_root}
            receipt = signed(checkpoint, key, 'checkpoint')
            db.execute('INSERT INTO checkpoints VALUES (?,?)', (checkpoint['generation'], canonical(receipt)))
        elif len(body['uel']) != prior['uel_seq']:
            fail()
        db.commit()
        _publish_head(directory, receipt)
        return signed({'profile': PROFILE, 'stream': stream, 'nonce': body['nonce'],
                       'request_hash': digest(encode(request)), 'checkpoint': receipt,
                       'status': 'COMMITTED'}, key, 'reply')


def _read(stream, size):
    parts = bytearray()
    while len(parts) < size:
        data = stream.recv(size - len(parts))
        if not data:
            fail()
        parts.extend(data)
    return bytes(parts)


def _receive(stream):
    size = struct.unpack('!I', _read(stream, 4))[0]
    if size > MAX_BYTES:
        fail()
    return _read(stream, size)


def _send(stream, value):
    if len(value) > MAX_BYTES:
        fail()
    stream.sendall(struct.pack('!I', len(value)) + value)


def exchange(endpoint, request):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(10)
        connection.connect(str(endpoint))
        _send(connection, encode(request))
        return decode(_receive(connection))


def serve(directory, endpoint):
    from xblago.core.uel_schema import require_synthetic
    require_synthetic()
    require_safe_sqlite()

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(10)
            try:
                response = accept(directory, decode(_receive(self.request)))
                _send(self.request, encode(response))
            except (JournalUnavailable, UnsafeSQLite, OSError, sqlite3.Error, ValueError, TypeError):
                _send(self.request, encode({'error': 'UNKNOWN_RECOVERY'}))

    with socketserver.UnixStreamServer(str(endpoint), Handler) as server:
        os.chmod(endpoint, 0o600)
        server.serve_forever()


def configuration():
    path = os.getenv('XBLAGO_UEL_CHECKPOINT_CONFIG', '')
    if not path:
        return None
    path = Path(path)
    if path.is_symlink() or path.stat().st_mode & 0o077 or path.stat().st_size > 4096:
        fail()
    config = decode(path.read_bytes())
    if type(config) is not dict or set(config) != {'stream', 'custodian_key', 'source_key_file', 'endpoint'}:
        fail()
    if not hex64(config['stream']) or not hex64(config['custodian_key']):
        fail()
    for field in ('source_key_file', 'endpoint'):
        if type(config[field]) is not str or not Path(config[field]).is_absolute():
            fail()
    return config


def policy(config):
    return {'profile': PROFILE, 'stream': config['stream'], 'custodian_key': config['custodian_key'],
            'source_key': public(load_key(config['source_key_file']))}


def proof(rows):
    journal, uel = [], []
    from xblago.core.uel_local import ZERO as empty
    prior = empty
    for row in rows:
        journal.append(digest((PROFILE + '/private-journal\0' + row[4]).encode()))
        event = json.loads(row[2])
        if event['kind'] == 'uel_local':
            record = json.loads(event['state'])
            if record['sequence'] != len(uel) + 1 or record['previous'] != prior:
                fail()
            prior = record['record_ref']
            uel.append(digest((PROFILE + '/private-uel\0' + canonical(record)).encode()))
    return journal, uel


def verify_reply(reply, request, config):
    body = verify_signed(reply, config['custodian_key'], 'reply')
    if type(body) is not dict or set(body) != {'profile', 'stream', 'nonce', 'request_hash', 'checkpoint', 'status'}:
        fail()
    if body['profile'] != PROFILE or body['stream'] != config['stream'] or body['nonce'] != request['body']['nonce'] or body['request_hash'] != digest(encode(request)) or body['status'] != 'COMMITTED':
        fail()
    checkpoint = verify_checkpoint(body['checkpoint'], config['custodian_key'], config['stream'])
    requested = request['body']
    if (checkpoint['journal_seq'], checkpoint['journal_root'], checkpoint['uel_seq'], checkpoint['uel_root']) != (
            len(requested['journal']), root(requested['journal']), len(requested['uel']), root(requested['uel'])):
        fail()
    return body['checkpoint']


def guard(journal, rows):
    """Called under the original journal lock before read/append success."""
    markers = [json.loads(row[2]) for row in rows if json.loads(row[2])['kind'] == 'uel_checkpoint_policy']
    try:
        config = configuration()
        if config is None:
            if markers:
                fail()
            return
        from xblago.core.uel_schema import require_synthetic
        require_synthetic()
        require_safe_sqlite()
        expected = policy(config)
        for marker in markers:
            if marker != {'kind': 'uel_checkpoint_policy', 'owner': '', 'target': config['stream'],
                          'state': canonical(expected), 'recorded_at': 0}:
                fail()
        journal_leaves, uel_leaves = proof(rows)
        request = signed({'profile': PROFILE, 'stream': config['stream'], 'nonce': secrets.token_hex(32),
                          'journal': journal_leaves, 'uel': uel_leaves},
                         load_key(config['source_key_file']), 'request')
        verify_reply(exchange(config['endpoint'], request), request, config)
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError, RecursionError) as exc:
        raise JournalUnavailable('UEL checkpoint UNKNOWN_RECOVERY') from exc


def enroll(journal):
    from xblago.core.uel_schema import require_synthetic
    require_synthetic()
    require_safe_sqlite()
    config = configuration()
    if config is None:
        fail()
    expected = policy(config)
    event = {'kind': 'uel_checkpoint_policy', 'owner': '', 'target': config['stream'],
             'state': canonical(expected), 'recorded_at': 0}
    return journal.append([('uel-checkpoint-policy:' + config['stream'], canonical(event))])


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('initialize', 'serve', 'enroll', 'export'))
    parser.add_argument('--directory', type=Path)
    parser.add_argument('--endpoint', type=Path)
    parser.add_argument('--stream')
    parser.add_argument('--source-key')
    parser.add_argument('--journal', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    from xblago.core.uel_schema import require_synthetic
    require_synthetic()
    require_safe_sqlite()
    if args.operation == 'initialize':
        if args.directory is None or args.stream is None or args.source_key is None:
            parser.error('directory, stream and source public key required')
        print(initialize(args.directory, args.stream, args.source_key))
    elif args.operation == 'serve':
        if args.directory is None or args.endpoint is None:
            parser.error('directory and endpoint required')
        serve(args.directory, args.endpoint)
    elif args.operation == 'enroll':
        if args.journal is None:
            parser.error('original journal required')
        from xblago.core.security_journal import SecurityJournal
        enroll(SecurityJournal(args.journal))
        print('Checkpoint policy durably enrolled')
    else:
        if args.directory is None or args.output is None:
            parser.error('custodian directory and new private output required')
        path = args.directory / 'custody.sqlite3'
        with _locked(args.directory), sqlite3.connect(path.resolve().as_uri() + '?mode=rw', uri=True) as db:
            db.execute('BEGIN IMMEDIATE')
            metadata = db.execute('SELECT stream FROM metadata WHERE singleton=1').fetchone()
            receipts = _receipts(db)
            key = public(load_key(args.directory / 'signing.key'))
            verify_chain(receipts, key, metadata[0])
            _verify_head(args.directory, receipts, key, metadata[0])
            from xblago.core.journal_custody import _new_file
            from xblago.core.journal_witness import _sync_dir
            _new_file(args.output, encode(receipts))
            _sync_dir(args.output.parent)
            print(canonical({'stream': metadata[0], 'key': key, 'tip': digest(encode(receipts[-1])),
                             'status': 'OFFLINE_INTEGRITY_ONLY'}))


if __name__ == '__main__':
    main()
