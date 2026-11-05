# Interface Audit 01 — evidence

Дата: 2026-11-05. Runtime source: `d7f619a1e6a7f15564facb5e8e84b184a3f24658`; проверенный #19 HEAD: `837b2bfd5d3db4842530d30ccf0f417bfe718737`.

| Файл | Что проверено | Чего не доказывает |
|---|---|---|
| `public-fetch.json` | Свежие 4 EN GET, status/size/SHA256 | Deployment процесса и настройки всей инфраструктуры |
| `ru-fetch.json` | 4 RU GET 200; `/app` 404 | Полный аудит локализации/сервера |
| `public-browser.json` | 4 EN страницы, links/form count, desktop/mobile width, JS errors, ограниченные признаки текстов | Безопасность, tracking consent, отсутствие других routes или другого app host |
| `local-browser.json` | 12 Chrome checks на synthetic localhost #19 | Реальный Telegram, физические passkeys, HF, production |
| `public-home-mobile.png` | Публичная Home, anonymous 390×844 viewport, full page | Реальный mobile browser или accessibility conformance |
| `local-mobile-chat.png` | Пустой synthetic chat того же viewport | Поведение mobile keyboard/Telegram |
| `summary.json` | Scope, refs, тестовые результаты и SHA256 артефактов | Production attestation |

Source match для EN: вычислить `sha1(b'blob ' + str(len(body)).encode() + b'\0' + body)` из свежего HTTPS body и сравнить с Git tree pinned `xBLAGO/web2`. Четыре значения приведены в [site audit](../BLAGO_PUBLIC_SITE_AUDIT.md). HTML не включён в PR; адреса переводов и private data не воспроизводятся. Screenshot investors намеренно не публикуется.

Для повторения существующих Web/API tests в среде с проверенной SQLite:

```sh
PYTHONPATH=.:src python -m unittest tests.test_web tests.test_browser_e2e -v
```

Требуются зависимости из `requirements-integration.lock`, разрешённые localhost sockets и patched SQLite согласно #19. Не заменять библиотеку production Python. Подробности Chrome checks: `local_check.py` и `public_check.py` — использованные вспомогательные scripts, сохранённые только как документационное evidence. Перед запуском настроить `BLAGO_AUDIT_CHROME`, при необходимости driver environment и `BLAGO_AUDIT_OUTPUT` вне repo. Они не входят в runtime или CI и не должны запускаться на production configuration.

```sh
PYTHONPATH=src python docs/ui_audit01/local_check.py
python docs/ui_audit01/public_check.py
```

Local script использует `tools/pilot_fixture.py`: новая временная SQLite, синтетические account/password, local provider, security journal и witness. Для Web-only проверки SDK Telegram заменён пустым response в отдельном browser context. Это не проверка настоящего Telegram exchange. Public script читает только четыре страницы и assets, блокируя analytics domains; не отправляет формы и не входит в аккаунты. Chrome запускается с новым context, не с пользовательским профилем. Cookies, пароль, raw exports и HTML не включены в evidence JSON.

Исторический полный #19 CI: [41902654438](https://github.com/xBLAGO/space/actions/runs/41902654438), HEAD `837b2bf`, success. Это не CI нового docs PR. Workflow #19 не подписан на `docs/**` push и pull requests в `integration/foundation-01-06-1`; отсутствие нового run обозначается NOT TRIGGERED, а не PASS. Workflows и release controls здесь не меняются.
