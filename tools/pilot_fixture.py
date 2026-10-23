"""Synthetic, disposable full Foundation stack. No production paths or Telegram I/O."""
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from xblago.core.identity_schema import migrate_identity_store
from xblago.core.identity_runtime_schema import migrate_identity_runtime
from xblago.core.privacy_schema import migrate_privacy
from xblago.core.pilot_schema import migrate_pilot
from xblago.core.journal_witness import initialize as initialize_witness
from xblago.core.security_journal import SecurityJournal
from xblago.core.storage import SQLiteBlagoStore
from xblago.integrations.web import WebApp


@contextmanager
def fixture(origin='http://localhost:7860',rp_id='localhost'):
    with tempfile.TemporaryDirectory(prefix='blago-pilot-fixture-') as root,patch.dict(os.environ,{
        'XBLAGO_PILOT_MODE':'false','XBLAGO_SECURITY_JOURNAL_DIR':'','XBLAGO_JOURNAL_WITNESS_DIR':'',
        'XBLAGO_REQUIRE_PRIVACY_JOURNAL':'false','XBLAGO_REQUIRE_SECURE_IDENTITY':'false',
        'XBLAGO_WEB_INVITE_CODE':'synthetic-pilot-invite','TELEGRAM_BOT_TOKEN':'123:synthetic-pilot-bot',
        'XBLAGO_LLM_PROVIDER':'local','XBLAGO_COOKIE_SECURE':'false',
        'XBLAGO_PUBLIC_ORIGIN':origin,'XBLAGO_WEBAUTHN_RP_ID':rp_id}):
        root=Path(root)
        path=root/'app.sqlite3'
        store=SQLiteBlagoStore(path)
        app=WebApp(store)
        migrate_identity_store(path)
        migrate_identity_runtime(path)
        journal=root/'journal'
        witness=root/'independent-witness'
        SecurityJournal.initialize(journal)
        migrate_privacy(path,journal)
        with patch.dict(os.environ,{'XBLAGO_SECURITY_JOURNAL_DIR':str(journal)}):
            migrate_pilot(path)
            initialize_witness(SecurityJournal(journal),witness)
            with patch.dict(os.environ,{'XBLAGO_JOURNAL_WITNESS_DIR':str(witness),'XBLAGO_PILOT_MODE':'true',
                'XBLAGO_REQUIRE_PRIVACY_JOURNAL':'true','XBLAGO_REQUIRE_SECURE_IDENTITY':'true'}):
                yield {'root':root,'path':path,'journal':journal,'witness':witness,'store':store,'app':app}
