"""
purchase.py — Automated Shatel traffic purchase via Playwright (real Chrome).

The purchase and the bank payment are driven through a *real* Chrome
(``channel="chrome"``) running with a **persistent profile**, so:

  * the my.shatel.ir login survives between purchases (no repeated logins), and
  * every request looks exactly like normal Chrome on Windows 11 (genuine
    User-Agent + client hints), which matters because banks may block obvious
    headless automation.

The card details are read at purchase time from an **external file** whose path
is stored in ``ShatelMon.conf`` — the card number / CVV2 / expiry are never
written into the project or the config.

Flow (recorded from the live site, Parsian gateway):

  1. Open my.shatel.ir; if the session expired, log in (username/password/ورود).
  2. "خرید ترافیک" (Buy Traffic) -> select the fixed package -> "پرداخت و تکمیل خرید".
  3. Choose the bank ("بانک پارسیان") -> "پرداخت و تکمیل خرید" -> "تایید".
  4. On the bank gateway, the app fills: card number / CVV2 / expiry month / year,
     and clicks "request dynamic password" (رمز پویا) if such a button exists.
  5. The app then HANDS OVER to the user, who enters the CAPTCHA (کد امنیتی) and
     the dynamic password (رمز دوم کارت) and clicks Pay (پرداخت) themselves.
  6. The app waits for the gateway success ("پرداخت با موفقیت انجام شد") and
     clicks "ادامه" to return to Shatel and finalize the purchase.

If anything goes wrong (or the user takes too long) the browser window is left
open so the payment can be finished/inspected manually — it is never killed
mid-payment.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger("shatelmon.purchase")

LOGIN_HOST = "account.shatel.ir"
SUCCESS_TEXT = "پرداخت با موفقیت انجام شد"

# Buttons that request the card's dynamic (one-time) password on Iranian
# gateways. Best-effort: clicked only if present.
_OTP_REQUEST_LABELS = (
    "درخواست رمز پویا",
    "دریافت رمز پویا",
    "درخواست رمز",
    "دریافت رمز",
    "رمز پویا",
)


class PurchaseError(Exception):
    """Raised when the automated purchase cannot be completed."""


def _session_guid(page) -> str:
    """The 32-hex session id that prefixes every my.shatel.ir report/service URL.

    When the traffic runs out Shatel redirects the dashboard to
    ``/{guid}/Message/FinishedTraffic`` — so the id is usually right there in the
    URL; otherwise we dig it out of the page's own links. The dashboard may still
    be mid-redirect when we're called, so wait for it to settle and tolerate
    in-flight navigations.
    """
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except Exception:  # noqa: BLE001
        pass
    for _ in range(12):
        m = re.search(r"/([a-f0-9]{32})/", page.url)
        if m:
            return m.group(1)
        try:
            m = re.search(r"/([a-f0-9]{32})/", page.content())
            if m:
                return m.group(1)
        except Exception:  # noqa: BLE001  (page is navigating right now)
            pass
        page.wait_for_timeout(500)
    raise PurchaseError("Could not determine the Shatel session id from the page")


def _click_pay_and_complete(page) -> None:
    """Click the "پرداخت و تکمیل خرید" control (it's a link on some pages, a
    button on others)."""
    for getter in (lambda: page.get_by_role("link", name="پرداخت و تکمیل خرید"),
                   lambda: page.get_by_role("button", name="پرداخت و تکمیل خرید")):
        el = getter()
        if el.count():
            el.first.click()
            return
    raise PurchaseError("Could not find the 'پرداخت و تکمیل خرید' control")


@dataclass
class PaymentInfo:
    card_number: str   # 16 digits, no separators
    cvv2: str
    exp_month: str     # 2 digits
    exp_year: str       # 2 digits

    @property
    def card_grouped(self) -> str:
        """Card number as 'XXXX-XXXX-XXXX-XXXX' (matches what the gateway accepts)."""
        d = self.card_number
        if len(d) == 16:
            return "-".join(d[i:i + 4] for i in range(0, 16, 4))
        return d


def default_profile_dir() -> str:
    """Per-user Chrome profile dir reused across purchases (persists the login)."""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "ShatelMon", "chrome-profile")


def load_payment_info(path: str) -> PaymentInfo:
    """Parse the external payment file (key=value lines):

        cardno=6219861073116864
        cvv=475
        exp-month=09
        exp-year=07
    """
    if not path or not os.path.isfile(path):
        raise PurchaseError(f"Payment info file not found: {path!r}")
    data: dict[str, str] = {}
    with open(path, "r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            data[k.strip().lower()] = v.strip()

    missing = [k for k in ("cardno", "cvv", "exp-month", "exp-year") if k not in data]
    if missing:
        raise PurchaseError(f"Payment file missing key(s): {', '.join(missing)}")

    card = re.sub(r"\D", "", data["cardno"])
    if len(card) != 16:
        raise PurchaseError("Card number in the payment file is not 16 digits")
    return PaymentInfo(
        card_number=card,
        cvv2=re.sub(r"\D", "", data["cvv"]),
        exp_month=re.sub(r"\D", "", data["exp-month"]).zfill(2),
        exp_year=re.sub(r"\D", "", data["exp-year"]).zfill(2),
    )


def run_purchase(cfg, on_status: Callable[[str], None] | None = None) -> None:
    """Perform one traffic purchase, blocking until it finishes or fails.

    ``cfg`` is the loaded :class:`config.Config`. ``on_status`` (optional) is
    called with short progress strings for tray notifications / logging.
    """
    # Imported lazily so the tray app still starts if Playwright isn't installed.
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    def status(msg: str) -> None:
        log.info("purchase: %s", msg)
        if on_status:
            try:
                on_status(msg)
            except Exception:  # noqa: BLE001
                pass

    pay = load_payment_info(cfg.payment_info_path)
    profile_dir = cfg.chrome_profile_dir or default_profile_dir()
    os.makedirs(profile_dir, exist_ok=True)
    wait_ms = max(1, int(cfg.payment_wait_minutes)) * 60 * 1000

    pw = sync_playwright().start()
    ctx = pw.chromium.launch_persistent_context(
        profile_dir,
        channel="chrome",
        headless=False,
        ignore_https_errors=True,
        locale="fa-IR",
        timezone_id="Asia/Tehran",
        no_viewport=True,
        args=["--start-maximized"],
    )
    # Only tear down cleanly on success; on error/timeout leave the window open
    # so the user can finish or inspect the payment.
    finished_ok = False
    try:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.set_default_timeout(30_000)

        status("Opening Shatel…")
        page.goto("https://my.shatel.ir/Home/Index", wait_until="domcontentloaded")

        # --- log in if the persistent session expired ---
        if LOGIN_HOST in page.url:
            status("Logging in…")
            page.get_by_role("textbox", name="username").fill(cfg.username)
            page.get_by_role("textbox", name="password").fill(cfg.password)
            page.get_by_role("button", name="ورود", exact=True).click()
            page.wait_for_url("**my.shatel.ir/**", timeout=60_000)

        # --- go straight to the purchase page ---
        # When the traffic is used up the dashboard redirects to
        # /{guid}/Message/FinishedTraffic; either way we jump directly to the
        # PurchaseTraffic page for the current session and pick the package there.
        guid = _session_guid(page)
        status("Opening the purchase page…")
        page.goto(f"https://my.shatel.ir/{guid}/Service/PurchaseTraffic",
                  wait_until="domcontentloaded")

        # --- select the (fixed) traffic package ---
        status("Selecting the traffic package…")
        clicked = False
        if cfg.package_text:
            pkg = page.get_by_text(cfg.package_text)
            try:
                pkg.first.wait_for(timeout=30_000)
                pkg.first.click()
                clicked = True
            except PWTimeout:
                pass
        if not clicked:
            page.locator(cfg.package_selector).first.click()

        # --- choose the bank and confirm ---
        status("Choosing the bank…")
        bank = page.get_by_text(cfg.bank_name)
        bank.first.wait_for(timeout=60_000)
        bank.first.click()
        _click_pay_and_complete(page)
        try:
            page.get_by_role("button", name="تایید").click(timeout=15_000)
        except PWTimeout:
            pass  # no confirmation step on this variant of the page

        # --- bank gateway: fill card details ---
        status("Filling the card details…")
        card_box = page.get_by_role("textbox", name="شماره کارت")
        card_box.wait_for(timeout=60_000)
        card_box.click()
        card_box.fill(pay.card_grouped)
        page.get_by_role("textbox", name="CVV2").fill(pay.cvv2)
        page.get_by_role("textbox", name="ماه").fill(pay.exp_month)
        page.get_by_role("textbox", name="سال").fill(pay.exp_year)

        # Best-effort: ask the gateway to send the dynamic password (OTP).
        for label in _OTP_REQUEST_LABELS:
            try:
                btn = page.get_by_role("button", name=label)
                if btn.count() and btn.first.is_visible():
                    btn.first.click(timeout=3_000)
                    status("Requested the dynamic password (OTP).")
                    break
            except (PWTimeout, Exception):  # noqa: BLE001
                continue

        # --- hand over to the user for CAPTCHA + OTP + Pay ---
        status("Enter the CAPTCHA and dynamic password in the browser, then click "
               "Pay (پرداخت). I'll finish the rest automatically.")
        page.wait_for_selector(f"text={SUCCESS_TEXT}", timeout=wait_ms)

        # --- finalize back on Shatel ---
        status("Payment confirmed — finalizing the purchase…")
        try:
            page.get_by_role("link", name="ادامه").click(timeout=30_000)
            page.wait_for_load_state("networkidle", timeout=30_000)
        except PWTimeout:
            pass  # payment already succeeded; the "continue" step is cosmetic
        status("Traffic purchase completed successfully.")
        finished_ok = True
    except Exception as e:  # noqa: BLE001
        raise PurchaseError(
            f"Automated purchase stopped: {e}. The browser window has been left "
            f"open so you can finish or check the payment manually."
        ) from e
    finally:
        if finished_ok:
            try:
                ctx.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                pw.stop()
            except Exception:  # noqa: BLE001
                pass
        # On failure we intentionally do NOT stop Playwright, leaving the browser
        # window alive for the user; the driver process exits when they close it.
