"""Die Arbeitsschritte, wie Menü und Kommandozeile sie aufrufen."""

import os
import time

from . import einstellungen as es
from .kern import analyse as kanalyse
from .kern import ausfuehren as kausf
from .kern import db as kdb
from .kern import exif, inventar
from .kern.klassifikation import Klassifikator
from .kern.planung import erstelle_plan


class SchrittFehler(Exception):
    pass


def pruefe_umgebung():
    probleme = []
    if not exif.vorhanden():
        probleme.append("exiftool fehlt (Debian/Ubuntu: sudo apt install libimage-exiftool-perl)")
    for mod, paket in (("PIL", "Pillow"), ("numpy", "numpy")):
        try:
            __import__(mod)
        except ImportError:
            probleme.append(f"Python-Paket {paket} fehlt")
    return probleme


def _klassifikator(s):
    k = s.einstellungen["klassifikation"]
    return Klassifikator(k["sidecars_plus"], k["muell_plus"])


def analyse(s, r, jobs=6, voll=False):
    """Sammlung einlesen und Befund erstellen. Beim ersten Mal werden die Vorschläge übernommen."""
    inventar.scan(s.db, s.wurzel, "sammlung", _klassifikator(s), r, jobs, voll)
    kdb.meta_set(s.db, "scan_sammlung", str(time.time()))
    r.phase("Befund erstellen")
    befund = kanalyse.analysiere(s)
    if not s.festgeschrieben and not kdb.meta_get(s.db, "einstellungen_geaendert") and befund["vorschlaege"]:
        # in Runden: erst mit dem erkannten Jahresordner-Muster sind die Ordnervorschläge richtig
        n = 0
        for _ in range(3):
            if not befund["vorschlaege"]:
                break
            n += len(befund["vorschlaege"])
            kanalyse.uebernehmen(s.einstellungen, befund["vorschlaege"])
            befund = kanalyse.analysiere(s)
        s.speichere_einstellungen()
        r.meldung(f"{n} Vorschläge für die Einstellungen übernommen")
    kdb.meta_set_json(s.db, "befund", befund)
    kdb.meta_set(s.db, "befund_zeit", str(time.time()))
    s.db.commit()
    return befund


def _einstellungen_ok(s):
    m = es.pruefe(s.einstellungen)
    fehler = [f"{b}: {x.text}" for b, x in m if x.stufe == "fehler"]
    if fehler:
        raise SchrittFehler("Die Einstellungen enthalten Fehler:\n" + "\n".join(fehler))


def plan_ordnen(s, r, jobs=6):
    if not kdb.meta_get(s.db, "scan_sammlung"):
        raise SchrittFehler("Zuerst die Analyse ausführen.")
    _einstellungen_ok(s)
    return erstelle_plan(s, r, modus="ordnen", jobs=jobs)


def import_lesen(s, quelle, r, jobs=6):
    """Quellordner einlesen (Bereich 'import')."""
    quelle = os.path.abspath(quelle)
    if not os.path.isdir(quelle):
        raise SchrittFehler(f"Quellordner nicht gefunden: {quelle}")
    if os.path.commonpath([quelle, s.wurzel]) in (s.wurzel, quelle):
        raise SchrittFehler("Quelle und Sammlung dürfen nicht ineinander liegen.")
    if kdb.meta_get(s.db, "import_quelle") != quelle:
        s.db.execute("DELETE FROM dateien WHERE bereich='import'")
        kdb.meta_set(s.db, "import_quelle", quelle)
        s.db.commit()
    inventar.scan(s.db, quelle, "import", _klassifikator(s), r, jobs)
    kdb.meta_set(s.db, "scan_import", str(time.time()))
    s.db.commit()
    return kanalyse.analysiere(s, "import")


def plan_import(s, quelle, r, jobs=6, import_modus="kopieren", ordner=None, ereignisnamen=None):
    if not s.festgeschrieben:
        raise SchrittFehler("Die Struktur der Sammlung ist noch nicht festgelegt. Zuerst die Sammlung ordnen "
                            "(oder die Einstellungen festschreiben).")
    if not kdb.meta_get(s.db, "scan_sammlung"):
        raise SchrittFehler("Die Sammlung wurde noch nicht eingelesen.")
    _einstellungen_ok(s)
    befund = import_lesen(s, quelle, r, jobs)
    kdb.meta_set_json(s.db, "import_befund", befund)
    kdb.meta_set(s.db, "import_modus", import_modus)
    s.db.commit()
    return erstelle_plan(s, r, modus="import", quelle=quelle, import_modus=import_modus, jobs=jobs,
                         import_ordner=ordner, import_ereignisnamen=ereignisnamen)


def ausfuehren(s, r, jobs=6):
    try:
        return kausf.ausfuehren(s, r, jobs)
    except kausf.AusfuehrFehler as err:
        raise SchrittFehler("Der Plan kann nicht ausgeführt werden:\n" + "\n".join(err.probleme[:30])
                            + (f"\n… und {len(err.probleme) - 30} weitere" if len(err.probleme) > 30 else ""))


def rueckgaengig(s, journal, r):
    try:
        return kausf.rueckgaengig(s, journal, r)
    except kausf.AusfuehrFehler as err:
        raise SchrittFehler("\n".join(err.probleme))


def altdaten(s, pfad, r):
    """Hashes und Entscheidungen aus der Datenbank einer Vorgängerversion übernehmen."""
    from .kern import altdaten as kalt
    try:
        return kalt.uebernehmen(s, pfad, r)
    except (ValueError, OSError) as err:
        raise SchrittFehler(str(err))


def festschreiben(s):
    """Einstellungen festschreiben, ohne die Sammlung umzuordnen (für bereits geordnete Sammlungen)."""
    _einstellungen_ok(s)
    s.festschreiben()


SCHRITTE = {
    "analyse": lambda s, r, a: analyse(s, r, a.get("jobs", 6), a.get("voll", False)),
    "plan": lambda s, r, a: plan_ordnen(s, r, a.get("jobs", 6)),
    "import_lesen": lambda s, r, a: import_lesen(s, a["quelle"], r, a.get("jobs", 6)),
    "import_plan": lambda s, r, a: plan_import(s, a["quelle"], r, a.get("jobs", 6), a.get("import_modus", "kopieren"),
                                               a.get("ordner"), a.get("ereignisnamen")),
    "ausfuehren": lambda s, r, a: ausfuehren(s, r, a.get("jobs", 6)),
    "rueckgaengig": lambda s, r, a: rueckgaengig(s, a["journal"], r),
    "altdaten": lambda s, r, a: altdaten(s, a["pfad"], r),
}
NAMEN = {"analyse": "Analyse", "plan": "Plan erstellen", "import_lesen": "Quelle einlesen",
         "import_plan": "Import planen", "ausfuehren": "Ausführen", "rueckgaengig": "Rückgängig machen",
         "altdaten": "Daten einer Vorgängerversion übernehmen"}
# Schritte, die nicht mittendrin abgebrochen werden dürfen
OHNE_ABBRUCH = {"ausfuehren", "rueckgaengig"}
