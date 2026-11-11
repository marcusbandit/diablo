import asyncio
import io
import logging
import os
import tempfile
import time

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from . import agent, brains, db, devices, questions, settings, stt, t3link, tts

logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

START_TEXT = "Diablo online, sir."
HELP_TEXT = (
    "Write to me, send a photo, or a voice note when transcription is set up.\n"
    "/new starts a fresh conversation\n"
    "/stop stops what I am doing\n"
    "/brain shows or switches the brain: /brain claude, /brain opencode, /brain api, /brain claude opus high\n"
    "/model <name> pins one model for every message, /model auto lets me pick haiku, sonnet or opus per message\n"
    "/t3 shows the T3 Code links, /t3 refresh renews them\n"
    "/pair makes a one-time code that pairs a phone or a browser, /devices lists them and revokes one\n"
    "/status shows all of it"
)
ERROR_TEXT = "Something went wrong, sir"
VOICE_BRIEF = (
    "He sent a voice note and your reply is spoken back as one. Keep it short: "
    "one or two sentences, only what is relevant, no lists, no markdown, no links."
)
VOICE_UNSUPPORTED_TEXT = "I cannot hear voice notes here, sir. Write it out, if you would."
STOPPED_TEXT = "Stopped."
NOTHING_RUNNING = "Nothing is running."
FRESH_TEXT = "A fresh page, then."
CHUNK = 3900
EDIT_EVERY = 2.5
MODEL_ALIASES = {"opus": "claude-opus-5-5", "sonnet": "claude-sonnet-5-5", "haiku": "claude-haiku-5-5", "fable": "claude-fable-5-1"}
EFFORTS = ("low", "medium", "high", "xhigh", "max")

COMMANDS = [
    ("new", "start a fresh conversation"),
    ("stop", "stop what Diablo is doing"),
    ("brain", "show or switch the brain"),
    ("model", "swap the model"),
    ("t3", "T3 Code links"),
    ("pair", "pair a phone or a browser"),
    ("devices", "paired devices"),
    ("status", "brain, links and watched threads"),
]

_app = None
_cfg = None
_loop = None


def owner():
    return str(settings.get("telegram_owner") or settings.get("telegram_chat_id") or "")


def allowed(update):
    chat = update.effective_chat
    chat_id = str(chat.id if chat else update.effective_message.chat_id)
    known = owner()
    if not known:
        settings.set_setting("telegram_owner", chat_id)
        return True
    if chat_id != known:
        logger.warning("ignored telegram message from chat %s", chat_id)
        return False
    return True


def split(text):
    text = text.strip()
    while len(text) > CHUNK:
        cut = text.rfind("\n", 0, CHUNK)
        if cut < CHUNK // 2:
            cut = CHUNK
        yield text[:cut]
        text = text[cut:].lstrip()
    if text:
        yield text


async def send_long(message, text):
    for part in split(text):
        await message.reply_text(part)


class Progress:
    def __init__(self, message, bot):
        self.message = message
        self.bot = bot
        self.status = None
        self.steps = []
        self.last_edit = 0.0
        self.typing = None

    async def _type(self):
        while True:
            try:
                await self.bot.send_chat_action(self.message.chat_id, ChatAction.TYPING)
            except Exception:
                pass
            await asyncio.sleep(4.5)

    def start(self):
        if self.bot is not None:
            self.typing = asyncio.create_task(self._type())

    async def step(self, label):
        self.steps.append(label)
        now = time.monotonic()
        if now - self.last_edit < EDIT_EVERY and self.status is not None:
            return
        self.last_edit = now
        body = "\n".join(f"- {line}" for line in self.steps[-4:])
        text = f"working ({len(self.steps)})\n{body}"[:CHUNK]
        try:
            if self.status is None:
                self.status = await self.message.reply_text(text)
            else:
                await self.status.edit_text(text)
        except Exception:
            pass

    async def finish(self):
        if self.typing:
            self.typing.cancel()
        if self.status is not None:
            try:
                await self.status.delete()
            except Exception:
                pass


def replied_question(message):
    """The pending question a Telegram reply answers in his own words, when he replied to its card."""
    replied = getattr(message, "reply_to_message", None)
    if replied is None:
        return None
    for question in questions.pending(brains.MAIN):
        ref = question.get("telegram") or {}
        if ref.get("message") == replied.message_id and question.get("allow_text"):
            return question
    return None


def take_answer(message, text):
    """The question this message answers, already recorded, or None for an ordinary message."""
    found = replied_question(message)
    try:
        if found is not None:
            return questions.choose(found, [], text, "telegram")
        found, choices = questions.match_reply(brains.MAIN, text)
        if found is not None:
            return questions.choose(found, choices, source="telegram")
    except questions.Problem:
        return None
    return None


async def answer(update, context, text, images=None, meta=None, answering=None):
    message = update.effective_message
    settings.set_setting("telegram_chat_id", str(message.chat_id))
    if answering is None and not images:
        answering = take_answer(message, text)
    if answering is not None:
        meta = dict(meta or {}, answers=answering["id"])
    said_id = db.add_message("telegram", "user", text, meta)
    said = questions.answer_line(answering) if answering is not None else text
    hint = {"thread_hint": answering["threads"]} if answering is not None and answering.get("threads") else {}
    progress = Progress(message, getattr(context, "bot", None))
    progress.start()
    try:
        turn = await brains.ask(brains.MAIN, said, origin="telegram", images=images, on_step=progress.step,
                                message_id=said_id, **hint)
    except BaseException:
        if answering is not None:
            questions.reopen(answering)
        raise
    finally:
        await progress.finish()
    if answering is not None and turn.error and not turn.text.strip():
        questions.reopen(answering)
    if turn.error == "stopped":
        return turn
    reply = turn.text.strip()
    if not reply:
        reply = agent.FALLBACK if not turn.error else f"That failed, sir: {turn.error[:600]}"
    if turn.note:
        reply += f"\n\n({turn.note})"
    meta = {"backend": turn.backend, "did": turn.did}
    if turn.threads:
        meta["threads"] = turn.threads
    if turn.objects:
        meta["objects"] = turn.objects
    db.add_message("telegram", "assistant", reply, meta)
    await send_long(message, reply)
    for block in turn.objects or []:
        if isinstance(block, dict) and block.get("kind") == "question":
            await send_question(message.chat_id, questions.get(block["id"]))
    return turn


def escape(text):
    return str(text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def question_card(question):
    """The text of a question's Telegram message for its current state."""
    lines = [f"<b>{escape(question.get('header'))}</b>", escape(question.get("question")), ""]
    state = question.get("state")
    answer = question.get("answer") or {}
    if state == "answered":
        chosen = ", ".join(answer.get("labels") or [])
        if chosen:
            lines.append(f"You chose: <b>{escape(chosen)}</b>")
        if answer.get("text"):
            lines.append(f"You said: {escape(answer['text'])}")
        return "\n".join(lines)
    if state in ("expired", "cancelled", "superseded"):
        lines.append({"expired": "No answer in time, so this lapsed.", "cancelled": "Withdrawn.",
                      "superseded": "Replaced by a newer question."}[state])
        return "\n".join(lines)
    recommended = question.get("recommended")
    for index, option in enumerate(question.get("options") or []):
        mark = " (recommended)" if index == recommended else ""
        detail = f": {escape(option['description'])}" if option.get("description") else ""
        lines.append(f"{index + 1}. <b>{escape(option['label'])}</b>{mark}{detail}")
    lines.append("")
    hint = "Tap the ones you want, then Done." if question.get("multi") else "Tap one."
    if question.get("allow_text"):
        hint += " Or reply to this message in your own words."
    lines.append(f"<i>{hint}</i>")
    return "\n".join(lines)


def question_keyboard(question):
    """The buttons under a pending question: callback data q:<id>:<n>, and q:<id>:done for a multi pick."""
    if question.get("state") != "pending":
        return None
    picked = set(question.get("picked") or [])
    rows = []
    for index, option in enumerate(question.get("options") or []):
        label = option["label"]
        if index == question.get("recommended"):
            label += " (recommended)"
        label = f"{index + 1}. {label}"
        if question.get("multi"):
            label = ("[x] " if index in picked else "[ ] ") + label
        rows.append([InlineKeyboardButton(label[:64], callback_data=f"q:{question['id']}:{index}")])
    if question.get("multi"):
        rows.append([InlineKeyboardButton("Done", callback_data=f"q:{question['id']}:done")])
    return InlineKeyboardMarkup(rows)


async def send_question(chat_id, question, bot=None):
    """Show a pending question as a card with a keyboard, and keep which message it is."""
    bot = bot or (_app.bot if _app is not None else None)
    if bot is None or question is None or question.get("state") != "pending":
        return None
    try:
        sent = await bot.send_message(int(chat_id), question_card(question), parse_mode=ParseMode.HTML,
                                      reply_markup=question_keyboard(question))
    except Exception:
        logger.exception("could not send question %s", question.get("id"))
        return None
    questions.note(question["id"], telegram={"chat": int(chat_id), "message": sent.message_id})
    return sent


async def push_questions(conv=None):
    """Send the questions a turn that did not come from Telegram asked, like a T3 event, when it is pushed here."""
    chat_id = settings.get("telegram_chat_id") or owner()
    if _app is None or not chat_id or (conv or brains.MAIN) != brains.MAIN:
        return 0
    sent = 0
    for question in reversed(questions.unsent(brains.MAIN, "telegram")):
        if await send_question(chat_id, question):
            sent += 1
    return sent


def parse_tap(data):
    """(question id, option index or "done") from a question button's callback data, else None."""
    parts = str(data or "").split(":")
    if len(parts) != 3 or parts[0] != "q" or not parts[1]:
        return None
    if parts[2] == "done":
        return parts[1], "done"
    if parts[2].isdigit():
        return parts[1], int(parts[2])
    return None


async def refresh_card(question):
    """Edit a question's Telegram message to show where it stands now."""
    ref = (question or {}).get("telegram") or {}
    if _app is None or not ref.get("message"):
        return
    try:
        await _app.bot.edit_message_text(question_card(question), chat_id=ref["chat"], message_id=ref["message"],
                                         parse_mode=ParseMode.HTML, reply_markup=question_keyboard(question))
    except Exception as error:
        if "not modified" not in str(error):
            logger.warning("could not edit question %s: %s", question.get("id"), error)


def on_question(question):
    """questions listener: a question that closed anywhere gets its Telegram card edited."""
    if _loop is None or question.get("state") == "pending" or not question.get("telegram"):
        return
    current = questions.get(question["id"]) or question
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is _loop:
        _loop.create_task(refresh_card(current))
    else:
        asyncio.run_coroutine_threadsafe(refresh_card(current), _loop)


async def _on_question_tap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query is None or not allowed(update):
        return
    tap = parse_tap(query.data)
    question = questions.get(tap[0]) if tap else None
    if question is None or question["state"] != "pending":
        await query.answer("That question is closed." if question else "")
        if question is not None:
            await refresh_card(question)
        return
    if question["conv"] != brains.MAIN:
        await query.answer("Answer this one in the app that asked it.")
        return
    if question.get("multi") and tap[1] != "done":
        picked = list(question.get("picked") or [])
        if tap[1] in picked:
            picked.remove(tap[1])
        else:
            picked.append(tap[1])
        question = questions.note(question["id"], picked=picked)
        await query.answer()
        await refresh_card(question)
        return
    choices = list(question.get("picked") or []) if tap[1] == "done" else [tap[1]]
    try:
        answered = questions.choose(question, choices, source="telegram")
    except questions.Problem as problem:
        await query.answer(problem.detail[:180])
        return
    await query.answer("Got it.")
    await refresh_card(answered)
    try:
        await answer(update, context, questions.shown_text(answered), answering=answered)
    except Exception:
        logger.exception("question answer turn failed")
        await query.message.reply_text(ERROR_TEXT)


async def _on_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if allowed(update):
        await update.effective_message.reply_text(START_TEXT)


async def _on_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if allowed(update):
        await update.effective_message.reply_text(HELP_TEXT)


def status_text():
    watches = db.open_watches()
    lines = [brains.describe(), "", "T3 links:", t3link.status()]
    if watches:
        lines += ["", "Watching:"] + [f"- {w['title'] or w['thread_id']} on {w['env']} ({w['state']})" for w in watches]
    return "\n".join(lines)


async def _on_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if allowed(update):
        await update.effective_message.reply_text(status_text())


async def _on_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not allowed(update):
        return
    await brains.stop(brains.MAIN)
    await brains.reset(brains.MAIN)
    await update.effective_message.reply_text(FRESH_TEXT)


async def _on_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not allowed(update):
        return
    stopped = await brains.stop(brains.MAIN)
    await update.effective_message.reply_text(STOPPED_TEXT if stopped else NOTHING_RUNNING)


def apply_brain(args):
    changed = []
    for raw in args:
        word = raw.strip()
        lower = word.lower()
        if lower == "auto":
            settings.set_setting("route.enabled", "on")
            changed.append("model picked per message")
        elif lower in brains.BACKENDS:
            settings.set_setting("brain.backend", lower)
            changed.append(f"brain {lower}")
        elif lower in EFFORTS:
            settings.set_setting("brain.claude.effort", lower)
            changed.append(f"effort {lower}")
        elif word:
            current = brains.backend()
            if current == "claude":
                settings.set_setting("brain.claude.model", MODEL_ALIASES.get(lower, word))
                settings.set_setting("route.enabled", "off")
            elif current == "opencode":
                settings.set_setting("brain.opencode.model", word)
            else:
                settings.set_setting("llm_model", word)
            changed.append(f"model {MODEL_ALIASES.get(lower, word)}")
    return changed


async def _on_brain(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not allowed(update):
        return
    args = context.args or []
    if args:
        apply_brain(args)
    await update.effective_message.reply_text(brains.describe())


async def _on_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not allowed(update):
        return
    args = context.args or []
    if not args:
        await update.effective_message.reply_text(f"{brains.describe()}\nusage: /model <name>")
        return
    apply_brain(args[:1])
    await update.effective_message.reply_text(brains.describe())


async def _on_t3(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not allowed(update):
        return
    args = [a.lower() for a in (context.args or [])]
    if args and args[0] == "refresh":
        await update.effective_message.reply_text("Renewing the T3 links...")
        report = await asyncio.to_thread(t3link.refresh, True)
        await brains.restart_all()
        await update.effective_message.reply_text("\n".join(f"{env}: {state}" for env, state in report.items()))
        return
    await update.effective_message.reply_text(t3link.status())


async def _on_pair(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not allowed(update):
        return
    await update.effective_message.reply_text(devices.pair_text(devices.mint()))


async def _on_devices(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not allowed(update):
        return
    args = [a.lower() for a in (context.args or [])]
    if len(args) == 2 and args[0] == "revoke" and args[1].isdigit():
        done = devices.revoke(int(args[1]))
        await update.effective_message.reply_text(devices.revoked_text(done) if done else f"No device {args[1]}.")
        return
    await update.effective_message.reply_text(devices.devices_text())


async def _on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not allowed(update):
        return
    try:
        await answer(update, context, update.effective_message.text)
    except Exception:
        logger.exception("text handler failed")
        await update.effective_message.reply_text(ERROR_TEXT)


async def _on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not allowed(update):
        return
    message = update.effective_message
    try:
        photo = message.photo[-1]
        tg_file = await photo.get_file()
        data = bytes(await tg_file.download_as_bytearray())
        text = (message.caption or "").strip() or "What do you make of this?"
        await answer(update, context, text, images=[(data, "image/jpeg")], meta={"photo": True})
    except Exception:
        logger.exception("photo handler failed")
        await message.reply_text(ERROR_TEXT)


async def _on_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not allowed(update):
        return
    message = update.effective_message
    try:
        settings.set_setting("telegram_chat_id", str(message.chat_id))
        if not stt.available():
            await message.reply_text(VOICE_UNSUPPORTED_TEXT)
            return
        tg_file = await message.voice.get_file()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "voice.ogg")
            await tg_file.download_to_drive(path)
            with open(path, "rb") as handle:
                data = handle.read()
        try:
            text, language = await asyncio.to_thread(stt.transcribe, data, "voice.ogg")
        except Exception:
            logger.exception("transcription failed")
            await message.reply_text(VOICE_UNSUPPORTED_TEXT)
            return
        if not text:
            await message.reply_text(VOICE_UNSUPPORTED_TEXT)
            return
        answering = take_answer(message, text)
        if settings.get("tts_provider") in ("", "none"):
            await answer(update, context, text, meta={"voice": True, "language": language}, answering=answering)
            return
        heard = {"voice": True, "language": language, **({"answers": answering["id"]} if answering else {})}
        said_id = db.add_message("telegram", "user", text, heard)
        await context.bot.send_chat_action(message.chat_id, ChatAction.RECORD_VOICE)
        said = questions.answer_line(answering) if answering else text
        hint = {"thread_hint": answering["threads"]} if answering and answering.get("threads") else {}
        try:
            turn = await brains.ask(brains.MAIN, said, origin="telegram voice note", notes=[VOICE_BRIEF], message_id=said_id, **hint)
        except BaseException:
            if answering:
                questions.reopen(answering)
            raise
        if answering and turn.error and not turn.text.strip():
            questions.reopen(answering)
        reply = turn.text.strip() or agent.FALLBACK
        kept = {"backend": turn.backend, "did": turn.did, **({"threads": turn.threads} if turn.threads else {}),
                **({"objects": turn.objects} if turn.objects else {})}
        asked = [questions.get(o["id"]) for o in turn.objects or [] if isinstance(o, dict) and o.get("kind") == "question"]
        try:
            audio = await asyncio.to_thread(tts.speak, " ".join([reply] + [questions.spoken_text(q) for q in asked if q]), True, language)
            db.add_message("telegram", "assistant", reply, dict(kept, voice_reply=True))
            await message.reply_voice(InputFile(io.BytesIO(audio), filename="reply.ogg"))
        except Exception:
            logger.exception("tts failed, falling back to text")
            db.add_message("telegram", "assistant", reply, kept)
            await send_long(message, reply)
        for question in asked:
            await send_question(message.chat_id, question)
    except Exception:
        logger.exception("voice handler failed")
        await message.reply_text(ERROR_TEXT)


def _speaks():
    return str(settings.get("speaking_mode") or "off").strip().lower() in ("on", "1", "true", "yes") and settings.get("tts_provider") not in ("", "none")


async def push(text, objects=None):
    """Send a finished task report to the chat Diablo was last written from. objects (a question it
    asked) are kept on the stored message even when Telegram cannot be reached, so the apps show them."""
    kept = {"objects": objects} if objects else {}
    chat_id = settings.get("telegram_chat_id") or owner()
    if _app is None or not chat_id:
        if _app is not None:
            logger.warning("telegram push skipped: no chat id yet")
        if kept:
            db.add_message("telegram", "assistant", text.strip(), dict(kept, pushed=False))
        return False
    sent = False
    for part in split(text):
        try:
            await _app.bot.send_message(int(chat_id), part)
            sent = True
        except Exception:
            logger.exception("telegram push failed")
            if kept:
                db.add_message("telegram", "assistant", text.strip(), dict(kept, pushed=sent))
            return sent
    db.add_message("telegram", "assistant", text.strip(), dict(kept, pushed=True))
    return sent


async def push_card(text, buttons=None):
    """Send one short HTML card, optionally with a row of buttons whose data a callback handler reads."""
    if _app is None:
        return False
    chat_id = settings.get("telegram_chat_id") or owner()
    if not chat_id:
        logger.warning("telegram card skipped: no chat id yet")
        return False
    markup = None
    if buttons:
        markup = InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=data) for label, data in row] for row in buttons])
    try:
        await _app.bot.send_message(int(chat_id), text[:4000], parse_mode=ParseMode.HTML,
                                    reply_markup=markup, disable_web_page_preview=True)
    except Exception:
        logger.exception("telegram card failed")
        return False
    return True


async def _on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query is None or not allowed(update):
        return
    parts = str(query.data or "").split(":")
    if len(parts) != 3 or parts[0] != "mail" or not parts[2].isdigit():
        await query.answer()
        return
    from . import mailbox

    try:
        said = await mailbox.decide(int(parts[2]), parts[1])
    except Exception:
        logger.exception("mail button failed")
        said = "That did not work. Try it from the app."
    await query.answer(said[:180])
    try:
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(said)
    except Exception:
        logger.exception("could not update the mail card")


async def start_bot(cfg, token):
    global _app, _cfg, _loop
    _cfg = cfg
    app = Application.builder().token(token).concurrent_updates(True).build()
    _app = app
    _loop = asyncio.get_running_loop()
    if on_question not in questions.listeners:
        questions.listeners.append(on_question)
    app.add_handler(CommandHandler("start", _on_start))
    app.add_handler(CommandHandler("help", _on_help))
    app.add_handler(CommandHandler("status", _on_status))
    app.add_handler(CommandHandler("new", _on_new))
    app.add_handler(CommandHandler("stop", _on_stop))
    app.add_handler(CommandHandler("brain", _on_brain))
    app.add_handler(CommandHandler("model", _on_model))
    app.add_handler(CommandHandler("t3", _on_t3))
    app.add_handler(CommandHandler("pair", _on_pair))
    app.add_handler(CommandHandler("devices", _on_devices))
    app.add_handler(CallbackQueryHandler(_on_button, pattern=r"^mail:"))
    app.add_handler(CallbackQueryHandler(_on_question_tap, pattern=r"^q:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _on_text))
    app.add_handler(MessageHandler(filters.PHOTO, _on_photo))
    app.add_handler(MessageHandler(filters.VOICE, _on_voice))
    await app.initialize()
    try:
        await app.bot.set_my_commands([BotCommand(name, text) for name, text in COMMANDS])
    except Exception:
        logger.warning("could not set the telegram command menu")
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
