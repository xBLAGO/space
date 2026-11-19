"""Harness safety tests, not evidence of live inference or physical devices."""
import os
import unittest
from unittest.mock import patch

from tools.agent_context_live_check import run, RecordedProvider
from xblago.core.llm import Completion, LlmProviderError, OpenAICompatibleProvider


class LiveAcceptanceHarnessTests(unittest.TestCase):
    def test_production_credentials_never_enable_live_run(self):
        with patch.dict(os.environ, {'BLAGO_TEST_HF_TOKEN':'','BLAGO_TEST_HF_MODEL':'',
                'BLAGO_TEST_HF_APPROVED':'','HF_TOKEN':'production-sentinel',
                'OPENAI_API_KEY':'production-sentinel'}), patch('tools.agent_context_live_check.RecordedProvider') as provider:
            result=run()
        provider.assert_not_called()
        self.assertEqual(result['status'],'NOT EXECUTED')
        self.assertEqual(result['requests'],0)
        self.assertEqual(result['physical_android'],'NOT EXECUTED')
    def test_test_credential_without_explicit_scope_never_calls_provider(self):
        with patch.dict(os.environ,{'BLAGO_TEST_HF_TOKEN':'synthetic-only-key',
                'BLAGO_TEST_HF_MODEL':'synthetic-model','BLAGO_TEST_HF_APPROVED':''}), patch('tools.agent_context_live_check.RecordedProvider') as provider:
            self.assertEqual(run()['status'],'NOT EXECUTED')
        provider.assert_not_called()
    def test_mocked_transport_exercises_real_agent_but_never_qualifies_quality(self):
        getenv=os.getenv
        def isolated_getenv(key,*args):
            if key in ('HF_TOKEN','OPENAI_API_KEY','XBLAGO_LLM_API_KEY'):
                raise AssertionError('Production provider configuration must not be read')
            return getenv(key,*args)
        with patch.dict(os.environ,{'BLAGO_TEST_HF_TOKEN':'synthetic-only-key',
                'BLAGO_TEST_HF_MODEL':'synthetic-model','BLAGO_TEST_HF_APPROVED':'synthetic-only',
                'HF_TOKEN':'production-sentinel','OPENAI_API_KEY':'production-sentinel'}), patch('os.getenv',side_effect=isolated_getenv), patch.object(
                OpenAICompatibleProvider,'_request',return_value=Completion('Ответ тестового транспорта.','stop',False)):
            result=run()
        self.assertTrue(all(result['state_checks'].values()))
        self.assertEqual(result['answer_quality'],'PENDING HUMAN REVIEW')
        self.assertEqual(result['physical_iphone'],'NOT EXECUTED')
        self.assertGreaterEqual(len(result['turns']),50)
        self.assertLessEqual(result['requests'],96)
        self.assertEqual(next(t for t in result['turns'] if t['stage']=='deleted_mini')['selected_facts'].get('city'),None)
        self.assertTrue(any(t['stage']=='C02_boundary' for t in result['turns']))
    def test_request_budget_is_checked_before_network(self):
        provider=RecordedProvider.__new__(RecordedProvider)
        provider.calls=[{}]*96
        with patch.object(OpenAICompatibleProvider,'_request') as transport:
            with self.assertRaises(LlmProviderError):provider._request([],0,384)
        transport.assert_not_called()
