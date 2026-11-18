"""09.3 synthetic custody contracts. No claim of independent operators."""
import ast
import copy
import errno
import json
import multiprocessing
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from experiments.capital_09_3 import runtime as r
from tools.capital_custody_lab import source_fixture,cluster,approvals,recover
from tools.capital_custody_verify import verify_archive,Probe
from tools.independent_capital_verifier import InvalidProof


def concurrent_prepare(root,key,control,pin,package,pipe):
    os.environ.update(XBLAGO_CAPITAL_CUSTODY_LAB_ENABLED='true',XBLAGO_INTEGRATION_MODE='synthetic')
    try:pipe.send(r.Node(root,key,control,pin).prepare(package))
    except Exception as e:pipe.send(type(e).__name__)
    finally:pipe.close()


def crash_prepare(root,key,control,pin,package,phase):
    os.environ.update(XBLAGO_CAPITAL_CUSTODY_LAB_ENABLED='true',XBLAGO_INTEGRATION_MODE='synthetic')
    def fault(stage):
        if stage==phase:os._exit(79)
    r.Node(root,key,control,pin).prepare(package,fault)


class CustodyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.fixture=source_fixture(100)
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.env=patch.dict(os.environ,{'XBLAGO_CAPITAL_CUSTODY_LAB_ENABLED':'true','XBLAGO_INTEGRATION_MODE':'synthetic'});self.env.start();self.addCleanup(self.env.stop)
        self.nodes,self.keys,self.pin=cluster(self.temp.name,self.fixture);self.t=r.Transport(self.nodes);self.packages=copy.deepcopy(self.fixture['packages']);self.now=self.fixture['at']
    def checkpoint(self,index=0):return r.replicate(self.t,self.packages[index])
    def floor(self):
        a=self.nodes['B'].export();return {'index':len(a['entries']),'tip':r.tip(a),'epoch':a['config']['epoch'],'config':r.digest(a['config'])}
    def inspect(self,archive=None,floor=None):return verify_archive(archive or self.nodes['B'].export(),self.pin,floor or self.floor())
    def rotation(self):
        a=self.nodes['B'].export();nodes=copy.deepcopy(a['config']['nodes']);nodes['A']=r.public(Ed25519PrivateKey.generate());b=r.rotation_body(a,nodes,self.now)
        return b,approvals(b,self.fixture['humans'],'rotation-approval')
    def test_100_участников_10_млн_и_три_копии(self):
        for i in range(3):self.checkpoint(i)
        self.assertEqual(self.inspect()['balance']['verified_minor'],1000000000)
        self.assertEqual(self.nodes['A'].export(),self.nodes['C'].export())
    def test_повтор_доставки_тот_же_сертификат(self):
        c=self.checkpoint();self.assertEqual(self.checkpoint(),c);self.assertEqual(len(self.nodes['B'].export()['entries']),1)
    def test_одиночная_подпись_не_закрепляет(self):
        v=self.nodes['A'].prepare(self.packages[0]);c={'body':v['body'],'votes':{'A':v},'package':self.packages[0]}
        with self.assertRaises(r.Rejected):self.nodes['A'].commit(c)
    def test_два_из_трех_не_обычный_кворум(self):
        vs={k:self.nodes[k].prepare(self.packages[0]) for k in ('A','B')};c={'body':vs['A']['body'],'votes':vs,'package':self.packages[0]}
        with self.assertRaises(r.Rejected):self.nodes['B'].commit(c)
    def test_разделение_сети_не_даёт_ACK(self):
        self.t.offline.add('C')
        with self.assertRaises(ConnectionError):self.checkpoint()
        self.assertTrue(self.nodes['A'].status('a'*64,self.now)['body']['pending'])
    def test_восстановление_сети_завершает_тот_же_prepare(self):
        self.t.offline.add('C')
        with self.assertRaises(ConnectionError):self.checkpoint()
        self.t.offline.clear();self.checkpoint();self.assertEqual(self.inspect()['index'],1)
    def test_конфликтующий_prepare_отклонён(self):
        self.nodes['B'].prepare(self.packages[0])
        with self.assertRaises(r.Rejected):self.nodes['B'].prepare(self.packages[1])
    def test_подмена_подписи_проверяется_независимо(self):
        self.checkpoint();a=self.nodes['B'].export();a['entries'][0]['votes']['C']['signature']='00'*64
        with self.assertRaises(InvalidProof):self.inspect(a)
    def test_усечение_ниже_внешней_вершины(self):
        self.checkpoint();old=self.nodes['B'].export();self.checkpoint(1)
        with self.assertRaises(InvalidProof):self.inspect(old,self.floor())
    def test_потеря_внешней_вершины_UNKNOWN(self):
        self.checkpoint()
        with self.assertRaises(InvalidProof):verify_archive(self.nodes['B'].export(),self.pin,None)
    def test_неправильный_genesis_pin(self):
        self.checkpoint()
        with self.assertRaises(InvalidProof):verify_archive(self.nodes['B'].export(),'0'*64,self.floor())
    def test_история_и_UEL_не_подменяются(self):
        self.checkpoint();bad=copy.deepcopy(self.packages[1]);bad['proof']['history']['events'][0]['fund']='other'
        with self.assertRaises(Exception):self.nodes['B'].prepare(bad)
    def test_свежесть_без_исходного_сервера(self):
        self.checkpoint();self.nodes['A'].key_file.unlink();nonce=secrets.token_hex(32)
        p=Probe(self.pin,self.floor(),self.now,nonce);replies={k:self.nodes[k].status(nonce,self.now) for k in ('B','C')}
        self.assertEqual(p.verify(self.nodes['B'].export(),replies,self.now)['freshness'],'TWO_PINNED_NODES_NONCE_BOUND')
        with self.assertRaises(InvalidProof):p.verify(self.nodes['B'].export(),replies,self.now)
    def test_подмена_nonce_статуса(self):
        self.checkpoint();p=Probe(self.pin,self.floor(),self.now,'a'*64)
        with self.assertRaises(InvalidProof):p.verify(self.nodes['B'].export(),{k:self.nodes[k].status('b'*64,self.now) for k in ('B','C')},self.now)
    def test_истекший_статус(self):
        self.checkpoint();p=Probe(self.pin,self.floor(),self.now,'a'*64)
        with self.assertRaises(InvalidProof):p.verify(self.nodes['B'].export(),{k:self.nodes[k].status('a'*64,self.now) for k in ('B','C')},self.now+31)
    def test_один_статус_не_доказывает_свежесть(self):
        self.checkpoint();p=Probe(self.pin,self.floor(),self.now,'a'*64)
        with self.assertRaises(InvalidProof):p.verify(self.nodes['B'].export(),{'B':self.nodes['B'].status('a'*64,self.now)},self.now)
    def test_незавершенная_доставка_не_скрывается_статусом(self):
        self.checkpoint();a=self.nodes['B'].export();f=self.floor();self.nodes['B'].prepare(self.packages[1]);p=Probe(self.pin,f,self.now,'a'*64)
        with self.assertRaises(InvalidProof):p.verify(a,{k:self.nodes[k].status('a'*64,self.now) for k in ('B','C')},self.now)
    def test_подписанный_старый_статус_не_подходит(self):
        self.checkpoint();f=self.floor();old=self.nodes['C'].status('a'*64,self.now);self.checkpoint(1);p=Probe(self.pin,f,self.now,'a'*64)
        with self.assertRaises(InvalidProof):p.verify(self.nodes['B'].export(),{'B':self.nodes['B'].status('a'*64,self.now),'C':old},self.now)
    def test_меньшинство_людей_не_меняет_ключи(self):
        self.checkpoint();b,a=self.rotation()
        with self.assertRaises(r.Rejected):self.nodes['B'].prepare_rotation(b,dict(list(a.items())[:3]),self.now)
    def test_один_оператор_не_меняет_ключи(self):
        self.checkpoint();b,a=self.rotation();v=self.nodes['B'].prepare_rotation(b,a,self.now)
        c={'body':b,'approvals':a,'votes':{'B':v},'activated_at':self.now+60}
        with self.assertRaises(r.Rejected):self.nodes['B'].activate_rotation(c,self.now+60)
    def test_задержка_восстановления_обязательна(self):
        self.checkpoint();b,a=self.rotation();v={k:self.nodes[k].prepare_rotation(b,a,self.now) for k in ('B','C')};c={'body':b,'approvals':a,'votes':v,'activated_at':self.now+59}
        with self.assertRaises(r.Rejected):self.nodes['B'].activate_rotation(c,self.now+59)
    def test_истекшее_согласование_не_активируется(self):
        self.checkpoint();b,a=self.rotation();v={k:self.nodes[k].prepare_rotation(b,a,self.now) for k in ('B','C')};c={'body':b,'approvals':a,'votes':v,'activated_at':self.now+600}
        with self.assertRaises(r.Rejected):self.nodes['B'].activate_rotation(c,self.now+600)
    def test_отказ_A_потеря_ключа_ротация_и_новая_копия(self):
        self.checkpoint();f=self.floor();before=self.inspect();self.nodes['A'].key_file.unlink();new,c=recover(self.nodes,self.keys,self.pin,self.fixture,self.now)
        after=verify_archive(new.export(),self.pin,f);self.assertEqual(after['balance'],before['balance']);self.assertEqual(after['epoch'],2);self.assertFalse(after['spending_authority_restored'])
    def test_старый_узел_после_ротации_не_текущий(self):
        self.checkpoint();old=self.nodes['A'].export();new,c=recover(self.nodes,self.keys,self.pin,self.fixture,self.now);f={'index':1,'tip':r.tip(new.export()),'epoch':2,'config':r.digest(new.export()['config'])}
        with self.assertRaises(InvalidProof):self.inspect(old,f)
    def test_ротация_не_меняет_ключи_CapitalLedger(self):
        self.checkpoint();b,a=self.rotation();b['new_config']['origin_pins']['node_key']=r.public(Ed25519PrivateKey.generate());a=approvals(b,self.fixture['humans'],'rotation-approval')
        with self.assertRaises(r.Rejected):self.nodes['B'].prepare_rotation(b,a,self.now)
    def test_ротация_с_неразрешенной_историей_запрещена(self):
        self.checkpoint();b,a=self.rotation();self.nodes['B'].prepare(self.packages[1])
        with self.assertRaises(r.Rejected):self.nodes['B'].prepare_rotation(b,a,self.now)
    def test_параллельный_prepare_между_процессами(self):
        n=self.nodes['B'];ctx=multiprocessing.get_context('spawn');jobs=[]
        for i in range(2):
            parent,child=ctx.Pipe();p=ctx.Process(target=concurrent_prepare,args=(str(n.root),str(n.key_file),str(n.control),self.pin,self.packages[0],child));p.start();child.close();jobs.append((p,parent))
        results=[]
        for p,pipe in jobs:
            self.assertTrue(pipe.poll(30));results.append(pipe.recv());p.join(30);self.assertEqual(p.exitcode,0)
        self.assertEqual(results[0],results[1])
    def test_crash_до_commit_не_подписывает(self):
        self.crash('before_commit');self.checkpoint();self.assertEqual(self.inspect()['index'],1)
    def crash(self,phase):
        n=self.nodes['B'];ctx=multiprocessing.get_context('spawn');p=ctx.Process(target=crash_prepare,args=(str(n.root),str(n.key_file),str(n.control),self.pin,self.packages[0],phase));p.start();p.join(30);self.assertEqual(p.exitcode,79)
    def test_crash_после_commit_до_control_UNKNOWN(self):
        self.crash('after_commit')
        with self.assertRaises(r.UnknownRecovery):self.nodes['B'].export()
    def test_crash_после_control_replay_того_же_голоса(self):
        self.crash('after_control');self.checkpoint();self.assertEqual(self.inspect()['index'],1)
    def test_полный_диск_до_commit_без_голоса(self):
        def fault(phase):raise OSError(errno.ENOSPC,'synthetic full disk')
        with self.assertRaises(OSError):self.nodes['B'].prepare(self.packages[0],fault)
        self.checkpoint();self.assertEqual(self.inspect()['index'],1)
    def test_ошибка_контрольного_файла_после_commit_hold(self):
        with patch.object(r,'atomic',side_effect=OSError(errno.EIO,'synthetic IO')):
            with self.assertRaises(OSError):self.nodes['B'].prepare(self.packages[0])
        with self.assertRaises(r.UnknownRecovery):self.nodes['B'].export()
    def test_откат_только_SQLite_обнаружен(self):
        n=self.nodes['B'];old=(n.root/'custody.sqlite3').read_bytes();self.checkpoint();(n.root/'custody.sqlite3').write_bytes(old)
        with self.assertRaises(r.UnknownRecovery):n.export()
    def test_согласованный_локальный_откат_обнаружен_другими(self):
        n=self.nodes['B'];db=(n.root/'custody.sqlite3').read_bytes();ctl=n.control.read_bytes();self.checkpoint();f=self.floor();(n.root/'custody.sqlite3').write_bytes(db);n.control.write_bytes(ctl)
        with self.assertRaises(InvalidProof):verify_archive(n.export(),self.pin,f)
    def test_потеря_control_не_считается_пустой_историей(self):
        self.nodes['B'].control.unlink()
        with self.assertRaises(r.UnknownRecovery):self.nodes['B'].export()
    def test_выключенный_flag_не_создает_БД(self):
        with patch.dict(os.environ,{'XBLAGO_CAPITAL_CUSTODY_LAB_ENABLED':'false'}):
            with self.assertRaises(r.Rejected):self.nodes['B'].export()
    def test_неподтвержденный_архив_не_импортируется(self):
        self.checkpoint();a=self.nodes['B'].export();a['entries'][0]['votes'].pop('A')
        with self.assertRaises(r.Rejected):r.Node.create(Path(self.temp.name)/'bad',self.nodes['B'].key_file,Path(self.temp.name)/'bad-head','B',a,genesis_pin=self.pin)
    def test_новый_верификатор_не_импортирует_runtime(self):
        p=Path('tools/capital_custody_verify.py');tree=ast.parse(p.read_text())
        for n in ast.walk(tree):
            if isinstance(n,ast.ImportFrom):self.assertFalse((n.module or '').startswith(('xblago','experiments')))
    def test_согласованный_откат_трех_копий_не_обходит_внешний_floor(self):
        saved={k:((n.root/'custody.sqlite3').read_bytes(),n.control.read_bytes()) for k,n in self.nodes.items()}
        self.checkpoint();f=self.floor()
        for k,n in self.nodes.items():(n.root/'custody.sqlite3').write_bytes(saved[k][0]);n.control.write_bytes(saved[k][1])
        with self.assertRaises(InvalidProof):verify_archive(self.nodes['B'].export(),self.pin,f)
    def test_сговор_двух_узлов_без_третьего_не_дает_checkpoint(self):
        vs={k:self.nodes[k].prepare(self.packages[0]) for k in ('A','C')};c={'body':vs['A']['body'],'votes':vs,'package':self.packages[0]}
        with self.assertRaises(r.Rejected):self.nodes['A'].commit(c)
    def test_потерянный_commit_ACK_после_control_идемпотентен(self):
        votes={k:n.prepare(self.packages[0]) for k,n in self.nodes.items()};c={'body':votes['A']['body'],'votes':votes,'package':self.packages[0]}
        def fail(stage):
            if stage=='after_control':raise OSError('lost ACK')
        with self.assertRaises(OSError):self.nodes['B'].commit(c,fail)
        self.assertEqual(self.nodes['B'].commit(c),r.digest(c))
    def test_неверный_floor_config_не_проходит(self):
        self.checkpoint();f=self.floor();f['config']='0'*64
        with self.assertRaises(InvalidProof):verify_archive(self.nodes['B'].export(),self.pin,f)
    def test_bool_не_заменяет_счетчик(self):
        self.checkpoint();a=self.nodes['B'].export();a['entries'][0]['body']['index']=True
        # Even three synthetic signatures must not redefine the frozen schema.
        a['entries'][0]['votes']={k:r.signed(a['entries'][0]['body'],key,'checkpoint') for k,key in self.keys.items()}
        with self.assertRaises(InvalidProof):verify_archive(a,self.pin,self.floor())
    def test_поздний_prepare_не_обходит_60_секунд(self):
        self.checkpoint();b,a=self.rotation();votes={k:self.nodes[k].prepare_rotation(b,a,self.now+60) for k in ('B','C')};c={'body':b,'approvals':a,'votes':votes,'activated_at':self.now+60}
        with self.assertRaises(r.Rejected):self.nodes['B'].activate_rotation(c,self.now+60)
    def test_ротация_разрешена_после_позднего_durable_delay(self):
        self.checkpoint();b,a=self.rotation();votes={k:self.nodes[k].prepare_rotation(b,a,self.now+60) for k in ('B','C')};c={'body':b,'approvals':a,'votes':votes,'activated_at':self.now+120}
        for k in ('B','C'):self.nodes[k].activate_rotation(c,self.now+121)
        self.assertEqual(self.inspect()['epoch'],2)
    def test_один_ключ_человека_не_три_узла(self):
        self.checkpoint();b,a=self.rotation();key=next(iter(self.fixture['humans'].values()));b['new_config']['nodes']['A']=r.public(key);a=approvals(b,self.fixture['humans'],'rotation-approval')
        with self.assertRaises(r.Rejected):self.nodes['B'].prepare_rotation(b,a,self.now)
    def test_другая_схема_или_профиль_отклоняется(self):
        self.checkpoint();a=self.nodes['B'].export();a['config']['profile']='other'
        with self.assertRaises(InvalidProof):verify_archive(a,self.pin,self.floor())
    def test_прежние_проверенные_форматы_не_изменены(self):
        import subprocess
        changed=subprocess.check_output(['git','diff','4706a60e7ec675ed1ae16b9c9cd551cde291cfd3','--name-only','--','src','docs/uel01','docs/governance092'],text=True)
        self.assertEqual(changed,'')
