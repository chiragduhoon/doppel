"""Per-contact chat history, persisted as one JSON file per chat under data/chats/."""

import json
import re
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CHATS_DIR = PROJECT_ROOT / "data" / "chats"

MAX_TURNS = 60  # individual messages kept per chat (~30 exchanges)


class ChatMemory:
    def __init__(self, chat_id, contact_name: str | None = None):
        safe = re.sub(r"[^-\w]", "_", str(chat_id))
        self.chat_id = safe
        self.path = CHATS_DIR / f"{safe}.json"
        data = {}
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                data = {}
        self.contact_name = contact_name or data.get("contact_name") or "them"
        self.turns: list[dict] = data.get("turns", [])

    def add(self, role: str, content: str) -> None:
        self.turns.append({"role": role, "content": content, "ts": time.time()})
        if len(self.turns) > MAX_TURNS:
            self.turns = self.turns[-MAX_TURNS:]
        self.save()

    def messages(self) -> list[dict]:
        msgs = [{"role": t["role"], "content": t["content"]} for t in self.turns]
        while msgs and msgs[0]["role"] != "user":
            msgs.pop(0)
        return msgs

    def save(self) -> None:
        CHATS_DIR.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"contact_name": self.contact_name, "turns": self.turns}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )


def recent_chats(n: int = 5) -> list[dict]:
    """Most recently active chats: [{name, chat_id, last_them, last_you, when}]."""
    if not CHATS_DIR.exists():
        return []
    files = sorted(CHATS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    out = []
    for path in files[:n]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        turns = data.get("turns", [])
        last_them = next((t["content"] for t in reversed(turns) if t["role"] == "user"), "")
        last_you = next((t["content"] for t in reversed(turns) if t["role"] == "assistant"), "")
        out.append({
            "name": data.get("contact_name", "?"),
            "chat_id": path.stem,
            "last_them": last_them,
            "last_you": last_you,
            "when": time.strftime("%d %b %H:%M", time.localtime(path.stat().st_mtime)),
        })
    return out
