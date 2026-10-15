"""Zielstruktur: Jahresordner, Monats- und Ereignisordner."""

import re
from collections import defaultdict

from .datierung import ereignis_name
from .vorlage import Vorlage, slug


def spezialpruefer(einst):
    """Funktion pfad -> bool: liegt die Datei in einem Ordner des Werkzeugs (_Dubletten, _Sonstiges …)?"""
    st, du = einst["struktur"], einst["dubletten"]
    oben = {x.lower() for x in (du["ordner"], st["sonstiges"], st["muellordner"], "_bildarchiv")}
    if st["videos"] == "eigener_ordner":
        oben.add(st["videoordner"].lower())
    tief = {du["ordner"].lower(), st["c1_trashordner"].lower()}

    def ist_spezial(pfad):
        parts = pfad.lower().split("/")
        if len(parts) > 1 and parts[0] in oben:
            return True
        return any(p in tief for p in parts[1:-1])
    return ist_spezial


class Struktur:
    def __init__(self, einst, top_ordner=(), unterordner=None):
        """top_ordner: vorhandene Ordner an der Wurzel; unterordner: {top: [namen]}."""
        s = einst["struktur"]
        self.art = s["art"]
        self.jv = Vorlage(s["jahresordner"])
        self.mv = Vorlage(s["monatsordner"])
        self.ev = Vorlage(s["ereignisordner"])
        self.ev_muster = self.ev.muster()
        self.muster = re.compile(s["jahresordner_muster"]) if s.get("jahresordner_muster") else self.jv.muster()
        self.jahre = {}
        for name in sorted(top_ordner):
            y = self.jahr_von(name)
            if y and y not in self.jahre:
                self.jahre[y] = name
        self.neue_jahre = set()
        self.unterordner = defaultdict(set, {k: set(v) for k, v in (unterordner or {}).items()})
        self._ereignis = {}          # (jahr, schluessel) -> Ordnername
        self._vergeben = defaultdict(set)

    def jahr_von(self, name):
        m = self.muster.match(name)
        if not m:
            return None
        g = m.groupdict()
        y = g.get("jahr") or g.get("datum_jjjj")
        return int(y) if y else None

    def jahresordner(self, jahr):
        if jahr not in self.jahre:
            self.jahre[jahr] = self.jv.render({"jahr": str(jahr), "datum": f"{jahr}0000"})
            self.neue_jahre.add(self.jahre[jahr])
        return self.jahre[jahr]

    def monatsordner(self, jahr, datum):
        mon = datum[4:6]
        if mon == "00":
            return None
        return self.mv.render({"jahr": str(jahr), "monat": mon, "datum": datum})

    def ereignisordner(self, jahr, schluessel, name, datum):
        """Ordnername für ein Ereignis in einem Jahr. Vorhandene, schon passende Ordner werden weiterverwendet."""
        k = (jahr, schluessel)
        if k in self._ereignis:
            return self._ereignis[k]
        y = self.jahresordner(jahr)
        sauber = ereignis_name(name)
        gefunden = None
        for vorhanden in sorted(self.unterordner.get(y, ())):
            r = self.ev.lies(vorhanden, muster=self.ev_muster)
            if r is not None and slug(ereignis_name(r.get("ereignis") or vorhanden)).lower() == slug(sauber).lower():
                gefunden = vorhanden
                break
        if not gefunden:
            basis = self.ev.render({"jahr": str(jahr), "datum": datum, "ereignis": sauber,
                                    "monat": datum[4:6], "tag": datum[6:8]}) or sauber
            gefunden, i = basis, 1
            belegt = {x.lower() for x in self.unterordner.get(y, ())} | self._vergeben[y]
            while gefunden.lower() in belegt:
                i += 1
                gefunden = f"{basis} ({i})"
        self._vergeben[y].add(gefunden.lower())
        self._ereignis[k] = gefunden
        return gefunden

    def zielordner(self, jahr, datum, ereignis=None):
        """ereignis: (schluessel, name, datum_min) oder None."""
        y = self.jahresordner(jahr)
        if self.art == "jahr_monat":
            m = self.monatsordner(jahr, datum)
            return f"{y}/{m}" if m else y
        if self.art == "jahr_ereignis" and ereignis:
            return f"{y}/{self.ereignisordner(jahr, *ereignis)}"
        return y
