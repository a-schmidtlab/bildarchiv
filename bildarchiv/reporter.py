"""Fortschritt und Meldungen: Konsole, Datenbank (für das Menü) oder still (Tests)."""

import sys
import time


class Abbruch(Exception):
    """Der Lauf wurde auf Wunsch abgebrochen."""


class Reporter:
    def __init__(self):
        self.meldungen = []

    def phase(self, text, gesamt=None):
        pass

    def fortschritt(self, fertig, gesamt=None):
        pass

    def meldung(self, text, stufe="info"):
        self.meldungen.append((stufe, text))

    def pruefe_abbruch(self):
        pass


class KonsolenReporter(Reporter):
    def __init__(self, stream=sys.stdout):
        super().__init__()
        self.stream = stream
        self._letzte = 0
        self._phase = ""
        self._t0 = time.time()
        self._offen = False

    def _zeilenende(self):
        if self._offen:
            self.stream.write("\n")
            self._offen = False

    def phase(self, text, gesamt=None):
        self._zeilenende()
        self._phase = text
        self._t0 = time.time()
        self.stream.write(f"{text} …\n")
        self.stream.flush()

    def fortschritt(self, fertig, gesamt=None):
        jetzt = time.time()
        if jetzt - self._letzte < 0.3 and fertig != gesamt:
            return
        self._letzte = jetzt
        g = f"/{gesamt}" if gesamt else ""
        self.stream.write(f"\r  {fertig}{g}  ({jetzt - self._t0:.0f} s)")
        self.stream.flush()
        self._offen = True

    def meldung(self, text, stufe="info"):
        super().meldung(text, stufe)
        self._zeilenende()
        pre = {"warnung": "ACHTUNG: ", "fehler": "FEHLER: "}.get(stufe, "")
        self.stream.write(f"{pre}{text}\n")
        self.stream.flush()


class DbReporter(Reporter):
    """Schreibt Fortschritt in die Tabelle jobs, Meldungen in job_log."""

    def __init__(self, db, job_id):
        super().__init__()
        self.db, self.job = db, job_id
        self._letzte = 0
        self._phase = ""
        self._gesamt = None

    def _abbruch_gewuenscht(self):
        r = self.db.execute("SELECT status FROM jobs WHERE id=?", (self.job,)).fetchone()
        return r and r[0] == "abbrechen"

    def phase(self, text, gesamt=None):
        self._phase, self._gesamt = text, gesamt
        self.db.execute("UPDATE jobs SET phase=?, fertig=0, gesamt=? WHERE id=?", (text, gesamt, self.job))
        self.db.execute("INSERT INTO job_log (job, zeit, stufe, text) VALUES (?,?,?,?)",
                        (self.job, time.time(), "phase", text))
        self.db.commit()
        self.pruefe_abbruch()

    def fortschritt(self, fertig, gesamt=None):
        jetzt = time.time()
        if jetzt - self._letzte < 0.5 and fertig != gesamt:
            return
        self._letzte = jetzt
        self.db.execute("UPDATE jobs SET fertig=?, gesamt=coalesce(?, gesamt) WHERE id=?",
                        (fertig, gesamt, self.job))
        self.db.commit()
        self.pruefe_abbruch()

    def meldung(self, text, stufe="info"):
        super().meldung(text, stufe)
        self.db.execute("INSERT INTO job_log (job, zeit, stufe, text) VALUES (?,?,?,?)",
                        (self.job, time.time(), stufe, text))
        self.db.commit()

    def pruefe_abbruch(self):
        if self._abbruch_gewuenscht():
            raise Abbruch()
