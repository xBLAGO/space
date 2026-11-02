"""Non-executing integration boundaries. Consensus 7 rules await supplied Genesis spec."""
from dataclasses import dataclass
from typing import Protocol, Literal


@dataclass(frozen=True)
class ProposalReference:
    proposal_id: str
    revision: int
    public_hash: str
    protocol_hash: str


@dataclass(frozen=True)
class JuryEvidence:
    proposal: ProposalReference
    jury_key: str
    decision: Literal['support','oppose','abstain']
    evidence_hash: str
    signature: str


class Consensus7(Protocol):
    """No default threshold, juror-selection, personhood or execution authority."""
    def verify(self, evidence: tuple[JuryEvidence, ...]) -> dict: ...


class PublicAnchor(Protocol):
    def prepare(self, proposal: ProposalReference) -> bytes: ...
    def verify(self, receipt: bytes, expected: ProposalReference) -> bool: ...


class UnconfiguredConsensus:
    def verify(self, *args, **kwargs):
        raise NotImplementedError('Consensus 7 specification, independent participants and authorization are not configured')


class UnconfiguredSolanaAnchor:
    def prepare(self, *args, **kwargs):
        raise NotImplementedError('No deployed program, RPC, wallet, transaction or signing authority')
    def verify(self, *args, **kwargs):
        raise NotImplementedError('No configured Solana anchor verifier')
