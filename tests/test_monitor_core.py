import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "monitor-competitive-account-signals" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from monitor import (  # noqa: E402
    calculate_confidence,
    classify_severity,
    evaluate,
    event_fingerprint,
    load_observations,
    powershell_smtp_setup,
    run_init_wizard,
    validate_profile,
)


NOW = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)


def profile():
    return {
        "version": 1,
        "salesperson": {"name": "Lin", "alert_email": "lin@example.com"},
        "timezone": "Asia/Shanghai",
        "smtp": {"env": {"host": "CI_SMTP_HOST", "port": "CI_SMTP_PORT", "username": "CI_SMTP_USERNAME", "password": "CI_SMTP_PASSWORD", "from": "CI_SMTP_FROM"}},
        "competitors": ["Acme"],
        "accounts": ["Globex"],
        "sources": ["https://example.com/feed"],
        "policy": {"scan_interval_minutes": 60, "daily_digest_time": "17:30"},
    }


def observation(signal_type="competitor.price", value={"price": 100}, **overrides):
    value = dict(value) if isinstance(value, dict) else value
    result = {
        "entity_id": "acme-1",
        "entity_name": "Acme",
        "signal_type": signal_type,
        "observed_at": (NOW - timedelta(days=2)).isoformat(),
        "effective_date": "2026-08-01",
        "normalized_value": value,
        "source_uri": "https://example.com/source",
        "evidence_text": "Acme published a change.",
        "content_hash": "content-a",
        "source_quality": 0.95,
        "extraction_certainty": 0.9,
        "independent_sources": 2,
        "impact": 4,
        "urgency": 4,
    }
    result.update(overrides)
    return result


def write_jsonl(path, records):
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")


class MonitorCoreTests(unittest.TestCase):
    def test_wizard_writes_complete_valid_profile_without_secret(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            answers = iter([
                "Lin", "lin@example.com", "", "1", "", "", "", "", "", "Acme, Rival", "Globex", "https://example.com/a", "./notes.txt", "",
            ])
            output = []
            path = run_init_wizard(project, input_fn=lambda prompt: next(answers), output_fn=output.append)
            self.assertEqual(path, project / "monitoring.yaml")
            import yaml
            saved = yaml.safe_load(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["version"], 1)
            self.assertEqual(saved["salesperson"], {"name": "Lin", "alert_email": "lin@example.com"})
            self.assertNotIn("alert_recipient", saved)
            self.assertEqual(saved["smtp"]["env"]["password"], "CI_SMTP_PASSWORD")
            self.assertNotIn("secret", path.read_text(encoding="utf-8").lower())
            self.assertEqual(saved["competitors"], ["Acme", "Rival"])
            self.assertEqual(saved["policy"], {"scan_interval_minutes": 60, "daily_digest_time": "17:30"})

    def test_wizard_uses_only_unique_temp_files_and_preserves_unrelated_temp_name(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            stale_name = project / ".monitoring.yaml.tmp"
            stale_name.write_text("unrelated", encoding="utf-8")
            answers = iter(["Lin", "lin@example.com", "", "5", "", "", "", "", "", "Acme", "", "file.txt", ""])
            run_init_wizard(project, input_fn=lambda prompt: next(answers), output_fn=lambda _: None)
            self.assertTrue(stale_name.exists())
            self.assertEqual(stale_name.read_text(encoding="utf-8"), "unrelated")
            self.assertEqual(list(project.glob(".monitoring.yaml.*.tmp")), [])

    def test_wizard_prompts_smtp_environment_names_in_contract_order(self):
        with tempfile.TemporaryDirectory() as temporary:
            prompts = []
            answers = iter(["Lin", "lin@example.com", "", "5", "", "", "", "", "", "Acme", "", "file.txt", ""])
            run_init_wizard(Path(temporary), input_fn=lambda prompt: (prompts.append(prompt), next(answers))[1], output_fn=lambda _: None)
            smtp_prompts = [prompt for prompt in prompts if "environment-variable name" in prompt]
            self.assertEqual(smtp_prompts, [
                "SMTP host environment-variable name [CI_SMTP_HOST]: ",
                "SMTP port environment-variable name [CI_SMTP_PORT]: ",
                "SMTP username environment-variable name [CI_SMTP_USERNAME]: ",
                "SMTP password environment-variable name [CI_SMTP_PASSWORD]: ",
                "SMTP from environment-variable name [CI_SMTP_FROM]: ",
            ])

    def test_wizard_explains_gmail_and_emits_local_only_secret_setup(self):
        with tempfile.TemporaryDirectory() as temporary:
            answers = iter(["Lin", "lin@example.com", "", "1", "", "", "", "", "", "Acme", "", "file.txt", ""])
            output = []
            saved = run_init_wizard(Path(temporary), input_fn=lambda prompt: next(answers), output_fn=output.append)
            text = "\n".join(output)
            self.assertIn("Gmail / Google Workspace", text)
            self.assertIn("smtp.gmail.com", text)
            self.assertIn("Read-Host \"SMTP app password\" -AsSecureString", text)
            self.assertIn("SetEnvironmentVariable", text)
            self.assertNotIn("lin@example.com", saved.read_text(encoding="utf-8").split("smtp:")[1])
            self.assertNotIn("app password", saved.read_text(encoding="utf-8").lower())

    def test_other_smtp_setup_leaves_host_and_port_for_user_without_revealing_password(self):
        guide = powershell_smtp_setup(
            {"name": "其他 SMTP", "host": "", "port": "", "note": "manual"},
            {"host": "H", "port": "P", "username": "U", "password": "S", "from": "F"},
        )
        self.assertIn("Set H to your SMTP host", guide)
        self.assertIn("Set P to your STARTTLS port", guide)
        self.assertIn("Read-Host \"SMTP app password\" -AsSecureString", guide)
        self.assertNotIn("Write-Host", guide)

    def test_wizard_reprompts_invalid_email_and_timezone(self):
        with tempfile.TemporaryDirectory() as temporary:
            answers = iter(["Lin", "wrong", "lin@example.com", "Mars/Olympus", "UTC", "5", "", "", "", "", "", "Acme", "", "file.txt", ""])
            output = []
            run_init_wizard(Path(temporary), input_fn=lambda prompt: next(answers), output_fn=output.append)
            text = "\n".join(output)
            self.assertIn("email", text.lower())
            self.assertIn("timezone", text.lower())

    def test_wizard_cancel_writes_nothing(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            self.assertIsNone(run_init_wizard(project, input_fn=lambda prompt: (_ for _ in ()).throw(KeyboardInterrupt()), output_fn=lambda _: None))
            self.assertFalse((project / "monitoring.yaml").exists())

    def test_wizard_requires_confirmation_to_overwrite_valid_profile(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            project.joinpath("monitoring.yaml").write_text("salesperson: Original\n", encoding="utf-8")
            # Invalid partial profiles do not count as an existing valid profile.
            answers = iter(["Lin", "lin@example.com", "", "5", "", "", "", "", "", "Acme", "", "file.txt", ""])
            run_init_wizard(project, input_fn=lambda prompt: next(answers), output_fn=lambda _: None)
            saved = project.joinpath("monitoring.yaml").read_text(encoding="utf-8")
            self.assertIn("Lin", saved)
            import yaml
            project.joinpath("monitoring.yaml").write_text(yaml.safe_dump(profile()), encoding="utf-8")
            answers = iter(["n"])
            self.assertIsNone(run_init_wizard(project, input_fn=lambda prompt: next(answers), output_fn=lambda _: None))
            self.assertIn("Lin", project.joinpath("monitoring.yaml").read_text(encoding="utf-8"))

    def test_profile_validation_identifies_required_configuration(self):
        errors = validate_profile({})
        self.assertTrue(any("salesperson" in error for error in errors))
        self.assertTrue(any("alert_email" in error for error in errors))
        self.assertTrue(any("source" in error for error in errors))

    def test_profile_requires_nested_salesperson_alert_email_with_full_string_validation(self):
        invalid = profile()
        invalid["salesperson"] = "Lin"
        invalid["alert_recipient"] = "lin@example.com\n"
        errors = validate_profile(invalid)
        self.assertIn("salesperson.alert_email is invalid", errors)
        nested_invalid = profile()
        nested_invalid["salesperson"]["alert_email"] = "lin@example.com\n"
        self.assertIn("salesperson.alert_email is invalid", validate_profile(nested_invalid))

    def test_wizard_recovers_from_non_utf8_existing_profile(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            project.joinpath("monitoring.yaml").write_bytes(b"\xff\xfe")
            answers = iter(["Lin", "lin@example.com", "", "5", "", "", "", "", "", "Acme", "", "file.txt", ""])
            path = run_init_wizard(project, input_fn=lambda prompt: next(answers), output_fn=lambda _: None)
            import yaml
            self.assertEqual(yaml.safe_load(path.read_text(encoding="utf-8"))["salesperson"]["alert_email"], "lin@example.com")

    def test_load_observations_accepts_all_signal_types(self):
        signal_types = ["competitor.price", "competitor.portfolio", "competitor.partnership", "account.business", "account.satisfaction", "account.need"]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "observations.jsonl"
            write_jsonl(path, [observation(signal_type=kind, value={"kind": kind}) for kind in signal_types])
            self.assertEqual([item["signal_type"] for item in load_observations(path)], signal_types)

    def test_load_observations_reports_line_number_for_malformed_input(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "observations.jsonl"
            path.write_text(json.dumps(observation()) + "\nnot json\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "line 2"):
                load_observations(path)

    def test_load_observations_prefixes_invalid_utf8_with_a_line_number(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "observations.jsonl"
            path.write_bytes(b"\xff")
            with self.assertRaisesRegex(ValueError, "line 1.*UTF-8"):
                load_observations(path)

    def test_load_observations_requires_nullable_effective_date_and_normalized_value_keys(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "observations.jsonl"
            missing_value = observation()
            del missing_value["normalized_value"]
            write_jsonl(path, [missing_value])
            with self.assertRaisesRegex(ValueError, "line 1.*normalized_value"):
                load_observations(path)
            missing_date = observation()
            del missing_date["effective_date"]
            write_jsonl(path, [missing_date])
            with self.assertRaisesRegex(ValueError, "line 1.*effective_date"):
                load_observations(path)

    def test_load_observations_prefixes_invalid_timestamp_with_its_line_number(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "observations.jsonl"
            invalid = observation(observed_at="2026-08-17")
            write_jsonl(path, [invalid])
            with self.assertRaisesRegex(ValueError, "line 1.*observed_at"):
                load_observations(path)

    def test_load_observations_rejects_non_finite_normalized_json_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "observations.jsonl"
            write_jsonl(path, [observation(value=float("nan"))])
            with self.assertRaisesRegex(ValueError, "line 1.*NaN"):
                load_observations(path)

    def test_load_observations_requires_zero_padded_effective_date(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "observations.jsonl"
            write_jsonl(path, [observation(effective_date="2026-8-1")])
            with self.assertRaisesRegex(ValueError, "line 1.*effective_date"):
                load_observations(path)

    def test_wizard_allows_an_account_when_competitors_are_blank(self):
        with tempfile.TemporaryDirectory() as temporary:
            answers = iter(["Lin", "lin@example.com", "", "5", "", "", "", "", "", "", "Globex", "file.txt", ""])
            saved = run_init_wizard(Path(temporary), input_fn=lambda prompt: next(answers), output_fn=lambda _: None)
            import yaml
            profile_data = yaml.safe_load(saved.read_text(encoding="utf-8"))
            self.assertEqual(profile_data["competitors"], [])
            self.assertEqual(profile_data["accounts"], ["Globex"])

    def test_confidence_boundaries_and_future_timestamp_rejection(self):
        high = observation(observed_at=(NOW - timedelta(days=7)).isoformat(), source_quality=1, extraction_certainty=1, independent_sources=2)
        self.assertEqual(calculate_confidence(high, NOW), 1.0)
        thirty = observation(observed_at=(NOW - timedelta(days=30)).isoformat(), source_quality=1, extraction_certainty=1, independent_sources=2)
        self.assertEqual(calculate_confidence(thirty, NOW), 0.96)
        self.assertEqual(classify_severity(0.75, 4, 4), "high")
        self.assertEqual(classify_severity(0.55, 3, 3), "medium")
        self.assertEqual(classify_severity(0.35, 1, 1), "low")
        self.assertIsNone(classify_severity(0.3499, 5, 5))
        future = observation(observed_at=(NOW + timedelta(seconds=1)).isoformat())
        with self.assertRaisesRegex(ValueError, "future"):
            calculate_confidence(future, NOW)

    def test_confidence_uses_90_day_and_single_source_boundaries(self):
        exact_90 = observation(observed_at=(NOW - timedelta(days=90)).isoformat(), source_quality=1, extraction_certainty=1, independent_sources=2)
        post_90 = observation(observed_at=(NOW - timedelta(days=90, seconds=1)).isoformat(), source_quality=1, extraction_certainty=1, independent_sources=2)
        high_quality_single = observation(source_quality=0.9, extraction_certainty=1, independent_sources=1)
        normal_single = observation(source_quality=0.89, extraction_certainty=1, independent_sources=1)
        self.assertEqual(calculate_confidence(exact_90, NOW), 0.92)
        self.assertEqual(calculate_confidence(post_90, NOW), 0.86)
        self.assertEqual(calculate_confidence(high_quality_single, NOW), 0.9)
        self.assertEqual(calculate_confidence(normal_single, NOW), 0.851)

    def test_evaluate_prefixes_future_observations_with_their_jsonl_line(self):
        with tempfile.TemporaryDirectory() as temporary:
            project, path = Path(temporary), Path(temporary) / "observations.jsonl"
            write_jsonl(path, [observation(observed_at=(NOW + timedelta(seconds=1)).isoformat())])
            with self.assertRaisesRegex(ValueError, "line 1.*future"):
                evaluate(project, path, "manual", NOW)

    def test_evaluation_baselines_changes_then_suppresses_repeats(self):
        with tempfile.TemporaryDirectory() as temporary:
            project, path = Path(temporary), Path(temporary) / "observations.jsonl"
            write_jsonl(path, [observation()])
            first = evaluate(project, path, "manual", NOW)
            self.assertTrue(first["baseline"])
            self.assertEqual(first["candidates"], [])
            changed = observation(value={"price": 90}, content_hash="content-b")
            write_jsonl(path, [changed])
            second = evaluate(project, path, "manual", NOW)
            self.assertFalse(second["baseline"])
            self.assertEqual(len(second["candidates"]), 1)
            self.assertEqual(second["candidates"][0]["severity"], "high")
            repeat = evaluate(project, path, "manual", NOW)
            self.assertEqual(repeat["candidates"], [])
            self.assertEqual(len(repeat["suppressed"]), 1)

    def test_evaluation_realerts_severity_upgrade_or_new_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            project, path = Path(temporary), Path(temporary) / "observations.jsonl"
            write_jsonl(path, [observation(value={"price": 100})])
            evaluate(project, path, "manual", NOW)
            low_change = observation(value={"price": 95}, source_quality=0.5, extraction_certainty=0.4, independent_sources=1, impact=1, urgency=1, content_hash="v1")
            write_jsonl(path, [low_change])
            self.assertEqual(evaluate(project, path, "manual", NOW)["candidates"][0]["severity"], "low")
            upgrade = dict(low_change, impact=4, urgency=4, source_quality=0.95, extraction_certainty=0.9, independent_sources=2)
            write_jsonl(path, [upgrade])
            self.assertEqual(evaluate(project, path, "manual", NOW)["candidates"][0]["severity"], "high")
            new_evidence = dict(upgrade, content_hash="v2")
            write_jsonl(path, [new_evidence])
            self.assertEqual(len(evaluate(project, path, "manual", NOW)["candidates"]), 1)

    def test_evaluation_candidates_when_value_returns_to_an_earlier_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            project, path = Path(temporary), Path(temporary) / "observations.jsonl"
            for value in (100, 90, 80):
                write_jsonl(path, [observation(value={"price": value}, content_hash=f"value-{value}")])
                evaluate(project, path, "manual", NOW)
            write_jsonl(path, [observation(value={"price": 90}, content_hash="value-90")])
            result = evaluate(project, path, "manual", NOW)
            self.assertEqual(len(result["candidates"]), 1)
            self.assertEqual(result["candidates"][0]["observation"]["normalized_value"], {"price": 90})

    def test_suppressed_event_updates_last_run_id(self):
        with tempfile.TemporaryDirectory() as temporary:
            project, path = Path(temporary), Path(temporary) / "observations.jsonl"
            write_jsonl(path, [observation(value={"price": 100})])
            evaluate(project, path, "manual", NOW)
            changed = observation(value={"price": 90}, content_hash="changed")
            write_jsonl(path, [changed])
            evaluate(project, path, "manual", NOW)
            repeat = evaluate(project, path, "manual", NOW)
            fingerprint = event_fingerprint(changed)
            connection = sqlite3.connect(project / ".competitive-radar" / "state.db")
            try:
                self.assertEqual(connection.execute("SELECT last_run_id FROM events WHERE fingerprint = ?", (fingerprint,)).fetchone()[0], repeat["run_id"])
            finally:
                connection.close()

    def test_source_failure_is_recorded_only_and_cannot_change_baseline(self):
        with tempfile.TemporaryDirectory() as temporary:
            project, path = Path(temporary), Path(temporary) / "observations.jsonl"
            write_jsonl(path, [observation(value={"price": 100})])
            evaluate(project, path, "manual", NOW)
            failure = observation(value={"price": 1}, source_status="404", content_hash="failed")
            write_jsonl(path, [failure])
            result = evaluate(project, path, "manual", NOW)
            self.assertEqual(result["candidates"], [])
            self.assertEqual(len(result["recorded_only"]), 1)
            write_jsonl(path, [observation(value={"price": 90}, content_hash="ok")])
            self.assertEqual(len(evaluate(project, path, "manual", NOW)["candidates"]), 1)

    def test_invalid_batch_rolls_back_all_sqlite_mutations_and_creates_required_tables(self):
        with tempfile.TemporaryDirectory() as temporary:
            project, path = Path(temporary), Path(temporary) / "observations.jsonl"
            write_jsonl(path, [observation()])
            evaluate(project, path, "manual", NOW)
            database = project / ".competitive-radar" / "state.db"
            connection = sqlite3.connect(database)
            try:
                tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertTrue({"runs", "observations", "events", "deliveries", "digest_items"}.issubset(tables))
                before = connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
            finally:
                connection.close()
            path.write_text(json.dumps(observation(value={"price": 80})) + "\n{broken}\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "line 2"):
                evaluate(project, path, "manual", NOW)
            connection = sqlite3.connect(database)
            try:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0], before)
            finally:
                connection.close()

    def test_in_transaction_failure_rolls_back_all_mutable_tables(self):
        with tempfile.TemporaryDirectory() as temporary:
            project, path = Path(temporary), Path(temporary) / "observations.jsonl"
            write_jsonl(path, [observation(value={"price": 100})])
            evaluate(project, path, "manual", NOW)
            database = project / ".competitive-radar" / "state.db"
            connection = sqlite3.connect(database)
            try:
                before = {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ("runs", "observations", "events", "deliveries", "digest_items")}
                connection.execute("CREATE TRIGGER reject_observation BEFORE INSERT ON observations WHEN NEW.run_id > 1 BEGIN SELECT RAISE(ABORT, 'injected failure'); END")
                connection.commit()
            finally:
                connection.close()
            write_jsonl(path, [observation(value={"price": 90})])
            with self.assertRaisesRegex(sqlite3.DatabaseError, "injected failure"):
                evaluate(project, path, "manual", NOW)
            connection = sqlite3.connect(database)
            try:
                after = {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in before}
            finally:
                connection.close()
            self.assertEqual(after, before)

    def test_concurrent_first_runs_produce_exactly_one_baseline(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            paths = [project / f"observations-{index}.jsonl" for index in range(2)]
            for path in paths:
                write_jsonl(path, [observation()])
            start = threading.Barrier(3)
            results, failures = [], []

            def worker(path):
                try:
                    start.wait()
                    results.append(evaluate(project, path, "manual", NOW))
                except Exception as error:  # captured for deterministic assertion below
                    failures.append(error)

            workers = [threading.Thread(target=worker, args=(path,)) for path in paths]
            for worker_thread in workers:
                worker_thread.start()
            start.wait()
            for worker_thread in workers:
                worker_thread.join()
            self.assertEqual(failures, [])
            self.assertEqual(sum(result["baseline"] for result in results), 1)

    def test_evaluate_does_not_enqueue_delivery_or_digest_rows(self):
        with tempfile.TemporaryDirectory() as temporary:
            project, path = Path(temporary), Path(temporary) / "observations.jsonl"
            write_jsonl(path, [observation(value={"price": 100})])
            evaluate(project, path, "manual", NOW)
            write_jsonl(path, [observation(value={"price": 90}, content_hash="changed")])
            self.assertEqual(len(evaluate(project, path, "manual", NOW)["candidates"]), 1)
            connection = sqlite3.connect(project / ".competitive-radar" / "state.db")
            try:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0], 0)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM digest_items").fetchone()[0], 0)
            finally:
                connection.close()

    def test_fingerprint_uses_effective_month_bucket(self):
        first = observation(effective_date="2026-08-01")
        second = observation(effective_date="2026-08-31")
        third = observation(effective_date="2026-09-01")
        self.assertEqual(event_fingerprint(first), event_fingerprint(second))
        self.assertNotEqual(event_fingerprint(first), event_fingerprint(third))
        self.assertEqual(len(event_fingerprint(first)), 64)

    def test_cli_help_and_validate_exit_codes(self):
        script = ROOT / "monitor-competitive-account-signals" / "scripts" / "monitor.py"
        help_result = subprocess.run([sys.executable, str(script), "--help"], text=True, capture_output=True, check=False)
        self.assertEqual(help_result.returncode, 0)
        self.assertIn("evaluate", help_result.stdout)
        with tempfile.TemporaryDirectory() as temporary:
            invalid = subprocess.run([sys.executable, str(script), "validate", "--project", temporary], text=True, capture_output=True, check=False)
            self.assertEqual(invalid.returncode, 1)
            Path(temporary, "monitoring.yaml").write_text("salesperson: Lin\n", encoding="utf-8")
            still_invalid = subprocess.run([sys.executable, str(script), "validate", "--project", temporary], text=True, capture_output=True, check=False)
            self.assertEqual(still_invalid.returncode, 1)
            import yaml
            Path(temporary, "monitoring.yaml").write_text(yaml.safe_dump(profile()), encoding="utf-8")
            valid = subprocess.run([sys.executable, str(script), "validate", "--project", temporary], text=True, capture_output=True, check=False)
            self.assertEqual(valid.returncode, 0)

    def test_cli_validate_non_utf8_profile_is_an_invalid_profile(self):
        script = ROOT / "monitor-competitive-account-signals" / "scripts" / "monitor.py"
        with tempfile.TemporaryDirectory() as temporary:
            Path(temporary, "monitoring.yaml").write_bytes(b"\xff")
            result = subprocess.run([sys.executable, str(script), "validate", "--project", temporary], text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 1)
            self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
