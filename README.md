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

## Unified Web App / Telegram Mini App (Foundation 02)

The Docker-hosted webhook server now serves the **same** responsive Web/PWA UI at `/app` and API at `/api/v1`. The existing Telegram webhook, CLI, SQLite identity, persisted memory, and chat behavior remain available. The browser interface uses the existing PersonalAssistantAgent; no mock messages or statistics are used.

### Web access without Telegram

Set a long random `XBLAGO_WEB_INVITE_CODE` (pilot-only shared invitation code) and `XBLAGO_DATA_DIR` on a persistent volume. Visit `https://<your-host>/app`, choose **I have an invitation**, choose a username and a password of at least 12 characters, and provide the code. Passwords are salted and stretched with PBKDF2; session tokens are random, server-stored as hashes and sent in HttpOnly/SameSite cookies. Use HTTPS, which is required for Secure cookies and most PWA features. For local **HTTP-only development**, override `XBLAGO_COOKIE_SECURE=false` (never on production).

For local web-only checks without contacting Telegram, set `TELEGRAM_DELIVERY_MODE=webhook`, `TELEGRAM_SKIP_WEBHOOK_REGISTRATION=true`, and `TELEGRAM_SKIP_STARTUP_CHECK=true`.

### Telegram Mini App

Configure BotFather's Mini App/menu button URL to `https://<your-host>/app`. A Telegram WebApp obtains a session by submitting its raw `initData`; the backend verifies Telegram's HMAC and max login age with `TELEGRAM_BOT_TOKEN`. It does **not** trust `initDataUnsafe` or browser-supplied BLAGO user IDs. The existing bot webhook remains `/telegram/<configured secret>` on the same listening port.

### Link existing Telegram and web accounts

From the **website**, open **Profile → Get linking code**, then privately message `/link HEX_CODE` to `@xBLAGObot` within 10 minutes. Each code is one-time, and the verified Telegram sender is attached to the website's BLAGO ID. History, needs, resources, sessions and memory are preserved; conflicting Telegram memory keys get a `telegram.` prefix. Linking commands in groups are rejected. This intentionally doesn't automatically combine accounts based on matching names.

### Available real endpoints

- `POST /api/v1/auth/register`, `POST /api/v1/auth/login`, `POST /api/v1/auth/telegram`, `POST /api/v1/auth/link-code`, `POST /api/v1/auth/logout`
- `GET/PATCH /api/v1/me`, `GET/POST /api/v1/chat/messages`, `GET /api/v1/memory`
- `GET/POST /api/v1/needs`, `GET/POST /api/v1/resources`
- `GET /api/v1/analytics/me`, `GET /api/v1/analytics/community`

Private needs/resources are only accessible by their creator. Community numbers aggregate only records explicitly marked `community`, without disclosing personal record details. On-chain assets, NFTs, liquidity, DAO governance and matching are **not yet implemented**. The pilot requires one running server and durable SQLite storage; high-availability deployment will require a shared database and distributed login-rate-limiting later.

**Security:** JSON only for API mutations, CSRF tokens on authenticated writes, explicit browser Origin checks, size limits, and basic in-memory throttling on login endpoints. Configure a separate secret/transport for an external email provider only if email authentication is added in a subsequent release. The temporary shared invite code should be rotated frequently or replaced with per-user invitations before broad onboarding.

Run all checks with `PYTHONPATH=src python -m unittest discover -s tests -v`.
