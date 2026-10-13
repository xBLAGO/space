from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from xblago.core.storage import SQLiteBlagoStore
from xblago.integrations.web import ApiError, WebApp


class EconomyProtocolTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = SQLiteBlagoStore(Path(self.tmp.name) / "blago.sqlite3")
        self.web = WebApp(self.store)
        env = patch.dict(
            "os.environ",
            {
                "XBLAGO_WEB_INVITE_CODE": "invite-only-test-code",
                "XBLAGO_LLM_PROVIDER": "local",
                "XBLAGO_COOKIE_SECURE": "false",
            },
        )
        env.start()
        self.addCleanup(env.stop)

    def test_asset_registration_is_self_declared_and_private(self) -> None:
        owner = self._register("owner")
        stranger = self._register("stranger")

        asset = self.web.create_asset(
            owner,
            {
                "title": "Генератор",
                "description": "Можно использовать после отдельного согласия",
                "asset_type": "tool",
                "ownership_claim": "self_declared",
                "use_permission": "permission_required",
                "management_status": "not_under_management",
            },
        )

        self.assertEqual(asset["verification_status"], "self_declared_unverified")
        self.assertEqual(asset["management_status"], "not_under_management")
        self.assertEqual(self.web.assets(stranger), [])

    def test_full_external_payment_simulation_without_real_money(self) -> None:
        requester = self._register("requester")
        helper = self._register("helper")
        need = self.web.create_item(
            "needs",
            requester,
            {"title": "Оплатить счёт за лекарства", "visibility": "community"},
        )
        self.web.create_resource_offer(
            helper,
            {
                "title": "Могу вручную оплатить небольшой счёт",
                "offer_type": "external_payment",
                "visibility": "community",
                "max_amount_minor": 5000_00,
                "currency": "RUB",
                "constraints": {"max_per_week": 1},
            },
        )
        request = self.web.create_payment_request(
            requester,
            {
                "need_id": need["id"],
                "title": "Счёт аптеки",
                "description": "Нужна помощь с внешней оплатой",
                "amount_minor": 1200_00,
                "currency": "RUB",
                "payment_purpose": "Счёт аптеки за лекарства",
                "payment_reference": "masked-reference-only",
            },
        )

        offered = self.web.offer_payment_request(requester, request["id"])
        incoming = self.web.incoming_payment_offers(helper)
        accepted = self.web.accept_payment_offer(helper, incoming[0]["id"])
        simulated = self.web.simulate_payment(helper, request["id"], {"note": "Симуляция оплаты"})
        completed = self.web.confirm_fulfillment(
            requester,
            request["id"],
            {"note": "Получатель вручную подтвердил", "xblago_rate": 0.25, "exchange_rate_source": "manual-test"},
        )
        events = self.web.economy_events(requester, request["id"])
        analytics = self.web.economy_analytics(helper)

        self.assertEqual(offered["request"]["status"], "offered")
        self.assertEqual(accepted["status"], "accepted")
        self.assertEqual(simulated["status"], "verification_pending")
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(len(events["contribution_events"]), 1)
        self.assertEqual(events["contribution_events"][0]["amount_minor"], 1200_00)
        self.assertEqual(analytics["recorded_contributions"], 1)
        self.assertGreater(analytics["xblago_statistical_estimate"], 0)

    def test_competing_acceptance_allows_only_one_executor(self) -> None:
        requester = self._register("requester")
        first = self._register("first")
        second = self._register("second")
        for uid in (first, second):
            self.web.create_resource_offer(
                uid,
                {
                    "title": "Готов помочь оплатой",
                    "offer_type": "external_payment",
                    "visibility": "community",
                    "max_amount_minor": 10_000_00,
                    "currency": "RUB",
                },
            )
        request = self.web.create_payment_request(
            requester,
            {
                "title": "Счёт",
                "amount_minor": 1000_00,
                "currency": "RUB",
                "payment_purpose": "Допустимый счёт",
            },
        )

        self.web.offer_payment_request(requester, request["id"], limit=2)
        first_offer = self.web.incoming_payment_offers(first)[0]
        second_offer = self.web.incoming_payment_offers(second)[0]
        self.web.accept_payment_offer(first, first_offer["id"])

        with self.assertRaises(ApiError) as rejected:
            self.web.accept_payment_offer(second, second_offer["id"])

        self.assertEqual(rejected.exception.status, 409)
        self.assertEqual(self.web.payment_request(requester, request["id"])["executor_user_id"], first)

    def test_duplicate_confirmation_does_not_create_second_contribution(self) -> None:
        requester = self._register("requester")
        helper = self._register("helper")
        self.web.create_resource_offer(
            helper,
            {
                "title": "Помощь",
                "offer_type": "external_payment",
                "visibility": "community",
                "max_amount_minor": 2000_00,
                "currency": "RUB",
            },
        )
        request = self.web.create_payment_request(
            requester,
            {"title": "Счёт", "amount_minor": 700_00, "currency": "RUB", "payment_purpose": "Допустимый счёт"},
        )
        self.web.offer_payment_request(requester, request["id"])
        offer = self.web.incoming_payment_offers(helper)[0]
        self.web.accept_payment_offer(helper, offer["id"])
        self.web.simulate_payment(helper, request["id"])
        self.web.confirm_fulfillment(requester, request["id"])

        with self.assertRaises(ApiError) as rejected:
            self.web.confirm_fulfillment(requester, request["id"])

        self.assertEqual(rejected.exception.status, 409)
        self.assertEqual(len(self.web.economy_events(requester, request["id"])["contribution_events"]), 1)

    def test_rejects_requests_asking_for_bank_secrets(self) -> None:
        requester = self._register("requester")

        request = self.web.create_payment_request(
            requester,
            {
                "title": "Нельзя",
                "description": "Передайте PIN и CVV для оплаты",
                "payment_purpose": "Оплата",
                "amount_minor": 100_00,
                "currency": "RUB",
            },
        )

        self.assertEqual(request["status"], "rejected")
        self.assertEqual(request["risk_level"], "rejected")
        with self.assertRaises(ApiError):
            self.web.offer_payment_request(requester, request["id"])

    def _register(self, username: str) -> str:
        token, _ = self.web.register(username, "a-long-password", "invite-only-test-code")
        return self.web.session(token)[0]


if __name__ == "__main__":
    unittest.main()
