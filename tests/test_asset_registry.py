"""09.1 synthetic durability, consent, cross-worker fencing and retained denials."""
import errno
import json
import multiprocessing
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
from tools.help_readiness_lab import initialize
from xblago.core import asset_registry_schema as schema
from xblago.core.context_fencing import Generation,ContextConflict
from xblago.core.integration import preflight
from xblago.core.privacy_control import PrivacyControl
from xblago.core.privacy_maintenance import restore,cleanup
from xblago.core.data_lifecycle import sweep
from xblago.core.security_journal import JournalUnavailable,SecurityJournal,canonical
from xblago.domain.asset_registry import AssetRegistry,suggest_claim
from xblago.integrations.web import WebApp,ApiError
from xblago.integrations.privacy import PrivacyService


def body(**updates):
    return dict({'label':'Синтетический велосипед','category':'bicycle','quantity':'2','available':'0','share_consent':False,
                 'unit':'item','right':'OWNER','audience':'PRIVATE','conditions':'ON_REQUEST'},**updates)


def worker(cfg,token,challenge,pipe):
    os.environ.update(cfg)
    try:
        r=AssetRegistry(WebApp(preflight(Path(cfg['XBLAGO_DATA_DIR'])/'blago.sqlite3')))
        try:pipe.send({'ok':r.commit(token,challenge,'ПОДТВЕРЖДАЮ')})
        except ApiError as exc:pipe.send({'denied':exc.status})
    finally:pipe.close()


def crash_worker(cfg,token,challenge,phase):
    os.environ.update(cfg);r=AssetRegistry(WebApp(preflight(Path(cfg['XBLAGO_DATA_DIR'])/'blago.sqlite3')))
    original=PrivacyControl.flush
    def flush(c):
        pending=c.db.execute("SELECT 1 FROM privacy_outbox WHERE json_extract(payload,'$.kind')='asset_state'").fetchone()
        if pending and phase=='before_journal':os._exit(81)
        result=original(c)
        if pending and phase=='after_journal':os._exit(82)
        return result
    if phase in ('before_journal','after_journal'):
        with patch.object(PrivacyControl,'flush',flush):r.commit(token,challenge,'ПОДТВЕРЖДАЮ')
    else:
        r.commit(token,challenge,'ПОДТВЕРЖДАЮ');os._exit(83)


def operation_worker(cfg,token,command,args,pipe):
    os.environ.update(cfg)
    try:
        service=AssetRegistry(WebApp(preflight(Path(cfg['XBLAGO_DATA_DIR'])/'blago.sqlite3')))
        pipe.send({'ready':True});pipe.recv()
        try:pipe.send({'ok':getattr(service,command)(token,*args)})
        except ApiError as exc:pipe.send({'denied':exc.status})
    finally:pipe.close()


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'new';self.cfg=initialize(self.root)
        self.env=patch.dict(os.environ,self.cfg);self.env.start();self.addCleanup(self.env.stop)
        self.path=self.root/'app/blago.sqlite3';schema.migrate_registry(self.path)
        self.cfg['XBLAGO_ASSET_REGISTRY_ENABLED']='true';os.environ.update(self.cfg)
        self.app=WebApp(preflight(self.path));self.r=AssetRegistry(self.app)
        self.a,self.b=[self.app.register('synthetic_asset_'+n,'synthetic-password-only','synthetic-help011-only')[0] for n in ('author','other')]
        self.uid=self.app.session(self.a)[0]
    def draft(self,**updates):return self.r.draft(self.a,body(**updates),'synthetic-canonical-asset-001')['id']
    def active(self):
        aid=self.draft();q=self.r.prepare(self.a,aid,1,body(available='1',share_consent=True,audience='LAB_COMMUNITY'))
        self.r.commit(self.a,q['challenge'],'ПОДТВЕРЖДАЮ');return aid
    def integrity(self):
        with self.app.store._locked_transaction() as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok');self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
    def spawn(self,challenge,phase):
        p=multiprocessing.get_context('spawn').Process(target=crash_worker,args=(self.cfg,self.a,challenge,phase));p.start();p.join(30)
        if p.is_alive():p.kill();p.join();self.fail('crash worker timeout')
        self.assertEqual(p.exitcode,{'before_journal':81,'after_journal':82,'after_commit':83}[phase])
    def test_повтор_миграции_ничего_не_импортирует(self):
        self.assertFalse(schema.migrate_registry(self.path));self.assertEqual(self.r.feed(self.a)['items'],[]);self.integrity()
    def test_владение_связано_с_действительной_сессией(self):
        for token in (self.uid,'invalid'):
            with self.assertRaises(ApiError):self.r.draft(token,body(),'synthetic-other-key-001')
        aid=self.draft()
        with self.assertRaises(ApiError):self.r.prepare(self.b,aid,1,body())
    def test_черновик_приватен_и_не_означает_готовность_делиться(self):
        aid=self.draft();self.assertEqual(self.r.feed(self.b)['items'],[])
        self.assertEqual(self.r.balance(self.a)['registered_objects'],0)
        with self.assertRaises(ApiError):self.r.draft(self.a,body(available='1',share_consent=True),'synthetic-publish-key-001')
        self.assertEqual(self.r.feed(self.a)['items'][0]['id'],aid)
    def test_повтор_черновика_не_создаёт_дубли(self):
        aid=self.draft();self.assertEqual(aid,self.draft())
        with self.assertRaises(ApiError):self.draft(label='Другой велосипед')
        with self.app.store._locked_transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM blago_assets').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count(*) FROM blago_resources').fetchone()[0],0)
    def test_legacy_не_импортируется_и_не_удваивает_баланс(self):
        self.app.create_asset(self.uid,{'title':'Legacy синтетика'})
        self.assertEqual(self.r.feed(self.a)['items'],[])
        aid=self.active();self.assertEqual(self.r.balance(self.b)['registered_objects'],1)
        self.assertEqual(self.r.balance(self.b)['categories']['bicycle:item']['known_stock'],'2')
    def test_явное_подтверждение_и_SELF_DECLARED(self):
        aid=self.draft();q=self.r.prepare(self.a,aid,1,body(available='1',share_consent=True,audience='LAB_COMMUNITY'))
        for token,confirmation in ((self.a,'да'),(self.b,'ПОДТВЕРЖДАЮ')):
            with self.assertRaises(ApiError):self.r.commit(token,q['challenge'],confirmation)
        receipt=self.r.commit(self.a,q['challenge'],'ПОДТВЕРЖДАЮ')
        self.assertEqual(receipt,self.r.commit(self.a,q['challenge'],'ПОДТВЕРЖДАЮ'))
        view=self.r.feed(self.b)['items'][0];self.assertEqual(view['evidence'],'SELF_DECLARED');self.assertNotIn('history',view)
        self.assertEqual(len(self.r.feed(self.a)['items'][0]['history']),2)
    def test_просроченное_подтверждение_и_откат_часов(self):
        aid=self.draft();q=self.r.prepare(self.a,aid,1,body());now=int(time.time())
        for stamp in (now-10,now+121):
            with patch('xblago.domain.asset_registry.time.time',return_value=stamp),self.assertRaises(ApiError):self.r.commit(self.a,q['challenge'],'ПОДТВЕРЖДАЮ')
    def test_ротация_сессии_закрывает_старое_подтверждение(self):
        aid=self.draft();q=self.r.prepare(self.a,aid,1,body());new=self.app.login('synthetic_asset_author','synthetic-password-only',old_token=self.a)[0]
        for token in (self.a,new):
            with self.assertRaises(ApiError):self.r.commit(token,q['challenge'],'ПОДТВЕРЖДАЮ')
    def test_отзыв_и_изменение_аудитории_без_старых_публичных_версий(self):
        aid=self.active();q=self.r.prepare(self.a,aid,2,body())
        self.r.commit(self.a,q['challenge'],'ПОДТВЕРЖДАЮ');self.assertEqual(self.r.feed(self.b)['items'],[])
        receipt=self.r.revoke(self.a,aid);self.assertEqual(receipt,self.r.revoke(self.a,aid))
        self.assertEqual(len(self.r.feed(self.a)['items'][0]['history']),4)
    def test_удаление_всех_версий_и_запрет_восстановления_по_ключу(self):
        aid=self.active();self.r.erase(self.a,aid);self.assertEqual(self.r.feed(self.a)['items'],[])
        with self.assertRaises(ApiError):self.draft()
        with self.app.store._locked_transaction() as db:
            for table in ('assetreg_versions','assetreg_operations','assetreg_challenges','blago_assets'):self.assertEqual(db.execute('SELECT count(*) FROM '+table).fetchone()[0],0)
        self.integrity()
    def test_экспорт_не_содержит_подтверждений_и_чужих_версий(self):
        self.active();data=PrivacyService(self.app).export(self.a);self.assertEqual(len(data['tables']['asset_registry']),1)
        encoded=json.dumps(data,ensure_ascii=False)
        self.assertNotIn('token_hash',encoded);self.assertNotIn('session_hash',encoded);self.assertNotIn('seal',encoded)
        self.assertEqual(PrivacyService(self.app).export(self.b)['tables']['asset_registry'],[])
    def test_удаление_аккаунта_очищает_каноническую_запись_и_сайдкары(self):
        self.active();p=PrivacyService(self.app);preview=p.preview(self.a);self.assertTrue(preview['eligible']);p.delete(self.a,preview['challenge'],'УДАЛИТЬ')
        self.assertEqual(self.r.feed(self.b)['items'],[])
        with self.app.store._locked_transaction() as db:
            for table in ('assetreg_records','assetreg_versions','assetreg_operations','assetreg_challenges'):self.assertEqual(db.execute('SELECT count(*) FROM '+table).fetchone()[0],0)
        token=self.app.register('synthetic_asset_reborn','synthetic-password-only','synthetic-help011-only')[0];self.assertEqual(self.r.feed(token)['items'],[])
    def test_неизвестное_количество_не_становится_нулём(self):
        aid=self.draft(quantity=None,available=None);q=self.r.prepare(self.a,aid,1,body(quantity=None,available=None,audience='LAB_COMMUNITY'))
        self.r.commit(self.a,q['challenge'],'ПОДТВЕРЖДАЮ');v=self.r.balance(self.b)
        self.assertEqual(v['unknown_stock_objects'],1);self.assertIsNone(v['btc']);self.assertIsNone(v['flow_btc'])
        self.assertEqual(self.r.feed(self.b)['items'][0]['body']['quantity'],None)
    def test_предположение_не_становится_заявлением_или_согласием(self):
        for text in ('Возможно, у меня есть велосипед','У соседа есть велосипед','«У меня есть велосипед»','Не запоминай мой велосипед'):
            self.assertIsNone(suggest_claim(text))
        suggested=suggest_claim('У меня есть велосипед, которым готов делиться')
        self.assertFalse(suggested['share_consent']);self.assertIsNone(suggested['quantity']);self.assertEqual(self.r.feed(self.a)['items'],[])
    def test_два_подтверждения_одной_версии_не_перезаписывают_друг_друга(self):
        aid=self.draft();qs=[self.r.prepare(self.a,aid,1,body(label=label)) for label in ('Первый вариант','Второй вариант')]
        self.r.commit(self.a,qs[0]['challenge'],'ПОДТВЕРЖДАЮ')
        with self.assertRaises(ApiError):self.r.commit(self.a,qs[1]['challenge'],'ПОДТВЕРЖДАЮ')
    def test_повреждение_истории_обнаруживается_до_чтения(self):
        self.active()
        with sqlite3.connect(self.path) as db:db.execute("UPDATE assetreg_versions SET body='{}' WHERE version=1")
        with self.assertRaises(JournalUnavailable):self.r.feed(self.a)
    def test_подмена_полей_challenge_обнаруживается(self):
        aid=self.draft();q=self.r.prepare(self.a,aid,1,body())
        with sqlite3.connect(self.path) as db:db.execute("UPDATE assetreg_challenges SET body=?",(canonical(body(label='Подмена')),))
        with self.assertRaises(JournalUnavailable):self.r.commit(self.a,q['challenge'],'ПОДТВЕРЖДАЮ')
    def test_подложная_квитанция_не_имитирует_сохранение(self):
        aid=self.draft();q=self.r.prepare(self.a,aid,1,body())
        with sqlite3.connect(self.path) as db:db.execute('UPDATE assetreg_challenges SET receipt=?',(canonical({'id':aid,'version':2,'status':'ACTIVE'}),))
        with self.assertRaises(JournalUnavailable):self.r.commit(self.a,q['challenge'],'ПОДТВЕРЖДАЮ')
    def test_прямое_legacy_изменение_управляемого_актива_откатывается(self):
        aid=self.active()
        with self.assertRaises(JournalUnavailable):
            with self.app.store._locked_transaction() as db:db.execute("UPDATE blago_assets SET title='неподтверждённое' WHERE id=?",(aid,))
        self.assertEqual(self.r.feed(self.a)['items'][0]['body']['label'],body()['label'])
    def test_потеря_witness_не_даёт_ответа_или_нового_согласия(self):
        aid=self.active();p=Path(self.cfg['XBLAGO_JOURNAL_WITNESS_DIR']);held=p.with_name('held-witness');p.rename(held)
        try:
            with self.assertRaises(JournalUnavailable):self.r.feed(self.a)
        finally:held.rename(p)
    def test_сбой_записи_до_journal_откатывает_операцию(self):
        aid=self.draft();q=self.r.prepare(self.a,aid,1,body())
        with patch.object(PrivacyControl,'flush',side_effect=OSError(errno.ENOSPC,'synthetic full disk')),self.assertRaises(OSError):self.r.commit(self.a,q['challenge'],'ПОДТВЕРЖДАЮ')
        self.assertEqual(self.r.feed(self.a)['items'][0]['version'],1)
    def test_старый_backup_не_возвращает_отозванное_согласие(self):
        aid=self.active();backup=self.app.store.backup_database(Path(self.tmp.name)/'backup');self.r.revoke(self.a,aid)
        store=restore(backup,Path(self.cfg['XBLAGO_SECURITY_JOURNAL_DIR']));app=WebApp(store);r=AssetRegistry(app)
        with self.assertRaises(ApiError):r.feed(self.a)
        token=app.login('synthetic_asset_author','synthetic-password-only')[0];self.assertEqual(r.feed(token)['items'],[])
        with self.assertRaises(ApiError):r.draft(token,body(),'synthetic-canonical-asset-001')
    def test_старый_backup_до_миграции_требует_явной_установки(self):
        with patch.dict(os.environ,{'XBLAGO_ASSET_REGISTRY_ENABLED':'false'}):cfg=initialize(Path(self.tmp.name)/'old-baseline')
        with patch.dict(os.environ,dict(cfg,XBLAGO_ASSET_REGISTRY_ENABLED='false')):
            path=Path(cfg['XBLAGO_DATA_DIR'])/'blago.sqlite3';app=WebApp(preflight(path))
            token=app.register('synthetic_preasset','synthetic-password-only','synthetic-help011-only')[0]
            backup=app.store.backup_database(Path(self.tmp.name)/'pre-migration-backup')
            self.assertTrue(schema.migrate_registry(path));self.assertFalse(schema.migrate_registry(path))
            os.environ['XBLAGO_ASSET_REGISTRY_ENABLED']='true';r=AssetRegistry(WebApp(preflight(path)))
            aid=r.draft(token,body(),'synthetic-preasset-key-01')['id'];r.erase(token,aid)
            with self.assertRaises(JournalUnavailable):restore(backup,Path(cfg['XBLAGO_SECURITY_JOURNAL_DIR']))
            self.assertTrue(schema.migrate_registry(backup))
            recovered=WebApp(restore(backup,Path(cfg['XBLAGO_SECURITY_JOURNAL_DIR'])))
            new=recovered.login('synthetic_preasset','synthetic-password-only')[0];rr=AssetRegistry(recovered)
            self.assertEqual(rr.feed(new)['items'],[])
            with self.assertRaises(ApiError):rr.draft(new,body(),'synthetic-preasset-key-01')
    def test_просроченные_challenges_очищаются_без_истории(self):
        aid=self.draft();self.r.prepare(self.a,aid,1,body())
        with sqlite3.connect(self.path) as db:db.execute('UPDATE assetreg_challenges SET expires_at=0')
        cleanup(self.app.store)
        self.assertEqual(len(self.r.feed(self.a)['items'][0]['history']),1)
        with self.app.store._locked_transaction() as db:self.assertEqual(db.execute('SELECT count(*) FROM assetreg_challenges').fetchone()[0],0)
    def test_миграция_не_применяется_к_пустой_или_неизвестной_базе(self):
        path=Path(self.tmp.name)/'empty.sqlite3';sqlite3.connect(path).close()
        with self.assertRaises((ValueError,sqlite3.Error,JournalUnavailable)):schema.migrate_registry(path)
        with sqlite3.connect(path) as db:self.assertEqual(db.execute('SELECT count(*) FROM sqlite_master').fetchone()[0],0)
    def test_ошибка_DDL_откатывает_всю_миграцию(self):
        with patch.dict(os.environ,{'XBLAGO_ASSET_REGISTRY_ENABLED':'false'}):cfg=initialize(Path(self.tmp.name)/'fresh')
        path=Path(cfg['XBLAGO_DATA_DIR'])/'blago.sqlite3'
        with patch.dict(os.environ,cfg),patch.object(schema,'DDL',schema.DDL+('INVALID SQL',)):
            with self.assertRaises(sqlite3.Error):schema.migrate_registry(path)
        with sqlite3.connect(path) as db:self.assertEqual(db.execute("SELECT count(*) FROM sqlite_master WHERE name LIKE 'assetreg_%'").fetchone()[0],0)
    def test_неверный_checksum_и_удалённый_trigger_блокируют_запуск(self):
        with sqlite3.connect(self.path) as db:db.execute("UPDATE assetreg_migrations SET checksum='bad'")
        with self.assertRaises(JournalUnavailable):preflight(self.path)
    def test_несколько_процессов_повторяют_один_commit(self):
        aid=self.draft();q=self.r.prepare(self.a,aid,1,body());ctx=multiprocessing.get_context('spawn');jobs=[]
        try:
            for _ in range(3):
                a,b=ctx.Pipe();p=ctx.Process(target=worker,args=(self.cfg,self.a,q['challenge'],b));p.start();b.close();jobs.append((p,a))
            responses=[]
            for p,c in jobs:
                self.assertTrue(c.poll(40));responses.append(c.recv());p.join(10);self.assertEqual(p.exitcode,0)
            self.assertTrue(all(x==responses[0] for x in responses));self.assertIn('ok',responses[0]);self.assertEqual(len(self.r.feed(self.a)['items'][0]['history']),2)
        finally:
            for p,c in jobs:
                c.close()
                if p.is_alive():p.kill();p.join()
    def test_процессы_конкурируют_за_одну_версию(self):
        aid=self.draft();qs=[self.r.prepare(self.a,aid,1,body(label=x)) for x in ('Вариант первый','Вариант второй')];ctx=multiprocessing.get_context('spawn');jobs=[]
        try:
            for q in qs:
                a,b=ctx.Pipe();p=ctx.Process(target=worker,args=(self.cfg,self.a,q['challenge'],b));p.start();b.close();jobs.append((p,a))
            results=[]
            for p,c in jobs:
                self.assertTrue(c.poll(40));results.append(c.recv());p.join(10);self.assertEqual(p.exitcode,0)
            self.assertEqual(sum('ok' in x for x in results),1);self.assertEqual(sum(x.get('denied')==409 for x in results),1)
        finally:
            for p,c in jobs:
                c.close()
                if p.is_alive():p.kill();p.join()
    def test_crash_до_journal_не_сохраняет_публикацию(self):
        aid=self.draft();q=self.r.prepare(self.a,aid,1,body());self.spawn(q['challenge'],'before_journal')
        self.assertEqual(self.r.feed(self.a)['items'][0]['version'],1);self.r.commit(self.a,q['challenge'],'ПОДТВЕРЖДАЮ')
    def test_crash_после_journal_до_SQL_commit_закрывает_запись(self):
        aid=self.draft();q=self.r.prepare(self.a,aid,1,body());self.spawn(q['challenge'],'after_journal')
        self.assertEqual(self.r.feed(self.a)['items'],[])
        with self.assertRaises(ApiError):self.r.commit(self.a,q['challenge'],'ПОДТВЕРЖДАЮ')
    def test_crash_после_commit_повтор_возвращает_ту_же_квитанцию(self):
        aid=self.draft();q=self.r.prepare(self.a,aid,1,body());self.spawn(q['challenge'],'after_commit')
        receipt=self.r.commit(self.a,q['challenge'],'ПОДТВЕРЖДАЮ');self.assertEqual(receipt['version'],2);self.assertEqual(len(self.r.feed(self.a)['items'][0]['history']),2)
    def test_G09_изменение_заявления_отклоняет_старую_генерацию(self):
        aid=self.active();g=Generation(self.app.store,self.uid)
        self.r.revoke(self.a,aid)
        with self.assertRaises(ContextConflict):g.commit()
    def test_account_merge_с_заявлениями_безопасно_отказывает(self):
        self.active();other=self.app.session(self.b)[0]
        with self.assertRaises(ApiError):
            with self.app.store._locked_transaction() as db:self.app.identity.merge(db,self.uid,other)
        self.assertEqual(len(self.r.feed(self.a)['items']),1);self.integrity()

    def test_срок_хранения_удаляет_все_версии_и_старый_backup(self):
        old=int(time.time())-91*86400
        with patch('xblago.domain.asset_registry.time.time',return_value=old):
            token=self.app.register('synthetic_expired_asset','synthetic-password-only','synthetic-help011-only')[0]
            aid=self.r.draft(token,body(),'synthetic-expired-key-01')['id']
            q=self.r.prepare(token,aid,1,body(audience='LAB_COMMUNITY'))
            self.r.commit(token,q['challenge'],'ПОДТВЕРЖДАЮ')
            backup=self.app.store.backup_database(Path(self.tmp.name)/'expired-backup')
        result=sweep(self.app.store);self.assertEqual(result['blago_assets'],1)
        self.assertEqual(self.r.feed(self.b)['items'],[])
        restored=WebApp(restore(backup,Path(self.cfg['XBLAGO_SECURITY_JOURNAL_DIR'])))
        new=restored.login('synthetic_expired_asset','synthetic-password-only')[0]
        self.assertEqual(AssetRegistry(restored).feed(new)['items'],[])
    def test_удаление_аккаунта_из_старой_копии_не_восстанавливает_версии(self):
        self.active();backup=self.app.store.backup_database(Path(self.tmp.name)/'deleted-backup')
        p=PrivacyService(self.app);q=p.preview(self.a);p.delete(self.a,q['challenge'],'УДАЛИТЬ')
        restored=WebApp(restore(backup,Path(self.cfg['XBLAGO_SECURITY_JOURNAL_DIR'])))
        with self.assertRaises(ApiError):restored.login('synthetic_asset_author','synthetic-password-only')
        with restored.store._locked_transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM assetreg_versions').fetchone()[0],0)
    def test_два_store_instances_видят_одинаковую_версию_после_restart(self):
        aid=self.active();other=AssetRegistry(WebApp(preflight(self.path)))
        self.assertEqual(self.r.feed(self.a),other.feed(self.a));other.revoke(self.a,aid)
        self.assertEqual(self.r.feed(self.b)['items'],[])
    def test_G09_другой_процесс_после_отзыва_не_записывает_ответ(self):
        from tests.test_context_fencing import held_worker
        aid=self.active();ctx=multiprocessing.get_context('spawn');a,b=ctx.Pipe()
        p=ctx.Process(target=held_worker,args=(self.cfg,self.uid,b));p.start();b.close()
        try:
            self.assertTrue(a.poll(30));self.assertTrue(a.recv()['loaded']);self.r.revoke(self.a,aid);a.send('release')
            self.assertTrue(a.poll(30));self.assertTrue(a.recv()['rejected']);p.join(10);self.assertEqual(p.exitcode,0)
            self.assertEqual(self.app.history(self.uid),[])
        finally:
            a.close()
            if p.is_alive():p.kill();p.join()
    def test_повтор_старой_квитанции_не_меняет_новую_приватность(self):
        aid=self.draft();q=self.r.prepare(self.a,aid,1,body(audience='LAB_COMMUNITY'))
        receipt=self.r.commit(self.a,q['challenge'],'ПОДТВЕРЖДАЮ')
        q2=self.r.prepare(self.a,aid,2,body());self.r.commit(self.a,q2['challenge'],'ПОДТВЕРЖДАЮ')
        self.assertEqual(self.r.commit(self.a,q['challenge'],'ПОДТВЕРЖДАЮ'),receipt)
        self.assertEqual(self.r.feed(self.b)['items'],[])
    def test_выключенный_flag_не_отключает_защиту_установленных_данных(self):
        self.active()
        with patch.dict(os.environ,{'XBLAGO_ASSET_REGISTRY_ENABLED':'false'}):
            with self.assertRaises(ApiError):self.r.feed(self.a)
            p=PrivacyService(self.app);q=p.preview(self.a);p.delete(self.a,q['challenge'],'УДАЛИТЬ')
        self.assertEqual(self.r.feed(self.b)['items'],[])
    def test_ошибка_источника_не_создаёт_оценку_или_платёж(self):
        self.active();balance=self.r.balance(self.b);self.assertIsNone(balance['btc'])
        with self.app.store._locked_transaction() as db:
            for table in ('blago_payment_requests','blago_contribution_events','help_requests'):
                self.assertEqual(db.execute('SELECT count(*) FROM '+table).fetchone()[0],0)

    def test_количество_актива_не_удваивается_при_расширении_доступности(self):
        aid=self.active();q=self.r.prepare(self.a,aid,2,body(available='2',share_consent=True,audience='LAB_COMMUNITY'))
        self.r.commit(self.a,q['challenge'],'ПОДТВЕРЖДАЮ');v=self.r.balance(self.b)
        self.assertEqual(v['registered_objects'],1);self.assertEqual(v['categories']['bicycle:item']['known_stock'],'2')
        self.assertEqual(v['categories']['bicycle:item']['available_self_declared'],'2')
    def test_нельзя_повысить_достоверность_полем_клиента(self):
        with self.assertRaises(ApiError):self.r.draft(self.a,dict(body(),evidence='INDEPENDENTLY_VERIFIED'),'synthetic-forged-evidence-01')
        with self.assertRaises(ApiError):self.r.draft(self.a,body(label='token=hf_synthetic012345678901234567890123456789'),'synthetic-sensitive-asset-01')
    def test_удалённый_защитный_trigger_блокирует_запуск(self):
        with sqlite3.connect(self.path) as db:db.execute('DROP TRIGGER assetreg_erase')
        with self.assertRaises(JournalUnavailable):preflight(self.path)
    def test_удаление_актива_инвалидирует_подготовленную_генерацию(self):
        aid=self.active();g=Generation(self.app.store,self.uid);self.r.erase(self.a,aid)
        with self.assertRaises(ContextConflict):g.commit()
    def test_удаление_версии_не_скрывает_историю_без_отказа(self):
        aid=self.active()
        with sqlite3.connect(self.path) as db:db.execute('DELETE FROM assetreg_versions WHERE asset_id=? AND version=1',(aid,))
        with self.assertRaises(JournalUnavailable):self.r.feed(self.a)
    def test_старый_backup_не_возвращает_прежнюю_аудиторию(self):
        aid=self.active();backup=self.app.store.backup_database(Path(self.tmp.name)/'audience-backup')
        q=self.r.prepare(self.a,aid,2,body());self.r.commit(self.a,q['challenge'],'ПОДТВЕРЖДАЮ')
        app=WebApp(restore(backup,Path(self.cfg['XBLAGO_SECURITY_JOURNAL_DIR'])))
        token=app.login('synthetic_asset_other','synthetic-password-only')[0]
        self.assertEqual(AssetRegistry(app).feed(token)['items'],[])

    def parallel_operations(self,commands):
        ctx=multiprocessing.get_context('spawn');jobs=[]
        try:
            for command,args in commands:
                a,b=ctx.Pipe();p=ctx.Process(target=operation_worker,args=(self.cfg,self.a,command,args,b));p.start();b.close();jobs.append((p,a))
            for p,c in jobs:self.assertTrue(c.poll(30));self.assertTrue(c.recv()['ready'])
            for p,c in jobs:c.send('go')
            results=[]
            for p,c in jobs:
                self.assertTrue(c.poll(30));results.append(c.recv());p.join(10);self.assertEqual(p.exitcode,0)
            return results
        finally:
            for p,c in jobs:
                c.close()
                if p.is_alive():p.kill();p.join()
    def test_конкурентные_черновики_имеют_один_канонический_ID(self):
        results=self.parallel_operations([('draft',(body(),'synthetic-concurrent-key-01')) for _ in range(3)])
        self.assertTrue(all(x==results[0] for x in results));self.assertIn('ok',results[0])
        with self.app.store._locked_transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM blago_assets').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count(*) FROM assetreg_versions').fetchone()[0],1)
    def test_гонка_отзыва_с_подтверждением_не_возвращает_доступность(self):
        aid=self.active();q=self.r.prepare(self.a,aid,2,body(available='2',share_consent=True,audience='LAB_COMMUNITY'))
        results=self.parallel_operations([('commit',(q['challenge'],'ПОДТВЕРЖДАЮ')),('revoke',(aid,))])
        self.assertIn('ok',results[1]);self.assertEqual(self.r.feed(self.b)['items'],[])
        self.assertEqual(self.r.feed(self.a)['items'][0]['status'],'REVOKED')
        self.integrity()
