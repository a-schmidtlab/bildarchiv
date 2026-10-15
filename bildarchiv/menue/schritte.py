"""Die Bildschirme der einzelnen Schritte."""

import copy
import json
import os
import time

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import (Button, DataTable, Footer, Input, Label, RadioButton, RadioSet, Select, Static,
                             Switch)

from .. import ablauf
from .. import einstellungen as es
from ..kern import ausfuehren as kausf
from ..kern import db as kdb
from ..kern import vorlage as kv
from ..kern.struktur import Struktur as KernStruktur
from . import beispiele as bsp
from .dialoge import T, Bestaetigen, Eingabe, Liste, OrdnerWahl, PlatzhalterHilfe, oeffnen
from .status import dubletten_zahlen, plan_status
from .texte import befund_text, einstellungen_text, zahl


def _meta_aendern(s):
    """Signal: Plan muss neu erstellt werden (Entscheidungen geändert)."""
    kdb.meta_set(s.db, "einstellungen_geaendert", str(time.time()))
    s.db.commit()


class Schritt(Screen):
    TITEL = ""
    BINDINGS = [Binding("escape", "app.pop_screen", "Zurück")]

    @property
    def s(self):
        return self.app.sammlung

    def kopf(self):
        yield Label(f"{self.TITEL} – {self.s.name}", classes="titel", markup=False)

    def aktualisieren(self):
        pass

    def on_screen_resume(self):
        self.aktualisieren()

    def on_mount(self):
        self.aktualisieren()


# --------------------------------------------------------------------------- 1 Analyse

class Analyse(Schritt):
    TITEL = "1 Analyse"
    BINDINGS = Schritt.BINDINGS + [Binding("a", "starten", "Analyse starten"),
                                   Binding("o", "ordner", "Ordner entscheiden")]

    def compose(self) -> ComposeResult:
        yield from self.kopf()
        yield Static("Liest alle Dateien und ihre Metadaten (nur lesen). Bei einer großen Sammlung dauert der erste "
                     "Lauf etwas, spätere Läufe lesen nur geänderte Dateien.", classes="hinweis")
        with Horizontal(classes="knoepfe"):
            yield Button("Analyse starten", id="start", variant="primary")
            yield Button("Alles neu lesen", id="voll")
            yield Button("Vorschläge übernehmen", id="vorschlaege")
            yield Button("Daten einer Vorgängerversion …", id="altdaten")
        with VerticalScroll():
            yield Static("", id="befund", markup=False)
        with Horizontal(classes="knoepfe"):
            yield Button("Müll", id="l_muell")
            yield Button("Ohne Datum", id="l_ohne")
            yield Button("Abweichungen", id="l_abw")
            yield Button("Ordner entscheiden …", id="l_ordner")
        yield Footer()

    def aktualisieren(self):
        b = kdb.meta_json(self.s.db, "befund")
        st = self.query_one("#befund", Static)
        if not b:
            st.update("Noch keine Analyse.")
        else:
            st.update("\n".join(befund_text(b, self.s.einstellungen)))
        self.query_one("#vorschlaege", Button).disabled = not (b and b.get("vorschlaege")) or self.s.festgeschrieben
        for i in ("l_muell", "l_ohne", "l_abw", "l_ordner", "altdaten"):
            self.query_one(f"#{i}", Button).disabled = not b

    def action_starten(self, voll=False):
        self.app.starte_job("analyse", {"voll": voll}, danach=lambda st: self.aktualisieren())

    def action_ordner(self):
        self.app.push_screen(OrdnerEntscheidung("sammlung"))

    def on_button_pressed(self, ev: Button.Pressed):
        b = kdb.meta_json(self.s.db, "befund") or {}
        w = self.s.wurzel
        i = ev.button.id
        if i == "start":
            self.action_starten()
        elif i == "voll":
            self.action_starten(True)
        elif i == "vorschlaege":
            from ..kern import analyse as ka
            ka.uebernehmen(self.s.einstellungen, b["vorschlaege"])
            self.s.speichere_einstellungen()
            b["vorschlaege"] = []
            kdb.meta_set_json(self.s.db, "befund", b)
            self.s.db.commit()
            self.app.notify("Vorschläge übernommen")
            self.aktualisieren()
        elif i == "altdaten":
            from pathlib import Path
            from ..kern import altdaten as kalt
            gefunden = kalt.finde(self.s, Path.home() / ".local" / "share" / "bilderauswahlen")

            def los(pfad):
                if pfad:
                    self.app.starte_job("altdaten", {"pfad": pfad.strip()}, danach=lambda st: self.aktualisieren())
            self.app.push_screen(Eingabe(
                "Datenbank der Vorgängerversion",
                gefunden[0] if gefunden else str(Path.home() / ".local" / "share" / "bilderauswahlen" / "archiv.db"),
                "Übernimmt ImageDataHash, dHash und Miniaturen (spart das erneute Lesen aller Bilder) und die "
                "Ablage der Dubletten und Re-Edit-Ordner. Nur lesend, die alte Datenbank bleibt unverändert."
                + (" Passende Datenbank gefunden." if gefunden else "")), los)
        elif i == "l_muell":
            self.app.push_screen(Liste("Müll", ["Datei"], [(p,) for p in b["muell_beispiele"]], w, 0))
        elif i == "l_ohne":
            self.app.push_screen(Liste("Ohne Datum", ["Datei"], [(p,) for p in b["ohne_datum"]], w, 0))
        elif i == "l_abw":
            self.app.push_screen(Liste("Bilder in einem anderen Jahresordner als ihr Aufnahmejahr",
                                       ["Datei", "Ordnerjahr", "Aufnahmejahr"], b["abweichung_beispiele"], w, 0))
        elif i == "l_ordner":
            self.action_ordner()


# --------------------------------------------------------------------------- Ordner-Entscheidungen

class OrdnerEntscheidung(Schritt):
    """Pro Ordner: als Ereignis / auflösen / unverändert lassen / an die Wurzel. modus: 'sammlung' | 'import'."""
    TITEL = "Ordner entscheiden"
    BINDINGS = Schritt.BINDINGS + [Binding("enter", "wechseln", "Entscheidung wechseln"),
                                   Binding("n", "umbenennen", "Ereignisname")]

    def __init__(self, modus="sammlung"):
        super().__init__()
        self.modus = modus
        self.folge = ["ereignis", "aufloesen", "behalten", "wurzel"] if modus == "sammlung" else \
            ["ereignis", "aufloesen", "behalten"]

    def compose(self) -> ComposeResult:
        yield from self.kopf()
        flach = self.s.einstellungen["struktur"]["art"] != "jahr_ereignis"
        yield Static("Enter wechselt die Entscheidung, n ändert den Namen des Ereignisses. "
                     + ("Bei der gewählten Struktur bedeuten „als Ereignis“ und „auflösen“ dasselbe: die Bilder "
                        "kommen nach Aufnahmedatum in die Jahresordner. " if flach else "")
                     + ("„unverändert lassen“ = nicht importieren." if self.modus == "import" else
                        "„unverändert lassen“ = Ordner bleibt, wo er ist; „an die Wurzel“ = Ordner kommt unverändert "
                        "auf die oberste Ebene (z. B. Re-Edit-Ordner)."), classes="hinweis", markup=False)
        yield DataTable(cursor_type="row", zebra_stripes=True)
        yield Footer()

    def _daten(self):
        db = self.s.db
        if self.modus == "sammlung":
            b = kdb.meta_json(db, "befund") or {}
            return (b.get("ordnerliste", []), self.s.einstellungen["struktur"]["ordner"],
                    self.s.einstellungen["struktur"]["ereignisnamen"])
        b = kdb.meta_json(db, "import_befund") or {}
        return (b.get("ordnerliste", []), kdb.meta_json(db, "import_ordner", {}),
                kdb.meta_json(db, "import_ereignisnamen", {}))

    def on_mount(self):
        t = self.query_one(DataTable)
        for c in ("Ordner", "Bilder", "Jahre", "Hinweis", "Entscheidung", "Ereignisname"):
            t.add_column(c, key=c)
        self.aktualisieren()
        t.focus()

    def aktualisieren(self):
        t = self.query_one(DataTable)
        zeile = t.cursor_row
        t.clear()
        liste, entsch, namen = self._daten()
        for o in liste:
            e = entsch.get(o["ordner"], "ereignis")
            text = es.TEXTE.get(e, e) if not (self.modus == "import" and e == "behalten") else "nicht importieren"
            t.add_row(T(o["ordner"]), zahl(o["bilder"]), T(o["jahre"]), T(o["art"]), T(text),
                      T(namen.get(o["ordner"], "")), key=o["ordner"])
        if t.row_count:
            t.move_cursor(row=min(zeile, t.row_count - 1))

    def _speichern(self, entsch, namen):
        if self.modus == "sammlung":
            st = self.s.einstellungen["struktur"]
            st["ordner"] = {k: v for k, v in entsch.items() if v != "ereignis"}
            st["ereignisnamen"] = {k: v for k, v in namen.items() if v}
            self.s.speichere_einstellungen()
        else:
            kdb.meta_set_json(self.s.db, "import_ordner", {k: v for k, v in entsch.items() if v != "ereignis"})
            kdb.meta_set_json(self.s.db, "import_ereignisnamen", {k: v for k, v in namen.items() if v})
            _meta_aendern(self.s)

    def _aktuell(self):
        t = self.query_one(DataTable)
        if not t.row_count:
            return None
        return t.coordinate_to_cell_key((t.cursor_row, 0)).row_key.value

    def action_wechseln(self):
        k = self._aktuell()
        if k is None:
            return
        _, entsch, namen = self._daten()
        entsch = dict(entsch)
        alt = entsch.get(k, "ereignis")
        entsch[k] = self.folge[(self.folge.index(alt) + 1) % len(self.folge)] if alt in self.folge else "ereignis"
        self._speichern(entsch, dict(namen))
        self.aktualisieren()

    def on_data_table_row_selected(self, ev):
        self.action_wechseln()

    def action_umbenennen(self):
        k = self._aktuell()
        if k is None:
            return
        liste, entsch, namen = self._daten()

        def fertig(wert):
            if wert is None:
                return
            n = dict(namen)
            n[k] = wert.strip()
            self._speichern(dict(entsch), n)
            self.aktualisieren()
        from ..kern.datierung import ereignis_name
        self.app.push_screen(Eingabe(f"Name des Ereignisses für „{k}“", namen.get(k) or ereignis_name(
            k.rsplit("/", 1)[-1]), "Leer lassen = Ordnername ohne Datum"), fertig)


# --------------------------------------------------------------------------- Formularhilfen

def _feld(gruppe, schluessel, beschriftung, art, wert, gesperrt=False):
    wid = f"{gruppe}__{schluessel}"
    if art == "select":
        w = Select([(es.TEXTE.get(o, o), o) for o in es.AUSWAHL[(gruppe, schluessel)]], value=wert,
                   allow_blank=False, id=wid, disabled=gesperrt)
    elif art == "switch":
        w = Switch(bool(wert), id=wid, disabled=gesperrt)
    else:
        w = Input(str(wert), id=wid, disabled=gesperrt)
    return Horizontal(Label(beschriftung, markup=False), w, classes="feld", id=f"f_{wid}")


class Formular(Schritt):
    """Bearbeitet eine Kopie der Einstellungen; Speichern übernimmt sie."""

    def on_mount(self):
        self.e = copy.deepcopy(self.s.einstellungen)
        self.geaendert = False
        self.aktualisieren()

    def _setze(self, wid, wert):
        if "__" not in (wid or ""):
            return
        g, k = wid.split("__", 1)
        if g in self.e and k in self.e[g]:
            if k == "zusatz":
                wert = [x.strip() for x in wert.split(",") if x.strip()]
            if self.e[g][k] == wert:
                return      # Startwert beim Aufbau des Formulars
            self.e[g][k] = wert
            self.geaendert = True
            self.pruefen()

    def on_input_changed(self, ev: Input.Changed):
        self._setze(ev.input.id, ev.value)

    def on_select_changed(self, ev: Select.Changed):
        self._setze(ev.select.id, ev.value)

    def on_switch_changed(self, ev: Switch.Changed):
        self._setze(ev.switch.id, ev.value)

    def pruefen(self):
        pass

    def speichern(self):
        meldungen = [m for b, m in es.pruefe(self.e) if m.stufe == "fehler"]
        if meldungen:
            self.app.notify("Nicht gespeichert – bitte zuerst die Fehler beheben.", severity="error")
            return False
        alt = self.s.einstellungen
        neu = self.e
        vorlage, kuerzel = neu["namen"]["vorlage"], neu["namen"]["kuerzel"]
        neu["namen"]["vorlage"], neu["namen"]["kuerzel"] = alt["namen"]["vorlage"], alt["namen"]["kuerzel"]
        es.vorlage_aendern(neu, vorlage)
        es.kuerzel_aendern(neu, kuerzel)
        self.s.einstellungen = neu
        self.s.speichere_einstellungen()
        self.e = copy.deepcopy(neu)
        self.geaendert = False
        self.app.notify("Gespeichert. Ein vorhandener Plan muss neu erstellt werden.")
        return True

    def action_zurueck(self):
        if not self.geaendert:
            self.app.pop_screen()
            return

        def fertig(ja):
            if ja and self.speichern():
                self.app.pop_screen()
            elif ja is False:
                self.app.pop_screen()
        self.app.push_screen(Bestaetigen("Änderungen speichern?", "Es gibt ungespeicherte Änderungen.",
                                         ja="Speichern"), fertig)


# --------------------------------------------------------------------------- 2 Zielstruktur

ARTEN_TEXT = {
    "jahr": "Jahresordner, flach – alle Bilder eines Jahres in einem Ordner",
    "jahr_monat": "Jahr / Monat – Unterordner pro Monat",
    "jahr_ereignis": "Jahr / Ereignis – Unterordner bleiben, Namen vereinheitlicht (Datum + Name)",
}


class Struktur(Formular):
    TITEL = "2 Zielstruktur"
    BINDINGS = [Binding("escape", "zurueck", "Zurück"), Binding("ctrl+s", "speichern", "Speichern"),
                Binding("o", "ordner", "Ordner entscheiden")]

    def compose(self) -> ComposeResult:
        e = self.s.einstellungen
        gesperrt = self.s.festgeschrieben
        yield from self.kopf()
        if gesperrt:
            yield Static("Die Struktur ist festgeschrieben und gilt für alle Importe. Sie lässt sich nicht mehr ändern.",
                         classes="warnung")
        with Horizontal(classes="spalten"):
            with VerticalScroll(classes="links formular"):
                with RadioSet(id="art", disabled=gesperrt):
                    for a, text in ARTEN_TEXT.items():
                        yield RadioButton(text, value=e["struktur"]["art"] == a, id=f"art_{a}")
                yield _feld("struktur", "jahresordner", "Jahresordner (Vorlage)", "input",
                            e["struktur"]["jahresordner"], gesperrt)
                yield _feld("struktur", "monatsordner", "Monatsordner (Vorlage)", "input",
                            e["struktur"]["monatsordner"], gesperrt)
                yield _feld("struktur", "ereignisordner", "Ereignisordner (Vorlage)", "input",
                            e["struktur"]["ereignisordner"], gesperrt)
                yield _feld("struktur", "videos", "Videos", "select", e["struktur"]["videos"], gesperrt)
                yield _feld("struktur", "videoordner", "Videoordner", "input", e["struktur"]["videoordner"], gesperrt)
                yield _feld("struktur", "muell", "Müll", "select", e["struktur"]["muell"], gesperrt)
                yield _feld("struktur", "capture_one", "Capture One", "select", e["struktur"]["capture_one"],
                            gesperrt)
                yield _feld("struktur", "c1_trash", "Bilder aus C1-Trash", "select", e["struktur"]["c1_trash"],
                            gesperrt)
                yield _feld("struktur", "datum_konflikt", "Datum im Ordnernamen ≠ EXIF", "select",
                            e["struktur"]["datum_konflikt"], gesperrt)
                yield _feld("dubletten", "ablage", "Dubletten-Ablage", "select", e["dubletten"]["ablage"], gesperrt)
                yield _feld("dubletten", "ordner", "Dubletten-Ordner", "input", e["dubletten"]["ordner"], gesperrt)
                yield _feld("dubletten", "aehnliche", "Ähnliche Bilder suchen", "switch",
                            e["dubletten"]["aehnliche"], gesperrt)
                yield _feld("struktur", "sonstiges", "Ordner für Sonstiges", "input", e["struktur"]["sonstiges"],
                            gesperrt)
                yield Static("", id="meldungen", classes="meldungen", markup=False)
                with Horizontal(classes="knoepfe"):
                    yield Button("Speichern", id="speichern", variant="primary", disabled=gesperrt)
                    yield Button("Ordner einzeln entscheiden …", id="ordner")
            with VerticalScroll(classes="rechts"):
                yield Label("Vorschau", classes="abschnitt")
                yield Static("", id="vorschau", classes="vorschau", markup=False)
        yield Footer()

    def on_radio_set_changed(self, ev: RadioSet.Changed):
        art = ev.pressed.id.removeprefix("art_")
        if art != self.e["struktur"]["art"]:
            self.e["struktur"]["art"] = art
            self.geaendert = True
        self.pruefen()

    def aktualisieren(self):
        if hasattr(self, "e"):
            self.pruefen()

    def pruefen(self):
        art = self.e["struktur"]["art"]
        self.query_one("#f_struktur__monatsordner").display = art == "jahr_monat"
        self.query_one("#f_struktur__ereignisordner").display = art == "jahr_ereignis"
        self.query_one("#f_struktur__videoordner").display = self.e["struktur"]["videos"] == "eigener_ordner"
        m = [(b, x) for b, x in es.pruefe(self.e) if b.startswith(("struktur", "dubletten"))]
        self.query_one("#meldungen", Static).update(
            "\n".join(f"{'✗' if x.stufe == 'fehler' else '!'} {x.text}" for _, x in m) or "✔ keine Fehler")
        self.query_one("#vorschau", Static).update(self._vorschau())

    def _vorschau(self):
        b = kdb.meta_json(self.s.db, "befund")
        if not b:
            return "Erst die Analyse ausführen."
        try:
            st = KernStruktur(self.e, [name for _, name in b["jahresordner"]])
        except Exception as err:     # ungültige Vorlage oder Muster
            return f"Vorlage ungültig: {err}"
        z = [f"{self.s.name}/"]
        jahre = b["jahre"][-8:]
        ereignisse = {}
        for o in b["ordnerliste"]:
            if o["ordnerjahr"]:
                ereignisse.setdefault(o["ordnerjahr"], o["ordner"].rsplit("/", 1)[-1])
        art = self.e["struktur"]["art"]
        if len(b["jahre"]) > 8:
            z.append(f"├── … ({len(b['jahre']) - 8} weitere Jahre)")
        for jahr, n in jahre:
            name = st.jahresordner(int(jahr))
            neu = "  (neu)" if name in st.neue_jahre else ""
            z.append(f"├── {name}/  {zahl(n)}{neu}")
            if art == "jahr_monat":
                z.append(f"│   ├── {st.monatsordner(int(jahr), f'{jahr}0714')}/   …")
            elif art == "jahr_ereignis" and int(jahr) in ereignisse:
                z.append(f"│   ├── {st.ereignisordner(int(jahr), 'x', ereignisse[int(jahr)], f'{jahr}0714')}/   …")
        z.append(f"├── {self.e['dubletten']['ordner']}/")
        if self.e["struktur"]["videos"] == "eigener_ordner":
            z.append(f"├── {self.e['struktur']['videoordner']}/")
        z.append(f"└── {self.e['struktur']['sonstiges']}/")
        if b["ordnermuster"]:
            z += ["", "Vorhandene Jahresordner behalten ihren Namen."]
        return "\n".join(z)

    def action_speichern(self):
        if not self.s.festgeschrieben:
            self.speichern()

    def action_ordner(self):
        self.app.push_screen(OrdnerEntscheidung("sammlung"))

    def on_button_pressed(self, ev: Button.Pressed):
        if ev.button.id == "speichern":
            self.action_speichern()
        elif ev.button.id == "ordner":
            if self.geaendert:
                self.speichern()
            self.action_ordner()



# --------------------------------------------------------------------------- 3 Dubletten

class Dubletten(Schritt):
    TITEL = "3 Dubletten prüfen"
    BINDINGS = Schritt.BINDINGS + [
        Binding("o", "oeffnen", "Bild öffnen"), Binding("b", "behalten", "Diese behalten"),
        Binding("k", "keine", "Keine Dublette"), Binding("z", "zuruecknehmen", "Entscheidung zurücknehmen"),
        Binding("p", "planen", "Plan neu erstellen")]

    def compose(self) -> ComposeResult:
        yield from self.kopf()
        yield Static("", id="info", classes="hinweis", markup=False)
        with Horizontal(classes="spalten"):
            with Vertical(classes="rechts"):
                yield Label("Gruppen (unsichere zuerst)", classes="abschnitt")
                yield DataTable(cursor_type="row", zebra_stripes=True, id="gruppen")
            with Vertical(classes="links"):
                yield Label("Mitglieder", classes="abschnitt")
                yield DataTable(cursor_type="row", zebra_stripes=True, id="mitglieder")
        yield Footer()

    def on_mount(self):
        g = self.query_one("#gruppen", DataTable)
        for c in ("Gruppe", "Art", "Dateien", ""):
            g.add_column(c)
        m = self.query_one("#mitglieder", DataTable)
        for c in ("Rolle", "Datei", "Größe", "Pixel", "Caption", "Entscheidung"):
            m.add_column(c)
        self.aktualisieren()
        g.focus()

    def aktualisieren(self):
        s = self.s
        ps = plan_status(s)
        modus = kdb.meta_get(s.db, "plan_modus")
        info = self.query_one("#info", Static)
        g = self.query_one("#gruppen", DataTable)
        g.clear()
        if ps == "kein" or modus != "ordnen":
            info.update("Die Dubletten werden beim Erstellen des Plans gesucht. p erstellt den Plan.")
            return
        n, uns = dubletten_zahlen(s)
        hinweis = "  –  Der Plan ist veraltet: p erstellt ihn neu." if ps == "veraltet" else ""
        info.update(f"{n} Gruppen, davon {uns} unsicher (ähnlich, verschiedene Auflösung).  "
                    f"b = diese Version behalten, k = keine Dublette, o = Bild öffnen.{hinweis}")
        for cl, nr, art, unsicher, anzahl in s.db.execute(
                "SELECT cluster, max(nr), max(art), max(unsicher), count(*) FROM dubletten GROUP BY cluster "
                "ORDER BY max(unsicher) DESC, cluster"):
            g.add_row(T(f"D{nr:05d}" if nr else f"#{cl}"), T("bitgleich" if art == "bitgleich" else "ähnlich"),
                      anzahl, T("unsicher" if unsicher else ""), key=str(cl))
        if g.row_count:
            self._mitglieder(g.coordinate_to_cell_key((0, 0)).row_key.value)

    def on_data_table_row_highlighted(self, ev: DataTable.RowHighlighted):
        if ev.data_table.id == "gruppen" and ev.row_key is not None:
            self._mitglieder(ev.row_key.value)

    def _mitglieder(self, cluster):
        self.cluster = cluster
        m = self.query_one("#mitglieder", DataTable)
        m.clear()
        ent = kdb.entscheidungen(self.s.db)
        texte = {"bevorzugt": "behalten", "keine_dublette": "keine Dublette"}
        rollen = {"bleibt": "★ bleibt", "kleiner": "kleiner", "dublette": "Dublette", "partner": "Partner",
                  "vorhanden": "im Bestand", "ersetzt": "wird ersetzt", "nicht_importiert": "nicht importiert"}
        for bereich, pfad, rolle, b, h, groesse, cap in self.s.db.execute(
                "SELECT bereich, pfad, rolle, breite, hoehe, groesse, caption FROM dubletten WHERE cluster=? "
                "ORDER BY rolle<>'bleibt', pfad", (int(cluster),)):
            m.add_row(T(rollen.get(rolle, rolle)), T(pfad), T(groesse_text(groesse)),
                      T(f"{b}×{h}" if b else ""), T("✓" if cap else ""), T(texte.get(ent.get((bereich, pfad)), "")),
                      key=f"{bereich}\t{pfad}")

    def _datei(self):
        m = self.query_one("#mitglieder", DataTable)
        if not m.row_count:
            return None
        return m.coordinate_to_cell_key((m.cursor_row, 0)).row_key.value.split("\t", 1)

    def _entscheiden(self, art):
        d = self._datei()
        if not d:
            self.app.notify("Erst eine Datei in der rechten Liste wählen (Tab).")
            return
        db = self.s.db
        if art:
            if art == "bevorzugt":
                # nur eine bevorzugte Version pro Gruppe
                for b, p in db.execute("SELECT bereich, pfad FROM dubletten WHERE cluster=?",
                                       (int(self.cluster),)).fetchall():
                    db.execute("DELETE FROM entscheidungen WHERE bereich=? AND pfad=? AND art='bevorzugt'", (b, p))
            db.execute("INSERT OR REPLACE INTO entscheidungen VALUES (?,?,?)", (d[0], d[1], art))
        else:
            db.execute("DELETE FROM entscheidungen WHERE bereich=? AND pfad=?", (d[0], d[1]))
        _meta_aendern(self.s)
        self._mitglieder(self.cluster)
        self.app.notify("Gemerkt. Wirkt beim nächsten Plan (p).")

    def action_behalten(self):
        self._entscheiden("bevorzugt")

    def action_keine(self):
        self._entscheiden("keine_dublette")

    def action_zuruecknehmen(self):
        self._entscheiden(None)

    def action_oeffnen(self):
        d = self._datei()
        if d:
            w = self.s.wurzel if d[0] == "sammlung" else kdb.meta_get(self.s.db, "plan_quelle")
            oeffnen(os.path.join(w, d[1]))

    def action_planen(self):
        self.app.starte_job("plan", danach=lambda st: self.aktualisieren())


# --------------------------------------------------------------------------- 4 Metadaten & Dateinamen

ZAEHLER_TEXT = {"gesamt": "fortlaufend über die ganze Sammlung", "jahr": "pro Jahr (Vorgabe)",
                "monat": "pro Monat", "tag": "pro Tag", "sekunde": "pro Sekunde (nur Serienbilder)",
                "ordner": "pro Zielordner"}


def groesse_text(n):
    n = n or 0
    for einheit, f in (("GB", 1e9), ("MB", 1e6), ("KB", 1e3)):
        if n >= f:
            return f"{n / f:.1f} {einheit}"
    return f"{n} B"


class Namen(Formular):
    TITEL = "4 Metadaten & Dateinamen"
    BINDINGS = [Binding("escape", "zurueck", "Zurück"), Binding("ctrl+s", "speichern", "Speichern"),
                Binding("f2", "platzhalter", "Platzhalter")]

    def compose(self) -> ComposeResult:
        e = self.s.einstellungen
        fest = self.s.festgeschrieben
        yield from self.kopf()
        with VerticalScroll(classes="formular"):
            yield Label("Namensvorlage", classes="abschnitt")
            if fest:
                yield Static("Die Sammlung ist festgeschrieben: Eine geänderte Vorlage gilt nur für neue Bilder, "
                             "bereits benannte Dateien werden weiter erkannt.", classes="warnung")
            yield _feld("namen", "vorlage", "Vorlage", "input", e["namen"]["vorlage"])
            with Horizontal(classes="knoepfe"):
                for name in kv.BEISPIELE:
                    yield Button(name, id=f"bsp_{list(kv.BEISPIELE).index(name)}")
                yield Button("Platzhalter (F2)", id="hilfe")
            yield _feld("namen", "kuerzel", "Kürzel", "input", e["namen"]["kuerzel"])
            yield Label("Zähler", classes="abschnitt")
            with RadioSet(id="zaehler", disabled=fest):
                for z in es.AUSWAHL[("namen", "zaehler")]:
                    yield RadioButton(ZAEHLER_TEXT[z], value=e["namen"]["zaehler"] == z, id=f"z_{z}")
            yield Static("", id="meldungen", classes="meldungen", markup=False)
            yield Label("Vorschau mit Beispielen aus der Sammlung", classes="abschnitt")
            yield DataTable(id="vorschau", show_cursor=False, classes="namen")
            yield Label("Metadaten", classes="abschnitt")
            yield Static("Standardfelder: alter Dateiname in XMP-xmpMM:PreservedFileName, Ordnerpfad in "
                         "XMP-iptcExt:Event. Zusätzliche Felder als FELD=inhalt (inhalt: altname, ordnerpfad, "
                         "neuname), mehrere durch Komma, z. B. IPTC:SpecialInstructions=altname.",
                         classes="hinweis", markup=False)
            yield _feld("metadaten", "altname", "alter Dateiname →", "input", e["metadaten"]["altname"])
            yield _feld("metadaten", "ordnerpfad", "Ordnerpfad →", "input", e["metadaten"]["ordnerpfad"])
            yield _feld("metadaten", "neuname", "neuer Name →", "input", e["metadaten"]["neuname"])
            yield _feld("metadaten", "zusatz", "zusätzlich", "input", ", ".join(e["metadaten"]["zusatz"]))
            yield _feld("metadaten", "altbestand_angleichen", "Altbestand angleichen", "switch",
                        e["metadaten"]["altbestand_angleichen"])
            yield Static("Altbestand angleichen: Bei schon benannten Bildern den alten Namen aus EXIF:UserComment "
                         "zusätzlich nach PreservedFileName schreiben (jede Datei wird neu geschrieben).",
                         classes="hinweis", markup=False)
            with Horizontal(classes="knoepfe"):
                yield Button("Speichern", id="speichern", variant="primary")
        yield Footer()

    def on_mount(self):
        t = self.query_one("#vorschau", DataTable)
        for c in ("Bisher", "Neu", "Fall"):
            t.add_column(c)
        self.beispiele = bsp.beispiele(self.s)
        super().on_mount()

    def aktualisieren(self):
        if hasattr(self, "e"):
            self.pruefen()

    def pruefen(self):
        n = self.e["namen"]
        m = kv.pruefe(n["vorlage"], n["zaehler"], self.e["struktur"]["art"], n["kuerzel"])
        m += [x for b, x in es.pruefe(self.e) if b in ("namen.kuerzel", "metadaten.zusatz")]
        zeichen = {"fehler": "✗", "warnung": "!", "hinweis": "·"}
        self.query_one("#meldungen", Static).update(
            "\n".join(f"{zeichen[x.stufe]} {x.text}" for x in m) or "✔ eindeutig, zeichensicher, chronologisch")
        t = self.query_one("#vorschau", DataTable)
        t.clear()
        if not self.beispiele:
            t.add_row(T("(noch keine Analyse)"), T(""), T(""))
            return
        for b in self.beispiele:
            b["werte"]["kuerzel"] = n["kuerzel"]
        v = bsp.vorschau(n["vorlage"], self.beispiele, n["zaehler"])
        if isinstance(v, str):
            t.add_row(T("Vorlage ungültig"), T(v), T(""))
            return
        for alt, neu, grund in v:
            t.add_row(T(alt), T(neu), T(grund))

    def on_radio_set_changed(self, ev: RadioSet.Changed):
        z = ev.pressed.id.removeprefix("z_")
        if z != self.e["namen"]["zaehler"]:
            self.e["namen"]["zaehler"] = z
            self.geaendert = True
        self.pruefen()

    def action_platzhalter(self):
        inp = self.query_one("#namen__vorlage", Input)

        def einfuegen(text):
            if not text:
                return
            pos = inp.cursor_position
            inp.value = inp.value[:pos] + text + inp.value[pos:]
            inp.cursor_position = pos + len(text)
            inp.focus()
        self.app.push_screen(PlatzhalterHilfe(), einfuegen)

    def action_speichern(self):
        self.speichern()

    def on_button_pressed(self, ev: Button.Pressed):
        i = ev.button.id
        if i == "speichern":
            self.speichern()
        elif i == "hilfe":
            self.action_platzhalter()
        elif i.startswith("bsp_"):
            self.query_one("#namen__vorlage", Input).value = list(kv.BEISPIELE.values())[int(i[4:])]


# --------------------------------------------------------------------------- 5 Vorschau

class Vorschau(Schritt):
    TITEL = "5 Vorschau (Dry-Run)"
    BINDINGS = Schritt.BINDINGS + [Binding("p", "planen", "Plan erstellen"), Binding("/", "suchen", "Suchen"),
                                   Binding("z", "zielordner", "Zielordner"), Binding("b", "berichte", "Berichte")]

    def compose(self) -> ComposeResult:
        yield from self.kopf()
        with VerticalScroll(classes="vorschau", id="zusammenfassung_box"):
            yield Static("", id="zusammenfassung", markup=False)
        with Horizontal(classes="feld"):
            yield Select([("alle Aktionen", "")], value="", allow_blank=False, id="filter")
            yield Input(placeholder="Suchen (Teil des Pfades) …", id="suche")
        yield DataTable(cursor_type="row", zebra_stripes=True, id="plan")
        with Horizontal(classes="knoepfe"):
            yield Button("Plan erstellen", id="planen", variant="primary")
            yield Button("Zielordner", id="zielordner")
            yield Button("Berichte öffnen", id="berichte")
            yield Button("Weiter: Ausführen", id="weiter")
        yield Footer()

    def on_mount(self):
        t = self.query_one("#plan", DataTable)
        for c in ("Aktion", "Quelle", "Ziel"):
            t.add_column(c)
        self.query_one("#zusammenfassung_box").styles.max_height = 14
        self.aktualisieren()

    def aktualisieren(self):
        s = self.s
        ps = plan_status(s)
        modus = kdb.meta_get(s.db, "plan_modus")
        zus = kdb.meta_json(s.db, "plan_zusammenfassung", [])
        kopf = {"kein": "Noch kein Plan. p erstellt ihn (nur lesen, nichts wird verändert).",
                "veraltet": "Der Plan ist veraltet (Einstellungen, Entscheidungen oder Bestand geändert). "
                            "Bitte neu erstellen (p).",
                "ausgefuehrt": "Dieser Plan wurde bereits ausgeführt.",
                "leer": "Nichts zu tun – die Sammlung entspricht bereits der Zielstruktur.",
                "aktuell": ""}[ps]
        if modus == "import" and ps != "kein":
            kopf = "Der letzte Plan ist ein Import-Plan (siehe Ordner importieren). " + kopf
        self.query_one("#zusammenfassung", Static).update("\n".join(([kopf, ""] if kopf else []) + zus))
        anzahl = dict(s.db.execute("SELECT aktion, count(*) FROM plan GROUP BY aktion").fetchall())
        sel = self.query_one("#filter", Select)
        sel.set_options([("alle Aktionen", "")] + [(f"{a} ({zahl(n)})", a) for a, n in sorted(anzahl.items())])
        self.query_one("#weiter", Button).disabled = not (ps == "aktuell" and modus == "ordnen")
        self._fuellen()

    def _fuellen(self):
        t = self.query_one("#plan", DataTable)
        t.clear()
        f = self.query_one("#filter", Select).value or ""
        q = self.query_one("#suche", Input).value.lower()
        sql = "SELECT aktion, quelle, ziel FROM plan" + (" WHERE aktion=?" if f else "") + " ORDER BY id"
        n = 0
        for a, quelle, ziel in self.s.db.execute(sql, (f,) if f else ()):
            if q and q not in (quelle or "").lower() and q not in (ziel or "").lower():
                continue
            t.add_row(T(a), T(quelle), T(ziel or ""))
            n += 1
            if n >= 5000:
                t.add_row(T("…"), T("(Anzeige auf 5000 Zeilen begrenzt – Filter verwenden)"), T(""))
                break

    def on_select_changed(self, ev):
        self._fuellen()

    def on_input_changed(self, ev):
        self._fuellen()

    def action_suchen(self):
        self.query_one("#suche", Input).focus()

    def action_planen(self):
        self.app.starte_job("plan", danach=lambda st: self.aktualisieren())

    def action_zielordner(self):
        rows = [tuple(json.loads(d)) for (d,) in self.s.db.execute(
            "SELECT daten FROM bericht WHERE kategorie='zielordner'")]
        self.app.push_screen(Liste("Verteilung auf die Zielordner", ["Ordner", "Dateien"], rows))

    def action_berichte(self):
        p = kdb.meta_get(self.s.db, "plan_berichte")
        if p and os.path.isdir(p):
            oeffnen(p)
            self.app.notify(p)

    def on_button_pressed(self, ev: Button.Pressed):
        i = ev.button.id
        if i == "planen":
            self.action_planen()
        elif i == "zielordner":
            self.action_zielordner()
        elif i == "berichte":
            self.action_berichte()
        elif i == "weiter":
            self.app.switch_screen(Ausfuehren())


# --------------------------------------------------------------------------- 6 Ausführen

class Ausfuehren(Schritt):
    TITEL = "6 Ausführen"

    def compose(self) -> ComposeResult:
        yield from self.kopf()
        with VerticalScroll():
            yield Static("", id="text", markup=False)
        yield Label("", id="frage", markup=False)
        yield Input(id="name")
        with Horizontal(classes="knoepfe"):
            yield Button("Ausführen", id="los", variant="error", disabled=True)
            yield Button("Zurück", id="zurueck")
        yield Footer()

    def aktualisieren(self):
        s = self.s
        probleme = kausf.pruefe(s)
        zus = kdb.meta_json(s.db, "plan_zusammenfassung", [])
        modus = kdb.meta_get(s.db, "plan_modus")
        if probleme:
            text = ["Der Plan kann nicht ausgeführt werden:", ""] + probleme[:40]
            if len(probleme) > 40:
                text.append(f"… und {len(probleme) - 40} weitere")
            self.query_one("#name").display = False
            self.query_one("#frage").display = False
        else:
            text = zus + ["", "Jetzt werden Dateien verschoben, umbenannt, gelöscht (Müll) und Metadaten geschrieben. "
                          "Alles steht im Journal; Verschiebungen lassen sich rückgängig machen, Löschungen nicht.",
                          "Lange Läufe gehen im Hintergrund weiter, auch wenn das Menü geschlossen wird."]
            if modus == "ordnen" and not s.festgeschrieben:
                text.append("Danach sind Struktur und Zähler festgeschrieben und gelten für alle Importe.")
            self.query_one("#frage", Label).update(f"Zur Bestätigung den Namen der Sammlung eintippen: {s.name}")
            self.query_one("#name").display = True
            self.query_one("#frage").display = True
            self.query_one("#name", Input).focus()
        self.query_one("#text", Static).update("\n".join(text))

    def on_input_changed(self, ev: Input.Changed):
        self.query_one("#los", Button).disabled = ev.value.strip() != self.s.name

    def on_input_submitted(self, ev: Input.Submitted):
        if ev.value.strip() == self.s.name:
            self._los()

    def _los(self):
        self.query_one("#name", Input).value = ""
        self.app.starte_job("ausfuehren", danach=lambda st: self.aktualisieren())

    def on_button_pressed(self, ev: Button.Pressed):
        if ev.button.id == "los":
            self._los()
        else:
            self.app.pop_screen()


# --------------------------------------------------------------------------- 7 Import

class Import(Schritt):
    TITEL = "7 Ordner importieren"
    BINDINGS = Schritt.BINDINGS + [Binding("p", "planen", "Planen")]

    def compose(self) -> ComposeResult:
        db = self.s.db
        yield from self.kopf()
        yield Static("", id="hinweis", classes="hinweis", markup=False)
        with Vertical(classes="formular"):
            with Horizontal(classes="feld"):
                yield Label("Quellordner")
                yield Input(kdb.meta_get(db, "import_quelle", ""), id="quelle")
                yield Button("…", id="waehlen")
            with RadioSet(id="modus"):
                m = kdb.meta_get(db, "import_modus", "kopieren")
                yield RadioButton("kopieren (Quelle bleibt unverändert)", value=m == "kopieren", id="m_kopieren")
                yield RadioButton("verschieben (importierte Dateien verlassen die Quelle)", value=m == "verschieben",
                                  id="m_verschieben")
        with Horizontal(classes="knoepfe"):
            yield Button("Quelle einlesen und planen", id="planen", variant="primary")
            yield Button("Ordner der Quelle …", id="ordner")
            yield Button("Importieren", id="los", variant="error")
            yield Button("Struktur festschreiben …", id="festschreiben")
        with VerticalScroll():
            yield Static("", id="zusammenfassung", markup=False)
        with Horizontal(classes="knoepfe"):
            yield Button("Plan", id="l_plan")
            yield Button("Schon vorhanden", id="l_schon_vorhanden")
            yield Button("Ersetzt", id="l_ersetzt")
            yield Button("Nicht importiert", id="l_nicht_importiert")
        yield Footer()

    def aktualisieren(self):
        s = self.s
        db = s.db
        fest = s.festgeschrieben
        ps = plan_status(s)
        modus = kdb.meta_get(db, "plan_modus")
        import_plan = modus == "import" and ps in ("aktuell", "leer")
        h = self.query_one("#hinweis", Static)
        if not fest:
            h.update("Die Struktur ist noch nicht festgelegt. Zuerst die Sammlung ordnen (Schritte 1–6). Ist die "
                     "Sammlung schon geordnet (Vorschau: nichts zu tun), lässt sich die Struktur auch direkt "
                     "festschreiben.")
        else:
            st = s.einstellungen
            h.update(f"Import in die festgelegte Struktur: {es.TEXTE[st['struktur']['art']]} · "
                     f"Vorlage {st['namen']['vorlage']} · Kürzel {st['namen']['kuerzel']}")
        self.query_one("#planen", Button).disabled = not fest
        self.query_one("#ordner", Button).disabled = not kdb.meta_get(db, "import_befund")
        self.query_one("#los", Button).disabled = not (import_plan and ps == "aktuell")
        self.query_one("#festschreiben", Button).display = not fest
        zus = kdb.meta_json(db, "plan_zusammenfassung", []) if modus == "import" else []
        if modus == "import" and ps == "veraltet":
            zus = ["Der Import-Plan ist veraltet – bitte neu planen.", ""] + zus
        elif modus == "import" and ps == "ausgefuehrt":
            zus = ["Dieser Import wurde ausgeführt.", ""] + zus
        self.query_one("#zusammenfassung", Static).update("\n".join(zus))
        for k in ("plan", "schon_vorhanden", "ersetzt", "nicht_importiert"):
            self.query_one(f"#l_{k}", Button).disabled = modus != "import"

    def _modus(self):
        rs = self.query_one("#modus", RadioSet)
        return "verschieben" if rs.pressed_button and rs.pressed_button.id == "m_verschieben" else "kopieren"

    def action_planen(self):
        q = self.query_one("#quelle", Input).value.strip()
        if not q or not os.path.isdir(q):
            self.app.notify("Bitte einen vorhandenen Quellordner angeben.", severity="error")
            return
        db = self.s.db
        if kdb.meta_get(db, "import_quelle") != os.path.abspath(q):
            kdb.meta_set_json(db, "import_ordner", {})
            kdb.meta_set_json(db, "import_ereignisnamen", {})
            db.commit()
        args = dict(quelle=os.path.abspath(q), import_modus=self._modus(),
                    ordner=kdb.meta_json(db, "import_ordner", {}),
                    ereignisnamen=kdb.meta_json(db, "import_ereignisnamen", {}))
        self.app.starte_job("import_plan", args, danach=lambda st: self.aktualisieren())

    def on_button_pressed(self, ev: Button.Pressed):
        i = ev.button.id
        s = self.s
        if i == "waehlen":
            def gewaehlt(p):
                if p:
                    self.query_one("#quelle", Input).value = p
            self.app.push_screen(OrdnerWahl("Quellordner wählen", self.query_one("#quelle", Input).value), gewaehlt)
        elif i == "planen":
            self.action_planen()
        elif i == "ordner":
            self.app.push_screen(OrdnerEntscheidung("import"))
        elif i == "los":
            modus = kdb.meta_get(s.db, "plan_import_modus")

            def ja(ok):
                if ok:
                    self.app.starte_job("ausfuehren", danach=lambda st: self.aktualisieren())
            self.app.push_screen(Bestaetigen(
                "Importieren?", "\n".join(kdb.meta_json(s.db, "plan_zusammenfassung", [])) +
                f"\n\nDie Dateien werden {'verschoben' if modus == 'verschieben' else 'kopiert'}.",
                eingabe=s.name, ja="Importieren", gefaehrlich=True), ja)
        elif i == "festschreiben":
            def ja(ok):
                if ok:
                    try:
                        ablauf.festschreiben(s)
                        self.app.notify("Struktur festgeschrieben.")
                    except ablauf.SchrittFehler as err:
                        self.app.notify(str(err), severity="error")
                    self.aktualisieren()
            self.app.push_screen(Bestaetigen(
                "Struktur festschreiben?",
                "Die aktuellen Einstellungen (Struktur, Namensvorlage, Zähler) werden festgeschrieben, ohne die "
                "Sammlung umzuordnen. Das ist für Sammlungen gedacht, die schon geordnet sind. Bereits benannte "
                "Bilder werden über ihre Namen erkannt, der Zähler läuft dort weiter.\n\n" +
                "\n".join(einstellungen_text(s.einstellungen)), ja="Festschreiben"), ja)
        elif i.startswith("l_"):
            k = i[2:]
            quelle = kdb.meta_get(s.db, "plan_quelle")
            if k == "plan":
                rows = s.db.execute("SELECT aktion, quelle, ziel FROM plan ORDER BY id").fetchall()
                self.app.push_screen(Liste("Import-Plan", ["Aktion", "Quelle", "Ziel"], rows, quelle, 1))
            else:
                from ..kern.planung import BERICHTE
                rows = [tuple(json.loads(d)) for (d,) in s.db.execute(
                    "SELECT daten FROM bericht WHERE kategorie=?", (k,))]
                self.app.push_screen(Liste(ev.button.label.plain if hasattr(ev.button.label, "plain")
                                           else str(ev.button.label), BERICHTE[k][1], rows, quelle, 0))


# --------------------------------------------------------------------------- 8 Rückgängig

class Rueckgaengig(Schritt):
    TITEL = "8 Rückgängig machen"
    BINDINGS = Schritt.BINDINGS + [Binding("enter", "zurueck_machen", "Rückgängig machen", show=True)]

    def compose(self) -> ComposeResult:
        yield from self.kopf()
        yield Static("Enter macht das gewählte Journal rückgängig. Neueste zuerst; mehrere Läufe in umgekehrter "
                     "Reihenfolge zurücknehmen. Verschiebungen und "
                     "Kopien lassen sich zurücknehmen; gelöschter Müll und geschriebene Metadaten nicht.",
                     classes="hinweis", markup=False)
        yield DataTable(cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self):
        t = self.query_one(DataTable)
        for c in ("Journal", "Art", "Aktionen", "Status"):
            t.add_column(c)
        self.aktualisieren()
        t.focus()

    def aktualisieren(self):
        t = self.query_one(DataTable)
        t.clear()
        erledigt = kdb.meta_json(self.s.db, "rueckgaengig", {})
        for p in self.s.journale():
            try:
                info = kausf.journal_info(p)
            except OSError:
                continue
            art = "Import" if p.stem.endswith("_import") else "Ordnen"
            status = f"rückgängig gemacht {erledigt[p.name]}" if p.name in erledigt else ""
            t.add_row(T(p.name), T(art), T(f"{info['ok']}/{info['anzahl']}"), T(status), key=str(p))

    def on_data_table_row_selected(self, ev):
        self.action_zurueck_machen()

    def action_zurueck_machen(self):
        t = self.query_one(DataTable)
        if not t.row_count:
            return
        p = t.coordinate_to_cell_key((t.cursor_row, 0)).row_key.value
        if os.path.basename(p) in kdb.meta_json(self.s.db, "rueckgaengig", {}):
            self.app.notify("Dieses Journal wurde bereits rückgängig gemacht.", severity="warning")
            return
        info = kausf.journal_info(p)
        text = "\n".join(f"{k}: {v}" for k, v in sorted(info["arten"].items()))

        def ja(ok):
            if ok:
                self.app.starte_job("rueckgaengig", {"journal": p}, danach=lambda st: self.aktualisieren())
        self.app.push_screen(Bestaetigen(f"{os.path.basename(p)} rückgängig machen?", text,
                                         eingabe=self.s.name, ja="Rückgängig machen", gefaehrlich=True), ja)


# --------------------------------------------------------------------------- 9 Einstellungen

class Einstellungen(Schritt):
    TITEL = "9 Einstellungen"

    def compose(self) -> ComposeResult:
        yield from self.kopf()
        with VerticalScroll():
            yield Static("", id="text", markup=False)
        with Horizontal(classes="knoepfe"):
            yield Button("Datenordner öffnen", id="daten")
            yield Button("Ordner _bildarchiv öffnen", id="spezial")
        yield Footer()

    def aktualisieren(self):
        s = self.s
        z = einstellungen_text(s.einstellungen)
        z += ["", f"Sammlung        {s.wurzel}", f"Datenbank       {s.db_pfad}",
              f"Festgeschrieben {'ja – ' + str(s.spezialordner / 'einstellungen.toml') if s.festgeschrieben else 'nein'}",
              "", "Vollständig (TOML):", "", es.toml(s.einstellungen)]
        self.query_one("#text", Static).update("\n".join(z))
        self.query_one("#spezial", Button).disabled = not s.spezialordner.is_dir()

    def on_button_pressed(self, ev: Button.Pressed):
        oeffnen(self.s.datenordner if ev.button.id == "daten" else self.s.spezialordner)

