"""Local synthetic Help rehearsal. No Telegram I/O, HF credentials or startup DDL."""
import argparse
import json
import os
from pathlib import Path
import shutil
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from tools.integration_fixture import create
from xblago.core.integration import preflight
from xblago.core.sqlite_safety import runtime_evidence
from xblago.integrations.web import WebApp, BlagoWebHandler

FORMAT = 'blago-help-readiness-lab-1'


def initialize(root, journal=None, witness=None):
    root = Path(root).resolve()
    control_paths = [Path(journal).resolve() if journal else root/'journal',
                     Path(witness).resolve() if witness else root/'witness']
    if any(a == b or a.is_relative_to(b) or b.is_relative_to(a) for a, b in [control_paths]):
        raise ValueError('Journal and witness directories must be separate')
    # Never initialize over an existing root or control store.
    for destination in (journal, witness):
        if destination is not None and Path(destination).exists():
            raise ValueError('Control destination must be new')
    cfg = create(root, help_enabled=True)
    for name, destination in (('XBLAGO_SECURITY_JOURNAL_DIR',journal),('XBLAGO_JOURNAL_WITNESS_DIR',witness)):
        if destination is not None:
            destination = Path(destination).resolve()
            shutil.move(cfg[name], destination)
            cfg[name] = str(destination)
    cfg.update(XBLAGO_WEB_INVITE_CODE='synthetic-help011-only', XBLAGO_COOKIE_SECURE='false',
               XBLAGO_PUBLIC_ORIGIN='http://localhost:17860', XBLAGO_WEBAUTHN_RP_ID='localhost',
               TELEGRAM_BOT_TOKEN='123:synthetic-help011-only',
               XBLAGO_IDENTITY_CORE_ENABLED='false', XBLAGO_IDENTITY_RECOVERY_ENABLED='false',XBLAGO_GENESIS_ENABLED='false')
    with patch.dict(os.environ,cfg): preflight(root/'app/blago.sqlite3')
    (root/'public-config.json').write_text(json.dumps(cfg,indent=2)+'\n')
    (root/'lab-only.json').write_text(json.dumps({'format':FORMAT,'synthetic_only':True,'config':cfg},indent=2)+'\n')
    (root/'lab-only.json').chmod(0o600)
    return cfg


def configuration(root):
    root=Path(root).resolve();marker=root/'lab-only.json'
    if marker.is_symlink() or marker.stat().st_mode & 0o077:
        raise ValueError('Private synthetic lab marker required')
    value=json.loads(marker.read_text())
    if value.get('format')!=FORMAT or value.get('synthetic_only') is not True:
        raise ValueError('Synthetic lab only')
    cfg=value['config']
    if Path(cfg['XBLAGO_DATA_DIR']).resolve()!=root/'app' or cfg['XBLAGO_INTEGRATION_MODE']!='synthetic' or cfg['XBLAGO_LLM_PROVIDER']!='local':
        raise ValueError('Not a synthetic local-provider fixture')
    if cfg.get('TELEGRAM_BOT_TOKEN')!='123:synthetic-help011-only':raise ValueError('No real bot credentials allowed')
    return cfg


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    init=sub.add_parser('init');init.add_argument('--new-root',type=Path,required=True);init.add_argument('--journal-dir',type=Path);init.add_argument('--witness-dir',type=Path)
    for name in ('serve','inspect'):
        s=sub.add_parser(name);s.add_argument('--root',type=Path,required=True)
        if name=='serve':s.add_argument('--bind',choices=['127.0.0.1','0.0.0.0'],default='127.0.0.1');s.add_argument('--port',type=int,default=17860)
    args=p.parse_args()
    if args.command=='init':initialize(args.new_root,args.journal_dir,args.witness_dir);print('New SYNTHETIC ONLY lab initialized; no real participants');return
    cfg=configuration(args.root)
    with patch.dict(os.environ,cfg):
        store=preflight(Path(cfg['XBLAGO_DATA_DIR'])/'blago.sqlite3')
        if args.command=='inspect':
            with store._connect() as db:
                result={'format':FORMAT,'sqlite':runtime_evidence(),'journal_mode':db.execute('PRAGMA journal_mode').fetchone()[0],
                        'synchronous':db.execute('PRAGMA synchronous').fetchone()[0], 'integrity_check':db.execute('PRAGMA integrity_check').fetchone()[0],
                        'foreign_key_violations':len(db.execute('PRAGMA foreign_key_check').fetchall()),
                        'independent_failure_domains':'UNKNOWN','real_pilot':'NO-GO'}
            print(json.dumps(result,indent=2));return
        server=ThreadingHTTPServer((args.bind,args.port),BlagoWebHandler);server.blago_web_app=WebApp(store)
        print('SYNTHETIC ONLY Web lab; no bot polling/webhook/provider access',flush=True)
        try:server.serve_forever()
        finally:server.server_close()

if __name__=='__main__':main()
