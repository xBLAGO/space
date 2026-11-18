"""Synthetic governance contracts, isolation, independent implementation and faults."""
import ast
import copy
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
from tools.foundation09_lab import lab,PASSWORD
from tools.governance_lab import setup,command,proof_export
from tools.capital_lab import sign,receipt
from tools.independent_capital_verifier import verify_bundle,InvalidProof,Replay
from xblago.core import governance_schema,capital_schema
from xblago.core.privacy_control import PrivacyControl
from xblago.core.privacy_maintenance import restore
from xblago.core.integration import preflight
from xblago.core.security_journal import JournalUnavailable
from xblago.domain import capital as cap,capital_governance as gov
from xblago.integrations.web import WebApp,ApiError
from xblago.integrations.privacy import PrivacyService


def base_policy(keys,oracle,node):
    members={}
    for i,k in enumerate(keys):
        roles=['VIEW']+(['DECLARE','PROPOSE'] if i==0 else ['EXECUTE'] if i==2 else [])
        if i in (1,3,4):roles+=['APPROVE','VERIFY']
        if i in (1,3,4,5,6):roles+=['GOV']
        members[f'actor{i:03d}']={'public_key':k.public_key().public_bytes_raw().hex(),'roles':roles,'expires_at':int(time.time())+7200}
    return {'format':gov.FORMAT,'id':'pure-governance','version':1,'currency':'RUB','members':members,'evidence_key':oracle.public_key().public_bytes_raw().hex(),'privacy_key':node.public_key().public_bytes_raw().hex(),
            'governors':sorted(k for k,v in members.items() if 'GOV' in v['roles']),'governance_quorum':4,'spend_quorum':2,'activation_delay':60,'delegation_seconds':300}


class PureGovernanceTests(unittest.TestCase):
    def setUp(self):
        self.keys=[Ed25519PrivateKey.generate() for _ in range(8)];self.oracle=Ed25519PrivateKey.generate();self.node=Ed25519PrivateKey.generate();self.policy=base_policy(self.keys,self.oracle,self.node)
        self.state=cap.empty(self.policy);self.version=1;self.now=int(time.time());self.reference=Replay(self.policy)

    def act(self,actor,kind,data,evidence=None):
        c={'fund':self.state['policy']['id'],'version':self.version,'policy_hash':cap.digest(self.state['policy']),'operation_id':'op'+str(self.version),'key_id':f'actor{actor:03d}','type':kind,'expires_at':self.now+1000,'data':data,'evidence':evidence}
        key=self.node if kind=='PrivacyMemberExited' else self.keys[actor];domain=gov.PRIVACY_DOMAIN if kind=='PrivacyMemberExited' else gov.DOMAIN
        signature=sign(key,c,domain)
        try:next_state=cap.transition(self.state,c,signature,self.now)
        except cap.CapitalError:
            other=copy.deepcopy(self.reference)
            with self.assertRaises(InvalidProof):other.apply(c,signature,self.now)
            raise
        self.reference.apply(c,signature,self.now)
        self.assertEqual(next_state,self.reference.s,'Independent implementation differs')
        self.state=next_state;self.version+=1
        return c

    def received(self):
        self.act(0,'CapitalReceived',{'lot':'lot','source':'fixture','amount_minor':10000})
        self.act(1,'CapitalVerified',{'lot':'lot'},receipt(self.oracle,self.policy,'RECEIVED','lot',10000,'received'))

    def proposed(self):self.act(0,'ProposalCreated',{'proposal':'p','amount_minor':6000,'purpose':'tools','destination':'fixture','expires_at':self.now+600})
    def vote(self,actor=1,delegation=None):
        d={'proposal':'p','approval':'a'}
        if delegation:d['delegation']=delegation
        self.act(actor,'ApprovalGranted',d)
    def reserve(self):self.act(2,'BudgetReserved',{'approval':'a','reservation':'r','allocations':{'lot':6000}})
    def spend(self):self.act(2,'CapitalSpent',{'reservation':'r'},receipt(self.oracle,self.policy,'SPENT','r',6000,'spent'))
    def prepared(self):self.received();self.proposed();self.vote(1);self.vote(3);self.reserve()
    def next_policy(self):p=copy.deepcopy(self.policy);p['version']=2;p['delegation_seconds']=240;return p
    def policy_proposed(self):self.act(1,'PolicyProposed',{'proposal':'policy','policy':self.next_policy()})

    def test_одного_согласования_недостаточно(self):
        self.received();self.proposed();self.vote()
        with self.assertRaises(cap.CapitalError):self.reserve()
        self.assertEqual(cap.balance(self.state)[0]['reserved_minor'],0)

    def test_два_независимых_согласования_и_расход(self):
        self.prepared();self.spend();self.assertEqual(cap.balance(self.state)[0]['spent_minor'],6000)

    def test_повтор_голоса_не_увеличивает_кворум(self):
        self.proposed();self.vote()
        with self.assertRaises(cap.CapitalError):self.vote()

    def test_нельзя_делегировать_голос_управления(self):
        self.proposed()
        with self.assertRaises(cap.CapitalError):self.act(5,'DelegationGranted',{'delegation':'d','recipient':'actor007','proposal':'p','max_amount_minor':6000,'expires_at':self.now+120})

    def test_делегирование_ограничено_предложением(self):
        self.received();self.proposed();self.act(1,'DelegationGranted',{'delegation':'d','recipient':'actor007','proposal':'p','max_amount_minor':6000,'expires_at':self.now+120})
        self.vote(7,'d');self.vote(3);self.reserve();self.spend()

    def test_один_подписант_не_заменяет_двух_участников(self):
        self.proposed()
        for actor,did in ((1,'first'),(3,'second')):self.act(actor,'DelegationGranted',{'delegation':did,'recipient':'actor007','proposal':'p','max_amount_minor':6000,'expires_at':self.now+120})
        self.vote(7,'first')
        with self.assertRaises(cap.CapitalError):self.vote(7,'second')

    def test_делегатор_не_голосует_повторно_после_представителя(self):
        self.proposed();self.act(1,'DelegationGranted',{'delegation':'d','recipient':'actor007','proposal':'p','max_amount_minor':6000,'expires_at':self.now+120});self.vote(7,'d')
        with self.assertRaises(cap.CapitalError):self.vote(1)

    def test_нет_цепочки_делегирования(self):
        self.proposed();self.act(1,'DelegationGranted',{'delegation':'d','recipient':'actor007','proposal':'p','max_amount_minor':6000,'expires_at':self.now+120})
        with self.assertRaises(cap.CapitalError):self.act(7,'DelegationGranted',{'delegation':'chain','recipient':'actor006','proposal':'p','max_amount_minor':6000,'expires_at':self.now+60})

    def test_отзыв_делегирования_освобождает_резерв(self):
        self.received();self.proposed();self.act(1,'DelegationGranted',{'delegation':'d','recipient':'actor007','proposal':'p','max_amount_minor':6000,'expires_at':self.now+120});self.vote(7,'d');self.vote(3);self.reserve()
        self.act(1,'DelegationRevoked',{'delegation':'d'})
        with self.assertRaises(cap.CapitalError):self.spend()
        self.assertEqual(cap.balance(self.state)[0]['reserved_minor'],0)

    def test_истечение_делегирования_блокирует_расход(self):
        self.received();self.proposed();self.act(1,'DelegationGranted',{'delegation':'d','recipient':'actor007','proposal':'p','max_amount_minor':6000,'expires_at':self.now+60});self.vote(7,'d');self.vote(3);self.reserve();self.now+=61
        with self.assertRaises(cap.CapitalError):self.spend()

    def test_отзыв_одного_голоса_не_отменяет_прошлый_расход(self):
        self.prepared();self.spend();self.act(1,'ApprovalRevoked',{'approval':'a','principal':'actor001'});self.assertEqual(cap.balance(self.state)[0]['spent_minor'],6000)

    def test_отзыв_голоса_перед_расходом(self):
        self.prepared();self.act(1,'ApprovalRevoked',{'approval':'a','principal':'actor001'})
        with self.assertRaises(cap.CapitalError):self.spend()

    def test_выход_участника_сохраняет_капитал_остальных(self):
        self.prepared();self.act(7,'MemberExited',{});self.assertEqual(cap.balance(self.state)[0]['reserved_minor'],6000);self.spend()

    def test_выход_согласующего_отзывает_его_голос(self):
        self.prepared();self.act(1,'MemberExited',{});self.assertEqual(cap.balance(self.state)[0]['available_minor'],10000)
        with self.assertRaises(cap.CapitalError):self.spend()

    def test_выход_не_снижает_фиксированный_кворум(self):
        self.policy_proposed();self.act(6,'MemberExited',{})
        for a in (1,3,4):self.act(a,'PolicyVoted',{'proposal':'policy'})
        self.now+=61
        with self.assertRaises(cap.CapitalError):self.act(1,'PolicyActivated',{'proposal':'policy'})

    def test_политика_требует_сверхкворума_и_задержки(self):
        self.policy_proposed()
        for a in (1,3,4,5):self.act(a,'PolicyVoted',{'proposal':'policy'})
        with self.assertRaises(cap.CapitalError):self.act(1,'PolicyActivated',{'proposal':'policy'})
        self.now+=61;self.act(1,'PolicyActivated',{'proposal':'policy'});self.assertEqual(self.state['policy']['version'],2)

    def test_отзыв_голоса_управления_сбрасывает_задержку(self):
        self.policy_proposed()
        for a in (1,3,4,5):self.act(a,'PolicyVoted',{'proposal':'policy'})
        self.act(5,'PolicyVoteRevoked',{'proposal':'policy'});self.now+=61
        with self.assertRaises(cap.CapitalError):self.act(1,'PolicyActivated',{'proposal':'policy'})

    def test_новая_политика_отзывает_старые_резервы(self):
        self.prepared();self.policy_proposed()
        for a in (1,3,4,5):self.act(a,'PolicyVoted',{'proposal':'policy'})
        self.now+=61;self.act(1,'PolicyActivated',{'proposal':'policy'});self.assertEqual(cap.balance(self.state)[0]['reserved_minor'],0)
        with self.assertRaises(cap.CapitalError):self.spend()

    def test_нельзя_ослабить_порог_или_сменить_электорат(self):
        for field,value in [('spend_quorum',1),('governance_quorum',3),('activation_delay',0),('governors',['actor001'])]:
            p=self.next_policy();p[field]=value
            with self.assertRaises(cap.CapitalError):self.act(1,'PolicyProposed',{'proposal':'p','policy':p})

    def test_одиночный_отзыв_ключа_управления_запрещён(self):
        with self.assertRaises(cap.CapitalError):self.act(1,'AuthorityRevoked',{'key_id':'actor006','endorsements':{}})
        with self.assertRaises(cap.CapitalError):self.act(1,'KeyRevoked',{'key_id':'actor006'})

    def test_серверная_подпись_не_становится_голосом(self):
        self.proposed();c={'fund':self.policy['id'],'version':self.version,'policy_hash':cap.digest(self.policy),'operation_id':'forged','key_id':'actor001','type':'ApprovalGranted','expires_at':self.now+60,'data':{'proposal':'p','approval':'a'},'evidence':None}
        with self.assertRaises(cap.CapitalError):cap.transition(self.state,c,sign(self.node,c,gov.PRIVACY_DOMAIN),self.now)

    def test_возврат_под_новой_политикой_не_создаёт_денег(self):
        self.prepared();self.spend();self.act(3,'RefundConfirmed',{'reservation':'r','amount_minor':2000},receipt(self.oracle,self.policy,'REFUND','r',2000,'refund'))
        self.assertEqual(cap.balance(self.state)[0]['available_minor'],6000)

    def test_сверхкворум_отзывает_ключ_без_новых_полномочий(self):
        body={'fund':self.policy['id'],'version':self.version,'policy_hash':cap.digest(self.policy),'operation_id':'op'+str(self.version),'key_id':'actor001','type':'AuthorityRevoked','expires_at':self.now+1000,'data':{'key_id':'actor006'},'evidence':None}
        endorsements={f'actor{a:03d}':sign(self.keys[a],body,'BLAGO-CAPITAL-REVOKE-0.2\n') for a in (1,3,4,5)}
        self.act(1,'AuthorityRevoked',{'key_id':'actor006','endorsements':endorsements})
        with self.assertRaises(cap.CapitalError):self.act(6,'PolicyProposed',{'proposal':'captured','policy':self.next_policy()})

    def test_подпись_не_меняет_формат_при_проверке(self):
        c={'fund':self.policy['id'],'version':1,'policy_hash':cap.digest(self.policy),'operation_id':'bad-encoding','key_id':'actor007','type':'MemberExited','expires_at':self.now+60,'data':{},'evidence':None}
        signature=sign(self.keys[7],c,gov.DOMAIN).upper()
        with self.assertRaises(cap.CapitalError):cap.transition(self.state,c,signature,self.now)
        with self.assertRaises(InvalidProof):self.reference.apply(c,signature,self.now)

    def test_лишний_голос_не_откладывает_активацию_кворума(self):
        self.policy_proposed()
        for actor in (1,3,4,5):self.act(actor,'PolicyVoted',{'proposal':'policy'})
        ready=self.state['policy_proposals']['policy']['ready_at'];self.now+=30
        self.act(6,'PolicyVoted',{'proposal':'policy'});self.assertEqual(self.state['policy_proposals']['policy']['ready_at'],ready)
        self.now+=31;self.act(1,'PolicyActivated',{'proposal':'policy'});self.assertEqual(self.state['policy']['version'],2)

    def test_отзыв_лишнего_голоса_не_даёт_миноритарного_вето(self):
        self.policy_proposed()
        for actor in (1,3,4,5,6):self.act(actor,'PolicyVoted',{'proposal':'policy'})
        ready=self.state['policy_proposals']['policy']['ready_at'];self.now+=30
        self.act(6,'PolicyVoteRevoked',{'proposal':'policy'});self.assertEqual(self.state['policy_proposals']['policy']['ready_at'],ready)
        self.now+=31;self.act(1,'PolicyActivated',{'proposal':'policy'});self.assertEqual(self.state['policy']['version'],2)


def worker(cfg,token,c,signature,clock,pipe,phase):
    os.environ.update(cfg)
    with patch('time.time',return_value=clock):
        service=cap.CapitalLedger(WebApp(preflight(Path(cfg['XBLAGO_DATA_DIR'])/'blago.sqlite3')));original=PrivacyControl.flush
        def flush(control):
            pending=control.db.execute("SELECT 1 FROM privacy_outbox WHERE json_extract(payload,'$.kind')='capital_state'").fetchone()
            if pending and phase=='before_journal':os._exit(91)
            original(control)
            if pending and phase=='after_journal':os._exit(92)
        pipe.send('ready');pipe.recv()
        try:
            with patch.object(PrivacyControl,'flush',flush):r=service.execute(token,c,signature)
            if phase=='after_commit':os._exit(93)
            pipe.send({'ok':r})
        except (cap.CapitalError,ApiError,JournalUnavailable) as exc:pipe.send({'denied':type(exc).__name__})
        finally:pipe.close()


class GovernanceIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.ctx=lab(8);self.state=self.ctx.__enter__();self.addCleanup(self.ctx.__exit__,None,None,None)
        self.service,self.keys,self.oracle,self.policy=setup(self.state);self.tokens=self.state['tokens'];self.n=0
        self.path=Path(self.state['cfg']['XBLAGO_DATA_DIR'])/'blago.sqlite3'

    def cmd(self,actor,kind,data,evidence=None):
        self.n+=1;return command(self.service,self.tokens[actor],self.policy,actor,kind,data,'integration-'+str(self.n),evidence)
    def act(self,actor,kind,data,evidence=None):
        c=self.cmd(actor,kind,data,evidence);return self.service.execute(self.tokens[actor],c,sign(self.keys[actor],c,gov.DOMAIN))
    def view(self):return self.service.inspect(self.tokens[0],self.policy['id'])
    def received(self):
        self.act(0,'CapitalReceived',{'lot':'lot','amount_minor':10000,'source':'fixture'})
        self.act(1,'CapitalVerified',{'lot':'lot'},receipt(self.oracle,self.policy,'RECEIVED','lot',10000,'received'))
    def prepared(self):
        self.received();self.act(0,'ProposalCreated',{'proposal':'p','amount_minor':6000,'purpose':'tools','destination':'fixture','expires_at':int(time.time())+600})
        for a in (1,3):self.act(a,'ApprovalGranted',{'proposal':'p','approval':'a'})
        self.act(2,'BudgetReserved',{'approval':'a','reservation':'r','allocations':{'lot':6000}})

    def test_миграция_повтор_и_старый_capital_installer(self):
        self.assertFalse(governance_schema.migrate(self.path));self.assertFalse(capital_schema.migrate(self.path))
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok');self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_безопасный_выход_не_удаляет_фонд(self):
        self.received();before=self.view();self.act(7,'MemberExited',{});after=self.view()
        self.assertEqual(after['available_minor'],before['available_minor'])
        with self.assertRaises(ApiError):self.service.inspect(self.tokens[7],self.policy['id'])
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM capital_funds').fetchone()[0],1);self.assertEqual(db.execute('SELECT count(*) FROM capital_members').fetchone()[0],7)

    def test_удаление_аккаунта_сохраняет_права_остальных(self):
        self.prepared();p=PrivacyService(self.state['app']);q=p.preview(self.tokens[7]);p.delete(self.tokens[7],q['challenge'],'УДАЛИТЬ')
        self.assertEqual(self.view()['reserved_minor'],6000)
        self.act(2,'CapitalSpent',{'reservation':'r'},receipt(self.oracle,self.policy,'SPENT','r',6000,'spend'))
        bundle,pins=proof_export(self.service,self.tokens[1],self.policy['id']);self.assertEqual(verify_bundle(bundle,pins)['balance']['spent_minor'],6000)
        uid=self.state['app'].session(self.tokens[0])[0]
        self.assertNotIn(uid,json.dumps(bundle))

    def test_удаление_согласующего_освобождает_только_незавершённое(self):
        self.prepared();p=PrivacyService(self.state['app']);q=p.preview(self.tokens[1]);p.delete(self.tokens[1],q['challenge'],'УДАЛИТЬ')
        self.assertEqual(self.view()['available_minor'],10000)

    def test_один_участник_не_стирает_общий_фонд(self):
        with self.assertRaises(cap.CapitalError):self.service.erase(self.tokens[7],self.policy['id'],'УДАЛИТЬ СИНТЕТИЧЕСКИЙ ФОНД')

    def test_флаг_выключен_а_privacy_удаление_работает(self):
        self.received()
        with patch.dict(os.environ,{'XBLAGO_CAPITAL_GOVERNANCE_ENABLED':'false'}):
            with self.assertRaises(ApiError):self.view()
            p=PrivacyService(self.state['app']);q=p.preview(self.tokens[7]);p.delete(self.tokens[7],q['challenge'],'УДАЛИТЬ')
        self.assertEqual(self.view()['available_minor'],10000)

    def test_отдельный_верификатор_без_импорта_runtime(self):
        self.prepared();bundle,pins=proof_export(self.service,self.tokens[1],self.policy['id'])
        with patch('xblago.domain.capital.transition',side_effect=AssertionError('Runtime unavailable')):result=verify_bundle(bundle,pins)
        self.assertEqual(result['balance']['reserved_minor'],6000)
        path=Path('tools/independent_capital_verifier.py').resolve();tree=ast.parse(path.read_text())
        imports=[n.module or '' for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
        self.assertFalse(any(m.startswith(('xblago','tools')) for m in imports))
        bp=self.state['root']/'bundle.json';pp=self.state['root']/'pins.json';bp.write_text(json.dumps(bundle));pp.write_text(json.dumps(pins))
        env=dict(os.environ);env.pop('PYTHONPATH',None)
        response=subprocess.run([sys.executable,str(path),str(bp),'--pins',str(pp)],env=env,cwd=self.state['root'],capture_output=True,text=True,check=True,timeout=30)
        self.assertEqual(json.loads(response.stdout)['status'],'PASS')

    def test_инъекции_подмена_событий_и_UEL_отклоняются(self):
        self.received();bundle,pins=proof_export(self.service,self.tokens[1],self.policy['id'])
        alterations=[lambda b:b['history']['events'][-1].update(signature='0'*128),lambda b:b['history']['events'][-1].update(extra_field='injection'),lambda b:b['history']['events'].pop(),lambda b:b['uel_headers'][0].update(source_version=2),lambda b:b['uel_leaves'].__setitem__(0,'0'*64),lambda b:b['binding'].update(history_count=1),lambda b:b['checkpoint']['body'].update(stream='0'*64),lambda b:b['history']['events'][-1].update(state_hash='0'*64)]
        for mutate in alterations:
            bad=copy.deepcopy(bundle);mutate(bad)
            with self.assertRaises(InvalidProof):verify_bundle(bad,pins)
        with self.assertRaises(InvalidProof):verify_bundle(bundle,{})

    def test_чужая_сессия_не_голосует_чужим_ключом(self):
        self.received();c=self.cmd(0,'ProposalCreated',{'proposal':'p','amount_minor':100,'purpose':'tools','destination':'fixture','expires_at':int(time.time())+60})
        with self.assertRaises(cap.CapitalError):self.service.execute(self.tokens[7],c,sign(self.keys[0],c,gov.DOMAIN))

    def test_повреждённый_капитал_закрывает_доступ(self):
        self.received()
        with sqlite3.connect(self.path) as db:db.execute("UPDATE capital_funds SET head_tag='corrupt'")
        with self.assertRaises(JournalUnavailable):self.view()

    def test_старый_backup_не_возвращает_выход_и_не_стирает_остальных(self):
        self.received();backup=self.state['app'].store.backup_database(self.state['root']/'backup')
        p=PrivacyService(self.state['app']);q=p.preview(self.tokens[7]);p.delete(self.tokens[7],q['challenge'],'УДАЛИТЬ')
        restored=self.state['root']/'restored.sqlite3';restored.write_bytes(backup.read_bytes())
        restore(restored,Path(self.state['cfg']['XBLAGO_SECURITY_JOURNAL_DIR']))
        with sqlite3.connect(restored) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM capital_funds').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count(*) FROM capital_members').fetchone()[0],7)
            self.assertEqual(db.execute('SELECT count(*) FROM govcap_holds').fetchone()[0],1)
        other=cap.CapitalLedger(WebApp(preflight(restored)))
        with self.assertRaises((ApiError,JournalUnavailable)):other.inspect(self.tokens[0],self.policy['id'])

    def test_свежий_backup_после_выхода_сохраняет_подтверждённый_капитал(self):
        self.received();self.act(7,'MemberExited',{});backup=self.state['app'].store.backup_database(self.state['root']/'fresh')
        target=self.state['root']/'fresh-restored.sqlite3';target.write_bytes(backup.read_bytes());restore(target,Path(self.state['cfg']['XBLAGO_SECURITY_JOURNAL_DIR']))
        with sqlite3.connect(target) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM govcap_holds').fetchone()[0],0);self.assertEqual(json.loads(db.execute('SELECT state FROM capital_funds').fetchone()[0])['lots']['lot']['amount'],10000)
        app=WebApp(preflight(target));service=cap.CapitalLedger(app)
        tokens={i:app.login(f'foundation09_synthetic_{i:03d}',PASSWORD)[0] for i in (0,1,2,3)}
        self.assertEqual(service.inspect(tokens[0],self.policy['id'])['available_minor'],10000)
        actions=[(0,'ProposalCreated',{'proposal':'post-restore','amount_minor':6000,'purpose':'tools','destination':'fixture','expires_at':int(time.time())+600},None),
                 (1,'ApprovalGranted',{'proposal':'post-restore','approval':'after-a'},None),
                 (3,'ApprovalGranted',{'proposal':'post-restore','approval':'after-a'},None),
                 (2,'BudgetReserved',{'approval':'after-a','reservation':'after-r','allocations':{'lot':6000}},None),
                 (2,'CapitalSpent',{'reservation':'after-r'},receipt(self.oracle,self.policy,'SPENT','after-r',6000,'after-receipt'))]
        for n,(actor,kind,data,evidence) in enumerate(actions):
            c=command(service,tokens[actor],self.policy,actor,kind,data,'post-restore-'+str(n),evidence)
            service.execute(tokens[actor],c,sign(self.keys[actor],c,gov.DOMAIN))
        self.assertEqual(service.inspect(tokens[0],self.policy['id'])['spent_minor'],6000)


    def processes(self,commands,phase='normal'):
        ctx=multiprocessing.get_context('spawn');jobs=[]
        for c in commands:
            actor=int(c['key_id'][5:]);a,b=ctx.Pipe();proc=ctx.Process(target=worker,args=(self.state['cfg'],self.tokens[actor],c,sign(self.keys[actor],c,gov.DOMAIN),self.state['clock'],b,phase));proc.start()
            if not a.poll(30):proc.terminate();proc.join();self.fail('Worker startup timeout')
            self.assertEqual(a.recv(),'ready');jobs.append((proc,a))
        for proc,a in jobs:a.send('go')
        result=[]
        for proc,a in jobs:
            proc.join(30)
            if proc.is_alive():proc.terminate();proc.join();self.fail('Worker timeout')
            result.append(a.recv() if proc.exitcode==0 else {'crash':proc.exitcode});a.close()
        return result

    def test_два_workers_повторяют_резерв_без_нового_расхода(self):
        self.prepared();c=self.cmd(2,'ReservationReleased',{'reservation':'r'});result=self.processes([c,c]);self.assertTrue(all('ok' in r for r in result));self.assertEqual(self.view()['available_minor'],10000)

    def test_конкурентный_отзыв_и_расход(self):
        self.prepared();a=self.cmd(1,'ApprovalRevoked',{'approval':'a','principal':'actor001'});b=self.cmd(2,'CapitalSpent',{'reservation':'r'},receipt(self.oracle,self.policy,'SPENT','r',6000,'spent'))
        result=self.processes([a,b]);self.assertEqual(sum('ok' in r for r in result),1);self.assertIn(self.view()['spent_minor'],(0,6000));self.assertEqual(self.view()['reserved_minor'],0)

    def test_crash_до_журнала_не_меняет_согласование(self):
        self.prepared();c=self.cmd(1,'ApprovalRevoked',{'approval':'a','principal':'actor001'});self.assertEqual(self.processes([c],'before_journal'),[{'crash':91}]);self.assertEqual(self.view()['reserved_minor'],6000)

    def test_crash_после_journal_замораживает_но_не_стирает_фонд(self):
        self.prepared();c=self.cmd(1,'ApprovalRevoked',{'approval':'a','principal':'actor001'});self.assertEqual(self.processes([c],'after_journal'),[{'crash':92}])
        with self.assertRaises(JournalUnavailable):self.view()
        with sqlite3.connect(self.path) as db:self.assertEqual(db.execute('SELECT count(*) FROM capital_funds').fetchone()[0],1)

    def test_crash_после_commit_возвращает_старую_квитанцию(self):
        self.prepared();c=self.cmd(1,'ApprovalRevoked',{'approval':'a','principal':'actor001'});self.assertEqual(self.processes([c],'after_commit'),[{'crash':93}]);self.service.execute(self.tokens[1],c,sign(self.keys[1],c,gov.DOMAIN));self.assertEqual(self.view()['reserved_minor'],0)

    def test_два_резерва_одних_денег_требуют_CAS_и_остатка(self):
        self.received()
        for pid in ('first','second'):
            self.act(0,'ProposalCreated',{'proposal':pid,'amount_minor':6000,'purpose':'tools','destination':'fixture','expires_at':int(time.time())+600})
            for actor in (1,3):self.act(actor,'ApprovalGranted',{'proposal':pid,'approval':'a-'+pid})
        commands=[self.cmd(2,'BudgetReserved',{'approval':'a-'+pid,'reservation':pid,'allocations':{'lot':6000}}) for pid in ('first','second')]
        result=self.processes(commands);self.assertEqual(sum('ok' in r for r in result),1);self.assertEqual(self.view()['reserved_minor'],6000)

    def test_конкурентные_голоса_не_теряются_и_не_удваиваются(self):
        new=copy.deepcopy(self.policy);new['version']=2;new['delegation_seconds']=240
        self.act(1,'PolicyProposed',{'proposal':'change','policy':new})
        commands=[self.cmd(actor,'PolicyVoted',{'proposal':'change'}) for actor in (1,3)];result=self.processes(commands)
        self.assertEqual(sum('ok' in r for r in result),1)
        failed=next(i for i,r in enumerate(result) if 'denied' in r);self.act((1,3)[failed],'PolicyVoted',{'proposal':'change'})
        with sqlite3.connect(self.path) as db:
            state=json.loads(db.execute('SELECT state FROM capital_funds').fetchone()[0]);self.assertEqual(len(state['policy_proposals']['change']['votes']),2)

    def test_неизвестный_privacy_signer_не_блокирует_будущее_удаление_молча(self):
        bad=copy.deepcopy(self.policy);bad['id']='bad-privacy-signer';bad['privacy_key']=Ed25519PrivateKey.generate().public_key().public_bytes_raw().hex()
        signatures={f'actor{i:03d}':sign(k,bad,gov.POLICY_DOMAIN) for i,k in enumerate(self.keys)}
        with self.assertRaises(JournalUnavailable):self.service.enroll(self.tokens[0],bad,{f'actor{i:03d}':t for i,t in enumerate(self.tokens)},signatures)
        with sqlite3.connect(self.path) as db:self.assertEqual(db.execute('SELECT count(*) FROM capital_funds').fetchone()[0],1)


class GovernanceMigrationTests(unittest.TestCase):
    def setUp(self):
        self.ctx=lab(8);self.s=self.ctx.__enter__();self.addCleanup(self.ctx.__exit__,None,None,None)
        self.path=Path(self.s['cfg']['XBLAGO_DATA_DIR'])/'blago.sqlite3';capital_schema.migrate(self.path)

    def test_новая_миграция_не_меняет_старые_checksums_или_записи(self):
        with sqlite3.connect(self.path) as db:
            before=db.execute('SELECT * FROM uel_events ORDER BY sequence').fetchall();checks=[db.execute('SELECT * FROM '+t).fetchall() for t in ('uel_migrations','capital_migrations')]
        self.assertTrue(governance_schema.migrate(self.path));self.assertFalse(governance_schema.migrate(self.path))
        with sqlite3.connect(self.path) as db:
            self.assertEqual(before,db.execute('SELECT * FROM uel_events ORDER BY sequence').fetchall());self.assertEqual(checks,[db.execute('SELECT * FROM '+t).fetchall() for t in ('uel_migrations','capital_migrations')])

    def test_ошибка_DDL_откатывает_только_новую_миграцию(self):
        with patch.object(governance_schema,'DDL',governance_schema.DDL+('INVALID SYNTHETIC DDL',)):
            with self.assertRaises(sqlite3.Error):governance_schema.migrate(self.path)
        with sqlite3.connect(self.path) as db:
            self.assertFalse(governance_schema.installed(db));self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok');capital_schema.validate(db)
