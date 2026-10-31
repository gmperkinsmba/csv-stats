import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request


class HttpBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        os.environ["OUTCOME_METRICS_DATA"] = cls.temp.name
        import server
        cls.app = server
        cls.httpd = cls.app.LocalServer(("127.0.0.1", 0), cls.app.Handler)
        cls.app.PORT = cls.httpd.server_port
        cls.url = f"http://127.0.0.1:{cls.httpd.server_port}"
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=2)
        cls.app.DB.close()
        cls.temp.cleanup()
        os.environ.pop("OUTCOME_METRICS_DATA", None)

    def request(self, path, headers=None, body=None):
        req = urllib.request.Request(self.url + path, data=body, headers=headers or {}, method="POST" if body is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=2) as res:
                return res.status, res.read()
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, exc.read()
            finally:
                exc.close()

    def test_wrong_host_rejected(self):
        status, _ = self.request("/api/health", {"Host":"evil.example"})
        self.assertEqual(status, 403)

    def test_post_requires_token_and_same_origin(self):
        status, _ = self.request("/api/definitions", {"Content-Type":"application/json"}, b"{}")
        self.assertEqual(status, 403)
        status, _ = self.request("/api/definitions", {"Content-Type":"application/json", "X-Local-Action":self.app.TOKEN,
                                                    "Origin":"http://evil.example"}, b"{}")
        self.assertEqual(status, 403)

    def test_csv_import_requires_type_and_validates_body(self):
        headers={"X-Local-Action":self.app.TOKEN,"Origin":self.url}
        status, _=self.request("/api/import", {**headers,"Content-Type":"text/csv"}, b"event_id,entity_id,event_type,occurred_at\n")
        self.assertEqual(status,400)
        status, _=self.request("/api/import", {**headers,"Content-Type":"application/json"}, b"{}")
        self.assertEqual(status, 400)
        upload={"file_name":"synthetic.csv","mapping":{"event_id":"ID","entity_id":"Account","event_type":"Step","occurred_at":"When"},
                "csv":"ID,Account,Step,When\n001,0002,visit,2026-01-01\n"}
        status, body=self.request("/api/import", {**headers,"Content-Type":"application/json"}, json.dumps(upload).encode())
        self.assertEqual(status, 200)
        result=json.loads(body)
        self.assertEqual((result["accepted"],result["duplicates"]),(1,0))
        raw=self.app.DB.execute("SELECT raw_json FROM provenance WHERE import_id=?",(result["import_id"],)).fetchone()[0]
        self.assertIn('"ID": "001"',raw)

    def test_synthetic_demo_reimports_then_adds_late_outcome(self):
        headers={"X-Local-Action":self.app.TOKEN,"Origin":self.url,"Content-Type":"application/json"}
        status, body=self.request("/api/demo", headers, b"{}")
        self.assertEqual(status,200)
        result=json.loads(body)
        self.assertGreater(result["first_import"]["accepted"],0)
        self.assertEqual(result["repeat_import"]["accepted"],0)
        self.assertEqual(result["repeat_import"]["duplicates"],result["first_import"]["accepted"])
        self.assertNotEqual(result["initial_run_id"],result["revised_run_id"])
        self.assertEqual(result["initial"]["definition_version"],result["revised"]["definition_version"])
        self.assertNotEqual(result["initial"]["converted"],result["revised"]["converted"])
        old_status, old_body=self.request("/api/run", {"X-Run-Id":str(result["initial_run_id"])})
        self.assertEqual(old_status,200)
        self.assertEqual(json.loads(old_body)["converted"],result["initial"]["converted"])


if __name__ == "__main__":
    unittest.main()
