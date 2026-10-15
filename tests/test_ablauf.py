"""Ende-zu-Ende: Testsammlung ordnen, erneut prüfen, importieren, rückgängig machen."""

import json
import os
import subprocess

import pytest

from bildarchiv import ablauf
from bildarchiv.kern import db as kdb
from bildarchiv.reporter import Reporter
from bildarchiv.sammlung import Sammlung

from testsammlung import baue_quelle, baue_sammlung

R = Reporter()


@pytest.fixture
def umgebung(tmp_path, monkeypatch):
    monkeypatch.setenv("BILDARCHIV_DATEN", str(tmp_path / "daten"))
    monkeypatch.setenv("BILDARCHIV_KONFIG", str(tmp_path / "konfig"))
    root = tmp_path / "Sammlung"
    baue_sammlung(str(root))
    return tmp_path, root


def dateien(root):
    out = set()
    for d, _, fs in os.walk(root):
        for f in fs:
            rel = os.path.relpath(os.path.join(d, f), root)
            if not rel.startswith("_bildarchiv"):
                out.add(rel)
    return out


def tags(pfad, *namen):
    p = subprocess.run(["exiftool", "-j", "-q", "-q", *[f"-{n}" for n in namen], str(pfad)],
                       capture_output=True, text=True)
    return json.loads(p.stdout)[0]


def plan(s):
    return [(a, q, z) for a, q, z in s.db.execute("SELECT aktion, quelle, ziel FROM plan ORDER BY id")]


def test_ordnen_import_rueckgaengig(umgebung):
    tmp, root = umgebung
    vorher = dateien(root)
    s = Sammlung(root)

    # ---------------------------------------------------------------- Analyse
    b = ablauf.analyse(s, R, jobs=2)
    e = s.einstellungen
    assert e["struktur"]["jahresordner"] == "{jahr} BILDER"
    assert e["namen"]["kuerzel"] == "AB"                          # aus dem Altbestand übernommen
    assert e["struktur"]["ordner"].get("Apple Export") == "aufloesen"
    assert b["muell"] == 3 and b["live_photos"] == 1 and b["abweichung"] == 1

    # ---------------------------------------------------------------- Plan + Ausführen
    erg = ablauf.plan_ordnen(s, R, jobs=2)
    assert erg["anzahl"]["loeschen"] == 3
    p = plan(s)
    ziele = {q: z for a, q, z in p}
    # RAW/JPEG/XMP teilen einen Namen
    n1 = ziele["2019 BILDER/Hochzeit Meier/IMG_0001.jpg"]
    assert n1 == "2019 BILDER/20190714_153012_AB" + s.db.execute(
        "SELECT value FROM meta WHERE key='plan_lauf'").fetchone()[0] + "_00001.jpg"
    assert ziele["2019 BILDER/Hochzeit Meier/IMG_0001.dng"] == n1[:-4] + ".dng"
    assert ziele["2019 BILDER/Hochzeit Meier/IMG_0001.xmp"] == n1[:-4] + ".xmp"
    # Serienbilder derselben Sekunde bleiben getrennt
    assert ziele["2019 BILDER/Hochzeit Meier/IMG_0004.jpg"] != ziele["2019 BILDER/Hochzeit Meier/IMG_0005.jpg"]
    # falscher Jahresordner, Export aufgelöst, Live-Photo zusammen
    assert ziele["2019 BILDER/Urlaub/IMG_0100.jpg"].startswith("2018 BILDER/20180801_100000_")
    assert ziele["Apple Export/IMG_5000.jpg"].startswith("2016 BILDER/")
    assert ziele["Apple Export/IMG_5003.mov"][:-4] == ziele["Apple Export/IMG_5003.jpg"][:-4]
    # Dubletten: Web-Fassung (kleiner) und bitgleiche Kopie
    dup = {q for a, q, z in p if a == "dublette"}
    assert "2019 BILDER/Hochzeit Meier/Web/IMG_0002_web.jpg" in dup
    assert len({"2019 BILDER/Hochzeit Meier/IMG_0003.jpg", "2019 BILDER/Kopie/IMG_0003.jpg"} & dup) == 1
    assert any(a == "caption" for a, q, z in p)
    # Altbestand bleibt
    assert not any(q.startswith("2017 BILDER/") for a, q, z in p)

    erg = ablauf.ausfuehren(s, R, jobs=2)
    assert not erg["fehler"]
    nachher = dateien(root)
    assert "2017 BILDER/20170101_120000_AB20260929_00007.jpg" in nachher
    assert not any(f.endswith(("Thumbs.db", ".DS_Store")) or "/._" in f for f in nachher)
    assert "_Sonstiges/Ablauf.pdf" in nachher
    assert any(f.startswith("_Dubletten/D00001_") for f in nachher)
    # flach: nur Jahresordner + Sonderordner an der Wurzel, keine Unterordner in Jahresordnern
    assert all(f.count("/") == 1 for f in nachher)
    t = tags(root / n1, "PreservedFileName", "Event", "ImageUniqueID")
    assert t["PreservedFileName"] == "IMG_0001.jpg" and t["Event"] == "2019 BILDER/Hochzeit Meier"
    assert t["ImageUniqueID"] == os.path.basename(n1)[:-4]
    keeper2 = ziele["2019 BILDER/Hochzeit Meier/IMG_0002.jpg"]
    assert tags(root / keeper2, "Caption-Abstract")["Caption-Abstract"] == "Brautpaar vor der Kirche"
    # nicht beschreibbare BMP -> Sidecar
    bmp = ziele["2020 BILDER/Diverses/grafik.bmp"]
    assert bmp + ".xmp" in nachher
    assert s.festgeschrieben and (root / "_bildarchiv" / "einstellungen.toml").exists()

    # ---------------------------------------------------------------- zweiter Lauf: nichts zu tun
    ablauf.analyse(s, R, jobs=2)
    erg = ablauf.plan_ordnen(s, R, jobs=2)
    assert erg["anzahl"] == {}, plan(s)

    # ---------------------------------------------------------------- Import
    quelle = tmp / "Quelle"
    baue_quelle(str(quelle), str(root))
    q_vorher = dateien(quelle)
    erg = ablauf.plan_import(s, str(quelle), R, jobs=2)
    p = plan(s)
    ziele = {q: z for a, q, z in p if a == "importieren"}
    assert "Hochzeit Kopie/IMG_0004.jpg" not in ziele                           # schon vorhanden
    assert ziele["Hochzeit Kopie/IMG_0004.dng"][:-4] == ziele.get("Hochzeit Kopie/IMG_0004.dng")[:-4]
    vorhanden4 = [f for f in nachher if f.startswith("2019 BILDER/20190714_180000_") and f.endswith(".jpg")]
    stems4 = {f[:-4] for f in vorhanden4}
    assert ziele["Hochzeit Kopie/IMG_0004.dng"][:-4] in stems4                  # RAW schließt an
    assert ziele["Hochzeit Kopie/IMG_0005.jpg"] in vorhanden4                   # ersetzt die Fassung ohne Caption
    assert ziele["Geburtstag Oma/DSC_9100.jpg"].startswith("2022 BILDER/") and \
        ziele["Geburtstag Oma/DSC_9100.jpg"].endswith("_00001.jpg")
    nrs = sorted(int(z[-9:-4]) for q, z in ziele.items() if q.startswith("Geburtstag Oma/DSC_900"))
    assert nrs == [6, 7, 8]                                                     # Zähler läuft weiter
    ablauf.ausfuehren(s, R, jobs=2)
    assert dateien(quelle) == q_vorher                                          # kopiert, Quelle unverändert
    assert tags(root / ziele["Hochzeit Kopie/IMG_0005.jpg"], "Caption-Abstract")["Caption-Abstract"] == "Tanz"
    assert any(f.startswith("_Dubletten/D00003_IMG_0005") for f in dateien(root))

    # ---------------------------------------------------------------- Rückgängig (Import, dann Ordnen)
    journale = s.journale()
    assert len(journale) == 2 and "_import" in journale[0].name
    ablauf.rueckgaengig(s, journale[0], R)
    assert dateien(root) == nachher
    with pytest.raises(ablauf.SchrittFehler):
        ablauf.rueckgaengig(s, journale[0], R)
    ablauf.rueckgaengig(s, journale[1], R)
    muell = {"2019 BILDER/Thumbs.db", "2019 BILDER/Hochzeit Meier/._IMG_0001.jpg", ".DS_Store"}
    assert dateien(root) == vorher - muell
    assert not s.festgeschrieben
    assert kdb.zaehler_alle(s.db) == {}


def test_import_braucht_festgelegte_struktur(umgebung):
    tmp, root = umgebung
    s = Sammlung(root)
    ablauf.analyse(s, R, jobs=2)
    with pytest.raises(ablauf.SchrittFehler):
        ablauf.plan_import(s, str(tmp), R, jobs=2)


def test_ereignisstruktur(umgebung):
    tmp, root = umgebung
    s = Sammlung(root)
    ablauf.analyse(s, R, jobs=2)
    s.einstellungen["struktur"]["art"] = "jahr_ereignis"
    s.speichere_einstellungen()
    ablauf.plan_ordnen(s, R, jobs=2)
    ziele = {q: z for a, q, z in plan(s)}
    assert ziele["2019 BILDER/Hochzeit Meier/IMG_0001.jpg"].startswith("2019 BILDER/2019-07-14 Hochzeit Meier/")
    assert ziele["2020 BILDER/Scans 2020-05-01/scan001.tif"].startswith("2020 BILDER/2020-05-01 Scans/")
    # Export-Ordner ist auf 'auflösen' gesetzt: direkt in die Jahresordner
    assert ziele["Apple Export/IMG_5000.jpg"].count("/") == 1
    ablauf.ausfuehren(s, R, jobs=2)
    ablauf.analyse(s, R, jobs=2)
    assert ablauf.plan_ordnen(s, R, jobs=2)["anzahl"] == {}


def test_monatsstruktur_und_kurze_vorlage(umgebung):
    tmp, root = umgebung
    s = Sammlung(root)
    ablauf.analyse(s, R, jobs=2)
    e = s.einstellungen
    e["struktur"]["art"] = "jahr_monat"
    e["namen"]["vorlage"] = "{datum:JJJJ-MM-TT}_{zeit}_{nr:3}"
    e["namen"]["zaehler"] = "tag"
    s.speichere_einstellungen()
    ablauf.plan_ordnen(s, R, jobs=2)
    ziele = {q: z for a, q, z in plan(s)}
    assert ziele["2019 BILDER/Hochzeit Meier/IMG_0001.jpg"] == "2019 BILDER/2019-07/2019-07-14_153012_001.jpg"
    assert ziele["2019 BILDER/Hochzeit Meier/IMG_0002.jpg"] == "2019 BILDER/2019-07/2019-07-14_160000_002.jpg"
    # ohne bekannten Monat direkt in den Jahresordner
    assert ziele["2020 BILDER/Diverses/grafik.bmp"].count("/") == 1


def test_fehlerhafte_vorlage_verhindert_plan(umgebung):
    tmp, root = umgebung
    s = Sammlung(root)
    ablauf.analyse(s, R, jobs=2)
    s.einstellungen["namen"]["vorlage"] = "{datum}_{zeit}"
    s.speichere_einstellungen()
    with pytest.raises(ablauf.SchrittFehler):
        ablauf.plan_ordnen(s, R, jobs=2)


def test_entscheidungen_dubletten(umgebung):
    tmp, root = umgebung
    s = Sammlung(root)
    ablauf.analyse(s, R, jobs=2)
    ablauf.plan_ordnen(s, R, jobs=2)
    # die Web-Fassung soll bleiben, die bitgleiche Kopie ist keine Dublette
    s.db.execute("INSERT INTO entscheidungen VALUES ('sammlung', '2019 BILDER/Hochzeit Meier/Web/IMG_0002_web.jpg', "
                 "'bevorzugt')")
    s.db.execute("INSERT INTO entscheidungen VALUES ('sammlung', '2019 BILDER/Kopie/IMG_0003.jpg', 'keine_dublette')")
    s.db.commit()
    ablauf.plan_ordnen(s, R, jobs=2)
    dup = {q for a, q, z in plan(s) if a == "dublette"}
    assert "2019 BILDER/Hochzeit Meier/IMG_0002.jpg" in dup
    assert not any("IMG_0003" in q for q in dup)


def test_einstellungen_geaendert_nach_plan(umgebung):
    tmp, root = umgebung
    s = Sammlung(root)
    ablauf.analyse(s, R, jobs=2)
    ablauf.plan_ordnen(s, R, jobs=2)
    s.einstellungen["namen"]["kuerzel"] = "XY"
    s.speichere_einstellungen()
    with pytest.raises(ablauf.SchrittFehler):
        ablauf.ausfuehren(s, R, jobs=2)
