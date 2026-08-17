"""Deterministic configuration and evaluation core for Competitive Account Radar."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml


SIGNAL_TYPES = {
    "competitor.price", "competitor.portfolio", "competitor.partnership",
    "account.business", "account.satisfaction", "account.need",
}
EMAIL_PATTERN = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
SMTP_DEFAULTS = {
    "host": "CI_SMTP_HOST", "port": "CI_SMTP_PORT", "username": "CI_SMTP_USERNAME",
    "password": "CI_SMTP_PASSWORD", "from": "CI_SMTP_FROM",
}


def _timezone_is_valid(value: object) -> bool:
    try:
        ZoneInfo(str(value))
        return True
    except (ZoneInfoNotFoundError, ValueError):
        return False


def validate_profile(profile: dict) -> list[str]:
    """Return human-readable validation errors for a monitoring profile."""
    errors: list[str] = []
    if not isinstance(profile, dict):
        return ["profile must be a mapping"]
    if profile.get("version") != 1:
        errors.append("version must be 1")
    salesperson = profile.get("salesperson")
    if not isinstance(salesperson, dict) or not isinstance(salesperson.get("name"), str) or not salesperson["name"].strip():
        errors.append("salesperson.name is required")
    recipient = salesperson.get("alert_email") if isinstance(salesperson, dict) else None
    if not isinstance(recipient, str) or EMAIL_PATTERN.fullmatch(recipient) is None:
        errors.append("salesperson.alert_email is invalid")
    if not isinstance(profile.get("timezone"), str) or not _timezone_is_valid(profile["timezone"]):
        errors.append("timezone must be a valid IANA timezone")
    smtp = profile.get("smtp")
    env = smtp.get("env") if isinstance(smtp, dict) else None
    if not isinstance(env, dict) or any(not isinstance(env.get(key), str) or not env[key].strip() for key in SMTP_DEFAULTS):
        errors.append("smtp.env must provide host, port, username, password, and from environment-variable names")
    competitors, accounts, sources = profile.get("competitors"), profile.get("accounts"), profile.get("sources")
    if not isinstance(competitors, list) or not all(isinstance(value, str) and value.strip() for value in competitors):
        errors.append("competitors must be a list of names")
    if not isinstance(accounts, list) or not all(isinstance(value, str) and value.strip() for value in accounts):
        errors.append("accounts must be a list of names")
    if not ((isinstance(competitors, list) and competitors) or (isinstance(accounts, list) and accounts)):
        errors.append("at least one competitor or account is required")
    if not isinstance(sources, list) or not sources or not all(isinstance(value, str) and value.strip() for value in sources):
        errors.append("at least one source is required")
    policy = profile.get("policy")
    if not isinstance(policy, dict) or policy.get("scan_interval_minutes") != 60 or policy.get("daily_digest_time") != "17:30":
        errors.append("policy defaults are required")
    return errors


def _ask(input_fn, output_fn, label: str, *, default: str | None = None, validator=None) -> str:
    while True:
        prompt = f"{label}{f' [{default}]' if default else ''}: "
        value = input_fn(prompt).strip()
        value = value or (default or "")
        if value and (validator is None or validator(value)):
            return value
        output_fn(f"Invalid {label.lower()}; please try again.")


def _comma_items(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _write_profile_atomically(target: Path, profile: dict) -> None:
    """Durably replace a profile without reusing a predictable temporary path."""
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=target.parent,
            prefix=f".{target.name}.", suffix=".tmp", delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            yaml.safe_dump(profile, temporary, allow_unicode=True, sort_keys=False)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, target)
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


def run_init_wizard(project: Path, input_fn=input, output_fn=print) -> Path | None:
    """Interactively build a complete profile, writing it atomically only on success."""
    project = Path(project)
    target = project / "monitoring.yaml"
    try:
        if target.exists():
            try:
                existing = yaml.safe_load(target.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, yaml.YAMLError):
                existing = None
            if not validate_profile(existing if isinstance(existing, dict) else {}):
                confirmation = input_fn("A valid monitoring.yaml exists. Overwrite? [y/N]: ").strip().lower()
                if confirmation not in {"y", "yes"}:
                    return None
        salesperson = _ask(input_fn, output_fn, "Salesperson name")
        recipient = _ask(input_fn, output_fn, "Alert recipient email", validator=lambda value: EMAIL_PATTERN.fullmatch(value) is not None)
        timezone_name = _ask(input_fn, output_fn, "IANA timezone", default="Asia/Shanghai", validator=_timezone_is_valid)
        smtp_env = {key: _ask(input_fn, output_fn, f"SMTP {key} environment-variable name", default=value) for key, value in SMTP_DEFAULTS.items()}
        competitors = _comma_items(input_fn("Competitors (comma-separated): "))
        accounts = _comma_items(input_fn("Key accounts (comma-separated): "))
        while not competitors and not accounts:
            output_fn("At least one competitor or account is required.")
            accounts = _comma_items(input_fn("Key accounts (comma-separated): "))
        sources: list[str] = []
        output_fn("Source URLs or local paths (one per line; blank line finishes):")
        while True:
            source = input_fn("").strip()
            if not source:
                break
            sources.append(source)
        while not sources:
            output_fn("At least one source is required.")
            source = input_fn("Source URL or local path: ").strip()
            if source:
                sources.append(source)
        profile = {
            "version": 1, "salesperson": {"name": salesperson, "alert_email": recipient}, "timezone": timezone_name,
            "smtp": {"env": smtp_env}, "competitors": competitors, "accounts": accounts, "sources": sources,
            "policy": {"scan_interval_minutes": 60, "daily_digest_time": "17:30"},
        }
        errors = validate_profile(profile)
        if errors:
            raise ValueError("; ".join(errors))
        project.mkdir(parents=True, exist_ok=True)
        _write_profile_atomically(target, profile)
        return target
    except (EOFError, KeyboardInterrupt):
        output_fn("Initialization cancelled.")
        return None


def _parse_datetime(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO-8601 timestamp with timezone")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field} must be an ISO-8601 timestamp with timezone") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include timezone")
    return parsed


def _validate_observation(item: object, line_number: int) -> dict:
    if not isinstance(item, dict):
        raise ValueError(f"line {line_number}: observation must be an object")
    required_strings = ["entity_id", "entity_name", "source_uri", "evidence_text", "content_hash"]
    for key in required_strings:
        if not isinstance(item.get(key), str) or not item[key].strip():
            raise ValueError(f"line {line_number}: {key} is required")
    if item.get("signal_type") not in SIGNAL_TYPES:
        raise ValueError(f"line {line_number}: invalid signal_type")
    if "effective_date" not in item:
        raise ValueError(f"line {line_number}: effective_date is required")
    if "normalized_value" not in item:
        raise ValueError(f"line {line_number}: normalized_value is required")
    try:
        _parse_datetime(item.get("observed_at"), "observed_at")
    except ValueError as error:
        raise ValueError(f"line {line_number}: {error}") from error
    effective = item.get("effective_date")
    if effective is not None:
        if not isinstance(effective, str):
            raise ValueError(f"line {line_number}: effective_date must be YYYY-MM-DD or null")
        try:
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", effective) is None:
                raise ValueError
            datetime.strptime(effective, "%Y-%m-%d")
        except ValueError as error:
            raise ValueError(f"line {line_number}: effective_date must be YYYY-MM-DD or null") from error
    try:
        json.dumps(item.get("normalized_value"), sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError(f"line {line_number}: normalized_value must be JSON-compatible") from error
    for key in ("source_quality", "extraction_certainty"):
        value = item.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
            raise ValueError(f"line {line_number}: {key} must be numeric in [0,1]")
    for key, minimum, maximum in (("independent_sources", 1, None), ("impact", 1, 5), ("urgency", 1, 5)):
        value = item.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum or (maximum is not None and value > maximum):
            constraint = f">={minimum}" if maximum is None else f"in [{minimum},{maximum}]"
            raise ValueError(f"line {line_number}: {key} must be an integer {constraint}")
    status = item.get("source_status", "ok")
    if not isinstance(status, str) or not status:
        raise ValueError(f"line {line_number}: source_status must be a non-empty string")
    clean = dict(item)
    clean["source_status"] = status
    return clean


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant {value}")


def _load_observations_with_lines(path: Path) -> list[tuple[int, dict]]:
    """Read and validate JSONL observations while retaining physical line numbers."""
    observations: list[tuple[int, dict]] = []
    try:
        raw = Path(path).read_bytes()
    except OSError as error:
        raise ValueError(f"could not read observations: {error}") from error
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        line_number = raw[:error.start].count(b"\n") + 1
        raise ValueError(f"line {line_number}: invalid UTF-8 ({error.reason})") from error
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            decoded = json.loads(line, parse_constant=_reject_json_constant)
        except json.JSONDecodeError as error:
            raise ValueError(f"line {line_number}: invalid JSON ({error.msg})") from error
        except ValueError as error:
            raise ValueError(f"line {line_number}: invalid JSON ({error})") from error
        observations.append((line_number, _validate_observation(decoded, line_number)))
    return observations


def load_observations(path: Path) -> list[dict]:
    """Read and completely validate JSONL observations before state mutation."""
    return [observation for _, observation in _load_observations_with_lines(path)]


def calculate_confidence(observation: dict, now: datetime) -> float:
    observed_at = _parse_datetime(observation.get("observed_at"), "observed_at")
    if now.tzinfo is None:
        raise ValueError("now must include timezone")
    age = now.astimezone(timezone.utc) - observed_at.astimezone(timezone.utc)
    if age.total_seconds() < 0:
        raise ValueError("future observations are invalid")
    days = age.total_seconds() / 86400
    recency = 1.0 if days <= 7 else 0.8 if days <= 30 else 0.6 if days <= 90 else 0.3
    sources = observation["independent_sources"]
    corroboration = 1.0 if sources >= 2 else 0.6 if observation["source_quality"] >= 0.9 else 0.3
    confidence = 0.40 * observation["source_quality"] + 0.25 * observation["extraction_certainty"] + 0.20 * recency + 0.15 * corroboration
    return round(confidence, 4)


def classify_severity(confidence: float, impact: int, urgency: int) -> str | None:
    score = impact * urgency
    if confidence >= 0.75 and score >= 16:
        return "high"
    if confidence >= 0.55 and score >= 9:
        return "medium"
    if confidence >= 0.35:
        return "low"
    return None


def event_fingerprint(observation: dict) -> str:
    effective = observation.get("effective_date")
    month = effective[:7] if isinstance(effective, str) else None
    canonical = {"entity_id": observation["entity_id"], "signal_type": observation["signal_type"], "normalized_value": observation["normalized_value"], "effective_date_month": month}
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def _open_database(project: Path) -> sqlite3.Connection:
    state_dir = project / ".competitive-radar"
    state_dir.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(state_dir / "state.db", timeout=5)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY, trigger TEXT NOT NULL, ran_at TEXT NOT NULL, baseline INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS observations (id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES runs(id), entity_id TEXT NOT NULL, signal_type TEXT NOT NULL, normalized_value TEXT NOT NULL, content_hash TEXT NOT NULL, source_status TEXT NOT NULL, raw_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events (fingerprint TEXT PRIMARY KEY, severity TEXT, content_hash TEXT NOT NULL, first_run_id INTEGER NOT NULL REFERENCES runs(id), last_run_id INTEGER NOT NULL REFERENCES runs(id));
        CREATE TABLE IF NOT EXISTS deliveries (id INTEGER PRIMARY KEY, event_fingerprint TEXT NOT NULL REFERENCES events(fingerprint), created_at TEXT NOT NULL, status TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS digest_items (id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES runs(id), event_fingerprint TEXT NOT NULL REFERENCES events(fingerprint));
        CREATE INDEX IF NOT EXISTS observations_latest_baseline_idx ON observations (entity_id, signal_type, source_status, run_id DESC, id DESC);
    """)
    return connection


def _severity_rank(value: str | None) -> int:
    return {None: 0, "low": 1, "medium": 2, "high": 3}[value]


def evaluate(project: Path, observations_path: Path, trigger: str, now: datetime) -> dict:
    """Transactionally record observations and return deterministic event decisions."""
    numbered_observations = _load_observations_with_lines(observations_path)
    observations = [item for _, item in numbered_observations]
    # Validate all time-sensitive data before creating a run or observation row.
    for line_number, item in numbered_observations:
        try:
            calculate_confidence(item, now)
        except ValueError as error:
            raise ValueError(f"line {line_number}: {error}") from error
    connection = _open_database(Path(project))
    try:
        connection.execute("BEGIN IMMEDIATE")
        with connection:
            prior_runs = connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
            baseline = prior_runs == 0
            cursor = connection.execute("INSERT INTO runs (trigger, ran_at, baseline) VALUES (?, ?, ?)", (trigger, now.isoformat(), int(baseline)))
            run_id = cursor.lastrowid
            candidates: list[dict[str, Any]] = []
            suppressed: list[dict[str, Any]] = []
            recorded_only: list[dict[str, Any]] = []
            for item in observations:
                normalized = json.dumps(item["normalized_value"], sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                connection.execute("INSERT INTO observations (run_id, entity_id, signal_type, normalized_value, content_hash, source_status, raw_json) VALUES (?, ?, ?, ?, ?, ?, ?)", (run_id, item["entity_id"], item["signal_type"], normalized, item["content_hash"], item["source_status"], json.dumps(item, sort_keys=True, ensure_ascii=False)))
                if item["source_status"] != "ok":
                    recorded_only.append({"entity_id": item["entity_id"], "signal_type": item["signal_type"], "source_status": item["source_status"]})
                    continue
                previous = connection.execute("SELECT normalized_value FROM observations WHERE entity_id = ? AND signal_type = ? AND source_status = 'ok' AND run_id < ? ORDER BY id DESC LIMIT 1", (item["entity_id"], item["signal_type"], run_id)).fetchone()
                if baseline or previous is None:
                    continue
                confidence = calculate_confidence(item, now)
                severity = classify_severity(confidence, item["impact"], item["urgency"])
                fingerprint = event_fingerprint(item)
                existing = connection.execute("SELECT severity, content_hash FROM events WHERE fingerprint = ?", (fingerprint,)).fetchone()
                previous_value_changed = previous[0] != normalized
                if not previous_value_changed and existing is None:
                    continue
                if severity is None:
                    recorded_only.append({"entity_id": item["entity_id"], "signal_type": item["signal_type"], "reason": "below_alert_threshold"})
                    continue
                should_notify = previous_value_changed or existing is None or _severity_rank(severity) > _severity_rank(existing[0]) or item["content_hash"] != existing[1]
                payload = {"fingerprint": fingerprint, "entity_id": item["entity_id"], "entity_name": item["entity_name"], "signal_type": item["signal_type"], "confidence": confidence, "severity": severity, "observation": item}
                if should_notify:
                    candidates.append(payload)
                    if existing is None:
                        connection.execute("INSERT INTO events (fingerprint, severity, content_hash, first_run_id, last_run_id) VALUES (?, ?, ?, ?, ?)", (fingerprint, severity, item["content_hash"], run_id, run_id))
                    else:
                        connection.execute("UPDATE events SET severity = ?, content_hash = ?, last_run_id = ? WHERE fingerprint = ?", (severity, item["content_hash"], run_id, fingerprint))
                else:
                    suppressed.append(payload)
                    connection.execute("UPDATE events SET last_run_id = ? WHERE fingerprint = ?", (run_id, fingerprint))
            return {"schema_version": 1, "run_id": run_id, "trigger": trigger, "baseline": baseline, "candidates": candidates, "suppressed": suppressed, "recorded_only": recorded_only}
    finally:
        connection.close()


def _load_profile(project: Path) -> dict:
    path = Path(project) / "monitoring.yaml"
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise ValueError(f"could not load monitoring.yaml: {error}") from error
    if not isinstance(loaded, dict):
        raise ValueError("monitoring.yaml must contain a mapping")
    return loaded
