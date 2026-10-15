"""Übernahme aus Datenbanken der Vorgängerversion (Einzelskripte mit Tabelle files, Daten unter
~/.local/share/bilderauswahlen).

Übernommen werden ImageDataHash, dHash und Miniaturen (spart das erneute Lesen aller Bilder) sowie
Entscheidungen, die sich aus der alten Ablage ergeben: Re-Edit-Ordner bleiben unverändert, die
Dubletten-Ablage folgt dem alten Schema, der Dubletten-Zähler läuft weiter.
"""

import re
import sqlite3

from . import db as kdb

PRO_JAHR_RE = re.compile(r"^[^/]+/_Dubletten/(\d{4,5})/", re.I)
FLACH_RE = re.compile(r"^_Dubletten/D(\d{5})_", re.I)


def finde(sammlung, ordner):
    """Alte Datenbanken in ordner, die zu dieser Sammlung gehören."""
    from pathlib import Path
    out = []
    for p in sorted(Path(ordner).glob("*.db")):
        try:
            db = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
            r = db.execute("SELECT value FROM meta WHERE key='archiv'").fetchone()
            db.close()
        except sqlite3.Error:
            continue
        if r and r[0].rstrip("/") == sammlung.wurzel.rstrip("/"):
            out.append(str(p))
    return out


def uebernehmen(sammlung, alte_db, reporter):
    db = sammlung.db
    if not kdb.meta_get(db, "scan_sammlung"):
        raise ValueError("Zuerst die Analyse ausführen (die Sammlung muss eingelesen sein).")
    alt = sqlite3.connect(f"file:{alte_db}?mode=ro", uri=True)
    try:
        wurzel_alt = (alt.execute("SELECT value FROM meta WHERE key='archiv'").fetchone() or [""])[0]
        spalten = {r[1] for r in alt.execute("PRAGMA table_info(files)")}
        if not {"path", "size", "datahash"} <= spalten:
            raise ValueError("Das ist keine Datenbank der Vorgängerversion.")
        if wurzel_alt and wurzel_alt.rstrip("/") != sammlung.wurzel.rstrip("/"):
            reporter.meldung(f"Die alte Datenbank gehört zu {wurzel_alt}. Übernommen wird nur, was nach Pfad "
                             "und Größe übereinstimmt.", "warnung")
        rows = alt.execute("SELECT path, size, datahash, dhash, aspect, thumb, role FROM files").fetchall()
    finally:
        alt.close()

    reporter.phase("Hashes übernehmen", len(rows))
    vorher = db.total_changes
    for i in range(0, len(rows), 2000):
        teil = rows[i:i + 2000]
        db.executemany(
            "UPDATE dateien SET datahash=coalesce(datahash, ?), dhash=coalesce(dhash, ?), "
            "aspect=coalesce(aspect, ?), thumb=coalesce(thumb, ?) "
            "WHERE bereich='sammlung' AND pfad=? AND groesse=? AND (datahash IS NULL OR dhash IS NULL)",
            [(h if h is not None else None, d, a, t, p, s) for p, s, h, d, a, t, _ in teil])
        reporter.fortschritt(min(i + 2000, len(rows)), len(rows))
    db.commit()
    uebernommen = db.total_changes - vorher

    # Ordner umbenannt oder Dateien verschoben: über eindeutigen Dateinamen + Größe zuordnen
    alt_name = {}
    for p, s, h, d, a, t, _ in rows:
        k = (p.rsplit("/", 1)[-1], s)
        alt_name[k] = None if k in alt_name else (h, d, a, t)
    offen = db.execute("SELECT pfad, groesse FROM dateien WHERE bereich='sammlung' AND datahash IS NULL "
                       "AND art IN ('bild','video')").fetchall()
    neu_name = {}
    for p, s in offen:
        k = (p.rsplit("/", 1)[-1], s)
        neu_name[k] = None if k in neu_name else p
    paare = [(alt_name[k], p) for k, p in neu_name.items() if p and alt_name.get(k)]
    if paare:
        reporter.phase("Hashes über Dateiname und Größe zuordnen", len(paare))
        vorher = db.total_changes
        db.executemany("UPDATE dateien SET datahash=?, dhash=coalesce(dhash, ?), aspect=coalesce(aspect, ?), "
                       "thumb=coalesce(thumb, ?) WHERE bereich='sammlung' AND pfad=?",
                       [(h, d, a, t, p) for (h, d, a, t), p in paare])
        db.commit()
        n2 = db.total_changes - vorher
        uebernommen += n2
        reporter.meldung(f"davon über Dateiname und Größe zugeordnet (Ordner umbenannt/verschoben): {n2}")

    e = sammlung.einstellungen
    aenderungen = []
    # Re-Edit-Ordner (vom alten Lauf an die Wurzel verschoben) bleiben unverändert
    vorhanden = {p.split("/", 1)[0] for (p,) in db.execute("SELECT pfad FROM dateien WHERE bereich='sammlung'")
                 if "/" in p}
    reedit = sorted({p.split("/", 1)[0] for p, *_, rolle in rows if rolle == "reedit" and "/" in p} & vorhanden)
    for top in reedit:
        if e["struktur"]["ordner"].get(top) != "behalten":
            e["struktur"]["ordner"][top] = "behalten"
            aenderungen.append(f"Ordner „{top}“ bleibt unverändert (Re-Edit)")
    # Dubletten-Ablage und Zähler
    pro_jahr = [int(m[1]) for p, *_ in rows if (m := PRO_JAHR_RE.match(p))]
    flach = [int(m[1]) for p, *_ in rows if (m := FLACH_RE.match(p))]
    if pro_jahr and len(pro_jahr) >= len(flach):
        if e["dubletten"]["ablage"] != "gruppenordner_pro_jahr":
            e["dubletten"]["ablage"] = "gruppenordner_pro_jahr"
            aenderungen.append("Dubletten-Ablage: pro Jahr, ein Ordner pro Gruppe (wie bisher)")
    elif flach and e["dubletten"]["ablage"] != "flach_praefix":
        e["dubletten"]["ablage"] = "flach_praefix"
        aenderungen.append("Dubletten-Ablage: flach mit Präfix D00001_ (wie bisher)")
    hoechste = max(pro_jahr + flach, default=0)
    if hoechste:
        kdb.zaehler_setzen(db, {"dubletten": hoechste})
        aenderungen.append(f"Dubletten-Zähler läuft ab {hoechste + 1} weiter")
    if aenderungen and not sammlung.festgeschrieben:
        sammlung.speichere_einstellungen()
    db.commit()
    reporter.meldung(f"Hashes übernommen für {uebernommen} Dateien (von {len(rows)} in der alten Datenbank)")
    for a in aenderungen:
        reporter.meldung(a)
    return dict(uebernommen=uebernommen, gesamt=len(rows), aenderungen=aenderungen)
