# Матрица проверки

| Область | Ожидаемый результат | Автоматическое доказательство |
|---|---|---|
| Точная policy version и fixed electorate | Подмена/понижение security floors запрещены | PureGovernanceTests: политика, пороги, electorate |
| Quorum расходов | Один principal/signer не заменяет двух | Два независимых согласования; duplicate vote/proxy |
| Делегирование | Scoped proposal/amount/TTL, no GOV, no chains | Положительный путь, revoke, expiry, повтор principal |
| Supermajority и delay | Активация до порога/срока отклоняется | PolicyProposed/Voted/Activated; vote revoke resets readiness |
| Отзыв ключа | Одного governor недостаточно, четыре точные подписи действуют | AuthorityRevoked positive/negative/domain binding |
| Новая policy | Старые незавершённые grants/reserves закрыты | Новый hash, version+1, освобождение без нового расхода |
| Exit / account deletion | Фонд и не затронутые права остаются | Integration: MemberExited, privacy challenge, deny old erase |
| Старый backup | Нет возвращённой UID-binding или positive authority | Restore в новый файл без исходного WAL; frozen hold, данные остальных сохранены |
| Свежий backup | Проверенное состояние сохраняется | Fresh post-exit restore |
| Concurrent votes/reserves/revocation | Один CAS порядок, без потери/удвоения | 2 processes, signed commands, retry/new version |
| Crash before journal | Source rollback | Exitcode91, состояние не меняется |
| Crash after journal before commit | Frozen hold, без стирания остальных | Exitcode92, source сохраняется, операции закрыты |
| Crash after commit | Идемпотентная исходная квитанция | Exitcode93, повтор без нового эффекта |
| Migration | Additive, repeat-safe, rollback-safe | Integrity/FK, сравнение старых UEL/ledgers, DDL fault |
| Отдельный verifier | Совпадение положительных/отрицательных переходов | Два отдельных алгоритма, state comparison, subprocess без runtime PYTHONPATH |
| UEL proof injection | Подмена события, checkpoint, leaf, version или binding отклоняется | Mutated bundle tests, external pins required |
| Совместимость | Прежние 725 тестов и 72 UEL vectors без изменения golden results | Full regression/CI |
| 100 участников | Согласованный баланс, policy v2, безопасный exit | tools.governance_lab, Docker --network none |

Реальные люди, физические устройства, независимые операторы, внешняя оценка governance fairness и независимый security audit — NOT EXECUTED. Второй verifier — отдельная реализация того же автора, а не independent audit. V1 signature replay сохраняет прежний reference verifier. Условия HMAC/node binding и свежести внешнего checkpoint обязательны.
