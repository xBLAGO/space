"""Independent write-ahead revocation journal, never part of application backups.

The journal directory, key and signed head must survive an application restore.
This protects against rollback of the application DB, not a compromised host/key
or simultaneous rollback of every independent copy of the control plane.
"""
from __future__ import annotations

try:
    import fcntl
except ImportError:  # Legacy stores remain usable; 05.3 needs tested POSIX locks.
    fcntl = None
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4


class JournalUnavailable(RuntimeError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


class SecurityJournal:
    @classmethod
    def initialize(cls, directory: Path):
        if fcntl is None:
            raise JournalUnavailable('Privacy journal requires POSIX file locking')
        directory.mkdir(mode=0o700, parents=False, exist_ok=False)
        key = {'journal_id': str(uuid4()), 'key': secrets.token_hex(32)}
        fd = os.open(directory/'key.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write(canonical(key))
            stream.flush()
            os.fsync(stream.fileno())
        with sqlite3.connect(directory/'journal.sqlite3') as db:
            db.execute('PRAGMA synchronous=FULL')
            db.execute('CREATE TABLE entries (seq INTEGER PRIMARY KEY, event_id TEXT NOT NULL UNIQUE, payload TEXT NOT NULL, prev TEXT NOT NULL, mac TEXT NOT NULL)')
        os.chmod(directory/'journal.sqlite3', 0o600)
        result = cls(directory, allow_new=True)
        result._write_anchor(0, '0'*64)
        parent_fd=os.open(directory.parent,os.O_RDONLY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
        return result

    def __init__(self, directory: Path, allow_new=False, witness_directory: Path | None = None):
        from xblago.core.sqlite_safety import enforce_pilot_sqlite
        enforce_pilot_sqlite()
        self.directory = directory
        configured=os.getenv('XBLAGO_JOURNAL_WITNESS_DIR','')
        self.witness_directory=witness_directory or (Path(configured) if configured else None)
        from xblago.core.sqlite_safety import pilot_enabled
        if pilot_enabled() and self.witness_directory is None:
            raise JournalUnavailable('Pilot requires independently retained witness checkpoints')
        try:
            for path in (directory, directory/'key.json', directory/'journal.sqlite3'):
                if path.is_symlink() or not path.exists() or path.stat().st_mode & 0o077:
                    raise ValueError('Unsafe journal permissions/path')
            data = json.loads((directory/'key.json').read_text())
            self.journal_id = data['journal_id']
            self.key = bytes.fromhex(data['key'])
            if len(self.key) != 32 or not self.journal_id:
                raise ValueError('Invalid journal key')
            if not allow_new and not (directory/'head.json').is_file():
                raise ValueError('Journal anchor missing')
            if (directory/'head.json').exists() and ((directory/'head.json').is_symlink() or (directory/'head.json').stat().st_mode & 0o077):
                raise ValueError('Unsafe journal anchor')
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise JournalUnavailable('Independent security journal unavailable') from exc

    def tag(self, *parts):
        values = [p.hex() if isinstance(p, bytes) else str(p) for p in parts]
        return hmac.new(self.key, canonical(values).encode(), hashlib.sha256).hexdigest()

    def _mac(self, value):
        return hmac.new(self.key, canonical(value).encode(), hashlib.sha256).hexdigest()

    def _write_anchor(self, seq, head):
        state = {'journal_id': self.journal_id, 'seq': seq, 'head': head}
        state['mac'] = self._mac(state)
        temporary = self.directory / ('head-'+secrets.token_hex(8)+'.tmp')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd,'w') as stream:
            stream.write(canonical(state))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary,self.directory/'head.json')
        fd = os.open(self.directory,os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    @contextmanager
    def _locked(self):
        if fcntl is None:
            raise JournalUnavailable('Privacy journal requires POSIX file locking')
        fd = None
        try:
            fd = os.open(self.directory/'journal.lock', os.O_RDWR | os.O_CREAT | getattr(os,'O_NOFOLLOW',0), 0o600)
            fcntl.flock(fd,fcntl.LOCK_EX)
            with sqlite3.connect((self.directory/'journal.sqlite3').resolve().as_uri()+'?mode=rw',uri=True,timeout=30) as db:
                db.execute('PRAGMA synchronous=FULL')
                yield db
        except (OSError, sqlite3.Error, ValueError, KeyError, TypeError) as exc:
            raise JournalUnavailable('Security journal verification/write failed') from exc
        finally:
            if fd is not None:
                os.close(fd)

    def _verified(self, db):
        anchor = json.loads((self.directory/'head.json').read_text())
        if (not isinstance(anchor,dict) or set(anchor)!={'journal_id','seq','head','mac'}
                or type(anchor['seq']) is not int or anchor['seq']<0
                or not isinstance(anchor['head'],str) or len(anchor['head'])!=64
                or any(c not in '0123456789abcdef' for c in anchor['head'])
                or not isinstance(anchor['mac'],str) or len(anchor['mac'])!=64):
            raise JournalUnavailable('Invalid signed journal anchor structure')
        signature = anchor.pop('mac')
        if anchor['journal_id'] != self.journal_id or not hmac.compare_digest(signature,self._mac(anchor)):
            raise JournalUnavailable('Security journal anchor invalid')
        rows = db.execute('SELECT seq,event_id,payload,prev,mac FROM entries ORDER BY seq').fetchall()
        head = '0'*64
        chain = [head]
        for seq, event_id, payload, previous, mac in rows:
            if seq != len(chain) or previous != head or not hmac.compare_digest(mac,self._mac([seq,event_id,payload,previous])):
                raise JournalUnavailable('Security journal chain invalid')
            json.loads(payload)
            head = mac
            chain.append(head)
        if anchor['seq'] >= len(chain) or chain[anchor['seq']] != anchor['head']:
            raise JournalUnavailable('Security journal rollback detected')
        if anchor['seq'] != len(rows):
            self._write_anchor(len(rows),head)  # recover append committed before anchor fsync
        self._sync_witness(chain)
        return rows, chain

    def _sync_witness(self,chain):
        if self.witness_directory is not None:
            from xblago.core.journal_witness import sync
            sync(self,chain,self.witness_directory)

    def read(self):
        with self._locked() as db:
            return self._verified(db)

    def append(self, entries):
        with self._locked() as db:
            rows, chain = self._verified(db)
            db.execute('BEGIN IMMEDIATE')
            head, seq = chain[-1], len(rows)
            for event_id, payload in entries:
                old = db.execute('SELECT payload FROM entries WHERE event_id=?',(event_id,)).fetchone()
                if old:
                    if old[0] != payload:
                        raise JournalUnavailable('Security journal event collision')
                    continue
                seq += 1
                mac = self._mac([seq,event_id,payload,head])
                db.execute('INSERT INTO entries VALUES (?,?,?,?,?)',(seq,event_id,payload,head,mac))
                head = mac
                chain.append(head)
            db.commit()
            self._write_anchor(seq,head)
            self._sync_witness(chain)
            return seq, head
