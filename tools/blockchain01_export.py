"""New synthetic CapitalLedger/UEL report; no existing store or network input."""
import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

from tools.capital_custody_lab import source_fixture
from tools.independent_capital_verifier import verify_bundle


def export(output):
    # Existing domain commands, export and independently implemented verifier.
    # Temporary SQLite/checkpoint processes are closed by source_fixture BEFORE
    # returning; no private signing keys from the fixture are persisted.
    fixture=source_fixture(8)
    package=fixture['packages'][-1]
    checked=verify_bundle(package['proof'],package['pins'])
    if checked['status']!='PASS':raise ValueError('Source proof not verified')
    result={'format':'blago-synthetic-fund-report-1','synthetic_only':True,
        'fund':package['proof']['history']['fund'],'participants':8,
        'proof':package['proof'],'producer_pins':package['pins'],'verified':checked,
        'pin_provenance':'SYNTHETIC_PRODUCER_NOT_INDEPENDENT_PINNING'}
    raw=(json.dumps(result,sort_keys=True,indent=2,ensure_ascii=False)+'\n').encode('utf-8')
    fd=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as f:f.write(raw)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--synthetic-only',action='store_true',required=True)
    args=p.parse_args();export(args.output)
    print(json.dumps({'synthetic_report':'WRITTEN','capital_uel_verification':'PASS','real_funds':False,
                     'python':sys.version.split()[0],'sqlite':sqlite3.sqlite_version}))


if __name__=='__main__':main()
