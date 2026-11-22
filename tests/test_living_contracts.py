"""The source-freeze exception permits exact adapter insertions, not lifecycle edits."""
import hashlib
import json
import unittest
from tools import living_contract_check as checker

class ContractTests(unittest.TestCase):
    def test_exact_pinned_legacy_sources_after_reviewed_hook_removal(self):
        checker.check()
    def test_unreviewed_lifecycle_change_is_detectable(self):
        pins=json.loads((checker.ROOT/'docs/living-deeds01/LEGACY_CONTRACTS.json').read_text())
        path='src/xblago/domain/help_service.py'
        source=(checker.ROOT/path).read_text().replace("state='COMPLETED'","state='DISPUTED'")
        # The fallback mutation is guaranteed to change bytes even if SQL differs.
        source+='\n# unreviewed change\n'
        self.assertNotEqual(hashlib.sha256(checker.normalize(path,source).encode()).hexdigest(),pins['sha256'][path])
    def test_removing_a_security_hook_is_rejected(self):
        path='src/xblago/domain/help_service.py'
        source=(checker.ROOT/path).read_text().replace('            guard_help(db,uid,rid,action,args,c)\n','')
        with self.assertRaises(ValueError):checker.normalize(path,source)
