"""Explicit synthetic retention operator commands. Never scheduled or deployed here."""
import argparse
import json
from pathlib import Path
from xblago.core.integration import preflight
from xblago.core.data_lifecycle import sweep,managed_backup,retire_backup


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['sweep','backup','retire-backup']);p.add_argument('--database',type=Path,required=True);p.add_argument('--backup-id');p.add_argument('--unlink',action='store_true');args=p.parse_args()
    store=preflight(args.database)
    if args.command=='sweep':result=sweep(store)
    elif args.command=='backup':
        result=managed_backup(store);result.pop('path')  # operator config owns the private backup directory
    else:
        if not args.backup_id:p.error('--backup-id required')
        result=retire_backup(store,args.backup_id,unlink=args.unlink)
    print(json.dumps({'synthetic_only':True,'result':result,'real_pilot':'NO-GO'}))

if __name__=='__main__':main()
