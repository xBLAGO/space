"""Synthetic durable capital: signatures, concurrency, crash, privacy and restore."""
import copy
import errno
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import unittest
from unittest.mock import patch
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import tests.test_uel_checkpoint as checkpoint_fixture
from tools.capital_lab import setup,command,receipt,sign
from tools.capital_verify import check
from xblago.core import capital_schema
from xblago.core.integration import preflight
from xblago.core.privacy_control import PrivacyControl
from xblago.core.privacy_maintenance import restore
from xblago.core.security_journal import JournalUnavailable
from xblago.core.uel_recovery import repair_index
from xblago.domain.capital import CapitalLedger,CapitalError,digest
from xblago.integrations.web import WebApp,ApiError
from xblago.integrations.privacy import PrivacyService


def worker(cfg,token,cmd,sig,clock,pipe,phase='normal'):
    os.environ.update(cfg)
    with patch('time.time',return_value=clock):
        service=CapitalLedger(WebApp(preflight(Path(cfg['XBLAGO_DATA_DIR'])/'blago.sqlite3')))
        original=PrivacyControl.flush
        def flush(c):
            pending=c.db.execute("SELECT 1 FROM privacy_outbox WHERE json_extract(payload,'$.kind')='capital_state'").fetchone()
            if pending and phase=='before_journal':os._exit(81)
            original(c)
            if pending and phase=='after_journal':os._exit(82)
        pipe.send('ready');pipe.recv()
        try:
            with patch.object(PrivacyControl,'flush',flush):r=service.execute(token,cmd,sig)
            if phase=='after_commit':os._exit(83)
            pipe.send({'ok':r})
        except (CapitalError,ApiError,JournalUnavailable) as exc:pipe.send({'denied':type(exc).__name__})
        finally:pipe.close()


class CapitalTests(unittest.TestCase):
    def setUp(self):
        self.fixture=checkpoint_fixture.CheckpointTests();self.addCleanup(self.fixture.doCleanups);self.fixture.setUp()
        self.lab=self.fixture.lab;self.tokens=[self.lab.a,self.lab.b,self.lab.c]
        state={'app':self.lab.app,'cfg':self.lab.cfg,'tokens':self.tokens}
        self.service,self.keys,self.oracle,self.policy=setup(state);self.n=0

    def cmd(self,actor,kind,data,evidence=None):
        self.n+=1
        return command(self.service,self.tokens[actor],self.policy,actor,kind,data,'operation-'+str(self.n),evidence)

    def act(self,actor,kind,data,evidence=None):
        c=self.cmd(actor,kind,data,evidence);return self.service.execute(self.tokens[actor],c,sign(self.keys[actor],c))

    def view(self):return self.service.inspect(self.tokens[0],self.policy['id'])

    def received(self,lid='lot',value=10000,verified=True):
        self.act(0,'CapitalReceived',{'lot':lid,'amount_minor':value,'source':'source-'+lid})
        if verified:self.act(1,'CapitalVerified',{'lot':lid},receipt(self.oracle,self.policy,'RECEIVED',lid,value,'receipt-'+lid))

    def approved(self,pid='project',value=6000):
        self.act(0,'ProposalCreated',{'proposal':pid,'amount_minor':value,'purpose':'shared-tools','destination':'fixture-provider','expires_at':int(time.time())+3000})
        self.act(1,'ApprovalGranted',{'proposal':pid,'approval':'approval-'+pid})

    def reserve(self,pid='project',allocations=None):
        return self.act(2,'BudgetReserved',{'approval':'approval-'+pid,'reservation':'reserve-'+pid,'allocations':allocations or {'lot':6000}})

    def spent(self):
        return self.act(2,'CapitalSpent',{'reservation':'reserve-project'},receipt(self.oracle,self.policy,'SPENT','reserve-project',6000,'spend-receipt'))

    def test_неподтверждённое_поступление_недоступно(self):
        self.received(verified=False);self.approved()
        with self.assertRaises(CapitalError):self.reserve()
        self.assertEqual(self.view()['available_minor'],0);self.assertEqual(self.view()['unverified_minor'],10000)

    def test_полный_цикл_возврат_и_точный_баланс(self):
        self.received();self.approved();self.reserve();self.spent()
        self.act(1,'RefundConfirmed',{'reservation':'reserve-project','amount_minor':2000},receipt(self.oracle,self.policy,'REFUND','reserve-project',2000,'refund-one'))
        b=self.view();self.assertEqual((b['available_minor'],b['net_spent_minor'],b['refunded_minor']),(6000,4000,2000));self.assertEqual(b['verified_minor'],b['available_minor']+b['reserved_minor']+b['net_spent_minor'])

    def test_частичный_резерв_нескольких_источников(self):
        self.received('first',4000);self.received('second',3000);self.approved(value=9000)
        self.reserve(allocations={'first':4000,'second':2000});self.assertEqual(self.view()['available_minor'],1000)

    def test_освобождение_не_возвращает_согласование(self):
        self.received();self.approved();self.reserve();self.act(2,'ReservationReleased',{'reservation':'reserve-project'})
        with self.assertRaises(CapitalError):self.reserve()
        self.assertEqual(self.view()['available_minor'],10000)

    def test_отзыв_согласования_освобождает_резерв(self):
        self.received();self.approved();self.reserve();self.act(1,'ApprovalRevoked',{'approval':'approval-project'})
        with self.assertRaises(CapitalError):self.spent()
        self.assertEqual(self.view()['reserved_minor'],0)

    def test_нельзя_отменой_создать_возврат(self):
        self.received();self.approved();self.reserve();self.spent()
        with self.assertRaises(CapitalError):self.act(2,'ReservationReleased',{'reservation':'reserve-project'})
        with self.assertRaises(CapitalError):self.act(1,'RefundConfirmed',{'reservation':'reserve-project','amount_minor':6001},receipt(self.oracle,self.policy,'REFUND','reserve-project',6001,'refund-too-large'))
        self.assertEqual(self.view()['available_minor'],4000)

    def test_ложная_подпись_поступления(self):
        self.received(verified=False)
        evidence=receipt(Ed25519PrivateKey.generate(),self.policy,'RECEIVED','lot',10000,'forged')
        with self.assertRaises(CapitalError):self.act(1,'CapitalVerified',{'lot':'lot'},evidence)
        self.assertEqual(self.view()['verified_minor'],0)

    def test_подмена_суммы_квитанции(self):
        self.received(verified=False)
        with self.assertRaises(CapitalError):self.act(1,'CapitalVerified',{'lot':'lot'},receipt(self.oracle,self.policy,'RECEIVED','lot',9999,'wrong-amount'))

    def test_самоподтверждение_поступления_запрещено(self):
        self.received(verified=False)
        with self.assertRaises(CapitalError):self.act(0,'CapitalVerified',{'lot':'lot'},receipt(self.oracle,self.policy,'RECEIVED','lot',10000,'self'))

    def test_подмена_политики_и_подписи(self):
        self.received();c=self.cmd(0,'ProposalCreated',{'proposal':'p','amount_minor':1,'purpose':'purpose','destination':'dest','expires_at':int(time.time())+300})
        sig=sign(self.keys[0],c);c['policy_hash']='0'*64
        with self.assertRaises(CapitalError):self.service.execute(self.tokens[0],c,sig)
        with self.assertRaises(CapitalError):self.service.execute(self.tokens[0],c,sign(self.keys[0],c))

    def test_истёкшее_полномочие(self):
        c=self.cmd(0,'CapitalReceived',{'lot':'lot','amount_minor':1,'source':'source'});c['expires_at']=int(time.time())
        with self.assertRaises(CapitalError):self.service.execute(self.tokens[0],c,sign(self.keys[0],c))

    def test_истечение_согласования_до_расхода(self):
        self.received();self.approved();self.reserve()
        with patch('time.time',return_value=time.time()+3100):
            with self.assertRaises((CapitalError,ApiError)):self.spent()
        self.assertEqual(self.view()['spent_minor'],0)

    def test_отозванный_ключ_не_исполняет(self):
        self.received();self.approved();self.reserve();self.act(1,'KeyRevoked',{'key_id':'actor2'})
        with self.assertRaises(CapitalError):self.spent()

    def test_повтор_расхода_не_тратит_дважды(self):
        self.received();self.approved();self.reserve();e=receipt(self.oracle,self.policy,'SPENT','reserve-project',6000,'spent')
        c=self.cmd(2,'CapitalSpent',{'reservation':'reserve-project'},e);sig=sign(self.keys[2],c)
        a=self.service.execute(self.tokens[2],c,sig);b=self.service.execute(self.tokens[2],c,sig)
        self.assertEqual(a,b);self.assertEqual(self.view()['spent_minor'],6000)
        c['operation_id']='another'
        with self.assertRaises(CapitalError):self.service.execute(self.tokens[2],c,sign(self.keys[2],c))

    def test_дубликат_источника_не_создаёт_капитал(self):
        self.received()
        with self.assertRaises(CapitalError):self.act(0,'CapitalReceived',{'lot':'another','amount_minor':10000,'source':'source-lot'})
        self.assertEqual(self.view()['verified_minor'],10000)

    def test_подмена_участника_и_чужая_сессия(self):
        c=self.cmd(0,'CapitalReceived',{'lot':'lot','amount_minor':1,'source':'s'})
        with self.assertRaises(CapitalError):self.service.execute(self.tokens[1],c,sign(self.keys[0],c))
        with self.assertRaises(ApiError):self.service.inspect(self.lab.app.session(self.tokens[0])[0],self.policy['id'])

    def test_независимый_запуск_верификатора_и_подмена(self):
        self.received();self.approved();self.reserve();self.spent();bundle=self.service.audit(self.tokens[0],self.policy['id'])
        n=len(bundle['events']);head=digest(bundle['events'][-1]);result=check(bundle,digest(self.policy),n,head)
        self.assertEqual(result['balance']['spent_minor'],6000)
        path=self.fixture.root/'private-audit.json';path.write_text(json.dumps(bundle));path.chmod(0o600)
        verified=subprocess.run([sys.executable,'-m','tools.capital_verify',str(path),'--policy-hash',digest(self.policy),'--events',str(n),'--head',head],capture_output=True,text=True,timeout=30,check=True)
        self.assertEqual(json.loads(verified.stdout)['balance']['spent_minor'],6000)
        for field,value in [('state_hash','0'*64),('previous','0'*64)]:
            bad=copy.deepcopy(bundle);bad['events'][-1][field]=value
            with self.assertRaises(CapitalError):check(bad,digest(self.policy),n,head)
        with self.assertRaises(CapitalError):check(dict(bundle,events=bundle['events'][:-1]),digest(self.policy),n,head)

    def test_перезапуск_и_сохранение_источников(self):
        self.received();self.approved();self.reserve();before=self.view()
        other=CapitalLedger(WebApp(preflight(self.lab.path)));self.assertEqual(other.inspect(self.tokens[0],self.policy['id']),before)
        self.assertEqual(self.lab.l.balance(self.tokens[0])['registered_asset_claims'],0)

    def test_повторная_миграция_и_integrity(self):
        self.received();self.assertFalse(capital_schema.migrate(self.lab.path))
        with sqlite3.connect(self.lab.path) as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok');self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_ошибка_проекции_откатывает_операцию(self):
        c=self.cmd(0,'CapitalReceived',{'lot':'lot','amount_minor':100,'source':'source'})
        with patch.object(self.service,'_emit',side_effect=OSError(errno.ENOSPC,'synthetic full disk')):
            with self.assertRaises(OSError):self.service.execute(self.tokens[0],c,sign(self.keys[0],c))
        self.assertEqual(self.view()['declared_minor'],0)

    def test_повреждение_источника_закрывает_доступ(self):
        self.received()
        with sqlite3.connect(self.lab.path) as db:db.execute("UPDATE capital_funds SET state='{}'")
        with self.assertRaises(JournalUnavailable):self.view()

    def test_удаление_и_старый_backup_не_возвращают_данные(self):
        self.received();backup=self.lab.app.store.backup_database(self.fixture.root/'capital-backup')
        self.service.erase(self.tokens[0],self.policy['id'],'УДАЛИТЬ СИНТЕТИЧЕСКИЙ ФОНД')
        with self.assertRaises(ApiError):self.view()
        with backup.open('rb') as source,self.lab.path.open('wb') as dest:dest.write(source.read())
        restore(self.lab.path,self.lab.journal)
        with sqlite3.connect(self.lab.path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM capital_funds').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM capital_members').fetchone()[0],0)
            self.assertGreater(db.execute('SELECT count(*) FROM capital_holds').fetchone()[0],0)

    def test_удаление_аккаунта_закрывает_связанный_фонд(self):
        self.received();p=PrivacyService(self.lab.app);preview=p.preview(self.tokens[0]);p.delete(self.tokens[0],preview['challenge'],'УДАЛИТЬ')
        with sqlite3.connect(self.lab.path) as db:self.assertEqual(db.execute('SELECT count(*) FROM capital_members').fetchone()[0],0)

    def test_потеря_внешнего_checkpoint_закрывает_капитал(self):
        self.received();self.fixture.stop_server()
        with self.assertRaises(JournalUnavailable):self.view()

    def _workers(self,commands,phase='normal'):
        ctx=multiprocessing.get_context('spawn');jobs=[]
        for cmd in commands:
            actor=int(cmd['key_id'].removeprefix('actor'))
            a,b=ctx.Pipe();p=ctx.Process(target=worker,args=(self.lab.cfg,self.tokens[actor],cmd,sign(self.keys[actor],cmd),self.lab.frozen_at,b,phase));p.start()
            if not a.poll(30):
                p.terminate();p.join(10);self.fail('Worker did not become ready')
            self.assertEqual(a.recv(),'ready');jobs.append((p,a))
        for p,a in jobs:a.send('go')
        result=[]
        for p,a in jobs:
            p.join(30)
            if p.is_alive():p.terminate();p.join();self.fail('Worker timeout')
            result.append(a.recv() if p.exitcode==0 else {'crash':p.exitcode});a.close()
        return result

    def test_два_процесса_не_резервируют_дважды(self):
        self.received();self.approved('first');self.approved('second')
        c1=self.cmd(2,'BudgetReserved',{'approval':'approval-first','reservation':'first','allocations':{'lot':6000}})
        c2=self.cmd(2,'BudgetReserved',{'approval':'approval-second','reservation':'second','allocations':{'lot':6000}})
        result=self._workers([c1,c2]);self.assertEqual(sum('ok' in r for r in result),1);self.assertEqual(self.view()['reserved_minor'],6000)

    def test_три_процесса_повторяют_один_резерв(self):
        self.received();self.approved();c=self.cmd(2,'BudgetReserved',{'approval':'approval-project','reservation':'one','allocations':{'lot':6000}})
        result=self._workers([c,c,c]);self.assertEqual(sum('ok' in r for r in result),3);self.assertEqual(self.view()['reserved_minor'],6000)

    def test_crash_до_журнала_откатывает_резерв(self):
        self.received();self.approved();c=self.cmd(2,'BudgetReserved',{'approval':'approval-project','reservation':'one','allocations':{'lot':6000}})
        self.assertEqual(self._workers([c],'before_journal'),[{'crash':81}]);self.assertEqual(self.view()['reserved_minor'],0)

    def test_crash_после_commit_возвращает_исходную_квитанцию(self):
        self.received();self.approved();c=self.cmd(2,'BudgetReserved',{'approval':'approval-project','reservation':'one','allocations':{'lot':6000}})
        self.assertEqual(self._workers([c],'after_commit'),[{'crash':83}]);self.assertEqual(self.service.execute(self.tokens[2],c,sign(self.keys[2],c))['status'],'COMMITTED');self.assertEqual(self.view()['reserved_minor'],6000)

    def test_crash_после_журнала_не_восстанавливает_разрешение(self):
        self.received();self.approved();c=self.cmd(2,'BudgetReserved',{'approval':'approval-project','reservation':'one','allocations':{'lot':6000}})
        self.assertEqual(self._workers([c],'after_journal'),[{'crash':82}])
        with self.assertRaises(ApiError):self.view()
        with sqlite3.connect(self.lab.path) as db:self.assertGreater(db.execute('SELECT count(*) FROM capital_holds').fetchone()[0],0)

    def test_ремонт_индекса_не_меняет_капитал(self):
        self.received();before=self.view()
        with sqlite3.connect(self.lab.path) as db:
            db.execute('DROP TRIGGER uel_append_only_delete');db.execute('DELETE FROM uel_events WHERE sequence=(SELECT max(sequence) FROM uel_events)')
        with self.assertRaises(JournalUnavailable):self.view()
        repair_index(self.lab.path);self.assertEqual(self.view(),before)

    def test_флаг_выключен_но_удаление_сохраняется(self):
        self.received()
        with patch.dict(os.environ,{'XBLAGO_CAPITAL_LAB_ENABLED':'false'}):
            with self.assertRaises(ApiError):self.view()
            p=PrivacyService(self.lab.app);preview=p.preview(self.tokens[0]);p.delete(self.tokens[0],preview['challenge'],'УДАЛИТЬ')
        with sqlite3.connect(self.lab.path) as db:self.assertEqual(db.execute('SELECT count(*) FROM capital_members').fetchone()[0],0)

    def test_отзыв_и_расход_сериализованы(self):
        self.received();self.approved();self.reserve()
        revoke=self.cmd(1,'ApprovalRevoked',{'approval':'approval-project'})
        spend=self.cmd(2,'CapitalSpent',{'reservation':'reserve-project'},receipt(self.oracle,self.policy,'SPENT','reserve-project',6000,'race-spend'))
        result=self._workers([revoke,spend]);self.assertEqual(sum('ok' in r for r in result),1)
        b=self.view();self.assertIn(b['spent_minor'],(0,6000));self.assertEqual(b['reserved_minor'],0)
        if b['spent_minor']==0:
            with self.assertRaises(CapitalError):self.spent()

    def test_коллизия_ключа_повтора(self):
        c=self.cmd(0,'CapitalReceived',{'lot':'one','amount_minor':1,'source':'s-one'});self.service.execute(self.tokens[0],c,sign(self.keys[0],c))
        c['data']['amount_minor']=2
        with self.assertRaises(CapitalError):self.service.execute(self.tokens[0],c,sign(self.keys[0],c))
        self.assertEqual(self.view()['declared_minor'],1)

    def test_экспорт_не_раскрывает_чужие_привязки(self):
        data=PrivacyService(self.lab.app).export(self.tokens[0]);body=json.dumps(data)
        for token in self.tokens:self.assertNotIn(token,body)
        self.assertIn('synthetic_capital_membership',body)
        self.assertNotIn(self.policy['members']['actor1']['public_key'],body)

    def test_отозванный_автор_не_даёт_новых_полномочий(self):
        self.received();self.approved();self.act(1,'KeyRevoked',{'key_id':'actor0'})
        with self.assertRaises(CapitalError):self.reserve()


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture=checkpoint_fixture.CheckpointTests();self.addCleanup(self.fixture.doCleanups);self.fixture.setUp();self.lab=self.fixture.lab

    def test_миграция_сохраняет_старую_историю_и_checksums(self):
        from xblago.core import uel_schema
        self.lab.asset()
        with sqlite3.connect(self.lab.path) as db:
            prior=db.execute('SELECT * FROM uel_events ORDER BY sequence').fetchall();checksum=db.execute('SELECT * FROM uel_migrations').fetchall()
        self.assertTrue(capital_schema.migrate(self.lab.path));self.assertFalse(capital_schema.migrate(self.lab.path));self.assertFalse(uel_schema.migrate_local(self.lab.path))
        with sqlite3.connect(self.lab.path) as db:
            self.assertEqual(prior,db.execute('SELECT * FROM uel_events ORDER BY sequence').fetchall());self.assertEqual(checksum,db.execute('SELECT * FROM uel_migrations').fetchall());self.assertEqual(checksum,[(uel_schema.VERSION,uel_schema.CHECKSUM)])

    def test_ошибка_DDL_возвращает_старую_схему(self):
        from xblago.core import uel_schema
        with patch.object(capital_schema,'DDL',capital_schema.DDL+('INVALID SYNTHETIC DDL',)):
            with self.assertRaises(sqlite3.Error):capital_schema.migrate(self.lab.path)
        with sqlite3.connect(self.lab.path) as db:
            self.assertFalse(capital_schema.installed(db));self.assertTrue(uel_schema.validate(db,mandatory=True));self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
