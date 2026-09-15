# TikTok Streak Auto 🚀

Automated daily TikTok streak keeper powered by Python, headless Selenium, and session cookie injection. Runs locally or 100% free in the cloud via GitHub Actions.

---

## ✨ Features

- **No Captcha / Anti-Bot Bypass**: Uses real session cookies (`sessionid`). Never triggers TikTok's login captcha or "maximum attempts reached" firewall.
- **Direct Profile Dispatch**: Navigates directly to friends' profiles (`/@username`) and sends messages—bypasses slow inbox scanning loops.
- **Human-like Anti-Ban Simulation**: Simulates typing character-by-character with randomized keystroke and click delays.
- **Actionable Telegram Alerts (WIB Timezone)**:
  - ✅ **Success**: Reports total messages delivered, duration, and 24-hour WIB timestamp.
  - ❌ **Failure**: Distinguishes between expired session, user not found, DMs blocked, rate limit, or UI timeouts with immediate action steps.
- **Free Cloud Automation**: Ready-to-use GitHub Actions workflow scheduled daily at **08:00 WIB** (01:00 UTC). Auto-uploads debug screenshots on failure.

---

## 🛠️ Local Setup

### 1. Install Dependencies
Ensure you have Python 3.10+ and Google Chrome installed.

```bash
git clone https://github.com/your-username/tiktok-streak.git
cd tiktok-streak
pip install -r requirements.txt
```

### 2. Configure `.env`
Copy the example template:
```bash
cp .env.example .env
```

Fill in your `.env` values:
```env
TIKTOK_SESSION_ID="your_sessionid_cookie"
MESSAGE="🔥 Daily Streak"

# Optional Telegram Notifications
TELEGRAM_BOT_TOKEN="123456789:ABCdefGHIjklMNOpqrSTUvwxYZ"
TELEGRAM_CHAT_ID="987654321"
```

### 3. Extract Your TikTok `sessionid`
1. Open Google Chrome (or your normal browser), go to [tiktok.com](https://www.tiktok.com), and log in.
2. Press `F12` to open DevTools:
   - **Chrome / Edge**: Go to **Application** -> **Cookies** -> `https://www.tiktok.com`.
   - **Firefox**: Press `Shift + F9` or go to **Storage** -> **Cookies** -> `https://www.tiktok.com`.
3. Locate the row named **`sessionid`**, double-click its value, and copy it into `.env`.

### 4. Set Up Friends List
Add TikTok handles (without `@`) to `friends.csv`:
```csv
Username
friend_one
friend_two
```
*(Or set `FRIENDS_LIST="friend_one, friend_two"` directly in `.env`).*

### 5. Run
```bash
python main.py
```

---

## 📱 Telegram Alerts Setup (2 Minutes)

Get instant status updates directly on your phone:

1. Open Telegram and search for [`@BotFather`](https://t.me/BotFather).
2. Send `/newbot`, choose a name and username. Copy the generated **API Token** (`TELEGRAM_BOT_TOKEN`).
3. Search for [`@userinfobot`](https://t.me/userinfobot) and press `/start`. Copy your **Id** (`TELEGRAM_CHAT_ID`).
4. Start a chat with your newly created bot and send any message (e.g. `/start`) so it can send you alerts.

---

## ☁️ 100% Free Cloud Automation (GitHub Actions)

Run the streak keeper daily without keeping your computer turned on. (Consumes ~15 minutes per month out of 2,000 free minutes).

1. **Fork or push** this repository to your GitHub account (make it **Private** to keep your secrets and code private).
2. Go to your repository on GitHub:
   - **Settings** -> **Secrets and variables** -> **Actions** -> **New repository secret**.
3. Add the following secrets:

| Secret Name | Description | Example |
|---|---|---|
| `TIKTOK_SESSION_ID` | Your copied TikTok session cookie | `a1b2c3d4...` |
| `MESSAGE` | The message to send | `🔥 Streak` |
| `FRIENDS_LIST` | Comma-separated usernames | `celuley, friend2` |
| `TELEGRAM_BOT_TOKEN` | (Optional) Telegram bot token | `123456:ABC...` |
| `TELEGRAM_CHAT_ID` | (Optional) Your Telegram numeric ID | `987654321` |

4. **Trigger Manually or Wait for Schedule**:
   - The workflow runs automatically every day at **08:00 WIB** (`01:00 UTC`).
   - To test immediately: Go to the **Actions** tab -> Select **TikTok Daily Streak** -> Click **Run workflow**.

---

## 🔍 Troubleshooting & Failure Codes

| Alert Scenario | Cause | Solution |
|---|---|---|
| `SessionExpiredError` | Cookie expired or logged out | Grab fresh `sessionid` from browser and update secret. |
| `UserNotFoundError` | Username changed or account deleted | Check spelling in `friends.csv` or `FRIENDS_LIST`. |
| `DMBlockedError` | Direct Message button not visible | Ensure mutual follow or check friend's DM privacy settings. |
| `RateLimitError` | Sending too many messages quickly | Script stops automatically; cooldown resets in 24 hours. |
| `TimeoutError` | TikTok page layout changed | Download `debug-diagnostics` artifact from GitHub Actions run to see the screenshot. |

---

## 📄 License
MIT License
