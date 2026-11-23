# Living Deeds 02 — scenario matrix

Все данные синтетические. Первые 18 строк подтверждены exact runtime local/CI/Docker; дополнительные 2 — отдельным локальным прогоном. Финальный combined20/full813 следует проверять на exact HEAD PR #43 в CI.

| Сценарий | Ожидание | Факт |
|---|---|---|
| test_additive_agent_schema_repeated_migration_and_frozen_predecessors | PASS | PASS |
| test_crash_after_commit_lost_ack_reuses_receipt | PASS | PASS |
| test_crash_after_journal_before_sql_commit_no_success | PASS | PASS |
| test_decline_blocks_late_ack_and_neutral_requester_state | PASS | PASS |
| test_disk_failure_before_journal_returns_no_ack | PASS | PASS |
| test_erasure_cascades_queue_no_hash_reconstruction | PASS | PASS |
| test_explicit_preview_minimal_payload_and_no_requester_leak | PASS | PASS |
| test_flags_default_off_and_external_adapter_absent | PASS | PASS |
| test_generation_fencing_blocks_memory_delete_during_agent_response | PASS | PASS |
| test_memory_correction_deletion_and_restart_no_stale_candidate | PASS | PASS |
| test_old_backup_restores_no_permissions_and_unknown_recovery | PASS | PASS |
| test_owner_memory_match_is_private_read_only_and_model_independent | PASS | PASS |
| test_receipt_replay_restart_lost_ack_and_single_consumption | PASS | PASS |
| test_recipient_deletion_retires_delivery_without_exposing_note | PASS | PASS |
| test_retention_of_unanswered_invitation_preserves_suppression | PASS | PASS |
| test_revocation_expiry_cancel_and_permission_withdrawal_fail_closed | PASS | PASS |
| test_silence_creates_private_no_contact_hold | PASS | PASS |
| test_two_processes_pull_one_durable_receipt | PASS | PASS |
| test_private_match_delivery_then_canonical_help_completion | PASS | PASS |
| test_current_backup_preserves_delivery_memory_and_owner_isolation | PASS | PASS |

Изоляция: только владелец читает свои зацепки, recipient получает минимальный task/referrer; B view не меняется при отказе.
Delivery: нет ACK до durable commit; два процесса возвращают одну квитанцию.
Recovery: до commit нет успеха; journal-ahead или старый backup дают UNKNOWN_RECOVERY; актуальный полный backup сохраняет ACL/receipt.

NOT EXECUTED: live LLM, physical Android/iPhone, real Telegram invitations, independent operators. No production/pilot gate promotion.
