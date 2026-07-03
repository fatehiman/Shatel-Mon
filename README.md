# Shatel Mon (ShatelMon)

A tiny Windows **system-tray** app that periodically checks your [Shatel](https://my.shatel.ir)
internet account and raises a Windows notification when **traffic is running low** or the
**current service period is about to expire**.

The tray icon is the **Shatel logo** (navy "A" with an orange circle). While a check is in
progress the **orange circle blinks** (hides/shows); the mark turns **red** while an alert
is active.

<p align="center"><img src="ShatelMon.png" width="96" alt="ShatelMon icon"></p>

## Features

- Logs into `my.shatel.ir` (OpenID Connect / IdentityServer flow) and reads the
  **Current Traffic Packages** report.
- On the same interval it also reads the **end of the current service period**
  (پایان دوره جاری), which is a **Jalali** date, converts it to Gregorian and computes the
  remaining days.
- Raises a Windows tray notification when **any** of these is true:
  - the **combined remaining traffic** of all packages drops below
    `low_traffic_threshold_mb` (default **2 GB**);
  - a traffic package that still has data left is within `expire_warning_days`
    (default **3 days**) of expiring;
  - the **service period** is within `service_expire_warning_days` (default **7 days**)
    of ending.
- The website's pre-computed "total" row is **not** trusted — the total is computed from
  each package's own remaining figure (positive packages only).
- Reads the **PPPOE connection status** (وضعیت اتصال) from the dashboard and includes it
  on a new line of the remaining-traffic notification as **Connected** / **Disconnected**.
- **Adaptive interval:** when plenty of traffic remains (above `relaxed_traffic_threshold_mb`,
  default 10 GB) and no alert is active, it checks every `relaxed_interval_minutes`
  (default 6 h); once traffic drops below that or an alert is active, it checks every
  `check_interval_minutes` (default 30 min).
- **Fetch errors are reported:** if login fails, the network is down, or the report can't be
  read, you get a clear notification (manual fetches always notify; periodic errors are
  de-duplicated so a long outage won't spam you).
- **Auto-buys traffic** when the combined remaining traffic drops below
  `auto_purchase_threshold_gb` (default **2 GB**). It drives a **real Chrome**
  (via Playwright, not headless — so the bank sees a normal Windows 11 browser),
  logs in, selects the configured package, chooses the bank, and fills the card
  number / CVV2 / expiry from an **external file**. Payment stays **human-in-the-loop**:
  the app requests the dynamic password (OTP) and then waits for **you** to enter
  the CAPTCHA + OTP and click **Pay**, after which it finishes the purchase on the
  Shatel side automatically. See [Automatic traffic purchase](#automatic-traffic-purchase).
- **All notifications are in English.**
- **Single instance** only — launching a second copy shows a message and exits.
- **First run** creates a config file with placeholders, tells you to fill it in, and exits.

## Tray menu

| Item | Action |
|------|--------|
| **Fetch remaind quota now** | Check remaining traffic immediately and show the result |
| **Fetch service expire date now** | Check the service expiry date immediately and show the result |
| **Buy traffic now** | Start a traffic purchase immediately (opens Chrome; you enter the OTP and pay) |
| **Exit** | Quit the app |

Each manual fetch always shows a notification with the result; the periodic background
checks only notify when something needs attention. Hovering the tray icon shows the
current remaining traffic and service-end date.

## Install & run

### Option A — download the released `.exe` (no Python needed)

1. Download `ShatelMon.exe` from the [Releases](../../releases) page.
2. Run it once. It creates **`ShatelMon.conf`** next to the exe and asks you to fill in your
   credentials, then exits.
3. Open `ShatelMon.conf`, set your `username` and `password`, save, and run `ShatelMon.exe`
   again. The orange **SH** icon appears in the system tray.

### Option B — from source

```powershell
pip install -r requirements.txt
python ShatelMon.py          # or: double-click ShatelMon.vbs (no console window)
```

### Start automatically at login

Press <kbd>Win</kbd>+<kbd>R</kbd>, type `shell:startup`, and drop a shortcut to
`ShatelMon.exe` (or `ShatelMon.vbs`) into that folder.

## Configuration — `ShatelMon.conf`

A sample is provided in [`ShatelMon.sample.conf`](ShatelMon.sample.conf):

```ini
[credentials]
username = YOUR_USERNAME
password = YOUR_PASSWORD

[settings]
check_interval_minutes       = 30     ; interval when traffic is low / an alert is active
relaxed_interval_minutes     = 360    ; interval when plenty of traffic remains (6 h)
relaxed_traffic_threshold_mb = 10240  ; "plenty" = combined remaining above this (10 GB)
low_traffic_threshold_mb     = 2048   ; alert below this combined remaining (2 GB)
expire_warning_days          = 3      ; alert when a package expires within N days
service_expire_warning_days  = 7      ; alert when the service period ends within N days
notify_repeat_hours          = 6      ; don't repeat the same alert more often than this
report                       = CurrentTrafficPackages
notify_summary_on_startup    = false  ; show a one-off summary notification at startup
```

A UTF-8 BOM (added by some editors / PowerShell) is tolerated.

## Automatic traffic purchase

When the combined remaining traffic drops below `auto_purchase_threshold_gb`
(default **2 GB**), ShatelMon buys more traffic for you. You can also trigger it
any time from the tray menu (**Buy traffic now**).

**How it works**

1. It drives your **installed Chrome** through [Playwright](https://playwright.dev/python/)
   with a **persistent profile** under `%LOCALAPPDATA%\ShatelMon\chrome-profile`
   — *not* headless, so the payment gateway sees a normal Windows 11 Chrome
   (genuine User-Agent + client hints). The profile keeps you logged in between
   purchases.
2. It logs in (if needed), opens **خرید ترافیک**, selects the package named by
   `package_selector`, and continues to the bank chosen by `bank_name`.
3. On the bank gateway it fills the **card number**, **CVV2** and **expiry**
   from the file at `payment_info_path`, and clicks *request dynamic password*
   (رمز پویا) if that button exists.
4. It then **waits for you**: enter the CAPTCHA (کد امنیتی) and the dynamic
   password (رمز دوم کارت) in the browser and click **Pay** (پرداخت). Your money
   is never spent without you.
5. When the gateway shows *پرداخت با موفقیت انجام شد*, it clicks **ادامه** to
   finish the purchase back on Shatel.

If the automation stalls or you take too long, the browser window is **left
open** so you can finish or inspect the payment manually — it is never closed
mid-payment.

**The payment file** (`payment_info_path`) is a plain `key=value` file kept
**outside** the project — only its *path* is stored in `ShatelMon.conf`:

```ini
cardno=6219861073116864
cvv=475
exp-month=09
exp-year=07
```

**Requirements:** `pip install playwright` (already in `requirements.txt`) and a
normal Google **Chrome** install. No `playwright install` browser download is
needed — it uses your system Chrome (`channel="chrome"`).

**Turn it off** by setting `enabled = false` in the `[purchase]` section; the
**Buy traffic now** menu item still works.

## Build the `.exe` yourself

```powershell
pip install -r requirements.txt pyinstaller
build.bat            # -> dist\ShatelMon.exe
```

## Project layout

| File | Purpose |
|------|---------|
| `ShatelMon.py` | Tray app: icon, periodic checks, menu, notifications, single-instance, first-run |
| `purchase.py` | Automated traffic purchase + payment (Playwright / real Chrome) |
| `shatel_client.py` | Login + traffic report + service-expiry fetch/parse |
| `jalali.py` | Jalali → Gregorian date conversion (no dependency) |
| `config.py` | Reads/creates `ShatelMon.conf` |
| `icon.py` | Generates the tray icon |
| `ShatelMon.sample.conf` | Template config (copy to `ShatelMon.conf`) |
| `ShatelMon.vbs` | Silent launcher |
| `build.bat` | Builds the exe |

## Security notes

- Your password is stored **in plain text** in `ShatelMon.conf`. The file is **git-ignored**
  and never committed — keep it private.
- If login fails or the network is down, the app shows a single notification (not a flood)
  and retries on the next interval.
- The report URL contains a 32-character session token that **expires**; the app always
  discovers a fresh one after logging in, so you never paste it.

## License

[MIT](LICENSE)
