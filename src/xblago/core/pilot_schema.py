"""Additive 05.4 lineage coverage. Frozen 05.1–05.3 checksums are untouched.

Activate on a reviewed current primary COPY before taking certified baselines.
An arbitrary old backup must not be re-labelled as a reviewed baseline.
"""
import hashlib
import secrets
from pathlib import Path

from xblago.core.security_journal import JournalUnavailable

VERSION='foundation05_pilot_004'


def lineage(name,table,operation,kind,old,new):
    return f'''CREATE TRIGGER {name} BEFORE UPDATE OF {operation} ON {table}
        WHEN blago_replaying()=0 AND {old}<>{new} BEGIN
        INSERT INTO privacy_outbox VALUES (lower(hex(randomblob(16))),json_object(
            'kind','{kind}','owner',blago_tag('user',OLD.blago_user_id),
            'target',{old},'state',{new},'recorded_at',CAST(strftime('%s','now') AS INTEGER))); END'''


DDL=(
    '''CREATE TABLE pilot_safety_state (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1),
        generation TEXT NOT NULL, lineage_start_seq INTEGER NOT NULL CHECK(lineage_start_seq>=0)
    )''',
    '''CREATE TABLE pilot_schema_migrations (
        version TEXT PRIMARY KEY NOT NULL, checksum TEXT NOT NULL
    )''',
    lineage('pilot_memory_lineage','memory_facts','blago_user_id,key,value_json,updated_at','memory_lineage',
            "blago_tag('memory_legacy',OLD.blago_user_id,OLD.key,OLD.value_json,OLD.updated_at)",
            "blago_tag('memory_legacy',NEW.blago_user_id,NEW.key,NEW.value_json,NEW.updated_at)"),
    lineage('pilot_history_lineage','dialogue_turns','id,blago_user_id,created_at,role,text','history_lineage',
            "blago_tag('history',OLD.id,OLD.blago_user_id,OLD.created_at,OLD.role,OLD.text)",
            "blago_tag('history',NEW.id,NEW.blago_user_id,NEW.created_at,NEW.role,NEW.text)"),
    '''CREATE TRIGGER pilot_cache_clear BEFORE UPDATE OF response_json,error ON events
        WHEN blago_replaying()=0 AND (OLD.response_json IS NOT NULL OR OLD.error IS NOT NULL)
        AND NEW.response_json IS NULL AND NEW.error IS NULL BEGIN
        INSERT INTO privacy_outbox VALUES (lower(hex(randomblob(16))),json_object(
            'kind','response_cache','owner',blago_tag('user',OLD.blago_user_id),
            'target',blago_tag('response_cache',OLD.channel,OLD.external_update_id),
            'state','','recorded_at',CAST(strftime('%s','now') AS INTEGER))); END''',
)
CHECKSUM=hashlib.sha256('\n'.join(DDL).encode()).hexdigest()


def validate(db,required=False):
    objects=dict(db.execute("SELECT name,sql FROM sqlite_master WHERE name LIKE 'pilot_%' AND sql IS NOT NULL"))
    if not objects:
        if required:
            raise JournalUnavailable('Pilot lineage migration/certified baseline required')
        return False
    if any(objects.get(sql.split()[2])!=sql for sql in DDL):
        raise JournalUnavailable('Pilot schema collision or drift')
    if [tuple(r) for r in db.execute('SELECT version,checksum FROM pilot_schema_migrations')]!=[(VERSION,CHECKSUM)]:
        raise JournalUnavailable('Pilot migration checksum mismatch')
    state=db.execute('SELECT generation,lineage_start_seq FROM pilot_safety_state WHERE singleton=1').fetchone()
    cursor=db.execute('SELECT applied_seq FROM privacy_state WHERE singleton=1').fetchone()
    if not state or not state[0] or type(state[1]) is not int or state[1]<0 or state[1]>cursor[0]:
        raise JournalUnavailable('Pilot lineage baseline/cursor mismatch')
    return True


def migrate_pilot(path: Path):
    from xblago.core.storage import SQLiteBlagoStore
    if not path.is_file():
        raise ValueError('An existing reviewed offline primary copy is required')
    store=SQLiteBlagoStore(path)
    with store._locked_transaction() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='privacy_state'").fetchone():
            raise ValueError('Apply Foundation 05.3 and bind independent journal first')
        if validate(db):
            return False
        seq=db.execute('SELECT applied_seq FROM privacy_state WHERE singleton=1').fetchone()[0]
        for statement in DDL:
            db.execute(statement)
        db.execute('INSERT INTO pilot_safety_state VALUES (1,?,?)',(secrets.token_hex(16),seq))
        db.execute('INSERT INTO pilot_schema_migrations VALUES (?,?)',(VERSION,CHECKSUM))
    return True


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database',type=Path)
    args=parser.parse_args()
    print('applied' if migrate_pilot(args.database) else 'already applied')
