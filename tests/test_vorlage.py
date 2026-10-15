import pytest

from bildarchiv.kern.vorlage import (Vorlage, VorlagenFehler, pruefe, pruefe_ordner, hat_fehler, slug,
                                     ALTSCHEMATA, BEISPIELE)

W = dict(datum="20190714", zeit="153012", lauf="20261015", nr=123, kuerzel="AB",
         ereignis="Hochzeit Müller", kamera="Canon EOS 5D", altname="IMG_4711", hash="a1b2c3d4e5f6")


def test_archivschema():
    v = Vorlage("{datum}_{zeit}_{kürzel}{lauf}_{nr:5}")
    assert v.render(W) == "20190714_153012_AB20261015_00123"


def test_formate():
    assert Vorlage("{datum:JJJJ-MM-TT}_{zeit:hh-mm-ss}_{nr:4}").render(W) == "2019-07-14_15-30-12_0123"
    assert Vorlage("{datum:JJ}{datum:MM}").render(W) == "1907"
    assert Vorlage("{jahr}-{monat}").render(W) == "2019-07"


def test_filter():
    assert Vorlage("{ereignis|slug|klein|max:8}_{nr}").render(W) == "hochzeit_0123"
    assert Vorlage("{ereignis|slug}_{nr}").render(W) == "Hochzeit-Mueller_0123"
    assert Vorlage("{kamera|slug|gross}").render(W) == "CANON-EOS-5D"
    assert Vorlage("{serie|oder:unbekannt}_{nr}").render(W) == "unbekannt_0123"


def test_optional():
    v = Vorlage("{datum}_{zeit}[_{kamera|slug|klein}]_{nr:3}")
    assert v.render(W) == "20190714_153012_canon-eos-5d_123"
    assert v.render({**W, "kamera": ""}) == "20190714_153012_123"


def test_unbekanntes_datum():
    v = Vorlage("{datum}_{zeit}_{nr:5}")
    assert v.render({**W, "datum": "20190700", "zeit": "000000"}) == "20190700_000000_00123"


def test_altnr_und_hash():
    assert Vorlage("{altnr}_{hash:6}").render(W) == "4711_a1b2c3"
    assert Vorlage("{hash:4}").render({"hash": "px:ff00aa"}) == "ff00"


def test_escapes():
    assert Vorlage("{{x}}_[[{nr}]]").render(W) == "{x}_[0123]"


@pytest.mark.parametrize("text", ["{datum", "datum}", "[{nr}", "{nr}]", "{foo}", "{datum:XYZ}",
                                  "{nr:abc}", "{ereignis|bla}", "{ereignis|max:x}", "a/b{nr}", "a:b{nr}",
                                  "[a[b]]", "{kamera|oder:}"])
def test_syntaxfehler(text):
    with pytest.raises(VorlagenFehler):
        Vorlage(text)


def test_unzulaessige_zeichen_im_wert():
    assert Vorlage("{ereignis}_{nr}").render({**W, "ereignis": "a/b:c"}) == "a-b-c_0123"


def test_ruecklesen():
    v = Vorlage("{datum}_{zeit}_{kürzel}{lauf}_{nr:5}")
    r = v.lies("20190714_153012_AB20261015_00123", kuerzel="AB")
    assert r["datum"] == "20190714" and r["zeit"] == "153012" and r["nr"] == 123 and r["lauf"] == "20261015"
    assert v.lies("20190714_153012_ABp20261015_00123", kuerzel="AB") is None
    assert v.lies("IMG_4711", kuerzel="AB") is None
    # Altschema: beliebiges Kürzel
    alt = Vorlage(ALTSCHEMATA[0])
    assert alt.lies("20190714_153012_ABp20261014_00007")["kuerzel"] == "ABp"
    # Zähler mit mehr Stellen als vorgesehen
    assert v.lies("20190714_153012_AB20261015_123456", kuerzel="AB")["nr"] == 123456


def test_ruecklesen_mit_text():
    v = Vorlage("{datum}_{ereignis|slug|klein}_{nr:4}")
    r = v.lies("20190714_hochzeit-meier_0123")
    assert r["ereignis"] == "hochzeit-meier" and r["nr"] == 123
    v2 = Vorlage("{datum}_{zeit}[_{kamera|slug|klein}]_{nr:3}")
    assert v2.lies("20190714_153012_123")["nr"] == 123
    assert v2.lies("20190714_153012_eos_123")["kamera"] == "eos"


@pytest.mark.parametrize("name,text", list(BEISPIELE.items()))
def test_beispiele_rundreise(name, text):
    v = Vorlage(text)
    assert not hat_fehler(pruefe(text)), pruefe(text)
    n = v.render(W)
    r = v.lies(n, kuerzel="AB")
    assert r is not None and r["nr"] == 123, (name, n)


def test_pruefung_nr_fehlt():
    assert hat_fehler(pruefe("{datum}_{zeit}"))
    assert hat_fehler(pruefe("{datum}[_{nr}]"))


def test_pruefung_zaehlerbereich():
    # pro Tag ohne Tag im Namen -> Wiederholung im Jahresordner
    m = pruefe("{jahr}_{nr:4}", zaehler="tag", struktur="jahr")
    assert hat_fehler(m)
    assert not hat_fehler(pruefe("{datum}_{nr:4}", zaehler="tag", struktur="jahr"))
    # pro Sekunde braucht die Uhrzeit
    assert hat_fehler(pruefe("{datum}_{nr:2}", zaehler="sekunde"))
    assert not hat_fehler(pruefe("{datum}_{zeit}_{nr:2}", zaehler="sekunde"))
    # pro Monat in Jahr/Monat-Struktur ist durch den Ordner gedeckt, aber nur dort eindeutig
    m = pruefe("{ereignis|oder:x}_{nr:4}", zaehler="monat", struktur="jahr_monat")
    assert not hat_fehler(m) and any(x.stufe == "warnung" for x in m)


def test_pruefung_trenner():
    assert hat_fehler(pruefe("{datum}_{ereignis}{kamera}_{nr}"))


def test_pruefung_hinweise():
    m = pruefe("{nr:4}_{datum}", zaehler="gesamt")
    assert not hat_fehler(m)
    assert any("chronologisch" in x.text for x in m)
    m = pruefe("{datum}_{kamera}_{nr}")
    assert any("leer" in x.text for x in m)


def test_ordnervorlagen():
    assert not pruefe_ordner("{jahr}1231 FOTOS ARCHIV {jahr}", "jahresordner")
    assert hat_fehler(pruefe_ordner("BILDER", "jahresordner"))
    assert not pruefe_ordner("{datum:JJJJ-MM-TT} {ereignis}", "ereignisordner")
    v = Vorlage("{jahr}1231 FOTOS ARCHIV {jahr}")
    assert v.render({"jahr": "2001"}) == "20011231 FOTOS ARCHIV 2001"
    assert v.lies("20011231 FOTOS ARCHIV 2001")["jahr"] == "2001"
    assert v.lies("20011231 FOTOS ARCHIV 2002") is None


def test_slug():
    assert slug("Ärger über Straße & Co.") == "Aerger-ueber-Strasse-Co"
    assert slug("Café") == "Cafe"
