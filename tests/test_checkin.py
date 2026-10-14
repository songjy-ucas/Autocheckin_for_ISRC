import json
import os
import threading
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import checkin


class FakeNewApiHandler(BaseHTTPRequestHandler):
    mode = "success"
    post_count = 0

    def log_message(self, _format, *_args):
        pass

    def _send(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.mode == "unauthorized":
            self._send(401, {"success": False, "code": "AUTH_UNAUTHORIZED", "message": "Unauthorized"})
            return
        checked = self.mode == "already"
        self._send(
            200,
            {
                "success": True,
                "data": {
                    "enabled": True,
                    "stats": {"checked_in_today": checked},
                },
            },
        )

    def do_POST(self):
        type(self).post_count += 1
        self._send(
            200,
            {
                "success": True,
                "message": "签到成功",
                "data": {"quota_awarded": 12345},
            },
        )


class CheckinIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.previous_no_proxy = os.environ.get("NO_PROXY")
        existing = cls.previous_no_proxy or ""
        os.environ["NO_PROXY"] = ",".join(
            value for value in ("127.0.0.1", "localhost", existing) if value
        )
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeNewApiHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        host, port = cls.server.server_address
        cls.base_url = f"http://{host}:{port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        if cls.previous_no_proxy is None:
            os.environ.pop("NO_PROXY", None)
        else:
            os.environ["NO_PROXY"] = cls.previous_no_proxy

    def setUp(self):
        FakeNewApiHandler.mode = "success"
        FakeNewApiHandler.post_count = 0

    def test_successful_checkin(self):
        result = checkin.perform_checkin("test-token", base_url=self.base_url)
        self.assertEqual(result.status, "success")
        self.assertEqual(result.reward, 12345)
        self.assertEqual(FakeNewApiHandler.post_count, 1)

    def test_already_checked_in_does_not_post(self):
        FakeNewApiHandler.mode = "already"
        result = checkin.perform_checkin("test-token", base_url=self.base_url)
        self.assertEqual(result.status, "already")
        self.assertEqual(FakeNewApiHandler.post_count, 0)

    def test_status_only_does_not_post(self):
        result = checkin.perform_checkin("test-token", base_url=self.base_url, status_only=True)
        self.assertEqual(result.status, "status")
        self.assertFalse(result.checked_in)
        self.assertEqual(FakeNewApiHandler.post_count, 0)

    def test_unauthorized_is_reported(self):
        FakeNewApiHandler.mode = "unauthorized"
        result = checkin.perform_checkin("bad-token", base_url=self.base_url)
        self.assertEqual(result.status, "login_required")
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(FakeNewApiHandler.post_count, 0)


class UtilityTests(unittest.TestCase):
    def test_normalize_bearer_token(self):
        self.assertEqual(checkin.normalize_token("  Bearer abc123  "), "abc123")

    def test_parse_auto_time(self):
        self.assertEqual(checkin.parse_auto_time("8:10"), (8, 10))
        self.assertEqual(checkin.parse_auto_time("23:59"), (23, 59))
        with self.assertRaises(ValueError):
            checkin.parse_auto_time("24:00")
        with self.assertRaises(ValueError):
            checkin.parse_auto_time("noon")

    def test_task_xml_runs_daily_without_waking_computer(self):
        xml_bytes = checkin.build_task_xml(
            "12:00",
            user_sid="S-1-5-21-123",
            python_executable=Path("C:/Python/pythonw.exe"),
            script_path=Path("C:/Auto Checkin/checkin.py"),
            log_file=Path("C:/Auto Checkin/checkin.log"),
            now=datetime(2026, 10, 14, 9, 30),
        )
        root = ET.fromstring(xml_bytes)
        ns = {"t": checkin.TASK_XML_NS}
        self.assertEqual(
            root.findtext(".//t:StartBoundary", namespaces=ns),
            "2026-10-14T12:00:00",
        )
        self.assertEqual(root.findtext(".//t:DaysInterval", namespaces=ns), "1")
        self.assertEqual(root.findtext(".//t:StartWhenAvailable", namespaces=ns), "true")
        self.assertEqual(root.findtext(".//t:WakeToRun", namespaces=ns), "false")
        self.assertIn("--log-file", root.findtext(".//t:Arguments", namespaces=ns))

    @unittest.skipUnless(checkin.os.name == "nt", "Windows DPAPI only")
    def test_dpapi_round_trip(self):
        encrypted = checkin.protect_for_current_windows_user("secret-token")
        self.assertNotIn(b"secret-token", encrypted)
        self.assertEqual(checkin.unprotect_for_current_windows_user(encrypted), "secret-token")


if __name__ == "__main__":
    unittest.main()
