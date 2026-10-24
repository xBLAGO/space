from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
import re

from xblago.core.llm import ChatContext, ChatProvider, LlmProviderError, LocalPlanningProvider, OpenAICompatibleProvider
from xblago.core.conversation import HISTORY_TURN_LIMIT, SERVICE_STATUS_TEXTS, understand
from xblago.core.memory import MemoryStore
from xblago.core.memory import is_sensitive_memory, redact_sensitive_text, MemoryConflictError
from xblago.core.memory_policy import may_extract, memory_veto
from xblago.core.memory_commands import (
    MemoryEdit, clarification, family, natural_forget_query, parse_edit,
    resolve_edit, resolve_forget,
)
from xblago.core.messages import AgentResponse, ConversationTurn, MessageRole, UserMessage
from xblago.core.profile import AgentProfile
from xblago.core.routing import detect_intent
from xblago.core.semantic_memory import SemanticMemoryExtractor, candidates_to_records


class PersonalAssistantAgent:
    """First xBLAGO agent: a user-facing personal assistant."""

    profile = AgentProfile(
        name="xBLAGO Personal Assistant",
        role="User-facing coordinator and personal helper",
        mission=(
            "Understand the person's conversation, help them think and learn, "
            "remember only grounded stable preferences, and support voluntary cooperation "
            "when they choose it. Conversation alone authorizes no external action."
        ),
        tone="Attentive, natural, candid and proportionate; respect silence and refusal.",
    )

    def __init__(
        self,
        memory: MemoryStore,
        chat_provider: ChatProvider | None = None,
        history_limit: int = HISTORY_TURN_LIMIT,
        initial_history: tuple[ConversationTurn, ...] = (),
        history_recorder: Callable[[ConversationTurn], None] | None = None,
        history_clearer: Callable[[], None] | None = None,
        memory_extractor: SemanticMemoryExtractor | None = None,
        exchange_recorder: Callable[[tuple[ConversationTurn, ConversationTurn]], None] | None = None,
    ) -> None:
        self.memory = memory
        self.chat_provider = chat_provider or LocalPlanningProvider()
        self.history_limit = history_limit
        self.history_recorder = history_recorder
        self.exchange_recorder = exchange_recorder
        self.history_clearer = history_clearer
        self.memory_extractor = memory_extractor or (
            SemanticMemoryExtractor(self.chat_provider)
            if isinstance(self.chat_provider, OpenAICompatibleProvider)
            else None
        )
        self._last_generation_succeeded = True
        self._history: list[ConversationTurn] = [
            ConversationTurn(role=t.role, text=_safe_history_text(t.text))
            for t in initial_history[-history_limit:]
        ]

    def welcome_message(self) -> str:
        if self._history:
            return "xBLAGO: Диалог сохранён. Можно продолжить."
        return (
            "xBLAGO: Привет. Я первый агент сети xBLAGO, твой персональный помощник. "
            "Здесь можно просто поговорить."
        )

    def respond(self, message: UserMessage) -> AgentResponse:
        intent = detect_intent(message.text)
        language = _language_for_text(message.text)

        veto = memory_veto(message.text)
        forget_query = natural_forget_query(message.text)
        if forget_query is not None:
            entries = tuple(self.memory.all().items()) + tuple((r.key, r.value) for r in self.memory.semantic_history())
            resolution = resolve_forget(forget_query, entries)
            if resolution.choices:
                return self._record_and_return(message, AgentResponse(clarification(resolution.choices)))
            if resolution.keys:
                key = resolution.keys[0]
                deleted = self.memory.delete(key)
                reply = _forgot_text(language, key) if deleted else _missing_memory_text(language, key)
            else:
                reply = _missing_memory_text(language, forget_query.split(":", 1)[1] if forget_query.startswith("@") else forget_query)
            return self._record_and_return(message, AgentResponse(reply))

        if intent.name == "exit":
            return self._record_and_return(
                message,
                AgentResponse(_text(language, "goodbye"), True),
            )

        if intent.name == "help":
            return self._record_and_return(message, AgentResponse(self._help_text(language)))

        if intent.name == "profile":
            return self._record_and_return(message, AgentResponse(self._user_profile_text(language)))

        if intent.name == "agent_profile":
            return self._record_and_return(message, AgentResponse(self._agent_profile_text(language)))

        if intent.name == "memory":
            return self._record_and_return(message, AgentResponse(self._memory_text(language)))

        if intent.name == "history":
            return self._record_and_return(message, AgentResponse(self._history_text(language)))

        if intent.name == "clear_memory":
            self.memory.clear()
            return self._record_and_return(message, AgentResponse(_text(language, "memory_cleared")))

        if intent.name == "clear_history":
            self._history.clear()
            if self.history_clearer is not None:
                self.history_clearer()
            return self._record_and_return(message, AgentResponse(_text(language, "history_cleared")))

        if intent.name == "remember" and intent.key and intent.value and not veto:
            if is_sensitive_memory(intent.key, intent.value):
                return self._record_and_return(
                    message,
                    AgentResponse(_text(language, "sensitive_memory_rejected")),
                )
            canonical = family(intent.key, intent.value)
            category = "preference" if canonical.startswith("preference:") else "fact"
            edit = MemoryEdit(canonical, intent.value, category)
            facts = self.memory.all()
            targets = tuple(k for k, v in facts.items() if family(k, v) == canonical)
            if targets or canonical.startswith("preference:"):
                try:
                    self.memory.replace_semantic(edit.record(), targets, facts)
                except MemoryConflictError:
                    return self._record_and_return(message, AgentResponse("Память уже изменилась. Повтори, пожалуйста, исправление."))
            else:
                self.memory.set(intent.key, intent.value)
            return self._record_and_return(
                message,
                AgentResponse(_remembered_text(language, intent.key, intent.value) if self.memory.all() != facts else ("Это уже есть в памяти." if language == "ru" else "This is already in memory.")),
            )

        if intent.name == "forget" and intent.key:
            if self.memory.delete(intent.key):
                return self._record_and_return(message, AgentResponse(_forgot_text(language, intent.key)))

            return self._record_and_return(message, AgentResponse(_missing_memory_text(language, intent.key)))

        if intent.name == "greeting":
            return self._record_and_return(
                message,
                AgentResponse(("Я здесь." if language == "ru" else "I'm here.") if self._history else _text(language, "greeting")),
            )

        edit = None if veto else parse_edit(message.text)
        facts = self.memory.all()
        resolution = resolve_edit(edit, facts) if edit else None
        if resolution and resolution.question:
            return self._record_and_return(message, AgentResponse(resolution.question))
        if resolution and resolution.choices:
            return self._record_and_return(message, AgentResponse(clarification(resolution.choices, replacement=edit.value)))
        if edit and edit.key == "preference:choice" and resolution.keys:
            target = resolution.keys[0]
            edit = replace(edit, key=family(target, facts[target]))
        preview = dict(facts)
        if edit:
            for key in resolution.keys:
                preview.pop(key, None)
            preview[edit.key] = edit.value
        response = self._generate_assistant_response(message.text, preview)
        if self._last_generation_succeeded and edit:
            try:
                self.memory.replace_semantic(edit.record(), resolution.keys, facts)
            except MemoryConflictError:
                response = "Память уже изменилась. Повтори, пожалуйста, исправление."
            else:
                confirmation = (("Теперь буду учитывать новое место жительства." if edit.key == "city"
                                 else f"Буду учитывать новое предпочтение: {edit.value}.")
                                if self.memory.all() != facts else "Это уже учтено.")
                # An edit with a substantive question must not erase the answer.
                # Pure corrections need only the committed acknowledgement.
                response = confirmation + (" " + _without_unconfirmed_claims(response) if "?" in message.text else "")
        elif self._last_generation_succeeded and not veto:
            self._forget_denied_fact(message.text)
            if may_extract(message.text) and not self._remember_from_conversation(message.text):
                question = self._extract_semantic_memory(message.text)
                if question:
                    response = question
        return self._record_and_return(message, AgentResponse(_without_unconfirmed_claims(response)))

    def history(self) -> tuple[ConversationTurn, ...]:
        return tuple(self._history)

    def _help_text(self, language: str) -> str:
        if language == "ru":
            return (
                "Я умею:\n"
                "- запоминать факты из разговора или командой: `запомни city = Москва`\n"
                "- исправлять факты: `исправь city на Казань`\n"
                "- забывать отдельный факт: `/forget timezone`\n"
                "- показывать память: `/memory`\n"
                "- показывать текущий диалог: `/history`\n"
                "- очищать память: `/clear-memory`\n"
                "- очищать текущий диалог: `/clear-history`\n"
                "- показывать профиль пользователя: `/profile`\n"
                "- показывать профиль агента: `/agent`\n"
                "- завершать сессию: `/exit`\n\n"
                "Можно просто общаться; не обязательно формулировать задачу."
            )

        return (
            "I can:\n"
            "- remember facts: `remember my timezone is Europe/Moscow`\n"
            "- forget one fact: `/forget timezone`\n"
            "- show memory: `/memory`\n"
            "- show this conversation: `/history`\n"
            "- clear memory: `/clear-memory`\n"
            "- clear this conversation: `/clear-history`\n"
            "- show your profile: `/profile`\n"
            "- show the agent profile: `/agent`\n"
            "- end the session: `/exit`\n\n"
            "You can simply talk; there is no need to formulate a task."
        )

    def _user_profile_text(self, language: str) -> str:
        facts = self.memory.all()
        if not facts:
            return _text(language, "empty_user_profile")

        lines = [_text(language, "user_profile_title")]
        lines.extend(f"- {key}: {value}" for key, value in sorted(facts.items()))
        return "\n".join(lines)

    def _agent_profile_text(self, language: str) -> str:
        if language != "ru":
            return self.profile.render()

        return (
            f"{self.profile.name}\n"
            f"Роль: {self.profile.role}\n"
            f"Миссия: {self.profile.mission}\n"
            f"Тон: {self.profile.tone}"
        )

    def _memory_text(self, language: str) -> str:
        facts = self.memory.all()
        facts.update(
            {
                record.key: record.value
                for record in self.memory.semantic_records()
                if record.status == "confirmed"
            }
        )
        candidates = [r for r in self.memory.semantic_records() if r.status == "candidate"]
        if not facts and not candidates:
            return _text(language, "empty_memory")

        lines = [_text(language, "memory_title")]
        lines.extend(f"- {key}: {value}" for key, value in sorted(facts.items()))
        label = "возможная потребность, не заявка" if language == "ru" else "possible need, no request created"
        lines.extend(f"- {r.key}: {r.value} ({label})" for r in candidates)
        return "\n".join(lines)

    def _history_text(self, language: str) -> str:
        if not self._history:
            return _text(language, "empty_history")

        lines = [_text(language, "history_title")]
        for turn in self._history:
            label = "You" if turn.role == "user" else "xBLAGO"
            lines.append(f"- {label}: {turn.text}")
        return "\n".join(lines)

    def _generate_assistant_response(self, text: str, memory: dict | None = None) -> str:
        context = ChatContext(
            agent_profile=self.profile,
            user_text=_safe_history_text(text),
            memory=self.memory.all() if memory is None else memory,
            history=self.history(),
            conversation=understand(_safe_history_text(text), self.history()),
        )
        try:
            response = self.chat_provider.generate(context)
            if (not isinstance(response, str) or not response.strip() or len(response) > 32000
                    or response in SERVICE_STATUS_TEXTS):
                raise LlmProviderError("Invalid assistant response")
            self._last_generation_succeeded = True
            return response
        except (LlmProviderError, TimeoutError, ValueError, TypeError) as exc:
            # Transport errors are not retried. The provider may repair one
            # incomplete text internally, without replaying this agent turn.
            self._last_generation_succeeded = False
            language = _language_for_text(text)
            return _text(language, "incomplete_answer" if getattr(exc, "category", "") == "incomplete" else "provider_unavailable")

    def _forget_denied_fact(self, text: str) -> None:
        # A direct retraction removes only the exact old fact; it invents no replacement.
        match = re.fullmatch(r"я (?:больше )?не живу в ([^.!?;]+)[.!]?", text.strip(), re.I)
        current = self.memory.all().get("city")
        if match and isinstance(current, str) and current.casefold() == match[1].strip().casefold():
            self.memory.delete("city")

    def _remember_from_conversation(self, text: str) -> bool:
        """Capture only explicit, stable, low-risk personal facts."""
        for key, value in _extract_personal_facts(text):
            if not is_sensitive_memory(key, value):
                facts = self.memory.all()
                keys = tuple(k for k, v in facts.items() if family(k, v) == key)
                if keys and (facts.get(key) != value or keys != (key,)):
                    self.memory.replace_semantic(MemoryEdit(key, value, "fact").record(), keys, facts)
                else:
                    self.memory.set(key, value)
                return True
        return False

    def _extract_semantic_memory(self, text: str) -> str | None:
        if self.memory_extractor is None:
            return
        try:
            context = ChatContext(
                agent_profile=self.profile,
                user_text=text,
                memory=self.memory.all(),
                history=self.history(),
            )
            records = candidates_to_records(self.memory_extractor.extract(context))
            normalized = [replace(r, key=family(r.key, r.value)) if r.category == "preference" else r for r in records]
            keys = [r.key for r in normalized]
            if len(keys) != len(set(keys)):
                return "В сообщении несколько вариантов одного предпочтения. Какой сейчас актуален?"
            for record in normalized:
                if record.category == "preference":
                    facts = self.memory.all()
                    edit = MemoryEdit(record.key, record.value, "preference", replacement=bool(re.search(r"\bтеперь\b", text, re.I)))
                    resolution = resolve_edit(edit, facts)
                    if resolution.question:
                        return resolution.question
                    if resolution.choices:
                        return clarification(resolution.choices, replacement=record.value)
                    self.memory.replace_semantic(record, resolution.keys, facts)
                else:
                    self.memory.save_semantic(record)
        except (LlmProviderError, TimeoutError, ValueError, TypeError):
            # Memory analysis is best-effort and must never block or alter the reply.
            return

    def _record_and_return(self, message: UserMessage, response: AgentResponse) -> AgentResponse:
        if response.text not in {_text("ru", "sensitive_memory_rejected"), _text("en", "sensitive_memory_rejected")}:
            response = AgentResponse(_safe_history_text(response.text), response.should_end_session)
        exchange = (ConversationTurn("user", _safe_history_text(message.text)),
                    ConversationTurn("assistant", _safe_history_text(response.text)))
        if self.exchange_recorder is not None:
            self.exchange_recorder(exchange)
        elif self.history_recorder is not None:
            for turn in exchange:
                self.history_recorder(turn)
        self._history.extend(exchange)
        self._history = self._history[-self.history_limit:]
        return response

    def _append_history(self, role: MessageRole, text: str) -> None:
        turn = ConversationTurn(role=role, text=_safe_history_text(text))
        self._history.append(turn)
        if self.history_recorder is not None:
            self.history_recorder(turn)
        if len(self._history) > self.history_limit:
            self._history = self._history[-self.history_limit :]


def _language_for_text(text: str) -> str:
    return "ru" if any("а" <= char.lower() <= "я" or char.lower() == "ё" for char in text) else "en"


def _text(language: str, key: str) -> str:
    texts = {
        "en": {
            "goodbye": "See you. Persistent memory is saved, and this session context is closed.",
            "memory_cleared": "Done. Local memory is cleared.",
            "history_cleared": "Done. This conversation context is cleared.",
            "greeting": "Hi.",
            "empty_user_profile": (
                "Your profile is empty for now.\n"
                "You can add a fact like this: `remember my timezone is Europe/Moscow`."
            ),
            "user_profile_title": "User profile:",
            "empty_memory": "I have not remembered anything yet. You can say: `remember my timezone is Europe/Moscow`.",
            "memory_title": "I remember:",
            "empty_history": "There is no saved conversation context in this session yet.",
            "history_title": "Current conversation:",
            "sensitive_memory_rejected": "I won't save passwords, tokens, card details, or other secrets.",
            "incomplete_answer": "I couldn't finish that answer. Your question is still in the conversation.",
            "provider_unavailable": "I couldn't answer just now. Your message is still in the conversation.",
        },
        "ru": {
            "goodbye": "До встречи. Постоянная память сохранена, сессионный контекст завершен.",
            "memory_cleared": "Готово. Локальная память очищена.",
            "history_cleared": "Готово. Сессионный контекст очищен.",
            "greeting": "Привет.",
            "empty_user_profile": (
                "Профиль пользователя пока пуст.\n"
                "Можно добавить факт так: `remember my timezone is Europe/Moscow`."
            ),
            "user_profile_title": "Профиль пользователя:",
            "empty_memory": "Пока ничего не запомнил. Можно сказать: `remember my timezone is Europe/Moscow`.",
            "memory_title": "Я помню:",
            "empty_history": "В этой сессии пока нет сохраненного хода диалога.",
            "history_title": "Текущий диалог:",
            "sensitive_memory_rejected": "Я не буду сохранять пароли, токены, данные карт и другие секреты.",
            "incomplete_answer": "Не получилось закончить ответ. Твой вопрос остался в диалоге.",
            "provider_unavailable": "Сейчас не получается связаться. Твоё сообщение осталось в диалоге.",
        },
    }
    return texts[language][key]


def _remembered_text(language: str, key: str, value: str) -> str:
    if language == "ru":
        return f"Запомнил: {key} = {value}."

    return f"I remembered: {key} = {value}."


def _forgot_text(language: str, key: str) -> str:
    if language == "ru":
        return f"Готово. Забыл `{key}`."

    return f"Done. I forgot `{key}`."


def _missing_memory_text(language: str, key: str) -> str:
    if language == "ru":
        return f"Я не нашел в памяти факт `{key}`."

    return f"I did not find `{key}` in memory."


def _extract_personal_facts(text: str) -> list[tuple[str, str]]:
    normalized = " ".join(text.strip().split())
    patterns = (
        (r"^(?:меня зовут|мое имя|моё имя)\s+(.+)$", "name"),
        (r"^(?:(?:исправление:|теперь)\s+)*(?:я живу в|мой город(?:\s*—|\s*-|\s*=)?|город)\s+(.+)$", "city"),
        (r"^(?:мой часовой пояс|мой timezone|timezone)\s*(?:—|-|=|is)?\s*(.+)$", "timezone"),
        (r"^(?:я работаю в|моя работа|я работаю)\s*(?:—|-|=|в)?\s*(.+)$", "work"),
        (r"^(?:я люблю|мне нравится|мне нравятся)\s+(.+)$", "likes"),
        (r"^(?:я предпочитаю|мне удобнее)\s+(.+)$", "preferences"),
    )
    for pattern, key in patterns:
        match = re.match(pattern, normalized, re.IGNORECASE)
        if match:
            value = match.group(1).strip(" .,!;:")
            if 1 <= len(value) <= 160 and not re.search(r"[.;!\n]|,|\b(?:но|а|и)\b", value, re.I):
                return [(key, value)]
    return []


def _safe_history_text(text: str) -> str:
    intent = detect_intent(text)
    if intent.name == "remember" and intent.key and intent.value and is_sensitive_memory(intent.key, intent.value):
        return text.replace(intent.value, "[скрыто]")
    return redact_sensitive_text(text)


def _without_unconfirmed_claims(text: str) -> str:
    # Provider prose cannot attest to a database write. Local command replies are
    # constructed only after commit and do not pass through this guard.
    claim = re.compile(
        r"^(?:готово[,.:]?\s*)?(?:теперь\s+)?(?:я\s+)?(?:"
        r"(?:запомнил[а]?|запоминаю)(?=[,:.]|\s+(?:что|это|ты|тво\w*|город|имя|предпочт\w*))|"
        r"(?:сохранил[а]?|обновил[а]?|изменил[а]?|удалил[а]?|забыл[а]?|сохраняю|обновляю)\s+"
        r"(?:(?:тво\w*|этот|эту|это|новый|новое)\s+)?(?:память|информац\w*|факт\w*|данные|предпочт\w*|город|имя)|"
        r"помню[, ]+что|"
        r"(?:создал[а]?|опубликовал[а]?|отправил[а]?|оформил[а]?|оплатил[а]?|перев[её]л|перевела)\s+"
        r"(?:(?:тво\w*|эту|этот|новую|новый)\s+)?(?:заявк\w*|заказ\w*|ресурс\w*|предложени\w*|деньги|плат[её]ж\w*|номер|данные))|"
        r"^(?:заявка|заказ|ресурс|плат[её]ж)\s+(?:создан\w*|опубликован\w*|отправлен\w*|выполнен\w*|провед[её]н\w*)|"
        r"^I(?:'ve| have)? (?:saved|remembered|updated|deleted|forgotten)\s+(?:your|this|that|the fact|in memory)\b|"
        r"^I(?:'ve| have)? (?:published|submitted|paid|transferred|sent)\s+(?:your|this|that|the|money)\b",
        re.I,
    )
    sentences = re.split(r"(?<=[.!?])\s+|\n+", text)
    kept = [sentence for sentence in sentences if not claim.search(sentence)]
    return " ".join(kept).strip() or (
        "Не могу подтвердить выполнение этого действия." if _language_for_text(text) == "ru"
        else "I can't confirm that this action was carried out."
    )
