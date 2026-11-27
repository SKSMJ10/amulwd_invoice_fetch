import os
import time

import requests
from dotenv import load_dotenv

load_dotenv(override=True)

from main import (
    AMUL_WD_CODE,
    generate_report,
    send_telegram_message,
    send_telegram_photo,
)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = str(os.getenv("TELEGRAM_CHAT_ID", "")).strip()

COMMAND_FILTERS: dict[str, tuple[str, str]] = {
    "/t": ("Today", "Today's invoice"),
    "t": ("Today", "Today's invoice"),
    "/today": ("Today", "Today's invoice"),
    "today": ("Today", "Today's invoice"),
    "/y": ("Yesterday", "Yesterday's invoice"),
    "y": ("Yesterday", "Yesterday's invoice"),
    "/yesterday": ("Yesterday", "Yesterday's invoice"),
    "yesterday": ("Yesterday", "Yesterday's invoice"),
    "/r": ("smart", "latest invoice report"),
    "r": ("smart", "latest invoice report"),
    "/report": ("smart", "latest invoice report"),
    "report": ("smart", "latest invoice report"),
    "/get": ("smart", "latest invoice report"),
}


def poll_bot():
    if not TELEGRAM_BOT_TOKEN:
        print("[!] ERROR: TELEGRAM_BOT_TOKEN is not set in .env")
        return

    print("==================================================")
    print("   Amul Report Telegram Bot Listener Active       ")
    print(f"   Waiting for commands from Chat ID: {TELEGRAM_CHAT_ID or 'ANY'}")
    print("==================================================")

    offset = None
    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

    try:
        me = requests.get(f"{api_url}/getMe", timeout=10).json()
        if not me.get("ok"):
            print(f"[!] Invalid Telegram Token: {me}")
            return
        bot_username = me.get("result", {}).get("username")
        print(f"[+] Connected to Telegram as @{bot_username}")
    except Exception as e:
        print(f"[!] Could not connect to Telegram: {e}")
        return

    while True:
        try:
            params = {"timeout": 30}
            if offset is not None:
                params["offset"] = offset

            response = requests.get(f"{api_url}/getUpdates", params=params, timeout=35)
            data = response.json()

            if not data.get("ok"):
                time.sleep(3)
                continue

            for update in data.get("result", []):
                offset = update["update_id"] + 1

                message = update.get("message", {})
                chat_id = str(message.get("chat", {}).get("id", ""))
                text = message.get("text", "").strip()

                if not text or not chat_id:
                    continue

                if TELEGRAM_CHAT_ID and chat_id != TELEGRAM_CHAT_ID:
                    print(
                        f"[!] Unauthorized request from Chat ID {chat_id}: '{text}' (Ignored)"
                    )
                    send_telegram_message(
                        TELEGRAM_BOT_TOKEN,
                        chat_id,
                        "⛔ Unauthorized. You are not allowed to use this bot.",
                    )
                    continue

                print(f"[*] Received message from {chat_id}: '{text}'")
                cmd = text.lower()

                if cmd in ["/start", "/help"]:
                    welcome = (
                        "👋 <b>Amul WD Portal Assistant</b>\n\n"
                        "• Send <b>/t</b> for today's invoice report.\n"
                        "• Send <b>/y</b> for yesterday's invoice report.\n"
                        "• Send <b>/r</b> or <b>/report</b> for the latest invoice report.\n"
                        "• Send <b>/stop</b> to shut down the bot listener remotely."
                    )
                    send_telegram_message(TELEGRAM_BOT_TOKEN, chat_id, welcome)

                elif cmd in ["/stop", "stop", "exit", "quit"]:
                    send_telegram_message(
                        TELEGRAM_BOT_TOKEN,
                        chat_id,
                        "🛑 <i>Amul Bot listener is shutting down. Goodbye!</i>",
                    )
                    print(
                        "[*] Received /stop command from authorized user. Exiting..."
                    )
                    time.sleep(1)
                    return

                elif cmd in COMMAND_FILTERS:
                    date_filter, label = COMMAND_FILTERS[cmd]
                    send_telegram_message(
                        TELEGRAM_BOT_TOKEN,
                        chat_id,
                        f"⏳ <i>Logging into Amul portal and fetching {label}...</i>",
                    )
                    try:
                        screenshot_path, date_mode, date_display = generate_report(
                            date_filter
                        )
                        caption = (
                            f"📊 <b>Amul Customer Invoice Report</b>\n"
                            f"📅 <b>Date:</b> {date_mode} ({date_display})\n"
                            f"🏢 <b>WD Code:</b> {AMUL_WD_CODE}"
                        )
                        send_telegram_photo(
                            TELEGRAM_BOT_TOKEN, chat_id, screenshot_path, caption
                        )
                    except Exception as err:
                        print(f"[!] Error handling {cmd}: {err}")
                        send_telegram_message(
                            TELEGRAM_BOT_TOKEN, chat_id, f"❌ <b>Error:</b> {err}"
                        )

        except requests.exceptions.RequestException as req_err:
            print(f"[!] Network error: {req_err}. Retrying in 5s...")
            time.sleep(5)
        except Exception as general_err:
            print(f"[!] Unexpected error in bot loop: {general_err}")
            time.sleep(3)


if __name__ == "__main__":
    poll_bot()
