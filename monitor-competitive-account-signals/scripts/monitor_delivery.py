"""Publication and durable SMTP delivery for Competitive Account Radar."""

from __future__ import annotations

import json
import os
import re
import smtplib
import sqlite3
import tempfile
from datetime import date, datetime, time, timedelta
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from monitor_core import _open_database, _parse_datetime


COMMITMENT_TERMS = ("guaranteed discount", "approve discount", "承诺折扣", "保证降价", "合同已批准", "费用减免")
RETRY_MINUTES = (1, 5, 30, 120)


def validate_alert_draft(draft: dict, candidate_ids: set[str]) -> list[str]:
    """Validate the AI-authored alert contract without mutating state."""
    errors: list[str] = []
    if not isinstance(draft, dict):
        return ["draft must be an object"]
    if draft.get("schema_version") != "1.0": errors.append("schema_version must be '1.0'")
    if not isinstance(draft.get("run_id"), int): errors.append("run_id is required")
    alerts = draft.get("alerts")
    if not isinstance(alerts, list): return errors + ["alerts must be a list"]
    required_text = ("summary", "why_it_matters", "talk_track", "value_proposition", "assumptions", "escalation_condition")
    for index, alert in enumerate(alerts):
        prefix = f"alerts[{index}]"
        if not isinstance(alert, dict):
            errors.append(f"{prefix} must be an object"); continue
        if alert.get("event_id") not in candidate_ids: errors.append(f"{prefix}.event_id must reference an existing candidate")
        for key in required_text:
            if not isinstance(alert.get(key), str) or not alert[key].strip(): errors.append(f"{prefix}.{key} is required")
        evidence = alert.get("evidence_ids")
        if not isinstance(evidence, list) or not evidence or not all(isinstance(item, str) and item.strip() for item in evidence): errors.append(f"{prefix}.evidence_ids must be non-empty")
        confidence = alert.get("confidence")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1: errors.append(f"{prefix}.confidence must be numeric in [0,1]")
        for key in ("impact", "urgency"):
            if isinstance(alert.get(key), bool) or not isinstance(alert.get(key), int) or not 1 <= alert[key] <= 5: errors.append(f"{prefix}.{key} must be an integer in [1,5]")
        if alert.get("severity") not in {"high", "medium", "low"}: errors.append(f"{prefix}.severity must be high, medium, or low")
        for key in ("actions_24h", "actions_7d"):
            if not isinstance(alert.get(key), list) or not alert[key] or not all(isinstance(item, str) and item.strip() for item in alert[key]): errors.append(f"{prefix}.{key} must be non-empty")
        questions = alert.get("discovery_questions")
        if not isinstance(questions, list) or len(questions) != 3 or not all(isinstance(item, str) and item.strip() for item in questions): errors.append(f"{prefix}.discovery_questions must contain exactly three questions")
        text = json.dumps(alert, ensure_ascii=False).lower()
        if any(term.casefold() in text.casefold() for term in COMMITMENT_TERMS) and (not isinstance(alert.get("authorization_required"), str) or not alert["authorization_required"].strip()): errors.append(f"{prefix} commitment language requires authorization_required")
    return errors


def _load_profile(project: Path) -> dict:
    try:
        profile = yaml.safe_load((Path(project) / "monitoring.yaml").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise ValueError(f"could not load monitoring.yaml: {error}") from error
    if not isinstance(profile, dict): raise ValueError("monitoring.yaml must contain a mapping")
    return profile


def _ensure_schema(connection: sqlite3.Connection) -> None:
    columns = {row[1] for row in connection.execute("PRAGMA table_info(deliveries)")}
    for name, definition in (("kind", "TEXT NOT NULL DEFAULT 'immediate'"), ("recipient", "TEXT"), ("subject", "TEXT"), ("body", "TEXT"), ("attempts", "INTEGER NOT NULL DEFAULT 0"), ("next_attempt_at", "TEXT"), ("local_date", "TEXT"), ("delivered_at", "TEXT")):
        if name not in columns: connection.execute(f"ALTER TABLE deliveries ADD COLUMN {name} {definition}")


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
            temporary_path = Path(handle.name); handle.write(text); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            try: temporary_path.unlink()
            except FileNotFoundError: pass


def _candidate_rows(connection: sqlite3.Connection, run_id: int) -> dict[str, dict]:
    rows = connection.execute("""SELECT e.fingerprint, e.severity, o.raw_json FROM events e
        JOIN observations o ON o.run_id = e.last_run_id WHERE e.last_run_id = ?""", (run_id,)).fetchall()
    result = {}
    for event_id, severity, raw in rows:
        observation = json.loads(raw)
        result[event_id] = {"severity": severity, "observation": observation}
    return result


def _markdown(alerts: list[dict]) -> str:
    chunks = ["# 竞争与客户信号告警\n"]
    for alert in alerts:
        observation = alert["_candidate"]["observation"]
        chunks.append("\n".join((
            f"## [{alert['severity'].upper()}] {observation['entity_name']}：{alert['summary']}",
            f"- 事件：{alert['event_id']}", f"- 变化：{observation['signal_type']} / {json.dumps(observation['normalized_value'], ensure_ascii=False)}",
            f"- 证据：{observation['source_uri']}（{observation['observed_at']}；{', '.join(alert['evidence_ids'])}）",
            f"- 重要性：{alert['why_it_matters']}", f"- 24小时行动：{'；'.join(alert['actions_24h'])}", f"- 7天行动：{'；'.join(alert['actions_7d'])}",
            f"- 发现问题：{'；'.join(alert['discovery_questions'])}", f"- 沟通话术：{alert['talk_track']}", f"- 价值主张：{alert['value_proposition']}",
            f"- 假设：{alert['assumptions']}", f"- 升级条件：{alert['escalation_condition']}", f"- 投递状态：{alert['_delivery_state']}",
        )))
    return "\n".join(chunks) + "\n"


def _email_body(alert: dict) -> str:
    observation = alert["_candidate"]["observation"]
    return "\n".join((alert["summary"], f"实体：{observation['entity_name']}", f"变化：{observation['signal_type']}", f"证据：{observation['source_uri']} ({observation['observed_at']})", f"重要性：{alert['why_it_matters']}", f"24小时：{'；'.join(alert['actions_24h'])}", f"7天：{'；'.join(alert['actions_7d'])}"))


def publish(project: Path, draft_path: Path, now: datetime) -> dict:
    """Validate a draft, atomically publish it, and enqueue eligible deliveries."""
    project, draft_path = Path(project), Path(draft_path)
    try: draft = json.loads(draft_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error: raise ValueError(f"could not load draft: {error}") from error
    profile = _load_profile(project)
    connection = _open_database(project)
    try:
        _ensure_schema(connection)
        candidates = _candidate_rows(connection, draft.get("run_id") if isinstance(draft, dict) else -1)
        errors = validate_alert_draft(draft, set(candidates))
        for index, alert in enumerate(draft.get("alerts", []) if isinstance(draft, dict) else []):
            candidate = candidates.get(alert.get("event_id")) if isinstance(alert, dict) else None
            if candidate and set(alert.get("evidence_ids", [])) - {candidate["observation"]["content_hash"]}: errors.append(f"alerts[{index}].evidence_ids must be present in the candidate")
            if candidate and alert.get("severity") != candidate["severity"]: errors.append(f"alerts[{index}].severity must match the candidate")
        if errors: raise ValueError("; ".join(errors))
        alerts = []
        local_day = now.astimezone(ZoneInfo(profile["timezone"])).date().isoformat()
        for original in draft["alerts"]:
            alert = dict(original); alert["_candidate"] = candidates[alert["event_id"]]
            alert["_delivery_state"] = "digest_pending" if alert["severity"] == "low" else "pending"; alerts.append(alert)
        public = {"schema_version": "1.0", "run_id": draft["run_id"], "alerts": [{key: value for key, value in alert.items() if not key.startswith("_")} for alert in alerts]}
        run_dir = project / ".competitive-radar" / "runs" / str(draft["run_id"])
        # Stage both replacements before mutable delivery state is committed.
        _atomic_write(run_dir / "alerts.json", json.dumps(public, ensure_ascii=False, indent=2) + "\n")
        _atomic_write(run_dir / "alerts.md", _markdown(alerts))
        with connection:
            for alert in alerts:
                candidate = alert["_candidate"]; subject = f"[{alert['severity'].upper()}][{candidate['observation']['entity_name']}] {alert['summary']}"
                connection.execute("INSERT INTO deliveries (event_fingerprint, created_at, status, kind, recipient, subject, body, attempts, next_attempt_at, local_date) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)", (alert["event_id"], now.isoformat(), alert["_delivery_state"], "digest_source" if alert["severity"] == "low" else "immediate", profile["salesperson"]["alert_email"], subject, _email_body(alert), now.isoformat(), local_day))
        return {"run_id": draft["run_id"], "published": len(alerts), "queued": sum(alert["severity"] != "low" for alert in alerts)}
    finally: connection.close()


def build_digest(project: Path, digest_date: date, now: datetime) -> dict:
    project = Path(project); profile = _load_profile(project); local_now = now.astimezone(ZoneInfo(profile["timezone"]))
    scheduled = time.fromisoformat(profile["policy"]["daily_digest_time"])
    if local_now.date() != digest_date or local_now.timetz().replace(tzinfo=None) < scheduled: return {"queued": 0, "run_id": None}
    connection = _open_database(project)
    try:
        _ensure_schema(connection)
        rows = connection.execute("""SELECT d.id, d.event_fingerprint, d.body, e.last_run_id FROM deliveries d
            JOIN events e ON e.fingerprint = d.event_fingerprint
            WHERE d.status = 'digest_pending' AND d.local_date = ?""", (digest_date.isoformat(),)).fetchall()
        if not rows: return {"queued": 0, "run_id": None}
        subject = f"[DAILY][{digest_date.isoformat()}] 竞争与客户弱信号摘要"; body = "\n\n".join(row[2] for row in rows)
        with connection:
            connection.execute("INSERT INTO deliveries (event_fingerprint, created_at, status, kind, recipient, subject, body, attempts, next_attempt_at, local_date) VALUES (?, ?, 'pending', 'digest', ?, ?, ?, 0, ?, ?)", (rows[0][1], now.isoformat(), profile["salesperson"]["alert_email"], subject, body, now.isoformat(), digest_date.isoformat()))
            connection.executemany("UPDATE deliveries SET status = 'digested' WHERE id = ?", [(row[0],) for row in rows])
        return {"queued": 1, "run_id": rows[0][3]}
    finally: connection.close()


def _smtp_settings(profile: dict) -> tuple[str, int, str, str, str]:
    names = profile.get("smtp", {}).get("env", {})
    values = {key: os.environ.get(names.get(key, ""), "") for key in ("host", "port", "username", "password", "from")}
    missing = [str(names.get(key, key)) for key, value in values.items() if not value and key != "username"]
    if missing: raise ValueError("missing SMTP environment variables: " + ", ".join(missing))
    try: port = int(values["port"])
    except ValueError as error: raise ValueError("SMTP port environment variable must be an integer") from error
    return values["host"], port, values["username"], values["password"], values["from"]


def dispatch(project: Path, now: datetime, smtp_factory=smtplib.SMTP) -> dict:
    project = Path(project); profile = _load_profile(project)
    try: settings = _smtp_settings(profile)
    except ValueError as error: return {"attempted": 0, "delivered": 0, "error": str(error)}
    connection = _open_database(project)
    try:
        _ensure_schema(connection)
        rows = connection.execute("SELECT id, recipient, subject, body, attempts FROM deliveries WHERE status = 'pending' AND next_attempt_at <= ? ORDER BY id", (now.isoformat(),)).fetchall()
        if not rows: return {"attempted": 0, "delivered": 0}
        host, port, username, password, sender = settings; delivered = 0
        def record_failure(row: tuple) -> None:
            attempts = row[4] + 1
            with connection:
                if attempts >= 5:
                    connection.execute("UPDATE deliveries SET attempts = ?, status = 'failed', next_attempt_at = NULL WHERE id = ?", (attempts, row[0]))
                else:
                    retry_at = (now + timedelta(minutes=RETRY_MINUTES[attempts - 1])).isoformat()
                    connection.execute("UPDATE deliveries SET attempts = ?, next_attempt_at = ? WHERE id = ?", (attempts, retry_at, row[0]))
        try:
            with smtp_factory(host, port) as smtp:
                smtp.starttls()
                if username: smtp.login(username, password)
                for row in rows:
                    message = EmailMessage(); message["From"] = sender; message["To"] = row[1]; message["Subject"] = row[2]; message.set_content(row[3])
                    try:
                        smtp.send_message(message)
                    except Exception:
                        record_failure(row)
                    else:
                        with connection: connection.execute("UPDATE deliveries SET status = 'delivered', delivered_at = ? WHERE id = ?", (now.isoformat(), row[0]))
                        delivered += 1
        except Exception:
            # A connection-level failure consumes one scheduled attempt for rows still due.
            for row in rows:
                pending = connection.execute("SELECT 1 FROM deliveries WHERE id = ? AND status = 'pending' AND next_attempt_at <= ?", (row[0], now.isoformat())).fetchone()
                if pending: record_failure(row)
            return {"attempted": len(rows), "delivered": delivered, "error": "SMTP delivery failed; delivery remains pending"}
        return {"attempted": len(rows), "delivered": delivered}
    finally: connection.close()
