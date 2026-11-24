"""Real synthetic seed/copy preparation, not a browser test."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from tools.living_evidence_prepare import prepare
from tools.living_bundle_contract import sha
from tools.living_bundle_verify import verify


class PreparationTests(unittest.TestCase):
    def test_identical_private_stores_and_explicitly_incomplete_bundle(self):
        with tempfile.TemporaryDirectory(prefix='lb22-',dir='/tmp') as tmp:
            root=Path(tmp);stores=root/'stores';bundle=root/'bundle'
            m=prepare(stores,bundle)
            self.assertEqual(len(set(v['initial_database_sha256'] for v in m['branches'].values())),1)
            for branch in m['branches']:
                dbpath=stores/branch/'app/blago.sqlite3'
                self.assertEqual(sha(dbpath.read_bytes()),m['branches'][branch]['initial_database_sha256'])
                with sqlite3.connect(dbpath) as db:
                    self.assertEqual({r[0] for r in db.execute('SELECT kind FROM living_records')},{'NOTE'})
                    note=json.loads(db.execute("SELECT state FROM living_records WHERE kind='NOTE'").fetchone()[0])['text']
                    for p in bundle.iterdir():self.assertNotIn(note.encode(),p.read_bytes())
            self.assertEqual((bundle/'audit.jsonl').read_bytes(),b'')
            matrix=json.loads((bundle/'visibility-matrix.json').read_bytes())
            self.assertEqual(len(matrix['expected']),360);self.assertEqual(matrix['actual'],[])
            self.assertEqual(verify(bundle)['exit_code'],2)
            self.assertEqual(m['seed']['markers']['RECIPIENT_ID_C'],m['seed']['participant_codes']['C'])
            self.assertFalse(any(p.suffix in ('.sqlite3','.db') for p in bundle.rglob('*')))
            with self.assertRaises(ValueError):prepare(stores,bundle)

    def test_outputs_cannot_overlap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for a,b in ((root,root),(root,root/'bundle'),(root/'stores',root)):
                with self.subTest(a=a,b=b),self.assertRaises(ValueError):prepare(a,b)
