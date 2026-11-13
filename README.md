# doppel

Texts people **as you** when you're away — your persona, your slang, your opinions, with per-contact memory.

Two ways to run it:

| | `src/bot.py` | `src/userbot.py` |
|---|---|---|
| Connects as | a separate bot account (`@yourname_bot`) | **your real Telegram account** |
| People message | the bot's own link | your normal contact/number, like always |
| Setup | official Bot API, zero risk | logs in with your phone number (Telethon) |
| Risk | none — bots are meant for this | automating a real personal account is against the spirit of Telegram's ToS for heavy use; light personal auto-reply is commonly done but isn't officially risk-free |

Pick based on how much you trust the "never admits it's a bot" behavior around people who matter, and how much risk you're OK with on your real account.

## How it works

- `persona/persona.md` — who you are (copy `persona/persona.example.md`; the real file is git-ignored)
- `persona/contacts.json` — optional per-contact instructions matched by name (copy `persona/contacts.example.json`; git-ignored because it names real people)
- `persona/style_examples.json` — how you actually text (extracted from your real chat exports)
- Both get baked into the system prompt; every contact gets their own remembered conversation in `data/chats/`
- Runs on the free Gemini API tier by default; can be switched to Claude later

## Setup (10 minutes)

### 1. Install

```powershell
cd doppel
pip install -r requirements.txt
copy .env.example .env
```

### 2. Get your keys and put them in `.env`

- **Gemini key (free):** https://aistudio.google.com/apikey → `GEMINI_API_KEY`

Then pick one Telegram mode:

**Bot-account mode:** message [@BotFather](https://t.me/BotFather) → `/newbot` → pick a name/username → copy the token → `TELEGRAM_BOT_TOKEN`. Also get **your** Telegram id from [@userinfobot](https://t.me/userinfobot) → `OWNER_CHAT_ID`.

**Userbot mode (replies from your real account):** go to https://my.telegram.org/apps, log in with your own phone number, create an app (any name/platform is fine) → copy **App api_id** and **App api_hash** into `TELEGRAM_API_ID` / `TELEGRAM_API_HASH`. Optionally set `NEVER_AUTOREPLY` to a comma-separated list of usernames/ids (e.g. family) who should always get a real reply from you, never the bot.

### 3. Teach it to be you

1. Fill in every section of `persona/persona.md` (the more honest and specific, the better it mimics you).
2. Feed it real chats — **Instagram, WhatsApp, and Telegram exports all work**:

**Instagram (your main one):** Instagram app → Accounts Center → *Your information and permissions* → *Download your information* → *Some of your information* → tick **Messages** → format **JSON** → request. When the zip arrives, each chat is a folder like `your_instagram_activity/messages/inbox/rahul_12345/`. Then:

```powershell
python scripts/extract_style.py "path\to\inbox\rahul_12345"
```

**WhatsApp:** chat → ⋮ → More → *Export chat* → *Without media*, then pass the `.txt` file the same way. **Telegram Desktop:** Export chat history as JSON, pass `result.json`.

The script asks which participant is you and writes `persona/style_examples.json`. Run it on 2–3 different chats with `--append` to capture how you talk to different people.

### 4. Test locally first

```powershell
python src/cli.py
```

Chat with yourself in the terminal. Iterate on `persona/persona.md` (it's re-read live) until replies feel like you. Try asking it "are you a bot?" and see how it deflects.

### 5. Go live

**Bot-account mode:**

```powershell
python src/bot.py
```

Share your bot's `t.me/...` link, or pin it in your bio. Owner commands (`/pause`, `/resume`, `/status`, `/recent 5`) are sent from your own Telegram account, in a chat with the bot.

**Userbot mode:**

```powershell
python src/userbot.py
```

First run only: Telegram texts/app-notifies you a login code — type your phone number and that code into the terminal when asked (2FA password too, if you have one set). After that, a local session file keeps you logged in and it won't ask again. People message **you**, exactly like always — no separate contact needed. Owner commands (`/pause`, `/resume`, `/status`, `/recent 5`) are sent to yourself, in **Saved Messages** (search your own name, or tap your own avatar).

Both modes only answer private chats and only run while their terminal window is open. Set `FORWARD_TO_OWNER=true` in `.env` to get every conversation mirrored to you in real time (bot mode: your own chat with the bot; userbot mode: Saved Messages).

## Safety and honesty

An agent that texts as you can do real damage, so the prompt sets hard limits (`src/agent.py`):
- never promises to meet, call, pay, lend or send anything;
- never shares OTPs, passwords, addresses, bank/UPI details or private information about anyone;
- serious topics (health, money, legal, emergencies, emotional conversations) get a short "caught up, will reply properly soon" instead of an improvised answer;
- never invents checkable events ("I just called you").

**Honest mode:** set `STAY_IN_CHARACTER=false` in `.env` and, when someone asks "is this really you?", the bot says it's an auto-reply assistant answering while you're away. Recommended for anyone you haven't told about the bot.

Nothing personal is committed: `.env`, the Telegram session, chat histories, your persona and per-contact rules are all git-ignored.

## Tests

```bash
pip install pytest
pytest -q     # prompt building, per-contact rules, honest mode; no API key or persona needed
```

## Behavior notes

- Waits a few seconds and shows "typing…" so replies feel human; groups rapid-fire messages and answers them together; may reply in 1–3 short bubbles like real texting.
- Hard boundaries are baked in regardless of persona: it never promises to meet/call/pay, never shares OTPs/passwords/addresses/bank details, and backs off to "I'm caught up, will get back to you" on serious topics (emergencies, money, legal, emotional).
- `STAY_IN_CHARACTER=true` (your chosen default) means it never admits to being a bot — it deflects casually. **Heads up:** friends who find out later may not love this; flip it to `false` any time to make it honestly say it's your auto-reply assistant when directly asked.
- **Userbot mode only:** `NEVER_AUTOREPLY` is your safety net — anyone listed there (by @username or numeric id) never gets an automated reply, full stop, regardless of persona or pause state. Use it for family or anyone where a bot reply standing in for you would actually be a problem.

## Switching to Claude later

For noticeably better persona fidelity (paid): `pip install anthropic`, then in `.env`:

```
LLM_PROVIDER=anthropic
LLM_MODEL=claude-opus-4-8
ANTHROPIC_API_KEY=sk-ant-...
```

## Next steps (not built yet)

- 24/7 hosting: it's a single polling process — a free-tier VPS, Raspberry Pi, or any always-on box works (`python src/bot.py`).
- Voice notes/images are acknowledged naturally but not actually opened.
