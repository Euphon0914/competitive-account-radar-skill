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
