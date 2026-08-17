# Task 2 TDD report

## RED

Command: `python -m unittest tests.test_monitor_delivery -v`

```
ImportError: cannot import name 'build_digest' from 'monitor'
Ran 1 test in 0.002s
FAILED (errors=1)
```

The failure was expected: the required delivery interfaces did not yet exist.

Self-review follow-up RED:

```
test_commitment_authorization_must_be_non_empty_and_strategy_numbers_bounded ... FAIL
AssertionError: False is not true
Ran 1 test in 0.011s
FAILED (failures=1)
```

Connection retry follow-up RED:

```
test_connection_failure_uses_the_same_durable_retry_schedule ... FAIL
AssertionError: 0 != 1
Ran 1 test in 0.459s
FAILED (failures=1)
```

## GREEN

Command: `python -m unittest tests.test_monitor_delivery -v`

```
test_commitment_authorization_must_be_non_empty_and_strategy_numbers_bounded ... ok
test_commitment_language_requires_authorization ... ok
test_connection_failure_uses_the_same_durable_retry_schedule ... ok
test_digest_delivery_has_daily_subject_and_recipient_isolation ... ok
test_dispatch_reports_missing_smtp_env_without_secrets_or_state_loss ... ok
test_dispatch_uses_tls_login_and_never_resends_success ... ok
test_failure_retries_on_schedule_then_stops_after_five_attempts ... ok
test_invalid_publish_preserves_prior_outputs_and_never_queues ... ok
test_low_alert_waits_for_local_digest_and_handles_timezone_date_boundary ... ok
test_publish_writes_atomic_json_markdown_and_queues_high ... ok
test_validate_alert_draft_rejects_unknown_or_missing_evidence ... ok
test_validate_alert_draft_requires_strategy_fields_and_exact_questions ... ok

Ran 12 tests in 4.153s
OK
```

Full suite command: `python -m unittest tests.test_monitor_core tests.test_monitor_delivery -v`

```
Ran 44 tests in 12.554s
OK
```

`git diff --check` also passed. The self-review added range validation, non-empty authorization validation, and durable retry handling for SMTP connection failures.

## Review remediation RED

Command: `python -m unittest tests.test_monitor_delivery -v`

```
ImportError: cannot import name '_open_database' from 'monitor'
Ran 1 test in 0.002s
FAILED (errors=1)
```

The focused regression suite correctly failed because the facade no longer re-exported Task 1 underscore helpers. It also contained regression coverage for persisted candidate selection, evidence association, paired output replacement rollback, stored-recipient digest grouping, no-login SMTP, UTC due comparison, and all three CLI commands.

Follow-up RED after timestamp normalization was introduced:

```
test_publish_can_follow_timestamp_normalization_without_partial_transaction ... ERROR
sqlite3.OperationalError: cannot start a transaction within a transaction
Ran 1 test in 0.546s
FAILED (errors=1)
```

## Review remediation GREEN

Focused command: `python -m unittest tests.test_monitor_delivery -v`

```
Ran 20 tests in 10.665s
OK
```

Final full-suite command: `python -m unittest tests.test_monitor_core tests.test_monitor_delivery -v`

```
Ran 53 tests in 18.349s
OK
```

`git diff --check` passed. Publication now uses candidate-specific persisted state, explicit facade re-exports, core profile loading, compensated dual-file replacement coordinated with the queue transaction, per-recipient digests, optional SMTP login, and UTC-normalized due timestamps.

## Snapshot remediation RED

Command: `python -m unittest tests.test_monitor_delivery.MonitorDeliveryTests.test_medium_candidate_queues_an_immediate_individual_delivery tests.test_monitor_delivery.MonitorDeliveryTests.test_publish_uses_candidate_severity_snapshot_after_later_upgrade tests.test_monitor_delivery.MonitorDeliveryTests.test_smtp_secret_is_absent_from_every_generated_runtime_file -v`

```
test_medium_candidate_queues_an_immediate_individual_delivery ... ok
test_publish_uses_candidate_severity_snapshot_after_later_upgrade ... ERROR
ValueError: alerts[0].severity must match the candidate
test_smtp_secret_is_absent_from_every_generated_runtime_file ... ok
Ran 3 tests in 1.590s
FAILED (errors=1)
```

The expected failure showed publication was still validating against the mutable event severity after a later upgrade.

## Snapshot remediation GREEN

```
Ran 3 tests in 1.455s
OK
```

Final full-suite command: `python -m unittest tests.test_monitor_core tests.test_monitor_delivery -v`

```
Ran 56 tests in 20.088s
OK
```

`git diff --check` passed. `run_candidates` now persists the evaluated severity snapshot; medium delivery queueing and every generated runtime file's lack of SMTP secret values are explicitly covered.

## Reliability hardening RED

Command: focused delivery reliability regressions.

```
test_publish_rejects_duplicate_event_ids_and_is_idempotent ... FAIL
test_publish_rejects_naive_now_and_snapshot_mismatches_before_queueing ... FAIL
test_publish_rejects_header_injection ... FAIL
test_digest_catches_up_prior_local_date_once_per_recipient_and_rejects_naive_now ... FAIL
test_dispatch_uses_tls_login_and_never_resends_success ... FAIL
test_dispatch_rejects_naive_now_and_claims_delivery_before_sending ... FAIL
Ran 6 tests in 2.476s
FAILED (failures=6)
```

## Reliability hardening GREEN

Focused rerun:

```
Ran 6 tests in 0.819s
OK
```

Final full-suite command: `python -m unittest tests.test_monitor_core tests.test_monitor_delivery -v`

```
Ran 61 tests in 10.435s
OK
```

The delivery path now uses verified TLS contexts and bounded SMTP timeouts; validates profiles and aware timestamps; snapshots all score inputs; enforces publication idempotency; catches up digest dates; claims rows before I/O with leases; normalizes persisted instants; and documents the unavoidable SMTP at-least-once crash boundary.

## Crash recovery and concurrent dispatch RED

Command: `python -m unittest tests.test_monitor_delivery.MonitorDeliveryTests.test_manifest_reader_only_exposes_complete_bundle_and_recovers_crash_before_queue_commit tests.test_monitor_delivery.MonitorDeliveryTests.test_two_concurrent_dispatchers_claim_one_delivery_once -v`

```
test_manifest_reader_only_exposes_complete_bundle_and_recovers_crash_before_queue_commit ... ERROR
AttributeError: module 'monitor_delivery' has no attribute 'read_current_bundle'
test_two_concurrent_dispatchers_claim_one_delivery_once ... ok
Ran 2 tests in 0.274s
FAILED (errors=1)
```

## Crash recovery and concurrent dispatch GREEN

```
Ran 2 tests in 2.121s
OK
```

Final full-suite command: `python -m unittest tests.test_monitor_core tests.test_monitor_delivery -v`

```
Ran 63 tests in 29.009s
OK
```

Publication now stages immutable JSON/Markdown bundles and atomically switches `current.json`; `publication.journal` reconciles interrupted work on re-entry. Dispatch claims rows in a committed SQLite transaction before SMTP I/O; the two-worker test verifies exactly one send.

## Delivery state-machine gap RED

Command: focused low-digest and initial-crash regressions.

```
test_late_low_alert_after_sent_digest_creates_supplemental_delivery ... FAIL
test_dispatch_startup_reconciles_initial_manifest_crash ... FAIL
Ran 2 tests in 1.958s
FAILED (failures=2)
```

Lease/migration follow-up RED:

```
test_stale_worker_rechecks_lease_before_send ... ERROR
AttributeError: module 'monitor_delivery' has no attribute '_before_send'
test_migration_backfills_legacy_candidate_score_snapshot ... ERROR
ValueError: alerts[0].confidence must match the candidate; alerts[0].impact must match the candidate; alerts[0].urgency must match the candidate
Ran 2 tests in 0.984s
FAILED (errors=2)
```

## Delivery state-machine gap GREEN

```
Ran 2 tests in 1.747s
OK

Ran 2 tests in 1.473s
OK
```

Final full-suite command: `python -m unittest tests.test_monitor_core tests.test_monitor_delivery -v`

```
Ran 67 tests in 12.323s
OK
```

Low alerts now append to an unsent digest or generate a supplemental digest after sending. Startup reconciliation runs for dispatch and digest, expired workers revalidate ownership immediately before SMTP I/O, and legacy candidate score snapshots are backfilled from their persisted observations.
