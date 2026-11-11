import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from hub import db, llm
from hub import devices, server

TOKEN = "test-token"
HEADERS = {"X-Diablo-Token": TOKEN}


class ApiCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        db.init(str(Path(self.tmp.name) / "diablo.db"))
        db.set_setting("brain.backend", "api")
        for target in (server, devices):
            config = mock.patch.object(target, "get_config", return_value={"hub_token": TOKEN})
            config.start()
            self.addCleanup(config.stop)
        self.client = TestClient(server.app)


class TestAuth(ApiCase):
    def body(self, **overrides):
        body = {"model": "diablo", "messages": [{"role": "user", "content": "hi"}]}
        body.update(overrides)
        return body

    def test_x_diablo_token_accepted(self):
        with mock.patch.object(server.agent, "run_agent", return_value=("Done, sir.", [])):
            response = self.client.post("/v1/chat/completions", headers=HEADERS, json=self.body())
        self.assertEqual(response.status_code, 200)

    def test_bearer_token_accepted(self):
        with mock.patch.object(server.agent, "run_agent", return_value=("Done, sir.", [])):
            response = self.client.post(
                "/v1/chat/completions", headers={"Authorization": f"Bearer {TOKEN}"}, json=self.body()
            )
        self.assertEqual(response.status_code, 200)

    def test_wrong_token_is_forbidden(self):
        for headers in ({"X-Diablo-Token": "nope"}, {"Authorization": "Bearer nope"}):
            response = self.client.post("/v1/chat/completions", headers=headers, json=self.body())
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json(), {"detail": "forbidden"})

    def test_missing_token_is_forbidden(self):
        response = self.client.post("/v1/chat/completions", json=self.body())
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"detail": "forbidden"})

    def test_bearer_without_the_bearer_scheme_is_forbidden(self):
        response = self.client.post(
            "/v1/chat/completions", headers={"Authorization": TOKEN}, json=self.body()
        )
        self.assertEqual(response.status_code, 403)


class TestButlerMode(ApiCase):
    def body(self, **overrides):
        body = {
            "model": "flash-9b",
            "messages": [
                {"role": "system", "content": "be a pirate"},
                {"role": "user", "content": "light the lamps"},
            ],
        }
        body.update(overrides)
        return body

    def test_reply_shape_and_storage(self):
        with mock.patch.object(server.agent, "run_agent", return_value=("Right away, sir.", [])) as run_agent:
            response = self.client.post("/v1/chat/completions", headers=HEADERS, json=self.body())
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["id"].startswith("chatcmpl-"))
        self.assertEqual(data["object"], "chat.completion")
        self.assertIsInstance(data["created"], int)
        self.assertEqual(data["model"], "flash-9b")
        self.assertEqual(data["choices"][0]["index"], 0)
        self.assertEqual(
            data["choices"][0]["message"], {"role": "assistant", "content": "Right away, sir."}
        )
        self.assertEqual(data["choices"][0]["finish_reason"], "stop")
        self.assertEqual(
            data["usage"], {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        )
        run_agent.assert_called_once()
        self.assertEqual(run_agent.call_args.args[0], "light the lamps")
        stored = db.get_messages(10, "api")
        self.assertEqual([message["role"] for message in stored], ["user", "assistant"])
        self.assertEqual([message["text"] for message in stored], ["light the lamps", "Right away, sir."])

    def test_rank_field_picks_the_brain(self):
        db.set_setting("rank.duke", "openrouter|anthropic/claude-opus")
        db.set_setting("provider.openrouter.base_url", "https://openrouter.ai/api/v1")
        db.set_setting("provider.openrouter.api_key", "openrouter-key")
        with mock.patch.object(server.agent, "run_agent", return_value=("sir.", [])) as run_agent:
            response = self.client.post(
                "/v1/chat/completions", headers=HEADERS, json=self.body(model="", rank="duke")
            )
        self.assertEqual(response.status_code, 200)
        brain = run_agent.call_args.args[2]
        self.assertEqual(brain, ("https://openrouter.ai/api/v1", "openrouter-key", "anthropic/claude-opus"))
        self.assertEqual(response.json()["rank"], "duke")

    def test_model_field_resolves_the_brain(self):
        db.set_setting("provider.openrouter.base_url", "https://openrouter.ai/api/v1")
        db.set_setting("provider.openrouter.api_key", "openrouter-key")
        with mock.patch.object(server.agent, "run_agent", return_value=("sir.", [])) as run_agent:
            response = self.client.post(
                "/v1/chat/completions", headers=HEADERS, json=self.body(model="openrouter|anthropic/claude-opus")
            )
        self.assertEqual(response.status_code, 200)
        brain = run_agent.call_args.args[2]
        self.assertEqual(brain, ("https://openrouter.ai/api/v1", "openrouter-key", "anthropic/claude-opus"))
        self.assertEqual(response.json()["rank"], "")

    def test_missing_model_uses_the_default_brain(self):
        with mock.patch.object(server.agent, "run_agent", return_value=("sir.", [])) as run_agent:
            response = self.client.post(
                "/v1/chat/completions", headers=HEADERS, json=self.body(model="")
            )
        self.assertEqual(response.status_code, 200)
        base, key, model = run_agent.call_args.args[2]
        self.assertEqual(model, "accounts/fireworks/models/glm-5p3-flash")
        self.assertEqual(response.json()["model"], "accounts/fireworks/models/glm-5p3-flash")

    def test_unknown_provider_is_400(self):
        response = self.client.post(
            "/v1/chat/completions", headers=HEADERS, json=self.body(model="nope|m")
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"detail": "unknown provider: nope"})

    def test_agent_failure_is_502(self):
        with mock.patch.object(server.agent, "run_agent", side_effect=RuntimeError("boom")):
            response = self.client.post("/v1/chat/completions", headers=HEADERS, json=self.body())
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json(), {"detail": "the agent failed"})

    def test_stream_rejected(self):
        with mock.patch.object(server.agent, "run_agent", return_value="no"):
            response = self.client.post(
                "/v1/chat/completions", headers=HEADERS, json=self.body(stream=True)
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"detail": "diablo does not stream yet"})

    def test_no_user_message_is_400(self):
        with mock.patch.object(server.agent, "run_agent", return_value="no"):
            response = self.client.post(
                "/v1/chat/completions", headers=HEADERS,
                json={"model": "flash-9b", "messages": [{"role": "assistant", "content": "hi"}]},
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"detail": "no user message"})


class TestProxyMode(ApiCase):
    def test_passthrough_verbatim(self):
        upstream = {"id": "cmpl-1", "object": "chat.completion", "model": "m", "choices": []}
        body = {"model": "m", "messages": [{"role": "user", "content": "hi"}], "temperature": 0.2}
        with mock.patch.object(server.llm, "completions_raw", return_value=upstream) as raw:
            response = self.client.post("/v1/proxy/completions", headers=HEADERS, json=body)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), upstream)
        raw.assert_called_once()
        self.assertEqual(raw.call_args.args[0], body)

    def test_missing_or_empty_model_gets_the_llm_default(self):
        db.set_setting("llm_model", "custom-model")
        for body in (
            {"messages": [{"role": "user", "content": "hi"}]},
            {"model": "", "messages": [{"role": "user", "content": "hi"}]},
        ):
            with mock.patch.object(server.llm, "completions_raw", return_value={}) as raw:
                response = self.client.post("/v1/proxy/completions", headers=HEADERS, json=body)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(raw.call_args.args[0]["model"], "custom-model")

    def test_provider_failure_is_502(self):
        with mock.patch.object(
            server.llm, "completions_raw", side_effect=llm.LLMError("chat failed with status 500: nope")
        ):
            response = self.client.post(
                "/v1/proxy/completions", headers=HEADERS,
                json={"model": "m", "messages": [{"role": "user", "content": "hi"}]},
            )
        self.assertEqual(response.status_code, 502)
        self.assertIn("chat failed with status 500", response.json()["detail"])

    def test_stream_forwarded_as_sse(self):
        def lines():
            yield 'data: {"choices": [{"delta": {"content": "hi"}}]}\n\n'
            yield 'data: [DONE]\n\n'

        with mock.patch.object(server.llm, "stream_completions_raw", return_value=lines()):
            response = self.client.post(
                "/v1/proxy/completions", headers=HEADERS,
                json={"model": "m", "messages": [{"role": "user", "content": "hi"}], "stream": True},
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
        self.assertIn("data: [DONE]", response.text)


class TestBodyValidation(ApiCase):
    def test_messages_must_be_a_non_empty_list(self):
        for body in ({"model": "m"}, {"model": "m", "messages": []}, {"model": "m", "messages": "hi"}):
            response = self.client.post("/v1/chat/completions", headers=HEADERS, json=body)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json(), {"detail": "messages must be a non-empty list"})

    def test_non_object_body_is_400(self):
        response = self.client.post("/v1/chat/completions", headers=HEADERS, json=[1, 2])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"detail": "body must be a json object"})

    def test_invalid_json_is_400(self):
        response = self.client.post(
            "/v1/chat/completions",
            headers={**HEADERS, "content-type": "application/json"},
            content=b"not json",
        )
        self.assertEqual(response.status_code, 400)


class TestStreamProxy(ApiCase):
    class FakeResponse:
        status_code = 200

        def iter_lines(self, decode_unicode=False):
            yield 'data: {"choices": [{"delta": {"content": "hi"}}]}'
            yield ''
            yield 'data: [DONE]'

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def test_lines_forwarded_verbatim_until_done(self):
        with mock.patch.object(llm.httpx, "stream", return_value=self.FakeResponse()):
            out = "".join(llm.stream_completions_raw({"model": "m", "messages": []}))
        self.assertEqual(
            out,
            'data: {"choices": [{"delta": {"content": "hi"}}]}\n\ndata: [DONE]\n\n',
        )

    def test_error_status_becomes_an_sse_error(self):
        class Bad:
            status_code = 500

            def read(self):
                return b"boom"

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        with mock.patch.object(llm.httpx, "stream", return_value=Bad()):
            out = "".join(llm.stream_completions_raw({"model": "m", "messages": []}))
        self.assertIn('"chat failed with status 500: boom"', out)
        self.assertTrue(out.endswith("data: [DONE]\n\n"))


class TestSwitchboard(ApiCase):
    def setUp(self):
        super().setUp()
        db.set_setting("brain.backend", "claude")

    def body(self, **overrides):
        body = {"messages": [{"role": "user", "content": "build me a timer app"}], "route": False}
        body.update(overrides)
        return body

    def test_completions_go_through_the_brain(self):
        turn = server.brains.Turn(text="Started it on nemurenai.", objects=[{"kind": "readout"}], backend="claude")
        with mock.patch.object(server.brains, "ask", mock.AsyncMock(return_value=turn)) as ask, \
                mock.patch.object(server.agent, "run_agent") as run_agent:
            response = self.client.post("/v1/chat/completions", headers=HEADERS, json=self.body())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["choices"][0]["message"]["content"], "Started it on nemurenai.")
        self.assertEqual(response.json()["objects"], [{"kind": "readout"}])
        run_agent.assert_not_called()
        self.assertEqual(ask.call_args.args[0], server.brains.MAIN)
        self.assertIn("build me a timer app", ask.call_args.args[1])

    def test_named_chat_gets_its_own_conversation(self):
        turn = server.brains.Turn(text="ok", backend="claude")
        with mock.patch.object(server.brains, "ask", mock.AsyncMock(return_value=turn)) as ask:
            self.client.post("/v1/chat/completions", headers=HEADERS, json=self.body(chat="anibeam-1"))
        self.assertEqual(ask.call_args.args[0], "web-anibeam-1")

    def test_failed_turn_says_so(self):
        turn = server.brains.Turn(error="claude exited with code 1", backend="claude")
        with mock.patch.object(server.brains, "ask", mock.AsyncMock(return_value=turn)):
            response = self.client.post("/v1/chat", headers=HEADERS, json={"text": "hi"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("claude exited with code 1", response.json()["reply"])

    def test_brain_switch(self):
        response = self.client.put("/v1/brain", headers=HEADERS, json={"backend": "opencode", "model": "fireworks-ai/x"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["backend"], "opencode")
        self.assertEqual(response.json()["model"], "fireworks-ai/x")
        response = self.client.put("/v1/brain", headers=HEADERS, json={"backend": "gpt"})
        self.assertEqual(response.status_code, 400)


class TestEventPing(unittest.TestCase):
    def test_default_is_fifteen_seconds(self):
        self.assertEqual(server.ping_interval(None), 15)

    def test_a_phone_in_the_background_asks_for_a_slow_ping(self):
        self.assertEqual(server.ping_interval(240), 240)

    def test_out_of_range_is_clamped(self):
        self.assertEqual(server.ping_interval(0), 5)
        self.assertEqual(server.ping_interval(86400), 600)

class TestVoiceContext(ApiCase):
    def test_screen_lines_and_picture_reach_the_brain(self):
        import base64

        turn = server.brains.Turn(text="ok", backend="claude")
        picture = base64.b64encode(b"\xff\xd8jpeg").decode()
        with mock.patch.object(server.brains, "ask", mock.AsyncMock(return_value=turn)) as ask:
            response = self.client.post("/v1/chat", headers=HEADERS, json={
                "text": "fix this button", "source": "voice",
                "context": ["Focused window: the Diablo desktop app: Diablo", ""],
                "images": [{"data": picture, "media_type": "image/jpeg"},
                           {"data": "not base64!", "media_type": "image/jpeg"}],
            })
        self.assertEqual(response.status_code, 200)
        kwargs = ask.call_args.kwargs
        self.assertIn("Whisper", kwargs["origin"])
        self.assertTrue(any("the Diablo desktop app" in note for note in kwargs["notes"]))
        self.assertTrue(any("screenshot" in note for note in kwargs["notes"]))
        self.assertEqual(kwargs["images"], [(b"\xff\xd8jpeg", "image/jpeg")])

    def test_a_plain_turn_carries_nothing_extra(self):
        turn = server.brains.Turn(text="ok", backend="claude")
        with mock.patch.object(server.brains, "ask", mock.AsyncMock(return_value=turn)) as ask:
            self.client.post("/v1/chat", headers=HEADERS, json={"text": "hi"})
        self.assertIsNone(ask.call_args.kwargs["notes"])
        self.assertIsNone(ask.call_args.kwargs["images"])

    def test_context_is_capped(self):
        body = server.ChatIn(text="x", context=["a" * 5000] * 50)
        notes, images = server.client_context(body)
        self.assertEqual(len(notes), 1)
        self.assertLess(len(notes[0]), server.CONTEXT_LINES * (server.CONTEXT_CHARS + 3) + 200)
        self.assertEqual(images, [])

    def test_charter_says_voice_is_heard_speech_in_his_app(self):
        from hub import charter

        text = charter.CHARTER
        self.assertIn("Whisper transcript", text)
        self.assertIn("Diablo desktop app", text)
        self.assertIn("Never tell him you only get text", text)
        self.assertNotIn(chr(0x2014), text)
        self.assertNotIn(chr(0x2013), text)


if __name__ == "__main__":
    unittest.main()
