"""Fail-closed attestation of the SQLite library actually called by Python."""
import argparse
import json
import os
import platform
import sqlite3
import _sqlite3

PINNED_VERSION='3.54.1'
PINNED_SOURCE_ID='2026-10-16 14:45:20 555e07ef33f69a0957d24d8e555b702464a131d6f1607f4c3c1e4551e8e6ca76'


class UnsafeSQLite(RuntimeError):
    pass


def runtime_evidence():
    with sqlite3.connect(':memory:') as db:
        source=db.execute('SELECT sqlite_source_id()').fetchone()[0]
    verified=sqlite3.sqlite_version==PINNED_VERSION and source==PINNED_SOURCE_ID
    return {'python':platform.python_version(),'sqlite_version':sqlite3.sqlite_version,'sqlite_source_id':source,
            'python_extension':_sqlite3.__file__,'wal_reset_fix_verified':verified,
            'policy':'pinned upstream version/source-id; no environment assertion of vendor backport is trusted'}


def require_safe_sqlite():
    result=runtime_evidence()
    if not result['wal_reset_fix_verified']:
        raise UnsafeSQLite('Pilot requires the verified patched SQLite library; inspect python -m xblago.core.sqlite_safety')
    return result


def pilot_enabled():
    return os.getenv('XBLAGO_PILOT_MODE','').lower() in ('true','1','yes')


def enforce_pilot_sqlite():
    if pilot_enabled():
        require_safe_sqlite()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--require-safe',action='store_true')
    args=parser.parse_args()
    result=runtime_evidence()
    print(json.dumps(result,indent=2))
    if args.require_safe and not result['wal_reset_fix_verified']:
        raise SystemExit(1)
