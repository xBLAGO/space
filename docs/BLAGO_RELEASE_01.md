# BLAGO Release 01

## Назначение

Release 01 готовит PR #2 к безопасной публикации BLAGO Unified App в Hugging Face Space. Цель релиза: один backend обслуживает Telegram-бота, Telegram Mini App, Web/PWA, SQLite-хранилище, память, историю, потребности, ресурсы и MVP Economy 01.

Merge в `main` и deployment в Hugging Face выполняются только после явного подтверждения владельца проекта.

## Статус проверки

- Ветка: `feat/foundation-02-unified-app`.
- PR: `https://github.com/xBLAGO/space/pull/2`.
- Локальные unit/integration/browser-style тесты должны проходить командой `PYTHONPATH=src python -m unittest discover -s tests -v`.
- GitHub Actions `Tests` теперь проверяет Python-тесты, синтаксис и Docker smoke test.
- Docker smoke test собирает образ, запускает один серверный порт `7860` и проверяет `/healthz`.
- Workflow публикации в Hugging Face запускается только вручную через `workflow_dispatch` и требует ввода `DEPLOY`.

## Docker Readiness

Docker-образ должен:

- устанавливаться из `pyproject.toml`;
- запускать `xblago-telegram`;
- слушать один порт `7860`;
- отдавать `/healthz`;
- обслуживать `/app`, `/api/v1/*` и `/telegram/*` через тот же процесс;
- запускаться без обращения к Telegram во время smoke test при `TELEGRAM_SKIP_STARTUP_CHECK=true` и `TELEGRAM_SKIP_WEBHOOK_REGISTRATION=true`.

Локальная команда для ручной проверки:

```bash
docker build -t xblago-space:release-01 .
docker run --rm -p 7860:7860 \
  -e TELEGRAM_BOT_TOKEN=123:local-smoke \
  -e TELEGRAM_DELIVERY_MODE=webhook \
  -e TELEGRAM_WEBHOOK_URL=http://127.0.0.1:7860/telegram/local-smoke \
  -e TELEGRAM_WEBHOOK_SECRET=local-smoke \
  -e TELEGRAM_SKIP_STARTUP_CHECK=true \
  -e TELEGRAM_SKIP_WEBHOOK_REGISTRATION=true \
  -e XBLAGO_WEB_INVITE_CODE=local-release-invite \
  -e XBLAGO_COOKIE_SECURE=false \
  xblago-space:release-01
```

Затем проверить:

```bash
curl -fsS http://127.0.0.1:7860/healthz
curl -fsS http://127.0.0.1:7860/app
curl -fsS http://127.0.0.1:7860/app/manifest.webmanifest
curl -fsS http://127.0.0.1:7860/app/sw.js
```

## Hugging Face Storage

Без постоянного хранилища данные участников могут быть потеряны при перезапуске или остановке Space. Для публикации обязательно:

- подключить Hugging Face persistent storage к Space;
- задать `XBLAGO_DATA_DIR` на путь внутри persistent storage;
- хранить SQLite, legacy backups и SQLite backups только в этом private storage;
- не коммитить `.sqlite`, `.sqlite3`, `.db`, memory exports или пользовательские документы в GitHub/Hugging Face repo;
- перед релизом создать тестового пользователя, перезапустить Space и убедиться, что память и история сохранились.

SQLite backup:

- код поддерживает `SQLiteBlagoStore.backup_database()`;
- backup надо хранить на persistent volume или переносить в контролируемое private-хранилище;
- backup не является публичным артефактом.

## Required Secrets And Variables

Hugging Face Space secrets:

- `TELEGRAM_BOT_TOKEN`;
- `TELEGRAM_WEBHOOK_SECRET`;
- `XBLAGO_WEB_INVITE_CODE`;
- optional `XBLAGO_LLM_PROVIDER`;
- optional `XBLAGO_LLM_MODEL`;
- optional `XBLAGO_LLM_API_KEY`;
- optional `XBLAGO_LLM_BASE_URL`;
- optional `TELEGRAM_SKIP_STARTUP_CHECK`;
- optional `TELEGRAM_SKIP_WEBHOOK_REGISTRATION`;
- optional `TELEGRAM_RETURN_WEBHOOK_REPLIES`.

Hugging Face Space variables:

- `XBLAGO_DATA_DIR`;
- optional `XBLAGO_PUBLIC_ORIGIN`;
- optional `TELEGRAM_DELIVERY_MODE=webhook`;
- optional `TELEGRAM_WEBHOOK_URL` if webhook URL is not derived from `SPACE_HOST`;
- `XBLAGO_COOKIE_SECURE=true` for production HTTPS.

GitHub Actions:

- secret `HF_TOKEN`;
- optional variable `HF_SPACE_REPO`, default `RomanMaloev/space`.

Do not store real tokens, bot secrets, invite codes, API keys, payment data or user exports in the repository.

## BotFather Checklist

- Set Mini App/Menu URL to `https://<space-host>/app`.
- Keep Telegram bot webhook path secret-based.
- Test `/start`, `/help`, `/memory`, `/profile`, `/history` in a private chat.
- Test web link flow: website profile link code, then `/link HEX_CODE` to the bot in private chat.
- Do not enable group linking; group `/link` should stay rejected.

## Web/PWA Checklist

- `/healthz` returns `ok`.
- `/app` loads over HTTPS.
- Registration requires `XBLAGO_WEB_INVITE_CODE`.
- Login/logout work.
- Chat uses the real BLAGO assistant and persists history.
- Profile saves language/timezone/display name.
- Memory is visible only to the authenticated user.
- Needs and resources create, list and preserve private/community visibility.
- Analytics show real counts, not demo numbers.
- `/app/manifest.webmanifest` is reachable.
- `/app/sw.js` is reachable and does not cache authenticated API data.
- Mobile and desktop layouts are checked in a real browser before release.

## Security Checklist

- Telegram Mini App auth verifies raw `initData` server-side with `TELEGRAM_BOT_TOKEN`.
- Invalid or stale Telegram `initData` is rejected.
- Session cookies are HttpOnly and SameSite.
- `XBLAGO_COOKIE_SECURE=true` in production.
- Authenticated writes require CSRF tokens.
- Browser Origin checks remain enabled.
- Login throttling remains enabled.
- Other users' memory, needs, resources, assets, payment requests and offers are not readable.
- Telegram repeated `update_id` values do not duplicate work.
- Webhook errors do not falsely mark failed delivery as processed.
- Logs do not print real tokens or full payment references.

## Economy 01 Audit

Implemented MVP entities:

- `Needs`;
- `Assets`;
- `ResourceOffers`;
- `PaymentRequests`;
- `FulfillmentEvents`;
- `ContributionEvents`.

Observed safety properties:

- assets are self-declared and unverified by default;
- payment references are stored as digest, not open text;
- real payments are not executed;
- manual confirmation explicitly is not bank verification;
- one payment request can be accepted by only one executor;
- contribution events are deduplicated by `event_key`;
- repeated confirmation does not create a second contribution;
- rejected/high-risk requests cannot be offered;
- xBLAGO is only a statistical estimate and does not create token balance, claim, payout right or privilege.

Release limitations:

- no KYC/KYB;
- no licensed payment provider integration;
- no automated fiat transfer;
- no sanctions/fraud screening beyond MVP keyword risk checks;
- no public immutable audit ledger;
- no moderation workflow for `disputed`;
- no per-user invitation system yet;
- no distributed database or distributed rate limiter.

Real payments must not be enabled until legal, anti-fraud, privacy and provider integrations are designed separately.

## Publication Gate

Before owner approval to deploy:

- PR #2 is up to date with `feat/foundation-02-unified-app`;
- GitHub Actions `Tests` is green on the exact commit to publish;
- Docker smoke test is green in GitHub Actions;
- release owner has verified mobile and desktop UI in a browser;
- Hugging Face persistent storage is attached;
- `XBLAGO_DATA_DIR` points to persistent storage;
- Space secrets are configured;
- BotFather Mini App URL is configured;
- owner confirms that deployment may be run.

Deployment is performed only by manually starting `Deploy Hugging Face Space` workflow and entering `DEPLOY`.

## Rollback

- Do not delete persistent storage during rollback.
- Keep the previous known-good Git commit hash.
- Re-run the manual deploy workflow for the previous commit if application rollback is needed.
- Before schema-changing releases, create a SQLite backup using `SQLiteBlagoStore.backup_database()`.
- If the Space fails to boot, disable webhook registration temporarily and check `/healthz` and container logs.

