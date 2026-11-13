---
pretty_name: BLAGO Research Evidence — UEL 0.3
language:
  - ru
  - en
tags:
  - blago
  - research
  - synthetic
  - privacy
  - event-ledger
---

# БЛАГО — проверяемый срез разработки

BLAGO разрабатывается как пространство добровольной взаимопомощи с персональным ИИ-агентом, управляемой памятью и проверяемой историей. Здесь закреплены исходники, инженерные отчёты и синтетические доказательства по состоянию на UEL 0.3.

**Статус — исследовательская предварительная публикация. GO для изолированных синтетических испытаний. Реальные участники — NO-GO до независимой проверки и закрытия release gates.**

## Зафиксированная версия

| Материал | Версия |
|---|---|
| Исходный код и документация | [06b3889afa906f1ac3af8eca6130f0e6fce6cec9](https://github.com/xBLAGO/space/tree/06b3889afa906f1ac3af8eca6130f0e6fce6cec9) |
| Проверенный runtime | `8ed6131e4617e0b47b157e9625ceb8845de22306` |
| Review | [Draft PR #32](https://github.com/xBLAGO/space/pull/32) |
| Финальный CI | [Два прогона по 638 тестов](https://github.com/xBLAGO/space/actions/runs/43483535901) |
| Репозиторий | [xBLAGO/space](https://github.com/xBLAGO/space) |

GitHub main, исследовательские ветки и работающие приложения имеют отдельные версии. Точные base/head SHA и состояния всех доступных PR приведены в `PR_SNAPSHOT.json` с временем наблюдения. Исходная версия этого архива зафиксирована отдельно от main.

## Что можно изучить

- [Единый MVP и интеграционные ограничения](https://github.com/xBLAGO/space/blob/06b3889afa906f1ac3af8eca6130f0e6fce6cec9/docs/BLAGO_FOUNDATION_INTEGRATION_02.md).
- [Естественный диалог и персональная память](https://github.com/xBLAGO/space/blob/06b3889afa906f1ac3af8eca6130f0e6fce6cec9/docs/BLAGO_FOUNDATION_06_1.md).
- [Нефинансовый цикл добровольной помощи](https://github.com/xBLAGO/space/blob/06b3889afa906f1ac3af8eca6130f0e6fce6cec9/docs/BLAGO_FOUNDATION_HELP_01.md).
- [Identity и защита доступа](https://github.com/xBLAGO/space/blob/06b3889afa906f1ac3af8eca6130f0e6fce6cec9/docs/BLAGO_FOUNDATION_05_2.md).
- [Приватность, retention и ограничения G04](https://github.com/xBLAGO/space/blob/06b3889afa906f1ac3af8eca6130f0e6fce6cec9/docs/BLAGO_FOUNDATION_PRIVACY_01_4.md).
- [Межпроцессная защита LLM-контекста G09](https://github.com/xBLAGO/space/blob/06b3889afa906f1ac3af8eca6130f0e6fce6cec9/docs/BLAGO_FOUNDATION_PRIVACY_01_2.md).
- [Коллективный калькулятор](https://github.com/xBLAGO/space/blob/06b3889afa906f1ac3af8eca6130f0e6fce6cec9/docs/BLAGO_COLLECTIVE_CALCULATOR_01.md).
- [Долговечный реестр активов и доступных ресурсов](https://github.com/xBLAGO/space/blob/06b3889afa906f1ac3af8eca6130f0e6fce6cec9/docs/BLAGO_FOUNDATION_09_1.md).
- [Universal Event Ledger 0.3](https://github.com/xBLAGO/space/blob/06b3889afa906f1ac3af8eca6130f0e6fce6cec9/docs/BLAGO_UEL_0_3.md), [модель угроз](https://github.com/xBLAGO/space/blob/06b3889afa906f1ac3af8eca6130f0e6fce6cec9/docs/uel03/THREAT_MODEL.md), [восстановление](https://github.com/xBLAGO/space/blob/06b3889afa906f1ac3af8eca6130f0e6fce6cec9/docs/uel03/RUNBOOK.md).

Foundation 07/08 и другие отдельные ветки перечислены в снимке PR. Их наличие в списке не означает включения в этот runtime. Даты и результаты ранних отчётов относятся к указанным в них версиям.

## Что доказано для UEL 0.3

| Проверка | Результат |
|---|---|
| Полная регрессия финального HEAD | 638/638 дважды в CI |
| Прежние offline-примеры UEL | 72/72, ожидаемые результаты сохранены |
| Docker | PASS, включая 35 прежних UEL и 38 новых проверок |
| Browser / WebAuthn / synthetic Mini App | PASS в виртуальной среде |
| Согласованный откат SQLite, journal и local witness | Обнаружен при сохранённом более новом custody frontier |
| Повреждение индекса, ремонт, удаление и старый backup | Проверены на синтетических данных |
| Независимый оператор и независимый аудит | NOT EXECUTED |
| Реальные пользователи и production federation | NO-GO |

Подписанный checkpoint подтверждает сохранённую последовательность. Достоверность заявлений и права на действия определяются исходными доменными механизмами. Лаборатория использует отдельные процессы на одном компьютере. Совместный откат всех контролей хранителя и доверенной конфигурации находится за пределами доказанной модели.

## Файлы и воспроизводимость

Архив содержит явно перечисленные документы UEL 0.3, синтетические evidence и test vectors, а также публичные метаданные PR. `manifest.json` фиксирует source SHA и состав файлов. `SHA256SUMS` позволяет проверить их целостность.

Полный исходный код закреплён ссылкой на commit GitHub. Для воспроизведения следует использовать этот commit, его lock-файл и инструкции исправленной SQLite из CI. Экспериментальные функции требуют явного включения в синтетической среде. Эксплуатационная ёмкость, внешние privacy controls и независимость операторов остаются открытыми вопросами.

Этот репозиторий HF предназначен для инженерных материалов и синтетических доказательств. Публикация кода и результатов даёт возможность независимой проверки и обсуждения. Прикладное обучение модели на этом наборе здесь не заявлено.

## Площадки публикации

- [GitHub — исследовательская предварительная публикация](https://github.com/xBLAGO/space/releases/tag/research-2026-11-uel03).
- [Hugging Face — архив доказательств](https://huggingface.co/datasets/xBLAGO/research-evidence).
- [Публичный сайт проекта](https://xblago.space/) имеет собственную историю и версию. Обновление ссылки на этот срез проходит отдельное review.

Исследовательский архив содержит информацию о разработке. Подключение реальных участников, запуск экономических операций и публикация приватных данных требуют отдельных решений.
