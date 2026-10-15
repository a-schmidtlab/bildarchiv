"""Menü (Textual): Sammlung wählen, Hauptmenü, Schritte."""

import os

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Footer, Label, OptionList, Static
from textual.widgets.option_list import Option

from .. import ablauf, jobs
from .. import sammlung as sm
from .dialoge import JobAnzeige, OrdnerWahl
from .status import menue as menue_status
from .status import zeit

CSS = """
Screen { background: $surface; }
.titel { text-style: bold; padding: 0 1; background: $primary-background; width: 100%; }
.untertitel { color: $text-muted; padding: 0 1; }
.abschnitt { text-style: bold; padding: 1 1 0 1; }
.hinweis { color: $text-muted; padding: 0 1; }
.fehler { color: $error; padding: 0 1; }
.warnung { color: $warning; padding: 0 1; }
.ok { color: $success; padding: 0 1; }
.dialog { width: 80; max-width: 95%; height: auto; max-height: 90%; border: thick $primary; background: $panel;
          padding: 1 2; }
.dialog.gross { width: 110; height: 85%; }
ModalScreen { align: center middle; }
.dialogtitel { text-style: bold; padding-bottom: 1; }
.dialogtext { padding-bottom: 1; }
.dialogscroll { height: auto; max-height: 20; }
.knoepfe { height: auto; padding-top: 1; }
.knoepfe Button { margin-right: 2; }
#hauptliste { height: auto; max-height: 100%; border: none; }
#jobzeile { dock: bottom; height: auto; padding: 0 1; background: $boost; }
#log { height: 1fr; border: round $primary-lighten-2; }
#baum { height: 1fr; }
DataTable { height: 1fr; }
#vorschau.namen { height: auto; max-height: 14; margin: 0 1; }
.formular { height: auto; padding: 0 1; }
.feld { height: auto; }
.feld Label { width: 26; padding-top: 1; }
.feld Input { width: 1fr; }
.feld Select { width: 40; }
.meldungen { height: auto; padding: 0 1; }
.vorschau { height: auto; border: round $primary-lighten-2; padding: 0 1; margin: 1 1 0 1; }
.spalten { height: 1fr; }
.links { width: 3fr; }
.rechts { width: 2fr; }
"""

EINTRAEGE = [
    ("analyse", "1", "Analyse"),
    ("struktur", "2", "Zielstruktur festlegen"),
    ("dubletten", "3", "Dubletten prüfen"),
    ("namen", "4", "Metadaten & Dateinamen"),
    ("vorschau", "5", "Vorschau (Dry-Run)"),
    ("ausfuehren", "6", "Ausführen"),
    ("-", "", ""),
    ("import", "7", "Ordner importieren …"),
    ("rueckgaengig", "8", "Rückgängig machen …"),
    ("einstellungen", "9", "Einstellungen ansehen"),
    ("wechseln", "0", "Andere Sammlung öffnen"),
]


class Start(Screen):
    """Sammlung wählen: zuletzt geöffnete oder neuer Ordner."""
    BINDINGS = [Binding("q", "app.quit", "Beenden"), Binding("n", "neu", "Ordner wählen")]

    def compose(self) -> ComposeResult:
        yield Label("bildarchiv – Sammlung öffnen", classes="titel")
        yield Static("Eine Sammlung ist ein Ordner mit Bildern, der geordnet werden soll oder schon geordnet ist. "
                     "Bis zum Ausführen wird darin nichts verändert.", classes="hinweis")
        opts = [Option(Text("＋ Ordner wählen …"), id="__neu__")]
        for e in sm.registry():
            da = "" if os.path.isdir(e["pfad"]) else "   (nicht erreichbar)"
            opts.append(Option(Text(f"{e['name']:30} {e['pfad']}{da}"), id=e["pfad"]))
        yield OptionList(*opts, id="liste", markup=False)
        yield Footer()

    def on_option_list_option_selected(self, ev: OptionList.OptionSelected):
        if ev.option.id == "__neu__":
            self.action_neu()
        elif os.path.isdir(ev.option.id):
            self.app.oeffne(ev.option.id)
        else:
            self.app.notify("Der Ordner ist nicht erreichbar (Platte nicht eingehängt?)", severity="error")

    def action_neu(self):
        self.app.push_screen(OrdnerWahl("Sammlung wählen"), lambda p: p and self.app.oeffne(p))


class Hauptmenue(Screen):
    BINDINGS = [Binding("q", "app.quit", "Beenden"), Binding("j", "job", "Laufender Job")] + [
        Binding(k, f"waehle('{i}')", show=False) for i, k, _ in EINTRAEGE if k]

    def compose(self) -> ComposeResult:
        s = self.app.sammlung
        yield Label(f"bildarchiv – {s.name}", classes="titel", markup=False)
        yield Label(s.wurzel, classes="untertitel", markup=False)
        yield OptionList(id="hauptliste", markup=False)
        yield Static("", id="jobzeile", markup=False)
        yield Footer()

    def on_mount(self):
        self.aktualisieren()
        self.set_interval(1.0, self._job_zeile)

    def on_screen_resume(self):
        self.aktualisieren()

    def aktualisieren(self):
        s = self.app.sammlung
        st = menue_status(s)
        ol = self.query_one("#hauptliste", OptionList)
        markiert = ol.highlighted
        ol.clear_options()
        for i, k, text in EINTRAEGE:
            if i == "-":
                ol.add_option(None)
                continue
            zeichen, info = st.get(i, (" ", ""))
            zeile = Text(f" {k}  {text:30}")
            zeile.append(f"{zeichen}  ", style="bold")
            zeile.append(info, style="dim")
            ol.add_option(Option(zeile, id=i, disabled=False))
        ol.highlighted = markiert if markiert is not None else 0
        ol.focus()
        self._job_zeile()

    def _job_zeile(self):
        j = jobs.laufender(self.app.sammlung.db)
        z = self.query_one("#jobzeile", Static)
        if j:
            g = f"{j['fertig'] or 0}/{j['gesamt']}" if j["gesamt"] else ""
            z.update(f"Läuft: {ablauf.NAMEN.get(j['schritt'], j['schritt'])} – {j['phase'] or ''} {g}   (j: anzeigen)")
            z.display = True
        else:
            z.display = False
            if getattr(self, "_lief", False):
                self._lief = False
                self.app.sammlung.neu_laden()
                self.aktualisieren()
        if j:
            self._lief = True

    def on_option_list_option_selected(self, ev: OptionList.OptionSelected):
        self.action_waehle(ev.option.id)

    def action_job(self):
        j = jobs.laufender(self.app.sammlung.db) or jobs.letzter(self.app.sammlung.db)
        if j:
            self.app.zeige_job(j["id"])

    def action_waehle(self, eintrag):
        from . import schritte
        if eintrag == "wechseln":
            self.app.switch_screen(Start())
            return
        screens = {
            "analyse": schritte.Analyse, "struktur": schritte.Struktur, "dubletten": schritte.Dubletten,
            "namen": schritte.Namen, "vorschau": schritte.Vorschau, "ausfuehren": schritte.Ausfuehren,
            "import": schritte.Import, "rueckgaengig": schritte.Rueckgaengig,
            "einstellungen": schritte.Einstellungen,
        }
        if eintrag in screens:
            self.app.push_screen(screens[eintrag]())


class BildarchivApp(App):
    TITLE = "bildarchiv"
    CSS = CSS
    BINDINGS = [Binding("ctrl+q", "quit", "Beenden", show=False)]

    def __init__(self, ordner=None, jobs_anzahl=None):
        super().__init__()
        self.ordner = ordner
        self.sammlung = None
        self.jobs_anzahl = jobs_anzahl or max(2, min(6, (os.cpu_count() or 4)))

    def on_mount(self):
        probleme = ablauf.pruefe_umgebung()
        if probleme:
            self.notify("\n".join(probleme), severity="error", timeout=30)
        if self.ordner and os.path.isdir(self.ordner):
            self.oeffne(self.ordner)
        else:
            if self.ordner:
                self.notify(f"Ordner nicht gefunden: {self.ordner}", severity="error")
            self.push_screen(Start())

    def oeffne(self, pfad):
        try:
            neu = sm.Sammlung(pfad)
        except (FileNotFoundError, OSError) as err:
            self.notify(str(err), severity="error")
            return
        if self.sammlung:
            self.sammlung.schliessen()
        self.sammlung = neu
        while len(self.screen_stack) > 1:
            self.pop_screen()
        self.push_screen(Hauptmenue())
        j = jobs.laufender(neu.db)
        if j:
            self.zeige_job(j["id"])

    def starte_job(self, schritt, args=None, danach=None):
        """Schritt im Hintergrund starten und Fortschritt zeigen. danach(status) nach dem Schließen."""
        args = dict(args or {})
        args.setdefault("jobs", self.jobs_anzahl)
        try:
            jid = jobs.starte(self.sammlung, schritt, args)
        except jobs.JobFehler as err:
            self.notify(str(err), severity="warning")
            return
        self.zeige_job(jid, danach)

    def zeige_job(self, jid, danach=None):
        def fertig(status):
            self.sammlung.neu_laden()
            if danach:
                danach(status)
            if hasattr(self.screen, "aktualisieren"):
                self.screen.aktualisieren()
        self.push_screen(JobAnzeige(self.sammlung, jid), fertig)


def starte(ordner=None):
    BildarchivApp(ordner).run()
