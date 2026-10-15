import os
import sqlite3

from bildarchiv import ablauf
from bildarchiv.kern import db as kdb
from bildarchiv.reporter import Reporter
from bildarchiv.sammlung import Sammlung

from testsammlung import bild

R = Reporter()


def alte_db(pfad, wurzel, zeilen):
    db = sqlite3.connect(pfad)
    db.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    db.execute("INSERT INTO meta VALUES ('archiv', ?)", (wurzel,))
    db.execute("CREATE TABLE files (path TEXT PRIMARY KEY, size INT, mtime REAL, kind TEXT, ext TEXT, dto TEXT, "
               "subsec TEXT, createdate TEXT, model TEXT, serial TEXT, width INT, height INT, orientation INT, "
               "datahash TEXT, dhash TEXT, aspect REAL, caption INT, thumb BLOB, role TEXT, orig_path TEXT)")
    db.executemany("INSERT INTO files (path, size, kind, datahash, dhash, aspect, role) VALUES (?,?,?,?,?,?,?)", zeilen)
    db.commit()
    db.close()


def test_altdaten(tmp_path, monkeypatch):
    monkeypatch.setenv("BILDARCHIV_DATEN", str(tmp_path / "daten"))
    monkeypatch.setenv("BILDARCHIV_KONFIG", str(tmp_path / "konfig"))
    root = tmp_path / "Archiv"
    a = root / "2019 BILDER" / "20190714_153012_AB20260929_00001.jpg"
    b = root / "2024 Serie re-Edits" / "x.jpg"
    c = root / "_Dubletten" / "D00042_IMG_1.jpg"
    for i, p in enumerate((a, b, c)):
        bild(str(p), i)
    s = Sammlung(root)
    ablauf.analyse(s, R, jobs=2)
    rel = lambda p: os.path.relpath(p, root)
    alte_db(tmp_path / "archiv.db", str(root), [
        (rel(a), os.path.getsize(a), "bild", "hash-a", "00ff00ff00ff00ff", 1.33, "master"),
        (rel(b), os.path.getsize(b), "bild", "hash-b", "", 1.33, "reedit"),
        (rel(c), 999, "bild", "hash-c", "", 1.33, "dublette"),          # Größe passt nicht: nicht übernehmen
    ])
    assert ablauf.altdaten(s, str(tmp_path / "archiv.db"), R)["uebernommen"] == 2
    h = dict(s.db.execute("SELECT pfad, datahash FROM dateien WHERE bereich='sammlung'"))
    assert h[rel(a)] == "hash-a" and h[rel(b)] == "hash-b" and h[rel(c)] is None
    assert s.einstellungen["struktur"]["ordner"]["2024 Serie re-Edits"] == "behalten"
    assert s.einstellungen["dubletten"]["ablage"] == "flach_praefix"
    assert kdb.zaehler_alle(s.db)["dubletten"] == 42


def test_jahresordner_mit_vielen_jahren_bleiben_jahresordner(tmp_path, monkeypatch):
    """Ein Jahresordner mit Aufnahmen aus vielen Jahren darf nicht als Sammelordner 'unverändert' werden."""
    from testsammlung import exif_setzen
    monkeypatch.setenv("BILDARCHIV_DATEN", str(tmp_path / "daten"))
    monkeypatch.setenv("BILDARCHIV_KONFIG", str(tmp_path / "konfig"))
    root = tmp_path / "Archiv"
    ex = []
    for i, (ordner, jahr) in enumerate([("20051231 FOTOS ARCHIV 2005", 2003), ("20051231 FOTOS ARCHIV 2005", 2004),
                                        ("20051231 FOTOS ARCHIV 2005", 2005), ("20061231 FOTOS ARCHIV 2006", 2006),
                                        ("2006 Portfolio", 2001), ("2006 Portfolio", 2003), ("2006 Portfolio", 2006)]):
        p = root / ordner / f"IMG_{i}.jpg"
        bild(str(p), i)
        ex.append((str(p), {"DateTimeOriginal": f"{jahr}:05:01 10:00:00"}))
    exif_setzen(ex)
    s = Sammlung(root)
    ablauf.analyse(s, R, jobs=2)
    ordner = s.einstellungen["struktur"]["ordner"]
    assert s.einstellungen["struktur"]["jahresordner"] == "{jahr}1231 FOTOS ARCHIV {jahr}"
    assert not any("FOTOS ARCHIV" in k for k in ordner), ordner
    assert ordner.get("2006 Portfolio") == "behalten"


def test_altbestand_mit_fremder_kamera_id(tmp_path, monkeypatch):
    """AB-Namen gelten auch mit abweichender ImageUniqueID als benannt; einfache Vorlagen brauchen die passende ID."""
    from testsammlung import exif_setzen
    monkeypatch.setenv("BILDARCHIV_DATEN", str(tmp_path / "daten"))
    monkeypatch.setenv("BILDARCHIV_KONFIG", str(tmp_path / "konfig"))
    root = tmp_path / "S"
    a = root / "2019" / "20190714_153012_AB20260929_00005.jpg"
    b = root / "2019" / "20190714_0007.jpg"
    bild(str(a), 1)
    bild(str(b), 2)
    exif_setzen([(str(a), {"DateTimeOriginal": "2019:07:14 15:30:12", "EXIF:ImageUniqueID": "KAMERA123"}),
                 (str(b), {"DateTimeOriginal": "2019:07:14 16:00:00", "EXIF:ImageUniqueID": "KAMERA456"})])
    s = Sammlung(root)
    ablauf.analyse(s, R, jobs=2)
    s.einstellungen["namen"]["vorlage"] = "{datum}_{nr:4}"
    s.einstellungen["namen"]["zaehler"] = "jahr"
    s.speichere_einstellungen()
    ablauf.plan_ordnen(s, R, jobs=2)
    quellen = {q for (q,) in s.db.execute("SELECT quelle FROM plan WHERE aktion='umbenennen'")}
    assert quellen == {"2019/20190714_0007.jpg"}
    # der AB-Altbestand zählt mit: die neue Nummer in 2019 ist 6
    ziel = s.db.execute("SELECT ziel FROM plan WHERE aktion='umbenennen'").fetchone()[0]
    assert ziel == "2019/20190714_0006.jpg"


def test_altdaten_nach_ordnerumbenennung(tmp_path, monkeypatch):
    """Ordner nach dem alten Lauf umbenannt: Zuordnung über eindeutigen Dateinamen + Größe."""
    monkeypatch.setenv("BILDARCHIV_DATEN", str(tmp_path / "daten"))
    monkeypatch.setenv("BILDARCHIV_KONFIG", str(tmp_path / "konfig"))
    root = tmp_path / "Archiv"
    a = root / "20191231 FOTOS AUSWAHL 2019" / "20190714_153012_AB20260929_00001.jpg"
    bild(str(a), 1)
    s = Sammlung(root)
    ablauf.analyse(s, R, jobs=2)
    alte_db(tmp_path / "archiv.db", str(root), [
        ("20191231 FOTOS ARCHIV 2019/20190714_153012_AB20260929_00001.jpg", os.path.getsize(a), "bild",
         "hash-a", "", 1.33, "master"),
        ("2006 Portfolio/x.jpg", 1, "bild", "hash-x", "", 1.0, "reedit"),     # gibt es nicht mehr
    ])
    assert ablauf.altdaten(s, str(tmp_path / "archiv.db"), R)["uebernommen"] == 1
    assert s.db.execute("SELECT datahash FROM dateien WHERE pfad LIKE '%00001.jpg'").fetchone()[0] == "hash-a"
    assert "2006 Portfolio" not in s.einstellungen["struktur"]["ordner"]


def test_fremdes_schema_bleibt_und_muell_bleibt_liegen(tmp_path, monkeypatch):
    """Frühere Vorlage mit festem Kürzel: Namen bleiben, ihre Nummern treiben den Zähler nicht hoch."""
    from testsammlung import exif_setzen, datei
    monkeypatch.setenv("BILDARCHIV_DATEN", str(tmp_path / "daten"))
    monkeypatch.setenv("BILDARCHIV_KONFIG", str(tmp_path / "konfig"))
    root = tmp_path / "S"
    j = root / "20171231 FOTOS AUSWAHL 2017"
    a, b, c = j / "20171217_000000_AB22160115421.jpg", j / "1998_000000_AB1950592677.jpg", j / "IMG_1.jpg"
    for i, p in enumerate((a, b, c)):
        bild(str(p), i)
    datei(str(j / "._IMG_1.jpg"))
    exif_setzen([(str(a), {"DateTimeOriginal": "2017:12:17 19:00:07", "EXIF:ImageUniqueID": "fremd"}),
                 (str(b), {"DateTimeOriginal": "2017:01:01 10:00:00"}),
                 (str(c), {"DateTimeOriginal": "2017:05:05 10:00:00"})])
    s = Sammlung(root)
    ablauf.analyse(s, R, jobs=2)
    e = s.einstellungen
    e["namen"]["frueher"] += ["{datum}_{zeit}_AB{nr:9}", "{jahr}_{zeit}_AB{nr:9}"]
    e["struktur"]["jahresordner"] = "{jahr}1231 FOTOS AUSWAHL {jahr}"
    e["struktur"]["jahresordner_muster"] = r"^\d{8} FOTOS AUSWAHL (?P<jahr>(?:19|20)\d{2})$"
    e["struktur"]["muell"] = "behalten"
    s.speichere_einstellungen()
    ablauf.plan_ordnen(s, R, jobs=2)
    p = s.db.execute("SELECT aktion, quelle, ziel FROM plan").fetchall()
    assert [(a_, q) for a_, q, z in p] == [("umbenennen", "20171231 FOTOS AUSWAHL 2017/IMG_1.jpg")]
    assert p[0][2].endswith("_00001.jpg")      # Zähler nicht durch AB22160115421 hochgetrieben
