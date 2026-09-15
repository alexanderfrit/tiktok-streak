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
    "friends": ["celuley"],
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

4. **Schedule**:
   - The workflow runs automatically every day at **08:00 WIB** (`01:00 UTC`).
   - To test immediately: Go to **Actions** -> **TikTok Daily Streak** -> **Run workflow**.

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
