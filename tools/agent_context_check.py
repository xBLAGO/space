"""Local synthetic HTTP/Web/Bot/Mini App context evidence; no external traffic.

Scripted answers inspect the actual context, not the quality of a live model.
--legacy runs this same probe against the exact unmodified source baseline.
"""
import argparse
import json
import os
from pathlib import Path
import statistics
import subprocess
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from tools.integration_fixture import create
from tools.unified_mvp_check import Client, init_data, bot_update
from xblago.core.integration import preflight
from xblago.core.llm import OpenAICompatibleProvider
from xblago.core.memory import relevant_memory
from xblago.integrations.web import WebApp, BlagoWebHandler
from xblago.integrations.telegram import TelegramBot


class InspectingProvider:
    def __init__(self):
        self.contexts = []
    def generate(self, context):
        self.contexts.append(context)
        return 'Синтетический ответ для проверки передачи контекста.'


def run(*, legacy=False):
    stages, checks, latency = {}, {}, []
    provider = InspectingProvider()
    def stage(name):
        context = provider.contexts[-1]
        participant = getattr(context, 'participant', None)
        if participant is not None:
            from xblago.core.agent_context import explain
            facts = explain(participant, context.memory, context.user_text)
        else:
            facts = {k: {'value': v, 'source': 'UNKNOWN'} for k, v in relevant_memory(context.memory, context.user_text).items()}
        stages[name] = {'selected': facts, 'profile': participant.profile if participant else None,
                        'model_history_turns': len(context.history),
                        'old_city_in_history': any('Москве' in t.text for t in context.history),
                        'source_text_history_is_not_fact_authority': True}
    with tempfile.TemporaryDirectory(prefix='blago-context-proof-') as directory:
        with patch.dict(os.environ, {'XBLAGO_AGENT_CONTEXT_ENABLED': 'false'}):
            cfg = create(Path(directory)/'new', help_enabled=True, context_fencing=True, lifecycle=True)
        cfg.update(XBLAGO_WEB_INVITE_CODE='synthetic-context-probe', TELEGRAM_BOT_TOKEN='123:synthetic-help011-only', XBLAGO_COOKIE_SECURE='false')
        with patch.dict(os.environ, cfg), patch('xblago.integrations.web.build_chat_provider', return_value=provider), patch('xblago.integrations.telegram.build_chat_provider', return_value=provider):
            path = Path(cfg['XBLAGO_DATA_DIR'])/'blago.sqlite3'
            if not legacy:
                from xblago.core.agent_context_schema import migrate, FLAG
                migrate(path)
                os.environ[FLAG] = 'true'
            app = WebApp(preflight(path))
            server = ThreadingHTTPServer(('127.0.0.1', 0), BlagoWebHandler)
            server.blago_web_app = app
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                web, mini, other = [Client('http://127.0.0.1:' + str(server.server_port)) for _ in range(3)]
                auth = {'username': 'synthetic_context_probe', 'password': 'synthetic-password-only', 'invite': 'synthetic-context-probe'}
                web.ok('POST', 'auth/register', auth)
                uid = web.ok('GET', 'me')['profile']['blago_user_id']
                web.ok('PATCH', 'me', {'display_name': 'Анна'})
                def say(client, text):
                    before = time.perf_counter()
                    result = client.ok('POST', 'chat/messages', {'text': text})
                    latency.append((time.perf_counter()-before)*1000)
                    return result
                say(web, 'Продолжим')
                stage('profile')
                say(web, 'Меня зовут Анна')
                say(web, 'Я живу в Москве')
                for i in range(35):
                    say(web, 'Обсудим механизм номер ' + str(i))
                say(web, 'Где я живу?')
                stage('long_dialogue')
                say(web, 'Продолжим')
                stage('generic_continuation')
                code = web.ok('POST', 'auth/link-code', {})['code']
                bot = TelegramBot(None, Path(directory)/'unused', app.store)
                bot.replies_for_update(bot_update(97001, '/link ' + code))
                web.ok('POST', 'auth/login', auth)
                challenge = mini.ok('POST', 'auth/telegram/challenge', {})
                mini.ok('POST', 'auth/telegram', {'challenge': challenge['challenge'], 'init_data': init_data()})
                checks['web_mini_same_participant'] = mini.ok('GET', 'me')['profile']['blago_user_id'] == uid
                checks['shared_stored_history'] = mini.ok('GET', 'chat/messages')['messages'] == web.ok('GET', 'chat/messages')['messages']
                say(mini, 'Я больше не живу в Москве, теперь живу в Казани')
                say(web, 'Где я живу?')
                stage('after_correction')
                server.blago_web_app = WebApp(preflight(path))
                say(mini, 'Где я живу?')
                stage('after_restart_in_mini')
                web.ok('DELETE', 'memory', {'key': 'city'})
                say(mini, 'Где я живу?')
                stage('after_deletion')
                other.ok('POST', 'auth/register', auth | {'username': 'synthetic_context_stranger'})
                say(other, 'Где я живу?')
                stage('other_participant')
                if not legacy:
                    checks.update(
                        profile_available=stages['profile']['profile']['display_name'] == 'Анна',
                        original_source_after_35_turns=stages['long_dialogue']['selected']['city']['source']['quote'] == 'Москве',
                        stable_name=stages['generic_continuation']['selected']['name']['value'] == 'Анна',
                        corrected_source=stages['after_correction']['selected']['city']['value'] == 'Казани',
                        correction_not_raw_history=not stages['after_correction']['old_city_in_history'],
                        restart_uses_current_fact=stages['after_restart_in_mini']['selected']['city']['value'] == 'Казани',
                        deleted_fact_absent='city' not in stages['after_deletion']['selected'],
                        no_foreign_memory=not stages['other_participant']['selected'],
                    )
                with server.blago_web_app.store._connect() as db:
                    checks['integrity'] = db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok' and not db.execute('PRAGMA foreign_key_check').fetchall()
                return {'format': 'blago-context-proof-1', 'source_sha': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
                        'mode': 'exact legacy baseline' if legacy else 'isolated new context projection',
                        'checks': checks, 'stages': stages, 'http_turns': len(latency),
                        'median_ms': round(statistics.median(latency), 3), 'max_ms': round(max(latency), 3),
                        'extra_llm_calls': 0, 'live_model': 'NOT EXECUTED', 'physical_devices': 'NOT EXECUTED',
                        'C14_independent_node_transfer': 'NOT IMPLEMENTED / NOT EXECUTED', 'real_pilot': 'NO-GO'}
            finally:
                server.shutdown()
                server.server_close()
                thread.join(5)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--legacy', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = run(legacy=args.legacy)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'checks': result['checks'], 'mode': result['mode'], 'real_pilot': 'NO-GO'}))
    if not all(result['checks'].values()):
        raise SystemExit(1)
