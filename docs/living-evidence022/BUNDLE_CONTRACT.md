# Контракт bundle v1

Версия `blago-living-bundle-1`. Нормативная исполняемая модель: `tools/living_bundle_contract.py`; strict audit field set: `tools/living_bundle_verify.py:FIELDS`. JSON UTF-8, duplicate keys запрещены, SHA-256 по точным байтам; для expected/local_ui map используется canonical `wire()` (sorted keys, без пробелов, UTF-8). Хеши manifest сами себя не включают.

| Файл | Содержимое |
|---|---|
| manifest.json | schema/harness/verifier versions; base_sha/checked_head/source_clean; created_at/capture_started_at; local_ui source_ref/files/sha256; отсутствие отдельного Gemini source; browser_version; plan; seed; три branch initial_database_sha256/status/reason; expected_matrix_sha256; cleanup_method; artifacts; claimed_verdict; independent verdict |
| visibility-matrix.json | frozen_at, expected — нормативные строки до capture, actual — только производные от audit |
| audit.jsonl | одна строка на наблюдение; несколько network_response на checkpoint допускаются с уникальным request_id |
| report.md | воспроизводимость, вердикт, 5 колонок по шагам, блокеры |
| *.png, *.html, *.txt, *.json | хешированные скриншоты и поверхности; никакой БД/ключей/сессий |

Каждая audit-строка содержит branch, checkpoint, step, role, session_id (произвольная метка), request_id, invitation_id, surface, action, visible_field_names, marker_presence, consent_state, refusal_state, revocation_state, recipient_first_text_delivery, timestamp; также artifact/screenshot, banner_dom/banner_screenshot, request_text_sha256, mask, counters, endpoint/http_status/response_field_names. Точный набор обязателен; неприменимые значения null. Timestamp ISO8601 с timezone, порядок внутри ветки монотонный.

Сетевой artifact: request_body и response_body; transport credentials, cookies, tokens, csrf и nonce не сохраняются. Оставлять такие ключи даже с REDACTED нельзя. Коллектор должен сканировать исходный ответ до удаления транспортных полей. Полная частная заметка B сканируется в RAM, затем заменяется на реальный NOTE_SECRET + [MASKED]. JSON/DOM escaping должен быть однозначно разобран; непонятная кодировка блокирует сохранение. Текстовый helper не реализует обработку картинок. Снимки B требуют отдельного проверенного маскирования до сохранения; полный текст не допускается даже в промежуточном artifact.

Successful C open response allowlist: `format, receipt, task, referrer, contact_only, resource_available`. Весь task совпадает с согласованным текстом; contact_only=true, resource_available=false. До первого успешного открытия task запрещён на всех поверхностях C. `decline_invitation` request data содержит только id. Причина: **N/A — причина не собирается**. REFUSAL_REASON_* не применять.

Три исхода — три клона одного начального состояния, не последовательные ветки в одной БД. В decline после отказа требуется остановить процесс браузера и все контексты, перезапустить сервер с прежним store, войти заново в свежих контекстах. Ожидаемый cleanup_method: `CLOSE_BROWSER_PROCESS_AND_ALL_CONTEXTS; FRESH_LOGINS`. Не записывать cookies; это обозначение процедуры, не независимое доказательство её выполнения.

После контакта counters offers/published_resources/reservations должны явно наблюдаться как 0, а не подразумеваться. Если API их не предоставляет, доработать наблюдатель/безопасный read model отдельной проверенной итерацией; не выдумывать нули. Отказ C не меняет author visible/network status/actions/counts; сравниваются реальные поверхности, не только marker_presence.

Все 360 expected-строк доступны из `expected_matrix()`. REQUIRED означает положительный контроль, FORBIDDEN — запрет на каждой поверхности, OPTIONAL — допустимость без требования появления. Actual пересчитывается из audit, marker union включает все ответы. Проверять весь текст, не один REQ_TEXT. Ccode — реальный синтетический help-member tag.

Verifier codes: 0 — BUNDLE_CONSISTENT_NOT_INDEPENDENT_ATTESTATION; 1 — FAIL/INVALID_BUNDLE; 2 — BLOCKED, пропуски сохраняются. Блокированная ветка + PASS считается ложным утверждением. Browser capture и внешний независимый вердикт не заменяются unit-fixtures.
