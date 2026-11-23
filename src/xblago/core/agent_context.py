"""Private, bounded, owner-scoped context projection; no LLM or new data store."""
from dataclasses import dataclass, replace, field
import json
import os
import re
from pathlib import Path

from xblago.core import agent_context_schema as schema
from xblago.core.memory import SemanticMemoryRecord, is_sensitive_memory, relevant_memory, redact_sensitive_text
from xblago.core.security_journal import JournalUnavailable, SecurityJournal
from xblago.core.memory_policy import assertion_text

SOURCE = re.compile(r'(?:^|\|)context_turn:([1-9][0-9]{0,17})(?:$|\|)')


@dataclass(frozen=True)
class ParticipantContext:
    profile: dict
    origins: dict
    history_floor: int
    has_prior_dialogue: bool
    pending_corrections: dict = field(default_factory=dict)


def city_correction(text):
    from xblago.core.memory_commands import parse_edit
    from xblago.core.memory_policy import extract_personal_facts
    edit = parse_edit(text)
    if edit and edit.key == 'city':
        return edit.value
    return dict(extract_personal_facts(text)).get('city')


def unsaved_city_reply(value, stored):
    return (f'Поправка о городе «{value}» не сохранена. '
            f'В памяти по-прежнему указан город проживания: {stored}. '
            'Последнее сообщение о новом городе осталось в истории, но не стало сохранённым фактом.')


def snapshot(db, uid, facts, records):
    installed = schema.validate(db, mandatory=schema.requested())
    if not installed:
        return None
    if not schema.requested():
        raise JournalUnavailable('Agent context installed: explicit synthetic activation required')
    if os.getenv('XBLAGO_INTEGRATION_MODE') != 'synthetic':
        raise JournalUnavailable('Agent context is synthetic-only')
    journal = SecurityJournal(Path(os.environ['XBLAGO_SECURITY_JOURNAL_DIR']))
    rows, _ = journal.read()
    tag = journal.tag('user', uid)
    events = [e for row in rows if (e := json.loads(row[2]))['kind'] == schema.KIND and e['owner'] == tag]
    # A user enrolled after migration needs an initial profile-trigger barrier.
    if not events:
        raise JournalUnavailable('Retained agent history barrier missing')
    for e in events:
        if not isinstance(e['state'], str) or not e['state'].isdecimal() or len(e['state']) > 18:
            raise JournalUnavailable('Invalid retained agent history barrier')
    floor = schema.floor_event(db, max(events, key=lambda e: int(e['state'])), journal)
    profile_row = db.execute('SELECT display_name,language,timezone FROM profiles WHERE blago_user_id=?', (uid,)).fetchone()
    profile = {k: redact_sensitive_text(str(profile_row[k]))[:80]
               for k in ('display_name', 'language', 'timezone') if profile_row and profile_row[k]}
    addresses=[e for row in rows if (e:=json.loads(row[2]))['kind']==schema.ADDRESS_KIND and e['owner']==tag]
    if addresses:
        schema.validate_address(db,addresses[-1],journal)
    if (not addresses or not profile_row or profile_row['display_name'] is None or
            addresses[-1]['state'] != journal.tag('profile-address',profile_row['display_name'])):
        profile.pop('display_name',None)
    username = db.execute('SELECT username FROM web_credentials WHERE blago_user_id=?',(uid,)).fetchone()
    if facts.get('name') and username and profile.get('display_name')==username[0]:
        profile.pop('display_name',None)
    if (facts.get('name') and profile.get('display_name') and
            str(facts['name']).casefold() != profile['display_name'].casefold() and
            not (username and profile['display_name'] == username[0])):
        # There is no per-field preferred-address version in the legacy schema.
        # Do not pretend a row timestamp proves which of two labels is preferred.
        profile.pop('display_name')
        profile['address_ambiguity'] = True
    origins = {}
    checked = 0
    for record in records:
        if record.status != 'confirmed' or record.key not in facts or facts[record.key] != record.value:
            continue
        checked += 1
        if checked > 64:
            break
        match = SOURCE.search(record.source)
        if not match or is_sensitive_memory(record.key, record.value):
            continue
        row = db.execute("SELECT id,text FROM dialogue_turns WHERE id=? AND blago_user_id=? AND role='user'",
                         (int(match[1]), uid)).fetchone()
        if row and isinstance(record.value, str):
            text = row['text']
            assertion = assertion_text(text)
            eligible = bool(assertion and record.value in assertion)
            offset = text.find(record.value,0,len(assertion)) if eligible else -1
            if not eligible:
                # Corrections contain a negated old clause. Expose only the new
                # value's exact source span, never feed the old clause back in.
                from xblago.core.memory_commands import parse_edit, family
                from xblago.core.routing import detect_intent
                edit, intent = parse_edit(text), detect_intent(text)
                if ((edit and family(edit.key, edit.value) == record.key and edit.value == record.value)
                        or (intent.name == 'remember' and family(intent.key, intent.value) == record.key and intent.value == record.value)):
                    eligible = True
                    offset = text.rfind(record.value)
            # A source message may contain several independent facts. Quoting
            # it wholesale would reintroduce one that has since been deleted.
            evidence = text[offset:offset+len(record.value)] if offset >= 0 else ''
            if offset >= 0 and len(evidence) <= 240:
                origins[record.key] = {'value': record.value, 'turn_id': row['id'],
                    'quote': redact_sensitive_text(evidence), 'span': [offset, offset+len(evidence)],
                    'source': record.source.split('|')[0]}
    prior=bool(db.execute('SELECT 1 FROM dialogue_turns WHERE blago_user_id=? LIMIT 1',(uid,)).fetchone())
    pending = {}
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='lifecycle_history_pairs'").fetchone():
        pairs = db.execute('''SELECT u.id,u.text,a.text AS answer FROM lifecycle_history_pairs p
            JOIN dialogue_turns u ON u.id=p.user_turn JOIN dialogue_turns a ON a.id=p.assistant_turn
            WHERE u.blago_user_id=? AND a.blago_user_id=? AND u.id>?
            AND u.role='user' AND a.role='assistant' ORDER BY u.id DESC LIMIT 64''', (uid,uid,floor)).fetchall()
        for pair in pairs:
            proposed = city_correction(pair['text'])
            old = facts.get('city')
            if proposed and proposed == old:
                break  # A later successful reaffirmation supersedes the failed attempt.
            if (isinstance(old,str) and proposed and proposed != old
                    and not is_sensitive_memory('city',proposed)
                    and pair['answer'] == unsaved_city_reply(proposed,old)):
                pending['city'] = {'value':proposed,'turn_id':pair['id'],'status':'NOT_SAVED'}
                break
    return ParticipantContext(profile, origins, floor, prior, pending)


def select(memory, query):
    """A tiny stable address/style core, plus relevant active memory, never biography."""
    safe = {k: v for k, v in memory.items() if isinstance(v, str) and len(v) <= 240
            and len(str(k)) <= 64 and not is_sensitive_memory(k, v)}
    core = {k: safe[k] for k in ('name', 'preference:response_style') if k in safe}
    rest = relevant_memory({k: v for k, v in safe.items() if k not in core}, query, limit=12-len(core))
    return core | rest


def explain(participant, memory, query):
    selected = select(memory, query)
    if participant.profile.get('address_ambiguity'):
        selected.pop('name',None)
    return {k: {'value': v, 'assertion': 'SELF_DECLARED' if participant.origins.get(k, {}).get('value') == v else 'UNVERIFIED_STORED_CLAIM',
                'independently_verified': False,
                'reason': 'stable_address_or_style' if k in ('name', 'preference:response_style') else 'query_relevance',
                'source': participant.origins[k] if participant.origins.get(k, {}).get('value') == v else 'UNKNOWN'}
            for k, v in selected.items()}


def render(participant, memory, query):
    return json.dumps({'profile': participant.profile, 'memory': explain(participant, memory, query),
                       'pending_corrections':participant.pending_corrections}, ensure_ascii=False)


def pending_city_reply(participant, memory, query):
    if participant is None or not re.search(r'\bгде\s+я\s+живу\b',query,re.I):
        return None
    pending = participant.pending_corrections.get('city')
    if pending and memory.get('city'):
        return unsaved_city_reply(pending['value'],memory['city'])
    return None


def provenance_reply(participant, memory, query):
    """Bounded source lookup, not model inference or an assertion of truth.

    Quote only the verified value span: the surrounding turn may contain a
    different fact which has since been erased. UNKNOWN describes provenance,
    never the availability of the retained value.
    """
    if participant is None:
        return None
    text = query.casefold().strip()
    if not re.search(r'\bоткуда\b.*\bзнаешь\b', text):
        return None
    key = ('name' if re.search(r'\bмо[её]\s+имя\b', text)
           else 'city' if re.search(r'\bгород\w*\b', text) and re.search(r'\bя\s+живу\b', text)
           else None)
    if key is None:
        return None
    value = memory.get(key)
    if (not isinstance(value, str) or not value or len(value) > 240
            or is_sensitive_memory(key, value)
            or (key == 'name' and participant.profile.get('address_ambiguity'))):
        return 'Сейчас в доступной памяти нет этих сведений.'
    label = 'имя' if key == 'name' else 'город проживания'
    origin = participant.origins.get(key, {})
    if origin.get('value') == value:
        reply = f'В памяти сохранено: {label} — {value}. Источник — твоё сообщение; точный фрагмент: «{origin["quote"]}».'
    else:
        reply = f'В памяти сохранилось: {label} — {value}. Исходная реплика сейчас недоступна; происхождение записи неизвестно.'
    pending = participant.pending_corrections.get(key)
    if pending:
        reply += f' Последняя поправка о городе «{pending["value"]}» ещё не сохранена.'
    return reply


def apply_memory(buffer, memory, marker):
    """Attach source intent inside the same transaction as the canonical writes."""
    for method, args in buffer.operations:
        if method == 'set':
            key, value = args
            memory.save_semantic(SemanticMemoryRecord('', key, value, 'fact', 'self_assertion|' + marker,
                                                       'confirmed', 1.0, '', ''))
        elif method in ('save_semantic', 'replace_semantic'):
            record, *tail = args
            record = replace(record, source=record.source + '|' + marker)
            getattr(memory, method)(record, *tail)
        else:
            getattr(memory, method)(*args)


def bind_sources(db, uid, marker, turn_id):
    # Marker only exists in this uncommitted transaction. Never bind an old row
    # to an unrelated latest turn; no-op writes retain their original provenance.
    for row in db.execute('SELECT id,source FROM semantic_memory WHERE blago_user_id=?', (uid,)).fetchall():
        if row['source'].endswith('|' + marker):
            db.execute('UPDATE semantic_memory SET source=? WHERE id=? AND blago_user_id=?',
                       (row['source'][:-len(marker)] + f'context_turn:{turn_id}', row['id'], uid))
