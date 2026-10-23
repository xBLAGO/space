"""Append-only signed high-water checkpoints on an independently retained store.

Production must give this path an independent, immutable/retained fault domain.
Path separation alone is not proof of off-host durability or IAM enforcement.
"""
import fcntl
import json
import os
import secrets
from pathlib import Path

from xblago.core.security_journal import JournalUnavailable,canonical


def _write_new(path,value):
    temporary=path.parent/('.checkpoint-'+secrets.token_hex(16)+'.tmp')
    try:
        fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
        with os.fdopen(fd,'w') as stream:
            stream.write(canonical(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary,path)  # atomic publish without replacing any retained checkpoint
    finally:
        temporary.unlink(missing_ok=True)


def _sync_dir(directory):
    fd=os.open(directory,os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def initialize(journal,directory: Path):
    rows,chain=journal.read()
    directory.mkdir(mode=0o700,parents=False,exist_ok=False)
    record={'journal_id':journal.journal_id,'seq':len(rows),'head':chain[-1]}
    record['mac']=journal._mac(record)
    _write_new(directory/'identity.json',{'journal_id':journal.journal_id})
    _write_new(directory/(f'{len(rows):020d}-'+chain[-1]+'.json'),record)
    _sync_dir(directory)
    _sync_dir(directory.parent)


def sync(journal,chain,directory: Path):
    """Validate the retained maximum, then durably append without overwriting it."""
    directory=directory.resolve()
    primary=journal.directory.resolve()
    if directory==primary or directory.is_relative_to(primary) or primary.is_relative_to(directory):
        raise JournalUnavailable('Witness must be outside the journal tree')
    try:
        if not directory.is_dir() or directory.stat().st_mode&0o077:
            raise ValueError('Witness missing or unsafe permissions')
        if json.loads((directory/'identity.json').read_text())!={'journal_id':journal.journal_id}:
            raise ValueError('Witness identity mismatch')
        fd=os.open(directory/'witness.lock',os.O_RDWR|os.O_CREAT|getattr(os,'O_NOFOLLOW',0),0o600)
        try:
            fcntl.flock(fd,fcntl.LOCK_EX)
            checkpoints=[]
            checkpoint_paths={}
            for path in directory.glob('[0-9]*.json'):
                if path.is_symlink() or path.stat().st_mode&0o077:
                    raise ValueError('Unsafe witness checkpoint')
                record=json.loads(path.read_text())
                if set(record)!={'journal_id','seq','head','mac'} or type(record['seq']) is not int or record['seq']<0:
                    raise ValueError('Invalid witness checkpoint')
                mac=record.pop('mac')
                import hmac
                if record['journal_id']!=journal.journal_id or not hmac.compare_digest(mac,journal._mac(record)):
                    raise ValueError('Witness signature mismatch')
                if record['seq']>=len(chain) or chain[record['seq']]!=record['head']:
                    raise ValueError('Journal is older than or diverges from independent witness')
                checkpoints.append(record['seq'])
                checkpoint_paths[record['seq']]=path
            if not checkpoints:
                raise ValueError('Independent witness high-water mark missing')
            seq=len(chain)-1
            if max(checkpoints)<seq:
                record={'journal_id':journal.journal_id,'seq':seq,'head':chain[-1]}
                record['mac']=journal._mac(record)
                _write_new(directory/(f'{seq:020d}-'+chain[-1]+'.json'),record)
                _sync_dir(directory)
            else:
                # A previous publish may have become visible before directory fsync failed.
                # Re-establish durability before treating a matching checkpoint as an ACK.
                published=checkpoint_paths[max(checkpoints)]
                checkpoint_fd=os.open(published,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
                try:
                    os.fsync(checkpoint_fd)
                finally:
                    os.close(checkpoint_fd)
                _sync_dir(directory)
        finally:
            os.close(fd)
    except (OSError,ValueError,KeyError,TypeError) as exc:
        raise JournalUnavailable('Independent witness unavailable or rollback detected') from exc
