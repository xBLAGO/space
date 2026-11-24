"""Prepare three identical private synthetic copies and an explicitly BLOCKED bundle.

No browser, HTTP delivery, consent or Help offer is executed by this command.
Never imports historical 02.1 observations as new 02.2 evidence.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
from unittest.mock import patch

from tools import living_consent_lab as lab
from tools.living_bundle_contract import (BASE, SCHEMA, HARNESS, VERIFIER, REASON,
    BRANCHES, ACTORS, steps, expected_matrix, sha, wire)

REQUEST = ('REQ_TEXT_BASELINE: Нужна добровольная помощь с ремонтом велосипеда.\n'
           'Нужно проверить переднее колесо в согласованное время.\n'
           'Критерий завершения: колесо вращается свободно, обе стороны подтвердили результат.')
NOTE = 'NOTE_SECRET_BASELINE'


def now(): return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')


def prepare(sandbox_root, bundle_root):
    sandbox=Path(sandbox_root).absolute(); bundle=Path(bundle_root).absolute()
    if sandbox==bundle or sandbox in bundle.parents or bundle in sandbox.parents:
        raise ValueError('Private app stores must be outside the evidence bundle')
    if sandbox.exists() or bundle.exists(): raise ValueError('Both outputs must be NEW; no data overwritten')
    expected=expected_matrix(audience_change=False); frozen_at=now()
    sandbox.mkdir(mode=0o700,parents=False)
    template=sandbox/'template'
    # Seed only: same source request and identities in all copies, no forwarding
    # permissions/mandates/invitations. This is not a browser prologue.
    with patch.dict(os.environ), patch.object(lab,'MARKER',NOTE), patch.object(lab,'TASKS',{k:REQUEST for k in lab.TASKS}):
        lab.init(template)
        cfg=lab.configuration(template); os.environ.update(cfg)
        from xblago.core.security_journal import SecurityJournal
        journal=SecurityJournal(template/'journal')
        with sqlite3.connect((template/'app/blago.sqlite3').resolve().as_uri()+'?mode=ro',uri=True) as db:
            ids={r[0]:r[1] for r in db.execute('SELECT username,blago_user_id FROM web_credentials')}
            codes={role:journal.tag('help-member',ids[user]) for role,user in lab.ROLES.items()}
            note=db.execute("SELECT state FROM living_records WHERE kind='NOTE'").fetchone()
            note_hash=sha(json.loads(note[0])['text'].encode())
            if {r[0] for r in db.execute('SELECT kind FROM living_records')}!={'NOTE'}:
                raise ValueError('Seed unexpectedly contains consent/mandates/delivery')
    (template/'cp.sock').unlink(missing_ok=True)
    baseline=sha((template/'app/blago.sqlite3').read_bytes())
    branches={}
    for branch in BRANCHES:
        destination=sandbox/branch
        shutil.copytree(template,destination,symlinks=False)
        for path in destination.rglob('*.json'):
            # Only privately generated config/fixture paths are relocated. SQL,
            # journal, witness and checkpoint bytes are not edited or rebuilt.
            text=path.read_text(); text=text.replace(str(template.resolve()),str(destination.resolve()))
            path.write_text(text); path.chmod(0o600)
        digest=sha((destination/'app/blago.sqlite3').read_bytes())
        if digest!=baseline: raise ValueError('Branch initial database changed')
        branches[branch]={'initial_database_sha256':digest,'status':'BLOCKED',
                          'reason':'MASKED_CAPTURE_ADAPTER_NOT_IMPLEMENTED' if branch!='consent' else 'HELP_OFFER_AUDIENCE_REQUIRED'}
    bundle.mkdir(mode=0o700,parents=False)
    matrix={'frozen_at':frozen_at,'expected':expected,'actual':[]}
    (bundle/'visibility-matrix.json').write_bytes(wire(matrix))
    (bundle/'audit.jsonl').write_bytes(b'')
    repo=Path(__file__).resolve().parents[1]
    files={str(p.resolve().relative_to(repo)):sha(p.read_bytes()) for p in sorted(lab.STATIC.iterdir()) if p.is_file()}
    # Path of the installed tools package is normally relative to this checkout.
    screen_hash=sha(wire(files))
    report=('# VERDICT: BLOCKED\n\n'
        'Подготовлены три отдельные копии одного исходного состояния; браузерный пролог и исходы НЕ ВЫПОЛНЕНЫ.\n'
        'Отдельных экранов Gemini нет: источник — локальный UI Codex 02.1 по текстовым спецификациям.\n'
        'Новый коллектор visible/full_dom/network_response с маскированием заметки ещё не выполнен.\n'
        'HelpService._row() требует help_audience; consent-ветка BLOCKED, завершение через Help NO-GO.\n'
        'Три ветки используют case positive и один common_request_id из идентичного seed.\n'
        'REFUSAL_REASON markers не применяются; N/A — причина не собирается.\n'
        'Cookies, токены, полная личная заметка, SQLite и ключи в bundle не включаются.\n'
        'Expected зафиксирована до подготовки; actual/audit пусты, снимков нет: наблюдения не выдумываются.\n'
        'Независимый вердикт DeepSeek: NOT PROVIDED. Реальные участники/внешняя доставка/production NO-GO.\n')
    report+='\n| Ветка, шаг и роль | Экран | Подтверждение | Остальные роли: UI + ответ сервера | Доказательство |\n|---|---|---|---|---|\n'
    for branch in BRANCHES:
        for step in steps(branch):
            report+=f'| {branch} / {step} / {ACTORS.get(step) or "A,B,C"} | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED: visible/full_dom/network_response каждой роли | Отсутствует; BLOCKED |\n'
    (bundle/'report.md').write_text(report)
    seedmeta=json.loads((template/'ui-seed.json').read_text())
    manifest={'schema_version':SCHEMA,'harness_version':HARNESS,'verifier_version':VERIFIER,
        'checked_head':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        'base_sha':BASE,'created_at':now(),'capture_started_at':None,
        'source_clean':not subprocess.check_output(['git','status','--porcelain'],text=True).strip(),
        'local_ui':{'source_ref':BASE,'origin':'Codex local UI 02.1 from textual specifications',
                    'sha256':screen_hash,'files':files},
        'gemini':'NOT_APPLICABLE: no separate source files/PR/commit',
        'browser_version':None,'target_integration':'PREPARATION_ONLY','claimed_verdict':'BLOCKED',
        'plan':{'author_audience_change':False,'ui_case':'positive','checkpoints':['CP1','CP2','CP3']},
        'seed':{'label':'synthetic-identical-origin-v1','request_text':REQUEST,
                'common_request_id':seedmeta['needs']['positive'],'participant_codes':codes,
                'markers':{'NOTE_SECRET':NOTE,'RECIPIENT_ID_C':codes['C'],'REQ_TEXT':'REQ_TEXT_BASELINE'},
                'private_note_sha256':note_hash,'refusal_reason':REASON},
        'branches':branches,'expected_matrix_sha256':sha(wire(expected)),
        'cleanup_method':'CLOSE_BROWSER_PROCESS_AND_ALL_CONTEXTS; FRESH_LOGINS',
        'deepseek_verdict':'NOT_PROVIDED',
        'artifacts':{n:sha((bundle/n).read_bytes()) for n in ('visibility-matrix.json','audit.jsonl','report.md')}}
    (bundle/'manifest.json').write_bytes(wire(manifest))
    return manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sandbox-root',type=Path,required=True);parser.add_argument('--bundle',type=Path,required=True)
    args=parser.parse_args();m=prepare(args.sandbox_root,args.bundle)
    print(json.dumps({'status':'BLOCKED','prepared_branches':list(m['branches']),
        'same_initial_database':True,'expected_rows':len(expected_matrix()),
        'observed_rows':0,'help':'BLOCKED_HELP_AUDIENCE_REQUIRED','bundle':str(args.bundle)},ensure_ascii=False))
    raise SystemExit(2)


if __name__=='__main__':main()
