"""Explicit offline rebuild of the derivative UEL index from original evidence."""
import argparse
from pathlib import Path
import os
import sqlite3
from uuid import uuid4

from xblago.core.security_journal import canonical

from xblago.core import uel_schema, uel_local
from xblago.core.privacy_control import PrivacyControl
from xblago.core.security_journal import JournalUnavailable, SecurityJournal


def repair_index(path):
    """Never reconstruct payloads, authorize a user, or discard original audit.

    All writers must be stopped. SQLite serializes concurrent callers as well.
    Original journal/witness/custodian must remain current and available.
    """
    uel_schema.require_synthetic()
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise JournalUnavailable('Existing isolated offline copy required')
    journal = SecurityJournal(Path(os.environ['XBLAGO_SECURITY_JOURNAL_DIR']))
    journal.read()
    from xblago.core.uel_checkpoint import configuration
    if configuration() is None:
        raise JournalUnavailable('Offline repair requires a live pinned checkpoint custodian')
    with sqlite3.connect(path.resolve().as_uri() + '?mode=rw', uri=True, timeout=30) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('BEGIN IMMEDIATE')
        actual = dict(db.execute("SELECT name,sql FROM sqlite_master WHERE name LIKE 'uel_%' AND sql IS NOT NULL"))
        triggers = {s.split()[2]: s for s in uel_schema.DDL if s.startswith('CREATE TRIGGER')}
        expected = {s.split()[2]: s for s in uel_schema.DDL}
        if set(actual) - set(expected) or any(actual.get(name) != sql for name, sql in expected.items() if name not in triggers):
            raise JournalUnavailable('Unsupported UEL structural damage; preserve offline copy')
        original_index = {
            'events': [dict(row) for row in db.execute('SELECT * FROM uel_events ORDER BY sequence')],
            'state': [dict(row) for row in db.execute('SELECT * FROM uel_state')],
            'triggers': {name: actual.get(name) for name in triggers},
        }
        for name, sql in triggers.items():
            if name in actual and actual[name] != sql:
                raise JournalUnavailable('Unknown trigger drift; preserve offline copy')
            if name not in actual:
                db.execute(sql)
        uel_schema.validate(db, mandatory=True)
        control = PrivacyControl.open(db)
        binding = db.execute('SELECT journal_id FROM uel_binding WHERE singleton=1').fetchone()
        if not binding or binding[0] != journal.journal_id:
            raise JournalUnavailable('Original UEL binding required')
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or db.execute('PRAGMA foreign_key_check').fetchall():
            raise JournalUnavailable('Source database integrity failure')
        for name in triggers:
            db.execute('DROP TRIGGER ' + name)
        db.execute('DELETE FROM uel_events')
        db.execute('UPDATE uel_state SET sequence=0,head=? WHERE singleton=1', (uel_local.ZERO,))
        for sql in triggers.values():
            db.execute(sql)
        import json
        for row in control.rows:
            event = json.loads(row[2])
            if event['kind'] == 'uel_local':
                uel_local.apply_checkpoint(db, event, journal)
        control.reconcile()
        from xblago.core.context_schema import check_frontiers
        from xblago.core.lifecycle_schema import check_denials
        from xblago.domain.asset_registry import check_frontiers as check_assets
        check_frontiers(db, control)
        check_denials(db, control)
        check_assets(db, control)
        uel_local.synchronize(db, control, at_start=True)
        uel_local.check(db, control)
        rebuilt_index = {
            'events': [dict(row) for row in db.execute('SELECT * FROM uel_events ORDER BY sequence')],
            'state': [dict(row) for row in db.execute('SELECT * FROM uel_state')],
            'triggers': triggers,
        }
        if original_index != rebuilt_index:
            event = {'kind': 'uel_index_repair', 'owner': '', 'target': uel_local.target(journal),
                     'state': canonical({'profile': 'uel-index-repair-0.3',
                                         'before': journal.tag('uel-index-repair-before-v3', canonical(original_index)),
                                         'after': journal.tag('uel-index-repair-after-v3', canonical(rebuilt_index))}),
                     'recorded_at': 0}
            db.execute('INSERT INTO privacy_outbox VALUES (?,?)', ('uel-repair:' + uuid4().hex, canonical(event)))
        control.flush()
        if db.execute('PRAGMA foreign_key_check').fetchall():
            raise JournalUnavailable('Rebuilt index foreign key failure')
        db.commit()
    return {'status': 'INDEX_REBUILT', 'source_payloads_recreated': False, 'user_authority_granted': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', type=Path)
    args = parser.parse_args()
    print(repair_index(args.database))


if __name__ == '__main__':
    main()
