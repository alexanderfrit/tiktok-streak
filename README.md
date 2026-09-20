# TikTok Streak Auto 🚀

Automated TikTok streak keeper powered by Python, headless Selenium, CDP anti-detection, and session cookie injection. Runs 100% inside your TikTok inbox (`/messages`) to eliminate captchas. Runs locally or free on GitHub Actions.

---

## ✨ Features

- **No Captcha / Anti-Bot Stealth**: Stays strictly inside `tiktok.com/messages`. Never visits external profiles or video watch pages, preventing TikTok `secsdk-captcha` triggers.
- **Session Cookie Injection**: Uses valid `sessionid` cookies. Completely avoids username/password forms, email 2FA, and puzzle sliders.
- **Human-Like Simulation**: Types keystroke-by-keystroke with randomized delays (`0.04s - 0.12s`).
- **Multi-Account Support**: Manage multiple TikTok accounts with isolated sessions and custom friend lists.
- **Actionable Telegram Alerts (24h WIB)**:
  - ✅ **Success**: Clean summary with sent count, duration, and timestamp.
  - ❌ **Failure**: Explicit errors (`SessionExpiredError`, `UserNotFoundError`, `RateLimitError`) with instant action steps.
- **100% Free Cloud Scheduling**: Runs daily at **08:00 WIB** (`01:00 UTC`) on GitHub Actions (~15 minutes/month out of 2,000 free minutes).

---

## 📁 Project Structure

```text
tiktok-streak/
├── .github/workflows/streak.yml  # Automated daily cloud runner
├── src/
│   ├── actions.py               # Inbox navigation, human typing, message sending
│   ├── browser.py               # Stealth Chrome launcher & session injector
│   ├── config.py                # Multi-account & single-account config parser
│   ├── exceptions.py            # Classified errors with actionable fix advice
│   └── notifier.py              # Telegram reports with 24h WIB timestamps
├── main.py                      # Main automation entry point
├── fetch_friends.py             # Tool to scrape mutual friends from inbox
├── test_runner.py               # Self-check test suite
├── accounts.example.json        # Template for multi-account mode
├── friends.csv.example          # Template for friends list
├── .env.example                 # Template for single-account mode
└── requirements.txt             # Minimal dependencies (selenium, python-dotenv)
```

---

## 🖥️ One-Click Setup GUI (Recommended)

Prefer not to touch tokens, secrets and JSON by hand? There is a desktop wizard
(`gui/`) that walks you through the whole setup in one window — GitHub login,
fork, enabling Actions, writing every secret, Telegram, and the TikTok session.

```bash
pip install -r requirements.txt -r requirements-gui.txt
python -m gui.app
```

What it does for you:

1. **GitHub** — paste a fine-grained PAT (scopes: `Contents`, `Workflows`,
   `Secrets`, `Administration` read/write). It forks the source repo into your
   account, **enables Actions** (forks have them off by default) and writes all
   repo secrets with correct names and formats.
2. **TikTok** — reads the `sessionid` straight from your installed browser's
   cookie store (Chrome, Edge, Brave, Chromium, Vivaldi, Opera, Firefox on
   Windows). Log in to TikTok in that browser first, then click *Read cookies*.
   Close the browser if reading fails (the cookie DB is locked while it runs).
   A **manual paste** field is always available as a fallback (Safari and any
   browser without on-disk cookies are not supported).
3. **Telegram** — reminders only; it links you to `@BotFather` (Telegram has no
   API to create a bot) and auto-detects your chat id via `getUpdates`.
4. **Repo & Run** — sets your message, friends, video share and photo options,
   then dispatches the workflow and shows the run status.

The GUI needs `pywebview`, `PyNaCl` and (Windows) `pywin32` + `pycryptodome`;
these ship in `requirements-gui.txt` and are **not** installed on the cloud
runner, so the automated workflow is unaffected.

---

## 🛠️ Quick Start (Local Setup)

### 1. Install Dependencies
Ensure Python 3.10+ and Google Chrome are installed:

```bash
git clone https://github.com/your-username/tiktok-streak.git
cd tiktok-streak
pip install -r requirements.txt
```

### 2. Extract Your TikTok `sessionid` Cookie
1. Open Chrome (or your normal browser), go to [tiktok.com](https://www.tiktok.com), and log in.
2. Press `F12` to open DevTools:
   - **Chrome / Edge**: Go to **Application** -> **Cookies** -> `https://www.tiktok.com`.
   - **Firefox**: Press `Shift + F9` -> **Cookies** -> `https://www.tiktok.com`.
3. Double-click the value of the cookie named **`sessionid`** and copy it.

---

## ⚙️ Configuration Modes

### Option A: Single Account (Simplest)
Create a `.env` file:
```bash
cp .env.example .env
```
Edit `.env`:
```env
TIKTOK_SESSION_ID="your_sessionid_cookie_here"
MESSAGE="🔥 Daily Streak"
FRIENDS_LIST="friend_handle_1, friend_handle_2"

# Local Browser: true = headless (background), false = visible window
HEADLESS=true
```

### Option B: Multi-Account Mode
Copy the template:
```bash
cp accounts.example.json accounts.json
```
Edit `accounts.json`:
```json
[
  {
    "name": "Main_Account",
    "session_id": "sessionid_cookie_1",
    "friends": ["friend_one"],
    "message": "🔥 Daily Streak"
  },
  {
    "name": "Alt_Account",
    "session_id": "sessionid_cookie_2",
    "friends": ["friend_two"],
    "message": "🔥 Streak!"
  }
]
```
*(Note: `accounts.json` is gitignored to keep your account credentials safe).*

---

## 🚀 Running the App

### Headless Run (Background)
```bash
python main.py
```

### Visible Desktop Run (Watch It Work Live)
```bash
python main.py --headful
```

### Scrape Friends from Inbox (Optional)
Automatically grab your mutual friends' handles into `friends.csv`:
```bash
python fetch_friends.py
```

---

## 📱 Telegram Alerts Setup (Optional)

Receive instant status reports directly on your phone:

1. Open Telegram, search for [`@BotFather`](https://t.me/BotFather), send `/newbot`, and copy the **API Token** (`TELEGRAM_BOT_TOKEN`).
2. Search for [`@userinfobot`](https://t.me/userinfobot), click `/start`, and copy your **Id** (`TELEGRAM_CHAT_ID`).
3. Send a message (e.g. `/start`) to your newly created bot.
4. Add the token and ID to `.env` or GitHub Secrets.

---

## ☁️ 100% Free Cloud Automation (GitHub Actions)

Run daily in the cloud without keeping your PC powered on:

1. Push or fork this repository to your GitHub account (**Make it Private**).
2. On GitHub, navigate to **Settings** -> **Secrets and variables** -> **Actions** -> **New repository secret**.
3. Add the secrets below:

| Secret Name | Required? | Description |
|---|---|---|
| `TIKTOK_SESSION_ID` | Yes (if single account) | Your copied TikTok cookie |
| `MESSAGE` | Optional | Custom streak message (default: `🔥 Daily Streak`) |
| `FRIENDS_LIST` | Yes (if single account) | Comma-separated friend usernames: `user1, user2` |
| `TIKTOK_ACCOUNTS_JSON`| Alternative (multi-account) | The raw content of your `accounts.json` |
| `TELEGRAM_BOT_TOKEN` | Optional | Telegram Bot API Token |
| `TELEGRAM_CHAT_ID` | Optional | Your Telegram User ID |

4. **Schedule (Modify Hours & Multiple Times)**:
   - Edit `.github/workflows/streak.yml`.
   - GitHub Actions cron runs in **UTC** (Conversion: `UTC = WIB - 7 hours`).

| Desired Times (WIB) | Equivalent (UTC) | Cron Syntax in `streak.yml` |
|---|---|---|
| **08:00 WIB** (Once daily) | 01:00 UTC | `cron: '0 1 * * *'` |
| **08:00 & 20:00 WIB** (Twice daily) | 01:00 & 13:00 UTC | `cron: '0 1,13 * * *'` |
| **08:00, 14:00, 20:00 WIB** (3 times daily) | 01:00, 07:00, 13:00 UTC | `cron: '0 1,7,13 * * *'` |
| **Every 6 hours** (4 times daily) | 00:00, 06:00, 12:00, 18:00 UTC | `cron: '0 */6 * * *'` |

   - To test immediately: Go to **Actions** -> **TikTok Daily Streak** -> **Run workflow**.

---

## 💻 Local Multiple Times Scheduling (Windows)

To send multiple times a day on this computer, create tasks in Windows Task Scheduler:

```powershell
# Send at 08:00 WIB
schtasks /Create /TN "TikTokStreak_08" /TR "pythonw.exe C:\Users\Fritz\Desktop\streaktiktok\main.py" /SC DAILY /ST 08:00 /F

# Send at 14:00 WIB
schtasks /Create /TN "TikTokStreak_14" /TR "pythonw.exe C:\Users\Fritz\Desktop\streaktiktok\main.py" /SC DAILY /ST 14:00 /F

# Send at 20:00 WIB
schtasks /Create /TN "TikTokStreak_20" /TR "pythonw.exe C:\Users\Fritz\Desktop\streaktiktok\main.py" /SC DAILY /ST 20:00 /F
```

To remove a local scheduled task:
```powershell
schtasks /Delete /TN "TikTokStreak_08" /F
```

---

## 🔍 Troubleshooting & Failure Codes

| Error | Root Cause | Immediate Action |
|---|---|---|
| `SessionExpiredError` | Cookie expired or user logged out | Copy a fresh `sessionid` from your browser into `.env` / GitHub Secrets. |
| `UserNotFoundError` | Handle not found in inbox | Make sure a mutual conversation thread exists with that friend. |
| `RateLimitError` | Sent too many messages too quickly | Script automatically stops; cooldown resets in 24 hours. |
| `TimeoutError` | TikTok inbox layout changed | Download the `debug-diagnostics` artifact from the GitHub Actions run to inspect the screenshot. |

---

## 🧪 Testing

Run the automated self-check test suite anytime:
```bash
python test_runner.py
```

---

## 📄 License
MIT License
