"""No EVM dependency in application tests; exporter contracts remain canonical."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.blockchain01_export import export


class BlockchainExportTests(unittest.TestCase):
    def test_hash_vector_independent_python_reference(self):
        v=json.loads(Path('tools/blockchain01/hash-vector.json').read_text());raw=v['file_utf8'].encode()
        framed=b'BLAGO/FUND-REPORT-CHECKPOINT/1\0'+len(raw).to_bytes(8,'big')+raw
        self.assertEqual('0x'+hashlib.sha256(framed).hexdigest(),v['expected_digest'])

    def test_export_uses_existing_proof_verifier_and_never_overwrites(self):
        fixture={'packages':[{'proof':{'history':{'fund':'synthetic-unit'}},'pins':{'unit':True}}]}
        with tempfile.TemporaryDirectory() as root,patch('tools.blockchain01_export.source_fixture',return_value=fixture) as source,patch('tools.blockchain01_export.verify_bundle',return_value={'status':'PASS'}) as check:
            p=Path(root)/'report.json';export(p)
            source.assert_called_once_with(8);check.assert_called_once_with(fixture['packages'][0]['proof'],fixture['packages'][0]['pins'])
            report=json.loads(p.read_bytes());self.assertTrue(report['synthetic_only'])
            self.assertNotIn('humans',report)
            with self.assertRaises(FileExistsError):export(p)

    def test_failed_source_verification_writes_no_report(self):
        with tempfile.TemporaryDirectory() as root,patch('tools.blockchain01_export.source_fixture',return_value={'packages':[{'proof':{},'pins':{}}]}),patch('tools.blockchain01_export.verify_bundle',side_effect=ValueError('invalid proof')):
            p=Path(root)/'report.json'
            with self.assertRaises(ValueError):export(p)
            self.assertFalse(p.exists())
