"""Lange Schritte als eigener Prozess: das Menü kann geschlossen werden, der Lauf geht weiter.

Fortschritt und Meldungen stehen in der Datenbank der Sammlung (Tabellen jobs, job_log).
"""

import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path

from . import ablauf
from .kern import db as kdb
from .reporter import Abbruch, DbReporter


class JobFehler(Exception):
    pass


def _lebt(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # Zombie (beendet, aber vom Elternprozess noch nicht abgeholt)?
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().split(")")[-1].split()[0] != "Z"
    except OSError:
        return True


def laufender(db):
    """Laufender Job als dict oder None. Verwaiste Einträge werden als abgebrochen markiert."""
    for jid, schritt, status, pid in db.execute(
            "SELECT id, schritt, status, pid FROM jobs WHERE status IN ('wartet','laeuft','abbrechen') ORDER BY id"
    ).fetchall():
        if status == "wartet" and not pid:
            return job(db, jid)
        if _lebt(pid):
            return job(db, jid)
        db.execute("UPDATE jobs SET status='abgestuerzt', beendet=? WHERE id=?", (time.time(), jid))
        db.commit()
    return None


def job(db, jid):
    r = db.execute("SELECT id, schritt, args, status, pid, phase, fertig, gesamt, gestartet, beendet, ergebnis "
                   "FROM jobs WHERE id=?", (jid,)).fetchone()
    if not r:
        return None
    k = ["id", "schritt", "args", "status", "pid", "phase", "fertig", "gesamt", "gestartet", "beendet", "ergebnis"]
    d = dict(zip(k, r))
    d["args"] = json.loads(d["args"] or "{}")
    d["ergebnis"] = json.loads(d["ergebnis"]) if d["ergebnis"] else None
    return d


def letzter(db, schritt=None):
    q = "SELECT id FROM jobs" + (" WHERE schritt=?" if schritt else "") + " ORDER BY id DESC LIMIT 1"
    r = db.execute(q, (schritt,) if schritt else ()).fetchone()
    return job(db, r[0]) if r else None


def log(db, jid, ab=0):
    return db.execute("SELECT id, zeit, stufe, text FROM job_log WHERE job=? AND id>? ORDER BY id",
                      (jid, ab)).fetchall()


def starte(sammlung, schritt, args=None, im_prozess=False):
    """Schritt als Hintergrundprozess starten. Liefert die Job-ID."""
    db = sammlung.db
    if schritt not in ablauf.SCHRITTE:
        raise JobFehler(f"Unbekannter Schritt: {schritt}")
    lf = laufender(db)
    if lf:
        raise JobFehler(f"Es läuft bereits: {ablauf.NAMEN.get(lf['schritt'], lf['schritt'])}")
    cur = db.execute("INSERT INTO jobs (schritt, args, status, gestartet) VALUES (?,?,?,?)",
                     (schritt, json.dumps(args or {}, ensure_ascii=False), "wartet", time.time()))
    jid = cur.lastrowid
    db.commit()
    if im_prozess:
        fuehre_aus(sammlung.wurzel, jid, sammlung=sammlung)
        return jid
    logdatei = sammlung.datenordner / "jobs" / f"job_{jid}.log"
    logdatei.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    paket = str(Path(__file__).resolve().parent.parent)
    env["PYTHONPATH"] = paket + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    with open(logdatei, "ab") as lf:
        p = subprocess.Popen([sys.executable, "-m", "bildarchiv", "job", sammlung.wurzel, str(jid)],
                             stdin=subprocess.DEVNULL, stdout=lf, stderr=subprocess.STDOUT,
                             start_new_session=True, env=env)
    db.execute("UPDATE jobs SET pid=? WHERE id=?", (p.pid, jid))
    db.commit()
    return jid


def abbrechen(db, jid):
    j = job(db, jid)
    if j and j["schritt"] in ablauf.OHNE_ABBRUCH:
        raise JobFehler("Dieser Schritt lässt sich nicht mittendrin abbrechen.")
    db.execute("UPDATE jobs SET status='abbrechen' WHERE id=? AND status IN ('wartet','laeuft')", (jid,))
    db.commit()


def fuehre_aus(wurzel, jid, sammlung=None):
    """Im Job-Prozess: Schritt ausführen und Status festhalten."""
    from .sammlung import Sammlung
    s = sammlung or Sammlung(wurzel)
    db = s.db
    j = job(db, jid)
    db.execute("UPDATE jobs SET status='laeuft', pid=?, gestartet=? WHERE id=?", (os.getpid(), time.time(), jid))
    db.commit()
    r = DbReporter(db, jid)
    if j["schritt"] in ablauf.OHNE_ABBRUCH:
        r.pruefe_abbruch = lambda: None
    try:
        erg = ablauf.SCHRITTE[j["schritt"]](s, r, j["args"])
        status, ergebnis = "fertig", erg
    except Abbruch:
        status, ergebnis = "abgebrochen", None
        r.meldung("Abgebrochen.", "warnung")
    except ablauf.SchrittFehler as err:
        status, ergebnis = "fehler", {"fehler": str(err)}
        r.meldung(str(err), "fehler")
    except Exception as err:
        status, ergebnis = "fehler", {"fehler": f"{type(err).__name__}: {err}", "trace": traceback.format_exc()}
        r.meldung(f"Unerwarteter Fehler: {type(err).__name__}: {err}", "fehler")
        traceback.print_exc()
    try:
        db.rollback()
    except Exception:
        pass
    db.execute("UPDATE jobs SET status=?, beendet=?, ergebnis=? WHERE id=?",
               (status, time.time(), json.dumps(ergebnis, ensure_ascii=False, default=str), jid))
    db.commit()
    return status
