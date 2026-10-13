from __future__ import annotations

import argparse
from pathlib import Path

from xblago.agents.personal_assistant import PersonalAssistantAgent
from xblago.core.config import load_llm_config
from xblago.core.llm import build_chat_provider
from xblago.core.memory import JsonMemoryStore, default_memory_path
from xblago.core.messages import UserMessage


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the xBLAGO personal assistant.")
    parser.add_argument(
        "--memory",
        type=Path,
        default=default_memory_path(),
        help="Path to the local JSON memory file.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    memory = JsonMemoryStore(args.memory)
    llm_config = load_llm_config()
    chat_provider = build_chat_provider(llm_config)
    agent = PersonalAssistantAgent(memory=memory, chat_provider=chat_provider)

    print(agent.welcome_message())

    while True:
        try:
            raw_text = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nxBLAGO: До встречи. Я буду рядом, когда понадоблюсь.")
            return

        if not raw_text:
            continue

        response = agent.respond(UserMessage(text=raw_text))
        print(f"xBLAGO: {response.text}")

        if response.should_end_session:
            return
