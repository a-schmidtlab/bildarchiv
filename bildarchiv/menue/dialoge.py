"""Wiederverwendbare Dialoge: Bestätigen, Texteingabe, Ordnerwahl, Liste, Platzhalter, Job-Fortschritt."""

import os
import subprocess
import time
from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import (Button, DataTable, DirectoryTree, Footer, Input, Label, OptionList, ProgressBar,
                             RichLog, Static)
from textual.widgets.option_list import Option

from .. import ablauf, jobs
from ..kern import vorlage as kv


def T(s):
    """Zelle als reiner Text (Pfade und Vorlagen enthalten eckige Klammern)."""
    return Text("" if s is None else str(s))


def oeffnen(pfad):
    """Datei oder Ordner mit dem Standardprogramm öffnen."""
    try:
        subprocess.Popen(["xdg-open", str(pfad)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        return True
    except OSError:
        return False


class Bestaetigen(ModalScreen):
    """Ja/Nein; optional muss ein Text eingetippt werden."""
    BINDINGS = [Binding("escape", "abbrechen", "Abbrechen")]

    def __init__(self, titel, text, eingabe=None, ja="Ja", gefaehrlich=False):
        super().__init__()
        self.titel, self.text, self.eingabe, self.ja, self.gefaehrlich = titel, text, eingabe, ja, gefaehrlich

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.titel, classes="dialogtitel", markup=False)
            with VerticalScroll(classes="dialogscroll"):
                yield Static(self.text, markup=False, classes="dialogtext")
            if self.eingabe:
                yield Label(f"Zur Bestätigung „{self.eingabe}“ eintippen:", markup=False)
                yield Input(id="eingabe")
            with Horizontal(classes="knoepfe"):
                yield Button(self.ja, id="ja", variant="error" if self.gefaehrlich else "primary",
                             disabled=bool(self.eingabe))
                yield Button("Abbrechen", id="nein")

    def on_input_changed(self, ev: Input.Changed):
        self.query_one("#ja", Button).disabled = ev.value.strip() != self.eingabe

    def on_button_pressed(self, ev: Button.Pressed):
        self.dismiss(ev.button.id == "ja")

    def action_abbrechen(self):
        self.dismiss(False)


class Eingabe(ModalScreen):
    BINDINGS = [Binding("escape", "abbrechen", "Abbrechen")]

    def __init__(self, titel, wert="", hinweis=""):
        super().__init__()
        self.titel, self.wert, self.hinweis = titel, wert, hinweis

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.titel, classes="dialogtitel", markup=False)
            if self.hinweis:
                yield Static(self.hinweis, markup=False)
            yield Input(self.wert, id="eingabe")
            with Horizontal(classes="knoepfe"):
                yield Button("OK", id="ok", variant="primary")
                yield Button("Abbrechen", id="nein")

    def on_input_submitted(self, ev):
        self.dismiss(ev.value)

    def on_button_pressed(self, ev: Button.Pressed):
        self.dismiss(self.query_one(Input).value if ev.button.id == "ok" else None)

    def action_abbrechen(self):
        self.dismiss(None)


class NurOrdner(DirectoryTree):
    def filter_paths(self, paths):
        return [p for p in paths if p.is_dir() and not p.name.startswith(".")]


class OrdnerWahl(ModalScreen):
    BINDINGS = [Binding("escape", "abbrechen", "Abbrechen")]

    def __init__(self, titel, start=None):
        super().__init__()
        self.titel = titel
        self.start = start if start and os.path.isdir(start) else (
            "/media" if os.path.isdir("/media") else str(Path.home()))

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog gross"):
            yield Label(self.titel, classes="dialogtitel", markup=False)
            yield Input(self.start, id="pfad", placeholder="Pfad eingeben oder unten auswählen")
            yield NurOrdner(self.start, id="baum")
            with Horizontal(classes="knoepfe"):
                yield Button("Diesen Ordner wählen", id="ok", variant="primary")
                yield Button("Eine Ebene höher", id="hoch")
                yield Button("Abbrechen", id="nein")

    def on_directory_tree_directory_selected(self, ev: DirectoryTree.DirectorySelected):
        self.query_one("#pfad", Input).value = str(ev.path)

    def on_input_submitted(self, ev: Input.Submitted):
        if os.path.isdir(ev.value):
            self.query_one("#baum", NurOrdner).path = ev.value

    def on_button_pressed(self, ev: Button.Pressed):
        pfad = self.query_one("#pfad", Input).value.strip()
        if ev.button.id == "hoch":
            neu = os.path.dirname(pfad.rstrip("/")) or "/"
            self.query_one("#pfad", Input).value = neu
            self.query_one("#baum", NurOrdner).path = neu
        elif ev.button.id == "ok":
            if os.path.isdir(pfad):
                self.dismiss(os.path.abspath(os.path.expanduser(pfad)))
            else:
                self.app.notify("Ordner nicht gefunden", severity="error")
        else:
            self.dismiss(None)

    def action_abbrechen(self):
        self.dismiss(None)


class Liste(Screen):
    """Tabelle mit Filter. zeilen: Liste von Tupeln. auswahl(zeile) wird bei Enter aufgerufen."""
    BINDINGS = [Binding("escape", "app.pop_screen", "Zurück"), Binding("/", "filter", "Filter"),
                Binding("o", "oeffnen", "Datei öffnen")]

    def __init__(self, titel, spalten, zeilen, wurzel=None, pfadspalte=None, max_zeilen=20000):
        super().__init__()
        self.titel, self.spalten, self.zeilen = titel, spalten, list(zeilen)
        self.wurzel, self.pfadspalte, self.max_zeilen = wurzel, pfadspalte, max_zeilen

    def compose(self) -> ComposeResult:
        yield Label(f"{self.titel}  ({len(self.zeilen)})", classes="titel", markup=False)
        yield Input(placeholder="Filter (Teil des Textes) …", id="filter")
        yield DataTable(cursor_type="row", zebra_stripes=True, id="tabelle")
        yield Footer()

    def on_mount(self):
        t = self.query_one(DataTable)
        for s in self.spalten:
            t.add_column(s)
        self._fuellen("")
        t.focus()

    def _fuellen(self, f):
        t = self.query_one(DataTable)
        t.clear()
        f = f.lower()
        n = 0
        for z in self.zeilen:
            if f and not any(f in str(x).lower() for x in z):
                continue
            t.add_row(*[T(x) for x in z])
            n += 1
            if n >= self.max_zeilen:
                break

    def on_input_changed(self, ev: Input.Changed):
        self._fuellen(ev.value)

    def action_filter(self):
        self.query_one("#filter", Input).focus()

    def action_oeffnen(self):
        if self.wurzel is None or self.pfadspalte is None:
            return
        t = self.query_one(DataTable)
        if t.row_count:
            zeile = t.get_row_at(t.cursor_row)
            oeffnen(os.path.join(self.wurzel, str(zeile[self.pfadspalte])))


class PlatzhalterHilfe(ModalScreen):
    BINDINGS = [Binding("escape", "abbrechen", "Abbrechen")]

    def compose(self) -> ComposeResult:
        opts = []
        for name, text in kv.BESCHREIBUNG.items():
            anzeige = "kürzel" if name == "kuerzel" else name
            opts.append(Option(Text(f"{{{anzeige}}}".ljust(16) + text), id="{" + anzeige + "}"))
        for f, text in (("|slug", "Umlaute → ae, Leerzeichen → -"), ("|klein", "kleinschreiben"),
                        ("|gross", "großschreiben"), ("|max:30", "auf 30 Zeichen kürzen"),
                        ("|oder:unbekannt", "Ersatz, wenn der Wert fehlt")):
            opts.append(Option(Text(f"{f}".ljust(16) + text), id=f))
        opts.append(Option(Text("[ … ]".ljust(16) + "optionaler Teil, entfällt wenn leer"), id="[_]"))
        with Vertical(classes="dialog gross"):
            yield Label("Platzhalter und Filter (Enter fügt ein)", classes="dialogtitel")
            yield OptionList(*opts, markup=False)

    def on_option_list_option_selected(self, ev: OptionList.OptionSelected):
        self.dismiss(ev.option.id)

    def action_abbrechen(self):
        self.dismiss(None)


class JobAnzeige(ModalScreen):
    """Fortschritt eines Hintergrund-Jobs. Kann geschlossen werden, der Job läuft weiter."""
    BINDINGS = [Binding("escape", "hintergrund", "Im Hintergrund weiter")]
    ENDE = {"fertig", "fehler", "abgebrochen", "abgestuerzt"}

    def __init__(self, sammlung, jid):
        super().__init__()
        self.s, self.jid = sammlung, jid
        self.log_ab = 0
        self.status = None

    def compose(self) -> ComposeResult:
        j = jobs.job(self.s.db, self.jid)
        with Vertical(classes="dialog gross"):
            yield Label(ablauf.NAMEN.get(j["schritt"], j["schritt"]), classes="dialogtitel", id="titel")
            yield Label("", id="phase", markup=False)
            yield ProgressBar(id="balken", show_eta=False)
            yield RichLog(id="log", markup=False, wrap=True, max_lines=2000)
            with Horizontal(classes="knoepfe"):
                yield Button("Im Hintergrund weiter", id="hintergrund")
                yield Button("Abbrechen", id="abbrechen", variant="error",
                             disabled=j["schritt"] in ablauf.OHNE_ABBRUCH)
                yield Button("Schließen", id="schliessen", variant="primary", disabled=True)

    def on_mount(self):
        self.timer = self.set_interval(0.4, self.abfragen)
        self.abfragen()

    def abfragen(self):
        db = self.s.db
        j = jobs.job(db, self.jid)
        if not j:
            return
        if j["status"] in ("wartet", "laeuft", "abbrechen") and not jobs._lebt(j["pid"]) and j["pid"]:
            jobs.laufender(db)      # markiert abgestürzte Jobs
            j = jobs.job(db, self.jid)
        fertig, gesamt = j["fertig"] or 0, j["gesamt"]
        dauer = (j["beendet"] or time.time()) - (j["gestartet"] or time.time())
        self.query_one("#phase", Label).update(f"{j['phase'] or 'startet …'}   ({dauer:.0f} s)")
        balken = self.query_one("#balken", ProgressBar)
        if gesamt:
            balken.update(total=gesamt, progress=fertig)
        else:
            balken.update(total=None)
        log = self.query_one("#log", RichLog)
        for lid, _, stufe, text in jobs.log(db, self.jid, self.log_ab):
            self.log_ab = lid
            pre = {"phase": "▸ ", "warnung": "! ", "fehler": "✗ "}.get(stufe, "  ")
            log.write(Text(pre + text, style={"fehler": "bold red", "warnung": "yellow",
                                               "phase": "bold"}.get(stufe, "")))
        if j["status"] in self.ENDE:
            self.timer.stop()
            self.status = j["status"]
            texte = {"fertig": "Fertig.", "fehler": "Mit Fehler beendet.", "abgebrochen": "Abgebrochen.",
                     "abgestuerzt": "Der Prozess wurde unerwartet beendet (siehe Protokoll im Datenordner)."}
            self.query_one("#phase", Label).update(texte[j["status"]] + f"   ({dauer:.0f} s)")
            if j["status"] == "fertig":
                balken.update(total=1, progress=1)
            self.query_one("#schliessen", Button).disabled = False
            self.query_one("#abbrechen", Button).disabled = True
            self.query_one("#hintergrund", Button).disabled = True
            self.query_one("#schliessen", Button).focus()

    def on_button_pressed(self, ev: Button.Pressed):
        if ev.button.id == "abbrechen":
            try:
                jobs.abbrechen(self.s.db, self.jid)
                self.app.notify("Abbruch angefordert …")
            except jobs.JobFehler as err:
                self.app.notify(str(err), severity="warning")
        elif ev.button.id == "schliessen":
            self.dismiss(self.status)
        else:
            self.dismiss(None)

    def action_hintergrund(self):
        self.dismiss(self.status)
