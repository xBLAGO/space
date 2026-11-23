"""Explicit live HF acceptance on NEW synthetic stores, never production tokens.

Reuses the existing test-provider credential contract and real runtime agent.
Telegram transport here is simulated; physical-device evidence is separate.
Actual prose requires human review. State assertions cannot qualify model quality.
"""
import argparse
import json
import os
from pathlib import Path
import tempfile
import time
from unittest.mock import patch

from tools.integration_fixture import create
from tools.agent_context_check import source_revision
from tools.unified_mvp_check import init_data, bot_update
from xblago.core.agent_context import explain, snapshot, provenance_reply
from xblago.core.agent_context_schema import migrate, FLAG
from xblago.core.config import LlmConfig
from xblago.core.conversation import model_history
from xblago.core.integration import preflight
from xblago.core.llm import OpenAICompatibleProvider, LlmProviderError
from xblago.core.memory import redact_sensitive_text
from xblago.integrations.web import WebApp
from xblago.integrations.telegram import TelegramBot

FILLERS = (
    'Почему образуется радуга?', 'Чем метеор отличается от метеорита?',
    'Почему у велосипеда проскальзывает цепь?', 'Как проверить давление в шинах?',
    'Как образуются облака?', 'Почему листья меняют цвет?', 'Как работает компас?',
    'Чем гипотеза отличается от теории?', 'Как возникает эхо?', 'Почему лёд плавает?',
    'Как устроен рычаг?', 'Чем масштаб карты отличается от разрешения?',
    'Как работает дверная петля?', 'Почему струна меняет высоту звука?',
    'Что делает подшипник?', 'Почему поверхность зеркала отражает свет?',
    'Как работает песочный таймер?', 'Почему соль растворяется в воде?',
    'Что такое круговорот воды?', 'Как возникают морские волны?',
    'Чем механическая работа отличается от мощности?', 'Как устроен термос?',
    'Почему горячий воздух поднимается?', 'Что такое центр тяжести?',
    'Как работает шестерня?', 'Почему видна Луна днём?', 'Зачем смазывать цепь?',
    'Как измеряют расстояние на карте?', 'Почему стекло прозрачно?',
    'Как устроены велосипедные тормоза?', 'Что такое инерция?',
    'Почему мосту нужны температурные швы?', 'Как работает пружина?',
    'Чем последовательное соединение отличается от параллельного?', 'Что такое орбита?',
)


class RecordedProvider(OpenAICompatibleProvider):
    def __init__(self, config):
        super().__init__(config)
        self.calls = []
        self.answers = []
        self.contexts = []
    def _request(self, messages, deadline, budget):
        if len(self.calls) >= 96:
            raise LlmProviderError('Synthetic test request budget exhausted')
        row = {'success': False, 'max_tokens': budget}
        self.calls.append(row)
        start = time.monotonic()
        try:
            result = super()._request(messages, deadline, budget)
            row['success'] = True
            row['incomplete'] = result.incomplete
            row['finish_reason'] = result.finish_reason
            return result
        except LlmProviderError as exc:
            row.update(status=exc.status, category=exc.category)
            raise
        finally:
            row['elapsed_ms'] = round((time.monotonic()-start)*1000, 2)
    def generate(self, context):
        answer = None
        if context.purpose == 'conversation':
            self.contexts.append(context)
        try:
            answer = super().generate(context)
            return answer
        finally:
            if context.purpose == 'conversation':
                self.answers.append(answer)


def run():
    # No get_token(), cached credentials, dotenv, generic HF_TOKEN or fallback.
    token = os.environ.get('BLAGO_TEST_HF_TOKEN')
    model = os.environ.get('BLAGO_TEST_HF_MODEL')
    approved = os.environ.get('BLAGO_TEST_HF_APPROVED') == 'synthetic-only'
    base = {'format': 'blago-context-live-1', 'source_sha': source_revision(),
            'synthetic_only': True, 'production_credentials_read': False,
            'physical_android': 'NOT EXECUTED', 'physical_iphone': 'NOT EXECUTED',
            'telegram_transport': 'synthetic updates/initData, not real Telegram',
            'C02_arbitrary_old_topic': 'UNAVAILABLE', 'C12_legacy_origin': 'UNKNOWN',
            'C14_independent_transfer': 'SEPARATE FEDERATION TASK', 'real_pilot': 'NO-GO'}
    if not (token and model and approved):
        return base | {'status': 'NOT EXECUTED', 'requests': 0,
                       'reason': 'Dedicated test token, model and synthetic-only approval required',
                       'answer_quality': 'NOT EXECUTED', 'turns': []}
    provider = RecordedProvider(LlmConfig('hf', model, token, 'https://router.huggingface.co/v1', 20, 384, 'off'))
    rows, checks = [], {}
    with tempfile.TemporaryDirectory(prefix='blago-context-live-') as directory:
        with patch.dict(os.environ, {FLAG: 'false'}):
            cfg = create(Path(directory)/'new', help_enabled=True, context_fencing=True, lifecycle=True)
        cfg.update(XBLAGO_WEB_INVITE_CODE='synthetic-live-context', TELEGRAM_BOT_TOKEN='123:synthetic-help011-only')
        with patch.dict(os.environ, cfg), patch('xblago.integrations.web.load_llm_config', return_value=provider.config), patch('xblago.integrations.telegram.load_llm_config', return_value=provider.config), patch('xblago.integrations.web.build_chat_provider', return_value=provider), patch('xblago.integrations.telegram.build_chat_provider', return_value=provider):
            path = Path(cfg['XBLAGO_DATA_DIR'])/'blago.sqlite3'
            migrate(path)
            os.environ[FLAG] = 'true'
            app = WebApp(preflight(path))
            auth = lambda name: app.register(name, 'synthetic-password-only', 'synthetic-live-context')[0]
            web_token, other_token = auth('synthetic_live_alina'), auth('synthetic_live_boris')
            uid, other = app.session(web_token)[0], app.session(other_token)[0]
            bot = TelegramBot(None, Path(directory)/'unused', app.store)
            update = 99000
            mini_token = None
            def say(stage, text, channel='web', participant='A'):
                nonlocal update
                before, contexts_before = len(provider.calls), len(provider.contexts)
                if channel == 'bot':
                    update += 1
                    answer = '\n'.join(r['text'] for r in bot.replies_for_update(bot_update(update, text)))
                else:
                    who, session = (other, other_token) if participant == 'B' else (uid, mini_token if channel == 'mini' else web_token)
                    answer = app.chat(who, text, session)
                context = provider.contexts[-1] if len(provider.contexts) > contexts_before else None
                visible_history = model_history(context.history) if context else ()
                # Match the production provider's short-follow-up selection,
                # including the preceding user topic, not just the current text.
                query = context.user_text if context else ''
                if context and len(query.split()) <= 10:
                    query += ' ' + next((t.text for t in reversed(context.history) if t.role == 'user'), '')
                who = other if participant == 'B' else uid
                source_context = None
                if context is None:
                    facts = app.store.all_memory(who)
                    with app.store._locked_transaction() as db:
                        candidate = snapshot(db, who, facts, app.store.all_semantic_memory(who))
                    if provenance_reply(candidate, facts, text) is not None:
                        source_context = candidate
                rows.append({'stage': stage, 'channel': channel, 'participant': participant,
                    'input': text, 'answer': redact_sensitive_text(answer),
                    'model_answer': redact_sensitive_text(provider.answers[-1]) if context and provider.answers[-1] else None,
                    'model_answer_success': bool(context and provider.answers[-1]),
                    'requests': len(provider.calls)-before,
                    'visible_history_turns':len(visible_history),
                    'story_title_visible_in_history':any('Сиреневый парус' in t.text for t in visible_history),
                    'selection_query': query,
                    'selected_facts': (explain(context.participant, context.memory, query) if context
                                       else explain(source_context, facts, text) if source_context else {}),
                    'answer_path': 'model' if context else 'verified_source_lookup' if source_context else 'application_command',
                    'memory_after': app.store.all_memory(who),
                    'review': 'PENDING HUMAN REVIEW' if context else 'APPLICATION COMMAND, NOT MODEL QUALITY'})
            say('introduce', 'Меня зовут Алина')
            say('self_fact', 'Я живу в Перми')
            # Recent dialogue is not proof of a durable, traceable memory write.
            with app.store._locked_transaction() as db:
                prepared = snapshot(db, uid, app.store.all_memory(uid), app.store.all_semantic_memory(uid))
            city_ready = (app.store.all_memory(uid).get('city') == 'Перми'
                          and prepared.origins.get('city', {}).get('value') == 'Перми')
            checks['C12_city_setup_ready'] = city_ready
            say('third_party', 'Мой знакомый живёт в Уфе. Это сведения о нём, не обо мне.')
            say('hypothesis', 'Наверное, я хотел бы переехать в Омск, но это только предположение.')
            checks['third_party_and_hypothesis_not_owner_fact'] = app.store.all_memory(uid).get('city') == 'Перми'
            say('arbitrary_topic', 'Для вымышленного рассказа предлагаю название «Сиреневый парус». Не сохраняй это в память.')
            for i, text in enumerate(FILLERS):
                say('topic_change_'+str(i+1), text+' Ответь в одном предложении.')
            say('recall_after_35_turns', 'Где я живу? Если сведений нет, скажи прямо.')
            if city_ready:
                say('source_before_clear', 'Откуда ты знаешь, в каком городе я живу?')
            else:
                rows.append({'stage':'source_before_clear', 'status':'NOT EXECUTED',
                             'reason':'SETUP FAILED: durable city or verified source unavailable',
                             'requests':0, 'answer':None})
            say('C02_boundary', 'Как назывался наш вымышленный рассказ? Если не знаешь, не придумывай.')
            # Long inference runs may outlive the existing fresh-auth window.
            # Reauthenticate legitimately; never extend/disable session guards.
            web_token = app.login('synthetic_live_alina', 'synthetic-password-only', old_token=web_token)[0]
            code = app.new_link_code(uid, web_token)
            bot.replies_for_update(bot_update(98999, '/link '+code))
            web_token = app.login('synthetic_live_alina', 'synthetic-password-only')[0]
            binding = 'synthetic-live-mini-binding-0123456789'
            mini_token = app.telegram_login(init_data(), app.identity.telegram_challenge(binding), binding)[0]
            checks['same_web_bot_mini_identity'] = app.session(mini_token)[0] == uid
            say('bot_continuation', 'Как меня зовут и где я живу?', 'bot')
            say('mini_correction', 'Я больше не живу в Перми, теперь живу в Томске', 'mini')
            for channel in ('web', 'bot', 'mini'):
                say('corrected_'+channel, 'Где я живу сейчас?', channel)
            checks['current_city'] = app.store.all_memory(uid).get('city') == 'Томске'
            say('delete_fact', 'Забудь мой город', 'web')
            for channel in ('web', 'bot', 'mini'):
                say('deleted_'+channel, 'Где я живу? Если не знаешь, скажи об этом прямо.', channel)
            checks['deleted_city_absent'] = 'city' not in app.store.all_memory(uid)
            before = app.store.all_memory(uid)
            before_records = app.store.all_semantic_memory(uid,include_inactive=True)
            say('no_save', 'Не запоминай это: я предпочитаю какао. Чем какао отличается от кофе?')
            checks['veto_did_not_write_fact'] = before == app.store.all_memory(uid)
            checks['veto_did_not_write_semantic_record'] = before_records == app.store.all_semantic_memory(uid,include_inactive=True)
            say('clear_dialogue_for_veto_check', '/clear-history')
            say('unknown_preference', 'Какой напиток я предпочитаю? Если сведений нет, скажи, что не знаешь.')
            say('C12_deleted_origin', 'Откуда ты знаешь моё имя? Если исходная реплика недоступна, так и скажи.')
            say('other_participant', 'Где я живу и какой напиток предпочитаю? Если сведений нет, скажи прямо.', participant='B')
            checks['other_memory_empty'] = not app.store.all_memory(other)
            with app.store._connect() as db:
                checks['integrity'] = db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok' and not db.execute('PRAGMA foreign_key_check').fetchall()
                checks['no_automatic_economic_actions'] = all(db.execute('SELECT count(*) FROM '+t).fetchone()[0] == 0 for t in ('help_requests','blago_payment_requests','blago_contribution_events'))
    return base | {'status': 'EXECUTED; ANSWERS REQUIRE HUMAN REVIEW', 'model': model,
                   'maximum_provider_requests': 96,
                   'requests': len(provider.calls), 'calls': provider.calls, 'turns': rows,
                   'state_checks': checks, 'answer_quality': 'PENDING HUMAN REVIEW',
                   'provider_failures': sum(not c['success'] for c in provider.calls)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = run()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({k: result[k] for k in ('status','requests','answer_quality','real_pilot')}))
