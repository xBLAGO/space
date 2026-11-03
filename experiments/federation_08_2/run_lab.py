"""Run all assertions and emit public, machine-readable evidence."""
import hashlib
import json
from pathlib import Path
import platform
import sys
import unittest
import runtime
from test_runtime import Lab

class Evidence(unittest.TextTestResult):
    def __init__(self,*args,**kwargs): super().__init__(*args,**kwargs); self.records=[]
    def addSuccess(self,test):
        super().addSuccess(test)
        self.records.append({'test':test._testMethodName,'expected':'all named scenario assertions hold','actual':'PASS','proof':getattr(test,'proof',{})})
    def addFailure(self,test,err):
        super().addFailure(test,err); self.records.append({'test':test._testMethodName,'expected':'all named scenario assertions hold','actual':'FAIL'})
    def addError(self,test,err):
        super().addError(test,err); self.records.append({'test':test._testMethodName,'expected':'all named scenario assertions hold','actual':'ERROR'})

if __name__=='__main__':
    output=Path(sys.argv[1]) if len(sys.argv)>1 else Path('/tmp/blago082-evidence.json')
    result=unittest.TextTestRunner(verbosity=2,resultclass=Evidence).run(unittest.defaultTestLoader.loadTestsFromTestCase(Lab))
    files={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob('*.py')}
    evidence={'profile':runtime.a.PROFILE,'base':'0a6acd9178de8c1aaac8f3af729d896efd4eb98a','receipt_schema_sha256':runtime.SCHEMA_HASH,'environment':{'python':platform.python_version(),'platform':platform.system(),'storage':'POSIX fsync/flock; same-host separate directories'},'tests_run':result.testsRun,'passed':len(result.records)-len(result.errors)-len(result.failures),'tests':result.records,'source_sha256':files,'S12':'NOT EXECUTED','classification':{'offline':'run separately, 73 vectors per reference','local_assertions':'PASS' if result.wasSuccessful() else 'FAIL','independent_operators':'NO-GO','production':'NO-GO'}}
    output.write_text(json.dumps(evidence,indent=2,sort_keys=True)+'\n')
    sys.exit(0 if result.wasSuccessful() else 1)
