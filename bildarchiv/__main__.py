"""bildarchiv – Bildsammlungen ordnen, Dubletten finden, eindeutig benennen.

    bildarchiv [ORDNER]                         Menü öffnen
    bildarchiv analyse ORDNER [--voll]          Sammlung einlesen, Befund zeigen
    bildarchiv plan ORDNER                      Dry-Run: Plan und Berichte erstellen
    bildarchiv ausfuehren ORDNER --ja           Plan ausführen
    bildarchiv import ORDNER QUELLE [--verschieben] [--ausfuehren]
    bildarchiv rueckgaengig ORDNER [JOURNAL]    ohne JOURNAL: Liste der Journale
    bildarchiv einstellungen ORDNER [--setze gruppe.schluessel=wert …] [--festschreiben]
    bildarchiv vorlage "VORLAGE" [--zaehler jahr] [--struktur jahr] [--kuerzel AX]
    bildarchiv altdaten ORDNER ALTE_DB          Hashes einer Vorgängerversion übernehmen
"""

import argparse
import json
import sys


def _sammlung(pfad):
    from .sammlung import Sammlung
    try:
        return Sammlung(pfad)
    except FileNotFoundError as err:
        sys.exit(str(err))


def _umgebung():
    from .ablauf import pruefe_umgebung
    p = pruefe_umgebung()
    if p:
        sys.exit("\n".join(p))


def _laufen(fn, *args):
    from .ablauf import SchrittFehler
    from .reporter import KonsolenReporter
    try:
        return fn(*args[:1], KonsolenReporter(), *args[1:])
    except SchrittFehler as err:
        sys.exit(str(err))


def cmd_analyse(a):
    from . import ablauf
    s = _sammlung(a.ordner)
    b = _laufen(lambda s, r: ablauf.analyse(s, r, a.jobs, a.voll), s)
    from .menue.texte import befund_text
    print()
    print("\n".join(befund_text(b, s.einstellungen)))


def cmd_plan(a):
    from . import ablauf
    s = _sammlung(a.ordner)
    erg = _laufen(lambda s, r: ablauf.plan_ordnen(s, r, a.jobs), s)
    print(f"\nBerichte: {erg['berichte']}")


def cmd_ausfuehren(a):
    from . import ablauf
    s = _sammlung(a.ordner)
    if not a.ja:
        sys.exit("Sicherheitsstopp: zum Ausführen --ja angeben.")
    _laufen(lambda s, r: ablauf.ausfuehren(s, r, a.jobs), s)


def cmd_import(a):
    from . import ablauf
    s = _sammlung(a.ordner)
    modus = "verschieben" if a.verschieben else "kopieren"
    erg = _laufen(lambda s, r: ablauf.plan_import(s, a.quelle, r, a.jobs, modus), s)
    print(f"\nBerichte: {erg['berichte']}")
    if a.ausfuehren:
        _laufen(lambda s, r: ablauf.ausfuehren(s, r, a.jobs), s)
    else:
        print("Dry-Run. Zum Importieren: --ausfuehren")


def cmd_rueckgaengig(a):
    from . import ablauf
    from .kern import ausfuehren as ka
    s = _sammlung(a.ordner)
    if not a.journal:
        for p in s.journale():
            i = ka.journal_info(p)
            print(f"{p}  ({i['ok']}/{i['anzahl']} Aktionen)")
        return
    _laufen(lambda s, r: ablauf.rueckgaengig(s, a.journal, r), s)


def cmd_einstellungen(a):
    from . import einstellungen as es
    s = _sammlung(a.ordner)
    for z in a.setze or []:
        key, _, wert = z.partition("=")
        g, _, k = key.partition(".")
        if g not in s.einstellungen or k not in s.einstellungen[g]:
            sys.exit(f"Unbekannte Einstellung: {key}")
        alt = s.einstellungen[g][k]
        if isinstance(alt, bool):
            wert = wert.lower() in ("1", "ja", "true", "wahr")
        elif isinstance(alt, (list, dict)):
            wert = json.loads(wert)
        if s.festgeschrieben and (g, k) in (("struktur", "art"), ("namen", "zaehler")):
            sys.exit("Die Struktur ist festgeschrieben und lässt sich nicht mehr ändern.")
        if (g, k) == ("namen", "vorlage"):
            es.vorlage_aendern(s.einstellungen, wert)
        else:
            s.einstellungen[g][k] = wert
    if a.setze:
        s.speichere_einstellungen()
    if a.festschreiben:
        from .ablauf import festschreiben
        festschreiben(s)
    print(es.toml(s.einstellungen))
    for bereich, m in es.pruefe(s.einstellungen):
        print(f"{m.stufe.upper()}: {bereich}: {m.text}")


def cmd_vorlage(a):
    from .kern.vorlage import Vorlage, pruefe
    for m in pruefe(a.vorlage, a.zaehler, a.struktur, a.kuerzel):
        print(f"{m.stufe.upper()}: {m.text}")
    try:
        v = Vorlage(a.vorlage)
    except ValueError:
        sys.exit(1)
    beispiele = [
        dict(datum="20190714", zeit="153012", altname="IMG_4711", kamera="Canon EOS 5D", ereignis="Hochzeit Müller"),
        dict(datum="20190700", zeit="000000", altname="scan_ohne_exif", ereignis=""),
    ]
    for i, b in enumerate(beispiele, 1):
        w = dict(lauf="20261015", kuerzel=a.kuerzel, nr=122 + i, hash="a1b2c3d4", **b)
        print(f"{b['altname']:20} → {v.render(w)}")


def cmd_altdaten(a):
    from . import ablauf
    s = _sammlung(a.ordner)
    _laufen(lambda s, r: ablauf.altdaten(s, a.alte_db, r), s)


def cmd_job(a):
    from .jobs import fuehre_aus
    fuehre_aus(a.ordner, int(a.job))


def cmd_menue(ordner):
    _umgebung()
    from .menue.app import starte
    starte(ordner)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    befehle = {"analyse", "plan", "ausfuehren", "import", "rueckgaengig", "einstellungen", "vorlage", "job",
               "altdaten"}
    if not argv or (argv[0] not in befehle and not argv[0].startswith("-")):
        return cmd_menue(argv[0] if argv else None)
    ap = argparse.ArgumentParser(prog="bildarchiv", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--jobs", type=int, default=6, help="parallele Prozesse (Vorgabe 6)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("analyse")
    s.add_argument("ordner")
    s.add_argument("--voll", action="store_true", help="alle Metadaten neu lesen")
    s = sub.add_parser("plan")
    s.add_argument("ordner")
    s = sub.add_parser("ausfuehren")
    s.add_argument("ordner")
    s.add_argument("--ja", action="store_true")
    s = sub.add_parser("import")
    s.add_argument("ordner")
    s.add_argument("quelle")
    s.add_argument("--verschieben", action="store_true", help="statt kopieren")
    s.add_argument("--ausfuehren", action="store_true")
    s = sub.add_parser("rueckgaengig")
    s.add_argument("ordner")
    s.add_argument("journal", nargs="?")
    s = sub.add_parser("einstellungen")
    s.add_argument("ordner")
    s.add_argument("--setze", nargs="*")
    s.add_argument("--festschreiben", action="store_true")
    s = sub.add_parser("vorlage")
    s.add_argument("vorlage")
    s.add_argument("--zaehler", default="jahr")
    s.add_argument("--struktur", default="jahr")
    s.add_argument("--kuerzel", default="AX")
    s = sub.add_parser("altdaten")
    s.add_argument("ordner")
    s.add_argument("alte_db")
    s = sub.add_parser("job")
    s.add_argument("ordner")
    s.add_argument("job")
    a = ap.parse_args(argv)
    if a.cmd not in ("vorlage", "job", "einstellungen"):
        _umgebung()
    {"analyse": cmd_analyse, "plan": cmd_plan, "ausfuehren": cmd_ausfuehren, "import": cmd_import,
     "rueckgaengig": cmd_rueckgaengig, "einstellungen": cmd_einstellungen, "vorlage": cmd_vorlage,
     "job": cmd_job, "altdaten": cmd_altdaten}[a.cmd](a)


if __name__ == "__main__":
    main()
