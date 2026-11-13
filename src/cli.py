"""Chat with your doppel locally — test the persona before going live on Telegram.

Run: python src/cli.py [--fresh]
  --fresh  forget the previous CLI test conversation
"""

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Windows consoles often report cp1252 for stdout even in a UTF-8 codepage —
# without this, printing an emoji (persona replies are full of them) crashes.
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

import agent  # noqa: E402
from memory import ChatMemory, CHATS_DIR  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Local test chat with your doppel")
    parser.add_argument("--fresh", action="store_true", help="start a new test conversation")
    parser.add_argument("--name", default="Test Friend", help="who the bot thinks it's talking to")
    args = parser.parse_args()

    if args.fresh:
        f = CHATS_DIR / "cli.json"
        if f.exists():
            f.unlink()

    provider = (os.getenv("LLM_PROVIDER") or "gemini").lower()
    model = os.getenv("LLM_MODEL") or agent.llm.DEFAULT_MODELS.get(provider, "?")
    print(f"doppel CLI test — {provider} / {model}")
    print(f"pretending to be: {args.name}  (change with --name, reset with --fresh)")
    print("type a message and press Enter. /quit to exit.\n")

    mem = ChatMemory("cli", args.name)

    while True:
        try:
            text = input("them> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text in ("/quit", "/exit", "/q"):
            break
        try:
            bubbles = agent.reply(mem, text)
        except Exception as e:  # noqa: BLE001
            print(f"[error] {e}", file=sys.stderr)
            continue
        if not bubbles:
            print("(model returned an empty reply)")
        for b in bubbles:
            print(f"you>  {b}")


if __name__ == "__main__":
    main()
