"""The persona agent: builds the system prompt and produces replies.

persona/persona.md and persona/style_examples.json are re-read on every reply,
so you can edit the persona while the bot is running and it takes effect
on the next message.
"""

import json
import os
from pathlib import Path

import llm
from memory import ChatMemory

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PERSONA_FILE = PROJECT_ROOT / "persona" / "persona.md"
STYLE_FILE = PROJECT_ROOT / "persona" / "style_examples.json"
CONTACTS_FILE = PROJECT_ROOT / "persona" / "contacts.json"

MAX_STYLE_EXAMPLES = 60
MAX_BUBBLES = 3


def _load_persona() -> str:
    if not PERSONA_FILE.exists():
        raise RuntimeError(f"Persona file missing: {PERSONA_FILE}. Copy persona/persona.example.md to "
                           "persona/persona.md and fill it in before running.")
    return PERSONA_FILE.read_text(encoding="utf-8").strip()


def _load_style_block() -> str:
    if not STYLE_FILE.exists():
        return ""
    try:
        pairs = json.loads(STYLE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ""
    lines = []
    for p in pairs[:MAX_STYLE_EXAMPLES]:
        them, me = (p.get("them") or "").strip(), (p.get("me") or "").strip()
        if them and me:
            lines.append(f"Them: {them}\nYou: {me}")
    if not lines:
        return ""
    return (
        "=== HOW YOU ACTUALLY TEXT (real examples of your past replies — copy this style exactly) ===\n"
        + "\n---\n".join(lines)
    )


def _identity_rule() -> str:
    stay = (os.getenv("STAY_IN_CHARACTER") or "true").strip().lower() != "false"
    if stay:
        return (
            "- Never mention AI, bots, models or assistants. If someone asks whether you're a bot "
            "or says \"is this really you?\", brush it off casually the way you would (\"lol what\", "
            "\"bro??\") and move on. Do NOT construct proof or elaborate stories about being human."
        )
    return (
        "- If someone directly asks whether you're a bot or really them, be honest: say you're an "
        "auto-reply assistant answering while the real person is away, then carry on casually."
    )


def _load_contact_rules() -> list[dict]:
    """Per-contact instructions live in persona/contacts.json (git-ignored: it names real people)."""
    if not CONTACTS_FILE.exists():
        return []
    try:
        return json.loads(CONTACTS_FILE.read_text(encoding="utf-8")).get("contacts", [])
    except (json.JSONDecodeError, AttributeError):
        return []


def _contact_specific_rule(contact_name: str) -> str:
    """Return the first rule whose match words appear in the contact's name (case-insensitive)."""
    normalized = (contact_name or "").strip().lower()
    for entry in _load_contact_rules():
        words = [w.lower() for w in entry.get("match", []) if w]
        if normalized and any(w in normalized for w in words):
            return entry.get("rule", "")
    return ""


RULES = """=== HOW TO BEHAVE ===
- You are texting on Telegram as yourself. This is a real, casual conversation — not a service.
- Sound EXACTLY like the persona and the examples: same message length, slang, punctuation habits.
- Match the other person's language (English / Hindi / Hinglish) and their energy.
- Real texts are short. One or two lines is normal. Never write paragraphs unless they did first.
- No markdown, no bullet points, no formal greetings or sign-offs. Never sound like an AI assistant.
- To send multiple separate texts, put each one on its own line (max {max_bubbles} lines).
{identity_rule}
- NEVER promise to meet, call, pay, lend or send anything. If pushed, say you'll confirm later.
- NEVER share OTPs, passwords, addresses, bank/UPI details, or private info about anyone.
- If the conversation turns serious (emergency, health, money, anything legal or emotional), reply
  briefly that you're caught up right now and will get back to them properly soon. Do not improvise.
- Never invent specific checkable events ("I just called you", "I met X today", "I sent it").
- It's fine to not know something or to give a low-effort reply — that's human."""


def build_system_prompt(contact_name: str) -> str:
    parts = [
        "You are the person described below, texting from your own Telegram account. "
        "Reply as yourself — never as an assistant.",
        "=== ABOUT YOU ===\n" + _load_persona(),
    ]
    style = _load_style_block()
    if style:
        parts.append(style)
    contact_rule = _contact_specific_rule(contact_name)
    if contact_rule:
        parts.append(contact_rule)
    parts.append(RULES.format(max_bubbles=MAX_BUBBLES, identity_rule=_identity_rule()))
    parts.append(f"=== THIS CONVERSATION ===\nYou are texting with: {contact_name}")
    return "\n\n".join(parts)


def reply(memory: ChatMemory, incoming_text: str) -> list[str]:
    """Add the incoming message, get a reply, return it as 1..MAX_BUBBLES chat bubbles."""
    memory.add("user", incoming_text)
    system = build_system_prompt(memory.contact_name)
    raw = llm.generate(system, memory.messages())
    bubbles = [ln.strip() for ln in raw.splitlines() if ln.strip()][:MAX_BUBBLES]
    if not bubbles:
        return []
    memory.add("assistant", "\n".join(bubbles))
    return bubbles
