"""Extract your real texting style from chat exports.

Turns an Instagram DM export (message_1.json), a WhatsApp export (.txt), or a
Telegram Desktop export (result.json) into persona/style_examples.json —
pairs of (their message -> your actual reply) that the bot copies the style of.

How to export:
  Instagram: Accounts Center > Your information and permissions > Download your
             information > Some of your information > Messages > JSON format.
             In the downloaded zip, each chat is a folder like
             your_instagram_activity/messages/inbox/rahul_12345/message_1.json —
             pass either that file or the whole folder.
  WhatsApp:  open a chat > three dots > More > Export chat > Without media
  Telegram:  Telegram Desktop > chat > three dots > Export chat history > JSON

Usage:
  python scripts/extract_style.py path/to/inbox/rahul_12345          (Instagram folder)
  python scripts/extract_style.py path/to/message_1.json --me "Chirag"
  python scripts/extract_style.py path/to/chat.txt                   (WhatsApp)
  python scripts/extract_style.py another_chat.txt --append   (add to existing examples)
"""

import argparse
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = PROJECT_ROOT / "persona" / "style_examples.json"

# WhatsApp line, tolerating Android/iOS variants, am/pm, and unicode space marks:
#   26/06/25, 21:47 - Chirag: yo
#   [26/06/2025, 9:47:12 PM] Chirag: yo
LINE_RE = re.compile(
    r"^[‎‏]?\[?"
    r"(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})"
    r",?\s+"
    r"(\d{1,2}:\d{2}(?::\d{2})?[\s  ]?(?:[AaPp]\.?[Mm]\.?)?)"
    r"\]?\s*-?\s*"
    r"([^:]{1,40}?):\s"
    r"(.*)$"
)

SKIP_MARKERS = (
    "<media omitted>",
    "omitted>",
    "this message was deleted",
    "you deleted this message",
    "missed voice call",
    "missed video call",
    "http://",
    "https://",
    "messages and calls are end-to-end encrypted",
    # Instagram system-ish lines
    "sent an attachment",
    "liked a message",
    "reacted to your message",
    "this message is no longer available",
    "shared a story",
    "shared a post",
    "shared a reel",
)


def parse_whatsapp(path: Path) -> list[tuple[str, str]]:
    """Returns [(sender, message), ...] in order."""
    messages: list[tuple[str, str]] = []
    with path.open(encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.rstrip("\n")
            m = LINE_RE.match(line)
            if m:
                sender, text = m.group(3).strip(), m.group(4).strip()
                messages.append((sender, text))
            elif messages and line.strip():
                # continuation of a multi-line message
                sender, text = messages[-1]
                messages[-1] = (sender, text + "\n" + line.strip())
    return messages


def _fix_ig_encoding(s: str) -> str:
    """Instagram exports emoji/Hindi as UTF-8 bytes mis-decoded as Latin-1 — undo that."""
    try:
        return s.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


def parse_instagram(path: Path) -> list[tuple[str, str]]:
    """Instagram data export: a chat folder containing message_*.json, or one such file."""
    files = sorted(path.glob("message_*.json")) if path.is_dir() else [path]
    if not files:
        sys.exit(f"No message_*.json files found in {path}")
    raw: list[tuple[int, str, str]] = []
    for f in files:
        data = json.loads(f.read_text(encoding="utf-8", errors="ignore"))
        for m in data.get("messages", []):
            sender = m.get("sender_name")
            text = m.get("content")
            if not sender or not text:
                continue  # photos, reels, likes, unsent messages have no content
            raw.append((m.get("timestamp_ms", 0), _fix_ig_encoding(sender).strip(), _fix_ig_encoding(text).strip()))
    raw.sort(key=lambda x: x[0])  # Instagram exports are newest-first; make chronological
    return [(s, t) for _, s, t in raw]


def parse_telegram(path: Path) -> list[tuple[str, str]]:
    data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    messages = []
    for m in data.get("messages", []):
        if m.get("type") != "message":
            continue
        sender = m.get("from")
        text = m.get("text", "")
        if isinstance(text, list):
            text = "".join(p if isinstance(p, str) else p.get("text", "") for p in text)
        text = (text or "").strip()
        if sender and text:
            messages.append((sender.strip(), text))
    return messages


def usable(text: str) -> bool:
    t = text.lower()
    if not t or len(text) > 300:
        return False
    return not any(marker in t for marker in SKIP_MARKERS)


def build_pairs(messages: list[tuple[str, str]], me: str) -> list[dict]:
    # merge consecutive messages from the same sender into one block
    blocks: list[tuple[str, str]] = []
    for sender, text in messages:
        if blocks and blocks[-1][0] == sender:
            blocks[-1] = (sender, blocks[-1][1] + "\n" + text)
        else:
            blocks.append((sender, text))

    pairs = []
    for (s1, t1), (s2, t2) in zip(blocks, blocks[1:]):
        if s1 != me and s2 == me and usable(t1) and usable(t2):
            # cap each side at 4 lines so examples stay short
            them = "\n".join(t1.splitlines()[:4]).strip()
            mine = "\n".join(t2.splitlines()[:4]).strip()
            if them and mine:
                pairs.append({"them": them, "me": mine})
    return pairs


def pick_me(messages: list[tuple[str, str]], me_arg: str | None) -> str:
    senders = Counter(s for s, _ in messages)
    if not senders:
        sys.exit("No messages found in this file — is it a real chat export?")
    if me_arg:
        for s in senders:
            if s.lower() == me_arg.lower():
                return s
        sys.exit(f'"{me_arg}" not found. Participants: {", ".join(senders)}')
    print("Who are YOU in this chat?")
    ranked = senders.most_common()
    for i, (s, n) in enumerate(ranked, 1):
        print(f"  {i}. {s}  ({n} messages)")
    choice = input("number> ").strip()
    try:
        return ranked[int(choice) - 1][0]
    except (ValueError, IndexError):
        sys.exit("Invalid choice.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract your texting style from chat exports")
    parser.add_argument("export_file", help="Instagram chat folder / message_1.json, WhatsApp .txt, or Telegram result.json")
    parser.add_argument("--me", help="your name exactly as it appears in the export")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="output json path")
    parser.add_argument("--max", type=int, default=80, help="max example pairs to keep (default 80)")
    parser.add_argument("--append", action="store_true", help="add to existing examples instead of replacing")
    args = parser.parse_args()

    path = Path(args.export_file)
    if not path.exists():
        sys.exit(f"File not found: {path}")

    if path.is_dir():
        messages = parse_instagram(path)
    elif path.suffix.lower() == ".json":
        peek = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
        first = (peek.get("messages") or [{}])[0]
        if "sender_name" in first or "participants" in peek:
            messages = parse_instagram(path)
        else:
            messages = parse_telegram(path)
    else:
        messages = parse_whatsapp(path)
    print(f"Parsed {len(messages)} messages.")

    me = pick_me(messages, args.me)
    pairs = build_pairs(messages, me)
    print(f"Found {len(pairs)} usable (them -> you) reply pairs.")
    if not pairs:
        sys.exit("Nothing usable — try a chattier export, or check the --me name.")

    if len(pairs) > args.max:
        random.seed(42)
        keep = sorted(random.sample(range(len(pairs)), args.max))
        pairs = [pairs[i] for i in keep]

    out = Path(args.out)
    if args.append and out.exists():
        try:
            existing = json.loads(out.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = []
        seen = {(p["them"], p["me"]) for p in existing}
        pairs = existing + [p for p in pairs if (p["them"], p["me"]) not in seen]

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(pairs, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Wrote {len(pairs)} examples to {out}")


if __name__ == "__main__":
    main()
