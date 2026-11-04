"""Encrypted offline DR packages; wrapping keys and fresh witnesses stay separate."""
import argparse
import base64
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from xblago.core.security_journal import JournalUnavailable,SecurityJournal,canonical

FORMAT='blago-journal-dr-1'
FILES={'key.json','journal.sqlite3','head.json'}


def _key(path):
    if path.is_symlink() or path.stat().st_mode&0o077:
        raise JournalUnavailable('DR wrapping key permissions must be private')
    value=path.read_bytes()
    if len(value)!=32:
        raise JournalUnavailable('A separate 32-byte wrapping key is required')
    return value


def _new_file(path,data):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
    with os.fdopen(fd,'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def export_bundle(journal,destination,wrapping_key_file):
    key=_key(wrapping_key_file)
    if destination.exists():
        raise ValueError('DR destination must not exist')
    with tempfile.TemporaryDirectory(prefix='blago-dr-export-') as root:
        with journal._locked() as db:
            rows,chain=journal._verified(db)
            snapshot=Path(root)/'journal.sqlite3'
            target=sqlite3.connect(snapshot)
            try:
                db.backup(target)
            finally:
                target.close()
            files={name:base64.b64encode((snapshot if name=='journal.sqlite3' else journal.directory/name).read_bytes()).decode() for name in FILES}
            payload={'journal_id':journal.journal_id,'seq':len(rows),'head':chain[-1],'files':files,
                     'sha256':{name:hashlib.sha256(base64.b64decode(value)).hexdigest() for name,value in files.items()}}
        nonce=os.urandom(12)
        encrypted=AESGCM(key).encrypt(nonce,canonical(payload).encode(),FORMAT.encode())
        envelope={'format':FORMAT,'nonce':base64.b64encode(nonce).decode(),'ciphertext':base64.b64encode(encrypted).decode()}
        _new_file(destination,canonical(envelope).encode())
        from xblago.core.journal_witness import _sync_dir
        _sync_dir(destination.parent)
    return {'journal_id':journal.journal_id,'seq':payload['seq'],'head':payload['head'],'package_sha256':hashlib.sha256(destination.read_bytes()).hexdigest()}


def recover_bundle(package,destination,wrapping_key_file,witness_directory):
    if destination.exists() or package.stat().st_size>128*1024*1024:
        raise JournalUnavailable('Recovery requires a new private destination and a bounded package')
    stage=None
    try:
        envelope=json.loads(package.read_text())
        if set(envelope)!={'format','nonce','ciphertext'} or envelope['format']!=FORMAT:
            raise ValueError('DR package format mismatch')
        payload=json.loads(AESGCM(_key(wrapping_key_file)).decrypt(base64.b64decode(envelope['nonce'],validate=True),
            base64.b64decode(envelope['ciphertext'],validate=True),FORMAT.encode()))
        if set(payload['files'])!=FILES or set(payload['sha256'])!=FILES:
            raise ValueError('DR package file allowlist mismatch')
        stage=Path(tempfile.mkdtemp(prefix='.blago-dr-',dir=destination.parent))
        for name in FILES:
            data=base64.b64decode(payload['files'][name],validate=True)
            if hashlib.sha256(data).hexdigest()!=payload['sha256'][name]:
                raise ValueError('DR package file hash mismatch')
            _new_file(stage/name,data)
        from xblago.core.journal_witness import _sync_dir
        _sync_dir(stage)
        journal=SecurityJournal(stage,witness_directory=witness_directory)
        rows,chain=journal.read()  # a stale package fails against the independent high-water mark
        if payload['journal_id']!=journal.journal_id or payload['seq']!=len(rows) or payload['head']!=chain[-1]:
            raise ValueError('DR manifest/journal mismatch')
        os.rename(stage,destination)
        stage=None
        _sync_dir(destination.parent)
        return SecurityJournal(destination,witness_directory=witness_directory)
    except Exception as exc:
        if isinstance(exc,JournalUnavailable):
            raise
        raise JournalUnavailable('DR package could not be authenticated/reconciled; access remains closed') from exc
    finally:
        if stage is not None:
            shutil.rmtree(stage)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation',choices=('init-wrap-key','init-witness','export','recover'))
    parser.add_argument('--journal',type=Path)
    parser.add_argument('--witness',type=Path)
    parser.add_argument('--wrap-key',type=Path)
    parser.add_argument('--package',type=Path)
    args=parser.parse_args()
    if args.operation=='init-wrap-key':
        if args.wrap_key is None:
            parser.error('--wrap-key is required')
        _new_file(args.wrap_key,os.urandom(32))
        from xblago.core.journal_witness import _sync_dir
        _sync_dir(args.wrap_key.parent)
        print('New wrapping key created; escrow separately, never print it')
    elif args.operation=='init-witness':
        if args.journal is None or args.witness is None:
            parser.error('--journal and a new --witness directory are required')
        from xblago.core.journal_witness import initialize
        initialize(SecurityJournal(args.journal),args.witness)
        print('Independent checkpoint initialized; preserve its latest retained head')
    elif args.operation=='export':
        if any(value is None for value in (args.journal,args.package,args.wrap_key)):
            parser.error('--journal, --package and --wrap-key are required')
        print(json.dumps(export_bundle(SecurityJournal(args.journal),args.package,args.wrap_key)))
    else:
        if any(value is None for value in (args.journal,args.package,args.wrap_key,args.witness)):
            parser.error('--journal (new), --package, --wrap-key and --witness are required')
        recover_bundle(args.package,args.journal,args.wrap_key,args.witness)
        print('Recovered journal authenticated against independent witness; restore application offline next')


if __name__=='__main__':
    main()
