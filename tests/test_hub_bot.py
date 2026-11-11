import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hub import bot, brains, db, settings


def _turn(text, **extra):
    return mock.AsyncMock(return_value=brains.Turn(text=text, backend="claude", **extra))


def _message(text="hi"):
    message = mock.MagicMock()
    message.text = text
    message.chat_id = 42
    message.reply_text = mock.AsyncMock()
    message.reply_voice = mock.AsyncMock()
    voice_file = mock.MagicMock()

    async def download(path):
        Path(path).write_bytes(b"ogg")

    voice_file.download_to_drive = download
    message.voice.get_file = mock.AsyncMock(return_value=voice_file)
    update = mock.MagicMock()
    update.effective_message = message
    update.effective_chat.id = 42
    return update, message


class BotCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        db.init(str(Path(self.tmp.name) / "diablo.db"))


class TestText(BotCase):
    def test_reply_is_sent_as_text(self):
        update, message = _message("light the lamps")
        with mock.patch.object(bot.brains, "ask", _turn("Done, sir.")) as ask:
            asyncio.run(bot._on_text(update, None))
        message.reply_text.assert_awaited_once_with("Done, sir.")
        self.assertEqual(ask.call_args.args[:2], (brains.MAIN, "light the lamps"))

    def test_empty_reply_falls_back(self):
        update, message = _message()
        with mock.patch.object(bot.brains, "ask", _turn("  ")):
            asyncio.run(bot._on_text(update, None))
        message.reply_text.assert_awaited_once_with(bot.agent.FALLBACK)

    def test_failure_is_reported(self):
        update, message = _message()
        with mock.patch.object(bot.brains, "ask", _turn("", error="usage limit reached")):
            asyncio.run(bot._on_text(update, None))
        self.assertIn("usage limit reached", message.reply_text.await_args.args[0])

    def test_stopped_turn_sends_nothing(self):
        update, message = _message()
        with mock.patch.object(bot.brains, "ask", _turn("", error="stopped")):
            asyncio.run(bot._on_text(update, None))
        message.reply_text.assert_not_awaited()

    def test_strangers_are_ignored(self):
        settings.set_setting("telegram_owner", "7")
        update, message = _message()
        with mock.patch.object(bot.brains, "ask", _turn("hi")) as ask:
            asyncio.run(bot._on_text(update, None))
        ask.assert_not_called()
        message.reply_text.assert_not_awaited()

    def test_first_chat_claims_the_bot(self):
        update, message = _message()
        with mock.patch.object(bot.brains, "ask", _turn("hi")):
            asyncio.run(bot._on_text(update, None))
        self.assertEqual(settings.get("telegram_owner"), "42")

    def test_long_reply_is_split(self):
        update, message = _message()
        long_text = ("word " * 900 + "\n") * 3
        with mock.patch.object(bot.brains, "ask", _turn(long_text)):
            asyncio.run(bot._on_text(update, None))
        self.assertGreater(message.reply_text.await_count, 1)
        for call in message.reply_text.await_args_list:
            self.assertLessEqual(len(call.args[0]), bot.CHUNK)


class TestBrainCommand(BotCase):
    def test_backend_model_alias_and_effort(self):
        changed = bot.apply_brain(["claude", "opus", "high"])
        self.assertEqual(settings.get("brain.backend"), "claude")
        self.assertEqual(settings.get("brain.claude.model"), "claude-opus-5-5")
        self.assertEqual(settings.get("brain.claude.effort"), "high")
        self.assertEqual(len(changed), 3)

    def test_model_follows_the_current_backend(self):
        bot.apply_brain(["opencode", "fireworks-ai/glm"])
        self.assertEqual(settings.get("brain.opencode.model"), "fireworks-ai/glm")


def _context():
    context = mock.MagicMock()
    context.bot.send_chat_action = mock.AsyncMock()
    return context


class TestVoice(BotCase):
    def test_no_transcription_key_says_unsupported(self):
        update, message = _message()
        asyncio.run(bot._on_voice(update, None))
        message.reply_text.assert_awaited_once_with(bot.VOICE_UNSUPPORTED_TEXT)

    def test_voice_note_answers_in_text_without_a_tts_provider(self):
        settings.set_setting("transcription_api_key", "k")
        settings.set_setting("tts_provider", "none")
        update, message = _message()
        with mock.patch.object(bot.stt, "transcribe", return_value=("hello", "en")), \
                mock.patch.object(bot.brains, "ask", _turn("Evening, sir.")), \
                mock.patch.object(bot.tts, "speak") as speak:
            asyncio.run(bot._on_voice(update, _context()))
        speak.assert_not_called()
        message.reply_text.assert_awaited_once_with("Evening, sir.")

    def test_tts_failure_falls_back_to_text(self):
        settings.set_setting("transcription_api_key", "k")
        settings.set_setting("tts_provider", "cartesia")
        settings.set_setting("speaking_mode", "on")
        update, message = _message()
        with mock.patch.object(bot.stt, "transcribe", return_value=("hello", "en")), \
                mock.patch.object(bot.brains, "ask", _turn("Evening, sir.")), \
                mock.patch.object(bot.tts, "speak", side_effect=RuntimeError("down")):
            asyncio.run(bot._on_voice(update, _context()))
        message.reply_voice.assert_not_awaited()
        message.reply_text.assert_awaited_once_with("Evening, sir.")

    def test_transcription_failure_says_unsupported(self):
        settings.set_setting("transcription_api_key", "k")
        update, message = _message()
        with mock.patch.object(bot.stt, "transcribe", side_effect=RuntimeError("down")):
            asyncio.run(bot._on_voice(update, None))
        message.reply_text.assert_awaited_once_with(bot.VOICE_UNSUPPORTED_TEXT)


if __name__ == "__main__":
    unittest.main()
