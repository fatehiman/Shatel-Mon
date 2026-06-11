"""Self-contained Jalali (Persian/Solar Hijri) -> Gregorian date conversion.

Uses the well-known Borujeni/jalaali algorithm; no third-party dependency.
"""

from __future__ import annotations

import datetime


def jalali_to_gregorian(jy: int, jm: int, jd: int) -> datetime.date:
    """Convert a Jalali (jy, jm, jd) date to a Gregorian datetime.date."""
    jy += 1595
    days = (-355668 + (365 * jy) + ((jy // 33) * 8) +
            (((jy % 33) + 3) // 4) + jd)
    if jm < 7:
        days += (jm - 1) * 31
    else:
        days += ((jm - 7) * 30) + 186

    gy = 400 * (days // 146097)
    days %= 146097
    if days > 36524:
        days -= 1
        gy += 100 * (days // 36524)
        days %= 36524
        if days >= 365:
            days += 1
    gy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        gy += (days - 1) // 365
        days = (days - 1) % 365
    gd = days + 1

    leap = (gy % 4 == 0 and gy % 100 != 0) or (gy % 400 == 0)
    months = [31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    gm = 0
    while gm < 12 and gd > months[gm]:
        gd -= months[gm]
        gm += 1
    return datetime.date(gy, gm + 1, gd)


def parse_jalali(text: str) -> tuple[int, int, int]:
    """Parse '1405/09/27' (Latin or Persian digits) into (year, month, day)."""
    digit_map = {ord(c): str(i) for i, c in enumerate("۰۱۲۳۴۵۶۷۸۹")}
    digit_map.update({ord(c): str(i) for i, c in enumerate("٠١٢٣٤٥٦٧٨٩")})
    text = text.translate(digit_map).strip()
    parts = [p for p in text.replace("-", "/").split("/") if p != ""]
    if len(parts) != 3:
        raise ValueError(f"Not a Jalali date: {text!r}")
    jy, jm, jd = (int(p) for p in parts)
    return jy, jm, jd


def jalali_str_to_gregorian(text: str) -> datetime.date:
    return jalali_to_gregorian(*parse_jalali(text))


if __name__ == "__main__":
    # quick self-check
    for s, expect in [("1405/09/27", None), ("1403/01/01", datetime.date(2024, 3, 20)),
                      ("1399/12/30", datetime.date(2021, 3, 20))]:
        g = jalali_str_to_gregorian(s)
        ok = "" if expect is None else ("OK" if g == expect else f"EXPECTED {expect}")
        print(s, "->", g, ok)
