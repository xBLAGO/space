from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentProfile:
    name: str
    role: str
    mission: str
    tone: str

    def render(self) -> str:
        return (
            f"{self.name}\n"
            f"Role: {self.role}\n"
            f"Mission: {self.mission}\n"
            f"Tone: {self.tone}"
        )
