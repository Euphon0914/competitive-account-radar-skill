import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "monitor-competitive-account-signals" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import monitor_delivery  # noqa: E402
from monitor import _open_database, _severity_rank, _timezone_is_valid, build_digest, dispatch, evaluate, publish, validate_alert_draft  # noqa: E402


NOW = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
SECRET = "unpersisted-smtp-secret"


def profile(timezone_name="Asia/Shanghai"):
    return {
        "version": 1,
        "salesperson": {"name": "Lin", "alert_email": "lin@example.com"},
        "timezone": timezone_name,
        "smtp": {"env": {
            "host": "TEST_SMTP_HOST", "port": "TEST_SMTP_PORT", "username": "TEST_SMTP_USERNAME",
            "password": "TEST_SMTP_PASSWORD", "from": "TEST_SMTP_FROM",
        }},
        "competitors": ["Acme"], "accounts": ["Globex"], "sources": ["https://example.com/feed"],
        "policy": {"scan_interval_minutes": 60, "daily_digest_time": "17:30"},
    }


def observation(value, *, impact=4, urgency=4, quality=0.95, certainty=0.9, content_hash="hash"):
    return {
        "entity_id": "acme-1", "entity_name": "Acme", "signal_type": "competitor.price",
        "observed_at": (NOW - timedelta(days=2)).isoformat(), "effective_date": "2026-08-01",
        "normalized_value": {"price": value}, "source_uri": "https://example.com/source",
        "evidence_text": "Acme published a price change.", "content_hash": content_hash,
        "source_quality": quality, "extraction_certainty": certainty, "independent_sources": 2,
        "impact": impact, "urgency": urgency,
    }


def write_jsonl(path, items):
    path.write_text("\n".join(json.dumps(item) for item in items) + "\n", encoding="utf-8")


def candidate_project(project, *, severity="high", timezone_name="Asia/Shanghai"):
    project.joinpath("monitoring.yaml").write_text(yaml.safe_dump(profile(timezone_name)), encoding="utf-8")
    source = project / "observations.jsonl"
    write_jsonl(source, [observation(100, content_hash="initial")])
    evaluate(project, source, "manual", NOW)
    if severity == "low":
        changed = observation(90, impact=1, urgency=1, quality=0.5, certainty=0.4, content_hash="low")
    elif severity == "medium":
        changed = observation(90, impact=3, urgency=3, quality=0.7, certainty=0.7, content_hash="medium")
    else:
        changed = observation(90, content_hash="high")
    write_jsonl(source, [changed])
    result = evaluate(project, source, "manual", NOW)
    return result["run_id"], result["candidates"][0]


def draft_for(run_id, candidate, **overrides):
    alert = {
        "event_id": candidate["fingerprint"], "summary": "Acme lowered enterprise pricing",
        "why_it_matters": "Affects renewal positioning.", "evidence_ids": [candidate["observation"]["content_hash"]],
        "confidence": candidate["confidence"], "impact": candidate["observation"]["impact"],
        "urgency": candidate["observation"]["urgency"], "severity": candidate["severity"],
        "actions_24h": ["Call account sponsor"], "actions_7d": ["Prepare comparison"],
        "discovery_questions": ["What changed?", "Who approves?", "When is renewal?"],
        "talk_track": "We can discuss value beyond price.", "value_proposition": "Lower risk delivery.",
        "assumptions": "The public offer applies to this account.", "escalation_condition": "Sponsor asks for a matching offer.",
    }
    alert.update(overrides)
    return {"schema_version": "1.0", "run_id": run_id, "alerts": [alert]}


class FakeSMTP:
    instances = []
    fail_sends = 0

    def __init__(self, host, port):
        self.host, self.port, self.calls = host, port, []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.calls.append("quit")

    def starttls(self):
        self.calls.append("starttls")

    def login(self, username, password):
        self.calls.append(("login", username, password))

    def send_message(self, message):
        self.calls.append(("send", message))
        if FakeSMTP.fail_sends:
            FakeSMTP.fail_sends -= 1
            raise OSError("transport unavailable")


class MonitorDeliveryTests(unittest.TestCase):
    def setUp(self):
        FakeSMTP.instances, FakeSMTP.fail_sends = [], 0
        self.environ = os.environ.copy()
        for key, value in {"TEST_SMTP_HOST": "smtp.example.test", "TEST_SMTP_PORT": "587", "TEST_SMTP_USERNAME": "lin", "TEST_SMTP_PASSWORD": SECRET, "TEST_SMTP_FROM": "radar@example.test"}.items():
            os.environ[key] = value

    def tearDown(self):
        os.environ.clear(); os.environ.update(self.environ)

    def _publish(self, project, *, severity="high", **alert_overrides):
        run_id, candidate = candidate_project(project, severity=severity)
        path = project / "draft.json"
        path.write_text(json.dumps(draft_for(run_id, candidate, **alert_overrides)), encoding="utf-8")
        return publish(project, path, NOW), candidate

    def test_validate_alert_draft_rejects_unknown_or_missing_evidence(self):
        valid = draft_for(7, {"fingerprint": "event-a", "confidence": .9, "severity": "high", "observation": {"content_hash": "evidence-a", "impact": 4, "urgency": 4}})
        self.assertEqual(validate_alert_draft(valid, {"event-a"}), [])
        valid["alerts"][0]["evidence_ids"] = []
        errors = validate_alert_draft(valid, {"event-a"})
        self.assertTrue(any("evidence" in error for error in errors))
        valid["alerts"][0]["event_id"] = "unknown"
        self.assertTrue(any("event_id" in error for error in validate_alert_draft(valid, {"event-a"})))

    def test_validate_alert_draft_rejects_an_absent_non_empty_evidence_id_at_publish(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            run_id, candidate = candidate_project(project)
            draft = project / "draft.json"
            draft.write_text(json.dumps(draft_for(run_id, candidate, evidence_ids=["absent-evidence"])), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "evidence_ids"):
                publish(project, draft, NOW)

    def test_suppressed_events_are_not_candidates_for_a_later_publish(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            run_id, candidate = candidate_project(project)
            source = project / "observations.jsonl"
            write_jsonl(source, [observation(90, content_hash="high")])
            suppressed_run = evaluate(project, source, "manual", NOW)["run_id"]
            draft = project / "suppressed.json"
            draft.write_text(json.dumps(draft_for(suppressed_run, candidate)), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "event_id"):
                publish(project, draft, NOW)

    def test_facade_reexports_task_one_underscore_helpers(self):
        self.assertTrue(_timezone_is_valid("UTC"))
        self.assertEqual(_severity_rank("high"), 3)
        self.assertTrue(callable(_open_database))

    def test_validate_alert_draft_requires_strategy_fields_and_exact_questions(self):
        valid = draft_for(7, {"fingerprint": "event-a", "confidence": .9, "severity": "high", "observation": {"content_hash": "evidence-a", "impact": 4, "urgency": 4}})
        del valid["alerts"][0]["talk_track"]
        valid["alerts"][0]["discovery_questions"] = ["only one"]
        errors = validate_alert_draft(valid, {"event-a"})
        self.assertTrue(any("talk_track" in error for error in errors))
        self.assertTrue(any("discovery_questions" in error for error in errors))

    def test_commitment_language_requires_authorization(self):
        valid = draft_for(7, {"fingerprint": "event-a", "confidence": .9, "severity": "high", "observation": {"content_hash": "evidence-a", "impact": 4, "urgency": 4}}, talk_track="我们承诺折扣")
        self.assertTrue(any("authorization" in error for error in validate_alert_draft(valid, {"event-a"})))
        valid["alerts"][0]["authorization_required"] = "Sales leadership approval"
        self.assertEqual(validate_alert_draft(valid, {"event-a"}), [])

    def test_commitment_authorization_must_be_non_empty_and_strategy_numbers_bounded(self):
        valid = draft_for(7, {"fingerprint": "event-a", "confidence": .9, "severity": "high", "observation": {"content_hash": "evidence-a", "impact": 4, "urgency": 4}}, talk_track="GuaranteeD Discount", authorization_required="", confidence=1.1, impact=6, urgency=0, severity="urgent")
        errors = validate_alert_draft(valid, {"event-a"})
        self.assertTrue(any("authorization" in error for error in errors))
        self.assertTrue(any("confidence" in error for error in errors))
        self.assertTrue(any("impact" in error for error in errors))
        self.assertTrue(any("urgency" in error for error in errors))
        self.assertTrue(any("severity" in error for error in errors))

    def test_publish_writes_atomic_json_markdown_and_queues_high(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            result, candidate = self._publish(project)
            run_dir = project / ".competitive-radar" / "runs" / str(result["run_id"])
            payload = json.loads(run_dir.joinpath("alerts.json").read_text(encoding="utf-8"))
            markdown = run_dir.joinpath("alerts.md").read_text(encoding="utf-8")
            self.assertEqual(payload["alerts"][0]["event_id"], candidate["fingerprint"])
            self.assertIn(candidate["observation"]["content_hash"], markdown)
            self.assertIn("证据", markdown); self.assertIn("https://example.com/source", markdown)
            self.assertEqual(list(run_dir.glob(".*.tmp")), [])
            connection = sqlite3.connect(project / ".competitive-radar" / "state.db")
            try:
                self.assertEqual(connection.execute("SELECT status FROM deliveries").fetchone()[0], "pending")
            finally: connection.close()

    def test_medium_candidate_queues_an_immediate_individual_delivery(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            result, _ = self._publish(project, severity="medium")
            connection = sqlite3.connect(project / ".competitive-radar" / "state.db")
            try:
                kind, status, subject = connection.execute("SELECT kind, status, subject FROM deliveries").fetchone()
            finally: connection.close()
            self.assertEqual((kind, status), ("immediate", "pending"))
            self.assertTrue(subject.startswith("[MEDIUM][Acme]"))

    def test_publish_uses_candidate_severity_snapshot_after_later_upgrade(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            original_run, original_candidate = candidate_project(project, severity="medium")
            source = project / "observations.jsonl"
            write_jsonl(source, [observation(90, content_hash="upgraded")])
            upgraded = evaluate(project, source, "manual", NOW)
            self.assertEqual(upgraded["candidates"][0]["severity"], "high")
            draft = project / "original-medium.json"
            draft.write_text(json.dumps(draft_for(original_run, original_candidate)), encoding="utf-8")
            published = publish(project, draft, NOW)
            self.assertEqual(published["queued"], 1)
            payload = json.loads((project / ".competitive-radar" / "runs" / str(original_run) / "alerts.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["alerts"][0]["severity"], "medium")

    def test_smtp_secret_is_absent_from_every_generated_runtime_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary); self._publish(project)
            dispatch(project, NOW, smtp_factory=FakeSMTP)
            generated = [path for path in project.rglob("*") if path.is_file()]
            self.assertTrue(generated)
            self.assertTrue(all(SECRET.encode("utf-8") not in path.read_bytes() for path in generated))

    def test_invalid_publish_preserves_prior_outputs_and_never_queues(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            result, candidate = self._publish(project)
            run_dir = project / ".competitive-radar" / "runs" / str(result["run_id"])
            prior = run_dir.joinpath("alerts.json").read_text(encoding="utf-8")
            invalid = project / "invalid.json"
            invalid.write_text(json.dumps(draft_for(result["run_id"], candidate, evidence_ids=[])), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "evidence"):
                publish(project, invalid, NOW)
            self.assertEqual(run_dir.joinpath("alerts.json").read_text(encoding="utf-8"), prior)
            connection = sqlite3.connect(project / ".competitive-radar" / "state.db")
            try: self.assertEqual(connection.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0], 1)
            finally: connection.close()

    def test_second_output_replacement_failure_restores_both_outputs_and_queue(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            result, candidate = self._publish(project)
            run_dir = project / ".competitive-radar" / "runs" / str(result["run_id"])
            prior_json, prior_markdown = (run_dir / "alerts.json").read_text(encoding="utf-8"), (run_dir / "alerts.md").read_text(encoding="utf-8")
            changed = project / "changed.json"
            changed.write_text(json.dumps(draft_for(result["run_id"], candidate, summary="Changed summary")), encoding="utf-8")
            real_replace, calls = monitor_delivery.os.replace, []
            def fail_second(source, target):
                calls.append(Path(target).name)
                if len(calls) == 2: raise OSError("injected replacement failure")
                return real_replace(source, target)
            with mock.patch.object(monitor_delivery.os, "replace", side_effect=fail_second):
                with self.assertRaisesRegex(OSError, "injected"):
                    publish(project, changed, NOW)
            self.assertEqual((run_dir / "alerts.json").read_text(encoding="utf-8"), prior_json)
            self.assertEqual((run_dir / "alerts.md").read_text(encoding="utf-8"), prior_markdown)
            connection = sqlite3.connect(project / ".competitive-radar" / "state.db")
            try: self.assertEqual(connection.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0], 1)
            finally: connection.close()

    def test_publish_can_follow_timestamp_normalization_without_partial_transaction(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            result, candidate = self._publish(project)
            connection = sqlite3.connect(project / ".competitive-radar" / "state.db")
            try: connection.execute("UPDATE deliveries SET next_attempt_at = ?", ("2026-08-17T10:00:00+03:00",)); connection.commit()
            finally: connection.close()
            draft = project / "again.json"
            draft.write_text(json.dumps(draft_for(result["run_id"], candidate, summary="Updated summary")), encoding="utf-8")
            self.assertEqual(publish(project, draft, NOW)["published"], 1)

    def test_low_alert_waits_for_local_digest_and_handles_timezone_date_boundary(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            result, _ = self._publish(project, severity="low")
            before = build_digest(project, date(2026, 8, 17), datetime(2026, 8, 17, 9, 29, tzinfo=timezone.utc))
            self.assertEqual(before["queued"], 0)
            at_time = build_digest(project, date(2026, 8, 17), datetime(2026, 8, 17, 9, 30, tzinfo=timezone.utc))
            self.assertEqual(at_time["queued"], 1)
            self.assertEqual(at_time["run_id"], result["run_id"])
            tomorrow = build_digest(project, date(2026, 8, 18), datetime(2026, 8, 17, 16, 0, tzinfo=timezone.utc))
            self.assertEqual(tomorrow["queued"], 0)

    def test_dispatch_reports_missing_smtp_env_without_secrets_or_state_loss(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary); self._publish(project)
            del os.environ["TEST_SMTP_HOST"]
            result = dispatch(project, NOW, smtp_factory=FakeSMTP)
            self.assertEqual(result["delivered"], 0); self.assertIn("TEST_SMTP_HOST", result["error"])
            self.assertNotIn(SECRET, result["error"])
            connection = sqlite3.connect(project / ".competitive-radar" / "state.db")
            try: self.assertEqual(connection.execute("SELECT status FROM deliveries").fetchone()[0], "pending")
            finally: connection.close()

    def test_dispatch_uses_tls_login_and_never_resends_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary); self._publish(project)
            sent = dispatch(project, NOW, smtp_factory=FakeSMTP)
            self.assertEqual(sent["delivered"], 1)
            calls = FakeSMTP.instances[0].calls
            self.assertEqual(calls[0], "starttls"); self.assertEqual(calls[1], ("login", "lin", SECRET)); self.assertEqual(calls[2][0], "send")
            self.assertEqual(calls[2][1]["Subject"], "[HIGH][Acme] Acme lowered enterprise pricing")
            self.assertEqual(dispatch(project, NOW + timedelta(minutes=1), smtp_factory=FakeSMTP)["delivered"], 0)
            self.assertEqual(len(FakeSMTP.instances), 1)
            database_text = (project / ".competitive-radar" / "state.db").read_bytes()
            self.assertNotIn(SECRET.encode(), database_text)

    def test_dispatch_uses_tls_without_login_when_username_is_empty(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary); self._publish(project)
            os.environ["TEST_SMTP_USERNAME"] = ""; del os.environ["TEST_SMTP_PASSWORD"]
            self.assertEqual(dispatch(project, NOW, smtp_factory=FakeSMTP)["delivered"], 1)
            calls = FakeSMTP.instances[0].calls
            self.assertEqual(calls[0], "starttls")
            self.assertFalse(any(isinstance(call, tuple) and call[0] == "login" for call in calls))

    def test_dispatch_compares_due_timestamps_as_utc_instants(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary); self._publish(project)
            connection = sqlite3.connect(project / ".competitive-radar" / "state.db")
            try: connection.execute("UPDATE deliveries SET next_attempt_at = ?", ("2026-08-17T10:00:00+03:00",)); connection.commit()
            finally: connection.close()
            result = dispatch(project, datetime(2026, 8, 17, 8, 0, tzinfo=timezone.utc), smtp_factory=FakeSMTP)
            self.assertEqual(result["delivered"], 1)

    def test_failure_retries_on_schedule_then_stops_after_five_attempts(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary); self._publish(project)
            FakeSMTP.fail_sends = 6
            for minutes, expected_attempt in [(0, 1), (1, 2), (6, 3), (36, 4), (156, 5)]:
                dispatch(project, NOW + timedelta(minutes=minutes), smtp_factory=FakeSMTP)
                connection = sqlite3.connect(project / ".competitive-radar" / "state.db")
                try:
                    attempt, next_at, status = connection.execute("SELECT attempts, next_attempt_at, status FROM deliveries").fetchone()
                finally: connection.close()
                self.assertEqual(attempt, expected_attempt)
                if expected_attempt < 5: self.assertEqual(datetime.fromisoformat(next_at), NOW + timedelta(minutes=minutes + [1, 5, 30, 120][expected_attempt - 1]))
                else: self.assertEqual(status, "failed")
            self.assertEqual(dispatch(project, NOW + timedelta(hours=8), smtp_factory=FakeSMTP)["attempted"], 0)

    def test_connection_failure_uses_the_same_durable_retry_schedule(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary); self._publish(project)
            def unavailable(host, port):
                raise OSError("unavailable")
            self.assertEqual(dispatch(project, NOW, smtp_factory=unavailable)["attempted"], 1)
            connection = sqlite3.connect(project / ".competitive-radar" / "state.db")
            try:
                attempts, next_at = connection.execute("SELECT attempts, next_attempt_at FROM deliveries").fetchone()
            finally: connection.close()
            self.assertEqual(attempts, 1)
            self.assertEqual(datetime.fromisoformat(next_at), NOW + timedelta(minutes=1))

    def test_digest_delivery_has_daily_subject_and_recipient_isolation(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary); self._publish(project, severity="low")
            build_digest(project, date(2026, 8, 17), datetime(2026, 8, 17, 9, 30, tzinfo=timezone.utc))
            self.assertEqual(dispatch(project, NOW, smtp_factory=FakeSMTP)["delivered"], 1)
            sent = FakeSMTP.instances[0].calls[2][1]
            self.assertEqual(sent["To"], "lin@example.com")
            self.assertEqual(sent["Subject"], "[DAILY][2026-08-17] 竞争与客户弱信号摘要")
            self.assertNotIn("radar@example.test", sent["To"])

    def test_digest_groups_items_by_the_recipient_stored_at_publish(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary); self._publish(project, severity="low")
            saved = yaml.safe_load((project / "monitoring.yaml").read_text(encoding="utf-8"))
            saved["salesperson"]["alert_email"] = "new@example.com"
            (project / "monitoring.yaml").write_text(yaml.safe_dump(saved), encoding="utf-8")
            source = project / "observations.jsonl"; write_jsonl(source, [observation(80, impact=1, urgency=1, quality=.5, certainty=.4, content_hash="low-two")])
            later = evaluate(project, source, "manual", NOW)
            draft = project / "second-low.json"; draft.write_text(json.dumps(draft_for(later["run_id"], later["candidates"][0])), encoding="utf-8")
            publish(project, draft, NOW)
            self.assertEqual(build_digest(project, date(2026, 8, 17), datetime(2026, 8, 17, 9, 30, tzinfo=timezone.utc))["queued"], 2)
            self.assertEqual(dispatch(project, NOW, smtp_factory=FakeSMTP)["delivered"], 2)
            recipients = {call[1]["To"] for instance in FakeSMTP.instances for call in instance.calls if isinstance(call, tuple) and call[0] == "send"}
            self.assertEqual(recipients, {"lin@example.com", "new@example.com"})

    def test_publish_dispatch_and_digest_cli_commands(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary); run_id, candidate = candidate_project(project)
            draft = project / "draft.json"; draft.write_text(json.dumps(draft_for(run_id, candidate)), encoding="utf-8")
            script = ROOT / "monitor-competitive-account-signals" / "scripts" / "monitor.py"
            environment = dict(os.environ)
            published = subprocess.run([sys.executable, str(script), "publish", "--project", str(project), "--draft", str(draft), "--now", NOW.isoformat()], text=True, capture_output=True, env=environment)
            dispatched = subprocess.run([sys.executable, str(script), "dispatch", "--project", str(project), "--now", NOW.isoformat()], text=True, capture_output=True, env=environment)
            digested = subprocess.run([sys.executable, str(script), "digest", "--project", str(project), "--date", "2026-08-17", "--now", NOW.isoformat()], text=True, capture_output=True, env=environment)
            self.assertEqual((published.returncode, dispatched.returncode, digested.returncode), (0, 0, 0))


if __name__ == "__main__":
    unittest.main()
