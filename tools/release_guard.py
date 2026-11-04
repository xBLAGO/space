"""Pure release policy; never deploys or migrates. Default is deny."""
import os
import re

def allowed(event,ref,sha,requested,confirmation,enabled):
    return (event=='workflow_dispatch' and ref=='refs/heads/main'
            and bool(re.fullmatch('[0-9a-f]{40}',sha or ''))
            and requested==sha and confirmation=='DEPLOY' and enabled=='true')

if __name__=='__main__':
    if not allowed(*(os.getenv(k,'') for k in ('GITHUB_EVENT_NAME','GITHUB_REF','GITHUB_SHA','BLAGO_RELEASE_SHA','BLAGO_CONFIRM_DEPLOY','BLAGO_DEPLOYMENT_ENABLED'))):
        raise SystemExit('Release blocked: explicit enabled main SHA approval required; migrations are separate')
    print('Code release guard passed; environment approval and operational gates remain mandatory')
