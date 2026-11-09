"""Offline byte inspector ONLY, no authority, delivery, DB, network or agent execution.
Not a production verifier. Frozen UEL lab ASCII subset, not full JCS.
"""
import base64
import hashlib
import json
import re
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

PROFILE='uel-0.1-lab.1'
DOMAIN=b'BLAGO/UEL/0.1-lab.1/EVENT\0'
OBJECT_DOMAIN=b'BLAGO/UEL/0.1-lab.1/OBJECT\0'
FIELDS={'profile','kind','network','policy_ref','event_id','type','schema','issuer','issuer_key','issuer_role',
        'subject','object','object_version','previous','parents','authority_ref','authority_epoch','disclosure',
        'created_at','payload_schema','payload_b64','payload_hash'}


class Invalid(ValueError):
    pass


def b64(raw):return base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii')


def unb64(value,size=None):
    if type(value) is not str or not re.fullmatch('[A-Za-z0-9_-]+',value):raise Invalid('ENCODING')
    try:raw=base64.urlsafe_b64decode(value+'='*((-len(value))%4))
    except Exception as exc:raise Invalid('ENCODING') from exc
    if b64(raw)!=value or (size is not None and len(raw)!=size):raise Invalid('ENCODING')
    return raw


def hashref(raw):return 'sha256:'+hashlib.sha256(raw).hexdigest()


def tag(value,prefix,n):return type(value) is str and re.fullmatch(re.escape(prefix)+'[0-9a-f]{'+str(n)+'}',value) is not None


def canonical(value,depth=0):
    if depth>8:raise Invalid('BOUNDS')
    if type(value) is dict:
        if len(value)>64:raise Invalid('BOUNDS')
        for key in value:canonical(key,depth+1)
        return b'{'+b','.join(canonical(k,depth+1)+b':'+canonical(value[k],depth+1) for k in sorted(value))+b'}'
    if type(value) is list:
        if len(value)>64:raise Invalid('BOUNDS')
        return b'['+b','.join(canonical(v,depth+1) for v in value)+b']'
    if type(value) is str:
        if len(value)>4096:raise Invalid('BOUNDS')
        if any(not 32<=ord(c)<=126 for c in value):raise Invalid('WIRE')
        return ('"'+value.replace('\\','\\\\').replace('"','\\"')+'"').encode('ascii')
    if value is None:return b'null'
    if type(value) is bool:return b'true' if value else b'false'
    if type(value) is int and 0<=value<=9007199254740991:return str(value).encode('ascii')
    raise Invalid('WIRE')


def parse(raw):
    if len(raw)>16384:raise Invalid('BOUNDS')
    def pairs(values):
        d={}
        for k,v in values:
            if k in d:raise Invalid('DUPLICATE_KEY')
            d[k]=v
        return d
    def bad_number(_):raise Invalid('WIRE')
    try:
        value=json.loads(raw.decode('ascii'),object_pairs_hook=pairs,parse_float=bad_number,parse_constant=bad_number)
        if canonical(value)!=raw:raise Invalid('WIRE')
        return value
    except (UnicodeError,json.JSONDecodeError,RecursionError) as exc:raise Invalid('WIRE') from exc


def inspect(raw,context):
    try:
        obj=parse(raw)
        if type(obj) is not dict or set(obj)!={'alg','body','signature'} or obj['alg']!='Ed25519':raise Invalid('SCHEMA')
        b=obj['body']
        if type(b) is not dict or set(b)!=FIELDS:raise Invalid('SCHEMA')
        if b['profile']!=PROFILE:raise Invalid('PROFILE')
        if b['kind']!='event' or b['type']!='source.observation' or b['schema']!='blago.inert-observation-frame.v1' or b['payload_schema']!='text.utf8.v1':raise Invalid('SCHEMA')
        if b['disclosure']!='FEDERATED':raise Invalid('DISCLOSURE')
        if b['issuer_role']!='NODE' or b['issuer']!=context['issuer'] or b['issuer_role']!=context['role']:raise Invalid('ROLE')
        if b['network']!=context['network'] or b['policy_ref']!=context['policy_ref']:raise Invalid('DOMAIN')
        for name,prefix,n in [('event_id','ev:',64),('object','ob:',64),('subject','ps:',32),('issuer','nd:',32)]:
            if not tag(b[name],prefix,n):raise Invalid('SCHEMA')
        for name in ('object_version','authority_epoch'):
            if type(b[name]) is not int or b[name]<1:raise Invalid('SCHEMA')
        if type(b['created_at']) is not int:raise Invalid('SCHEMA')
        if not tag(b['authority_ref'],'sha256:',64):raise Invalid('SCHEMA')
        if b['object_version']==1 and b['previous'] is not None:raise Invalid('LINKS')
        if b['object_version']>1 and not tag(b['previous'],'sha256:',64):raise Invalid('LINKS')
        if type(b['parents']) is not list or len(b['parents'])>8 or any(not tag(v,'sha256:',64) for v in b['parents']) or b['parents']!=sorted(set(b['parents'])):raise Invalid('LINKS')
        payload=unb64(b['payload_b64'])
        if not 1<=len(payload)<=2048:raise Invalid('BOUNDS')
        try:text=payload.decode('utf-8',errors='strict')
        except UnicodeError as exc:raise Invalid('PAYLOAD') from exc
        if any(ord(c)<32 and c not in '\n\t' for c in text):raise Invalid('PAYLOAD')
        if b['payload_hash']!=hashref(payload):raise Invalid('PAYLOAD')
        key=unb64(context['public_key'],32)
        if b['issuer_key']!=hashref(key):raise Invalid('KEY')
        signature=unb64(obj['signature'],64)
        try:Ed25519PublicKey.from_public_bytes(key).verify(signature,DOMAIN+canonical(b))
        except (InvalidSignature,ValueError) as exc:raise Invalid('SIGNATURE') from exc
        return 'WIRE_OK_NOT_AUTHORIZED'
    except Invalid as exc:return str(exc)


def object_ref(obj):return hashref(OBJECT_DOMAIN+canonical(obj))


def public_leaf(salt,release):
    if len(salt)!=32:raise Invalid('SALT')
    if type(release) is not dict or set(release)!={'format','release_id','metric','value','privacy_policy_ref'} or release['format']!='blago.public-aggregate.v1':raise Invalid('PUBLIC_SCHEMA')
    return b'BLAGO/UEL/0.1-lab.1/PUBLIC-LEAF\0'+salt+canonical(release)


def merkle(leaves):
    # RFC9162-style tree shape/hash prefixes; not a deployed CT service.
    if not leaves:return hashlib.sha256(b'').digest()
    if len(leaves)==1:return hashlib.sha256(b'\x00'+leaves[0]).digest()
    k=1<<((len(leaves)-1).bit_length()-1)
    return hashlib.sha256(b'\x01'+merkle(leaves[:k])+merkle(leaves[k:])).digest()
