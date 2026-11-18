"""Fail-closed custody of existing UEL/capital proofs, NOT a financial event source.

All three nodes endorse normal checkpoints. Recovery changes custody keys only;
CapitalLedger owner/evidence/privacy keys and spending authority are never changed.
"""
import copy
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding,PrivateFormat,NoEncryption
from tools.independent_capital_verifier import verify_bundle,Replay

PROFILE='capital-custody-0.3-lab.1'
ZERO='0'*64
LIMIT=8*1024*1024

class Rejected(ValueError):pass
class UnknownRecovery(Rejected):pass

def need(ok,why):
    if not ok:raise Rejected(why)

def wire(x):return json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()
def digest(x):return hashlib.sha256(wire(x)).hexdigest()
def public(k):return k.public_key().public_bytes_raw().hex()
def signed(body,key,kind):return {'body':copy.deepcopy(body),'signature':key.sign((PROFILE+'/'+kind+'\0').encode()+wire(body)).hex()}
def verify(s,key,kind):
    try:
        need(set(s)=={'body','signature'} and type(s['signature']) is str and len(s['signature'])==128,'signature shape')
        need(s['signature']==bytes.fromhex(s['signature']).hex(),'signature encoding')
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(key)).verify(bytes.fromhex(s['signature']),(PROFILE+'/'+kind+'\0').encode()+wire(s['body']))
        return s['body']
    except Exception as exc:raise Rejected('Invalid '+kind+' signature') from exc

def bounded(x):need(len(wire(x))<=LIMIT,'packet limit')
def require_lab():
    need(os.getenv('XBLAGO_CAPITAL_CUSTODY_LAB_ENABLED')=='true' and os.getenv('XBLAGO_INTEGRATION_MODE')=='synthetic','Synthetic custody flag OFF')

def atomic(path,data):
    path=Path(path);need(not path.is_symlink(),'symlink')
    tmp=path.with_name('.stage-'+secrets.token_hex(16))
    try:
        fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
        fd=os.open(path.parent,os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)
    finally:tmp.unlink(missing_ok=True)

def load_key(path):
    p=Path(path);need(not p.is_symlink() and p.stat().st_mode&0o077==0,'private key permissions')
    return Ed25519PrivateKey.from_private_bytes(p.read_bytes())

def replay(bundle,pins):
    verify_bundle(bundle,pins)
    events=bundle['history']['events'];r=Replay(events[0]['policy'])
    for e in events[1:]:r.apply(e['command'],e['signature'],e['recorded_at'])
    return r

def check_config(c):
    need(set(c)=={'profile','fund','epoch','previous','nodes','origin_pins','rotation_delay'},'config shape')
    need(c['profile']==PROFILE and type(c['epoch']) is int and c['epoch']>=1 and c['rotation_delay']==60,'config profile')
    need(set(c['nodes'])=={'A','B','C'} and len(set(c['nodes'].values()))==3,'three distinct node keys')
    for x in [c['previous'],*c['nodes'].values()]:need(type(x) is str and len(x)==64 and bytes.fromhex(x).hex()==x,'key/hash encoding')

def attestations(body,signatures,keys,threshold,kind):
    need(type(signatures) is dict and len(signatures)>=threshold and set(signatures)<=set(keys),'signature threshold')
    for who,s in signatures.items():need(verify(s,keys[who],kind)==body,'signature body binding')

def governors(bundle,now):
    pins=bundle['pins'];p=replay(bundle['proof'],pins)
    policy=p.s['policy']
    return {k:policy['members'][k]['public_key'] for k in policy['governors'] if p.active(k,now,'GOV')},policy['governance_quorum']

def bootstrap(config,bundle,approvals,now):
    check_config(config);need(config['epoch']==1 and config['previous']==ZERO,'genesis config')
    need(bundle['pins']==config['origin_pins'] and bundle['proof']['history']['fund']==config['fund'],'bootstrap scope')
    keys,threshold=governors(bundle,now)
    all_keys={m['public_key'] for m in bundle['proof']['history']['events'][0]['policy']['members'].values()}
    need(not set(config['nodes'].values()) & (all_keys|{config['origin_pins']['node_key'],config['origin_pins']['custodian_key']}),'Human/Node/origin keys must differ')
    attestations(config,approvals,keys,threshold,'bootstrap')
    return {'genesis':{'config':copy.deepcopy(config),'approvals':copy.deepcopy(approvals),'basis':copy.deepcopy(bundle),'at':now},'config':copy.deepcopy(config),'entries':[],'rotations':[]}

def tip(archive):return digest(archive['entries'][-1]) if archive['entries'] else ZERO

def basis(archive):return archive['entries'][-1]['package'] if archive['entries'] else archive['genesis']['basis']

def candidate(archive,package):
    bounded(package);need(set(package)=={'proof','pins'},'package shape')
    cfg=archive['config'];need(package['pins']['policy_hash']==cfg['origin_pins']['policy_hash'],'origin policy pin')
    for k in ('node_key','custodian_key','stream'):need(package['pins'][k]==cfg['origin_pins'][k],'origin anchor replacement forbidden')
    replay(package['proof'],package['pins']);need(package['proof']['history']['fund']==cfg['fund'],'fund substitution')
    old=basis(archive);before=old['proof']['history']['events'];after=package['proof']['history']['events']
    need(len(after)>=len(before) and after[:len(before)]==before,'history fork or truncation')
    leaves=old['proof']['uel_leaves'];need(package['proof']['uel_leaves'][:len(leaves)]==leaves,'UEL prefix conflict')
    before_cp=old['proof']['checkpoint']['body'];after_cp=package['proof']['checkpoint']['body']
    need(all(after_cp[k]>=before_cp[k] for k in ('generation','journal_seq','uel_seq')),'checkpoint rollback')
    if archive['entries']:need(len(after)>len(before),'no new canonical event')
    return {'profile':PROFILE,'fund':cfg['fund'],'epoch':cfg['epoch'],'config':digest(cfg),'index':len(archive['entries'])+1,'parent':tip(archive),'package':digest(package),'history_count':len(after),'history_head':digest(after[-1]),'uel_checkpoint':digest(package['proof']['checkpoint'])}

def check_certificate(archive,cert):
    need(set(cert)=={'body','votes','package'},'checkpoint shape')
    b=candidate(archive,cert['package']);need(cert['body']==b and all(type(cert['body'][k]) is int for k in ('epoch','index','history_count')),'checkpoint binding')
    attestations(b,cert['votes'],archive['config']['nodes'],3,'checkpoint')

def rotation_body(archive,nodes,now):
    cfg=copy.deepcopy(archive['config']);cfg.update(epoch=cfg['epoch']+1,previous=digest(cfg),nodes=copy.deepcopy(nodes))
    check_config(cfg)
    return {'profile':PROFILE,'kind':'custody-rotation','parent':tip(archive),'index':len(archive['entries']),'old_config':digest(archive['config']),'new_config':cfg,'not_before':now+60,'expires_at':now+600}

def check_rotation(archive,body,approvals,now,activate=False):
    need(set(body)=={'profile','kind','parent','index','old_config','new_config','not_before','expires_at'},'rotation shape')
    old=archive['config'];new=body['new_config'];check_config(new)
    need(body['profile']==PROFILE and body['kind']=='custody-rotation' and archive['entries'],'rotation domain/basis')
    need(type(body['index']) is int and body['parent']==tip(archive) and body['index']==len(archive['entries']) and body['old_config']==digest(old),'rotation frontier')
    need(new['epoch']==old['epoch']+1 and new['previous']==digest(old),'rotation epoch')
    need(all(new[k]==old[k] for k in ('profile','fund','origin_pins','rotation_delay')),'fund authority rotation forbidden')
    need(type(body['not_before']) is int and type(body['expires_at']) is int and body['expires_at']-body['not_before']==540,'rotation TTL')
    need(body['not_before']-60<=now<body['expires_at'],'rotation expiry/clock')
    if activate:need(now>=body['not_before'],'rotation delay')
    keys,threshold=governors(basis(archive),now)
    all_keys={m['public_key'] for m in basis(archive)['proof']['history']['events'][0]['policy']['members'].values()}
    need(not set(new['nodes'].values()) & (all_keys|{old['origin_pins']['node_key'],old['origin_pins']['custodian_key']}),'Human/Node separation')
    attestations(body,approvals,keys,threshold,'rotation-approval')

class Node:
    """One operator-local proof replica plus independently retained signed control.

    Control and DB may be on different volumes. Host independence is NOT inferred.
    No endpoint accepts caller-supplied user/session IDs or financial commands.
    """
    def __init__(self,root,key_file,control,genesis_pin):
        self.root=Path(root);self.key_file=Path(key_file);self.control=Path(control);self.genesis_pin=genesis_pin

    @classmethod
    def create(cls,root,key_file,control,alias,archive,*,genesis_pin):
        require_lab();check_archive(archive,genesis_pin);root=Path(root);key_file=Path(key_file);control=Path(control)
        need(not root.exists() and not control.exists(),'explicit new synthetic stores only')
        need(alias in archive['config']['nodes'] and public(load_key(key_file))==archive['config']['nodes'][alias],'pinned node key')
        root.mkdir(mode=0o700);control.parent.mkdir(parents=True,exist_ok=True)
        node=cls(root,key_file,control,genesis_pin)
        with node._db() as db:
            db.execute('CREATE TABLE custody (id INTEGER PRIMARY KEY CHECK(id=1),state TEXT NOT NULL)')
            state={'alias':alias,'archive':copy.deepcopy(archive),'pending':None,'rotation':None}
            db.execute('INSERT INTO custody VALUES (1,?)',(wire(state).decode(),));db.commit()
        node._retain(state)
        return node

    def _db(self):
        need(not self.root.is_symlink() and not (self.root/'custody.sqlite3').is_symlink(),'store symlink')
        from xblago.core.sqlite_safety import require_safe_sqlite
        require_safe_sqlite()
        db=sqlite3.connect(self.root/'custody.sqlite3',timeout=15,isolation_level=None)
        db.execute('PRAGMA journal_mode=DELETE');db.execute('PRAGMA synchronous=FULL')
        return db

    def _retain(self,s):atomic(self.control,wire(signed({'state':digest(s)},load_key(self.key_file),'local-control')))

    @contextmanager
    def tx(self,fault=None):
        require_lab();need(self.root.is_dir() and (self.root/'custody.sqlite3').is_file(),'Missing custody DB')
        with open(self.root/'lock','a+b') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            db=self._db()
            try:
                db.execute('BEGIN IMMEDIATE')
                if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise UnknownRecovery('Corrupt replica')
                need(db.execute('SELECT length(state) FROM custody WHERE id=1').fetchone()[0]<=LIMIT,'stored byte bound')
                s=json.loads(db.execute('SELECT state FROM custody WHERE id=1').fetchone()[0]);before=digest(s)
                check_archive(s['archive'],self.genesis_pin)
                try:
                    ctl=verify(json.loads(self.control.read_bytes()),public(load_key(self.key_file)),'local-control')
                    if ctl!={'state':before}:raise UnknownRecovery('DB/control rollback or interrupted write')
                except (OSError,ValueError,Rejected) as exc:raise UnknownRecovery('Missing/mismatched retained control') from exc
                yield s
                if digest(s)!=before:
                    bounded(s)
                    if fault:fault('before_commit')
                    db.execute('UPDATE custody SET state=? WHERE id=1',(wire(s).decode(),));db.commit()
                    if fault:fault('after_commit')
                    self._retain(s)
                    if fault:fault('after_control')
                else:db.commit()
            except BaseException:
                db.rollback();raise
            finally:db.close();fcntl.flock(lock,fcntl.LOCK_UN)

    def prepare(self,package,fault=None):
        with self.tx(fault) as s:
            need(s['rotation'] is None,'Recovery pending')
            a=s['archive']
            if a['entries'] and a['entries'][-1]['package']==package:return copy.deepcopy(a['entries'][-1]['votes'][s['alias']])
            b=candidate(a,package);vote=signed(b,load_key(self.key_file),'checkpoint')
            pending={'body':b,'package':copy.deepcopy(package),'vote':vote}
            need(s['pending'] is None or s['pending']==pending,'Conflicting prepared checkpoint')
            s['pending']=pending
        return vote

    def commit(self,cert,fault=None):
        with self.tx(fault) as s:
            a=s['archive']
            if a['entries'] and a['entries'][-1]==cert:return digest(cert)
            need(s['rotation'] is None,'Recovery pending');check_certificate(a,cert)
            need(s['pending'] and s['pending']['body']==cert['body'],'Must durably prepare before commit')
            a['entries'].append(copy.deepcopy(cert));s['pending']=None
        return digest(cert)

    def status(self,nonce,now=None):
        now=int(time.time()) if now is None else now
        need(type(nonce) is str and len(nonce)==64 and bytes.fromhex(nonce).hex()==nonce,'receiver nonce')
        with self.tx() as s:
            a=s['archive'];b={'profile':PROFILE,'node':s['alias'],'nonce':nonce,'config':digest(a['config']),'epoch':a['config']['epoch'],'tip':tip(a),'index':len(a['entries']),'pending':s['pending'] is not None or s['rotation'] is not None,'issued_at':now,'expires_at':now+30}
            return signed(b,load_key(self.key_file),'status')

    def export(self):
        with self.tx() as s:
            need(not s['pending'] and not s['rotation'],'Pending evidence; UNKNOWN_RECOVERY')
            return copy.deepcopy(s['archive'])

    def prepare_rotation(self,body,approvals,now,fault=None):
        with self.tx(fault) as s:
            need(s['pending'] is None,'Unresolved prepared history')
            check_rotation(s['archive'],body,approvals,now)
            pending={'body':copy.deepcopy(body),'approvals':copy.deepcopy(approvals),'prepared_at':now}
            if s['rotation'] is not None:
                need(s['rotation']['body']==body and s['rotation']['approvals']==approvals,'Conflicting recovery')
                pending=s['rotation']
            s['rotation']=pending
            vote=signed({'rotation':digest(body),'prepared_at':pending['prepared_at'],'node':s['alias']},load_key(self.key_file),'rotation-vote')
        return vote

    def activate_rotation(self,cert,now,fault=None):
        with self.tx(fault) as s:
            if s['archive']['rotations'] and s['archive']['rotations'][-1]==cert:return digest(cert)
            a=s['archive'];need(set(cert)=={'body','approvals','votes','activated_at'},'rotation cert shape')
            need(type(now) is int and type(cert['activated_at']) is int and now>=cert['activated_at'],'activation clock')
            check_rotation(a,cert['body'],cert['approvals'],now,True)
            rotation_votes(cert,a['config']['nodes'],cert['activated_at'])
            need(s['pending'] is None and s['rotation'] is not None and s['rotation']['body']==cert['body'] and s['rotation']['approvals']==cert['approvals'],'Unprepared recovery')
            # A surviving operator cannot silently replace its own key in place.
            need(cert['body']['new_config']['nodes'][s['alias']]==public(load_key(self.key_file)),'Use a new replacement store')
            a['rotations'].append(copy.deepcopy(cert));a['config']=copy.deepcopy(cert['body']['new_config']);s['rotation']=None
        return digest(cert)

class Transport:
    """Injectable local transport. Not a claim of authenticated remote networking."""
    def __init__(self,nodes):self.nodes=nodes;self.offline=set()
    def call(self,alias,method,*args,**kwargs):
        if alias in self.offline:raise ConnectionError('Synthetic partition')
        return getattr(self.nodes[alias],method)(*args,**kwargs)

def replicate(transport,package):
    votes={k:transport.call(k,'prepare',package) for k in ('A','B','C')}
    body=votes['A']['body'];cert={'body':body,'votes':votes,'package':copy.deepcopy(package)}
    need(all(v['body']==body for v in votes.values()),'Disagreeing prepare')
    receipts={k:transport.call(k,'commit',cert) for k in ('A','B','C')}
    need(len(set(receipts.values()))==1,'Receipt substitution')
    return cert

def check_archive(a,genesis_pin):
    bounded(a);need(set(a)=={'genesis','config','entries','rotations'},'archive shape')
    g=a['genesis'];need(set(g)=={'config','approvals','basis','at'} and digest(g['config'])==genesis_pin,'Out-of-band genesis pin')
    rebuilt=bootstrap(g['config'],g['basis'],g['approvals'],g['at']);rotations=list(a['rotations'])
    need(type(a['entries']) is list and len(a['entries'])<=64 and len(rotations)<=16,'lab bound')
    for entry in a['entries']:
        check_certificate(rebuilt,entry);rebuilt['entries'].append(copy.deepcopy(entry))
        while rotations and rotations[0]['body']['index']==len(rebuilt['entries']):
            r=rotations.pop(0);need(set(r)=={'body','approvals','votes','activated_at'},'rotation certificate shape')
            check_rotation(rebuilt,r['body'],r['approvals'],r['activated_at'],True)
            rotation_votes(r,rebuilt['config']['nodes'],r['activated_at'])
            rebuilt['rotations'].append(r);rebuilt['config']=copy.deepcopy(r['body']['new_config'])
    need(not rotations and rebuilt==a,'Incomplete/conflicting archive')
    return rebuilt


def rotation_votes(cert,keys,now):
    votes=cert['votes'];need(type(votes) is dict and 2<=len(votes)<=3 and set(votes)<=set(keys),'recovery node quorum')
    for name,value in votes.items():
        b=verify(value,keys[name],'rotation-vote')
        need(set(b)=={'rotation','prepared_at','node'} and b['node']==name and b['rotation']==digest(cert['body']) and type(b['prepared_at']) is int,'rotation vote binding')
        need(cert['body']['not_before']-60<=b['prepared_at']<=now-60,'durable preparation delay')
    need(now==max(cert['body']['not_before'],max(v['body']['prepared_at'] for v in votes.values())+60),'canonical activation time')
