---
title: xBLAGO Bot
emoji: X
colorFrom: blue
colorTo: green
sdk: docker
pinned: false
---

# xBLAGO

xBLAGO starts with a personal assistant agent: a warm, practical companion that talks with the user, remembers useful preferences locally, and prepares the ground for a wider network of specialized agents.

## What Is Included

- `xblago` CLI chat loop.
- A first agent profile: `PersonalAssistantAgent`.
- Local JSON memory stored outside the source tree by default.
- A small intent router for greetings, help, remembering facts, recalling facts, and planning next actions.
- Extension points for future agent-to-agent routing and LLM providers.

## Run

```bash
PYTHONPATH=src python3 -m xblago
```

Or, after installing the package:

```bash
xblago
```

## Run Telegram Bot

Create or reuse a Telegram bot token from BotFather, then run:

```bash
export TELEGRAM_BOT_TOKEN=your_bot_token
PYTHONPATH=src python3 -m xblago.integrations.telegram
```

The bot stores participant identity, memory, dialogue history, and processed Telegram updates in a local SQLite database under `~/.xblago/telegram` by default. Set `XBLAGO_DATA_DIR` to move this database to another directory.

Legacy `telegram_<chat_id>.json` memory files are migrated automatically and copied to a timestamped `backups/` directory before import.

## Deploy

The repository includes a Dockerfile that runs the Telegram bot:

```bash
docker build -t xblago-bot .
docker run --env-file .env xblago-bot
```

For Hugging Face Spaces, create a Docker Space at `RomanMaloev/space` and add the secrets listed in `deploy/huggingface-space/README.md`. The default Space filesystem is ephemeral, so long-running participant data requires a mounted Hugging Face Storage Bucket/Volume and `XBLAGO_DATA_DIR` pointing to that mount.

The GitHub Actions workflow `.github/workflows/huggingface-space.yml` can mirror `main` to a Hugging Face Docker Space after these GitHub settings are added:

- secret `HF_TOKEN`
- optional variable `HF_SPACE_REPO`, for example `RomanMaloev/space`

## Test

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```

Useful commands inside the chat:

- `/help` shows available commands.
- `/profile` shows the remembered user profile.
- `/agent` shows the active agent profile.
- `/memory` shows remembered facts.
- `/forget <key>` removes one remembered fact.
- `/history` shows the current session context.
- `/clear-memory` clears local memory.
- `/clear-history` clears the current session context.
- `/exit` ends the session.

The assistant keeps several kinds of context:

- Participant identity: an internal `blago_user_id` linked to Telegram `from.id`.
- Persistent memory: stable facts scoped to the internal user.
- Dialogue history: recent user and assistant turns restored after restart when stored on persistent disk.
- Event ledger: processed Telegram `update_id` values used to avoid duplicate operations.

## Optional LLM Provider

By default the assistant uses a deterministic local planning provider, so it works without network access or API keys.

To use an OpenAI-compatible chat completions endpoint:

```bash
export XBLAGO_LLM_PROVIDER=openai
export XBLAGO_LLM_MODEL=gpt-5-mini
export XBLAGO_LLM_API_KEY=your_api_key
PYTHONPATH=src python3 -m xblago
```

Optional:

```bash
export XBLAGO_LLM_BASE_URL=https://api.openai.com/v1
export XBLAGO_LLM_TIMEOUT_SECONDS=30
```

## Example

```text
You: remember my timezone is Europe/Moscow
xBLAGO: I remembered: timezone = Europe/Moscow.

You: what do you remember?
xBLAGO: I remember:
- timezone: Europe/Moscow

You: /forget timezone
xBLAGO: Done. I forgot `timezone`.
```

## Project Shape

```text
src/xblago/
  agents/              agent implementations
  core/                reusable primitives: messages, memory, routing
  cli.py               terminal interface
tests/                 focused tests for the first behavior
```

The first milestone is intentionally small: make the personal agent reliable, understandable, and easy to evolve.
