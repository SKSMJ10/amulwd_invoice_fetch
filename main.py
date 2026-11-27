import imaplib
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

load_dotenv(override=True)

AMUL_WD_CODE = os.getenv("AMUL_WD_CODE")
GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD") or os.getenv("GMAIL_PASSWORD")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
HEADLESS_MODE = os.getenv("HEADLESS_MODE", "true").lower() == "true"
DATE_FILTER_ENV = os.getenv("DATE_FILTER", "smart").strip().lower()
PLAYWRIGHT_CHANNEL = os.getenv("PLAYWRIGHT_CHANNEL", "").strip() or None

AMUL_SENDER = "background_amulwd@amul.coop"
SPAM_MAILBOX = '"[Gmail]/Spam"'
BLOCKED_RESOURCE_TYPES = {"image", "font", "media"}


def imap_since_date() -> str:
    # Yesterday UTC avoids IMAP SINCE timezone boundary misses around midnight
    return (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%d-%b-%Y")


def open_imap_connection(user: str, app_pass: str) -> imaplib.IMAP4_SSL:
    mail = imaplib.IMAP4_SSL("imap.gmail.com", timeout=30)
    try:
        mail.login(user, app_pass)
    except Exception:
        close_imap_connection(mail)
        raise
    return mail


def close_imap_connection(mail: imaplib.IMAP4_SSL | None):
    if mail is None:
        return
    try:
        mail.logout()
    except Exception:
        try:
            mail.shutdown()
        except Exception:
            pass


def select_inbox(mail: imaplib.IMAP4_SSL):
    status, _ = mail.select("INBOX")
    if status != "OK":
        raise imaplib.IMAP4.error(f"Failed to select INBOX: {status}")


def get_latest_amul_uid(
    mail: imaplib.IMAP4_SSL, mailbox: str, since_date: str, required: bool = False
) -> int | None:
    status, _ = mail.select(mailbox)
    if status != "OK":
        if required:
            raise imaplib.IMAP4.error(f"Failed to select {mailbox}: {status}")
        return None
    status, msgs = mail.uid("search", None, "SINCE", since_date, "FROM", AMUL_SENDER)
    if status != "OK" or not msgs or not msgs[0]:
        return None
    uids = [int(uid) for uid in msgs[0].split()]
    return max(uids) if uids else None


def find_new_otp(
    mail: imaplib.IMAP4_SSL, since_date: str, pre_uid: int | None
) -> tuple[str | None, int | None]:
    status, msgs = mail.uid("search", None, "SINCE", since_date, "FROM", AMUL_SENDER)
    if status != "OK" or not msgs or not msgs[0]:
        return None, pre_uid

    new_uids = sorted(
        (int(u) for u in msgs[0].split() if pre_uid is None or int(u) > pre_uid),
        reverse=True,
    )
    if not new_uids:
        return None, pre_uid

    all_fetched = True
    for uid in new_uids:
        status, data = mail.uid("fetch", str(uid), "(BODY[TEXT])")
        if status != "OK" or not data:
            all_fetched = False
            continue
        for part in data:
            if (
                isinstance(part, tuple)
                and len(part) >= 2
                and isinstance(part[1], (bytes, bytearray))
            ):
                body = part[1].decode(errors="ignore")
                match = re.search(r"login is\s*(\d{4})", body)
                if match:
                    return match.group(1), uid

    return None, (new_uids[0] if all_fetched else pre_uid)


def fetch_gmail_otp(
    mail: imaplib.IMAP4_SSL,
    user: str,
    app_pass: str,
    since_date: str,
    pre_inbox_uid: int | None = None,
    pre_spam_uid: int | None = None,
    timeout: int = 240,
    page=None,
) -> str:
    print(f"[*] Listening for new OTP email (timeout: {timeout}s)...")
    initial_mail = mail
    active_mail: imaplib.IMAP4_SSL | None = mail

    start = time.monotonic()
    notified_delay = False
    resend_attempted = False
    last_inbox_reselect = start
    last_spam_check = 0.0

    try:
        while time.monotonic() - start < timeout:
            now = time.monotonic()
            elapsed = now - start

            try:
                if active_mail is None:
                    active_mail = open_imap_connection(user, app_pass)
                    select_inbox(active_mail)
                    last_inbox_reselect = time.monotonic()
                elif now - last_inbox_reselect >= 15:
                    select_inbox(active_mail)
                    last_inbox_reselect = now
                else:
                    active_mail.noop()

                otp, pre_inbox_uid = find_new_otp(
                    active_mail, since_date, pre_inbox_uid
                )
                if otp:
                    print(f"[+] Retrieved OTP: {otp} (after {int(elapsed)}s)")
                    return otp

                if elapsed >= 45 and (
                    last_spam_check == 0.0 or now - last_spam_check >= 20
                ):
                    last_spam_check = now
                    status, _ = active_mail.select(SPAM_MAILBOX)
                    if status == "OK":
                        otp, pre_spam_uid = find_new_otp(
                            active_mail, since_date, pre_spam_uid
                        )
                        if otp:
                            print(
                                f"[+] Retrieved OTP from SPAM folder: {otp} (after {int(elapsed)}s)"
                            )
                            return otp
                    select_inbox(active_mail)
                    last_inbox_reselect = time.monotonic()

            except (imaplib.IMAP4.error, OSError) as e:
                print(f"[!] IMAP connection error ({e}), reconnecting...")
                close_imap_connection(active_mail)
                active_mail = None

            if elapsed > 40 and not notified_delay:
                notified_delay = True
                print("[*] Amul mail queue delayed. Notifying Telegram...")
                if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
                    send_telegram_message(
                        TELEGRAM_BOT_TOKEN,
                        TELEGRAM_CHAT_ID,
                        "⏳ <i>Amul's mail server is slow tonight (~1-2m queue delay). Still waiting for OTP...</i>",
                    )

            if elapsed > 125 and not resend_attempted and page:
                resend_attempted = True
                try:
                    resend_btn = page.locator("#resend_otp")
                    if resend_btn.is_visible() and resend_btn.is_enabled():
                        print(
                            "[*] 120s elapsed. Clicking 'Resend OTP' to poke queue..."
                        )
                        resend_btn.click()
                        if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
                            send_telegram_message(
                                TELEGRAM_BOT_TOKEN,
                                TELEGRAM_CHAT_ID,
                                "🔄 <i>Clicked 'Resend OTP' on Amul portal to refresh dispatch queue...</i>",
                            )
                except Exception as e:
                    print(f"[*] Resend button check: {e}")

            time.sleep(1)

        raise TimeoutError(f"OTP email did not arrive within {timeout} seconds.")
    finally:
        if active_mail is not initial_mail:
            close_imap_connection(active_mail)


def send_telegram_message(token: str, chat_id: str, text: str):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        requests.post(
            url,
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=15,
        )
    except Exception as e:
        print(f"[!] Failed to send message to Telegram: {e}")


def send_telegram_photo(token: str, chat_id: str, photo_path: str, caption: str):
    if not os.path.exists(photo_path) or os.path.getsize(photo_path) == 0:
        raise FileNotFoundError(
            f"Screenshot file does not exist or is empty: {photo_path}"
        )

    url_photo = f"https://api.telegram.org/bot{token}/sendPhoto"
    with open(photo_path, "rb") as f:
        res = requests.post(
            url_photo,
            data={"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"},
            files={"photo": f},
            timeout=45,
        )

    data = res.json()
    if data.get("ok"):
        print("[+] Delivered photo to Telegram!")
        return data

    # Telegram rejects very tall/wide table images as photos, so fall back to uncompressed document
    print(
        f"[*] sendPhoto notice ({data.get('description')}), attempting sendDocument fallback..."
    )
    url_doc = f"https://api.telegram.org/bot{token}/sendDocument"
    with open(photo_path, "rb") as f:
        res = requests.post(
            url_doc,
            data={"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"},
            files={"document": f},
            timeout=45,
        )

    data = res.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram delivery failed: {data.get('description')}")

    print("[+] Delivered document to Telegram!")
    return data


def resolve_date_filter(mode_str: str | None = None) -> tuple[str, str]:
    ist_now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    req = (mode_str or DATE_FILTER_ENV or "smart").strip().lower()

    if req in ["today", "t"]:
        return "Today", ist_now.strftime("%d-%b-%Y")
    elif req in ["yesterday", "y"]:
        y_date = ist_now - timedelta(days=1)
        return "Yesterday", y_date.strftime("%d-%b-%Y")
    else:
        # Between 00:00 and 06:00 IST, default to Yesterday's closing invoices
        if 0 <= ist_now.hour < 6:
            y_date = ist_now - timedelta(days=1)
            return "Yesterday", f"{y_date.strftime('%d-%b-%Y')} (Auto: Post-Midnight)"
        return "Today", ist_now.strftime("%d-%b-%Y")


def select_dropdown_filter(page, target_label: str):
    dropdown = page.locator("select").first
    dropdown.select_option(label=target_label.lower())
    print(f"[+] Selected dropdown filter: {target_label}")


def launch_browser(playwright):
    launch_args = [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
    ]
    if PLAYWRIGHT_CHANNEL:
        try:
            print(
                f"[*] Starting browser (Headless: {HEADLESS_MODE}, Channel: {PLAYWRIGHT_CHANNEL})..."
            )
            return playwright.chromium.launch(
                headless=HEADLESS_MODE,
                channel=PLAYWRIGHT_CHANNEL,
                args=launch_args,
            )
        except Exception as e:
            print(
                f"[!] Failed to launch channel '{PLAYWRIGHT_CHANNEL}' ({e}), falling back to bundled Chromium..."
            )

    print(f"[*] Starting browser (Headless: {HEADLESS_MODE})...")
    return playwright.chromium.launch(headless=HEADLESS_MODE, args=launch_args)


def generate_report(date_filter: str | None = None) -> tuple[str, str, str]:
    missing = [
        name
        for name, val in [
            ("AMUL_WD_CODE", AMUL_WD_CODE),
            ("GMAIL_ADDRESS", GMAIL_ADDRESS),
            ("GMAIL_APP_PASSWORD", GMAIL_APP_PASSWORD),
        ]
        if not val
    ]
    if missing:
        raise ValueError(
            f"Missing required environment variable(s): {', '.join(missing)}"
        )

    date_mode, date_display = resolve_date_filter(date_filter)
    print(f"=== Amul Daily Report [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ===")
    print(f"[*] Target Date Filter: {date_mode} ({date_display})")

    with sync_playwright() as p:
        browser = launch_browser(p)
        try:
            page = browser.new_page(viewport={"width": 1920, "height": 1080})
            page.route(
                "**/*",
                lambda route: route.abort()
                if route.request.resource_type in BLOCKED_RESOURCE_TYPES
                else route.continue_(),
            )

            page.goto("https://wd.amul.in/", timeout=60000, wait_until="domcontentloaded")
            page.wait_for_selector("#kt_select2_1", state="visible", timeout=15000)
            page.select_option("#kt_select2_1", value="wd")
            page.wait_for_selector("#email", state="visible", timeout=15000)
            page.fill("#email", AMUL_WD_CODE)

            mail = open_imap_connection(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            try:
                since_date = imap_since_date()
                pre_spam_uid = get_latest_amul_uid(mail, SPAM_MAILBOX, since_date)
                pre_inbox_uid = get_latest_amul_uid(
                    mail, "INBOX", since_date, required=True
                )

                print("[*] Requesting OTP...")
                page.click("#send_otp")

                page.wait_for_selector("#email_otp", state="visible", timeout=15000)
                otp = fetch_gmail_otp(
                    mail,
                    GMAIL_ADDRESS,
                    GMAIL_APP_PASSWORD,
                    since_date=since_date,
                    pre_inbox_uid=pre_inbox_uid,
                    pre_spam_uid=pre_spam_uid,
                    timeout=240,
                    page=page,
                )
            finally:
                close_imap_connection(mail)

            page.fill("#email_otp", otp)
            page.click("#verify_otp")

            # Wait only for commit so we skip loading dashboard assets before jumping to invoices
            page.wait_for_url("**/dashboard**", timeout=30000, wait_until="commit")
            print("[+] Logged in successfully!")

            print("[*] Navigating directly to Customer Invoice...")
            page.goto(
                "https://wd.amul.in/customerinvoice",
                timeout=60000,
                wait_until="domcontentloaded",
            )
            page.wait_for_selector("select", timeout=15000, state="visible")

            select_dropdown_filter(page, date_mode)

            print("[*] Clicking 'Get Data'...")
            page.locator("button:has-text('Get Data')").first.click()

            print("[*] Waiting for table #dtl_tbl to render...")
            page.wait_for_selector("#dtl_tbl", state="visible", timeout=30000)
            try:
                page.wait_for_selector("#dtl_tbl tbody tr", state="visible", timeout=5000)
            except PlaywrightTimeoutError:
                pass
            page.wait_for_timeout(300)

            # Remove sticky headers so they do not cover table headers in the screenshot
            page.evaluate("""() => {
                const header = document.getElementById('kt_header');
                if (header && header.tagName !== 'BODY') header.remove();

                const subheader = document.getElementById('kt_subheader');
                if (subheader && subheader.tagName !== 'BODY') subheader.remove();
            }""")

            table = page.locator("#dtl_tbl").first
            screenshot_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "daily_invoice_report.png"
            )
            table.screenshot(path=screenshot_path)
            print(f"[+] SUCCESS: Saved table screenshot to {screenshot_path}")

            return screenshot_path, date_mode, date_display
        finally:
            browser.close()


def run(date_filter: str | None = None):
    try:
        screenshot_path, date_mode, date_display = generate_report(date_filter)
        if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
            caption = (
                f"📊 <b>Amul Customer Invoice Report</b>\n"
                f"📅 <b>Date:</b> {date_mode} ({date_display})\n"
                f"🏢 <b>WD Code:</b> {AMUL_WD_CODE}"
            )
            send_telegram_photo(
                TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, screenshot_path, caption
            )
        print("[+] All done!")
    except Exception as e:
        print(f"[!] Scraper error: {e}")
        if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
            send_telegram_message(
                TELEGRAM_BOT_TOKEN,
                TELEGRAM_CHAT_ID,
                f"❌ <b>Amul Scraper Failed:</b>\n<code>{e}</code>",
            )
        raise


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else None
    run(target)
