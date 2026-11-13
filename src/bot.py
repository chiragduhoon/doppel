"""Doppel — a Telegram bot that texts people as you, in your absence.

Run: python src/bot.py   (needs .env — see .env.example)

Owner commands (only work from OWNER_CHAT_ID):
  /pause   - stop replying to everyone
  /resume  - start replying again
  /status  - provider, model, paused state, chat count
  /recent  - last few conversations (e.g. /recent 5)
"""

import asyncio
import json
import logging
import os
import random
import sys
from pathlib import Path

from dotenv import load_dotenv

# Windows consoles often report cp1252 for stdout even in a UTF-8 codepage —
# without this, logging a message that contains an emoji crashes the bot.
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

import agent  # noqa: E402
import memory as memory_mod  # noqa: E402
from memory import ChatMemory  # noqa: E402

from telegram import Update  # noqa: E402
from telegram.constants import ChatAction  # noqa: E402
from telegram.ext import (  # noqa: E402
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("doppel")

OWNER_ID = (os.getenv("OWNER_CHAT_ID") or "").strip()
FORWARD_TO_OWNER = (os.getenv("FORWARD_TO_OWNER") or "false").strip().lower() == "true"
DELAY_MIN = float(os.getenv("REPLY_DELAY_MIN") or 2)
DELAY_MAX = float(os.getenv("REPLY_DELAY_MAX") or 12)
GROUP_WINDOW = 4.0  # seconds to wait for rapid-fire follow-up messages before replying

STATE_FILE = PROJECT_ROOT / "data" / "state.json"

# chat_id -> list of not-yet-answered incoming texts / debounce task
_pending: dict[int, list[str]] = {}
_debounce: dict[int, asyncio.Task] = {}


# ---------- pause state (survives restarts) ----------

def _is_paused() -> bool:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8")).get("paused", False)
    except (OSError, json.JSONDecodeError):
        return False


def _set_paused(value: bool) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({"paused": value}), encoding="utf-8")


# ---------- helpers ----------

def _is_owner(update: Update) -> bool:
    return OWNER_ID != "" and str(update.effective_user.id) == OWNER_ID


async def _notify_owner(context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    if OWNER_ID:
        try:
            await context.bot.send_message(chat_id=int(OWNER_ID), text=text)
        except Exception:  # noqa: BLE001
            log.warning("could not notify owner")


def _contact_name(update: Update) -> str:
    u = update.effective_user
    name = " ".join(p for p in [u.first_name, u.last_name] if p) or (u.username or "them")
    return name


def _placeholder_for_media(msg) -> str | None:
    if msg.voice or msg.audio:
        return "[they sent a voice note you can't listen to right now — react naturally, maybe say you'll hear it later]"
    if msg.photo or msg.video or msg.video_note:
        return "[they sent a photo/video you can't open right now — react naturally, maybe say you'll check it later]"
    if msg.sticker:
        return "[they sent a sticker — react casually or ignore it]"
    if msg.document:
        return "[they sent a file you can't open right now — react naturally]"
    return None


# ---------- owner commands ----------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if _is_owner(update):
        await update.message.reply_text(
            "doppel is running.\n/pause /resume /status /recent <n>"
        )
        return
    # a stranger tapping Start = them opening the chat; greet in persona
    await _enqueue(update, context, "hi")


async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
        return
    _set_paused(True)
    await update.message.reply_text("paused — I won't reply to anyone until /resume")


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
        return
    _set_paused(False)
    await update.message.reply_text("resumed — replying as you again")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
        return
    provider = (os.getenv("LLM_PROVIDER") or "gemini").lower()
    model = os.getenv("LLM_MODEL") or agent.llm.DEFAULT_MODELS.get(provider, "?")
    n_chats = len(list(memory_mod.CHATS_DIR.glob("*.json"))) if memory_mod.CHATS_DIR.exists() else 0
    await update.message.reply_text(
        f"provider: {provider}\nmodel: {model}\npaused: {_is_paused()}\n"
        f"chats: {n_chats}\nforward to owner: {FORWARD_TO_OWNER}"
    )


async def cmd_recent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
        return
    try:
        n = int(context.args[0]) if context.args else 5
    except ValueError:
        n = 5
    chats = memory_mod.recent_chats(n)
    if not chats:
        await update.message.reply_text("no conversations yet")
        return
    lines = []
    for c in chats:
        lines.append(f"— {c['name']} ({c['when']})\n   them: {c['last_them'][:80]}\n   you:  {c['last_you'][:80]}")
    await update.message.reply_text("\n".join(lines))


# ---------- conversation flow ----------

async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    if msg is None:
        return

    if _is_owner(update):
        await msg.reply_text("(this is your own bot — commands: /pause /resume /status /recent)")
        return

    text = msg.text or msg.caption or _placeholder_for_media(msg)
    if not text:
        return

    if _is_paused():
        if FORWARD_TO_OWNER:
            await _notify_owner(context, f"(paused) 💬 {_contact_name(update)}: {text}")
        return

    await _enqueue(update, context, text)


async def _enqueue(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    """Collect rapid-fire messages for GROUP_WINDOW seconds, then answer them together."""
    chat_id = update.effective_chat.id
    name = _contact_name(update)
    _pending.setdefault(chat_id, []).append(text)

    if task := _debounce.get(chat_id):
        task.cancel()
    _debounce[chat_id] = asyncio.create_task(_reply_later(chat_id, name, context))


async def _reply_later(chat_id: int, contact_name: str, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        await asyncio.sleep(GROUP_WINDOW)
    except asyncio.CancelledError:
        return  # a newer message restarted the clock

    texts = _pending.pop(chat_id, [])
    _debounce.pop(chat_id, None)
    if not texts:
        return
    combined = "\n".join(texts)

    try:
        # human-like pause with "typing…" showing
        delay = random.uniform(DELAY_MIN, DELAY_MAX)
        loop = asyncio.get_running_loop()
        end = loop.time() + delay
        while loop.time() < end:
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            await asyncio.sleep(min(4.5, max(0.1, end - loop.time())))

        mem = ChatMemory(chat_id, contact_name)
        bubbles = await asyncio.to_thread(agent.reply, mem, combined)

        for i, bubble in enumerate(bubbles):
            await context.bot.send_message(chat_id=chat_id, text=bubble)
            if i < len(bubbles) - 1:
                await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
                await asyncio.sleep(random.uniform(1.0, 2.5))

        if FORWARD_TO_OWNER and str(chat_id) != OWNER_ID:
            reply_text = "\n".join(bubbles) if bubbles else "(no reply sent)"
            await _notify_owner(context, f"💬 {contact_name}: {combined}\n🫥 you: {reply_text}")

    except Exception as e:  # noqa: BLE001
        log.exception("failed replying to %s", contact_name)
        await _notify_owner(context, f"⚠️ doppel error replying to {contact_name}: {e}")


def main() -> None:
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        sys.exit("TELEGRAM_BOT_TOKEN is not set — copy .env.example to .env and fill it in.")
    if not OWNER_ID:
        log.warning("OWNER_CHAT_ID is not set — owner commands and forwarding are disabled.")

    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("pause", cmd_pause))
    app.add_handler(CommandHandler("resume", cmd_resume))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("recent", cmd_recent))
    # private chats only; every non-command message (text, media, stickers)
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, on_message))

    log.info("doppel is running — press Ctrl+C to stop")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
