import os
import tempfile
import unittest
import sqlite3

from metrics import connect, create_definition, definitions, get_run, import_csv, init_db, overview, run_metric


class MetricsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = connect(os.path.join(self.tmp.name, "test.sqlite3"))
        init_db(self.db)
        self.definition = create_definition(self.db, "Visit to apply", "visit", "apply", 14)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def import_text(self, body, name="events.csv"):
        return import_csv(self.db, body.encode(), name)

    def test_same_import_twice_deduplicates_and_keeps_provenance(self):
        csv = "event_id,entity_id,event_type,occurred_at,source\n0001,00007,visit,2026-01-01,feed\n0002,00007,apply,2026-01-03,feed\n"
        first = self.import_text(csv)
        second = self.import_text(csv)
        self.assertEqual((first["accepted"], first["duplicates"]), (2, 0))
        self.assertEqual((second["accepted"], second["duplicates"]), (0, 2))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 2)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM provenance").fetchone()[0], 4)
        self.assertEqual(self.db.execute("SELECT entity_id FROM events WHERE event_id='0001'").fetchone()[0], "00007")

    def test_late_outcome_revises_new_run_only_and_one_entity_counts_once(self):
        self.import_text("event_id,entity_id,event_type,occurred_at\n1,001,visit,2026-02-01\n2,001,visit,2026-02-02\n")
        old = run_metric(self.db, "funnel", 1, "2026-02-01", "2026-02-03", "2026-02-20")
        self.import_text("event_id,entity_id,event_type,occurred_at\n3,001,apply,2026-02-10\n")
        new = run_metric(self.db, "funnel", 1, "2026-02-01", "2026-02-03", "2026-02-20")
        reread = get_run(self.db, old["id"])
        self.assertEqual((old["entered"], old["converted"], old["rate"]), (1, 0, 0.0))
        self.assertEqual((new["entered"], new["converted"], new["rate"]), (1, 1, 1.0))
        self.assertEqual((reread["entered"], reread["converted"], reread["rate"]), (1, 0, 0.0))
        extra = [e for e in new["exclusions"] if e["reason"] == "Additional denominator event; entity enters once"]
        self.assertEqual(len(extra), 1)

    def test_pending_is_not_a_failed_outcome_and_not_in_rate_denominator(self):
        self.import_text("event_id,entity_id,event_type,occurred_at\n1,done,visit,2026-01-01\n2,done,apply,2026-01-02\n3,waiting,visit,2026-01-10\n")
        result = run_metric(self.db, "funnel", 1, "2026-01-01", "2026-01-10", "2026-01-20")
        self.assertEqual((result["entered"], result["mature"], result["pending"]), (2, 1, 1))
        self.assertEqual(result["rate"], 1.0)
        self.assertEqual({e["entity_id"]: e["state"] for e in result["entities"]}, {"done":"converted", "waiting":"pending"})

    def test_zero_missing_and_pending_are_distinct(self):
        self.import_text("event_id,entity_id,event_type,occurred_at\n1,zero,visit,2026-01-01\n2,pending,visit,2026-01-10\n")
        zero_pending = run_metric(self.db, "funnel", 1, "2026-01-01", "2026-01-10", "2026-01-20")
        zero = next(e for e in zero_pending["entities"] if e["entity_id"] == "zero")
        self.assertEqual(zero["state"], "zero")
        self.assertEqual(zero_pending["pending"], 1)
        empty = run_metric(self.db, "funnel", 1, "2025-12-01", "2025-12-31", "2026-01-20")
        self.assertEqual(empty["status"], "missing")
        self.assertIsNone(empty["rate"])

    def test_metric_definition_versions_are_immutable_and_runs_keep_version(self):
        first = run_metric(self.db, "funnel", 1, "2026-01-01", "2026-01-02", "2026-02-01")
        second_def = create_definition(self.db, "Visit to enroll", "visit", "enroll", 21)
        self.assertEqual(second_def["version"], 2)
        self.assertEqual([(d["version"], d["numerator_event"]) for d in definitions(self.db)], [(2,"enroll"),(1,"apply")])
        self.assertEqual(get_run(self.db, first["id"])["definition"]["version"], 1)
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE definitions SET name='rewritten' WHERE version=1")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE runs SET converted=9 WHERE id=?", (first["id"],))

    def test_global_earliest_denominator_wins_even_if_later_row_imported_first(self):
        self.import_text("event_id,entity_id,event_type,occurred_at\nlate,person,visit,2026-03-10\n")
        self.import_text("event_id,entity_id,event_type,occurred_at\nearly,person,visit,2026-02-01\n")
        result = run_metric(self.db, "funnel", 1, "2026-03-01", "2026-03-31", "2026-04-30")
        self.assertEqual(result["entered"], 0)

    def test_import_excludes_bad_fields_and_dates_but_keeps_valid_zero_id(self):
        result = self.import_text("event_id,entity_id,event_type,occurred_at\n0000,0000,visit,2026-01-01T10:15:30Z\n2,,visit,2026-01-02\n3,x,visit,2026-01-03junk\n")
        self.assertEqual((result["accepted"], result["excluded"]), (1, 2))
        row = self.db.execute("SELECT event_id,entity_id,occurred_at FROM events").fetchone()
        self.assertEqual(tuple(row), ("0000", "0000", "2026-01-01T10:15:30.000000+00:00"))

    def test_source_namespaces_are_distinct_and_conflicts_retained(self):
        result = self.import_text("event_id,entity_id,event_type,occurred_at,source\n1,a,visit,2026-01-01,x\n1,b,visit,2026-01-02,x\n1,b,visit,2026-01-02,y\n")
        self.assertEqual((result["accepted"], result["duplicates"]), (2, 1))
        self.assertEqual(self.db.execute("SELECT entity_id FROM events WHERE source_key='x'").fetchone()[0], "a")
        self.assertEqual(self.db.execute("SELECT status FROM provenance WHERE import_id=? ORDER BY row_number", (result["import_id"],)).fetchall()[1][0], "duplicate conflict; original retained")

    def test_same_day_outcome_before_entry_is_excluded_and_after_entry_counts(self):
        self.import_text("event_id,entity_id,event_type,occurred_at\n1,x,visit,2026-01-01T09:00:00Z\n2,x,apply,2026-01-01T08:59:00Z\n3,x,apply,2026-01-01T09:01:00Z\n")
        result=run_metric(self.db,"funnel",1,"2026-01-01","2026-01-01","2026-01-20")
        self.assertEqual((result["converted"],result["rate"]),(1,1.0))
        self.assertEqual(result["entities"][0]["outcome_event"],"3")
        self.assertEqual(result["exclusions"][0]["reason"],"Outcome outside observation window")

    def test_timezone_offset_is_normalized_before_event_ordering(self):
        self.import_text("event_id,entity_id,event_type,occurred_at\n1,x,visit,2026-01-01T23:30:00-02:00\n2,x,apply,2026-01-02T01:00:00Z\n")
        result=run_metric(self.db,"funnel",1,"2026-01-02","2026-01-02","2026-01-20")
        self.assertEqual(result["entities"][0]["entry_at"],"2026-01-02T01:30:00.000000+00:00")
        self.assertEqual((result["converted"],result["rate"]),(0,0.0))
        self.assertEqual(result["exclusions"][0]["reason"],"Outcome outside observation window")

    def test_malformed_csv_quotes_are_rejected_before_import(self):
        with self.assertRaisesRegex(ValueError,"CSV quoting"):
            self.import_text('event_id,entity_id,event_type,occurred_at\n1,x,"visit,2026-01-01\n')
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM imports").fetchone()[0],0)

    def test_import_mapping_preserves_original_fields_and_hashes_source_csv(self):
        body=b"ID,Account,Step,When,Campaign\n0009,00004,visit,2026-01-01,early\n"
        result=import_csv(self.db,body,"export.csv",{"event_id":"ID","entity_id":"Account","event_type":"Step","occurred_at":"When","source":"Campaign"})
        self.assertEqual(result["sha256"],__import__("hashlib").sha256(body).hexdigest())
        source_row=self.db.execute("SELECT raw_json FROM provenance WHERE import_id=?",(result["import_id"],)).fetchone()[0]
        self.assertIn('"Campaign": "early"',source_row)
        self.assertEqual(tuple(self.db.execute("SELECT event_id,entity_id,source_key FROM events").fetchone()),("0009","00004","early"))
        self.assertNotIn("raw_json", overview(self.db)["provenance"][0])


if __name__ == "__main__":
    unittest.main()
