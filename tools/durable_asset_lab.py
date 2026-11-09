"""09.1 synthetic persistent registry in the SAME guarded app SQLite. No network."""
import argparse
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch
from tools.help_readiness_lab import initialize
from xblago.core import asset_registry_schema as schema
from xblago.core.context_fencing import Generation,ContextConflict
from xblago.core.integration import preflight
from xblago.core.privacy_maintenance import restore
from xblago.core.security_journal import JournalUnavailable
from xblago.domain.asset_registry import AssetRegistry,suggest_claim
from xblago.domain.collective_calculator import HelpAccountingView
from xblago.domain.help_service import HelpService
from xblago.integrations.web import WebApp,ApiError
from xblago.integrations.privacy import PrivacyService


def run():
    with tempfile.TemporaryDirectory(prefix='blago-durable-asset-') as t:
        root=Path(t)/'new';cfg=initialize(root)
        with patch.dict(os.environ,cfg):
            path=root/'app/blago.sqlite3';app=WebApp(preflight(path))
            a,b=[app.register('synthetic_durable_'+n,'synthetic-password-only','synthetic-help011-only')[0] for n in ('owner','helper')]
            uid=app.session(a)[0]
            app.chat(uid,'Запомни city = Синтетический город')
            before_memory=app.store.all_memory(uid);before_history=app.history(uid)
            app.create_asset(uid,{'title':'Несопоставленная legacy синтетика'})
            checks={}
            def check(name,value):
                checks[name]=bool(value)
                if not value:raise AssertionError(name)
            check('explicit_additive_migration',schema.migrate_registry(path))
            check('migration_repeat_noop',not schema.migrate_registry(path))
            os.environ['XBLAGO_ASSET_REGISTRY_ENABLED']='true'
            app=WebApp(preflight(path));r=AssetRegistry(app)
            check('same_identity_memory_history',app.session(a)[0]==uid and app.store.all_memory(uid)==before_memory and app.history(uid)==before_history)
            check('no_automatic_legacy_import',r.feed(a)['items']==[])
            check('natural_suggestion_no_authority',not suggest_claim('У меня есть велосипед, готов делиться')['share_consent'])
            body={'label':'Синтетические компьютеры','category':'computer','quantity':'2','unit':'item','available':'0','share_consent':False,'right':'OWNER','audience':'PRIVATE','conditions':'ON_REQUEST'}
            key='synthetic-durable-key-01';aid=r.draft(a,body,key)['id']
            check('draft_private',r.feed(b)['items']==[])
            check('draft_idempotent',r.draft(a,body,key)['id']==aid)
            q=r.prepare(a,aid,1,dict(body,available='1',share_consent=True,audience='LAB_COMMUNITY'))
            check('preview_is_not_grant',r.feed(b)['items']==[])
            receipt=r.commit(a,q['challenge'],'ПОДТВЕРЖДАЮ')
            check('commit_replay_same_receipt',r.commit(a,q['challenge'],'ПОДТВЕРЖДАЮ')==receipt)
            restarted=AssetRegistry(WebApp(preflight(path)))
            check('durable_restart',restarted.feed(a)==r.feed(a))
            view=r.balance(b)
            check('no_asset_resource_double_count',view['registered_objects']==1 and view['categories']['computer:item']['known_stock']=='2' and view['categories']['computer:item']['available_self_declared']=='1')
            check('self_declared_not_verified',r.feed(b)['items'][0]['evidence']=='SELF_DECLARED' and view['btc'] is None)
            exported=PrivacyService(app).export(a)
            check('own_version_export',len(exported['tables']['asset_registry'][0]['history'])==2)
            check('foreign_export_isolated',PrivacyService(app).export(b)['tables']['asset_registry']==[])
            h=HelpService(app);rid=h.draft(a,'Мне нужна помощь с настройкой компьютера',[h.code(b)['code']],'synthetic-durable-help-01')['id']
            def act(actor,name,args=None):
                p=h.prepare(actor,rid,h.get(actor,rid)['version'],name,args or {})
                h.commit(actor,p['challenge'],'ПОДТВЕРЖДАЮ')
            act(a,'publish');act(b,'offer',{'note':'Могу добровольно помочь с настройкой'})
            act(a,'accept',{'offer_id':h.get(a,rid)['offers'][0]['id']});act(a,'confirm');act(b,'confirm')
            check('existing_help_only_lifecycle',HelpAccountingView(h).read(a)['completed_count']==1)
            backup=app.store.backup_database(Path(t)/'backup')
            generation=Generation(app.store,uid);r.revoke(a,aid)
            try:generation.commit();fenced=False
            except ContextConflict:fenced=True
            check('g09_context_invalidated',fenced)
            check('revoked_not_available',r.feed(b)['items']==[])
            recovered=WebApp(restore(backup,Path(cfg['XBLAGO_SECURITY_JOURNAL_DIR'])))
            try:AssetRegistry(recovered).feed(a);old_session_denied=False
            except ApiError:old_session_denied=True
            check('restore_invalidates_sessions',old_session_denied)
            token=recovered.login('synthetic_durable_owner','synthetic-password-only')[0];rr=AssetRegistry(recovered)
            check('old_snapshot_no_consent_resurrection',rr.feed(token)['items']==[])
            try:rr.draft(token,body,key);replay_denied=False
            except ApiError:replay_denied=True
            check('old_key_replay_denied',replay_denied)
            with recovered.store._locked_transaction() as db:
                check('sqlite_integrity',db.execute('PRAGMA integrity_check').fetchone()[0]=='ok')
                check('sqlite_foreign_keys',not db.execute('PRAGMA foreign_key_check').fetchall())
                check('no_financial_actions',db.execute('SELECT count(*) FROM blago_payment_requests').fetchone()[0]==0)
            return {'format':'blago-foundation09-1-lab-1','base_sha':'00c665b369acb6465f9afa795a6046c43f4107ce','status':'PASS','checks':checks,
                    'before_revocation_balance':view,'migration':{'version':schema.VERSION,'checksum':schema.CHECKSUM,'ddl':list(schema.DDL)},
                    'app_database_count':1,'external_llm_calls':0,'financial_operations':0,'automatic_legacy_imports':0,
                    'same_identity':True,'physical_ownership_verification':'NOT EXECUTED','federation':'NOT IMPLEMENTED',
                    'independent_operator_review':'NOT EXECUTED','real_pilot':'NO-GO'}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    result=run();args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'status':result['status'],'checks':len(result['checks']),'real_pilot':result['real_pilot']}))
