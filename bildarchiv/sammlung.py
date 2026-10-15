"""Eine Sammlung: Wurzelordner, Einstellungen, Arbeitsdatenbank und Speicherorte.

Vor dem ersten Ausführen wird in der Sammlung nichts geschrieben: Einstellungen und Berichte liegen
zunächst im Datenordner (~/.local/share/bildarchiv/<id>/). Erst beim Ausführen entsteht
<Sammlung>/_bildarchiv/ mit Einstellungen, Berichten und einer Sicherung der Datenbank.
"""

import os
import shutil
import sqlite3
import time
import tomllib
import uuid
from datetime import datetime
from pathlib import Path

from . import einstellungen as es
from .kern import db as kdb

DATEN = Path(os.environ.get("BILDARCHIV_DATEN") or Path.home() / ".local" / "share" / "bildarchiv")
KONFIG = Path(os.environ.get("BILDARCHIV_KONFIG") or Path.home() / ".config" / "bildarchiv")
SPEZIAL = "_bildarchiv"


def datenordner():
    return Path(os.environ.get("BILDARCHIV_DATEN") or DATEN)


def konfigordner():
    return Path(os.environ.get("BILDARCHIV_KONFIG") or KONFIG)


# --------------------------------------------------------------------------- Liste der Sammlungen

def _registry_pfad():
    return konfigordner() / "sammlungen.toml"


def registry():
    p = _registry_pfad()
    if not p.exists():
        return []
    with open(p, "rb") as fh:
        return tomllib.load(fh).get("sammlung", [])


def _registry_schreiben(eintraege):
    p = _registry_pfad()
    p.parent.mkdir(parents=True, exist_ok=True)
    zeilen = ["# zuletzt geöffnete Sammlungen", ""]
    for e in eintraege:
        zeilen.append("[[sammlung]]")
        for k in ("pfad", "id", "name", "zuletzt"):
            zeilen.append(f"{k} = {es._wert(str(e.get(k, '')))}")
        zeilen.append("")
    p.write_text("\n".join(zeilen), encoding="utf-8")


def _registry_merken(pfad, sid, name):
    eintraege = [e for e in registry() if e.get("pfad") != pfad and e.get("id") != sid]
    eintraege.insert(0, dict(pfad=pfad, id=sid, name=name, zuletzt=datetime.now().strftime("%Y-%m-%d %H:%M")))
    _registry_schreiben(eintraege[:30])


def registry_entfernen(pfad):
    _registry_schreiben([e for e in registry() if e.get("pfad") != pfad])


# --------------------------------------------------------------------------- Sammlung

class Sammlung:
    def __init__(self, wurzel):
        self.wurzel = str(Path(wurzel).expanduser().resolve())
        if not os.path.isdir(self.wurzel):
            raise FileNotFoundError(f"Ordner nicht gefunden: {self.wurzel}")
        datei = self.spezialordner / "einstellungen.toml"
        e_datei = es.laden(datei) if datei.exists() else None
        sid = (e_datei or {}).get("sammlung", {}).get("id")
        if not sid:
            sid = next((e["id"] for e in registry() if e.get("pfad") == self.wurzel), None)
        self.id = sid or uuid.uuid4().hex[:12]
        self.datenordner = datenordner() / self.id
        self.datenordner.mkdir(parents=True, exist_ok=True)
        self.db_pfad = self.datenordner / "sammlung.db"
        if not self.db_pfad.exists():
            self._sicherung_herstellen()
        self.db = kdb.oeffne(self.db_pfad)
        kdb.meta_set(self.db, "wurzel", self.wurzel)
        self.neu_laden()
        self.db.commit()
        _registry_merken(self.wurzel, self.id, self.name)

    def neu_laden(self):
        """Einstellungen (neu) laden: festgeschriebene Datei in der Sammlung, sonst Arbeitsdatenbank."""
        datei = self.spezialordner / "einstellungen.toml"
        e_datei = es.laden(datei) if datei.exists() else None
        gespeichert = kdb.meta_json(self.db, "einstellungen")
        if e_datei and e_datei["sammlung"].get("festgeschrieben"):
            self.einstellungen = e_datei
        elif gespeichert:
            self.einstellungen = es.aus_dict(gespeichert)
        elif e_datei:
            self.einstellungen = e_datei
        else:
            self.einstellungen = es.standard()
        s = self.einstellungen["sammlung"]
        s["id"] = self.id
        s["name"] = s.get("name") or os.path.basename(self.wurzel) or self.wurzel

    # ------------------------------------------------------------------ Orte

    @property
    def spezialordner(self):
        return Path(self.wurzel) / SPEZIAL

    @property
    def name(self):
        return self.einstellungen["sammlung"]["name"]

    @property
    def festgeschrieben(self):
        return bool(self.einstellungen["sammlung"].get("festgeschrieben"))

    def berichtordner(self, art):
        p = self.datenordner / "berichte" / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{art}"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def journale(self):
        """Alle Journale (neueste zuerst): Datenordner und Sammlung."""
        orte = [self.datenordner / "berichte", self.spezialordner / "berichte"]
        gefunden = {}
        for o in orte:
            if o.is_dir():
                for p in o.rglob("journal_*.csv"):
                    gefunden.setdefault(p.name, p)
        return sorted(gefunden.values(), key=lambda p: p.name, reverse=True)

    # ------------------------------------------------------------------ Einstellungen

    def speichere_einstellungen(self):
        kdb.meta_set_json(self.db, "einstellungen", self.einstellungen)
        kdb.meta_set(self.db, "einstellungen_geaendert", str(time.time()))
        self.db.commit()
        if self.festgeschrieben:
            es.speichern(self.einstellungen, self.spezialordner / "einstellungen.toml")

    def festschreiben(self):
        self.einstellungen["sammlung"]["festgeschrieben"] = True
        self.speichere_einstellungen()

    # ------------------------------------------------------------------ Datenbank sichern

    def sichere_datenbank(self, stempel):
        ziel = self.spezialordner / "datenbank" / f"sammlung_{stempel}.db"
        ziel.parent.mkdir(parents=True, exist_ok=True)
        dst = sqlite3.connect(ziel)
        self.db.backup(dst)
        dst.close()
        # nur die letzten fünf Sicherungen behalten
        alte = sorted(ziel.parent.glob("sammlung_*.db"))[:-5]
        for p in alte:
            p.unlink()
        return ziel

    def _sicherung_herstellen(self):
        ordner = self.spezialordner / "datenbank"
        if ordner.is_dir():
            kandidaten = sorted(ordner.glob("sammlung_*.db"))
            if kandidaten:
                shutil.copy2(kandidaten[-1], self.db_pfad)

    def schliessen(self):
        self.db.close()
