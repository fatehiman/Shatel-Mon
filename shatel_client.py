"""
shatel_client.py — Logs into my.shatel.ir and reads the "Current Traffic Packages"
report, returning the per-package remaining traffic and remaining time.

The login is an OpenID Connect (IdentityServer) flow:

  1. GET my.shatel.ir/Home/Index  ->  redirects to the SPA login page carrying
     a ?returnUrl=<authorize-callback> query parameter.
  2. POST account-api.shatel.ir/ui/v1.0/account/login  with JSON
     {username, password, returnUrl}.  On success the JSON result contains the
     authorize-callback returnUrl.
  3. GET that callback  ->  returns an auto-submitting <form method=post> ("form_post"
     response mode) that posts an authorization `code` back to my.shatel.ir/Account/FireLogin.
  4. POST those hidden fields to FireLogin  ->  sets the authenticated session cookie.
  5. GET /Home/Index, scrape the per-session 32-hex GUID that prefixes every report URL.
  6. GET /{guid}/Report/CurrentTrafficPackages and parse the packages table.

The GUID in step 5/6 is session specific and expires, which is why it is discovered
fresh on every run rather than stored in the config.
"""

from __future__ import annotations

import datetime
import logging
import re
from dataclasses import dataclass
from urllib.parse import urlparse, parse_qs

import requests
import urllib3

from jalali import jalali_str_to_gregorian

# We intentionally skip TLS verification (see ShatelClient._new_session), so
# silence the per-request InsecureRequestWarning it would otherwise emit.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

log = logging.getLogger("shatelmon.client")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

ENTRY_URL = "https://my.shatel.ir/Home/Index"
HOME_URL = "https://my.shatel.ir/Home/Index"
LOGIN_API = "https://account-api.shatel.ir/ui/v1.0/account/login"
LOGIN_CONTEXT_API = "https://account-api.shatel.ir/ui/v1.0/account/login/context"
REPORT_URL_TMPL = "https://my.shatel.ir/{guid}/Report/{report}"

# Persian/Arabic-Indic digits -> ASCII
_DIGIT_MAP = {ord(c): str(i) for i, c in enumerate("۰۱۲۳۴۵۶۷۸۹")}
_DIGIT_MAP.update({ord(c): str(i) for i, c in enumerate("٠١٢٣٤٥٦٧٨٩")})
_ZWNJ = "‌"


class ShatelError(Exception):
    """Base error for anything that goes wrong talking to Shatel."""


class LoginError(ShatelError):
    """Raised when authentication fails (bad credentials, OIDC failure, ...)."""


@dataclass
class Package:
    name: str                     # e.g. "پایه", "روزشمار", "مصرف مازاد"
    domain: str                   # e.g. "اینترنت"
    volume_mb: float | None       # total package volume, MB
    used_mb: float | None         # consumed, MB
    remaining_mb: float | None    # remaining, MB ("---" -> None, over-usage -> negative)
    duration_days: int | None     # configured package length, days
    remaining_days: float | None  # time left until expiry, fractional days
    remaining_time_text: str      # raw "X روز و Y ساعت و Z دقیقه"

    @property
    def is_excess(self) -> bool:
        # "مصرف مازاد" = over-usage / debt row, not a real package
        return "مازاد" in self.name


@dataclass
class ServiceExpiry:
    jalali: str                   # raw "1405/09/27"
    gregorian: datetime.date      # converted date
    remaining_days: int           # whole days from today (can be negative if past)


def _norm_digits(s: str) -> str:
    return (s or "").translate(_DIGIT_MAP)


def _norm_header(s: str) -> str:
    """Strip tags, ZWNJ and whitespace so Persian headers can be matched exactly."""
    s = re.sub(r"<[^>]+>", "", s or "")
    return s.replace(_ZWNJ, "").replace(" ", "").strip()


def _parse_mb(cell_html: str) -> float | None:
    """Pull the precise MB figure out of a cell's title="12,288.00 MB" attribute."""
    m = re.search(r'title="\s*(-?[\d,]+(?:\.\d+)?)\s*MB"', cell_html)
    if not m:
        return None
    return float(m.group(1).replace(",", ""))


def _parse_remaining_days(text: str) -> float | None:
    """'190 روز و 14 ساعت و 3 دقیقه' -> 190.58 (fractional days). '0'/'' -> 0."""
    text = _norm_digits(text or "").strip()
    if not text or text == "0":
        return 0.0
    days = hours = minutes = 0
    m = re.search(r"(\d+)\s*روز", text)
    if m:
        days = int(m.group(1))
    m = re.search(r"(\d+)\s*ساعت", text)
    if m:
        hours = int(m.group(1))
    m = re.search(r"(\d+)\s*دقیقه", text)
    if m:
        minutes = int(m.group(1))
    if days == hours == minutes == 0 and not re.search(r"روز|ساعت|دقیقه", text):
        return None
    return days + hours / 24.0 + minutes / 1440.0


def _strip_tags(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


class ShatelClient:
    def __init__(self, username: str, password: str,
                 report: str = "CurrentTrafficPackages", timeout: int = 40):
        self.username = username
        self.password = password
        self.report = report
        self.timeout = timeout
        self.session: requests.Session | None = None
        self.guid: str | None = None

    # -- session ---------------------------------------------------------------

    def _new_session(self) -> requests.Session:
        s = requests.Session()
        s.headers.update({"User-Agent": USER_AGENT,
                          "Accept-Language": "fa,en;q=0.8"})
        # Shatel occasionally serves an invalid/misconfigured certificate on the
        # report endpoints; skip TLS verification so the quota fetch still works.
        s.verify = False
        return s

    # -- login -----------------------------------------------------------------

    def login(self) -> None:
        s = self._new_session()

        # 1. trigger the OIDC redirect chain and land on the login page
        r = s.get(ENTRY_URL, timeout=self.timeout)
        q = parse_qs(urlparse(r.url).query)
        return_url = (q.get("returnUrl") or [None])[0]
        if not return_url:
            raise LoginError(f"Could not find OIDC returnUrl (landed on {r.url})")

        # 2. warm up the login context (mirrors the browser; harmless if it 404s)
        try:
            s.get(LOGIN_CONTEXT_API, params={"returnUrl": return_url},
                  headers={"Accept": "application/json"}, timeout=self.timeout)
        except requests.RequestException:
            pass

        # 3. submit credentials
        r = s.post(LOGIN_API,
                   json={"username": self.username,
                         "password": self.password,
                         "returnUrl": return_url},
                   headers={"Accept": "application/json",
                            "Origin": "https://account.shatel.ir",
                            "Referer": "https://account.shatel.ir/"},
                   timeout=self.timeout)
        try:
            data = r.json()
        except ValueError:
            raise LoginError(f"Login endpoint returned non-JSON (HTTP {r.status_code})")
        if not data.get("isSuccess"):
            msgs = "; ".join(data.get("messages") or []) or f"HTTP {r.status_code}"
            raise LoginError(f"Login rejected: {msgs}")
        callback_url = (data.get("result") or {}).get("returnUrl")
        if not callback_url:
            raise LoginError("Login succeeded but no callback returnUrl was returned")

        # 4. follow the authorize callback -> get the form_post page
        r = s.get(callback_url,
                  headers={"Referer": "https://account.shatel.ir/"},
                  timeout=self.timeout)
        action, fields = self._parse_form_post(r.text)
        if not action:
            raise LoginError("OIDC callback did not return the expected form_post page")

        # 5. post the authorization code to FireLogin -> sets the auth cookie
        s.post(action, data=fields, allow_redirects=False,
               headers={"Content-Type": "application/x-www-form-urlencoded",
                        "Origin": "https://account-api.shatel.ir",
                        "Referer": "https://account-api.shatel.ir/"},
               timeout=self.timeout)

        # 6. confirm we are in and grab the live GUID
        self.session = s
        self.guid = self._discover_guid()
        if not self.guid:
            raise LoginError("Authenticated but could not locate the session report GUID")
        log.info("Login OK (guid=%s)", self.guid)

    @staticmethod
    def _parse_form_post(html: str):
        m = re.search(r"<form[^>]*action='([^']+)'", html) or \
            re.search(r'<form[^>]*action="([^"]+)"', html)
        action = m.group(1) if m else None
        fields = dict(re.findall(r"name='([^']+)'\s+value='([^']*)'", html))
        if not fields:
            fields = dict(re.findall(r'name="([^"]+)"\s+value="([^"]*)"', html))
        return action, fields

    def _discover_guid(self) -> str | None:
        r = self.session.get(HOME_URL, timeout=self.timeout)
        m = re.search(r"/([a-f0-9]{32})/Report/", r.text)
        return m.group(1) if m else None

    # -- report ----------------------------------------------------------------

    def get_packages(self, relogin: bool = True) -> list[Package]:
        """Fetch and parse the traffic packages. Logs in automatically if needed."""
        if not self.session or not self.guid:
            self.login()
        url = REPORT_URL_TMPL.format(guid=self.guid, report=self.report)
        r = self.session.get(url, headers={"Referer": HOME_URL}, timeout=self.timeout)

        packages = self._parse_packages(r.text)
        if not packages and relogin:
            # session/GUID probably expired -> log in again and retry once
            log.info("No packages parsed; re-authenticating and retrying once")
            self.session = self.guid = None
            self.login()
            url = REPORT_URL_TMPL.format(guid=self.guid, report=self.report)
            r = self.session.get(url, headers={"Referer": HOME_URL}, timeout=self.timeout)
            packages = self._parse_packages(r.text)
        if not packages:
            raise ShatelError("Logged in but found no traffic packages in the report")
        return packages

    # -- service expiry --------------------------------------------------------

    def get_service_expiry(self, relogin: bool = True) -> ServiceExpiry:
        """Read 'End of current period' (پایان دوره جاری) from the dashboard,
        convert the Jalali date to Gregorian and compute remaining days."""
        if not self.session or not self.guid:
            self.login()
        html = self._fetch_home_html()
        expiry = self._parse_service_expiry(html)
        if expiry is None and relogin:
            log.info("Service expiry not found; re-authenticating and retrying once")
            self.session = self.guid = None
            self.login()
            html = self._fetch_home_html()
            expiry = self._parse_service_expiry(html)
        if expiry is None:
            raise ShatelError("Logged in but could not find the service expiry date")
        return expiry

    def _fetch_home_html(self) -> str:
        r = self.session.get(HOME_URL, headers={"Referer": "https://my.shatel.ir/"},
                             timeout=self.timeout)
        return r.text

    @staticmethod
    def _parse_service_expiry(html: str) -> ServiceExpiry | None:
        # <span for="BuildUpTo">پایان دوره جاری:</span> ... <strong> 1405/09/27</strong>
        m = re.search(r'for="BuildUpTo".*?(1[34]\d{2}/\d{1,2}/\d{1,2})', html, re.S)
        if not m:
            m = re.search(r'پایان دوره جاری.*?(1[34]\d{2}/\d{1,2}/\d{1,2})', html, re.S)
        if not m:
            return None
        jalali = m.group(1)
        try:
            greg = jalali_str_to_gregorian(jalali)
        except ValueError:
            return None
        remaining = (greg - datetime.date.today()).days
        return ServiceExpiry(jalali=jalali, gregorian=greg, remaining_days=remaining)

    @staticmethod
    def _parse_packages(html: str) -> list[Package]:
        packages: list[Package] = []
        for table in re.findall(r"<table.*?</table>", html, re.S):
            headers = [_norm_header(h) for h in
                       re.findall(r"<th[^>]*>(.*?)</th>", table, re.S)]
            if "نوعبسته" not in headers or "باقیمانده" not in headers:
                continue  # not the per-package table

            idx = {name: i for i, name in enumerate(headers)}
            i_name = idx["نوعبسته"]
            i_domain = idx.get("دامنهاستفاده")
            i_vol = idx.get("حجمبسته")
            i_used = idx.get("مقدارمصرف")
            i_rem = idx["باقیمانده"]
            i_dur = idx.get("مدتزمانبسته(روز)")
            i_rtime = idx.get("زمانباقیمانده")

            rows = re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.S)
            for row in rows:
                cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
                if len(cells) <= i_rem:
                    continue

                def txt(i):
                    return _strip_tags(cells[i]) if i is not None and i < len(cells) else ""

                def mb(i):
                    return _parse_mb(cells[i]) if i is not None and i < len(cells) else None

                dur_raw = _norm_digits(txt(i_dur)) if i_dur is not None else ""
                try:
                    duration_days = int(re.sub(r"[^\d-]", "", dur_raw)) if dur_raw else None
                except ValueError:
                    duration_days = None

                rtime_text = txt(i_rtime) if i_rtime is not None else ""
                packages.append(Package(
                    name=txt(i_name),
                    domain=txt(i_domain),
                    volume_mb=mb(i_vol),
                    used_mb=mb(i_used),
                    remaining_mb=mb(i_rem),
                    duration_days=duration_days,
                    remaining_days=_parse_remaining_days(rtime_text),
                    remaining_time_text=rtime_text,
                ))
            break  # only the first matching table
        return packages


def total_remaining_mb(packages: list[Package]) -> float:
    """Sum of positive remaining traffic across real packages (excludes debt/'---')."""
    return sum(p.remaining_mb for p in packages
               if p.remaining_mb is not None and p.remaining_mb > 0)
