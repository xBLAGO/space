"""Offline bundle-only verifier. Standard library; no BLAGO, SQLite or network.

Checks consistency/completeness of supplied observations, not their independent
authenticity. A human/DeepSeek verdict is separate and never auto-issued here.
"""
import argparse
from datetime import datetime
import json
import html
from pathlib import Path
import re
import struct
import zlib

from tools.living_bundle_contract import (SCHEMA, VERIFIER, BRANCHES, ROLES, SURFACES,
    REASON, BANNER, DELIVERY_FIELDS, expected_matrix, key, sha, wire)

LIMIT = 32 * 1024 * 1024
SECRET_KEYS = {'cookie','cookies','set-cookie','authorization','token','access_token',
               'refresh_token','csrf','nonce','password'}
FIELDS = {'branch','checkpoint','step','role','session_id','request_id','invitation_id',
          'surface','action','visible_field_names','marker_presence','consent_state',
          'refusal_state','revocation_state','recipient_first_text_delivery','timestamp',
          'artifact','screenshot','banner_dom','banner_screenshot','request_text_sha256',
          'mask','counters','endpoint','http_status','response_field_names'}


def no_duplicates(pairs):
    result = {}
    for k,v in pairs:
        if k in result: raise ValueError('Duplicate JSON key')
        result[k]=v
    return result


def load(raw): return json.loads(raw, object_pairs_hook=no_duplicates)


def png_nonblank(raw):
    """Validate Chrome-style 8-bit RGB/RGBA PNG and reject blank images."""
    if not raw.startswith(b'\x89PNG\r\n\x1a\n'): raise ValueError('Not PNG')
    pos=8; image=bytearray(); shape=None; ended=False
    while pos < len(raw):
        if pos+12>len(raw): raise ValueError('Truncated PNG')
        n=struct.unpack('>I',raw[pos:pos+4])[0]; tag=raw[pos+4:pos+8]
        data=raw[pos+8:pos+8+n]; tail=raw[pos+8+n:pos+12+n]
        if n>LIMIT or len(data)!=n or len(tail)!=4: raise ValueError('PNG chunk bound')
        if zlib.crc32(tag+data)&0xffffffff != struct.unpack('>I',tail)[0]: raise ValueError('PNG CRC')
        if tag==b'IHDR':
            if shape is not None or n!=13: raise ValueError('PNG header')
            w,h,depth,color,compression,filtering,interlace=struct.unpack('>IIBBBBB',data)
            if not (320<=w<=4096 and 200<=h<=32768 and depth==8 and color in (2,6) and
                    compression==filtering==interlace==0): raise ValueError('Unsupported PNG profile')
            channels=3 if color==2 else 4; shape=(w,h,channels)
        elif tag==b'IDAT': image.extend(data)
        elif tag==b'IEND': ended=True; pos+=12+n; break
        pos+=12+n
    if not shape or not ended or pos!=len(raw): raise ValueError('Incomplete PNG')
    w,h,bpp=shape; stride=w*bpp; maximum=(stride+1)*h
    if maximum>64*1024*1024: raise ValueError('PNG decompression bound')
    decoder=zlib.decompressobj(); data=decoder.decompress(bytes(image),maximum+1)
    if len(data)!=maximum or not decoder.eof or decoder.unused_data: raise ValueError('PNG decoded size')
    previous=bytearray(stride); colors=set(); i=0
    for _ in range(h):
        kind=data[i]; row=bytearray(data[i+1:i+1+stride]); i+=stride+1
        if kind not in range(5): raise ValueError('PNG filter')
        for x in range(stride):
            a=row[x-bpp] if x>=bpp else 0; b=previous[x]; c=previous[x-bpp] if x>=bpp else 0
            if kind==1: predictor=a
            elif kind==2: predictor=b
            elif kind==3: predictor=(a+b)//2
            elif kind==4:
                p=a+b-c; distances=(abs(p-a),abs(p-b),abs(p-c)); predictor=(a,b,c)[distances.index(min(distances))]
            else: predictor=0
            row[x]=(row[x]+predictor)&255
        if len(colors)<32:
            for x in range(0,stride,bpp):
                if bpp==3 or row[x+3]>0: colors.add(bytes(row[x:x+3]))
                if len(colors)>=32: break
        previous=row
    if len(colors)<16: raise ValueError('Blank/near-solid screenshot')
    return {'width':w,'height':h}


def forbidden_keys(value):
    if isinstance(value,dict):
        for k,v in value.items():
            if k.lower() in SECRET_KEYS or k.startswith('REFUSAL_REASON_'): return True
            if forbidden_keys(v): return True
    if isinstance(value,list): return any(forbidden_keys(v) for v in value)
    return False


def strings(value):
    if isinstance(value,str): return [value]
    if isinstance(value,dict): return [s for k,v in value.items() for s in [str(k),*strings(v)]]
    if isinstance(value,list): return [s for v in value for s in strings(v)]
    return []


class Bundle:
    def __init__(self, directory): self.root=Path(directory).resolve()
    def read(self, name):
        path=Path(name)
        if path.is_absolute() or '..' in path.parts or path.suffix not in ('.json','.jsonl','.txt','.html','.png','.md'):
            raise ValueError('Invalid artifact path/type; databases are never read')
        target=self.root/path
        if not target.resolve().is_relative_to(self.root) or any(p.is_symlink() for p in [target,*target.parents] if p!=self.root.parent):
            raise ValueError('Artifact symlink/escape')
        if target.stat().st_size>LIMIT: raise ValueError('Artifact bound')
        return target.read_bytes()


def verify(directory):
    b=Bundle(directory); errors=[]
    try:
        manifest=load(b.read('manifest.json')); matrix=load(b.read('visibility-matrix.json'))
        if manifest['schema_version']!=SCHEMA or manifest['verifier_version']!=VERIFIER: raise ValueError('Unknown schema/verifier')
        if forbidden_keys(manifest): raise ValueError('Credentials/refusal reason fields in manifest')
        seed=manifest['seed']; markers=seed['markers']
        if seed['refusal_reason']!=REASON: raise ValueError('Refusal reasons are not collected')
        if set(manifest['branches'])!=set(BRANCHES): raise ValueError('Missing/extra branch')
        if manifest['capture_started_at'] is not None:
            frozen=datetime.fromisoformat(matrix['frozen_at'].replace('Z','+00:00'))
            started=datetime.fromisoformat(manifest['capture_started_at'].replace('Z','+00:00'))
            if frozen>started: raise ValueError('Expected matrix frozen after capture')
        for branch in manifest['branches'].values():
            if not re.fullmatch('[0-9a-f]{64}',branch['initial_database_sha256']): raise ValueError('Initial DB SHA required')
        if manifest['cleanup_method']!='CLOSE_BROWSER_PROCESS_AND_ALL_CONTEXTS; FRESH_LOGINS':
            raise ValueError('Unspecified restart/cleanup procedure')
        if not re.fullmatch('[0-9a-f]{64}',markers['RECIPIENT_ID_C']): raise ValueError('Actual synthetic participant code required')
        if not markers['NOTE_SECRET'].startswith('NOTE_SECRET_'): raise ValueError('Positive note control required')
        text_hash=sha(seed['request_text'].encode())
        required=expected_matrix(audience_change=manifest['plan']['author_audience_change'])
        if matrix['expected']!=required or manifest['expected_matrix_sha256']!=sha(wire(required)):
            raise ValueError('Expected matrix changed/weakened')
        if len(required)>2000: raise ValueError('Matrix bound')
        for name, digest in manifest['artifacts'].items():
            if sha(b.read(name))!=digest: raise ValueError('Artifact hash mismatch: '+name)
        rows=[load(line) for line in b.read('audit.jsonl').splitlines() if line]
        if len(rows)>10000: raise ValueError('Audit bound')
        grouped={key(r):[] for r in required}; sessions={}; first={}; timestamps={}; seen=set(); images={}
        for row in rows:
            if set(row)!=FIELDS: raise ValueError('Audit field set differs from fixed schema')
            k=key(row)
            if k not in grouped: raise ValueError('Unexpected role/step/surface')
            if forbidden_keys(row): raise ValueError('Credentials or unsupported refusal reason')
            parsed=datetime.fromisoformat(row['timestamp'].replace('Z','+00:00'))
            if parsed.tzinfo is None: raise ValueError('Timezone required')
            previous=timestamps.get(row['branch'])
            if previous and parsed<previous: raise ValueError('Audit chronology')
            timestamps[row['branch']]=parsed
            if not re.fullmatch('[A-Za-z0-9_-]{1,80}',row['session_id']): raise ValueError('Opaque context label required')
            owner=sessions.setdefault(row['session_id'],(row['branch'],row['role']))
            if owner!=(row['branch'],row['role']): raise ValueError('Context reused across branches/roles')
            if type(row['recipient_first_text_delivery']) is not bool: raise ValueError('Delivery event must be boolean')
            if row['checkpoint']!=next(r['checkpoint'] for r in required if key(r)==k): raise ValueError('Checkpoint mismatch')
            event=(*k,row['request_id'])
            if event in seen: raise ValueError('Duplicate audit observation')
            seen.add(event)
            if set(row['marker_presence'])!=set(markers) or any(type(v) is not bool for v in row['marker_presence'].values()):
                raise ValueError('Marker observations must be explicit booleans before masking')
            for artifact in (row['artifact'],row['screenshot']):
                if artifact is not None and artifact not in manifest['artifacts']: raise ValueError('Unhashed observation artifact')
            payload=b.read(row['artifact']); surface=row['surface']
            if not payload.strip(): raise ValueError('Empty observed surface')
            if surface in ('visible','full_dom'):
                content=payload.decode()
                if BANNER not in content or len(content.strip())<=len(BANNER)+10: raise ValueError('Empty screen/banner absent')
                if not row['banner_dom'] or not row['banner_screenshot']: raise ValueError('Banner not verified on both surfaces')
                raw_image=b.read(row['screenshot']); image_hash=sha(raw_image)
                if image_hash not in images: images[image_hash]=png_nonblank(raw_image)
                decoded=html.unescape(content)
            else:
                value=load(payload)
                decoded='\n'.join(strings(value))
                if forbidden_keys(value): raise ValueError('Credentials/refusal reason in network artifact')
                if not row['request_id']: raise ValueError('Network request label required')
                if row['action']=='recipient_receive' and row['marker_presence']['REQ_TEXT']:
                    body=value['response_body']
                    if tuple(sorted(body))!=tuple(sorted(DELIVERY_FIELDS)): raise ValueError('Unexpected invitation response fields')
                    if body['task']!=seed['request_text'] or body['contact_only'] is not True or body['resource_available'] is not False:
                        raise ValueError('Task truncated/substituted or contact implies resource')
                if row['action']=='recipient_decline':
                    body=value['request_body']; data=body.get('data',body)
                    if set(data)!={'id'}: raise ValueError('decline_invitation accepts only id')
            for name,marker in markers.items():
                if (marker in decoded) != row['marker_presence'][name]: raise ValueError('Marker flags contradict persisted artifact')
            if row['request_text_sha256'] is not None:
                if row['request_text_sha256']!=text_hash or seed['request_text'] not in decoded:
                    raise ValueError('Whole request text not present or substituted')
            if row['mask'] is not None and row['mask']!={'policy':'marker-only-v1','scan_before_mask':True,'full_note_persisted':False}:
                raise ValueError('Invalid note redaction evidence')
            if row['mask'] is not None and row['role']!='B': raise ValueError('Unauthorized surface must not be cleaned into PASS')
            if row['mask'] is not None and '[MASKED]' not in decoded: raise ValueError('Missing masked-note replacement')
            if row['role']=='C' and surface=='network_response' and row['marker_presence']['REQ_TEXT']:
                first.setdefault(row['branch'],row)
            grouped[k].append(row)
        actual=[]
        for expected in required:
            k=key(expected); observations=grouped[k]
            if not observations:
                errors.append('MISSING_OBSERVATION:'+':'.join(k)); continue
            union={name:any(r['marker_presence'][name] for r in observations) for name in markers}
            for name,policy in expected['markers'].items():
                if policy=='REQUIRED' and not union[name] or policy=='FORBIDDEN' and union[name]:
                    errors.append('MARKER_POLICY:'+':'.join(k)+':'+name)
            if expected['full_request_required'] and not any(r['request_text_sha256']==text_hash for r in observations):
                errors.append('INCOMPLETE_REQUEST_TEXT:'+':'.join(k))
            if expected['note_mask_required'] and any(r['mask'] is None for r in observations): errors.append('NOTE_NOT_MASKED:'+':'.join(k))
            if expected['counters_zero_required'] and any(r['counters']!={'offers':0,'published_resources':0,'reservations':0} for r in observations):
                errors.append('CONTACT_CREATED_ECONOMIC_ACTION:'+':'.join(k))
            if expected['branch']=='decline' and expected['role']=='A' and any(r['refusal_state']!='NOT_OBSERVABLE' for r in observations):
                errors.append('AUTHOR_REFUSAL_ORACLE:'+':'.join(k))
            actual.append({'branch':k[0],'step':k[1],'role':k[2],'surface':k[3],
                           'marker_presence':union,'observations':len(observations)})
        if matrix['actual']!=actual: errors.append('ACTUAL_MATRIX_DOES_NOT_MATCH_AUDIT')
        # Compare actual author content; a presence-bit matrix alone cannot
        # prove that counts, status text or action lists did not change.
        author=[]
        for step in ('recipient_inbox','recipient_receive','recipient_decline'):
            visible=grouped[('decline',step,'A','visible')]
            network=grouped[('decline',step,'A','network_response')]
            author.append(([(b.read(r['artifact']),r['visible_field_names']) for r in visible],
                sorted([(r['endpoint'],r['http_status'],wire(load(b.read(r['artifact']))['response_body'])) for r in network])))
        if author[0]!=author[1] or author[1]!=author[2]: errors.append('AUTHOR_OBSERVABLE_STATE_CHANGED')
        for role in ROLES:
            before={r['session_id'] for r in rows if r['branch']=='decline' and r['role']==role and r['step']=='recipient_decline'}
            after={r['session_id'] for r in rows if r['branch']=='decline' and r['role']==role and r['step']=='restart_cleanup_relogin'}
            if not before or not after or before & after: errors.append('BROWSER_CONTEXT_NOT_REPLACED:'+role)
        for role in ROLES:
            observations=grouped[('consent','contact_consent',role,'network_response')]
            if observations and not any(load(b.read(r['artifact']))['response_body'].get('counters') ==
                    {'offers':0,'published_resources':0,'reservations':0} for r in observations):
                errors.append('CONTACT_COUNTERS_NOT_OBSERVED:'+role)
        for branch in BRANCHES:
            observed=first.get(branch)
            if branch=='revoke' and observed: errors.append('BLOCKED:RECIPIENT_ALREADY_RECEIVED_TEXT_BEFORE_REVOKE')
            if branch!='revoke':
                if not observed or not observed['recipient_first_text_delivery']: errors.append('MISSING_FIRST_TEXT_DELIVERY:'+branch)
                elif observed['action']!='recipient_receive': errors.append('UNEXPECTED_FIRST_TEXT_DELIVERY:'+branch)
            flags=[r for r in rows if r['branch']==branch and r['recipient_first_text_delivery']]
            if len(flags)!=(0 if branch=='revoke' else 1): errors.append('FIRST_DELIVERY_EVENT_COUNT:'+branch)
        if manifest['local_ui']['sha256']!=sha(wire(manifest['local_ui']['files'])): errors.append('MISSING_SCREEN_PROVENANCE')
        if len({v['initial_database_sha256'] for v in manifest['branches'].values()})!=1: errors.append('DIFFERENT_INITIAL_STATES')
        blocked=any(v['status']=='BLOCKED' for v in manifest['branches'].values())
        report=b.read('report.md').decode()
        if (blocked or errors) and (manifest['claimed_verdict']=='PASS' or '# VERDICT: PASS' in report): errors.append('FALSE_PASS_CLAIM')
        if blocked: errors.append('BLOCKED_BRANCHES')
        if manifest['target_integration']!='EXECUTED': errors.append('TARGET_FLOW_NOT_EXECUTED')
        if errors: return {'verdict':'BLOCKED' if blocked else 'FAIL','errors':errors,'exit_code':2 if blocked else 1}
        return {'verdict':'BUNDLE_CONSISTENT_NOT_INDEPENDENT_ATTESTATION','errors':[], 'exit_code':0,
                'deepseek_verdict':'NOT_PROVIDED; must be issued by independent reviewer'}
    except (OSError,ValueError,KeyError,TypeError,struct.error,zlib.error) as exc:
        return {'verdict':'INVALID_BUNDLE','errors':[str(exc)],'exit_code':1}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('bundle',type=Path)
    args=p.parse_args();result=verify(args.bundle);print(json.dumps(result,ensure_ascii=False,indent=2));raise SystemExit(result['exit_code'])


if __name__=='__main__': main()
