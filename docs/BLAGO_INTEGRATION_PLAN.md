# BLAGO Integration Plan — Integration Audit 01

**Draft, plan only.** Никакие ветки не объединялись, staging/production не
разворачивались. Это последовательность будущих отдельных reviewed PR и
изолированных проверок. Точный snapshot и зависимости:
[dependency map](BLAGO_FOUNDATION_DEPENDENCY_MAP.md).

## 1. Стратегия

Минимальный runtime-кандидат — exact #10
`d11a2a555efe57e6818450d534655020bf47b9ca` с обязательными исправлениями/операционными
gates. Он уже содержит ancestry 05.4 и исправление UI acceptance #9. #12
`b79cb603a58adbab1e8a5313a06393a2feb5532f` используется как isolated compatibility
superset, но криптографический core не является условием запуска обычного аккаунта.
Не складывать diff каждого PR относительно main и не cherry-pick все cumulative
heads. Основной review path линейный; experimental siblings остаются изолированными.

Выделить временную integration/staging ветку только в следующем явно согласованном
задании. Здесь нет ни такого merge, ни фиктивного SHA итоговой сборки. Каждый будущий
PR фиксирует actual base, source commit set, result SHA и schema/image manifest;
изменение любого SHA требует повторной проверки затронутых gates.

## 2. Последовательность с конкретными исходными SHA

| Шаг / риск | Exact input base и source target | Зависимости / необходимые изменения | Обязательные проверки | GO / NO-GO | Безопасный откат |
|---|---|---|---|---|---|
| I0 release guard, P0 | Base main `3c218dc5757879c26b3aec520af3636b2d702715` | Отделить merge/code acceptance от HF deploy-on-main-push; staged approval, immutable image, secrets/environment ownership review | Workflow event tests, PR не deploy, release требует approval; rollback rehearsed в synthetic окружении | G00/G01, иначе дальнейшее принятие к rollout запрещено | Вернуть workflow только до любых runtime writes; не запускать старый auto-deploy как rollback |
| I1 базовый Web и память, P0 | Base main `3c218dc5757879c26b3aec520af3636b2d702715`; source path #2 `4bc48f2a6b23f198ca88a022e4bad0f03f709e8a` →03 `8e086ec24bc9f3fe0d11e6dd12473f867ede3b44` →04 `25dcb0fae420b27916629b1686f07f6f96e5c446` →#3 `af94ac93dde649747b1a89104000f92f75ecb7fc` | I0 guard patch manifest; один ID/store, Web/PWA/Mini App, economy simulation и semantic correction. Review всех промежуточных commits, не пропускать 03/04 без PR | Main→candidate synthetic fixture; IDs/profiles/history/update dedupe; memory version/correction/veto/delete/sensitive filtering; provider errors; CSRF/isolation | Изолированный integration GO при pass; реальных людей ещё нет | До writes можно отбросить disposable DB. После writes только image/schema compatible rollback или forward-fix; не запускать main writer на новой схеме |
| I2 secure auth/privacy, P0 | Base #3 `af94ac93dde649747b1a89104000f92f75ecb7fc`; target #7 `8634e94a6dddd2b8cfff415b112f6b895eb0f4ae` | #4 `8820d7852f0db6c4fc5101f9e281211ba7b6b074` →#5 `8b63b8795490a11ad2b5a709b9a258033b7d6381` →#6 `224ad9d2a1775a8372354af35d542f433cb8bdde`; explicit migrations, journal/witness/custody; no auto migration | Schema checksum/repeat/crash rollback, invalid FK, login/link/replay races, session/passkey revoke, account merge preservation, TTL, stale backups, lost journal/key/server, integrity/FK checks | G02–G05; synthetic pass не закрывает независимое хранение | Остановить запись. Restore app copy с **актуальными** control/witness; replay denials, invalidate sessions/challenges. Journal/keys/witness не откатывать |
| I3 natural UI/dialogue, P0 | Base #7 `8634e94a6dddd2b8cfff415b112f6b895eb0f4ae`; #9 `37be0437578125503f0a851c6ce9a86222937cdc` → target #10 `d11a2a555efe57e6818450d534655020bf47b9ca` | I2 + UI selector correction из #10 обязательна; сохранить atomic history и refusal/consent boundary. Закрытый #8 не применять | Full regression + Docker + Chrome virtual; multi-step natural dialogue/restart, 503/empty/length/timeout, memory isolation, no economic side effects, cost/latency counts | Зелёный exact candidate, G06 live acceptance OPEN до фактического прогона | Сохранить совместимый store/control; отключить onboarding/новые диалоги, compatible previous image либо forward-fix. Не удалять history ради повтора теста |
| I4 минимальная взаимопомощь, P0 | Design input base #10 `d11a2a555efe57e6818450d534655020bf47b9ca` | I1–I3; новый explicit nonfinancial lifecycle/discovery, scoped audience/withdrawal/accept/cancel/confirm. Existing payment simulation не является реализацией. Согласовать retention/delete для новых FK и contribution facts | 3 principals + IDOR, private vs community, exact-text approval, отказ/смена мнения, concurrent accept, cancel/replay, restart/crash и restored withdrawal; no financial/tool actions | G04/G07/G08 PASS; отсутствующий код = NO-GO | Feature OFF + immutable denied/retracted intentions сохраняются; нельзя откатывать к образу, который игнорирует новые FK/tombstones |
| I5 эксплуатационная квалификация, P0 | Immutable source baseline #10 `d11a2a555efe57e6818450d534655020bf47b9ca`; review patches I0–I4 отдельным pinned manifest | Выбранные persistent FS, independent custody, TLS/origin/RP/bot ownership, qualified SQLite/image; global queue/worker topology decision; actual device matrix | Actual image/library attestation, process/disk fault, restore на другой host, stale token/links, concurrent participants и cross-channel ordering, live provider quality/availability, support drill | G02–G10 PASS на одной сборке; UNKNOWN блокирует | Read-only/stop intake, сохранить evidence и control; fail-closed при потере anchors. Никакого переинициализирования для обхода gate |
| I6 release rehearsal и решение, P0 | Reference source #10 `d11a2a555efe57e6818450d534655020bf47b9ca`; actual result SHA I0–I5 пока **не существует** | Не подставлять d11 как якобы протестированный patched release. Перед стартом freeze exact result SHA, image digest, migration hashes; отдельное решение о rollout | Полный regression/recovery/acceptance на единой synthetic baseline, ≥3 consenting-user staging cases после privacy review, independent sign-off | Все G00–G11, иначе NO-GO. Merge/deploy требуют отдельного разрешения | Проверенный план stop/recover/forward-fix; не восстановление старого snapshot поверх новых отзывов |

Строки I4–I6 называют конкретный **исходный** base SHA для проектирования. SHA
несуществующих исправлений не может быть честно указан сейчас. Перед выполнением
каждый шаг обязан записать полученный result SHA предыдущего шага и фактический
base следующего PR. Без этого последовательность не считается executable release
manifest и запуск I5/I6 запрещён. Это снимает риск «проверили один commit, выпустили другой».

## 3. Порядок offline миграций и совместимость

На disposable копии, после онлайн-consistent backup и с offline writers:

1. Инициализированный legacy SQLiteBlagoStore; WebApp добавляет существующую Web/economy
   схему. Проверить counts/IDs/FK до и после; `executescript` legacy startup не
   является versioned transactional migration всего приложения.
2. `migrate_identity_store` (05.1) — не копировать channel_links в дублирующий identity_links.
3. `migrate_identity_runtime` (05.2) — расширяет web_sessions; legacy session не
   становится fresh authentication. Проверить session recovery/quarantine policy.
4. Новый **разрешённый для теста** journal + `migrate_privacy` (05.3); для production
   нельзя заменить уже существующий original journal новым ключом. Связать custody.
5. Reviewed prospective lineage baseline + `migrate_pilot` (05.4), independent witness.
   Сам факт новой baseline не доказывает содержание прежних удалений. Не угадывать
   lineage по совпадению value, username или биографических сведений.
6. Повторить migrations: unchanged; integrity_check=ok, foreign_key_check=[];
   нарушенные/неизвестные версии должны отказать без partial state.
7. Включить требуемые secure/privacy/pilot guards только с проверенным manifest;
   отсутствие миграции или control state должно закрывать доступ. Для реальных
   людей отсутствие guard — NO-GO, а не удобный legacy fallback.

07.1/Genesis migrations в минимальном плане отсутствуют. Если такие таблицы уже
есть в копии, учитывать их explicit delete/export/merge guards; не удалять их
для обхода ограничений. Все rehearsals сохраняют старые blago_user_id. Unknown
ownership tables блокируют merge/delete, пока не написан и проверен adapter.

## 4. Отдельные ветви, не входящие в MVP

| Исследовательская ветвь | Exact base / head | Требуется до будущей интеграции |
|---|---|---|
| 07 / 07.1 | #10 `d11a2a555efe57e6818450d534655020bf47b9ca` →#11 `2d6d65913d9091e7d42966532162a0d1de9d0c76` →#12 `b79cb603a58adbab1e8a5313a06393a2feb5532f` | Отдельный owner/bootstrap/custody review, recovery limits, no session→controller promotion; flags OFF |
| 07.2 recovery | #12 `b79cb603a58adbab1e8a5313a06393a2feb5532f` →#13 `d96ade9375f3499ad93095b2307abd750f0a8578` | Independent atomic authority infrastructure; activation сейчас fail-closed |
| Genesis/public memory | #12 `b79cb603a58adbab1e8a5313a06393a2feb5532f` →#14 `483475fd237ba3400881699c407be5b91d53dcdd` | Public disclosure/withdrawal, safe deletion/retention, privacy restore hooks; отдельный review siblings #13/#14 |
| Federation design/lab | Main `3c218dc5757879c26b3aec520af3636b2d702715` →#15 `03cdbf82ec45ee10ec45567ba60f3bfb06fbca5f` →#16 `0a6acd9178de8c1aaac8f3af729d896efd4eb98a` →#17 `44a5d52a4ac8022478a247f4e1bbd2eaea551142` | Не переносить filesystem lab в production. Independent operator/key/transport/retention proof, scoped app adapter, explicit mandate/current revoke и real fault-domain evidence |

08.2 journal не заменяет 05.4 privacy recovery. Receipt доставки заметки не
подтверждает fulfillment человека и не разрешает следующий economic action.
Future federation adapter начинается с добровольно опубликованного scoped объекта,
а не с репликации всей memory SQLite. Для выхода/переноса отдельно проектируются
key history, revocations, privacy scope и отсутствие server/session secrets.

## 5. Откат без нарушения приватности

- До любого real write: предыдущий verified image + disposable data можно вернуть
  после подтверждения schema compatibility; это не разрешение удалить реальные данные.
- После нового согласия/отзыва/удаления: старый application snapshot допустим только
  с текущим независимо удержанным journal/frontier и replay. Old backup никогда
  не должен вновь разрешить старую сессию, passkey, memory или withdrawal.
- Journal, retained checkpoint и escrow не входят в rollback artifact приложения.
  Их отсутствие закрывает доступ. Нельзя уменьшать seq/head или выключать guard.
- Account merge нельзя безопасно отменить обычным restore одной таблицы; нужен
  полный проверенный reconciliation с сохранением identity aliases/economy history.
- Не обещать zero-data-loss rollback без измеренных RPO/RTO и актуальных внешних
  evidence. При сомнении — stop writes, preserve artifacts, forward-fix.

## 6. Рекомендуемые следующие PR

1. Release approval/image/migration manifest без deployment исполнения.
2. Нефинансовая mutual-help domain модель и API, без изменения real payment rules;
   одновременно deletion/retention для новых ownership FK и contribution records.
3. Per-user/cross-channel scheduling, bounded queue и cancellation; regression старых
   atomic histories, replay и privacy denies.
4. Квалификация target storage/custody/recovery и device/provider acceptance evidence
   для exact patched candidate. Любой live environment доступ — отдельное разрешение.
5. Только после выполнения [gates](BLAGO_MVP_RELEASE_GATES.md) — PR с решением о
   limited pilot. Независимая федерация остаётся самостоятельным проектом.
