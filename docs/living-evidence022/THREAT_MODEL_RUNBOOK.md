# Угрозы, ограничения и восстановление

| Угроза | Контроль | Оставшееся ограничение |
|---|---|---|
| Подмена роли query/header/cookie | Реальная IdentityRuntime session, HTTP negative test | Это локальная синтетическая аутентификация, не independent operator |
| Разные начальные состояния трёх веток | Один seed, byte-identical SQLite clones, DB hashes | Нет доказательства реального браузерного запуска; хеш не доказывает честность автора |
| Текст пришёл C фоном до отзыва | Сканировать все network_response; earliest event обязательный; revoke leakage BLOCKED | Полный collector ещё не реализован |
| Утечка заметки скрытым DOM | Проверять visible/full_dom/network_response, marker policies | Только известные маркеры; непромаркированные персональные данные требуют review |
| Утечка заметки в evidence B | Только marker-only после pre-mask scan; никакой полной заметки | PNG/DOM masking adapter NOT IMPLEMENTED, ветки BLOCKED |
| Косвенный oracle отказа | Точное сравнение author visible+response state; refusal NOT_OBSERVABLE | Timing/network side channels не доказаны |
| Фальсификация evidence | Хеши/полнота/schema/negative tests; отдельная независимая сверка | Автор bundle может сфабриковать все файлы. Нет аппаратной аттестации или подписанного независимого capture |
| Compression/path attack verifier | File bound, PNG bounds/CRC, suffix allowlist, path/symlink rejection | Python process sandbox и доверенная локальная копия verifier рекомендуются |
| Спорный Help доступ | Canonical _row сохранён, BLOCKED без обхода | Нужен отдельно согласованный UI переход |

Verifier не доказывает наличие баннера в пикселях по OCR: проверяет непустой валидный PNG, DOM banner и заявленный результат проверки снимка. Человеческая сверка обязательна. Аналогично source/files hashes — заявленное происхождение; для независимой сверки сравнить их с Git exact SHA отдельно. SHA БД описывает исходное состояние, не переносится сама БД.

Только localhost, новые синтетические каталоги. Подготовщик не восстанавливает production и не применяет новые миграции. Store, journal, witness, checkpoint и ключи остаются приватными. Не переносить приватный каталог в artifacts. Отказ/потеря приватной payload не восстанавливаются из UEL hash; существующий UNKNOWN_RECOVERY сохраняется.

При сбое подготовки не продолжать в полузаполненной ветке и не перезаписывать её: сохранить диагностический статус, выбрать два новых имени каталога и заново подготовить чистый seed. Это новый прогон с новым manifest; старые артефакты не смешивать. Для decline будущего E2E наоборот требуется прежняя БД после restart, иначе suppression не проверяется.

Rollback разработки: остановить только собственный локальный сервер/custodian, оставить существующие #43/#44 ветки неизменными, вернуться к exact #44 в новой рабочей копии. Никакого git reset существующих веток, production rollback или миграций. Никаких внешних приглашений, реальных участников, payments или HF операций.

Независимый проверяющий получает только bundle и stdlib verifier/contract. Команда `python -m tools.living_bundle_verify /path/to/bundle` не требует BLAGO, SQLite или сети. Верификатор возвращает неполноту вместо успешной проверки при потере доказательств. DeepSeek verdict записывается только после его собственной сверки; сейчас NOT PROVIDED.
