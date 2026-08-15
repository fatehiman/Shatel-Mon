"""Loads / creates ShatelMon.conf (INI format)."""

from __future__ import annotations

import configparser
import os
from dataclasses import dataclass

CONFIG_NAME = "ShatelMon.conf"
SAMPLE_NAME = "ShatelMon.sample.conf"

# Placeholders written on first run; treated as "not configured yet".
USERNAME_PLACEHOLDER = "YOUR_USERNAME"
PASSWORD_PLACEHOLDER = "YOUR_PASSWORD"
_PLACEHOLDERS = {USERNAME_PLACEHOLDER.lower(), PASSWORD_PLACEHOLDER.lower(), ""}

# The [purchase] section — shared by the first-run template and the migration
# that appends it to configs created before the auto-purchase feature existed.
_PURCHASE_SECTION = """\
[purchase]
; Automatically buy more traffic when the combined remaining traffic drops
; below auto_purchase_threshold_gb. The card details are read from the
; external file at payment_info_path (never stored here); the app fills the
; card number / CVV2 / expiry and requests the dynamic password, then WAITS for
; you to enter the CAPTCHA + dynamic password and click Pay in the browser.
enabled = true

; Buy when the combined remaining traffic drops below this many GB.
auto_purchase_threshold_gb = 2

; Full path to the file holding the card details (key=value lines:
; cardno=..., cvv=..., exp-month=.., exp-year=..). Keep this file OUTSIDE the
; project; only this path is stored here.
payment_info_path = E:\\appServices\\paymentInfo\\am-saman-expence.txt

; Chrome profile directory reused across purchases (keeps you logged in and makes
; the browser look like normal Chrome). Leave blank for the default under
; %LOCALAPPDATA%\\ShatelMon\\chrome-profile.
chrome_profile_dir =

; The bank to pay through, as shown on Shatel's payment page.
bank_name = بانک پارسیان

; The traffic package to buy, matched by its visible text on the purchase page.
package_text = ۱۰۰ گیگابایت ترافیک ۱۵ روزه

; Fallback CSS selector for the package, used only if package_text isn't found.
package_selector = div:nth-child(2) > .row > .radio-box-new > .col-9-large > .desc

; How long (minutes) to wait for you to enter the CAPTCHA/OTP and click Pay
; before giving up (the browser is left open either way).
payment_wait_minutes = 10

; Don't start another automatic purchase within this many hours of the last one.
purchase_cooldown_hours = 6
"""

_TEMPLATE = """\
; ============================================================
;  Shatel Mon (ShatelMon) configuration
; ============================================================

[credentials]
; Your my.shatel.ir login (phone number) and password.
username = {username}
password = {password}

[settings]
; How often to check (minutes) when traffic is LOW or an alert is active.
check_interval_minutes = 30

; When plenty of traffic remains, check less often to save effort:
; if the combined remaining traffic is above relaxed_traffic_threshold_mb
; (and no alert is active), the app waits relaxed_interval_minutes between
; checks instead of check_interval_minutes. 6 hours = 360 minutes, 10 GB = 10240 MB.
relaxed_interval_minutes = 360
relaxed_traffic_threshold_mb = 10240

; Alert when the COMBINED remaining traffic of all packages drops
; below this many megabytes. 2 GB = 2048 MB.
low_traffic_threshold_mb = 2048

; Alert when a traffic package that still has data left is within this
; many days of expiring.
expire_warning_days = 3

; Alert when the current SERVICE period (پایان دوره جاری) is within this
; many days of ending.
service_expire_warning_days = 7

; Don't repeat the same alert more often than this many hours.
notify_repeat_hours = 6

; Which traffic report to read (leave as-is).
report = CurrentTrafficPackages

; Check immediately when the app starts. Off by default: the app usually starts
; at boot, when the machine may still be without internet for a few minutes, so
; the first check waits one full interval instead.
check_on_startup = false

; Show a normal (non-alert) summary notification once at startup.
; (Shown after the first completed check when check_on_startup = false.)
notify_summary_on_startup = false

""" + _PURCHASE_SECTION


@dataclass
class Config:
    username: str = ""
    password: str = ""
    check_interval_minutes: int = 30
    relaxed_interval_minutes: int = 360
    relaxed_traffic_threshold_mb: float = 10240.0
    low_traffic_threshold_mb: float = 2048.0
    expire_warning_days: float = 3.0
    service_expire_warning_days: float = 7.0
    notify_repeat_hours: float = 6.0
    report: str = "CurrentTrafficPackages"
    notify_summary_on_startup: bool = False
    check_on_startup: bool = False
    # -- purchase --
    auto_purchase_enabled: bool = True
    auto_purchase_threshold_mb: float = 2048.0
    payment_info_path: str = ""
    chrome_profile_dir: str = ""
    bank_name: str = "بانک پارسیان"
    package_text: str = "۱۰۰ گیگابایت ترافیک ۱۵ روزه"
    package_selector: str = "div:nth-child(2) > .row > .radio-box-new > .col-9-large > .desc"
    payment_wait_minutes: int = 10
    purchase_cooldown_hours: float = 6.0
    path: str = ""

    @property
    def credentials_present(self) -> bool:
        return (self.username.strip().lower() not in _PLACEHOLDERS and
                self.password.strip().lower() not in _PLACEHOLDERS)


def create_default(path: str,
                   username: str = USERNAME_PLACEHOLDER,
                   password: str = PASSWORD_PLACEHOLDER) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(_TEMPLATE.format(username=username, password=password))


def _append_purchase_section(path: str) -> None:
    """Add the [purchase] section to a config created before that feature existed."""
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n" + _PURCHASE_SECTION)


def load(path: str) -> Config:
    cp = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    # utf-8-sig tolerates a UTF-8 BOM that some editors (and PowerShell) add.
    cp.read(path, encoding="utf-8-sig")

    # Migrate older configs: append the [purchase] section if it's missing.
    if os.path.exists(path) and not cp.has_section("purchase"):
        _append_purchase_section(path)
        cp = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
        cp.read(path, encoding="utf-8-sig")
    cred = cp["credentials"] if cp.has_section("credentials") else {}
    s = cp["settings"] if cp.has_section("settings") else {}
    p = cp["purchase"] if cp.has_section("purchase") else {}

    def getf(section, key, default):
        try:
            return float(section.get(key, default))
        except (ValueError, AttributeError):
            return float(default)

    def geti(section, key, default):
        return int(getf(section, key, default))

    def getb(section, key, default):
        v = str(section.get(key, default)).strip().lower()
        return v in ("1", "true", "yes", "on")

    return Config(
        username=cred.get("username", "").strip() if cred else "",
        password=cred.get("password", "").strip() if cred else "",
        check_interval_minutes=max(1, geti(s, "check_interval_minutes", 30)),
        relaxed_interval_minutes=max(1, geti(s, "relaxed_interval_minutes", 360)),
        relaxed_traffic_threshold_mb=getf(s, "relaxed_traffic_threshold_mb", 10240),
        low_traffic_threshold_mb=getf(s, "low_traffic_threshold_mb", 2048),
        expire_warning_days=getf(s, "expire_warning_days", 3),
        service_expire_warning_days=getf(s, "service_expire_warning_days", 7),
        notify_repeat_hours=getf(s, "notify_repeat_hours", 6),
        report=(s.get("report", "CurrentTrafficPackages").strip() if s else "CurrentTrafficPackages"),
        notify_summary_on_startup=getb(s, "notify_summary_on_startup", "false"),
        check_on_startup=getb(s, "check_on_startup", "false"),
        auto_purchase_enabled=getb(p, "enabled", "true"),
        auto_purchase_threshold_mb=getf(p, "auto_purchase_threshold_gb", 2) * 1024.0,
        payment_info_path=(p.get("payment_info_path", "").strip() if p else ""),
        chrome_profile_dir=(p.get("chrome_profile_dir", "").strip() if p else ""),
        bank_name=(p.get("bank_name", "بانک پارسیان").strip() if p else "بانک پارسیان"),
        package_text=(p.get("package_text", Config.package_text).strip()
                      if p else Config.package_text),
        package_selector=(p.get("package_selector", Config.package_selector).strip()
                          if p else Config.package_selector),
        payment_wait_minutes=max(1, geti(p, "payment_wait_minutes", 10)),
        purchase_cooldown_hours=getf(p, "purchase_cooldown_hours", 6),
        path=path,
    )
