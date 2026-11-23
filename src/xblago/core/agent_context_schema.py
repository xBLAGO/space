"""Explicit, additive synthetic-lab migration; retained history exclusion barriers.

The existing privacy journal is authoritative. No private text or second journal.
An old snapshot without this schema fails closed when retained barriers exist.
"""
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from xblago.core.security_journal import JournalUnavailable, SecurityJournal

VERSION = 'agent_context_001'
KIND = 'agent_history_floor'
ADDRESS_KIND = 'agent_address_permission'
FLAG = 'XBLAGO_AGENT_CONTEXT_ENABLED'
DDL = ['CREATE TABLE agent_context_migrations (version TEXT PRIMARY KEY, checksum TEXT NOT NULL)']


def barrier(owner):
    return ("INSERT INTO privacy_outbox VALUES (lower(hex(randomblob(16))),"
            "json_object('kind','agent_history_floor','owner',blago_tag('user'," + owner + "),"
            "'target',blago_tag('agent-history'," + owner + "),"
            "'state',CAST(COALESCE((SELECT max(id) FROM dialogue_turns),0) AS TEXT),"
            "'recorded_at',CAST(strftime('%s','now') AS INTEGER)));")


def address_event(owner, state):
    return ("INSERT INTO privacy_outbox VALUES (lower(hex(randomblob(16))),"
            "json_object('kind','agent_address_permission','owner',blago_tag('user'," + owner + "),"
            "'target',blago_tag('agent-address'," + owner + "),'state'," + state + ","
            "'recorded_at',CAST(strftime('%s','now') AS INTEGER)));")


for table in ('memory_facts', 'semantic_memory', 'profiles'):
    for op in ('INSERT', 'UPDATE', 'DELETE'):
        ref = 'OLD' if op == 'DELETE' else 'NEW'
        when = ''
        if table == 'semantic_memory' and op == 'UPDATE':
            # Attaching a source pointer after the atomic pair was inserted must
            # not exclude that pair. Content/status/ownership changes still do.
            when = ' AND (' + ' OR '.join('OLD.' + c + ' IS NOT NEW.' + c for c in
                ('blago_user_id', 'memory_key', 'value_json', 'category', 'status')) + ')'
        DDL.append(f'CREATE TRIGGER agent_context_{table}_{op.lower()} AFTER {op} ON {table} '
                   f'WHEN blago_replaying()=0{when} BEGIN ' + barrier(ref + '.blago_user_id') + ' END')
for op in ('INSERT','UPDATE OF display_name'):
    DDL.append('CREATE TRIGGER agent_context_address_' + op.split()[0].lower() +
        ' AFTER ' + op + ' ON profiles WHEN blago_replaying()=0 BEGIN ' +
        address_event('NEW.blago_user_id', "CASE WHEN NEW.display_name IS NULL THEN 'DENY' ELSE blago_tag('profile-address',NEW.display_name) END") + ' END')
for table,key in (('memory_facts','key'),('semantic_memory','memory_key')):
    DDL.append(f'CREATE TRIGGER agent_context_address_delete_{table} AFTER DELETE ON {table} '
        f"WHEN blago_replaying()=0 AND OLD.{key}='name' BEGIN " + address_event('OLD.blago_user_id',"'DENY'") + ' END')
DDL.append('CREATE TRIGGER agent_context_address_delete_profile AFTER DELETE ON profiles WHEN blago_replaying()=0 BEGIN ' + address_event('OLD.blago_user_id',"'DENY'") + ' END')
DDL = tuple(DDL)
CHECKSUM = hashlib.sha256('\n'.join(DDL).encode()).hexdigest()
OBJECTS = {s.split()[2] for s in DDL}


def requested():
    return os.getenv(FLAG, '').lower() in ('1', 'true', 'yes')


def validate(db, *, mandatory=False):
    objects = dict(db.execute("SELECT name,sql FROM sqlite_master WHERE name LIKE 'agent_context_%' AND sql IS NOT NULL"))
    if not objects:
        if mandatory:
            raise JournalUnavailable('Explicit agent context migration required')
        return False
    if set(objects) != OBJECTS or any(objects.get(s.split()[2]) != s for s in DDL):
        raise JournalUnavailable('Agent context schema drift')
    if [tuple(r) for r in db.execute('SELECT version,checksum FROM agent_context_migrations')] != [(VERSION, CHECKSUM)]:
        raise JournalUnavailable('Agent context migration mismatch')
    return True


def floor_event(db, event, journal):
    validate(db, mandatory=True)
    state = event['state']
    if not isinstance(state, str) or not state.isdecimal() or len(state) > 18:
        raise ValueError('Invalid agent history barrier')
    uid = db.execute("SELECT blago_user_id FROM users WHERE blago_tag('user',blago_user_id)=?", (event['owner'],)).fetchone()
    if uid and event['target'] != journal.tag('agent-history', uid[0]):
        raise ValueError('Agent history barrier owner mismatch')
    # Prevent restored AUTOINCREMENT allocation from falling below a retained
    # barrier. This is ordering, never authorization or a copy of deleted data.
    db.execute("UPDATE sqlite_sequence SET seq=max(seq,?) WHERE name='dialogue_turns'", (int(state),))
    db.execute("INSERT INTO sqlite_sequence(name,seq) SELECT 'dialogue_turns',? WHERE NOT EXISTS (SELECT 1 FROM sqlite_sequence WHERE name='dialogue_turns')", (int(state),))
    return int(state)


def validate_address(db,event,journal):
    validate(db,mandatory=True)
    state=event['state']
    if state!='DENY' and (not isinstance(state,str) or len(state)!=64 or any(c not in '0123456789abcdef' for c in state)):
        raise ValueError('Invalid address permission')
    uid=db.execute("SELECT blago_user_id FROM users WHERE blago_tag('user',blago_user_id)=?",(event['owner'],)).fetchone()
    if uid and event['target']!=journal.tag('agent-address',uid[0]):
        raise ValueError('Address permission owner mismatch')


def deny_address(db,uid):
    if validate(db):
        db.execute(address_event('?',"'DENY'"),(uid,uid))


def migrate(path):
    from xblago.core.integration import preflight
    from xblago.core.privacy_control import PrivacyControl
    from xblago.core.context_schema import validate as fencing
    from xblago.core.sqlite_safety import require_safe_sqlite
    require_safe_sqlite()
    if os.getenv('XBLAGO_INTEGRATION_MODE') != 'synthetic':
        raise ValueError('Synthetic offline migration only')
    path = Path(path)
    if not path.is_file():
        raise ValueError('Existing synthetic offline copy required')
    journal = SecurityJournal(Path(os.environ['XBLAGO_SECURITY_JOURNAL_DIR']))
    journal.read()  # existing independent witness validation, never initialize
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=rw', uri=True)
    db.row_factory = sqlite3.Row
    db.create_function('blago_tag', -1, journal.tag, deterministic=True)
    db.create_function('blago_replaying', 0, lambda: 1)
    try:
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('BEGIN IMMEDIATE')
        fencing(db, mandatory=True)
        from xblago.core import context_schema, lifecycle_schema
        from xblago.core.integration import schema_shape
        from xblago.core.help_schema import present as has_help
        lifecycle_schema.validate(db)
        expected = json.loads(Path(__file__).with_name('integration_help_schema.json' if has_help(db) else 'integration_schema.json').read_text())
        from xblago.core import asset_registry_schema, uel_schema, living_schema
        asset_registry_schema.validate(db)
        uel_schema.validate(db)
        living_schema.validate(db)
        extra = asset_registry_schema.OBJECTS | uel_schema.OBJECTS | living_schema.OBJECTS
        if {k: v for k, v in schema_shape(db).items() if k not in OBJECTS | context_schema.OBJECTS | lifecycle_schema.OBJECTS | extra} != expected:
            raise ValueError('Agent context requires frozen integration baseline')
        changed = not validate(db)
        if changed:
            for statement in DDL:
                db.execute(statement)
            db.execute('INSERT INTO agent_context_migrations VALUES (?,?)', (VERSION, CHECKSUM))
        control = PrivacyControl.open(db)
        if control is None or control.journal.witness_directory is None:
            raise JournalUnavailable('Retained privacy journal and witness required')
        control.reconcile()
        if changed:
            # Old unproven conversation is not silently promoted to evidence.
            for row in db.execute("SELECT blago_user_id FROM users WHERE status='active'").fetchall():
                db.execute(barrier('?'), (row[0], row[0]))
                tag=journal.tag('user',row[0])
                if not any((e:=json.loads(r[2]))['kind']==ADDRESS_KIND and e['owner']==tag for r in control.rows):
                    profile=db.execute('SELECT display_name FROM profiles WHERE blago_user_id=?',(row[0],)).fetchone()
                    if profile and profile[0] is not None:
                        db.execute(address_event('?', '?'),(row[0],row[0],journal.tag('profile-address',profile[0])))
        control.flush()
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or db.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Agent context integrity failure')
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()
    preflight(path)  # validates frozen baseline plus explicit optional schema
    return changed


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', type=Path)
    print('applied' if migrate(parser.parse_args().database) else 'unchanged')
