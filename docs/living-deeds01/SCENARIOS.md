# Матрица приёмки

Источник результатов — `tools.living_deeds_lab`: каждый test ID имеет expected
PASS и фактический PASS/FAIL/ERROR/NOT EXECUTED. Наличие теста не считается PASS
до завершения прогона; итоговый JSON публикуется отдельно.

| ID | Требование | Исполняемое доказательство |
|---|---|---|
| LD01 | Need → offer → explicit binding → reserve → two confirmations | `test_positive_canonical_cycle_and_no_double_count` |
| LD02 | Дневник A, два согласия, B не видит Лену до её offer | `test_private_note_two_consents_then_same_help_lifecycle` |
| LD03 | Отказ нейтрален для B; повтор подавлен | `test_decline_is_private_neutral_and_suppresses_repeat` |
| LD04 | Чужая сессия не подтверждает приглашение | `test_foreign_user_cannot_consent_or_read_private_note` |
| LD05 | Replay не создаёт второй event/эффект | `test_replay_receipt_and_conflicting_key`, `test_replay_after_restart_returns_original_receipt_without_new_events` |
| LD06 | Два самостоятельных процесса спорят за один slot | `test_concurrent_independent_processes_cannot_double_reserve` |
| LD07 | Прямой Help accept не обходит ресурс | `test_direct_help_accept_cannot_bypass_resource_reservation` |
| LD08 | Спор меняет current view; correction сохраняет предыдущую версию | `test_dispute_and_correction_preserve_event_history` |
| LD09 | Актуальный backup / restart даёт тот же view | `test_current_backup_and_restart_preserve_verified_closed_deed` |
| LD10 | Index rebuild из исходного журнала даёт прежние headers и view | `test_full_uel_index_rebuild_keeps_identical_verified_projection` |
| LD11 | Старый backup без положительного source не выдаёт права | `test_old_backup_never_reconstructs_missing_positive_consent` |
| LD12 | Диск/SQL rollback не оставляет новый NOTE | `test_rollback_before_uel_does_not_leave_note_or_authority` |
| LD13 | Crash после journal до SQL не превращается в grant | `test_crash_after_journal_before_sql_never_replays_a_grant` |
| LD14 | Crash после commit / lost ACK: replay возвращает прежнюю receipt | `test_crash_after_commit_replay_has_one_source_event` |
| LD15 | Отзыв ресурса закрывает prepared reserve | `test_revoked_resource_blocks_prepared_reservation_and_confirmations` |
| LD16 | Изменение Need версии отменяет forwarding permission | `test_need_revision_invalidates_invitation_authority` |
| LD17 | Отсутствие checkpoint закрывает операции | `test_missing_checkpoint_evidence_fails_closed` |
| LD18 | Context epoch, session, expiry, confirmation и seal обязательны | `test_correction_cannot_reuse_stale_privacy_context_or_lost_session`, `test_bad_confirmation_expired_challenge_and_substitution_do_not_write` |
| LD19 | Удаление стирает private payload и previews; backup не возвращает их | `test_erased_private_payload_does_not_return_from_old_backup`, `test_completed_previews_do_not_retain_private_text_after_erasure`, `test_account_deletion_purges_owned_payloads_and_commands` |
| LD20 | Retention read-denial и явная cleanup | `test_expiry_blocks_invitation_then_explicit_cleanup_erases_payload` |
| LD21 | Миграция no-op / fault rollback; OFF не выключает installed guards | `test_atomic_migration_failure_preserves_original_headers_and_ledgers`, `test_migration_idempotent_and_flag_off_still_checks_data` |
| LD22 | Headers / memory / export не раскрывают NOTE или чужой UID | `test_private_note_and_headers_are_not_a_public_catalog_or_llm_memory`, `test_exports_redact_foreign_internal_identity_references` |
| LD23 | Отзыв завершённого Help не освобождает уже использованный interval | `test_withdrawal_of_completed_help_does_not_free_consumed_slot` |

## Не выполнено и не заявляется

| Направление | Статус / причина |
|---|---|
| Реальные добровольцы | NOT EXECUTED; нет допуска |
| Физические Android/iPhone | NOT EXECUTED |
| Живой LLM / agent tool-calling | NOT EXECUTED; нет нового LLM data scope |
| Telegram/email доставка приглашения | NOT EXECUTED; только локальный inbox |
| Независимые операторы / federation | NOT EXECUTED; процессы на одном host не независимы |
| Полное восстановление потерянных private payloads из UEL hashes | НЕВОЗМОЖНО данным контрактом; UNKNOWN_RECOVERY |
| Разрешение Help после DISPUTED | NOT IMPLEMENTED; отдельное изменение канонического Help |
| Безопасное снятие do-not-disturb новым разрешением | NOT IMPLEMENTED; старый deny не снимается автоматически |
| Автоматическая production retention cleanup | NOT EXECUTED; explicit lab cleanup only |
| Внутренний источник физической достоверности навыка/исполнения | UNKNOWN; SELF_DECLARED / MUTUAL_SESSION_CONFIRMATION не independent verification |

Exact-text preview проверяет
`test_invitation_preview_binds_exact_task_without_exposing_private_note`;
retained enrollment, а не просто наличие config, —
`test_configuration_without_retained_enrollment_is_not_authority`.
Три теста `test_living_contracts` дополнительно проверяют исходные source pins,
обнаружение произвольного lifecycle diff и удаления security hook.

* `test_erasing_a_reserved_resource_does_not_bypass_help_confirmation_guard`:
  erasure и old-backup restore сохраняют запрет Help confirm при утраченном
  ресурсе, а не превращают bound offer в unrestricted generic offer.
* `test_own_skill_hour_is_a_consented_canonical_capability_not_a_third_party_note`:
  собственная добровольная capability с unit=hour проходит тот же цикл без
  создания нового реестра навыков и без независимой верификации способности.
