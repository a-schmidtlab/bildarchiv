"""Zustand einer Sammlung für die Anzeige im Menü."""

from datetime import datetime

from .. import einstellungen as es
from .. import jobs
from ..kern import db as kdb
from .texte import zahl


def zeit(ts):
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%d.%m. %H:%M")
    except (TypeError, ValueError):
        return ""


def plan_status(s):
    """'kein' | 'ausgefuehrt' | 'veraltet' | 'leer' | 'aktuell'."""
    db = s.db
    m = lambda k: kdb.meta_get(db, k, "")
    pid = m("plan_id")
    if not pid:
        return "kein"
    if m("plan_ausgefuehrt") == pid:
        return "ausgefuehrt"
    if m("plan_einstellungen_stand") != m("einstellungen_geaendert"):
        return "veraltet"
    bereich = "sammlung" if m("plan_modus") == "ordnen" else "import"
    if m("plan_scan_stand") != m(f"scan_{bereich}"):
        return "veraltet"
    if not db.execute("SELECT count(*) FROM plan").fetchone()[0]:
        return "leer"
    return "aktuell"


def dubletten_zahlen(s):
    r = s.db.execute("SELECT count(DISTINCT cluster), count(DISTINCT CASE WHEN unsicher=1 THEN cluster END) "
                     "FROM dubletten").fetchone()
    return r[0] or 0, r[1] or 0


def menue(s):
    """{eintrag: (zeichen, text)} – ✔ erledigt, ● in Arbeit/Hinweis, ○ offen, ✗ nicht möglich, ■ festgeschrieben."""
    db = s.db
    e = s.einstellungen
    m = lambda k, d="": kdb.meta_get(db, k, d)
    out = {}
    befund = kdb.meta_json(db, "befund")
    if befund and float(m("ausfuehrung_zeit", "0") or 0) > float(m("befund_zeit", "0") or 0):
        out["analyse"] = ("●", f"{zeit(m('befund_zeit'))}  vor der letzten Ausführung – neu einlesen")
    elif befund:
        out["analyse"] = ("✔", f"{zeit(m('befund_zeit'))}  {zahl(befund['dateien'])} Dateien")
    else:
        out["analyse"] = ("○", "noch nicht eingelesen")
    st = es.TEXTE[e["struktur"]["art"]]
    out["struktur"] = ("■" if s.festgeschrieben else ("✔" if befund else "○"), st)
    ps = plan_status(s)
    modus = m("plan_modus")
    if ps in ("aktuell", "leer") and modus == "ordnen":
        n, uns = dubletten_zahlen(s)
        out["dubletten"] = ("●" if uns else "✔", f"{n} Gruppen" + (f", {uns} unsicher" if uns else ""))
    else:
        out["dubletten"] = ("○", "nach dem Plan")
    out["namen"] = ("■" if s.festgeschrieben else "✔", e["namen"]["vorlage"])
    anzahl = db.execute("SELECT count(*) FROM plan").fetchone()[0]
    if ps == "aktuell" and modus == "ordnen":
        out["vorschau"] = ("✔", f"{zahl(anzahl)} Aktionen geplant")
    elif ps == "leer" and modus == "ordnen":
        out["vorschau"] = ("✔", "nichts zu tun – die Sammlung ist geordnet")
    elif ps == "veraltet" and modus == "ordnen":
        out["vorschau"] = ("●", "veraltet – bitte neu planen")
    elif ps == "ausgefuehrt":
        out["vorschau"] = ("✔", "ausgeführt – für weitere Änderungen neu planen")
    else:
        out["vorschau"] = ("○", "noch kein Plan")
    if ps == "aktuell" and modus == "ordnen":
        out["ausfuehren"] = ("○", "bereit")
    elif m("letzte_ausfuehrung"):
        st_ = m("letzte_ausfuehrung")
        out["ausfuehren"] = ("✔", f"zuletzt {st_[6:8]}.{st_[4:6]}.{st_[:4]} {st_[9:11]}:{st_[11:13]}")
    else:
        out["ausfuehren"] = ("✗", "erst nach der Vorschau")
    if s.festgeschrieben:
        out["import"] = ("○", "bereit" + ("  (Import-Plan liegt vor)" if ps == "aktuell" and modus == "import" else ""))
    else:
        out["import"] = ("✗", "erst wenn die Struktur festgelegt ist")
    j = s.journale()
    out["rueckgaengig"] = ("○" if j else "✗", f"{len(j)} Journale" if j else "noch nichts ausgeführt")
    out["einstellungen"] = ("■" if s.festgeschrieben else " ", "festgeschrieben" if s.festgeschrieben
                            else "noch nicht festgeschrieben")
    lf = jobs.laufender(db)
    out["_job"] = lf
    return out
