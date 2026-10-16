---
title: xBLAGO Bot
emoji: 🤖
colorFrom: blue
colorTo: green
sdk: docker
pinned: false
---

# xBLAGO Bot

Telegram entrypoint for the first xBLAGO personal assistant agent.

Recommended Space repo: `RomanMaloev/space`.

Required Space secrets:

- `TELEGRAM_BOT_TOKEN`
- optional `XBLAGO_LLM_PROVIDER`
- optional `XBLAGO_LLM_MODEL`
- optional `XBLAGO_LLM_API_KEY`
- optional `XBLAGO_LLM_BASE_URL`
- optional `XBLAGO_LLM_TIMEOUT_SECONDS`
- optional `XBLAGO_LLM_MAX_TOKENS`

For Hugging Face Inference Providers, set `XBLAGO_LLM_PROVIDER=hf`,
`HF_TOKEN` (or `XBLAGO_LLM_API_KEY`), and `XBLAGO_LLM_MODEL=Qwen/Qwen3-14B:cheapest`.
The application uses the OpenAI-compatible router at
`https://router.huggingface.co/v1` by default. Keep the provider disabled (`local`)
until the token and model have been tested; no deployment or secret is changed by
the repository configuration.
- optional `TELEGRAM_DELIVERY_MODE`
- optional `TELEGRAM_WEBHOOK_SECRET`
- optional `TELEGRAM_WEBHOOK_URL`
- optional `TELEGRAM_SKIP_STARTUP_CHECK`
- optional `TELEGRAM_POLL_TIMEOUT_SECONDS`
- optional `TELEGRAM_REQUEST_TIMEOUT_SECONDS`
- optional `XBLAGO_DATA_DIR`

On Hugging Face Spaces, the bot uses Telegram webhook delivery automatically when `SPACE_HOST` is present.

For long-running BLAGO participant data, attach a Hugging Face Storage Bucket/Volume and set `XBLAGO_DATA_DIR` to the mounted path. Without an attached volume, the Space filesystem is ephemeral and the SQLite database can be lost when the Space restarts or is stopped.

The bot keeps legacy JSON memory files reversible: before importing `telegram_<chat_id>.json`, it copies the source file to a timestamped `backups/` directory.

SQLite can be backed up while the app is running through the internal `SQLiteBlagoStore.backup_database()` helper. Store backups on the same persistent volume or copy them to a controlled private location; never commit `.sqlite3` files or user exports to the repository.

The Docker entrypoint runs:

```bash
xblago-telegram
```

## Browser access (Foundation 02)

When deployed in webhook mode, the same Space port serves:

- `/app`: one responsive Web/PWA and Telegram Mini App user interface
- `/api/v1/*`: web auth, chat, needs, resources, profile, memory and real analytics
- `/healthz`: health endpoint
- `/telegram/*`: unchanged Telegram Bot webhook

Configure **Secret** `XBLAGO_WEB_INVITE_CODE` to enable invite-only website registration. Use a strong random value; this is a pilot-only shared code. Set `XBLAGO_DATA_DIR` to a persistent mounted storage location. `XBLAGO_COOKIE_SECURE=true` is the default and requires HTTPS. Optionally set `XBLAGO_PUBLIC_ORIGIN` for an explicitly approved external origin; frontend and API should ordinarily share the same origin.

In BotFather, configure the Mini App/menu URL to your publicly reachable HTTPS `/app` endpoint. Raw Telegram WebApp initData is checked server-side, no separate Telegram frontend or database required. Browser users who have no Telegram account can register using the invitation code.

Before publishing, follow the full checklist in `docs/BLAGO_RELEASE_01.md`. At minimum, verify:

- `/healthz` returns `ok`
- `/app` loads over HTTPS
- `/app/manifest.webmanifest` and `/app/sw.js` are reachable
- `XBLAGO_DATA_DIR` points to attached persistent storage
- `XBLAGO_WEB_INVITE_CODE`, `TELEGRAM_BOT_TOKEN`, and webhook/relay secrets are configured as Space secrets

GitHub deployment is manual-only. Start the `Deploy Hugging Face Space` workflow only after owner approval and enter `DEPLOY` in the confirmation input.
