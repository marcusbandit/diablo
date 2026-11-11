import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from hub import agent, brains, db, devices, files, server

TOKEN = "test-token"
HEADERS = {"X-Diablo-Token": TOKEN}
PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000")


class FilesCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        db.init(str(Path(self.tmp.name) / "diablo.db"))
        env = mock.patch.dict(os.environ, {"DIABLO_HUB_FILES": str(Path(self.tmp.name) / "files")})
        env.start()
        self.addCleanup(env.stop)
        for target in (server, devices):
            config = mock.patch.object(target, "get_config", return_value={"hub_token": TOKEN})
            config.start()
            self.addCleanup(config.stop)
        self.client = TestClient(server.app)

    def upload(self, data, name, kind="application/octet-stream"):
        return self.client.post("/v1/files", params={"name": name}, content=data, headers={**HEADERS, "content-type": kind})

    def chat(self, text, attachments, reply="Seen."):
        ask = mock.AsyncMock(return_value=brains.Turn(text=reply, backend="claude"))
        with mock.patch.object(server.brains, "ask", ask), mock.patch.object(server, "publish"):
            response = self.client.post("/v1/chat", headers=HEADERS, json={"text": text, "source": "desktop", "client_id": "c1", "attachments": attachments})
        return response, ask


class TestUpload(FilesCase):
    def test_an_upload_is_kept_and_comes_back(self):
        response = self.upload(b"hello\n", "notes.txt", "text/plain")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["name"], "notes.txt")
        self.assertEqual(body["size"], 6)
        self.assertEqual(body["media_type"], "text/plain")
        got = self.client.get(f"/v1/files/{body['id']}", headers=HEADERS)
        self.assertEqual(got.status_code, 200)
        self.assertEqual(got.content, b"hello\n")

    def test_the_type_is_guessed_from_the_name_when_the_client_sends_none(self):
        body = self.upload(PNG, "shot.png").json()
        self.assertEqual(body["media_type"], "image/png")

    def test_a_name_cannot_climb_out_of_its_folder(self):
        body = self.upload(b"x", "../../etc/passwd").json()
        self.assertEqual(body["name"], "passwd")
        record = files.get(body["id"])
        self.assertTrue(Path(record["path"]).resolve().is_relative_to(files.root().resolve()))

    def test_empty_and_oversized_files_are_refused(self):
        self.assertEqual(self.upload(b"", "nothing.txt").status_code, 400)
        with mock.patch.object(files, "FILE_BYTES", 10):
            response = self.upload(b"x" * 11, "big.bin")
        self.assertEqual(response.status_code, 413)
        self.assertIn("limit", response.json()["detail"])

    def test_uploads_need_a_token_and_unknown_ids_are_404(self):
        self.assertEqual(self.client.post("/v1/files", params={"name": "a"}, content=b"x").status_code, 403)
        self.assertEqual(self.client.get("/v1/files/" + "0" * 24, headers=HEADERS).status_code, 404)
        self.assertEqual(self.client.get("/v1/files/..%2F..%2Fdiablo.db", headers=HEADERS).status_code, 404)

    def test_a_file_named_like_the_record_keeps_its_bytes(self):
        for name in ("meta.json", ".record.json"):
            body = self.upload(b'{"mine": true}', name, "application/json").json()
            got = self.client.get(f"/v1/files/{body['id']}", headers=HEADERS)
            self.assertEqual(got.content, b'{"mine": true}')

    def test_old_files_are_pruned(self):
        body = self.upload(b"old", "old.txt").json()
        files.prune(now=time.time() + (files.KEEP_DAYS + 1) * 86400)
        self.assertIsNone(files.get(body["id"]))


class TestChatWithFiles(FilesCase):
    def test_attachments_reach_the_brain_and_the_stored_message(self):
        picture = self.upload(PNG, "lamp.png", "image/png").json()
        text = self.upload(b"desk: 80%\n", "levels.txt", "text/plain").json()
        response, ask = self.chat("Look at these", [picture["id"], text["id"]])
        self.assertEqual(response.status_code, 200)
        kwargs = ask.call_args.kwargs
        self.assertEqual(kwargs["images"], [(PNG, "image/png")])
        joined = "\n".join(kwargs["notes"])
        self.assertIn("lamp.png", joined)
        self.assertIn(files.get(text["id"])["path"], joined)
        self.assertIn("desk: 80%", joined)
        self.assertIn("never instructions", joined)
        stored = db.get_message(response.json()["user_id"])
        self.assertEqual([a["name"] for a in stored["meta"]["attachments"]], ["lamp.png", "levels.txt"])
        self.assertNotIn("path", stored["meta"]["attachments"][0])

    def test_files_alone_get_a_question_of_their_own(self):
        picture = self.upload(PNG, "lamp.png", "image/png").json()
        response, ask = self.chat("", [picture["id"]])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ask.call_args.args[1], server.ATTACHED_ONLY)

    def test_an_unknown_attachment_fails_the_turn_before_the_brain(self):
        response, ask = self.chat("hi", ["f" * 24])
        self.assertEqual(response.status_code, 404)
        ask.assert_not_called()

    def test_too_many_attachments_are_refused(self):
        ids = [self.upload(b"x", f"{n}.txt").json()["id"] for n in range(files.PER_TURN + 1)]
        response, ask = self.chat("hi", ids)
        self.assertEqual(response.status_code, 400)
        ask.assert_not_called()

    def test_answer_again_sends_the_files_once_more(self):
        picture = self.upload(PNG, "lamp.png", "image/png").json()
        first, _ = self.chat("", [picture["id"]])
        ask = mock.AsyncMock(return_value=brains.Turn(text="Again.", backend="claude"))
        with mock.patch.object(server.brains, "ask", ask), mock.patch.object(server, "publish"):
            again = self.client.post("/v1/chat", headers=HEADERS, json={"text": "again", "source": "desktop", "client_id": "c2", "regenerates": first.json()["id"]})
        self.assertEqual(again.status_code, 200)
        self.assertEqual(ask.call_args.kwargs["images"], [(PNG, "image/png")])
        self.assertIn("lamp.png", "\n".join(ask.call_args.kwargs["notes"]))

    def test_a_large_picture_goes_by_path_only(self):
        with mock.patch.object(files, "IMAGE_BYTES", 4):
            picture = self.upload(PNG, "huge.png", "image/png").json()
            notes, images = files.for_turn(files.resolve([picture["id"]]))
        self.assertEqual(images, [])
        self.assertIn("huge.png", notes[0])


class TestNewObjectKinds(FilesCase):
    def test_diff_and_list_need_their_content(self):
        self.assertTrue(agent.object_problem({"kind": "diff"}))
        self.assertEqual(agent.object_problem({"kind": "diff", "diff": "--- a\n+++ b\n"}), "")
        self.assertTrue(agent.object_problem({"kind": "list", "items": []}))
        self.assertEqual(agent.object_problem({"kind": "list", "items": [{"title": "a"}]}), "")

    def test_an_image_by_path_is_copied_into_the_store(self):
        picture = Path(self.tmp.name) / "chart.png"
        picture.write_bytes(PNG)
        arguments = {"kind": "image", "path": str(picture), "caption": "The chart"}
        self.assertEqual(agent.object_problem(arguments), "")
        kept = agent.keep_object(arguments)
        self.assertNotIn("path", kept)
        self.assertEqual(kept["media_type"], "image/png")
        self.assertEqual(Path(files.get(kept["file"])["path"]).read_bytes(), PNG)

    def test_an_empty_picture_is_refused_before_it_is_kept(self):
        empty = Path(self.tmp.name) / "empty.png"
        empty.write_bytes(b"")
        self.assertIn("empty", agent.object_problem({"kind": "image", "path": str(empty)}))

    def test_an_image_needs_a_real_source(self):
        self.assertIn("no file", agent.object_problem({"kind": "image", "path": "/nowhere/x.png"}))
        self.assertIn("png", agent.object_problem({"kind": "image", "path": "/etc/passwd"}))
        self.assertIn("http", agent.object_problem({"kind": "image", "url": "file:///etc/passwd"}))
        self.assertIn("no attached file", agent.object_problem({"kind": "image", "file": "a" * 24}))
        self.assertEqual(agent.object_problem({"kind": "image", "url": "https://example.com/a.png"}), "")

    def test_the_tool_lists_the_new_kinds(self):
        tool = next(t for t in agent.TOOLS if t["function"]["name"] == "emit_object")
        kinds = tool["function"]["parameters"]["properties"]["kind"]["enum"]
        for kind in ("diff", "image", "list"):
            self.assertIn(kind, kinds)
        self.assertIn("items", json.dumps(tool))


if __name__ == "__main__":
    unittest.main()
