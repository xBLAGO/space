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
- optional `TELEGRAM_DELIVERY_MODE`
- optional `TELEGRAM_WEBHOOK_SECRET`
- optional `TELEGRAM_WEBHOOK_URL`
- optional `TELEGRAM_POLL_TIMEOUT_SECONDS`
- optional `TELEGRAM_REQUEST_TIMEOUT_SECONDS`

On Hugging Face Spaces, the bot uses Telegram webhook delivery automatically when `SPACE_HOST` is present.

The Docker entrypoint runs:

```bash
xblago-telegram
```
