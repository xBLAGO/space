"""Foundation Help 01 boundary only. No storage, routes, executor or payment model.
A session principal identifies an actor; it is never a federation mandate.
"""
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

class HelpState(str,Enum):
    DRAFT='DRAFT'
    PUBLISHED='PUBLISHED'
    OFFERED='OFFERED'
    AWAITING_CONFIRMATION='AWAITING_CONFIRMATION'
    DISPUTED='DISPUTED'
    WITHDRAWN='WITHDRAWN'
    ACCEPTED='ACCEPTED'
    COMPLETED='COMPLETED'

@dataclass(frozen=True)
class ReviewedPublication:
    actor_id: str
    draft_id: str
    revision: int
    exact_text_sha256: str
    audience: tuple[str,...]
    confirmation_id: str

class HelpService(Protocol):
    """Runtime boundary: session token authenticates; prepare binds exact server
    revision/audience; commit requires the actor's explicit one-use confirmation.
    No LLM may call mutation methods or receive challenge tokens.
    """
    def draft(self, token: str, text: str, audience: list[str], key: str) -> dict: ...
    def prepare(self, token: str, rid: str, version: int, action: str, args: dict) -> dict: ...
    def commit(self, token: str, challenge: str, confirmation: str) -> dict: ...
    def get(self, token: str, rid: str) -> dict: ...
    def feed(self, token: str) -> dict: ...
