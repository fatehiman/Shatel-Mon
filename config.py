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

; Show a normal (non-alert) summary notification once at startup.
notify_summary_on_startup = false
"""


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


def load(path: str) -> Config:
    cp = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    # utf-8-sig tolerates a UTF-8 BOM that some editors (and PowerShell) add.
    cp.read(path, encoding="utf-8-sig")
    cred = cp["credentials"] if cp.has_section("credentials") else {}
    s = cp["settings"] if cp.has_section("settings") else {}

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
        path=path,
    )
