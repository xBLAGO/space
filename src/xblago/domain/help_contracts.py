"""Foundation Help 01 boundary only. No storage, routes, executor or payment model.
A session principal identifies an actor; it is never a federation mandate.
"""
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

class HelpState(str,Enum):
    DRAFT='draft'
    PUBLISHED='published'
    WITHDRAWN='withdrawn'
    ACCEPTED='accepted'
    COMPLETED='completed'
    CANCELLED='cancelled'

@dataclass(frozen=True)
class ReviewedPublication:
    actor_id: str
    draft_id: str
    revision: int
    exact_text_sha256: str
    audience: tuple[str,...]
    confirmation_id: str

class HelpService(Protocol):
    """Future implementation MUST recheck principal, revision and atomic one-use
    confirmation against exact text/audience; permission is not inferred from chat.
    Private memory, bio/key material, money and ratings are outside this contract.
    """
    def publish(self, principal: str, approval: ReviewedPublication) -> str: ...
    def withdraw(self, principal: str, object_id: str, expected_revision: int) -> None: ...
    def offer(self, principal: str, need_id: str, expected_revision: int) -> str: ...
    def accept(self, principal: str, offer_id: str, expected_revision: int) -> None: ...
    def confirm_completion(self, principal: str, commitment_id: str, expected_revision: int) -> None: ...
