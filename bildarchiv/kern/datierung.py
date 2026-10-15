"""Datum aus EXIF, Datei- und Ordnernamen."""

import re
from datetime import datetime

# Kamera-Standarddaten (Uhr nicht gestellt)
INVALID_DATES = {"19700101", "19800101", "20000101"}

DATE_RE = re.compile(r"^(\d{4})[:\-](\d{2})[:\-](\d{2})(?:[ T](\d{2}):(\d{2}):(\d{2}))?")
YMD_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})[-_.]?(\d{2})[-_.]?(\d{2})(?!\d)")
YM_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})-(\d{2})(?!\d)")
DMY_RE = re.compile(r"(?<!\d)(\d{1,2})\.(\d{1,2})\.((?:19|20)?\d{2})(?!\d)")
FNAME_DT_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})[-_.]?(\d{2})[-_.]?(\d{2})"
                         r"(?:[-_. T]?(\d{2})[-_.:]?(\d{2})[-_.:]?(\d{2}))?(?!\d)")
JAHR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
FUEHRENDES_JAHR_RE = re.compile(r"^((?:19|20)\d{2})\b")


def parse_dt(val):
    """EXIF-Datum -> ('YYYYMMDD', 'HHMMSS') oder None."""
    if not val:
        return None
    m = DATE_RE.match(str(val).strip())
    if not m:
        return None
    y, mo, d = int(m[1]), int(m[2]), int(m[3])
    if not (1900 <= y <= 2100 and 1 <= mo <= 12 and 1 <= d <= 31):
        return None
    if f"{m[1]}{m[2]}{m[3]}" in INVALID_DATES:
        return None
    hh, mm, ss = (m[4] or "00"), (m[5] or "00"), (m[6] or "00")
    return f"{m[1]}{m[2]}{m[3]}", f"{hh}{mm}{ss}"


def name_date(texts):
    """Erstes plausibles Datum (+Uhrzeit) aus Datei-/Ordnernamen, unabhängig vom Jahr."""
    for t in texts:
        for m in FNAME_DT_RE.finditer(t):
            y, mo, d = int(m[1]), int(m[2]), int(m[3])
            if not (1 <= mo <= 12 and 1 <= d <= 31) or y > datetime.now().year:
                continue
            if f"{m[1]}{m[2]}{m[3]}" in INVALID_DATES:
                continue
            tm = "000000"
            if m[4] and int(m[4]) < 24 and int(m[5]) < 60 and int(m[6]) < 60:
                tm = f"{m[4]}{m[5]}{m[6]}"
            return f"{m[1]}{m[2]}{m[3]}", tm
    return None


def derive_date(texts, year):
    """Datum aus Datei-/Ordnernamen ableiten, nur wenn es zum Jahr passt; sonst 'YYYY0000'."""
    for t in texts:
        for m in YMD_RE.finditer(t):
            if int(m[1]) == year and 1 <= int(m[2]) <= 12 and 1 <= int(m[3]) <= 31:
                return f"{m[1]}{m[2]}{m[3]}"
        for m in YM_RE.finditer(t):
            if int(m[1]) == year and 1 <= int(m[2]) <= 12:
                return f"{m[1]}{m[2]}00"
    return f"{year}0000"


def folder_date_year(sub):
    """Jahr eines eindeutigen Datums im Unterordnerpfad (tiefster Ordner zuerst), sonst None."""
    for part in reversed([p for p in sub.split("/") if p]):
        for m in YMD_RE.finditer(part):
            if 1 <= int(m[2]) <= 12 and 0 <= int(m[3]) <= 31:
                return int(m[1])
        for m in DMY_RE.finditer(part):
            if 1 <= int(m[1]) <= 31 and 1 <= int(m[2]) <= 12:
                y = int(m[3])
                return y if y > 99 else (2000 + y if y < 70 else 1900 + y)
    return None


def fuehrendes_jahr(name):
    m = FUEHRENDES_JAHR_RE.match(name)
    return int(m[1]) if m else None


_DATUM = (r"(?:(?:19|20)\d{2}[-_.]?\d{2}[-_.]?\d{2}|\d{1,2}\.\d{1,2}\.(?:19|20)?\d{2}"
          r"|(?:19|20)\d{2}[-_.]\d{2}|(?:19|20)\d{2})")
EREIGNIS_DATUM_RE = re.compile(r"^\s*" + _DATUM + r"(?=$|[\s_\-.])[\s_\-.]*")
EREIGNIS_DATUM_ENDE_RE = re.compile(r"(?:^|(?<=[\s_\-.]))" + _DATUM + r"[\s_\-.]*$")
JAHRESBEREICH_RE = re.compile(r"(?:19|20)\d{2}\s*[-–]\s*(?:19|20)\d{2}\s*$")


def ereignis_name(ordnername):
    """Ordnernamen ohne Datum am Anfang oder Ende: '2019-07-14 Hochzeit' / 'Scans 2020-05-01' -> Name."""
    rest = ordnername
    while True:
        neu = EREIGNIS_DATUM_RE.sub("", rest, count=1)
        if not JAHRESBEREICH_RE.search(neu):
            neu = EREIGNIS_DATUM_ENDE_RE.sub("", neu, count=1).rstrip(" _-.")
        if neu == rest:
            break
        rest = neu
    return rest.strip(" _-.") or ordnername.strip()
