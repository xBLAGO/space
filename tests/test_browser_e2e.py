from __future__ import annotations

import http.cookiejar
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

from xblago.core.storage import SQLiteBlagoStore
from xblago.integrations.web import BlagoWebHandler, WebApp


class BrowserE2ETest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = SQLiteBlagoStore(Path(self.tmp.name) / "blago.sqlite3")
        self.web = WebApp(self.store)
        env = patch.dict(
            "os.environ",
            {
                "XBLAGO_WEB_INVITE_CODE": "release-e2e-invite",
                "XBLAGO_LLM_PROVIDER": "local",
                "XBLAGO_COOKIE_SECURE": "false",
            },
        )
        env.start()
        self.addCleanup(env.stop)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), BlagoWebHandler)
        self.httpd.blago_web_app = self.web
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join, 5)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"
        self.opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.csrf = ""

    def test_registration_chat_profile_needs_resources_analytics_and_pwa_shell(self) -> None:
        status, html = self.request("/app")
        self.assertEqual(status, 200)
        self.assertIn("Чат с BLAGO", html)
        self.assertIn("id=\"mobile-nav\"", html)
        self.assertIn("https://telegram.org/js/telegram-web-app.js", html)
        self.assertIn("@media(max-width:640px)", html)

        status, manifest = self.request("/app/manifest.webmanifest")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(manifest)["display"], "standalone")
        status, worker = self.request("/app/sw.js")
        self.assertEqual(status, 200)
        self.assertIn("Never cache authenticated API/data", worker)

        registered = self.json_request(
            "/api/v1/auth/register",
            {
                "username": "release_e2e",
                "password": "release-password-123",
                "invite": "release-e2e-invite",
            },
        )
        self.csrf = registered["csrf"]
        profile = self.json_request("/api/v1/me", method="GET")
        self.assertEqual(profile["profile"]["display_name"], "release_e2e")

        reply = self.json_request("/api/v1/chat/messages", {"text": "запомни релиз = готов"})
        self.assertIn("Запомнил", reply["reply"])
        history = self.json_request("/api/v1/chat/messages", method="GET")
        self.assertEqual(len(history["messages"]), 2)

        updated = self.json_request(
            "/api/v1/me",
            {"display_name": "Релиз", "language": "ru", "timezone": "Europe/Moscow"},
            method="PATCH",
        )
        self.assertEqual(updated["profile"]["display_name"], "Релиз")

        need = self.json_request(
            "/api/v1/needs",
            {
                "title": "Проверить релиз",
                "description": "Нужна финальная проверка",
                "visibility": "community",
            },
        )
        resource = self.json_request(
            "/api/v1/resources",
            {
                "title": "Навык проверки",
                "description": "Могу помогать с релизами",
                "visibility": "community",
            },
        )
        self.assertEqual(need["item"]["status"], "open")
        self.assertEqual(resource["item"]["visibility"], "community")

        personal = self.json_request("/api/v1/analytics/me", method="GET")["analytics"]
        community = self.json_request("/api/v1/analytics/community", method="GET")["analytics"]
        self.assertEqual(personal["needs"], 1)
        self.assertEqual(personal["resources"], 1)
        self.assertEqual(personal["memory_facts"], 1)
        self.assertEqual(community["published_needs"], 1)
        self.assertEqual(community["published_resources"], 1)

    def test_sqlite_backup_contains_user_data(self) -> None:
        token, _ = self.web.register("backup_user", "release-password-123", "release-e2e-invite")
        uid = self.web.session(token)[0]
        self.store.set_memory(uid, "release", "ready")

        backup_path = self.store.backup_database(Path(self.tmp.name) / "manual-backups")
        restored = SQLiteBlagoStore(backup_path)

        self.assertEqual(restored.all_memory(uid)["release"], "ready")

    def request(self, path: str, method: str = "GET", payload: dict | None = None) -> tuple[int, str]:
        headers = {"Origin": self.base} if method != "GET" else {}
        if self.csrf and method != "GET":
            headers["X-CSRF-Token"] = self.csrf
        data = None
        if payload is not None:
            data = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        req = Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=5) as response:
                return response.status, response.read().decode()
        except HTTPError as exc:
            return exc.code, exc.read().decode()

    def json_request(self, path: str, payload: dict | None = None, method: str = "POST") -> dict:
        status, body = self.request(path, method=method, payload=payload)
        self.assertLess(status, 400, body)
        return json.loads(body)


if __name__ == "__main__":
    unittest.main()
