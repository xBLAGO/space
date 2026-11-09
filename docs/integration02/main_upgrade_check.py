"""Offline synthetic main -> candidate rehearsal, never opens production paths."""
import io,json,os,sqlite3,subprocess,sys,tarfile,tempfile
from pathlib import Path
from unittest.mock import patch
import _sqlite3
from xblago.core.storage import SQLiteBlagoStore
from xblago.integrations.web import WebApp
from xblago.core.identity_schema import migrate_identity_store
from xblago.core.identity_runtime_schema import migrate_identity_runtime
from xblago.core.privacy_schema import migrate_privacy
from xblago.core.pilot_schema import migrate_pilot
from xblago.core.help_schema import migrate_help
from xblago.core.context_schema import migrate_context
from xblago.core.lifecycle_schema import migrate_lifecycle
from xblago.core.security_journal import SecurityJournal,JournalUnavailable
from xblago.core.journal_witness import initialize
from xblago.core.integration import preflight
from xblago.core.privacy_maintenance import restore
MAIN='3c218dc5757879c26b3aec520af3636b2d702715'
SEED="""import json,sys
from pathlib import Path
from xblago.core.storage import SQLiteBlagoStore,TelegramIdentity
s=SQLiteBlagoStore(Path(sys.argv[1]));u=s.resolve_telegram_user(TelegramIdentity(7000000501,7000000501,'private'))
s.memory_for_user(u).set('city','Синтетический город')
s.append_history(u,'user','Синтетическая старая реплика')
s.append_history(u,'assistant','Синтетический старый ответ')
print(json.dumps({'uid':u}))
"""
def run():
    with tempfile.TemporaryDirectory(prefix='blago-int02-main-upgrade-') as d:
        root=Path(d);source=root/'legacy-src';source.mkdir();appdir=root/'app';appdir.mkdir();path=appdir/'blago.sqlite3'
        archive=subprocess.check_output(['git','archive',MAIN,'src'])
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:tar.extractall(source,filter='data')
        env={k:os.environ[k] for k in ('PATH','HOME','TMPDIR','LANG','LC_ALL','LD_PRELOAD','LD_LIBRARY_PATH','DYLD_LIBRARY_PATH') if k in os.environ}
        env['PYTHONPATH']=str(Path(_sqlite3.__file__).parent)+os.pathsep+str(source/'src')
        uid=json.loads(subprocess.check_output([sys.executable,'-c',SEED,str(path)],cwd=source,env=env,text=True))['uid']
        cfg={'XBLAGO_PILOT_MODE':'false','XBLAGO_REQUIRE_SECURE_IDENTITY':'false','XBLAGO_REQUIRE_PRIVACY_JOURNAL':'false','XBLAGO_SECURITY_JOURNAL_DIR':'','XBLAGO_JOURNAL_WITNESS_DIR':'','XBLAGO_INTEGRATION_MODE':'','XBLAGO_HELP_ENABLED':'false','XBLAGO_CONTEXT_FENCING_REQUIRED':'false','XBLAGO_LIFECYCLE_REQUIRED':'false'}
        with patch.dict(os.environ,cfg):
            WebApp(SQLiteBlagoStore(path));migrate_identity_store(path);migrate_identity_runtime(path)
            journal=root/'journal';witness=root/'witness';SecurityJournal.initialize(journal);migrate_privacy(path,journal)
            os.environ['XBLAGO_SECURITY_JOURNAL_DIR']=str(journal);migrate_pilot(path);initialize(SecurityJournal(journal),witness)
        cfg.update(XBLAGO_PILOT_MODE='true',XBLAGO_REQUIRE_SECURE_IDENTITY='true',XBLAGO_REQUIRE_PRIVACY_JOURNAL='true',XBLAGO_SECURITY_JOURNAL_DIR=str(journal),XBLAGO_JOURNAL_WITNESS_DIR=str(witness),XBLAGO_INTEGRATION_MODE='synthetic',XBLAGO_HELP_ENABLED='true',XBLAGO_CONTEXT_FENCING_REQUIRED='false',XBLAGO_LIFECYCLE_REQUIRED='false',XBLAGO_LLM_PROVIDER='local')
        with patch.dict(os.environ,cfg):
            migrate_help(path);migrate_context(path);migrate_lifecycle(path)
            os.environ.update(XBLAGO_CONTEXT_FENCING_REQUIRED='true',XBLAGO_LIFECYCLE_REQUIRED='true')
            unchanged=[not f(path) for f in (migrate_identity_store,migrate_identity_runtime,migrate_pilot,migrate_context,migrate_lifecycle)]
            unchanged.append(not migrate_privacy(path,journal))
            before_help=path.read_bytes()
            try:migrate_help(path)
            except ValueError as exc:
                assert 'frozen Integration 01' in str(exc)
                legacy_reapply='REFUSED_FROZEN_BASELINE'
            else:raise AssertionError('Expected documented legacy Help migration refusal')
            store=preflight(path);old=store.backup_database(root/'offline-recovery')
            with store._locked_transaction() as db:
                checks={'old_identity_preserved':db.execute('SELECT count(*) FROM users WHERE blago_user_id=?',(uid,)).fetchone()[0]==1,
                        'memory_preserved':store.all_memory(uid).get('city')=='Синтетический город',
                        'history_preserved':len(store.recent_history(uid,100))==2,'supported_repeat_migrations_no_change':all(unchanged),
                        'legacy_help_reapply_safely_refused':legacy_reapply=='REFUSED_FROZEN_BASELINE',
                        'integrity':db.execute('PRAGMA integrity_check').fetchone()[0]=='ok' and not db.execute('PRAGMA foreign_key_check').fetchall()}
            store.delete_memory(uid,'city');recovered=restore(old,journal)
            checks['old_certified_backup_does_not_restore_deleted_fact']='city' not in recovered.all_memory(uid)
            assert all(checks.values()),checks
    return {'format':'blago-integration02-main-upgrade-1','main_source_sha':MAIN,'runtime_source_sha':'395ecf10f9187663fddb64795597e7efe0f014fa','scope':'Synthetic DB actually seeded by archived main code; newly reviewed synthetic control lineage only','checks':checks,'production_database_used':False,'limits':['No real legacy JSON inventory or production upgrade','Legacy Help migrator is not stack-idempotent after Context/Lifecycle; requires reviewed ledger-aware dispatch, no checksum weakening','Legacy unpaired dialogue TTL still requires review','Off-host storage/DR not qualified'],'real_pilot':'NO-GO'}
if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);args=p.parse_args();r=run();args.output.write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r))
