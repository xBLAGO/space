# Risk-oriented integration and pilot proposal

**No merge or deployment authorized.** Base/main observed SHA must be re-read before any future action; captured snapshot is not evergreen approval. Source candidate `395ecf10f9187663fddb64795597e7efe0f014fa` extends exact#25 `0772e5bc5919aefac2c209f4e40fe5ea790d6792` with tests/evidence, not another independent runtime.

| Step / exact starting point | Dependencies / change | Required tests / GO | Stop / rollback |
|---|---|---|---|
|0. Before any main change: main3c218dc5757879c26b3aec520af3636b2d702715|Owner reviews legacy push-deploy workflow, queues and protected release authority. No settings changed here|Explicit release governance, no unreviewed run after merge. UNKNOWN until owner evidence|No merge, no deploy; retain old runtime/data|
|1. Preserve Integration01 source d7f619a1e6a7f15564facb5e8e84b184a3f24658 / PR19 docs837b2bfd5d3db4842530d30ccf0f417bfe718737|Distinguish code and four later docs files; identity05.4 +06.1 pinned in manifest|Ancestry + file hashes + negative financial route/unsafe SQLite/startup guards|Reject mismatched tree, never select newest documentation HEAD as new source by inference|
|2. Aggregate existing stack at0772e5bc5919aefac2c209f4e40fe5ea790d6792|Help#20→#22→G09#23→lifecycle#24→ops#25. Already integrated; no repeated cherry-pick|464 inherited tests plus new unified HTTP/Bot/Mini/browser and recovery. No experiments|Keep this isolated branch; no destructive downmigration or denial rollback|
|3. Reviewed candidate395ecf10f9187663fddb64795597e7efe0f014fa|Reproducible single DB journey38; browser17/mobile14; captured PR/main audit|472 full tests; actual Docker image identity/SQLite and offline main upgrade evidence. GO only lab|Pinned binary/source + retained controls; app rollback requires replay, not reset|
|4. Fix bounded MVP gaps on this same integration line|Owner chooses guided Help panel or conversational read/preview bridge. Unify Help/legacy needs view; no LLM mutation authority|Actor/audience/CSRF, exact text/version review, refusal/change mind, accessible status, no duplicates, no disclosure|Feature-off/restore previous compatible binary; preserve holds and all committed user decisions|
|5. Qualify operator release, still before real data|Ledger-aware migration dispatcher, complete legacy inventory, approved retention, external copies/logs/provider route, independent witness/custody and alerting|Main-copy upgrade/repeat/crash/old-backup rehearsal on target filesystem; G02/03/04/08/10 evidence|Fail closed on unknown schema/frontier/ownership; no automatic init of original controls|
|6. Candidate for real-user approval: exact new SHA TBD|Reviewed real-user mode/manifest replacing intentional synthetic-only restriction; physical clients/live provider gates; corrected public site|All required gates closed with scoped operational evidence and owner approval. CI alone insufficient|Before opening: stop plan; after data exists: preserve current controls, disable ingress, reviewed forward recovery|

## Как объединять PR

Предпочтительно рассматривать текущую aggregate integration branch как единый кандидат с сохранённой provenance. Если владельцу нужны отдельные merges, сначала review/deployment governance, затем#19→#20→#22→#23→#24→#25 с повторной ancestry/tree проверкой после каждого base update; Integration02 тесты должны идти на **итоговом** дереве. Retarget/merge/cherry-pick здесь не выполнялись.

#19 latest docs HEAD не является ancestor Help runtime; text merge чистый, но downstream base обновление меняет commit/tree identity. Не переписывать старые evidence как будто они относятся к новой сборке. #18/#21 docs могут быть отдельно reviewed/cherry-picked, без заявления runtime coverage. #11–17 остаются вне MVP: отсутствие textual conflict не отменяет нового schema/authority/publication scope. Никаких wallets, tokens, mainnet, federation, Genesis или Sovereign Identity для закрытого Help pilot.

## Минимальный пилот — предложение, не разрешение

После закрытия gates:3–5 добровольных совершеннолетних участников из известной группы,1 оператор и отдельно назначенный custodian. Предложенные задачи — настройка велосипеда, объяснение инструмента или помощь с учебным материалом; без денег, адресов дома, здоровья, личных документов, опасных работ и внешних обязательств. Участники сами задают private audience; каждый action требует отдельного preview/confirmation. Никаких real users до approval.

Первый сценарий допускает guided Help panel, если владелец явно принимает это ограничение. Если критерий — полностью естественная беседа со статусом и уточнениями, текущий candidate не соответствует: сначала scoped read/preview capability, UX и live model review. Matching между людьми пока manual/by known audience codes; автоматический matchmaking не заявлен.

Admission: доказанные auth/client/recovery/privacy gates; понятные данные/сроки/удаление; уведомление, что agent ошибается, memory исправляема, Help outcome — взаимное подтверждение, не независимое доказательство услуги. Moderator/custodian не получают полномочий молча подтверждать действия участника.

Stop немедленно при audience/IDOR leakage, resurrected deleted fact/action, stale generation commit, missing witness/journal, unexplained identity merge, ложном completion/publication claim, недоступном retention/backup control или provider/logging breach. Отключить ingress/публикации, сохранить минимальные incident evidence, не очищать journal/holds, не возвращать старый SQL snapshot без denials. Incident replay проводится сначала synthetic.

Success criteria: люди понимают кто увидит текст, могут отказаться/изменить решение, пройти offer/accept/mutual-confirm или dispute, найти текущий статус и удалить личные данные; оператор выполняет остановку/восстановление/retention без восстановления отозванного. Metrics: explicit completed/disputed/withdrawn states и usability observations, не token balances/рейтинги личности/измерение счастья.

## Решения владельца BLAGO

1. Guided-panel MVP или обязательный chat-only companion; что считать минимальным качеством реального agent/provider.
2. Account possession vs дополнительные внеприложенческие меры допуска; individual invite lifecycle и human cohort size.
3. Operators, independent custodian, target hosting/storage, budgets/alerts, provider processor/region and external-copy controls.
4. Retention policy, legacy ownership/economic records, metadata linkage и безопасный migration/recovery порядок; не поручать юридическое утверждение коду.
5. Scope споров/отзыва, личная vs коллективная статистика и non-financial product wording.
6. Website source/owner, удаление или явное отделение token/investment/NFT claims и ссылка на будущую **одобренную** app-сборку.
7. Явное согласование точного release SHA/image и закрытие G11. Никакого автоматического production GO из Draft PR или зелёного CI.
