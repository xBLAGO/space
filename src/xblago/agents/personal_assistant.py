from __future__ import annotations

from xblago.core.llm import ChatContext, ChatProvider, LocalPlanningProvider
from xblago.core.memory import MemoryStore
from xblago.core.messages import AgentResponse, ConversationTurn, MessageRole, UserMessage
from xblago.core.profile import AgentProfile
from xblago.core.routing import detect_intent


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
    ) -> None:
        self.memory = memory
        self.chat_provider = chat_provider or LocalPlanningProvider()
        self.history_limit = history_limit
        self._history: list[ConversationTurn] = []

    def welcome_message(self) -> str:
        return (
            "xBLAGO: Привет. Я первый агент сети xBLAGO, твой персональный помощник. "
            "Могу запоминать важные факты, помогать формулировать задачи и раскладывать "
            "идеи на следующие шаги. Напиши /help, если нужен список команд."
        )

    def respond(self, message: UserMessage) -> AgentResponse:
        intent = detect_intent(message.text)

        if intent.name == "exit":
            return self._record_and_return(
                message,
                AgentResponse("До встречи. Постоянная память сохранена, сессионный контекст завершен.", True),
            )

        if intent.name == "help":
            return self._record_and_return(message, AgentResponse(self._help_text()))

        if intent.name == "profile":
            return self._record_and_return(message, AgentResponse(self.profile.render()))

        if intent.name == "memory":
            return self._record_and_return(message, AgentResponse(self._memory_text()))

        if intent.name == "history":
            return self._record_and_return(message, AgentResponse(self._history_text()))

        if intent.name == "clear_memory":
            self.memory.clear()
            return self._record_and_return(message, AgentResponse("Готово. Локальная память очищена."))

        if intent.name == "clear_history":
            self._history.clear()
            return self._record_and_return(message, AgentResponse("Готово. Сессионный контекст очищен."))

        if intent.name == "remember" and intent.key and intent.value:
            self.memory.set(intent.key, intent.value)
            return self._record_and_return(
                message,
                AgentResponse(f"I remembered: {intent.key} = {intent.value}."),
            )

        if intent.name == "greeting":
            return self._record_and_return(
                message,
                AgentResponse(
                    "Привет. Я на связи. С чего начнем: задача, идея, план дня или настройка памяти?"
                ),
            )

        return self._record_and_return(message, AgentResponse(self._generate_assistant_response(message.text)))

    def history(self) -> tuple[ConversationTurn, ...]:
        return tuple(self._history)

    def _help_text(self) -> str:
        return (
            "Я умею:\n"
            "- запоминать факты: `remember my timezone is Europe/Moscow`\n"
            "- показывать память: `/memory`\n"
            "- показывать текущий диалог: `/history`\n"
            "- очищать память: `/clear-memory`\n"
            "- очищать текущий диалог: `/clear-history`\n"
            "- показывать профиль агента: `/profile`\n"
            "- завершать сессию: `/exit`\n\n"
            "Если напишешь обычную задачу, я помогу выделить цель и следующий шаг."
        )

    def _memory_text(self) -> str:
        facts = self.memory.all()
        if not facts:
            return "Пока ничего не запомнил. Можно сказать: `remember my timezone is Europe/Moscow`."

        lines = ["I remember:"]
        lines.extend(f"- {key}: {value}" for key, value in sorted(facts.items()))
        return "\n".join(lines)

    def _history_text(self) -> str:
        if not self._history:
            return "В этой сессии пока нет сохраненного хода диалога."

        lines = ["Текущий диалог:"]
        for turn in self._history:
            label = "You" if turn.role == "user" else "xBLAGO"
            lines.append(f"- {label}: {turn.text}")
        return "\n".join(lines)

    def _generate_assistant_response(self, text: str) -> str:
        return self.chat_provider.generate(
            ChatContext(
                agent_profile=self.profile,
                user_text=text,
                memory=self.memory.all(),
                history=self.history(),
            )
        )

    def _record_and_return(self, message: UserMessage, response: AgentResponse) -> AgentResponse:
        self._append_history("user", message.text)
        self._append_history("assistant", response.text)
        return response

    def _append_history(self, role: MessageRole, text: str) -> None:
        self._history.append(ConversationTurn(role=role, text=text))
        if len(self._history) > self.history_limit:
            self._history = self._history[-self.history_limit :]
