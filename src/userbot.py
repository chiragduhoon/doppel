"""Doppel userbot — logs into YOUR REAL Telegram account and replies as you.

Unlike bot.py (a separate @username), this uses Telegram's user API (Telethon)
to act as your own account, so people who already have you saved get replies
from the real "you" — no separate bot contact needed.

First run is interactive: Telegram will text/app-notify you a login code, and
you'll be asked to type your phone number and that code once. After that, a
local session file keeps you logged in — you won't be asked again.

Run: python src/userbot.py   (needs .env — see .env.example)

Owner commands — send these to yourself in "Saved Messages" (the chat you get
when you search your own name/number, or tap your own avatar):
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
# without this, logging a message that contains an emoji crashes the userbot.
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

import agent  # noqa: E402
import memory as memory_mod  # noqa: E402
from memory import ChatMemory  # noqa: E402

from telethon import TelegramClient, events  # noqa: E402
from telethon.tl.custom import Message  # noqa: E402

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("telethon").setLevel(logging.WARNING)
log = logging.getLogger("doppel-userbot")

FORWARD_TO_OWNER = (os.getenv("FORWARD_TO_OWNER") or "false").strip().lower() == "true"
DELAY_MIN = float(os.getenv("REPLY_DELAY_MIN") or 2)
DELAY_MAX = float(os.getenv("REPLY_DELAY_MAX") or 12)
GROUP_WINDOW = 4.0  # seconds to wait for rapid-fire follow-up messages before replying

STATE_FILE = PROJECT_ROOT / "data" / "state.json"
SESSION_FILE = str(PROJECT_ROOT / "data" / "doppel_userbot")  # Telethon appends .session

NEVER_AUTOREPLY = {
    s.strip().lstrip("@").lower()
    for s in (os.getenv("NEVER_AUTOREPLY") or "").split(",")
    if s.strip()
}

# chat_id -> pending texts / debounce task
_pending: dict[int, list[str]] = {}
_debounce: dict[int, asyncio.Task] = {}

_client: TelegramClient | None = None
_me = None  # your own Telethon User entity, set once connected


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

def _is_excluded(sender) -> bool:
    if sender is None:
        return False
    candidates = [str(sender.id)]
    if getattr(sender, "username", None):
        candidates.append(sender.username.lower())
    return any(c.lower() in NEVER_AUTOREPLY for c in candidates)


def _contact_name(sender) -> str:
    if sender is None:
        return "them"
    name = " ".join(p for p in [getattr(sender, "first_name", None), getattr(sender, "last_name", None)] if p)
    return name or (getattr(sender, "username", None) or "them")


def _placeholder_for_media(msg: Message) -> str | None:
    if msg.voice or msg.audio:
        return "[they sent a voice note you can't listen to right now — react naturally, maybe say you'll hear it later]"
    if msg.photo or msg.video or msg.video_note or msg.gif:
        return "[they sent a photo/video you can't open right now — react naturally, maybe say you'll check it later]"
    if msg.sticker:
        return "[they sent a sticker — react casually or ignore it]"
    if msg.document:
        return "[they sent a file you can't open right now — react naturally]"
    return None


async def _notify_owner(text: str) -> None:
    try:
        await _client.send_message("me", text)  # "Saved Messages"
    except Exception:  # noqa: BLE001
        log.warning("could not notify Saved Messages")


# ---------- owner commands (sent to yourself, in Saved Messages) ----------

async def _handle_owner_command(text: str) -> None:
    cmd, *rest = text.strip().split(maxsplit=1)
    cmd = cmd.lower()

    if cmd == "/pause":
        _set_paused(True)
        await _notify_owner("paused — I won't reply to anyone until /resume")
    elif cmd == "/resume":
        _set_paused(False)
        await _notify_owner("resumed — replying as you again")
    elif cmd == "/status":
        provider = (os.getenv("LLM_PROVIDER") or "gemini").lower()
        model = os.getenv("LLM_MODEL") or agent.llm.DEFAULT_MODELS.get(provider, "?")
        n_chats = len(list(memory_mod.CHATS_DIR.glob("*.json"))) if memory_mod.CHATS_DIR.exists() else 0
        await _notify_owner(
            f"provider: {provider}\nmodel: {model}\npaused: {_is_paused()}\n"
            f"chats: {n_chats}\nforward to saved messages: {FORWARD_TO_OWNER}\n"
            f"never auto-reply: {', '.join(NEVER_AUTOREPLY) or '(none set)'}"
        )
    elif cmd == "/recent":
        try:
            n = int(rest[0]) if rest else 5
        except ValueError:
            n = 5
        chats = memory_mod.recent_chats(n)
        if not chats:
            await _notify_owner("no conversations yet")
            return
        lines = [
            f"— {c['name']} ({c['when']})\n   them: {c['last_them'][:80]}\n   you:  {c['last_you'][:80]}"
            for c in chats
        ]
        await _notify_owner("\n".join(lines))


# ---------- conversation flow ----------

async def _on_new_message(event: events.NewMessage.Event) -> None:
    msg: Message = event.message

    # Messages you send to yourself in "Saved Messages" are owner commands.
    if event.is_private and msg.out and event.chat_id == _me.id:
        if msg.text and msg.text.strip().startswith("/"):
            await _handle_owner_command(msg.text)
        return

    if not event.is_private or msg.out:
        return  # ignore groups/channels and your own outgoing messages elsewhere

    sender = await event.get_sender()
    if _is_excluded(sender):
        return  # e.g. family — you always want to answer these yourself

    text = msg.text or _placeholder_for_media(msg)
    if not text:
        return

    if _is_paused():
        if FORWARD_TO_OWNER:
            await _notify_owner(f"(paused) 💬 {_contact_name(sender)}: {text}")
        return

    await _enqueue(event.chat_id, _contact_name(sender), text)


async def _enqueue(chat_id: int, name: str, text: str) -> None:
    """Collect rapid-fire messages for GROUP_WINDOW seconds, then answer them together."""
    _pending.setdefault(chat_id, []).append(text)
    if task := _debounce.get(chat_id):
        task.cancel()
    _debounce[chat_id] = asyncio.create_task(_reply_later(chat_id, name))


async def _reply_later(chat_id: int, contact_name: str) -> None:
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
        delay = random.uniform(DELAY_MIN, DELAY_MAX)
        async with _client.action(chat_id, "typing"):
            await asyncio.sleep(delay)

        mem = ChatMemory(chat_id, contact_name)
        bubbles = await asyncio.to_thread(agent.reply, mem, combined)

        for i, bubble in enumerate(bubbles):
            await _client.send_message(chat_id, bubble)
            if i < len(bubbles) - 1:
                async with _client.action(chat_id, "typing"):
                    await asyncio.sleep(random.uniform(1.0, 2.5))

        if FORWARD_TO_OWNER:
            reply_text = "\n".join(bubbles) if bubbles else "(no reply sent)"
            await _notify_owner(f"💬 {contact_name}: {combined}\n🫥 you: {reply_text}")

    except Exception as e:  # noqa: BLE001
        log.exception("failed replying to %s", contact_name)
        await _notify_owner(f"⚠️ doppel error replying to {contact_name}: {e}")


async def main() -> None:
    global _client, _me

    api_id = (os.getenv("TELEGRAM_API_ID") or "").strip()
    api_hash = (os.getenv("TELEGRAM_API_HASH") or "").strip()
    if not api_id or not api_hash:
        sys.exit(
            "TELEGRAM_API_ID / TELEGRAM_API_HASH are not set.\n"
            "Get them from https://my.telegram.org/apps and put them in .env"
        )

    SESSION_FILE_PATH = Path(SESSION_FILE)
    SESSION_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)

    _client = TelegramClient(SESSION_FILE, int(api_id), api_hash)
    _client.add_event_handler(_on_new_message, events.NewMessage())

    await _client.start()  # first run: prompts for phone number + login code, interactively
    _me = await _client.get_me()
    log.info("logged in as %s %s (@%s)", _me.first_name, _me.last_name or "", _me.username or "-")
    log.info("doppel userbot is running — press Ctrl+C to stop")
    if NEVER_AUTOREPLY:
        log.info("excluded from auto-reply: %s", ", ".join(NEVER_AUTOREPLY))

    async with _client:
        await _client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
