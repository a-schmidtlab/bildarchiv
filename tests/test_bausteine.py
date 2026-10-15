import re
import tomllib

import pytest

from bildarchiv import einstellungen as es
from bildarchiv.kern.analyse import ordnermuster
from bildarchiv.kern.datierung import ereignis_name, folder_date_year, name_date, parse_dt
from bildarchiv.kern.klassifikation import Klassifikator
from bildarchiv.kern.struktur import Struktur


@pytest.mark.parametrize("ein,aus", [
    ("2019-07-14 Hochzeit", "Hochzeit"), ("Scans 2020-05-01", "Scans"), ("Konzert 12.4.01", "Konzert"),
    ("20190714_Urlaub Rom", "Urlaub Rom"), ("2019", "2019"), ("Hochzeit Meier", "Hochzeit Meier"),
    ("Best of 2010-2015", "Best of 2010-2015"), ("2024 Serie re-Edits", "Serie re-Edits"),
])
def test_ereignis_name(ein, aus):
    assert ereignis_name(ein) == aus


def test_ordnermuster():
    rx, vorlage, jahr = ordnermuster("20011231 FOTOS ARCHIV 2001")
    assert vorlage == "{jahr}1231 FOTOS ARCHIV {jahr}" and jahr == 2001
    assert re.match(rx, "20010615 FOTOS ARCHIV 2001")["jahr"] == "2001"
    rx, vorlage, _ = ordnermuster("2019 FOTOS FAMILIE")
    assert vorlage == "{jahr} FOTOS FAMILIE" and re.match(rx, "2023 FOTOS FAMILIE")
    assert ordnermuster("Hochzeit") is None


def test_struktur_vorhandene_jahresordner():
    e = es.standard()
    e["struktur"]["jahresordner"] = "{jahr}1231 FOTOS ARCHIV {jahr}"
    e["struktur"]["jahresordner_muster"] = ordnermuster("20011231 FOTOS ARCHIV 2001")[0]
    s = Struktur(e, ["20010615 FOTOS ARCHIV 2001", "Videos"])
    assert s.jahresordner(2001) == "20010615 FOTOS ARCHIV 2001"     # vorhandener Name bleibt exakt
    assert s.jahresordner(1998) == "19981231 FOTOS ARCHIV 1998"
    assert s.neue_jahre == {"19981231 FOTOS ARCHIV 1998"}


def test_split_stem_und_klassen():
    k = Klassifikator()
    assert k.split_stem("IMG_1.CR2.xmp") == ("IMG_1", ".CR2.xmp")
    assert k.split_stem("IMG_1.jpg") == ("IMG_1", ".jpg")
    assert k.split_stem("IMG_1.AAE") == ("IMG_1", ".AAE")
    assert k.art("a/._IMG_1.jpg") == "muell" and k.art("a/Thumbs.db") == "muell"
    assert k.art("a/.designerthumb/x.jpg") == "muell"
    assert k.art("a/session.cosessiondb") == "c1"
    assert k.art("a/x.pdf") == "sonstiges"
    k2 = Klassifikator(sidecars_plus=["dop"], muell_plus=["*.bak", "picasa.ini"])
    assert k2.art("a/x.dop") == "sidecar" and k2.art("a/x.bak") == "muell" and k2.art("a/Picasa.ini") == "muell"


def test_datum():
    assert parse_dt("2019:07:14 15:30:12") == ("20190714", "153012")
    assert parse_dt("1970:01:01 00:00:00") is None
    assert name_date(["IMG_20190714_153012.jpg"]) == ("20190714", "153012")
    assert folder_date_year("Konzert 12.4.01") == 2001


def test_toml_rundreise(tmp_path):
    e = es.standard()
    e["struktur"]["ordner"] = {"2019 BILDER/Portfolio \"neu\"": "wurzel"}
    e["namen"]["frueher"] = ["{datum}_{zeit}_AB{lauf}_{nr:5}"]
    p = tmp_path / "e.toml"
    es.speichern(e, p)
    with open(p, "rb") as fh:
        assert es.aus_dict(tomllib.load(fh)) == e


def test_einstellungen_pruefen():
    e = es.standard()
    assert not es.hat_fehler(es.pruefe(e))
    e["namen"]["kuerzel"] = "A1"
    e["struktur"]["art"] = "quatsch"
    bereiche = {b for b, m in es.pruefe(e) if m.stufe == "fehler"}
    assert {"namen.kuerzel", "struktur.art"} <= bereiche


def test_vorlage_historie():
    e = es.standard()
    e["namen"]["kuerzel"] = "AB"
    es.vorlage_aendern(e, "{datum}_{nr:4}")
    assert e["namen"]["frueher"] == []                         # vor dem Festschreiben keine Historie
    e["sammlung"]["festgeschrieben"] = True
    es.vorlage_aendern(e, "{datum}_{zeit}_{nr:4}")
    assert e["namen"]["frueher"] == ["{datum}_{nr:4}"]
    es.kuerzel_aendern(e, "BX")
    e["namen"]["vorlage"] = "{datum}_{kürzel}_{nr:4}"
    es.kuerzel_aendern(e, "CX")
    assert "{datum}_BX_{nr:4}" in e["namen"]["frueher"]
