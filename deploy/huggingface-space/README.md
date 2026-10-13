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
- optional `TELEGRAM_POLL_TIMEOUT_SECONDS`
- optional `TELEGRAM_REQUEST_TIMEOUT_SECONDS`

The Docker entrypoint runs:

```bash
xblago-telegram
```
