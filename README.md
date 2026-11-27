# Amul Report Automation

Automates logging into the Amul WD Portal (`https://wd.amul.in/`), reading the 2FA OTP from Gmail over IMAP, capturing a screenshot of the Customer Invoice table, and sending it to Telegram.

You can run it locally, trigger it manually from GitHub Actions, or trigger it from Telegram through a Cloudflare Worker.

## Prerequisites

- Amul WD Portal login code (`AMUL_WD_CODE`).
- Gmail account that receives Amul OTP emails, using a 16-character Google App Password (`GMAIL_APP_PASSWORD`). Do not use your regular Gmail password; create an App Password at `https://myaccount.google.com/apppasswords` (requires 2-Step Verification).
- Telegram bot token from `@BotFather` (`TELEGRAM_BOT_TOKEN`) and your numeric chat ID from `@userinfobot` (`TELEGRAM_CHAT_ID`).

## Local Setup

1. Copy `.env.example` to `.env` and fill in your credentials:

   ```bash
   cp .env.example .env
   ```

   `GMAIL_APP_PASSWORD` must be a Google App Password, not your regular Gmail password.

2. Install the Python dependencies and the Chromium browser binary for Playwright:

   ```bash
   pip install -r requirements.txt
   playwright install chromium
   ```

   If Google Chrome is already installed locally, you can skip `playwright install chromium` and set `PLAYWRIGHT_CHANNEL=chrome` in `.env` instead.

3. Run the script:

   ```bash
   python main.py
   ```

You can optionally pass a date argument on the command line to override `DATE_FILTER`:

- `python main.py t` fetches today's report.
- `python main.py y` fetches yesterday's report.
- With no argument, `DATE_FILTER` defaults to `smart` (uses yesterday between 00:00 and 06:00 IST, and today otherwise).

For local debugging, set `HEADLESS_MODE=false` in `.env` to keep the browser window visible.

## GitHub Actions Setup

The workflow in `.github/workflows/report.yml` runs on `ubuntu-latest` using the pre-installed Chrome browser with pip caching.

1. Create a **private** GitHub repository and push this project to the `master` branch (`.gitignore` keeps `.env` out of git):

   ```bash
   git init
   git add .
   git commit -m "Initial commit"
   git branch -M master
   git remote add origin https://github.com/<YOUR_GITHUB_USERNAME>/<REPO_NAME>.git
   git push -u origin master
   ```

2. In your GitHub repository, open **Settings > Secrets and variables > Actions** and add these 5 repository secrets:

   | Secret | Description |
   | :--- | :--- |
   | `AMUL_WD_CODE` | Your Amul WD code (e.g. `YOUR_WD_CODE`) |
   | `GMAIL_ADDRESS` | Gmail address that receives Amul OTP emails |
   | `GMAIL_APP_PASSWORD` | 16-character Google App Password (not your regular Gmail password) |
   | `TELEGRAM_BOT_TOKEN` | Bot token from `@BotFather` |
   | `TELEGRAM_CHAT_ID` | Numeric chat ID from `@userinfobot` |

3. To trigger a report manually, open the **Actions** tab, select **Generate Amul Invoice Report**, and click **Run workflow**. You can optionally set `date_filter` to `smart`, `Today`, or `Yesterday`.

## Cloudflare Worker Setup (Telegram Triggers)

To request reports directly from Telegram chat:

1. In the Cloudflare dashboard, open **Workers & Pages** and deploy a new Worker using `cloudflare_webhook_worker.js`.
2. Under the Worker's **Settings > Variables and Secrets**, add these environment variables (store sensitive tokens as secrets):

   | Variable | Required | Description |
   | :--- | :--- | :--- |
   | `TELEGRAM_BOT_TOKEN` | Yes | Bot token from `@BotFather` |
   | `TELEGRAM_CHAT_ID` | Yes | Allowed numeric Telegram chat ID |
   | `GITHUB_PAT` | Yes | GitHub Personal Access Token with permission to trigger Actions workflows (`Actions: Read and write` for a fine-grained token, or `repo` scope for a classic token) |
   | `GITHUB_REPO` | Yes | Target repository in `owner/repo` format |
   | `TELEGRAM_WEBHOOK_SECRET` | No | Optional secret token used to verify incoming Telegram webhook requests |

3. Point your Telegram bot webhook to your Worker URL:

   ```bash
   curl -X POST "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/setWebhook" \
     -H "Content-Type: application/json" \
     -d '{"url": "https://<your-worker>.workers.dev", "secret_token": "<TELEGRAM_WEBHOOK_SECRET>"}'
   ```

   If you did not configure `TELEGRAM_WEBHOOK_SECRET`, omit `"secret_token"` from the request payload.

### Telegram Commands

Once the webhook is configured, send any of these commands to your bot:

- `/t`: Fetch today's invoice report.
- `/y`: Fetch yesterday's invoice report.
- `/r`: Fetch the invoice report using `smart` date mode (yesterday between 00:00 and 06:00 IST, today otherwise).
