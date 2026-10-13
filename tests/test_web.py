from __future__ import annotations

import hashlib
import hmac
import http.cookiejar
import json
import os
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener
from urllib.parse import urlencode

from xblago.core.storage import SQLiteBlagoStore, TelegramIdentity
from xblago.integrations.telegram import TelegramBot
from xblago.integrations.web import ApiError, BlagoWebHandler, WebApp, verify_telegram_init_data


class WebAppTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = SQLiteBlagoStore(Path(self.tmp.name) / "blago.sqlite3")
        self.web = WebApp(self.store)
        env = patch.dict(os.environ, {"XBLAGO_WEB_INVITE_CODE": "invite-only-test-code", "XBLAGO_LLM_PROVIDER": "local", "XBLAGO_COOKIE_SECURE": "false"})
        env.start()
        self.addCleanup(env.stop)

    def test_invitation_and_login_require_real_password(self) -> None:
        with self.assertRaises(ApiError) as rejected:
            self.web.register("person", "a-long-password", "incorrect")
        self.assertEqual(rejected.exception.status, 403)

        token, csrf = self.web.register("person", "a-long-password", "invite-only-test-code")
        uid, saved_csrf = self.web.session(token)
        self.assertEqual(saved_csrf, csrf)
        self.assertEqual(self.web.me(uid)["display_name"], "person")
        self.assertNotEqual(self.web.login("PERSON", "a-long-password")[0], token)
        with self.assertRaises(ApiError):
            self.web.login("person", "wrong-password")
        with self.assertRaises(ApiError):
            self.web.register("PERSON", "a-long-password", "invite-only-test-code")
        self.web.logout(token)
        with self.assertRaises(ApiError):
            self.web.session(token)

    def test_chat_memory_and_records_are_isolated(self) -> None:
        t1, _ = self.web.register("person1", "strong-password1", "invite-only-test-code")
        t2, _ = self.web.register("person2", "strong-password2", "invite-only-test-code")
        u1 = self.web.session(t1)[0]
        u2 = self.web.session(t2)[0]
        response = self.web.chat(u1, "запомни город = Краснодар")
        self.assertIn("Краснодар", response)
        self.assertEqual(self.store.all_memory(u1)["город"], "Краснодар")
        self.assertNotIn("город", self.store.all_memory(u2))
        self.assertTrue(self.web.history(u1))
        self.assertEqual(self.web.history(u2), [])
        self.web.create_item("needs", u1, {"title": "Помощь с ремонтом", "visibility": "community"})
        self.web.create_item("resources", u2, {"title": "Навыки разработки", "visibility": "private"})
        self.assertEqual(len(self.web.rows("needs", u1)), 1)
        self.assertEqual(len(self.web.rows("needs", u2)), 0)
        self.assertEqual(self.web.analytics(u1)["needs"], 1)
        self.assertEqual(self.web.analytics(u2)["resources"], 1)
        self.assertEqual(self.web.analytics(u1, community=True)["published_needs"], 1)
        self.assertEqual(self.web.analytics(u1, community=True)["published_resources"], 0)

    def test_profile_updates_and_rejects_unsupported_fields(self) -> None:
        token, _ = self.web.register("person", "a-long-password", "invite-only-test-code")
        uid = self.web.session(token)[0]
        self.assertEqual(self.web.update_me(uid, {"display_name": "Роман", "language": "ru"})["display_name"], "Роман")
        with self.assertRaises(ApiError):
            self.web.update_me(uid, {"blago_user_id": "someone-else"})

    def test_telegram_hmac_and_expiration(self) -> None:
        token = "1234:TEST_SECRET"
        user = '{"id":1001,"first_name":"User"}'
        params = {"user": user, "auth_date": str(int(time.time()))}
        check = "\n".join(f"{k}={v}" for k,v in sorted(params.items()))
        secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        params["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        raw = urlencode(params)
        self.assertEqual(verify_telegram_init_data(raw, token)["id"], 1001)
        with self.assertRaises(ApiError):
            verify_telegram_init_data(raw.replace("User", "Attacker"), token)
        params["auth_date"] = "1000000000"
        check = "\n".join(f"{k}={v}" for k,v in sorted(params.items()) if k!="hash")
        params["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        with self.assertRaises(ApiError):
            verify_telegram_init_data(urlencode(params), token)

    def test_one_time_telegram_link_merges_history_without_memory_loss(self) -> None:
        token, _ = self.web.register("person", "a-long-password", "invite-only-test-code")
        web_uid = self.web.session(token)[0]
        telegram = TelegramIdentity(telegram_user_id=2002, chat_id=2002, chat_type="private")
        tg_uid = self.store.resolve_telegram_user(telegram)
        self.store.set_memory(web_uid, "city", "Krasnodar")
        self.store.set_memory(tg_uid, "city", "Moscow")
        self.web.chat(web_uid, "Составь план")
        self.store.append_history(tg_uid, "user", "Telegram message")
        code = self.web.new_link_code(web_uid)
        self.assertEqual(self.web.redeem_telegram_link(tg_uid, code), web_uid)
        self.assertEqual(self.store.resolve_telegram_user(telegram), web_uid)
        self.assertEqual(self.web.analytics(web_uid, community=True)["participants"], 1)
        self.assertEqual(self.store.all_memory(web_uid)["city"], "Krasnodar")
        self.assertEqual(self.store.all_memory(web_uid)["telegram.city"], "Moscow")
        self.assertTrue(any(m["text"] == "Telegram message" for m in self.web.history(web_uid)))
        with self.assertRaises(ApiError):
            self.web.redeem_telegram_link(tg_uid, code)

    def test_web_http_api_and_csrf(self) -> None:
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), BlagoWebHandler)
        httpd.blago_web_app = self.web
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        base = f"http://127.0.0.1:{httpd.server_port}"
        opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))

        def request(path: str, method: str = "GET", payload: dict | None = None, csrf: str | None = None, origin: str | None = None):
            headers = {"Origin": base} if method != "GET" else {}
            if origin is not None:
                headers["Origin"] = origin
            if csrf:
                headers["X-CSRF-Token"] = csrf
            data = None
            if payload is not None:
                data = json.dumps(payload).encode()
                headers["Content-Type"] = "application/json"
            req = Request(base + path, data=data, headers=headers, method=method)
            try:
                with opener.open(req, timeout=4) as result:
                    return result.status, result.read().decode()
            except HTTPError as exc:
                return exc.code, exc.read().decode()

        self.assertEqual(request("/healthz")[0], 200)
        self.assertIn("BLAGO", request("/app")[1])
        self.assertEqual(request("/app/manifest.webmanifest")[0], 200)
        self.assertEqual(request("/api/v1/me")[0], 401)
        self.assertEqual(request("/api/v1/auth/register", "POST", {"username":"a_person","password":"a-long-password","invite":"invite-only-test-code"}, origin="https://attacker.example")[0], 403)
        status, body = request("/api/v1/auth/register", "POST", {"username":"a_person","password":"a-long-password","invite":"invite-only-test-code"})
        self.assertEqual(status, 200)
        csrf = json.loads(body)["csrf"]
        status, body = request("/api/v1/me")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["profile"]["display_name"], "a_person")
        self.assertEqual(request("/api/v1/needs", "POST", {"title":"Needed"})[0], 403)
        self.assertEqual(request("/api/v1/needs", "POST", {"title":"Real need"}, csrf)[0], 201)
        self.assertEqual(len(json.loads(request("/api/v1/needs")[1])["items"]), 1)
        self.assertEqual(request("/api/v1/auth/logout", "POST", {}, csrf)[0], 200)
        self.assertEqual(request("/api/v1/me")[0], 401)


if __name__ == "__main__":
    unittest.main()
