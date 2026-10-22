from contextlib import redirect_stderr, redirect_stdout
from io import BytesIO, StringIO
import json
import os
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from toollery_dsh import provider_bridge as bridge


class ProviderBridgeTests(unittest.TestCase):
    def request(self):
        return {
            "protocol_version": 2, "system_prompt": "Paper prompt",
            "proxy_query_count": 1, "model": "fixture-flash",
            "capabilities": [{"name": "\u5929\u6c14", "specification": {"description": "\u57ce\u5e02\u5929\u6c14"}}],
            "parameters": {"api_url": "https://example.test/v1", "api_key_env": "FIXTURE_KEY"},
        }

    def test_wire_payload_and_unicode_response(self):
        manuals = {"\u5929\u6c14": [{"query": "\u5317\u4eac\u4f1a\u4e0b\u96e8\u5417？", "scenario_type": "forecast", "generation_notes": "fixture"}]}
        response = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(manuals)}}], "usage": {"prompt_tokens": 12}}
        out, err = StringIO(), StringIO()
        with patch.dict(os.environ, {"FIXTURE_KEY": "fixture-secret"}, clear=True), \
                patch("sys.stdin", StringIO(json.dumps(self.request()))), \
                redirect_stdout(out), redirect_stderr(err), \
                patch("urllib.request.build_opener") as build:
            build.return_value.open.return_value = BytesIO(json.dumps(response).encode())
            self.assertEqual(bridge.main(), 0)
        sent = build.return_value.open.call_args.args[0]
        self.assertEqual(sent.full_url, "https://example.test/v1/chat/completions")
        self.assertEqual(sent.get_header("Authorization"), "Bearer fixture-secret")
        payload = json.loads(sent.data)
        self.assertEqual(payload["model"], "fixture-flash")
        self.assertEqual(payload["messages"][0]["content"], "Paper prompt")
        self.assertEqual(json.loads(payload["messages"][1]["content"]), {"capabilities": self.request()["capabilities"]})
        self.assertEqual(json.loads(out.getvalue()), {"manuals": manuals, "usage": response["usage"]})
        self.assertEqual(err.getvalue(), "")
        self.assertNotIn("fixture-secret", out.getvalue())

    def test_missing_key_does_not_call_network(self):
        with patch.dict(os.environ, {}, clear=True), patch("urllib.request.build_opener") as build:
            with self.assertRaisesRegex(bridge.BridgeError, "Missing API key"):
                bridge._request({}, 1, 2, self.request()["parameters"])
        build.assert_not_called()

    def test_401_is_not_retried_and_error_body_is_hidden(self):
        error = HTTPError("https://example.test", 401, "SECRET", {}, BytesIO(b"SECRET"))
        with patch.dict(os.environ, {"FIXTURE_KEY": "fixture"}, clear=True), \
                patch("urllib.request.build_opener") as build, patch.object(bridge.time, "sleep") as sleep:
            build.return_value.open.side_effect = error
            with self.assertRaises(bridge.BridgeError) as raised:
                bridge._request({}, 1, 2, self.request()["parameters"])
        self.assertEqual(str(raised.exception), "API returned HTTP 401")
        self.assertEqual(build.return_value.open.call_count, 1)
        sleep.assert_not_called()

    def test_429_is_retried(self):
        error = HTTPError("https://example.test", 429, "busy", {}, BytesIO(b"busy"))
        with patch.dict(os.environ, {"FIXTURE_KEY": "fixture"}, clear=True), \
                patch("urllib.request.build_opener") as build, patch.object(bridge.time, "sleep") as sleep:
            build.return_value.open.side_effect = [error, BytesIO(b'{}')]
            self.assertEqual(bridge._request({}, 1, 2, self.request()["parameters"]), {})
        self.assertEqual(build.return_value.open.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_incomplete_response_rejected(self):
        with self.assertRaises(ValueError):
            bridge._json_content({"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]})
        self.assertEqual(bridge._strip_json_fence('```json\n{"a": []}\n```'), '{"a": []}')

    def test_invalid_model_json_not_logged(self):
        response = {"choices": [{"message": {"content": "PRIVATE_MODEL_RESPONSE"}}]}
        out, err = StringIO(), StringIO()
        with patch("sys.stdin", StringIO(json.dumps(self.request()))), \
                redirect_stdout(out), redirect_stderr(err), patch.object(bridge, "_request", return_value=response):
            self.assertEqual(bridge.main(), 1)
        self.assertEqual(json.loads(out.getvalue()), {"error": {"code": "invalid_response"}})
        self.assertNotIn("PRIVATE_MODEL_RESPONSE", err.getvalue())
        self.assertIn("JSONDecodeError", err.getvalue())
