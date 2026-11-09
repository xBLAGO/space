"""Read-only offline audit of a captured GitHub PR inventory and exact Git trees."""
import argparse
import json
from pathlib import Path
import subprocess

MAIN='3c218dc5757879c26b3aec520af3636b2d702715'
BASE='0772e5bc5919aefac2c209f4e40fe5ea790d6792'


def git(*args):
    return subprocess.check_output(['git',*args],text=True,encoding='utf-8',errors='replace').strip()


def exists(sha,path):
    return subprocess.run(['git','cat-file','-e',sha+':'+path],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0


def audit(inventory):
    prs=inventory['pull_requests'];rows=[]
    for p in sorted(prs,key=lambda p:p['number']):
        sha=p['head_sha'];base=git('merge-base',BASE,sha)
        ancestor=subprocess.run(['git','merge-base','--is-ancestor',sha,BASE],stdout=subprocess.DEVNULL).returncode==0
        conflict_count=0
        if not ancestor:
            r=subprocess.run(['git','merge-tree',base,BASE,sha],capture_output=True,text=True,encoding='utf-8',errors='replace')
            if r.returncode:raise RuntimeError('Read-only merge simulation unavailable')
            conflict_count=r.stdout.count('<<<<<<<')
        rows.append({'pr':p['number'],'head_sha':sha,'ancestor_of_candidate':ancestor,'merge_base':base,
                     'textual_conflict_markers':conflict_count,'runtime_compatibility':'NOT implied by text merge or individual CI'})
    components={
        'telegram_sqlite':'src/xblago/core/storage.py','personal_agent':'src/xblago/agents/personal_assistant.py',
        'web_mini_app':'src/xblago/integrations/web.py','semantic_memory':'src/xblago/core/semantic_memory.py',
        'identity_runtime':'src/xblago/integrations/identity.py','privacy_recovery':'src/xblago/core/privacy_control.py',
        'natural_conversation':'src/xblago/core/conversation.py','help_cycle':'src/xblago/domain/help_service.py',
        'context_fencing':'src/xblago/core/context_fencing.py','lifecycle':'src/xblago/core/lifecycle_schema.py',
        'retention_scheduler':'src/xblago/core/retention_ops.py','sovereign_identity':'src/xblago/core/sovereign.py',
        'genesis':'src/xblago/core/genesis.py','federation_lab':'experiments/federation_08_2/runtime.py'}
    table=[{'component':name,'path':path,'in_main':exists(MAIN,path),'in_candidate':exists(BASE,path)} for name,path in components.items()]
    raw=git('diff','--numstat',MAIN,BASE).splitlines()
    changed=[{'path':x.split('\t',2)[2],'additions':x.split('\t')[0],'deletions':x.split('\t')[1]} for x in raw]
    return {'format':'blago-integration02-repository-audit-1','observed_main_sha':MAIN,'candidate_base_sha':BASE,
            'main_files':len(git('ls-tree','-r','--name-only',MAIN).splitlines()),
            'candidate_files':len(git('ls-tree','-r','--name-only',BASE).splitlines()),
            'components':table,'main_to_candidate_files':changed,'pull_requests':rows,
            'pr19_post_runtime_changes':git('diff','--name-only','d7f619a1e6a7f15564facb5e8e84b184a3f24658','837b2bfd5d3db4842530d30ccf0f417bfe718737').splitlines(),
            'main_auto_deploy_on_push':'branches: ["main"]' in git('show',MAIN+':.github/workflows/huggingface-space.yml'),
            'limitations':['Captured PR HEAD snapshot; re-read GitHub before any approved integration','Text conflict count is not semantic compatibility','No merges, migrations, index changes or deployment executed']}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--inventory',type=Path,required=True);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    result=audit(json.loads(args.inventory.read_text()));args.output.write_text(json.dumps(result,indent=2)+'\n')
    print('Audited PRs:',len(result['pull_requests']),'main files:',result['main_files'],'candidate files:',result['candidate_files'])
