import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "monitor-competitive-account-signals" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from monitor import build_digest, dispatch, evaluate, publish, validate_alert_draft  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
