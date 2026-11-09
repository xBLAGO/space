# Retention Policy — proposal for review

Draft / **NOT APPROVED FOR REAL USERS**. Владелец проекта не утверждал этим PR юридические основания, сроки или операторов обработки. Сроки ниже — техническое предложение, согласованное с неизменённой `synthetic-lifecycle-1` Privacy01.3. Изменять её canonical JSON/checksum вручную запрещено; другая политика требует отдельной reviewed version/migration и regression/recovery evidence.

| Категория | Назначение / ожидаемый выбор человека | Предложенный срок и минимизация | Применяемый control / граница |
|---|---|---|---|
| Личные диалоги | Контекст/продолжение разговора, доступ только субъекту | 30d; user/assistant pair удаляется целиком | Existing paired sweep, denials, G09; legacy без доказанных границ блокирует очистку. Доставленный текст у Telegram/браузера — отдельная copy |
| Подтверждённая память | Полезные устойчивые факты, исправление/forget участником | До correction/forget/delete; candidate7d, archived superseded/negated30d | Archive retention сохраняет active descendants. User forget/delete по прежней политике; не стирать все факты по возрасту |
| Потребности/ресурсы legacy | Добровольное описание возможностей | 90d from creation при отсутствии financial/ownership blockers | Exactrow denials; legacy economic retention не определять догадкой. Никаких новых финансовых функций |
| Help private graph | Exact-text/version/audience consent, добровольный цикл | Idle active30d, terminal90d, hard180d | Retire text/notes/people/events/receipts. Account deletion может убрать весь затронутый Help graph; прекращение отображения другим участникам — известная политика01.3 |
| Цифровая идентичность | Устойчивый внутренний ID и подтверждённые способы | Active keys до revoke; revoked key/device90d. Session idle1800s/absolute7d; challenges по expires_at | Не удалять последний active factor по TTL. Account tombstone/withdrawal/replay остаются; session secrets не export |
| Коллективные/Help доказательства | Минимальная запись учтённого исхода | Outcome/count/protocol/tag; без people/text/time в summary. **Срок controls не утверждён** | Это linkable pseudonyms, не public anonymous analytics. Пока relevant backups могут воскресить данные, holds/denials автоматически не prune. Public collective/ISTINA runtime в эту базу не включён |
| Technical logs | Обнаружение сбоев, без содержимого диалогов | Ops result7d; timestamps/counts/reason codes, private access. Host/proxy/CI предложить отдельно после inventory | Ops TTL не удаляет privacy journal. Existing bot logs требуют отдельного canary/redaction review; raw error strings в новых ops logs не сохраняются |
| Application backups | Offline recovery после сбоев | Managed backup7d, retired digest/holds дольше; физическое unlink только проверенного файла | Scheduler retire+unlink eligible managed files; unknown/offsite snapshots не удалены автоматически. Создание backup не должно бесконтрольно продлевать доступ к старому plaintext |
| Provider/client copies | Доставка и inference, внешние processors | UNKNOWN до provider-specific review/согласования | No false promise: локальное delete не является remote erase; никакие реальные запросы/доступы этим PR не выполнялись |

## Operator schedule proposal

Scheduler default **OFF**. На isolated stand: interval1h, maximum lateness alert2h от last SUCCESS (до первого успеха — от initial due), retries60s/120s/240s, maximum3 attempts, затем operator reschedule. Общие bounds и точная separate schema — `operator-schema.json`. `reschedule` не сбрасывает дату последнего успеха/overdue. SUCCESS определяется только после post-commit confirmation, не по отсутствию ошибки HTTP.

Очистка допускается только при original journal/witness + activated context/lifecycle. Отказ backup/control/result store, неизвестный snapshot, legacy blockers или CLOCK_ROLLBACK означает degraded/stop; не отключать protection ради соблюдения nominal TTL. Alert публикуется в structured stdout/status и private result store; **доставка on-call не настроена**. Предлагаемый runtime loop bounded, supervision/auto-restart/host SLA требуют отдельного испытания.

При healthy single host с проверенной monotonic-enough wall clock и запущенным polling loop задача запускается не ранее due и при первой возможности polling. Это условная периодичность, не доказанная real-host deletion SLA. Состояние «не запускалось», «неизвестно» и «успешно выполнено локально» не смешиваются.

## Обязательное утверждение до реальных данных

Для каждой строки владелец/ответственный за privacy должен указать: accountable operator и processors, назначение/основание, scope согласия/отзыва, срок/исключения, доступ/export/delete, конфликт с recovery/economic доказательствами, внешние копии/территории, критерий retirement и подтверждающее evidence. Это перечень для решения владельца, не правовая консультация и не объявление законности обработки.

Необходим независимый re-identification review: journal key и старые backups нельзя отдавать тому же кругу лиц, которому доступен summary, без оценки корреляции. Retention minimal proof не означает бесконечное хранение «анонимной» истории. Когда требуется pruning controls, сначала нужно доказать retirement **всех** relevant rollback copies; процедура такого глобального pruning здесь не реализована.
