from __future__ import annotations

from collections.abc import Callable
import re

from xblago.core.llm import ChatContext, ChatProvider, LlmProviderError, LocalPlanningProvider, OpenAICompatibleProvider
from xblago.core.memory import MemoryStore
from xblago.core.memory import is_sensitive_memory
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
            "Help the user clarify intentions, remember stable preferences, "
            "turn loose ideas into next actions, and prepare requests for "
            "future specialized xBLAGO agents."
        ),
        tone="Warm, concise, practical, and quietly proactive.",
    )

    def __init__(
        self,
        memory: MemoryStore,
        chat_provider: ChatProvider | None = None,
        history_limit: int = 12,
        initial_history: tuple[ConversationTurn, ...] = (),
        history_recorder: Callable[[ConversationTurn], None] | None = None,
        history_clearer: Callable[[], None] | None = None,
        memory_extractor: SemanticMemoryExtractor | None = None,
    ) -> None:
        self.memory = memory
        self.chat_provider = chat_provider or LocalPlanningProvider()
        self.history_limit = history_limit
        self.history_recorder = history_recorder
        self.history_clearer = history_clearer
        self.memory_extractor = memory_extractor or (
            SemanticMemoryExtractor(self.chat_provider)
            if isinstance(self.chat_provider, OpenAICompatibleProvider)
            else None
        )
        self._last_generation_succeeded = True
        self._history: list[ConversationTurn] = list(initial_history[-history_limit:])

    def welcome_message(self) -> str:
        return (
            "xBLAGO: Привет. Я первый агент сети xBLAGO, твой персональный помощник. "
            "Могу запоминать важные факты, помогать формулировать задачи и раскладывать "
            "идеи на следующие шаги. Напиши /help, если нужен список команд."
        )

    def respond(self, message: UserMessage) -> AgentResponse:
        intent = detect_intent(message.text)
        language = _language_for_text(message.text)

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

        if intent.name == "remember" and intent.key and intent.value:
            if is_sensitive_memory(intent.key, intent.value):
                return self._record_and_return(
                    message,
                    AgentResponse(_text(language, "sensitive_memory_rejected")),
                )
            self.memory.set(intent.key, intent.value)
            return self._record_and_return(
                message,
                AgentResponse(_remembered_text(language, intent.key, intent.value)),
            )

        if intent.name == "forget" and intent.key:
            if self.memory.delete(intent.key):
                return self._record_and_return(message, AgentResponse(_forgot_text(language, intent.key)))

            return self._record_and_return(message, AgentResponse(_missing_memory_text(language, intent.key)))

        if intent.name == "greeting":
            return self._record_and_return(
                message,
                AgentResponse(_text(language, "greeting")),
            )

        response = self._generate_assistant_response(message.text)
        self._remember_from_conversation(message.text)
        if self._last_generation_succeeded:
            self._extract_semantic_memory(message.text)
        return self._record_and_return(message, AgentResponse(response))

    def history(self) -> tuple[ConversationTurn, ...]:
        return tuple(self._history)

    def _help_text(self, language: str) -> str:
        if language == "ru":
            return (
                "Я умею:\n"
                "- запоминать факты: `remember my timezone is Europe/Moscow`\n"
                "- забывать отдельный факт: `/forget timezone`\n"
                "- показывать память: `/memory`\n"
                "- показывать текущий диалог: `/history`\n"
                "- очищать память: `/clear-memory`\n"
                "- очищать текущий диалог: `/clear-history`\n"
                "- показывать профиль пользователя: `/profile`\n"
                "- показывать профиль агента: `/agent`\n"
                "- завершать сессию: `/exit`\n\n"
                "Если напишешь обычную задачу, я помогу выделить цель и следующий шаг."
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
            "If you send a regular task, I will help clarify the goal and next step."
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
        if not facts:
            return _text(language, "empty_memory")

        lines = [_text(language, "memory_title")]
        lines.extend(f"- {key}: {value}" for key, value in sorted(facts.items()))
        return "\n".join(lines)

    def _history_text(self, language: str) -> str:
        if not self._history:
            return _text(language, "empty_history")

        lines = [_text(language, "history_title")]
        for turn in self._history:
            label = "You" if turn.role == "user" else "xBLAGO"
            lines.append(f"- {label}: {turn.text}")
        return "\n".join(lines)

    def _generate_assistant_response(self, text: str) -> str:
        context = ChatContext(
            agent_profile=self.profile,
            user_text=text,
            memory=self.memory.all(),
            history=self.history(),
        )
        try:
            response = self.chat_provider.generate(context)
            self._last_generation_succeeded = True
            return response
        except (LlmProviderError, TimeoutError) as exc:
            # One provider attempt only; local fallback avoids Telegram retry loops and extra spend.
            self._last_generation_succeeded = False
            language = _language_for_text(text)
            return _text(language, "provider_unavailable")

    def _remember_from_conversation(self, text: str) -> None:
        """Capture only explicit, stable, low-risk personal facts."""
        for key, value in _extract_personal_facts(text):
            if not is_sensitive_memory(key, value):
                self.memory.set(key, value)

    def _extract_semantic_memory(self, text: str) -> None:
        if self.memory_extractor is None:
            return
        try:
            context = ChatContext(
                agent_profile=self.profile,
                user_text=text,
                memory=self.memory.all(),
                history=self.history(),
            )
            for record in candidates_to_records(self.memory_extractor.extract(context)):
                self.memory.save_semantic(record)
        except (LlmProviderError, TimeoutError, ValueError, TypeError):
            # Memory analysis is best-effort and must never block or alter the reply.
            return

    def _record_and_return(self, message: UserMessage, response: AgentResponse) -> AgentResponse:
        self._append_history("user", message.text)
        self._append_history("assistant", response.text)
        return response

    def _append_history(self, role: MessageRole, text: str) -> None:
        turn = ConversationTurn(role=role, text=text)
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
            "greeting": "Hi. I am here. Where shall we start: a task, an idea, a daily plan, or memory setup?",
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
            "provider_unavailable": "I can't reach the language model right now, but I'm still here. Please try again later.",
        },
        "ru": {
            "goodbye": "До встречи. Постоянная память сохранена, сессионный контекст завершен.",
            "memory_cleared": "Готово. Локальная память очищена.",
            "history_cleared": "Готово. Сессионный контекст очищен.",
            "greeting": "Привет. Я на связи. С чего начнем: задача, идея, план дня или настройка памяти?",
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
            "provider_unavailable": "Сейчас не получается связаться с языковой моделью, но я здесь. Попробуй ещё раз позже.",
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
        (r"^(?:я живу в|мой город(?:\s*—|\s*-|\s*=)?|город)\s+(.+)$", "city"),
        (r"^(?:мой часовой пояс|мой timezone|timezone)\s*(?:—|-|=|is)?\s*(.+)$", "timezone"),
        (r"^(?:я работаю в|моя работа|я работаю)\s*(?:—|-|=|в)?\s*(.+)$", "work"),
        (r"^(?:я люблю|мне нравится|мне нравятся)\s+(.+)$", "likes"),
        (r"^(?:я предпочитаю|мне удобнее)\s+(.+)$", "preferences"),
    )
    for pattern, key in patterns:
        match = re.match(pattern, normalized, re.IGNORECASE)
        if match:
            value = match.group(1).strip(" .,!;:")
            if 1 <= len(value) <= 160:
                return [(key, value)]
    return []
