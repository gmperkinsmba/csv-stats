"""Native policy and ephemeral backend lifecycle checks without opening a UI."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

import build_desktop
import install_desktop
from desktop_policy import navigation_policy


class DesktopTests(unittest.TestCase):
    def test_asset_manifest_rejects_traversal_and_symlinks(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "static").mkdir()
            (root / "static" / "app.js").write_text("ok", encoding="utf-8")
            manifest = root / "assets.txt"
            manifest.write_text("static/app.js\n", encoding="utf-8")
            with patch.object(build_desktop, "ROOT", root), patch.object(build_desktop, "ASSET_MANIFEST", manifest):
                self.assertEqual(build_desktop.read_assets()[0][0], "static/app.js")
                manifest.write_text("../outside.js\n", encoding="utf-8")
                with self.assertRaises(SystemExit):
                    build_desktop.read_assets()
                (root / "linked.js").symlink_to(root / "static" / "app.js")
                manifest.write_text("linked.js\n", encoding="utf-8")
                with self.assertRaises(SystemExit):
                    build_desktop.read_assets()

    def test_installer_refuses_unmarked_existing_application(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.app"
            target = root / "Applications" / install_desktop.APP_NAME
            source_resources = source / "Contents" / "Resources"
            source_resources.mkdir(parents=True)
            with (source / "Contents" / "Info.plist").open("wb") as stream:
                import plistlib
                plistlib.dump({"CFBundleIdentifier": install_desktop.BUNDLE_ID}, stream)
            (source_resources / install_desktop.OWNER_MARKER.name).write_bytes(install_desktop.OWNER_MARKER.read_bytes())
            target.mkdir(parents=True)
            sentinel = target / "keep.txt"
            sentinel.write_text("unrelated app", encoding="utf-8")
            with patch.object(install_desktop, "SOURCE", source), patch.object(install_desktop, "DESTINATION", target):
                with self.assertRaises(SystemExit):
                    install_desktop.main()
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "unrelated app")

    def test_notices_include_runtime_and_dependency_licenses(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "THIRD_PARTY_NOTICES.txt"
            build_desktop.write_notices(destination)
            notices = destination.read_text(encoding="utf-8")
        self.assertNotIn("\x00", notices)
        self.assertIn("Python runtime 3.12.14", notices)
        self.assertIn("Third-party license texts", notices)
        self.assertIn("The BSD License (BSD)", notices)

    def test_navigation_boundary(self):
        origin = "http://127.0.0.1:49125/"
        self.assertEqual(navigation_policy(origin, origin, False, False), "allow")
        self.assertEqual(navigation_policy("blob:" + origin + "export", origin, False, True), "download")
        for url in (
            "file:///etc/passwd", "javascript:alert(1)", "data:text/html,hello",
            "http://127.0.0.1:49126/", "http://127.0.0.1.evil.test:49125/",
            "http://user@127.0.0.1:49125/", "blob:https://evil.test/export",
        ):
            self.assertEqual(navigation_policy(url, origin, False, False), "cancel")
        self.assertEqual(navigation_policy("https://example.com", origin, True, False), "external")
        self.assertEqual(navigation_policy("https://example.com", origin, False, False), "cancel")

    def test_ephemeral_host_origin_token_demo_and_shutdown(self):
        script = r'''
import json, socket, threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch
import server

with patch("socket.getfqdn", side_effect=AssertionError("Reverse DNS must not delay app startup")):
    check = server.make_server(port=0)
    dns_skipped = check.server_name == "127.0.0.1" and check.server_port > 0
    check.server_close()
backend = server.make_server(port=0)
port = backend.server_port
worker = threading.Thread(target=backend.serve_forever, daemon=True)
worker.start()
def get(url, headers=None):
    try:
        with urlopen(Request(url, headers=headers or {}), timeout=3) as response:
            return response.status, response.read()
    except HTTPError as error:
        try:
            return error.code, error.read()
        finally:
            error.close()
def post(url, headers=None):
    try:
        with urlopen(Request(url, data=b"{}", method="POST", headers=headers or {}), timeout=5) as response:
            return response.status, response.read()
    except HTTPError as error:
        try:
            return error.code, error.read()
        finally:
            error.close()
try:
    status, body = get(f"http://127.0.0.1:{port}/api/bootstrap")
    bootstrap = json.loads(body)
    bad_host, _ = get(f"http://127.0.0.1:{port}/api/health", {"Host": "evil.test"})
    bad_origin, _ = get(f"http://127.0.0.1:{port}/api/health", {"Origin": "http://evil.test"})
    no_token, _ = post(f"http://127.0.0.1:{port}/api/demo", {"Content-Type": "application/json"})
    demo_status, demo_body = post(f"http://127.0.0.1:{port}/api/demo", {
        "Content-Type": "application/json", "X-Local-Action": bootstrap["token"],
        "Origin": f"http://127.0.0.1:{port}"})
    result = json.loads(demo_body)
finally:
    backend.shutdown()
    backend.server_close()
    worker.join(timeout=2)
with socket.socket() as probe:
    listener_closed = probe.connect_ex(("127.0.0.1", port)) != 0
print(json.dumps({"bootstrap": status, "host_rejected": bad_host, "origin_rejected": bad_origin,
                  "unauthorized": no_token, "demo": demo_status,
                  "duplicate_count": result["repeat_import"]["duplicates"],
                  "reverse_dns_skipped": dns_skipped,
                  "worker_stopped": not worker.is_alive(), "socket_closed": backend.fileno() == -1 and listener_closed}))
'''
        with tempfile.TemporaryDirectory(prefix="outcome-desktop-tests-") as runtime:
            env = {**os.environ, "OUTCOME_METRICS_DATA": runtime}
            completed = subprocess.run(
                [sys.executable, "-c", script],
                cwd=Path(__file__).resolve().parents[1],
                env=env,
                capture_output=True,
                text=True,
                timeout=15,
                check=True,
            )
        import json
        result = json.loads(completed.stdout.strip().splitlines()[-1])
        self.assertEqual(result["bootstrap"], 200)
        self.assertEqual((result["host_rejected"], result["origin_rejected"], result["unauthorized"]), (403, 403, 403))
        self.assertEqual(result["demo"], 200)
        self.assertGreater(result["duplicate_count"], 0)
        self.assertTrue(result["reverse_dns_skipped"])
        self.assertTrue(result["worker_stopped"])
        self.assertTrue(result["socket_closed"])


if __name__ == "__main__":
    unittest.main()
