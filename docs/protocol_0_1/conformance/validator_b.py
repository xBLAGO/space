"""Offline reference B: stdlib JSON canonicalization + PyNaCl/libsodium verification.
Separate parsing/validation implementation. No validator_a/xblago imports.
Same author and corpus: NOT evidence of independent operators or external review.
"""
import base64
import hashlib
import json
import sys
from nacl.signing import VerifyKey
from nacl.bindings import crypto_core_ed25519_is_valid_point

VERSION='bop-0.1-lab.1'
BOUND=9007199254740991


class Failure(Exception): pass


def check(condition, tag):
    if not condition: raise Failure(tag)


def tree(node, level=0):
    check(level<=8,'WIRE')
    if node is None or type(node) is bool: return
    if type(node) is int: check(0<=node<=BOUND,'WIRE');return
    if type(node) is str: check(len(node)<=4096 and all(' '<=c<='~' for c in node),'WIRE');return
    if isinstance(node,list):
        check(len(node)<=64,'WIRE')
        for v in node: tree(v,level+1)
        return
    check(type(node) is dict and len(node)<=64,'WIRE')
    for k,v in node.items(): tree(k,level+1);tree(v,level+1)


def encode(obj):
    tree(obj)
    return json.dumps(obj,ensure_ascii=True,sort_keys=True,separators=(',',':'),allow_nan=False).encode('ascii')


def reject_float(_): raise Failure('WIRE')


def object_pairs(entries):
    check(len({k for k,v in entries})==len(entries),'WIRE')
    return dict(entries)


def document(value):
    check(type(value) is str and len(value)<=32768,'WIRE')
    try:
        data=bytes.fromhex(value)
        check(0<len(data)<=16384 and data.isascii(),'WIRE')
        parsed=json.loads(data.decode('ascii'),object_pairs_hook=object_pairs,parse_float=reject_float,parse_constant=reject_float)
        check(encode(parsed)==data,'WIRE')
    except (ValueError,UnicodeError,RecursionError,TypeError) as e: raise Failure('WIRE') from e
    check(type(parsed) is dict and sorted(parsed)==['alg','body','signature'] and parsed['alg']=='Ed25519' and type(parsed['body']) is dict,'SCHEMA')
    return parsed


def binary(value, count=None):
    check(type(value) is str and 1<=len(value)<=4096 and set(value)<=set('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_'),'ENCODING')
    try: data=base64.b64decode(value+'='*(-len(value)%4),altchars=b'-_',validate=True)
    except Exception as e: raise Failure('ENCODING') from e
    check(base64.urlsafe_b64encode(data).decode().rstrip('=')==value and (count is None or len(data)==count),'ENCODING')
    return data


def hash_object(value): return 'sha256:'+hashlib.sha256(b'BOP/0.1-lab.1/OBJECT\0'+encode(value)).hexdigest()
def key_name(key): return 'sha256:'+hashlib.sha256(key).hexdigest()


def verify(value,key):
    check(crypto_core_ed25519_is_valid_point(key),'KEY')
    sig=binary(value['signature'],64)
    data=b'BOP/0.1-lab.1/'+value['body']['kind'].upper().encode('ascii')+b'\0'+encode(value['body'])
    try: VerifyKey(key).verify(data,sig)
    except Exception as e: raise Failure('SIGNATURE') from e


def shape(obj,keys): check(set(obj)==set(keys.split()),'SCHEMA')

def tagged(text,prefix,length):
    return isinstance(text,str) and text[:len(prefix)]==prefix and len(text)==len(prefix)+length and set(text[len(prefix):])<=set('0123456789abcdef')


def validate(test):
    try:
        env=document(test['envelope_hex']);mandate=document(test['mandate_hex']);fresh=document(test['status_hex'])
        check(type(test['authority_hex']) is list and len(test['authority_hex'])<=64,'SCHEMA')
        log=list(map(document,test['authority_hex']))
        a=env['body'];m=mandate['body'];f=fresh['body'];cfg=test['context']
        shape(a,'kind profile network policy_ref type event_id issuer issuer_key subject recipient_agent recipient_node scope object mandate_ref links nonce issued_at expires_at payload_schema payload_b64 payload_hash')
        shape(m,'kind profile network policy_ref mandate_id issuer issuer_key subject subject_key recipient_agent recipient_node scope object not_before expires_at allow_delegation parent limits authority_epoch')
        shape(f,'kind profile network policy_ref identity issuer_key epoch sequence head mandate_ref mandate_state event_ref recipient_node status_nonce issued_at expires_at')
        check((a['kind'],m['kind'],f['kind'])==('envelope','mandate','authority-status'),'SCHEMA')
        for record in (env,mandate,fresh,*log):
            content=record['body'];check(content['profile']==VERSION,'PROFILE')
            check((content['network'],content['policy_ref'])==(cfg['network'],cfg['policy_ref']),'DOMAIN')
        for token,mark in [(m['issuer'],'id:'),(m['subject'],'ag:'),(a['recipient_agent'],'ag:'),(m['recipient_agent'],'ag:'),(a['recipient_node'],'nd:'),(m['recipient_node'],'nd:'),(a['network'],'fd:'),(m['object'],'ch:')]:
            check(tagged(token,mark,32),'SCHEMA')
        check(tagged(a['policy_ref'],'sha256:',64),'SCHEMA')
        check(tagged(cfg['genesis_head'],'sha256:',64),'SCHEMA')
        check(cfg['retained_available'] is True,'UNKNOWN_RECOVERY')
        check(cfg['clock_ok'] is True and type(cfg['clock_uncertainty']) is int and 0<=cfg['clock_uncertainty']<=5,'UNKNOWN_CLOCK')
        check(type(cfg['now']) is int and 0<=cfg['now']<=BOUND,'SCHEMA')
        check(type(cfg['owner_epoch']) is int and cfg['owner_epoch']==1,'UNKNOWN_KEY_HISTORY')
        owner=binary(cfg['owner_key_b64'],32)
        check(m['issuer']==cfg['owner_identity'] and m['issuer_key']==key_name(owner),'AUTHORITY')
        verify(mandate,owner)
        check(type(m['authority_epoch']) is int and m['authority_epoch']==cfg['owner_epoch'],'AUTHORITY')
        check(m['parent'] is None and m['allow_delegation'] is False,'DELEGATION')
        shape(m['limits'],'max_uses max_payload_bytes')
        check(type(m['limits']['max_uses']) is int and m['limits']['max_uses']==1 and type(m['limits']['max_payload_bytes']) is int and 1<=m['limits']['max_payload_bytes']<=2048,'LIMIT')
        subject=binary(m['subject_key'],32);verify(env,subject)
        check((a['issuer'],a['issuer_key'],a['subject'])==(m['subject'],key_name(subject),m['issuer']),'AUTHORITY')
        for part in (a,m): check((part['recipient_agent'],part['recipient_node'])==(cfg['recipient_agent'],cfg['recipient_node']),'RECIPIENT')
        check(a['type']=='note.deliver' and a['scope']==m['scope']==['note.deliver'] and a['object']==m['object'],'SCOPE')
        for token,prefix in ((a['event_id'],'ev:'),(m['mandate_id'],'md:')):
            check(type(token) is str and len(token)==67 and token.startswith(prefix) and set(token[3:])<=set('0123456789abcdef'),'SCHEMA')
        check(a['mandate_ref']==hash_object(mandate),'MANDATE_REF');binary(a['nonce'],32)
        check(a['event_id'] not in cfg['seen_event_ids'],'REPLAY')
        check(type(cfg['mandate_uses']) is int and 0<=cfg['mandate_uses']<m['limits']['max_uses'],'LIMIT')
        links=a['links'];check(type(links) is list and len(links)<=8 and all(type(x) is str for x in links) and links==sorted(set(links)) and all(tagged(x,'sha256:',64) for x in links),'LINKS')
        check(all(tagged(link,'sha256:',64) and link in cfg['known_refs'] for link in links),'UNKNOWN_LINK')
        payload=binary(a['payload_b64']);check(a['payload_schema']=='text.utf8.v1','SCHEMA')
        check(0<len(payload)<=m['limits']['max_payload_bytes'],'LIMIT')
        check(a['payload_hash']=='sha256:'+hashlib.sha256(payload).hexdigest(),'PAYLOAD')
        try: decoded=payload.decode('utf-8')
        except UnicodeError as e: raise Failure('PAYLOAD') from e
        check(not any(ord(ch)<32 and ch not in '\t\n' for ch in decoded),'PAYLOAD')
        windows=[(m['not_before'],m['expires_at'],3600),(a['issued_at'],a['expires_at'],120),(f['issued_at'],f['expires_at'],30)]
        for begin,end,maximum in windows:
            check(type(begin) is int and type(end) is int and 0<=begin<end<=BOUND and end-begin<=maximum,'TIME')
            check(begin<=cfg['now']-cfg['clock_uncertainty'] and cfg['now']+cfg['clock_uncertainty']<end,'TIME')
        pointer=cfg['genesis_head'];sequence=0;registry={};heads=[pointer]
        for entry in log:
            t=entry['body'];shape(t,'kind profile network policy_ref identity issuer_key epoch sequence previous operation mandate_ref')
            check(t['kind']=='authority-event' and t['identity']==cfg['owner_identity'] and t['issuer_key']==key_name(owner) and type(t['epoch']) is int and t['epoch']==1,'AUTHORITY')
            verify(entry,owner)
            check(type(t['sequence']) is int and t['sequence']==sequence+1 and t['previous']==pointer,'HISTORY')
            check(t['operation'] in ['mandate.issue','mandate.revoke'],'HISTORY')
            identifier=t['mandate_ref'];check(tagged(identifier,'sha256:',64),'HISTORY')
            if t['operation']=='mandate.issue': check(identifier not in registry,'HISTORY');registry[identifier]='active'
            else: check(registry.get(identifier)=='active','HISTORY');registry[identifier]='revoked'
            sequence+=1;pointer=hash_object(entry);heads.append(pointer)
        check(f['identity']==cfg['owner_identity'] and f['issuer_key']==key_name(owner) and type(f['epoch']) is int and f['epoch']==1,'AUTHORITY')
        verify(fresh,owner)
        check(type(f['sequence']) is int and f['sequence']==sequence and f['head']==pointer,'HISTORY')
        floor=cfg['floor_sequence'];check(type(floor) is int and floor>=0,'SCHEMA')
        check(sequence>=floor,'UNKNOWN_STALE')
        check(heads[floor]==cfg['floor_head'],'FORK')
        check(f['mandate_ref']==hash_object(mandate) and f['mandate_state']==registry.get(hash_object(mandate),'unknown'),'HISTORY')
        check(f['event_ref']==hash_object(env) and f['recipient_node']==cfg['recipient_node'] and f['status_nonce']==cfg['expected_status_nonce'],'STATUS_BINDING')
        binary(f['status_nonce'],32)
        check(f['mandate_state']!='revoked','REVOKED')
        check(f['mandate_state']=='active','UNKNOWN_MANDATE')
        return {'decision':'ACCEPT','code':'OK'}
    except Failure as e:
        code=str(e);return {'decision':'UNKNOWN' if code.startswith('UNKNOWN_') else 'REJECT','code':code}
    except (KeyError,TypeError,ValueError,IndexError,OverflowError): return {'decision':'REJECT','code':'SCHEMA'}


if __name__=='__main__':
    for line in sys.stdin:
        if len(line)>2_000_000: print(json.dumps({'decision':'REJECT','code':'WIRE'}));continue
        print(json.dumps(validate(json.loads(line)),sort_keys=True))
