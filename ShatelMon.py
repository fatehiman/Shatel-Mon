"""
Shatel Mon (ShatelMon)
======================
A tiny Windows system-tray app that periodically checks the remaining traffic of
your Shatel internet account and the end date of the current service period, and
pops up a notification when traffic is running low or expiry is near.

Tray icon : orange "SH" on a dark-blue solid triangle (turns red while alerting).
Config    : ShatelMon.conf (created next to the program on first run).

Tray menu : Fetch remaind quota now | Fetch service expire date now | Exit

Run:  pythonw ShatelMon.py      (pythonw = no console window)
"""

from __future__ import annotations

import ctypes
import logging
import os
import queue
import sys
import threading
import time
from logging.handlers import RotatingFileHandler

import pystray
import requests

import config as cfgmod
import purchase
from icon import make_icon
from shatel_client import (ShatelClient, ShatelError, LoginError, Package,
                           ServiceExpiry, total_remaining_mb)

APP_NAME = "Shatel Mon"
APP_ID = "ShatelMon"
MUTEX_NAME = "Global\\ShatelMon_SingleInstance_Mutex_v1"
ERROR_ALREADY_EXISTS = 183

# English labels for the known Persian package types (all alerts must be English).
_LABELS = {
    "پایه": "Base service",
    "روزشمار": "Daily counter package",
    "هدیه": "Gift package",
    "مصرف مازاد": "Excess usage",
    "نامحدود": "Unlimited package",
    "شبانه": "Nightly package",
    "ترافیک": "Traffic package",
}


def base_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


BASE = base_dir()
CONFIG_PATH = os.path.join(BASE, cfgmod.CONFIG_NAME)
LOG_PATH = os.path.join(BASE, "ShatelMon.log")

BLINK_INTERVAL = 0.45   # seconds between "SH" on/off frames while processing

log = logging.getLogger("shatelmon")


def setup_logging() -> None:
    handler = RotatingFileHandler(LOG_PATH, maxBytes=512 * 1024, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


# --------------------------------------------------------------------------- #
#  Windows helpers (single instance + message box)
# --------------------------------------------------------------------------- #

def acquire_single_instance():
    """Create a named mutex. Returns the handle if we are the only instance,
    or None if another instance already owns it."""
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
        if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            return None
        return handle
    except Exception:  # noqa: BLE001  (non-Windows / no access -> don't block)
        return True


def message_box(text: str, title: str = APP_NAME, error: bool = False) -> None:
    MB_OK = 0x0
    MB_ICONINFORMATION = 0x40
    MB_ICONERROR = 0x10
    MB_SETFOREGROUND = 0x10000
    flags = MB_OK | (MB_ICONERROR if error else MB_ICONINFORMATION) | MB_SETFOREGROUND
    try:
        ctypes.windll.user32.MessageBoxW(0, text, title, flags)
    except Exception:  # noqa: BLE001
        print(f"{title}: {text}")


# --------------------------------------------------------------------------- #
#  Formatting helpers
# --------------------------------------------------------------------------- #

def fmt_traffic(mb: float | None) -> str:
    if mb is None:
        return "n/a"
    if abs(mb) >= 1024:
        return f"{mb / 1024:.2f} GB"
    return f"{mb:.0f} MB"


def fmt_days(days: float | None) -> str:
    if days is None:
        return "?"
    if days >= 1:
        d = int(days)
        h = int((days - d) * 24)
        return f"{d}d {h}h" if h else f"{d}d"
    return f"{int(days * 24)}h"


def pkg_label(p: Package) -> str:
    return _LABELS.get(p.name.strip(), "Traffic package")


# --------------------------------------------------------------------------- #
#  Notifications
# --------------------------------------------------------------------------- #

def notify(title: str, message: str, icon=None) -> None:
    """Show a notification using the tray icon's balloon (Shell_NotifyIcon).

    This is the reliable path for a tray app: unlike Windows toasts via an
    unregistered AppUserModelID (which Windows silently drops), the tray balloon
    always shows as long as notifications are enabled."""
    try:
        if icon is not None:
            icon.notify(message, title)
            log.info("Notified: %s — %s", title, message)
            return
    except Exception as e:  # noqa: BLE001
        log.warning("tray balloon failed: %s", e)
    log.info("Notification (not shown): %s — %s", title, message)


# --------------------------------------------------------------------------- #
#  Alert evaluation (English only)
# --------------------------------------------------------------------------- #

def evaluate_quota_alerts(packages: list[Package], cfg: cfgmod.Config):
    """Return (alerts, total_mb). alerts = list of (key, title, message)."""
    alerts = []
    total = total_remaining_mb(packages)

    if total < cfg.low_traffic_threshold_mb:
        alerts.append((
            "low_traffic",
            f"{APP_NAME}: traffic low",
            f"Only {fmt_traffic(total)} of traffic remaining across all packages.",
        ))

    for p in packages:
        if p.is_excess or p.remaining_days is None:
            continue
        if p.remaining_mb is None or p.remaining_mb <= 0:
            continue  # nothing left to lose (the base/service period is covered separately)
        if 0 < p.remaining_days <= cfg.expire_warning_days:
            alerts.append((
                f"expire:{p.name}",
                f"{APP_NAME}: package expiring",
                f"{pkg_label(p)} expires in {fmt_days(p.remaining_days)} "
                f"({fmt_traffic(p.remaining_mb)} left).",
            ))
    return alerts, total


def evaluate_service_alert(exp: ServiceExpiry, cfg: cfgmod.Config):
    """Return (key, title, message) if the service period is near/at/past expiry, else None."""
    if exp.remaining_days > cfg.service_expire_warning_days:
        return None
    g = exp.gregorian.isoformat()
    if exp.remaining_days > 0:
        when = f"in {exp.remaining_days} day(s)"
        verb = "ends"
    elif exp.remaining_days == 0:
        when = "today"
        verb = "ends"
    else:
        when = f"{-exp.remaining_days} day(s) ago"
        verb = "ended"
    return (
        "service_expire",
        f"{APP_NAME}: service expiring",
        f"Your Shatel service period {verb} {when} "
        f"(Gregorian {g}, Jalali {exp.jalali}).",
    )


# --------------------------------------------------------------------------- #
#  The app
# --------------------------------------------------------------------------- #

class ShatelMonApp:
    def __init__(self, cfg: cfgmod.Config):
        self.cfg = cfg

        self.client: ShatelClient | None = None
        self._quota_status = "checking quota…"
        self._service_status = "checking expiry…"
        self._quota_alert_keys: set[str] = set()
        self._service_alert_active = False
        self._in_alert = False

        self._stop = threading.Event()
        self._cmd_queue: queue.Queue[str] = queue.Queue()
        self._last_notified: dict[str, float] = {}
        self._busy = threading.Event()          # set while a fetch is in progress
        self._last_total_mb: float | None = None  # last known combined remaining
        self._purchase_in_progress = threading.Event()  # set while buying traffic
        self._last_purchase_ts: float | None = None      # monotonic time of last attempt

        self.icon = pystray.Icon(APP_ID, make_icon(64), APP_NAME, menu=self._build_menu())

    # -- menu (exactly three items) -------------------------------------------

    def _build_menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem("Fetch remaind quota now", self.on_fetch_quota, default=True),
            pystray.MenuItem("Fetch service expire date now", self.on_fetch_expire),
            pystray.MenuItem("Buy traffic now", self.on_buy_traffic),
            pystray.MenuItem("Exit", self.on_exit),
        )

    def on_fetch_quota(self, icon, item):
        self._cmd_queue.put("quota")        # manual -> announce result

    def on_fetch_expire(self, icon, item):
        self._cmd_queue.put("expire")       # manual -> announce result

    def on_buy_traffic(self, icon, item):
        self._cmd_queue.put("buy")          # manual -> start a purchase

    def on_exit(self, icon, item):
        log.info("Exit requested")
        self._stop.set()
        self._busy.clear()
        self._cmd_queue.put("quit")
        icon.stop()

    # -- icon / tooltip --------------------------------------------------------

    def _set_icon_image(self, alert: bool, show_circle: bool = True):
        try:
            self.icon.icon = make_icon(64, alert=alert, show_circle=show_circle)
        except Exception:  # noqa: BLE001  (icon loop may not be ready yet)
            pass

    def _refresh_icon(self):
        """Set the steady icon (normal/red) and update the tooltip. Skipped while
        the processing animation owns the icon."""
        self._in_alert = bool(self._quota_alert_keys) or self._service_alert_active
        if not self._busy.is_set():
            self._set_icon_image(self._in_alert, show_circle=True)
        title = f"{APP_NAME} — {self._quota_status} · {self._service_status}"
        try:
            self.icon.title = title[:127]
        except Exception:  # noqa: BLE001
            pass

    def _animator(self):
        """Show/hide the orange circle while a fetch is running, to convey 'processing'."""
        circle_on = False
        while not self._stop.is_set():
            if self._busy.is_set():
                circle_on = not circle_on
                self._set_icon_image(self._in_alert, show_circle=circle_on)
                self._stop.wait(BLINK_INTERVAL)
            else:
                # idle: wait until the next fetch starts (or we stop)
                while not self._busy.is_set() and not self._stop.is_set():
                    if self._stop.wait(0.2):
                        break

    # -- notifications with de-dup --------------------------------------------

    def maybe_notify(self, key: str, title: str, message: str):
        now = time.monotonic()
        last = self._last_notified.get(key)
        repeat = self.cfg.notify_repeat_hours * 3600
        if last is None or (now - last) >= repeat:
            notify(title, message, self.icon)
            self._last_notified[key] = now

    def _notify_error(self, key: str, title: str, message: str, announce: bool):
        """Notify about a fetch error. Manual fetches always notify; periodic
        fetches notify but are de-duplicated so a persistent failure (e.g. the
        network is down) does not spam the user."""
        self.client = None
        log.error("%s: %s", title, message)
        if announce:
            notify(title, message, self.icon)
        else:
            self.maybe_notify(key, title, message)

    @staticmethod
    def _error_text(e: Exception) -> str:
        if isinstance(e, requests.exceptions.RequestException):
            return ("Could not reach Shatel — please check your internet "
                    "connection. Will retry automatically.")
        return f"Could not read the data from Shatel: {e}"

    def _client_or_new(self) -> ShatelClient:
        if self.client is None:
            self.client = ShatelClient(self.cfg.username, self.cfg.password,
                                       report=self.cfg.report)
        return self.client

    def _interval_minutes(self) -> int:
        """Check frequently when traffic is low or an alert is active; relax to
        the longer interval when plenty of traffic remains."""
        if self._quota_alert_keys or self._service_alert_active:
            return self.cfg.check_interval_minutes
        if (self._last_total_mb is not None and
                self._last_total_mb > self.cfg.relaxed_traffic_threshold_mb):
            return self.cfg.relaxed_interval_minutes
        return self.cfg.check_interval_minutes

    # -- the two checks --------------------------------------------------------

    def check_quota(self, announce: bool = False):
        self._busy.set()
        try:
            packages = self._client_or_new().get_packages()
        except LoginError as e:
            self._quota_status = "login failed"
            self._notify_error("login_error", f"{APP_NAME}: login failed",
                               "Login to Shatel failed — please check your username and "
                               "password in ShatelMon.conf.", announce)
            return
        except Exception as e:  # noqa: BLE001
            self._quota_status = "quota check failed"
            self._notify_error("quota_error", f"{APP_NAME}: quota check failed",
                               self._error_text(e), announce)
            return
        finally:
            self._busy.clear()
            self._refresh_icon()

        # Also read the PPPOE connection status (best-effort; never fails the check).
        conn_text = ""
        try:
            connected = self._client_or_new().get_connection_status()
            if connected is True:
                conn_text = "Connection (PPPOE): Connected"
            elif connected is False:
                conn_text = "Connection (PPPOE): Disconnected"
        except Exception as e:  # noqa: BLE001
            log.warning("Could not read connection status: %s", e)

        alerts, total = evaluate_quota_alerts(packages, self.cfg)
        self._last_total_mb = total
        self._quota_status = f"{fmt_traffic(total)} left"
        new_keys = {k for k, _, _ in alerts}
        for k in self._quota_alert_keys - new_keys:   # cleared -> allow future re-alert
            self._last_notified.pop(k, None)
        self._quota_alert_keys = new_keys
        self._refresh_icon()
        log.info("Quota: total=%.1f MB, alerts=%d", total, len(alerts))

        # Auto-buy traffic when it runs low (payment still needs the user's OTP).
        if (self.cfg.auto_purchase_enabled
                and total < self.cfg.auto_purchase_threshold_mb
                and not self._purchase_in_progress.is_set()
                and self._purchase_cooldown_ok()):
            log.info("Traffic %.0f MB below purchase threshold %.0f MB -> auto-purchase",
                     total, self.cfg.auto_purchase_threshold_mb)
            self._start_purchase(manual=False)

        if announce:
            npkg = sum(1 for p in packages if not p.is_excess)
            extra = f"  ⚠ {alerts[0][2]}" if alerts else ""
            conn_line = f"\n{conn_text}" if conn_text else ""
            notify(f"{APP_NAME}: remaining traffic",
                   f"{fmt_traffic(total)} remaining across {npkg} package(s).{extra}{conn_line}",
                   self.icon)
        for key, title, message in alerts:
            self.maybe_notify(key, title, message)

    def check_service_expire(self, announce: bool = False):
        self._busy.set()
        try:
            exp = self._client_or_new().get_service_expiry()
        except LoginError as e:
            self._service_status = "login failed"
            self._notify_error("login_error", f"{APP_NAME}: login failed",
                               "Login to Shatel failed — please check your username and "
                               "password in ShatelMon.conf.", announce)
            return
        except Exception as e:  # noqa: BLE001
            self._service_status = "expiry check failed"
            self._notify_error("expire_error", f"{APP_NAME}: expiry check failed",
                               self._error_text(e), announce)
            return
        finally:
            self._busy.clear()
            self._refresh_icon()

        alert = evaluate_service_alert(exp, self.cfg)
        self._service_status = f"service ends {exp.gregorian.isoformat()} ({exp.remaining_days}d)"
        self._service_alert_active = alert is not None
        self._refresh_icon()
        log.info("Service expiry: %s (Gregorian %s), %d days left",
                 exp.jalali, exp.gregorian.isoformat(), exp.remaining_days)

        if announce:
            notify(f"{APP_NAME}: service expiry",
                   f"Service period ends on {exp.gregorian.isoformat()} "
                   f"(Jalali {exp.jalali}) — {exp.remaining_days} day(s) left.",
                   self.icon)
        if alert:
            self.maybe_notify(*alert)
        elif not announce:
            self._last_notified.pop("service_expire", None)

    # -- automated purchase ----------------------------------------------------

    def _purchase_cooldown_ok(self) -> bool:
        if self._last_purchase_ts is None:
            return True
        elapsed = time.monotonic() - self._last_purchase_ts
        return elapsed >= self.cfg.purchase_cooldown_hours * 3600

    def _start_purchase(self, manual: bool = False):
        """Kick off a purchase in its own thread so checks keep running."""
        if self._purchase_in_progress.is_set():
            if manual:
                notify(f"{APP_NAME}: purchase", "A purchase is already in progress.", self.icon)
            return
        self._purchase_in_progress.set()
        self._last_purchase_ts = time.monotonic()   # cooldown counts from the attempt
        threading.Thread(target=self._run_purchase, args=(manual,),
                         name="purchase", daemon=True).start()

    def _run_purchase(self, manual: bool):
        try:
            notify(f"{APP_NAME}: buying traffic",
                   "Opening Chrome to buy more traffic — you'll enter the CAPTCHA/OTP "
                   "and click Pay when prompted.", self.icon)
            purchase.run_purchase(
                self.cfg,
                on_status=lambda m: notify(f"{APP_NAME}: buying traffic", m, self.icon),
            )
            self._last_purchase_ts = time.monotonic()
            notify(f"{APP_NAME}: purchase complete",
                   "Traffic purchase finished. Refreshing your quota…", self.icon)
            self._cmd_queue.put("quota_bg")   # refresh the tooltip without a popup
        except purchase.PurchaseError as e:
            notify(f"{APP_NAME}: purchase failed", str(e), self.icon)
        except Exception as e:  # noqa: BLE001
            log.exception("Purchase error: %s", e)
            notify(f"{APP_NAME}: purchase failed",
                   f"Could not complete the automatic purchase: {e}", self.icon)
        finally:
            self._purchase_in_progress.clear()

    # -- worker loop -----------------------------------------------------------

    def _worker(self):
        log.info("Checker thread started")
        # By default the first check happens only after one full interval: the app
        # is usually launched at boot, when the machine often has no internet yet,
        # and an immediate check would just produce a "can't reach Shatel" popup.
        # Set check_on_startup = true to check right away instead.
        pending_summary = self.cfg.notify_summary_on_startup
        if self.cfg.check_on_startup:
            self.check_quota()
            self.check_service_expire()
            if pending_summary:
                notify(APP_NAME, f"{self._quota_status}; {self._service_status}.", self.icon)
                pending_summary = False
        else:
            log.info("Skipping the startup check; first check in one interval")

        while not self._stop.is_set():
            wait_min = self._interval_minutes()
            log.info("Next check in %d min", wait_min)
            try:
                cmd = self._cmd_queue.get(timeout=wait_min * 60)
            except queue.Empty:
                cmd = "all"
            if cmd == "quit":
                break
            if cmd == "buy":
                self._start_purchase(manual=True)
                continue
            try:
                if cmd in ("quota", "quota_bg", "all"):
                    self.check_quota(announce=(cmd == "quota"))
                if cmd in ("expire", "all"):
                    self.check_service_expire(announce=(cmd == "expire"))
            except Exception as e:  # noqa: BLE001
                log.exception("Unexpected error during check: %s", e)
            # The startup summary is deferred to the first completed check.
            if pending_summary and cmd == "all":
                pending_summary = False
                notify(APP_NAME, f"{self._quota_status}; {self._service_status}.", self.icon)

    def run(self):
        # Start the background threads directly rather than via pystray's `setup`
        # callback: the callback does not fire reliably in a frozen (PyInstaller
        # --windowed) build, whereas plain threads started here always run.
        threading.Thread(target=self._animator, name="animator", daemon=True).start()
        threading.Thread(target=self._worker, name="checker", daemon=True).start()
        self.icon.run()


# --------------------------------------------------------------------------- #
#  Entry point
# --------------------------------------------------------------------------- #

def main():
    setup_logging()

    mutex = acquire_single_instance()
    if mutex is None:
        log.info("Another instance is already running; exiting")
        message_box("Shatel Mon is already running.\n\nLook for the orange \"SH\" icon "
                    "in the system tray (near the clock).", APP_NAME)
        return

    log.info("=== %s starting (base=%s) ===", APP_NAME, BASE)

    # First run: create the config with placeholders, tell the user, and exit.
    if not os.path.exists(CONFIG_PATH):
        cfgmod.create_default(CONFIG_PATH)
        log.info("Created default config at %s", CONFIG_PATH)
        message_box(
            "Welcome to Shatel Mon!\n\n"
            f"A configuration file was created:\n{CONFIG_PATH}\n\n"
            "Please open it, set your Shatel username and password, then run "
            "Shatel Mon again.",
            APP_NAME)
        return

    try:
        cfg = cfgmod.load(CONFIG_PATH)
    except Exception as e:  # noqa: BLE001
        log.exception("Could not read config: %s", e)
        message_box(f"Your configuration file could not be read:\n\n{CONFIG_PATH}\n\n"
                    f"Error: {e}\n\nPlease fix or delete it and run Shatel Mon again.",
                    APP_NAME, error=True)
        return
    if not cfg.credentials_present:
        log.info("Credentials not set; exiting")
        message_box(
            "Your Shatel username and password are not set yet.\n\n"
            f"Please edit the configuration file and fill them in:\n{CONFIG_PATH}\n\n"
            "Then run Shatel Mon again.",
            APP_NAME)
        return

    try:
        ShatelMonApp(cfg).run()
    except Exception as e:  # noqa: BLE001
        log.exception("Fatal: %s", e)
        message_box(f"Shatel Mon stopped unexpectedly:\n\n{e}", APP_NAME, error=True)
        raise


if __name__ == "__main__":
    main()
