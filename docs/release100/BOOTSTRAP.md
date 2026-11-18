# Безопасное первоначальное включение — действия владельца

Это инструкция, не свидетельство выполненных изменений. Агент не изменял settings,
secrets, production или main. До выполнения шагов merge остаётся NO-GO.

## Перед первым merge

1. Зафиксировать current main/source PR head, последнюю известную HF revision,
   текущие running/queued workflows и ответственного за stop. Не запускать бота,
   HF CLI upload или автоматическую миграцию для проверки.
2. **Отключить старый Deploy Hugging Face Space workflow** в Actions по его ID383106331
   (файл huggingface-space.yml; исторический main run38441506701 success). Отменить и проверить отсутствие всех уже queued/
   in-progress deployment runs, включая старые SHA. Отключение будущих запусков
   само по себе не отменяет выполняющийся upload. При неоднозначности — STOP.
3. Инвентаризировать repository/org-level production write credentials: старые
   HF write token, Telegram/webhook credentials и аналоги. Убрать доступ к ним из
   недоверенных branch/PR jobs. Агент не знает их наличия и не делает это сам.
   Если нужна credential rotation — отдельное согласованное действие владельца.
   Не менять inference/provider secrets HF Space ради этого guard.
4. Создать защищённый GitHub Environment `production-release`: selected **branch**
   main, без wildcard и без разрешённых tags; независимые required reviewers;
   Prevent self-review; запрет administrator bypass. Проверить реальные настройки
   и план GitHub, а не только их упоминание в YAML. Отдельный reviewer обязателен;
   один владелец не должен обходить self-review ради первого запуска.
5. Защитить main: запрет прямого push/force/delete, обязательный PR review,
   ограничение bypass, required Safe CI; review workflow/tools/release policy
   через выбранных владельцем CODEOWNERS. Не выдумывать несуществующие teams.
6. Защитить tags `release-preparations/**` от update/delete, определить trusted
   создателей и аудит. Проверить, что эта metadata namespace не запускает сторонний
   publisher/webhook. Не считать tag сам по себе разрешением deployment.
7. Оставить `BLAGO_RELEASE_PREPARATION_ENABLED` отсутствующим/false. Ни CI, ни этот
   guard не нуждаются в HF production credentials. Не выдавать их новому job.
8. Подтвердить steps2–7 доказательствами владельца, проверить exact final PR head
   и diff: только workflow/tool/metadata/tests/docs; runtime unchanged. Получить
   отдельное разрешение на merge. Автоматического merge нет.

## Сам переход

Старый workflow остаётся disabled. Выполнить только отдельно одобренный merge
защитного PR. По документации GitHub push обычно выбирает definition из связанного
commit; **не полагаться на это как единственную защиту первого merge**: уже
запущенные старые workflows и доступные repo secrets остаются угрозой.

После merge проверить main SHA, новый файл с единственным workflow_dispatch,
отсутствие upload/HF secrets и безопасный CI. Убедиться, что push/PR/docs merge
не создали deployment. Не запускать publisher — его в этом PR нет.

## После проверки

Workflow того же файла/ID можно включить только после review новой definition,
оставив preparation variable false. Никакой ручной dispatch в рамках этого задания
не выполнялся. Если позже отдельно разрешена подготовка metadata intent: сначала
проверить environment branch restrictions/reviewers и tags, затем явно разрешить
переменную и ручной запуск с exact SHA/artifact/config. Receipt не разрешает deploy.

## Проверка untrusted workflow

Отрицательные tests доказывают, что guard отвергает branch refs. Но злоумышленник
может переписать guard в своей ветке: YAML if не является security boundary.
Реальная граница — отсутствие repo/org production credentials, environment branch
policy и независимое approval, protected main и review изменений workflows.
До отдельного безвредного platform drill эти свойства UNKNOWN. Такой drill не
должен использовать HF token или совершать deployment. Fork PR CI read-only,
без pull_request_target и privileged workflow_run.

## Checklist статусов владельца

| Control | Сейчас | Требуемое доказательство |
|---|---|---|
| Старый workflow disabled / старые runs остановлены | NOT EXECUTED | ID/state и отсутствие active/queued deploy |
| Repository/org production credentials недоступны branches | UNKNOWN | Проверка владельца без публикации значений |
| Environment reviewers / no self-review / no bypass | UNKNOWN | Проверка settings и отдельного reviewer |
| Environment разрешает только branch main | UNKNOWN | Тип branch, exact main, отсутствие tag/wildcard rules |
| Main protection / CODEOWNERS / Safe CI required | UNKNOWN | Classic protection API403; owner подтверждает |
| Immutable preparation tag rules | NOT CONFIRMED | Видимый rulesets API вернул[] |
| Переменная подготовки выключена | UNKNOWN в GitHub; default code OFF | Проверка repo/environment variables |
| Нет downstream deployment hook на metadata tags | UNKNOWN | Инвентаризация GitHub/HF external integrations |

Ни одна отметка UNKNOWN не превращается в PASS только потому, что CI зелёный.

## Официальные основания

[GitHub workflow events](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows) описывает выбор definition и workflow_dispatch ref. [Environment policies](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments) различает branch/tag rules; [reviewing deployments](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/review-deployments) описывает запреты self-review и admin bypass. [Artifact REST API](https://docs.github.com/en/rest/actions/artifacts) предоставляет run identity, expiry, digest и временный download redirect. Эти возможности GitHub не доказывают, что они настроены в xBLAGO/space.
