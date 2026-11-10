"""Local minimal shadow audit. No domain state machine, content copy or grant replay."""
import json
import re
from xblago.core import uel_schema as schema
from xblago.core.security_journal import JournalUnavailable,canonical

ZERO='0'*64
FIELDS={'sequence','source_kind','source_ref','object_ref','source_version','phase','coverage','previous','causal_ref','payload_ref','source_state','record_ref'}


def unavailable():raise JournalUnavailable('UEL UNKNOWN_RECOVERY: unverified index/source/frontier')


def target(journal):return journal.tag('uel-local-binding-v2',journal.journal_id)


def seal(record,journal):return journal.tag('uel-local-record-v2',canonical({k:v for k,v in record.items() if k!='record_ref'}))


def heads(db):
    result={}
    for r in db.execute('SELECT * FROM uel_events ORDER BY sequence'):result[(r['source_kind'],r['object_ref'])]=dict(r)
    return result


def insert_record(db,r,journal):
    if type(r) is not dict or set(r)!=FIELDS or type(r['sequence']) is not int or type(r['source_version']) is not int or r['sequence']<1 or r['source_version']<1:unavailable()
    if r['source_kind'] not in ('ASSET','HELP') or r['phase'] not in ('OBSERVED','RETIRED') or r['coverage'] not in ('FROM_ORIGIN','BASELINE_CURRENT'):unavailable()
    for field in ('source_ref','object_ref','previous','causal_ref','source_state','record_ref'):
        if type(r[field]) is not str or not re.fullmatch('[0-9a-f]{64}',r[field]):unavailable()
    if (r['phase']=='OBSERVED' and (type(r['payload_ref']) is not str or not re.fullmatch('[0-9a-f]{64}',r['payload_ref']))) or (r['phase']=='RETIRED' and r['payload_ref'] is not None):unavailable()
    if seal(r,journal)!=r['record_ref']:unavailable()
    old=db.execute('SELECT * FROM uel_events WHERE sequence=?',(r['sequence'],)).fetchone()
    if old:
        if dict(old)!=r:unavailable()
        return
    state=db.execute('SELECT sequence,head FROM uel_state WHERE singleton=1').fetchone()
    if not state or r['sequence']!=state[0]+1 or r['previous']!=state[1]:unavailable()
    prior=db.execute('SELECT * FROM uel_events WHERE source_kind=? AND object_ref=? ORDER BY sequence DESC LIMIT 1',(r['source_kind'],r['object_ref'])).fetchone()
    if prior:
        if prior['phase']=='RETIRED' or r['causal_ref']!=prior['record_ref'] or r['coverage']!=prior['coverage']:unavailable()
        if r['source_version']!=prior['source_version']+(r['phase']=='OBSERVED'):unavailable()
    elif r['phase']!='OBSERVED' or r['causal_ref']!=ZERO or (r['coverage']=='FROM_ORIGIN' and r['source_version']!=1):unavailable()
    cols=sorted(FIELDS)
    db.execute('INSERT INTO uel_events ('+','.join(cols)+') VALUES ('+','.join('?' for _ in cols)+')',tuple(r[k] for k in cols))
    db.execute('UPDATE uel_state SET sequence=?,head=? WHERE singleton=1',(r['sequence'],r['record_ref']))


def apply_checkpoint(db,event,journal):
    schema.validate(db,mandatory=True)
    if event['target']!=target(journal) or event['owner']!='':unavailable()
    insert_record(db,json.loads(event['state']),journal)


def retained(control,db):
    result=[]
    for row in control.rows:
        e=json.loads(row[2])
        if e['kind']=='uel_local':result.append(e)
    for row in db.execute('SELECT payload FROM privacy_outbox ORDER BY rowid'):
        e=json.loads(row[0])
        if e['kind']=='uel_local':result.append(e)
    return result


def check(db,control):
    if not schema.validate(db):return
    binding=db.execute('SELECT journal_id FROM uel_binding WHERE singleton=1').fetchone()
    if not binding or binding[0]!=control.journal.journal_id:unavailable()
    rows=[dict(r) for r in db.execute('SELECT * FROM uel_events ORDER BY sequence')];events=retained(control,db)
    if len(rows)!=len(events):unavailable()
    previous=ZERO;by_object={}
    for i,(row,e) in enumerate(zip(rows,events),1):
        if e['target']!=target(control.journal) or e['owner']!='' or row!=json.loads(e['state']) or row['sequence']!=i or row['previous']!=previous or seal(row,control.journal)!=row['record_ref']:unavailable()
        key=(row['source_kind'],row['object_ref']);prior=by_object.get(key)
        if row['causal_ref']!=(prior['record_ref'] if prior else ZERO):unavailable()
        by_object[key]=row;previous=row['record_ref']
    state=db.execute('SELECT sequence,head FROM uel_state WHERE singleton=1').fetchone()
    if not state or tuple(state)!=(len(rows),previous):unavailable()


def sources(db,c):
    """Private SQL references only. Never persist this mapping or return raw content."""
    from xblago.domain.asset_registry import fingerprint as asset_fingerprint
    from xblago.domain.help_service import fingerprint as help_fingerprint
    latest={}
    for row in c.rows:
        e=json.loads(row[2])
        if e['kind'] in ('asset_state','help_state'):latest[(e['kind'],e['target'])]=e['state']
    for row in db.execute('SELECT payload FROM privacy_outbox ORDER BY rowid'):
        e=json.loads(row[0])
        if e['kind'] in ('asset_state','help_state'):latest[(e['kind'],e['target'])]=e['state']
    result={};seen=set()
    for kind,table,col,versions,check_fn,namespace,holds in (
            ('ASSET','assetreg_records','asset_id','assetreg_versions',asset_fingerprint,'asset-record','assetreg_holds'),
            ('HELP','help_requests','id','help_events',help_fingerprint,'help-request','help_holds')):
        for row in db.execute('SELECT * FROM '+table):
            oid=row[col];ctag=c.journal.tag(namespace,oid)
            if db.execute('SELECT 1 FROM '+holds+' WHERE target_tag=?',(ctag,)).fetchone():
                if kind=='ASSET' or row['state']!='WITHDRAWN' or row['text'] or row['selected_offer'] or row['owner_confirmed'] or row['helper_confirmed'] or db.execute("SELECT 1 FROM help_audience WHERE request_id=? UNION ALL SELECT 1 FROM help_offers WHERE request_id=? AND note<>'' UNION ALL SELECT 1 FROM help_challenges WHERE request_id=?",(oid,oid,oid)).fetchone():unavailable()
                continue
            if check_fn(db,oid,c.journal)!=row['head_tag'] or latest.get(('asset_state' if kind=='ASSET' else 'help_state',ctag))!=row['head_tag']:unavailable()
            rows=[dict(r) for r in db.execute('SELECT * FROM '+versions+' WHERE '+('asset_id' if kind=='ASSET' else 'request_id')+'=? ORDER BY version',(oid,))]
            if not rows or rows[-1]['version']!=row['version']:unavailable()
            seen.add(('asset_state' if kind=='ASSET' else 'help_state',ctag))
            result[(kind,ctag)]={'id':oid,'root':dict(row),'rows':rows,'state':row['head_tag']}
    for kind,tag in latest:
        if (kind,tag) not in seen and not db.execute('SELECT 1 FROM '+('assetreg_holds' if kind=='asset_state' else 'help_holds')+' WHERE target_tag=?',(tag,)).fetchone():unavailable()
    return result


def row_refs(kind,row,journal):
    return (journal.tag('uel-source-v2',kind,row['event_id']),journal.tag('uel-protected-payload-v2',kind,canonical(row)))


def append(db,c,key,version,phase,coverage,source_ref,payload_ref,source_state):
    if phase=='OBSERVED' and db.execute("SELECT count(*) FROM uel_events WHERE phase='OBSERVED'").fetchone()[0]>=8192:
        raise JournalUnavailable('UEL synthetic observation limit; privacy retirements remain permitted')
    seq,previous=db.execute('SELECT sequence,head FROM uel_state WHERE singleton=1').fetchone()
    prior=db.execute('SELECT * FROM uel_events WHERE source_kind=? AND object_ref=? ORDER BY sequence DESC LIMIT 1',key).fetchone()
    r={'sequence':seq+1,'source_kind':key[0],'object_ref':key[1],'source_ref':source_ref,'source_version':version,
       'phase':phase,'coverage':coverage,'previous':previous,'causal_ref':prior['record_ref'] if prior else ZERO,
       'payload_ref':payload_ref,'source_state':source_state}
    r['record_ref']=seal(r,c.journal);insert_record(db,r,c.journal)
    e={'kind':'uel_local','owner':'','target':target(c.journal),'state':canonical(r),'recorded_at':0}
    db.execute('INSERT INTO privacy_outbox VALUES (?,?)',('uel:'+r['record_ref'],canonical(e)))


def synchronize(db,c,*,baseline=False,at_start=False):
    if not schema.validate(db):return False
    check(db,c);live=sources(db,c);indexed=heads(db);changed=False
    for key,source in sorted(live.items()):
        prior=indexed.get(key)
        if prior and prior['phase']=='RETIRED':unavailable()
        version=source['root']['version']
        by_version={r['version']:r for r in source['rows']}
        for indexed_row in db.execute("SELECT source_version,source_ref,payload_ref FROM uel_events WHERE source_kind=? AND object_ref=? AND phase='OBSERVED'",key):
            original=by_version.get(indexed_row['source_version'])
            if original is None or row_refs(key[0],original,c.journal)!=(indexed_row['source_ref'],indexed_row['payload_ref']):unavailable()
        if prior and prior['source_version']==version:
            ref,payload=row_refs(key[0],source['rows'][-1],c.journal)
            if prior['source_ref']!=ref or prior['payload_ref']!=payload or prior['source_state']!=source['state']:unavailable()
            continue
        if at_start and (not baseline or prior):unavailable()  # Never backfill an unobserved positive on a read.
        if prior and version<=prior['source_version']:unavailable()
        coverage=prior['coverage'] if prior else ('BASELINE_CURRENT' if baseline else 'FROM_ORIGIN')
        pending=[r for r in source['rows'] if r['version']>(prior['source_version'] if prior else 0)]
        if baseline and not prior:pending=pending[-1:]
        expected=prior['source_version']+1 if prior else (version if baseline else 1)
        for row in pending:
            if row['version']!=expected:unavailable()
            ref,payload=row_refs(key[0],row,c.journal)
            append(db,c,key,row['version'],'OBSERVED',coverage,ref,payload,source['state']);expected+=1;changed=True
    for key,prior in sorted(indexed.items()):
        if key not in live:
            holds='assetreg_holds' if key[0]=='ASSET' else 'help_holds'
            if not db.execute('SELECT 1 FROM '+holds+' WHERE target_tag=?',(key[1],)).fetchone():unavailable()
            if prior['phase']=='RETIRED':continue
            append(db,c,key,prior['source_version'],'RETIRED',prior['coverage'],c.journal.tag('uel-retirement-v2',key[0],key[1],prior['record_ref']),None,c.journal.tag('uel-retired-v2',key[0],key[1]));changed=True
    check(db,c)
    return changed
