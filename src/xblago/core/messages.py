from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class UserMessage:
    text: str


@dataclass(frozen=True)
class AgentResponse:
    text: str
    should_end_session: bool = False


MessageRole = Literal["user", "assistant"]


@dataclass(frozen=True)
class ConversationTurn:
    role: MessageRole
    text: str
