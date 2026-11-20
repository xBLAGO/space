"""Initialize a NEW private synthetic stand using the existing Help lab server.

This explicit lab command opts in. Package/runtime defaults remain OFF.
"""
import argparse
import json
import os
from pathlib import Path
from unittest.mock import patch

from tools.help_readiness_lab import initialize
from xblago.core.agent_context_schema import FLAG, migrate


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--new-root',type=Path,required=True)
    args=parser.parse_args()
    root=args.new_root.resolve()
    with patch.dict(os.environ,{FLAG:'false'}):
        cfg=initialize(root)
    with patch.dict(os.environ,cfg):
        migrate(root/'app/blago.sqlite3')
    marker=root/'lab-only.json'
    value=json.loads(marker.read_text())
    value['config'][FLAG]='true'
    marker.write_text(json.dumps(value,indent=2)+'\n')
    (root/'public-config.json').write_text(json.dumps(value['config'],indent=2)+'\n')
    print('New synthetic context stand initialized; no real users or external LLM. Use help_readiness_lab serve with this root.')


if __name__=='__main__':
    main()
