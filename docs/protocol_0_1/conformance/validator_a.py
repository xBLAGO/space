"""Offline reference A: manual ASCII canonical encoder + cryptography Ed25519.
Not a node, API or durable inbox. No imports from xblago or validator_b.
"""
import base64
import hashlib
import json
import sys
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from nacl.bindings import crypto_core_ed25519_is_valid_point

PROFILE = 'bop-0.1-lab.1'
MAX = 2**53-1


class Invalid(Exception):
    pass


def demand(test, code):
    if not test: raise Invalid(code)


def canonical(value, depth=0):
    demand(depth <= 8, 'WIRE')
    if value is None: return b'null'
    if type(value) is bool: return b'true' if value else b'false'
    if type(value) is int:
        demand(0 <= value <= MAX, 'WIRE')
        return str(value).encode('ascii')
    if type(value) is str:
        demand(len(value) <= 4096 and all(32 <= ord(c) <= 126 for c in value), 'WIRE')
        return ('"'+value.replace('\\','\\\\').replace('"','\\"')+'"').encode('ascii')
    if type(value) is list:
        demand(len(value) <= 64, 'WIRE')
        return b'['+b','.join(canonical(v,depth+1) for v in value)+b']'
    demand(type(value) is dict and len(value) <= 64 and all(type(k) is str for k in value), 'WIRE')
    return b'{'+b','.join(canonical(k,depth+1)+b':'+canonical(value[k],depth+1) for k in sorted(value))+b'}'


def pairs(items):
    result={}
    for key,value in items:
        demand(key not in result, 'WIRE')
        result[key]=value
    return result


def no_number(_): raise Invalid('WIRE')


def read_wire(hexadecimal):
    demand(type(hexadecimal) is str and len(hexadecimal) <= 32768, 'WIRE')
    try:
        raw=bytes.fromhex(hexadecimal)
        demand(raw and len(raw)<=16384 and all(c<128 for c in raw), 'WIRE')
        value=json.loads(raw,object_pairs_hook=pairs,parse_float=no_number,parse_constant=no_number)
        demand(canonical(value)==raw, 'WIRE')
        demand(type(value) is dict and set(value)=={'alg','body','signature'} and value['alg']=='Ed25519' and type(value['body']) is dict, 'SCHEMA')
        return value
    except (ValueError,UnicodeError,TypeError,RecursionError) as exc: raise Invalid('WIRE') from exc


def unb64(value,length=None):
    demand(type(value) is str and 1<=len(value)<=4096 and all(c.isascii() and (c.isalnum() or c in '_-') for c in value),'ENCODING')
    try: raw=base64.urlsafe_b64decode(value+'='*((-len(value))%4))
    except Exception as exc: raise Invalid('ENCODING') from exc
    demand(base64.urlsafe_b64encode(raw).decode().rstrip('=')==value and (length is None or len(raw)==length),'ENCODING')
    return raw


def ref(value): return 'sha256:'+hashlib.sha256(b'BOP/0.1-lab.1/OBJECT\0'+canonical(value)).hexdigest()
def kid(raw): return 'sha256:'+hashlib.sha256(raw).hexdigest()


def signature(obj,key):
    demand(crypto_core_ed25519_is_valid_point(key),'KEY')
    sig=unb64(obj['signature'],64)
    try: Ed25519PublicKey.from_public_bytes(key).verify(sig,b'BOP/0.1-lab.1/'+obj['body']['kind'].upper().encode()+b'\0'+canonical(obj['body']))
    except Exception as exc: raise Invalid('SIGNATURE') from exc


def fields(body, names): demand(set(body)==set(names.split()),'SCHEMA')

def tag(value,prefix,size):
    return type(value) is str and value.startswith(prefix) and len(value)==len(prefix)+size and all(x in '0123456789abcdef' for x in value[len(prefix):])


def validate(case):
    try: return _validate(case)
    except Invalid as exc:
        code=str(exc)
        return {'decision':'UNKNOWN' if code.startswith('UNKNOWN_') else 'REJECT','code':code}
    except (KeyError,TypeError,ValueError,OverflowError): return {'decision':'REJECT','code':'SCHEMA'}


def _validate(case):
    env=read_wire(case['envelope_hex']); grant=read_wire(case['mandate_hex']); status=read_wire(case['status_hex'])
    demand(type(case['authority_hex']) is list and len(case['authority_hex'])<=64,'SCHEMA')
    history=[read_wire(raw) for raw in case['authority_hex']]
    e,g,c=env['body'],grant['body'],status['body'];ctx=case['context']
    fields(e,'kind profile network policy_ref type event_id issuer issuer_key subject recipient_agent recipient_node scope object mandate_ref links nonce issued_at expires_at payload_schema payload_b64 payload_hash')
    fields(g,'kind profile network policy_ref mandate_id issuer issuer_key subject subject_key recipient_agent recipient_node scope object not_before expires_at allow_delegation parent limits authority_epoch')
    fields(c,'kind profile network policy_ref identity issuer_key epoch sequence head mandate_ref mandate_state event_ref recipient_node status_nonce issued_at expires_at')
    demand(e['kind']=='envelope' and g['kind']=='mandate' and c['kind']=='authority-status','SCHEMA')
    for obj in [env,grant,status]+history:
        b=obj['body']
        demand(b['profile']==PROFILE,'PROFILE')
        demand(b['network']==ctx['network'] and b['policy_ref']==ctx['policy_ref'],'DOMAIN')
    for value,prefix in ((g['issuer'],'id:'),(g['subject'],'ag:'),(e['recipient_agent'],'ag:'),(g['recipient_agent'],'ag:'),(e['recipient_node'],'nd:'),(g['recipient_node'],'nd:'),(e['network'],'fd:'),(g['object'],'ch:')):
        demand(tag(value,prefix,32),'SCHEMA')
    demand(tag(e['policy_ref'],'sha256:',64),'SCHEMA')
    demand(tag(ctx['genesis_head'],'sha256:',64),'SCHEMA')
    demand(ctx['retained_available'] is True,'UNKNOWN_RECOVERY')
    demand(ctx['clock_ok'] is True and type(ctx['clock_uncertainty']) is int and 0<=ctx['clock_uncertainty']<=5,'UNKNOWN_CLOCK')
    demand(type(ctx['now']) is int and 0<=ctx['now']<=MAX,'SCHEMA')
    demand(type(ctx['owner_epoch']) is int and ctx['owner_epoch']==1,'UNKNOWN_KEY_HISTORY')
    root=unb64(ctx['owner_key_b64'],32)
    demand(g['issuer']==ctx['owner_identity'] and g['issuer_key']==kid(root),'AUTHORITY')
    signature(grant,root)
    demand(g['authority_epoch']==ctx['owner_epoch'] and type(g['authority_epoch']) is int,'AUTHORITY')
    demand(g['allow_delegation'] is False and g['parent'] is None,'DELEGATION')
    fields(g['limits'],'max_uses max_payload_bytes')
    demand(type(g['limits']['max_uses']) is int and g['limits']['max_uses']==1 and type(g['limits']['max_payload_bytes']) is int and 1<=g['limits']['max_payload_bytes']<=2048,'LIMIT')
    agent=unb64(g['subject_key'],32); signature(env,agent)
    demand(e['issuer']==g['subject'] and e['issuer_key']==kid(agent) and e['subject']==g['issuer'],'AUTHORITY')
    for b in (e,g): demand(b['recipient_node']==ctx['recipient_node'] and b['recipient_agent']==ctx['recipient_agent'],'RECIPIENT')
    demand(e['type']=='note.deliver' and e['scope']==g['scope']==['note.deliver'] and e['object']==g['object'],'SCOPE')
    demand(type(e['event_id']) is str and len(e['event_id'])==67 and e['event_id'].startswith('ev:') and all(v in '0123456789abcdef' for v in e['event_id'][3:]),'SCHEMA')
    demand(type(g['mandate_id']) is str and len(g['mandate_id'])==67 and g['mandate_id'].startswith('md:') and all(v in '0123456789abcdef' for v in g['mandate_id'][3:]),'SCHEMA')
    demand(e['mandate_ref']==ref(grant),'MANDATE_REF');unb64(e['nonce'],32)
    demand(e['event_id'] not in ctx['seen_event_ids'],'REPLAY')
    demand(type(ctx['mandate_uses']) is int and 0<=ctx['mandate_uses']<g['limits']['max_uses'],'LIMIT')
    demand(type(e['links']) is list and len(e['links'])<=8 and all(type(x) is str for x in e['links']) and e['links']==sorted(set(e['links'])) and all(tag(x,'sha256:',64) for x in e['links']),'LINKS')
    demand(all(tag(x,'sha256:',64) and x in ctx['known_refs'] for x in e['links']),'UNKNOWN_LINK')
    payload=unb64(e['payload_b64'])
    demand(e['payload_schema']=='text.utf8.v1','SCHEMA')
    demand(0<len(payload)<=g['limits']['max_payload_bytes'],'LIMIT')
    demand(e['payload_hash']=='sha256:'+hashlib.sha256(payload).hexdigest(),'PAYLOAD')
    try: text=payload.decode('utf-8',errors='strict')
    except UnicodeError as exc: raise Invalid('PAYLOAD') from exc
    demand(all(ord(x)>=32 or x in '\n\t' for x in text),'PAYLOAD')
    for b,start,cap in ((g,'not_before',3600),(e,'issued_at',120),(c,'issued_at',30)):
        lo,hi=b[start],b['expires_at'];n=ctx['now'];s=ctx['clock_uncertainty']
        demand(type(lo) is int and type(hi) is int and 0<=lo<hi<=MAX and hi-lo<=cap,'TIME')
        demand(lo<=n-s and n+s<hi,'TIME')
    parent=ctx['genesis_head']; seq=0;states={};frontiers={0:parent}
    for item in history:
        b=item['body'];fields(b,'kind profile network policy_ref identity issuer_key epoch sequence previous operation mandate_ref')
        demand(b['kind']=='authority-event' and b['identity']==ctx['owner_identity'] and b['issuer_key']==kid(root) and type(b['epoch']) is int and b['epoch']==1,'AUTHORITY')
        signature(item,root)
        demand(type(b['sequence']) is int and b['sequence']==seq+1 and b['previous']==parent,'HISTORY')
        demand(b['operation'] in ('mandate.issue','mandate.revoke'),'HISTORY')
        k=b['mandate_ref']
        demand(tag(k,'sha256:',64),'HISTORY')
        if b['operation']=='mandate.issue':
            demand(k not in states,'HISTORY');states[k]='active'
        else:
            demand(states.get(k)=='active','HISTORY');states[k]='revoked'
        seq+=1;parent=ref(item);frontiers[seq]=parent
    demand(c['identity']==ctx['owner_identity'] and c['issuer_key']==kid(root) and type(c['epoch']) is int and c['epoch']==1,'AUTHORITY')
    signature(status,root)
    demand(type(c['sequence']) is int and c['sequence']==seq and c['head']==parent,'HISTORY')
    demand(type(ctx['floor_sequence']) is int and ctx['floor_sequence']>=0,'SCHEMA')
    demand(seq>=ctx['floor_sequence'],'UNKNOWN_STALE')
    demand(frontiers.get(ctx['floor_sequence'])==ctx['floor_head'],'FORK')
    demand(c['mandate_ref']==ref(grant) and c['mandate_state']==states.get(ref(grant),'unknown'),'HISTORY')
    demand(c['event_ref']==ref(env) and c['recipient_node']==ctx['recipient_node'] and c['status_nonce']==ctx['expected_status_nonce'],'STATUS_BINDING')
    unb64(c['status_nonce'],32)
    demand(c['mandate_state']!='revoked','REVOKED')
    demand(c['mandate_state']=='active','UNKNOWN_MANDATE')
    return {'decision':'ACCEPT','code':'OK'}


if __name__=='__main__':
    for line in sys.stdin:
        if len(line)>2_000_000: print(json.dumps({'decision':'REJECT','code':'WIRE'}));continue
        print(json.dumps(validate(json.loads(line)),sort_keys=True))
