"""Duplicate-safe event storage and immutable cohort metric runs."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

REQUIRED = ("event_id", "entity_id", "event_type", "occurred_at")


def connect(path: str) -> sqlite3.Connection:
    db = sqlite3.connect(path, check_same_thread=False, timeout=5)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA journal_mode=WAL")
    return db


def init_db(db: sqlite3.Connection) -> None:
    db.executescript("""
    CREATE TABLE IF NOT EXISTS imports(
      id INTEGER PRIMARY KEY, source_name TEXT NOT NULL, sha256 TEXT NOT NULL,
      imported_at TEXT NOT NULL, accepted INTEGER NOT NULL DEFAULT 0,
      duplicates INTEGER NOT NULL DEFAULT 0, excluded INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS events(
      id INTEGER PRIMARY KEY, source_key TEXT NOT NULL, event_id TEXT NOT NULL,
      entity_id TEXT NOT NULL, event_type TEXT NOT NULL, occurred_at TEXT NOT NULL,
      UNIQUE(source_key,event_id)
    );
    CREATE TABLE IF NOT EXISTS provenance(
      id INTEGER PRIMARY KEY, import_id INTEGER NOT NULL REFERENCES imports(id),
      row_number INTEGER NOT NULL, source_key TEXT, event_id TEXT, event_row_id INTEGER REFERENCES events(id),
      status TEXT NOT NULL, raw_json TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS definitions(
      metric_key TEXT NOT NULL, version INTEGER NOT NULL, name TEXT NOT NULL,
      denominator_event TEXT NOT NULL, numerator_event TEXT NOT NULL,
      observation_days INTEGER NOT NULL, created_at TEXT NOT NULL,
      PRIMARY KEY(metric_key,version)
    );
    CREATE TABLE IF NOT EXISTS runs(
      id INTEGER PRIMARY KEY, metric_key TEXT NOT NULL, definition_version INTEGER NOT NULL,
      definition_json TEXT NOT NULL, cohort_start TEXT NOT NULL, cohort_end TEXT NOT NULL,
      as_of TEXT NOT NULL, created_at TEXT NOT NULL, status TEXT NOT NULL,
      entered INTEGER NOT NULL, converted INTEGER NOT NULL, mature INTEGER NOT NULL,
      pending INTEGER NOT NULL, rate REAL,
      FOREIGN KEY(metric_key,definition_version) REFERENCES definitions(metric_key,version)
    );
    CREATE TABLE IF NOT EXISTS run_entities(
      run_id INTEGER NOT NULL REFERENCES runs(id), entity_id TEXT NOT NULL,
      entry_event_id INTEGER REFERENCES events(id), outcome_event_id INTEGER REFERENCES events(id),
      state TEXT NOT NULL, exclusion TEXT, PRIMARY KEY(run_id,entity_id)
    );
    CREATE TABLE IF NOT EXISTS run_exclusions(
      id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES runs(id),
      event_row_id INTEGER REFERENCES events(id), entity_id TEXT, reason TEXT NOT NULL,
      UNIQUE(run_id,event_row_id,reason)
    );
    CREATE INDEX IF NOT EXISTS event_type_date ON events(event_type,occurred_at);
    CREATE INDEX IF NOT EXISTS event_entity_date ON events(entity_id,occurred_at);
    CREATE TRIGGER IF NOT EXISTS definitions_no_update BEFORE UPDATE ON definitions BEGIN SELECT RAISE(ABORT,'metric definitions are immutable'); END;
    CREATE TRIGGER IF NOT EXISTS definitions_no_delete BEFORE DELETE ON definitions BEGIN SELECT RAISE(ABORT,'metric definitions are immutable'); END;
    CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT,'events are immutable'); END;
    CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT,'events are immutable'); END;
    CREATE TRIGGER IF NOT EXISTS runs_no_update BEFORE UPDATE ON runs BEGIN SELECT RAISE(ABORT,'metric runs are immutable'); END;
    CREATE TRIGGER IF NOT EXISTS runs_no_delete BEFORE DELETE ON runs BEGIN SELECT RAISE(ABORT,'metric runs are immutable'); END;
    CREATE TRIGGER IF NOT EXISTS run_entities_no_update BEFORE UPDATE ON run_entities BEGIN SELECT RAISE(ABORT,'run snapshots are immutable'); END;
    CREATE TRIGGER IF NOT EXISTS run_entities_no_delete BEFORE DELETE ON run_entities BEGIN SELECT RAISE(ABORT,'run snapshots are immutable'); END;
    CREATE TRIGGER IF NOT EXISTS run_exclusions_no_update BEFORE UPDATE ON run_exclusions BEGIN SELECT RAISE(ABORT,'run snapshots are immutable'); END;
    CREATE TRIGGER IF NOT EXISTS run_exclusions_no_delete BEFORE DELETE ON run_exclusions BEGIN SELECT RAISE(ABORT,'run snapshots are immutable'); END;
    """)
    db.commit()


def parse_date(value: str) -> date:
    value = value.strip()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if "T" not in value and " " not in value:
            return date.fromisoformat(value)
        return parsed.date()
    except ValueError:
        raise ValueError("Use a full ISO date or timestamp")


def normalize_time(value: str) -> str:
    value = value.strip()
    try:
        if "T" not in value and " " not in value:
            parsed = datetime.combine(date.fromisoformat(value), time.min, tzinfo=timezone.utc)
        else:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            parsed = parsed.astimezone(timezone.utc)
        return parsed.isoformat(timespec="microseconds")
    except ValueError as exc:
        raise ValueError("Use a full ISO date or timestamp") from exc


def import_csv(db: sqlite3.Connection, body: bytes, source_name: str, mapping: dict[str, str] | None = None) -> dict[str, Any]:
    if len(body) > 1_000_000:
        raise ValueError("CSV exceeds the 1 MB limit")
    text = body.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text), strict=True)
    if mapping is None:
        mapping = {k: k for k in REQUIRED}
        if "source" in (reader.fieldnames or []):
            mapping["source"] = "source"
    if set(mapping) - {*REQUIRED, "source"}:
        raise ValueError("Column map contains an unknown event field")
    if not reader.fieldnames or len(reader.fieldnames) > 100 or len(set(reader.fieldnames)) != len(reader.fieldnames) or any(not h.strip() for h in reader.fieldnames):
        raise ValueError("CSV needs unique, named columns, with no more than 100 fields")
    if any(not isinstance(mapping.get(c), str) or mapping[c] not in reader.fieldnames for c in REQUIRED):
        raise ValueError("CSV needs event_id, entity_id, event_type, and occurred_at columns")
    if mapping.get("source") not in (None, "") and mapping["source"] not in reader.fieldnames:
        raise ValueError("Mapped source column was not found")
    required_columns = [mapping[c] for c in REQUIRED]
    if len(set(required_columns)) != len(REQUIRED) or (mapping.get("source") and mapping["source"] in required_columns):
        raise ValueError("Map each event field to a different CSV column")
    required_columns = [mapping[c] for c in REQUIRED]
    if len(set(required_columns)) != len(REQUIRED) or (mapping.get("source") and mapping["source"] in required_columns):
        raise ValueError("Map each event field to a different CSV column")
    try:
        input_rows = []
        for row in reader:
            if len(input_rows) >= 50_000:
                raise ValueError("CSV exceeds the 50,000 row limit")
            input_rows.append(row)
    except csv.Error as exc:
        raise ValueError("CSV quoting or row structure is invalid") from exc
    digest = hashlib.sha256(body).hexdigest()
    stamp = datetime.now(timezone.utc).isoformat()
    cur = db.execute("INSERT INTO imports(source_name,sha256,imported_at) VALUES(?,?,?)",
                     (source_name[:160], digest, stamp))
    import_id = cur.lastrowid
    accepted = duplicates = excluded = 0
    for row_num, row in enumerate(input_rows, start=2):
        safe_raw = json.dumps(row, ensure_ascii=False)
        event_id = (row.get(mapping["event_id"]) or "").strip()
        entity_id = (row.get(mapping["entity_id"]) or "").strip()
        event_type = (row.get(mapping["event_type"]) or "").strip()
        raw_date = (row.get(mapping["occurred_at"]) or "").strip()
        source_column = mapping.get("source")
        source = ((row.get(source_column) if source_column else None) or source_name or "default").strip()[:160]
        reason = None
        if not event_id or not entity_id or not event_type or not raw_date:
            reason = "Missing required field"
        elif len(event_id) > 256 or len(entity_id) > 256 or len(event_type) > 120:
            reason = "Field exceeds allowed length"
        else:
            try:
                parsed = parse_date(raw_date)
                normalized_time = normalize_time(raw_date)
            except (TypeError, ValueError):
                reason = "Invalid occurred_at date"
        if reason:
            excluded += 1
            db.execute("INSERT INTO provenance(import_id,row_number,source_key,event_id,status,raw_json) VALUES(?,?,?,?,?,?)",
                       (import_id, row_num, source, event_id or None, "excluded: " + reason, safe_raw))
            continue
        result = db.execute("INSERT OR IGNORE INTO events(source_key,event_id,entity_id,event_type,occurred_at) VALUES(?,?,?,?,?)",
                            (source, event_id, entity_id, event_type, normalized_time))
        if result.rowcount:
            accepted += 1
            event_row_id = result.lastrowid
            status = "accepted"
        else:
            duplicates += 1
            found = db.execute("SELECT id,entity_id,event_type,occurred_at FROM events WHERE source_key=? AND event_id=?",
                               (source, event_id)).fetchone()
            event_row_id = found["id"]
            status = "duplicate" if (found["entity_id"], found["event_type"], found["occurred_at"]) == (entity_id, event_type, normalized_time) else "duplicate conflict; original retained"
        db.execute("INSERT INTO provenance(import_id,row_number,source_key,event_id,event_row_id,status,raw_json) VALUES(?,?,?,?,?,?,?)",
                   (import_id, row_num, source, event_id, event_row_id, status, safe_raw))
    db.execute("UPDATE imports SET accepted=?,duplicates=?,excluded=? WHERE id=?", (accepted, duplicates, excluded, import_id))
    db.commit()
    return {"import_id": import_id, "sha256": digest, "accepted": accepted, "duplicates": duplicates, "excluded": excluded}


def create_definition(db: sqlite3.Connection, name: str, denominator: str, numerator: str, days: int,
                      metric_key: str = "funnel") -> dict[str, Any]:
    if not all(isinstance(x, str) for x in (name, denominator, numerator)):
        raise ValueError("Metric name and event types must be text")
    name, denominator, numerator = name.strip(), denominator.strip(), numerator.strip()
    if not name or len(name) > 100 or not denominator or not numerator or len(denominator) > 120 or len(numerator) > 120:
        raise ValueError("Enter a name and valid event types")
    if denominator == numerator:
        raise ValueError("Denominator and numerator event types must differ")
    if type(days) is not int or not 1 <= days <= 365:
        raise ValueError("Observation window must be between 1 and 365 days")
    version = db.execute("SELECT COALESCE(MAX(version),0)+1 FROM definitions WHERE metric_key=?", (metric_key,)).fetchone()[0]
    created = datetime.now(timezone.utc).isoformat()
    db.execute("INSERT INTO definitions VALUES(?,?,?,?,?,?,?)", (metric_key, version, name, denominator, numerator, days, created))
    db.commit()
    return dict(metric_key=metric_key, version=version, name=name, denominator_event=denominator,
                numerator_event=numerator, observation_days=days, created_at=created)


def definitions(db: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(r) for r in db.execute("SELECT * FROM definitions ORDER BY metric_key,version DESC")]


def run_metric(db: sqlite3.Connection, metric_key: str, version: int, start: str, end: str, as_of: str) -> dict[str, Any]:
    if metric_key != "funnel" or type(version) is not int or version < 1:
        raise ValueError("Choose a valid metric definition version")
    start_d, end_d, asof_d = date.fromisoformat(start), date.fromisoformat(end), date.fromisoformat(as_of)
    if end_d < start_d or asof_d < end_d:
        raise ValueError("Cohort end must follow its start, and as-of must be on or after cohort end")
    d = db.execute("SELECT * FROM definitions WHERE metric_key=? AND version=?", (metric_key, version)).fetchone()
    if not d:
        raise ValueError("Metric definition version was not found")
    # Entry and outcome candidates use original immutable event facts, never import-repaired values.
    start_at = normalize_time(start_d.isoformat())
    end_exclusive = normalize_time((end_d + timedelta(days=1)).isoformat())
    asof_exclusive = normalize_time((asof_d + timedelta(days=1)).isoformat())
    entrants = db.execute("SELECT e.* FROM events e WHERE e.event_type=? AND e.occurred_at>=? AND e.occurred_at<? AND NOT EXISTS(SELECT 1 FROM events earlier WHERE earlier.entity_id=e.entity_id AND earlier.event_type=e.event_type AND (earlier.occurred_at<e.occurred_at OR (earlier.occurred_at=e.occurred_at AND earlier.id<e.id))) ORDER BY e.entity_id,e.occurred_at,e.id",
      (d["denominator_event"], start_at, end_exclusive)).fetchall()
    now = datetime.now(timezone.utc).isoformat()
    definition = dict(d)
    entered = converted = mature = pending = 0
    run_rows = []
    in_scope: set[int] = set()
    for entry in entrants:
        entered += 1
        entry_time = datetime.fromisoformat(entry["occurred_at"])
        deadline = entry_time + timedelta(days=int(d["observation_days"]))
        is_mature = deadline < datetime.fromisoformat(asof_exclusive)
        if is_mature:
            mature += 1
        else:
            pending += 1
        outcome = db.execute("SELECT * FROM events WHERE entity_id=? AND event_type=? AND occurred_at>=? AND occurred_at<=? ORDER BY occurred_at,id LIMIT 1",
                             (entry["entity_id"], d["numerator_event"], entry["occurred_at"], deadline.isoformat(timespec="microseconds"))).fetchone()
        # A pending cohort is never scored yet, even if an outcome arrived early.
        state = "pending" if not is_mature else ("converted" if outcome else "zero")
        selected_outcome = outcome if is_mature else None
        if state == "converted":
            converted += 1
        run_rows.append((entry, selected_outcome, state, deadline))
        in_scope.add(entry["id"])
        if selected_outcome:
            in_scope.add(selected_outcome["id"])
    denominator = entered
    rate = converted / mature if mature else None
    status = "missing" if not denominator else ("pending" if mature == 0 else ("complete with pending" if pending else "complete"))
    cur = db.execute("INSERT INTO runs(metric_key,definition_version,definition_json,cohort_start,cohort_end,as_of,created_at,status,entered,converted,mature,pending,rate) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (metric_key, version, json.dumps(definition), start, end, as_of, now, status, entered, converted, mature, pending, rate))
    run_id = cur.lastrowid
    for entry, outcome, state, deadline in run_rows:
        db.execute("INSERT INTO run_entities VALUES(?,?,?,?,?,?)", (run_id, entry["entity_id"], entry["id"], outcome["id"] if outcome else None, state, None))
    # Explain events that did not contribute: additional entry, out of window, pending, or no matching cohort.
    all_events = db.execute("SELECT * FROM events WHERE event_type IN (?,?) ORDER BY occurred_at,id", (d["denominator_event"], d["numerator_event"])).fetchall()
    entity_entries = {e["entity_id"]: e for e in entrants}
    first_denoms = db.execute("SELECT e.* FROM events e WHERE e.event_type=? AND NOT EXISTS(SELECT 1 FROM events earlier WHERE earlier.entity_id=e.entity_id AND earlier.event_type=e.event_type AND (earlier.occurred_at<e.occurred_at OR (earlier.occurred_at=e.occurred_at AND earlier.id<e.id)))", (d["denominator_event"],)).fetchall()
    first_by_entity = {e["entity_id"]: e for e in first_denoms}
    for ev in all_events:
        if ev["id"] in in_scope:
            continue
        ent = entity_entries.get(ev["entity_id"])
        if ev["event_type"] == d["denominator_event"] and ent:
            reason = "Additional denominator event; entity enters once"
        elif ev["event_type"] == d["denominator_event"] and first_by_entity.get(ev["entity_id"]) and ev["id"] != first_by_entity[ev["entity_id"]]["id"]:
            first = first_by_entity.get(ev["entity_id"])
            reason = "Entity entered before selected cohort period; later event ignored" if first and first["occurred_at"] < start_at else "Later denominator event is outside this cohort"
        elif ev["event_type"] == d["denominator_event"]:
            reason = "First denominator event is outside selected cohort period"
        elif ev["event_type"] == d["numerator_event"] and ent:
            entry_time = datetime.fromisoformat(ent["occurred_at"])
            limit = entry_time + timedelta(days=int(d["observation_days"]))
            if limit >= datetime.fromisoformat(asof_exclusive):
                reason = "Cohort is not yet observed"
            elif datetime.fromisoformat(ev["occurred_at"]) < entry_time or datetime.fromisoformat(ev["occurred_at"]) > limit:
                reason = "Outcome outside observation window"
            elif any(row[0]["entity_id"] == ev["entity_id"] and row[1] for row in run_rows):
                reason = "Additional outcome event; entity counts once"
            else:
                reason = "Not counted in this run"
        else:
            first = first_by_entity.get(ev["entity_id"])
            reason = "No denominator event for this entity" if not first else "Entity entered outside selected cohort period"
        db.execute("INSERT OR IGNORE INTO run_exclusions(run_id,event_row_id,entity_id,reason) VALUES(?,?,?,?)",
                   (run_id, ev["id"], ev["entity_id"], reason))
    db.commit()
    return get_run(db, run_id)


def get_run(db: sqlite3.Connection, run_id: int) -> dict[str, Any]:
    row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    if not row:
        raise ValueError("Run was not found")
    result = dict(row)
    result["definition"] = json.loads(result.pop("definition_json"))
    result["entities"] = [dict(x) for x in db.execute("SELECT re.*,e.event_id AS entry_event,e.event_type AS entry_type,e.occurred_at AS entry_at,o.event_id AS outcome_event,o.event_type AS outcome_type,o.occurred_at AS outcome_at FROM run_entities re LEFT JOIN events e ON e.id=re.entry_event_id LEFT JOIN events o ON o.id=re.outcome_event_id WHERE re.run_id=? ORDER BY re.entity_id", (run_id,))]
    result["exclusions"] = [dict(x) for x in db.execute("SELECT x.*,e.event_id,e.event_type,e.occurred_at FROM run_exclusions x LEFT JOIN events e ON e.id=x.event_row_id WHERE x.run_id=? ORDER BY e.occurred_at,e.event_id", (run_id,))]
    return result


def overview(db: sqlite3.Connection) -> dict[str, Any]:
    counts = db.execute("SELECT COUNT(*) events, COUNT(DISTINCT entity_id) entities FROM events").fetchone()
    imports = [dict(r) for r in db.execute("SELECT * FROM imports ORDER BY id DESC LIMIT 10")]
    runs = [dict(r) for r in db.execute("SELECT id,metric_key,definition_version,cohort_start,cohort_end,as_of,created_at,status,entered,converted,mature,pending,rate FROM runs ORDER BY id DESC LIMIT 12")]
    provenance = [dict(r) for r in db.execute("SELECT p.import_id,p.row_number,p.status,p.source_key,p.event_id,i.source_name,i.imported_at FROM provenance p JOIN imports i ON i.id=p.import_id ORDER BY p.id DESC LIMIT 40")]
    return {"counts": dict(counts), "imports": imports, "runs": runs, "provenance": provenance}
