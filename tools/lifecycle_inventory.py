"""Schema-only inventory from a NEW synthetic fixture; never opens user databases."""
import argparse
import json
from pathlib import Path
import sqlite3
import tempfile
from tools.integration_fixture import create

GROUPS={
 'profile':['users','profiles','channel_links'],
 'personal_memory':['memory_facts','semantic_memory'],
 'conversation':['dialogue_turns','events'],
 'identity':['web_credentials','web_sessions','account_link_codes','user_devices','passkey_credentials','auth_challenges','telegram_exchanges','telegram_proofs','webauthn_pending','identity_recovery_state'],
 'optional_activity':['activity_events','security_signals','consent_records'],
 'legacy_nonfinancial':['blago_needs','blago_resources','blago_assets','blago_resource_offers'],
 'legacy_economy':['blago_payment_requests','blago_payment_request_offers','blago_fulfillment_events','blago_contribution_events'],
 'help':['help_requests','help_audience','help_offers','help_events','help_challenges','help_operations','help_holds'],
 'privacy_controls':['privacy_state','privacy_outbox','privacy_aliases','privacy_delete_challenges','pilot_safety_state','context_versions','context_generations','lifecycle_policy','lifecycle_history_pairs','lifecycle_holds','lifecycle_generation_times','lifecycle_help_evidence','lifecycle_backups'],
 'migration_metadata':['migrations','identity_schema_migrations','identity_runtime_migrations','privacy_schema_migrations','pilot_schema_migrations','help_schema_migrations','context_schema_migrations','lifecycle_schema_migrations'],
}

def inventory():
    reverse={name:group for group,names in GROUPS.items() for name in names};tables=[]
    with tempfile.TemporaryDirectory(prefix='blago-lifecycle-inventory-') as d:
        cfg=create(Path(d)/'new',help_enabled=True,context_fencing=True,lifecycle=True)
        with sqlite3.connect(Path(cfg['XBLAGO_DATA_DIR'])/'blago.sqlite3') as db:
            for name, in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
                if name not in reverse:raise ValueError('Unclassified table: '+name)
                tables.append({'table':name,'category':reverse[name],'columns':[r[1] for r in db.execute('PRAGMA table_info("'+name+'")')],
                               'foreign_keys':[{'parent':r[2],'column':r[3]} for r in db.execute('PRAGMA foreign_key_list("'+name+'")')]})
    return {'format':'blago-lifecycle-inventory-1','scope':'schema only, new synthetic DB','tables':tables,
            'external':['LLM provider request/response processing and metadata','Telegram server/client copies','browser RAM/DOM/downloads','application backups/WAL/filesystem snapshots','journal/witness/custody copies','host/CI/proxy logs','legacy JSON files'],
            'real_pilot':'NO-GO'}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    result=inventory();args.output.write_text(json.dumps(result,indent=2)+'\n');print('Classified tables:',len(result['tables']))
