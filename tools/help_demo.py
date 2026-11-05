"""Synthetic-only verifiable successful nonfinancial help cycle; no network/LLM."""
import argparse
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch
from tools.integration_fixture import create
from xblago.core.integration import preflight
from xblago.domain.help_service import HelpService,suggest_draft
from xblago.integrations.web import WebApp

def run():
    with tempfile.TemporaryDirectory(prefix='blago-help-demo-') as root:
        cfg=create(Path(root)/'new',help_enabled=True);cfg['XBLAGO_WEB_INVITE_CODE']='synthetic-demo-only'
        with patch.dict(os.environ,cfg):
            app=WebApp(preflight(Path(cfg['XBLAGO_DATA_DIR'])/'blago.sqlite3'));h=HelpService(app)
            a=app.register('demo_author','synthetic-password-only','synthetic-demo-only')[0]
            b=app.register('demo_helper','synthetic-password-only','synthetic-demo-only')[0]
            utterance='Мне нужна помощь с ремонтом велосипеда'
            assert suggest_draft(utterance)
            assert h.feed(a)['items']==[]
            rid=h.draft(a,utterance,[h.code(b)['code']],'synthetic-demo-request-01')['id']
            transitions=[{'state':'DRAFT','audience_verified':h.feed(b)['items']==[]}]
            def action(actor,name,args=None):
                version=h.get(actor,rid)['version'];preview=h.prepare(actor,rid,version,name,args or {})
                receipt=h.commit(actor,preview['challenge'],'ПОДТВЕРЖДАЮ')
                transitions.append({'action':name,'state':receipt['state'],'version':receipt['version']});return receipt
            action(a,'publish');action(b,'offer',{'note':'Могу добровольно помочь с ремонтом'})
            action(a,'accept',{'offer_id':h.get(a,rid)['offers'][0]['id']})
            action(a,'confirm');action(b,'confirm')
            assert h.get(a,rid)['state']=='COMPLETED';assert h.feed(a)['completed_owned']==1
            with app.store._connect() as db:
                assert db.execute('SELECT count(*) FROM blago_payment_requests').fetchone()[0]==0
                assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
                assert not db.execute('PRAGMA foreign_key_check').fetchall()
            return {'scope':'synthetic local only','dialogue_origin':utterance,'transitions':transitions,'events':h.get(a,rid)['events'],'mutually_confirmed_owned_count':1,'financial_operations':0,'integrity_check':'ok','foreign_key_check':[],'real_pilot':'NO-GO'}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    value=run();args.output.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');print(json.dumps(value,ensure_ascii=False))
