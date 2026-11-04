"""Ephemeral conversational guidance, never an authorization or personality label.

Rules are deliberately hints: the main model interprets nuance in the same call.
No economic action, memory write, durable classification or LLM call lives here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from xblago.core.messages import ConversationTurn

HISTORY_TURN_LIMIT = 24
HISTORY_CHARACTER_BUDGET = 24000
# Application-owned transport/status messages, including the Foundation 04/05
# versions. This is provenance projection of complete assistant records, not a
# blacklist of ordinary words or of the participant's speech. Storage is untouched.
SERVICE_STATUS_TEXTS = frozenset({
    "Сейчас не получается связаться с языковой моделью, но я здесь. Попробуй ещё раз позже.",
    "I can't reach the language model right now, but I'm still here. Please try again later.",
    "Сейчас не получается связаться. Твоё сообщение осталось в диалоге.",
    "I couldn't answer just now. Your message is still in the conversation.",
    "Не получилось закончить ответ. Твой вопрос остался в диалоге.",
    "I couldn't finish that answer. Your question is still in the conversation.",
})


class ConversationIntent(str, Enum):
    REFLECTION = "reflection"
    CONVERSATION = "conversation"
    EMOTION = "emotion"
    FACT_QUESTION = "fact_question"
    NEED = "need"
    RESOURCE_OFFER = "resource_offer"
    ACTION_REQUEST = "action_request"
    REFUSAL = "refusal"
    REVISION = "revision"


@dataclass(frozen=True)
class ConversationGuidance:
    intent: ConversationIntent
    continuing: bool
    previous_intent: ConversationIntent | None = None

    def instruction(self) -> str:
        advice = {
            ConversationIntent.REFLECTION: "Explore the thought without converting it into a task or a need.",
            ConversationIntent.CONVERSATION: "Respond to the conversation itself; no initiative is required.",
            ConversationIntent.EMOTION: "Acknowledge what was actually said without diagnosis, staged empathy or unsolicited solutions.",
            ConversationIntent.FACT_QUESTION: "Answer the question directly; distinguish evidence from uncertainty.",
            ConversationIntent.NEED: "Discuss the stated need. No request has been created or authorized.",
            ConversationIntent.RESOURCE_OFFER: "Explore the voluntary offer without committing, publishing or transferring anything.",
            ConversationIntent.ACTION_REQUEST: "Help with the explicit request inside this conversation. External execution is unavailable here.",
            ConversationIntent.REFUSAL: "Respect the refusal. Do not persuade or repeat the declined offer.",
            ConversationIntent.REVISION: "Give precedence to the latest intention; do not continue a superseded plan.",
        }
        continuity = "This is an existing conversation. Continue without reintroducing yourself." if self.continuing else "No introduction is required."
        previous = f" Previous conversational hint: {self.previous_intent.value}." if self.previous_intent else ""
        return (f"Tentative conversational hint: {self.intent.value}. It may be wrong; interpret the actual message and history. "
                + advice[self.intent] + " " + continuity + previous)


def _hint(text: str) -> ConversationIntent:
    text = " ".join(text.casefold().split())
    # Anchored first-person/imperative cues avoid treating a quoted person's need
    # as the participant's own intent. Ambiguity stays ordinary conversation.
    rules = (
        (ConversationIntent.REVISION, r"^(?:я )?(?:передумал[а]?|изменил[а]? мнение)|^(?:лучше|давай) вместо|^(?:раньше|теперь)\b|^actually[, ]+|^i changed my mind"),
        (ConversationIntent.REFUSAL, r"^(?:нет\b|не (?:надо|нужно|хочу|создавай|публикуй|отправляй|предлагай)|отмен[аи]|no\b|don['’]t\b)"),
        (ConversationIntent.RESOURCE_OFFER, r"^(?:я )?(?:могу помочь|готов[а]? помочь|предлагаю помощь|могу поделиться|готов[а]? поделиться)|^у меня есть .+ (?:могу|готов)|^i (?:can help|can offer|could help)"),
        (ConversationIntent.EMOTION, r"^(?:я )?(?:очень )?(?:устал[а]?|расстроен[а]?|рад[а]?\b|злюсь|тревожусь)|^мне (?:грустно|обидно|тяжело|страшно)|^i (?:feel|am tired|am sad)"),
        (ConversationIntent.REFLECTION, r"^(?:я )?(?:думаю|размышляю|кажется|иногда думаю)|^(?:а что если|интересно[,. ]|может быть|пожалуй)|^i (?:wonder|think)|^what if\b"),
        (ConversationIntent.NEED, r"^(?:мне (?:нужен|нужна|нужно|нужны)|я ищу|i need|i am looking for)\b"),
        (ConversationIntent.ACTION_REQUEST, r"^(?:пожалуйста[, ]+)?(?:создай|опубликуй|отправь|переведи|купи|забронируй|составь|собери|добавь|напиши|расскажи|объясни|сравни|помоги|подготовь|можешь\b|please\b|write\b|explain\b|create\b|send\b|build\b|add\b)"),
        (ConversationIntent.FACT_QUESTION, r"^(?:почему|как|когда|где|кто|что (?:такое|значит)|сколько|чем отличается|why\b|how\b|when\b|what is\b|who\b)"),
    )
    for intent, pattern in rules:
        if re.search(pattern, text):
            return intent
    return ConversationIntent.CONVERSATION


def understand(text: str, history: tuple[ConversationTurn, ...] = ()) -> ConversationGuidance:
    previous = next((turn.text for turn in reversed(history) if turn.role == "user"), None)
    return ConversationGuidance(_hint(text), bool(history), _hint(previous) if previous else None)


def model_history(history: tuple[ConversationTurn, ...]) -> tuple[ConversationTurn, ...]:
    """Bound prompt cost without slicing a sentence or altering stored history."""
    chosen: list[ConversationTurn] = []
    size = 0
    for turn in reversed(history[-HISTORY_TURN_LIMIT:]):
        if turn.role not in ("user", "assistant"):
            continue
        if turn.role == "assistant" and turn.text in SERVICE_STATUS_TEXTS:
            continue
        if size + len(turn.text) > HISTORY_CHARACTER_BUDGET:
            break
        chosen.append(turn)
        size += len(turn.text)
    chosen.reverse()
    while chosen and chosen[0].role != "user":
        chosen.pop(0)
    return tuple(chosen)
