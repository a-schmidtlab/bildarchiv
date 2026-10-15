"""Einstellungen einer Sammlung: Struktur, Namen, Metadaten, Dubletten."""

import copy
import re
import tomllib

from .kern import vorlage

STANDARD = {
    "sammlung": {"id": "", "name": "", "festgeschrieben": False},
    "struktur": {
        "art": "jahr",                          # jahr | jahr_monat | jahr_ereignis
        "jahresordner": "{jahr}",
        "jahresordner_muster": "",              # Regex vorhandener Jahresordner, Gruppe 'jahr'
        "monatsordner": "{jahr}-{monat}",
        "ereignisordner": "{datum:JJJJ-MM-TT} {ereignis}",
        "videos": "jahresordner",               # jahresordner | eigener_ordner
        "videoordner": "Videos",
        "muell": "loeschen",                    # loeschen | verschieben | behalten
        "muellordner": "_Muell",
        "capture_one": "verwerfen",             # verwerfen | sonstiges
        "c1_trash": "eigener_ordner",           # eigener_ordner | normal
        "c1_trashordner": "_C1_Trash",
        "datum_konflikt": "ordner",             # ordner | exif
        "sonstiges": "_Sonstiges",
        "ordner": {},                           # Ordner -> aufloesen | ereignis | behalten | wurzel
        "ereignisnamen": {},                    # Ordner -> Name des Ereignisses (statt Ordnername)
    },
    "dubletten": {
        "ablage": "flach_praefix",              # flach_praefix | gruppenordner | gruppenordner_pro_jahr
        "ordner": "_Dubletten",
        "aehnliche": True,
    },
    "namen": {
        "vorlage": "{datum}_{zeit}_{kürzel}{lauf}_{nr:5}",
        "zaehler": "jahr",                      # gesamt | jahr | monat | tag | sekunde | ordner
        "kuerzel": "BA",
        "frueher": [],                          # frühere Vorlagen (Kürzel eingesetzt)
    },
    "metadaten": {
        "altname": "XMP-xmpMM:PreservedFileName",
        "ordnerpfad": "XMP-iptcExt:Event",
        "neuname": "EXIF:ImageUniqueID",
        "zusatz": [],                           # z. B. "IPTC:SpecialInstructions=altname"
        "altbestand_angleichen": False,
    },
    "klassifikation": {"sidecars_plus": [], "muell_plus": []},
}

AUSWAHL = {
    ("struktur", "art"): ["jahr", "jahr_monat", "jahr_ereignis"],
    ("struktur", "videos"): ["jahresordner", "eigener_ordner"],
    ("struktur", "muell"): ["loeschen", "verschieben", "behalten"],
    ("struktur", "capture_one"): ["verwerfen", "sonstiges"],
    ("struktur", "c1_trash"): ["eigener_ordner", "normal"],
    ("struktur", "datum_konflikt"): ["ordner", "exif"],
    ("dubletten", "ablage"): ["flach_praefix", "gruppenordner", "gruppenordner_pro_jahr"],
    ("namen", "zaehler"): ["gesamt", "jahr", "monat", "tag", "sekunde", "ordner"],
}
ORDNER_ENTSCHEIDUNG = ["aufloesen", "ereignis", "behalten", "wurzel"]
INHALTE = ("altname", "ordnerpfad", "neuname")

TEXTE = {
    "jahr": "Jahresordner, flach", "jahr_monat": "Jahr / Monat", "jahr_ereignis": "Jahr / Ereignis",
    "jahresordner": "im Jahresordner", "eigener_ordner": "eigener Ordner",
    "loeschen": "löschen", "verschieben": "verschieben",
    "verwerfen": "verwerfen", "sonstiges": "nach _Sonstiges", "normal": "wie andere Bilder",
    "ordner": "Ordner gewinnt", "exif": "EXIF gewinnt",
    "flach_praefix": "flach mit Präfix D00001_", "gruppenordner": "ein Ordner pro Gruppe",
    "gruppenordner_pro_jahr": "pro Jahr, ein Ordner pro Gruppe",
    "gesamt": "fortlaufend gesamt", "monat": "pro Monat", "tag": "pro Tag", "sekunde": "pro Sekunde",
    "aufloesen": "auflösen", "ereignis": "als Ereignis", "behalten": "unverändert lassen",
    "wurzel": "an die Wurzel verschieben",
}


def _merge(basis, neu):
    out = copy.deepcopy(basis)
    for k, v in (neu or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k not in ("ordner", "ereignisnamen"):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def standard():
    return copy.deepcopy(STANDARD)


def aus_dict(d):
    return _merge(STANDARD, d)


def laden(pfad):
    with open(pfad, "rb") as fh:
        return aus_dict(tomllib.load(fh))


# --------------------------------------------------------------------------- TOML schreiben

BARE = re.compile(r"^[A-Za-z0-9_-]+$")


def _key(k):
    return k if BARE.match(k) else _str(k)


def _str(s):
    out = ['"']
    for c in s:
        if c == '"':
            out.append('\\"')
        elif c == "\\":
            out.append("\\\\")
        elif c == "\n":
            out.append("\\n")
        elif c == "\t":
            out.append("\\t")
        elif ord(c) < 32 or ord(c) == 127:
            out.append(f"\\u{ord(c):04x}")
        else:
            out.append(c)
    out.append('"')
    return "".join(out)


def _wert(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return _str(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_wert(x) for x in v) + "]"
    raise TypeError(f"Nicht als TOML darstellbar: {v!r}")


def toml(d, prefix=""):
    zeilen, tabellen = [], []
    for k, v in d.items():
        if isinstance(v, dict):
            tabellen.append((k, v))
        else:
            zeilen.append(f"{_key(k)} = {_wert(v)}")
    out = []
    if zeilen:
        if prefix:
            out.append(f"[{prefix}]")
        out.extend(zeilen)
        out.append("")
    for k, v in tabellen:
        name = f"{prefix}.{_key(k)}" if prefix else _key(k)
        if not v:
            out.append(f"[{name}]")
            out.append("")
        else:
            out.append(toml(v, name))
    return "\n".join(out)


def speichern(e, pfad):
    from pathlib import Path
    p = Path(pfad)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text("# bildarchiv – Einstellungen dieser Sammlung\n\n" + toml(e), encoding="utf-8")
    tmp.replace(p)


# --------------------------------------------------------------------------- Änderungen mit Historie

def _alte_vorlage_merken(e):
    n = e["namen"]
    if not e["sammlung"].get("festgeschrieben"):
        return
    alt = n["vorlage"].replace("{kürzel}", n["kuerzel"]).replace("{kuerzel}", n["kuerzel"])
    if alt not in n["frueher"]:
        n["frueher"].append(alt)


def vorlage_aendern(e, neu):
    """Neue Namensvorlage; nach dem Festschreiben bleibt die alte zum Erkennen des Altbestands erhalten."""
    if neu != e["namen"]["vorlage"]:
        _alte_vorlage_merken(e)
        e["namen"]["vorlage"] = neu


def kuerzel_aendern(e, neu):
    if neu != e["namen"]["kuerzel"]:
        _alte_vorlage_merken(e)
        e["namen"]["kuerzel"] = neu


# --------------------------------------------------------------------------- Prüfung

def pruefe(e):
    """Alle Einstellungen prüfen. Liefert [(bereich, Meldung)]."""
    out = []
    for (g, k), werte in AUSWAHL.items():
        if e[g][k] not in werte:
            out.append((f"{g}.{k}", vorlage.Meldung("fehler", f"Ungültiger Wert '{e[g][k]}'")))
    s, n = e["struktur"], e["namen"]
    for m in vorlage.pruefe(n["vorlage"], n["zaehler"], s["art"], n["kuerzel"]):
        out.append(("namen.vorlage", m))
    if not re.fullmatch(r"[A-Za-z]{0,8}", n["kuerzel"]):
        out.append(("namen.kuerzel", vorlage.Meldung("fehler", "Das Kürzel darf nur aus 0–8 Buchstaben bestehen.")))
    for key, art in (("jahresordner", "jahresordner"), ("monatsordner", "monatsordner"),
                     ("ereignisordner", "ereignisordner")):
        if key == "monatsordner" and s["art"] != "jahr_monat":
            continue
        if key == "ereignisordner" and s["art"] != "jahr_ereignis":
            continue
        for m in vorlage.pruefe_ordner(s[key], art):
            out.append((f"struktur.{key}", m))
    if s["jahresordner_muster"]:
        try:
            rx = re.compile(s["jahresordner_muster"])
            if "jahr" not in rx.groupindex:
                out.append(("struktur.jahresordner_muster",
                            vorlage.Meldung("fehler", "Das Muster braucht eine Gruppe (?P<jahr>…).")))
        except re.error as err:
            out.append(("struktur.jahresordner_muster", vorlage.Meldung("fehler", f"Ungültiges Muster: {err}")))
    for z in e["metadaten"]["zusatz"]:
        feld, _, inhalt = z.partition("=")
        if not feld or inhalt not in INHALTE:
            out.append(("metadaten.zusatz", vorlage.Meldung(
                "fehler", f"'{z}': Format FELD=inhalt mit inhalt aus {', '.join(INHALTE)}")))
    for k, v in s["ordner"].items():
        if v not in ORDNER_ENTSCHEIDUNG:
            out.append(("struktur.ordner", vorlage.Meldung("fehler", f"Ordner '{k}': ungültige Entscheidung '{v}'")))
    return out


def hat_fehler(meldungen):
    return any(m.stufe == "fehler" for _, m in meldungen)
