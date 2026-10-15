"""Plan erstellen (Dry-Run).

Zwei Modi mit derselben Pipeline:
  ordnen  – die Sammlung selbst wird in die Zielstruktur gebracht (Dateien werden verschoben)
  import  – ein fremder Ordner wird in die festgelegte Struktur übernommen (kopiert oder verschoben);
            Dubletten werden zusätzlich gegen den Bestand der Sammlung geprüft

Ablauf: einordnen → Gruppen → Altbestand → Datum → Dubletten → Partner → Ziele und Namen → Kopien/Captions
"""

import csv
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime

from . import db as kdb
from . import dubletten, hashes
from .datierung import (derive_date, ereignis_name, folder_date_year, fuehrendes_jahr, name_date, parse_dt)
from .klassifikation import DATE_PRIO, MEDIEN, ORIGINAL_DIRS, RAW_EXT, Klassifikator, norm_ext
from .struktur import Struktur, spezialpruefer
from .vorlage import ALTSCHEMATA, Text, Vorlage

# macOS legt über SMB unzulässige Zeichen als Private-Use-Zeichen ab (Samba 'fruit'-Kodierung)
SFM = str.maketrans({0xF020: '"', 0xF021: "*", 0xF022: ":", 0xF023: "<", 0xF024: ">",
                     0xF025: "?", 0xF026: "\\", 0xF027: "|", 0xF028: " ", 0xF029: "."})

SPALTEN = ["pfad", "groesse", "mtime", "art", "ext", "dto", "subsec", "createdate", "modell", "serie",
           "breite", "hoehe", "caption", "uid", "pfn", "usercomment", "datahash", "dhash", "aspect"]

BERICHTE = {
    "dubletten": ("dubletten.csv", ["Gruppe", "Rolle", "Bereich", "Datei", "Breite", "Höhe", "Aufnahme", "Hash"]),
    "jahrwechsel": ("jahrwechsel.csv", ["Datei", "Ordnerjahr", "Zieljahr", "Datumsquelle", "Datum"]),
    "ohne_datum": ("ohne_datum.csv", ["Datei", "Datumsquelle", "Datum"]),
    "ordnerdatum": ("ordnerdatum_konflikt.csv", ["Datei", "Ordnerjahr (bleibt)", "EXIF-Jahr", "Grund"]),
    "zusammengefuehrt": ("zusammengefuehrt.csv", ["Gruppe", "hinzugefügt aus anderem Unterordner"]),
    "abgelehnt": ("aehnlich_abgelehnt.csv", ["Bild A", "Bild B", "Fall"]),
    "altbestand": ("altbestand.csv", ["Datei", "Ziel", "Erkannt über"]),
    "schon_vorhanden": ("schon_vorhanden.csv", ["Datei (Quelle)", "In der Sammlung", "Art"]),
    "nicht_importiert": ("nicht_importiert.csv", ["Datei (Quelle)", "Grund"]),
    "ersetzt": ("ersetzt.csv", ["Datei (Quelle)", "ersetzt in der Sammlung", "Grund"]),
    "ordner": ("ordnerentscheidungen.csv", ["Ordner", "Entscheidung", "Ziel"]),
    "zielordner": ("zielordner.csv", ["Ordner", "Dateien"]),
}


def ordnerpfad_text(p):
    return p.translate(SFM).strip()


def streng(v):
    """Vorlage mit Datum, Kürzel (Platzhalter oder fester Buchstabentext) bzw. Laufdatum und Nummer:
    solche Namen sind unverwechselbar mit Kameranamen."""
    n = v.namen(mit_optional=False)
    buchstaben = any(isinstance(t, Text) and re.search("[A-Za-z]", t.wert) for t in v.teile)
    return "nr" in n and bool(n & {"datum", "jahr"}) and (bool(n & {"kuerzel", "lauf"}) or buchstaben)


def nr_format(v):
    return next((f.fmt for f in v.felder() if f.name == "nr"), None)


class PlanFehler(Exception):
    pass


class Planer:
    def __init__(self, sammlung, reporter, modus="ordnen", quelle=None, import_modus="kopieren", jobs=6,
                 import_ordner=None, import_ereignisnamen=None):
        if modus not in ("ordnen", "import"):
            raise ValueError(modus)
        self.s = sammlung
        self.db = sammlung.db
        self.e = sammlung.einstellungen
        self.r = reporter
        self.modus = modus
        self.jobs = jobs
        self.wurzel = sammlung.wurzel
        self.quelle = os.path.abspath(quelle) if quelle else None
        self.import_modus = import_modus
        self.bereich = "sammlung" if modus == "ordnen" else "import"
        self.quellname = os.path.basename(self.quelle.rstrip("/")) if self.quelle else ""
        self.lauf = datetime.now().strftime("%Y%m%d")
        st = self.e["struktur"]
        if modus == "ordnen":
            self.ordner = dict(st["ordner"])
            self.ereignisnamen = dict(st["ereignisnamen"])
        else:
            self.ordner = dict(import_ordner or {})
            self.ereignisnamen = dict(import_ereignisnamen or {})

        self.kl = Klassifikator(self.e["klassifikation"]["sidecars_plus"], self.e["klassifikation"]["muell_plus"])
        self.F, self.B = {}, {}
        self.actions = []
        self.report = defaultdict(list)
        self.used = set()
        self.used_stems = set()
        self.G, self.GI = {}, {}
        self.final = {}
        self.dup_files = {}         # F-Datei -> (nr, keeper)
        self.ersetzt = {}           # B-Datei -> (nr, keeper)
        self.keeper_copy = {}       # keeper -> (nr, jahr)
        self.captions = []          # (spender, keeper)
        self.anschluss = {}         # gid -> B-Schlüssel der Gruppe, an die angeschlossen wird
        self.ersetzt_ziel = {}      # F-keeper -> B-Datei, deren Platz er übernimmt
        self.dup_cluster = []
        self.wurzel_ordner = {}
        self.statistik = Counter()

    # ------------------------------------------------------------------ Hilfen

    def add(self, aktion, quelle, ziel=None, gruppe="", qb=None, **info):
        info["qb"] = qb or self.bereich
        self.actions.append((aktion, quelle, ziel, gruppe, info))

    def unique(self, path):
        """Freien Pfad finden (case-insensitiv wie exFAT), Suffix _1, _2 … vor der Endung."""
        d, name = path.rsplit("/", 1) if "/" in path else ("", path)
        base, suffix = self.kl.split_stem(name)
        if not suffix and "." in name[1:]:
            base, ext = name.rsplit(".", 1)
            suffix = "." + ext
        cand, i = path, 0
        while cand.lower() in self.used:
            i += 1
            cand = f"{d}/{base}_{i}{suffix}" if d else f"{base}_{i}{suffix}"
        self.used.add(cand.lower())
        return cand

    def _entscheidung(self, it):
        d = it["dir"]
        for k, v in self.ordner.items():
            if v in ("behalten", "wurzel") and (d == k or d.startswith(k + "/")):
                return v, k
        return self.ordner.get(it["ek"], "ereignis"), it["ek"]

    def _jahr_der_datei(self, pfad):
        top = pfad.split("/", 1)[0] if "/" in pfad else ""
        return self.struktur.jahr_von(top) if top else None

    # ------------------------------------------------------------------ Ablauf

    def erstelle(self):
        self._vorbereiten()
        self._laden()
        self._einordnen()
        self._gruppen()
        self._altbestand()
        self._datieren()
        self._dubletten()
        self._partner()
        self._ziele_und_namen()
        self._kopien_und_captions()
        return self._speichern()

    def _vorbereiten(self):
        e = self.e
        self.ist_spezial = spezialpruefer(e)

        bestand = [p for (p,) in self.db.execute("SELECT pfad FROM dateien WHERE bereich='sammlung'")]
        tops = set()
        unter = defaultdict(set)
        for p in bestand:
            parts = p.split("/")
            if len(parts) > 1:
                tops.add(parts[0])
            if len(parts) > 2:
                unter[parts[0]].add(parts[1])
        if os.path.isdir(self.wurzel):
            for ent in os.scandir(self.wurzel):
                if ent.is_dir():
                    tops.add(ent.name)
        self.struktur = Struktur(e, tops, unter)
        self.used = {p.lower() for p in bestand} | {t.lower() for t in tops}
        self.used_stems = {self.kl.split_stem(p.rsplit("/", 1)[-1])[0].lower() for p in bestand}
        self.bestand = bestand

        if self.modus == "ordnen":
            hashes.rechne(self.db, self.wurzel, "sammlung", self.r, self.jobs,
                          filter_pfad=lambda p: not self.ist_spezial(p))
        else:
            hashes.rechne(self.db, self.quelle, "import", self.r, self.jobs)
            # Bestand der Sammlung: fehlende Hashes nachholen (z. B. aus einer Vorgängerversion übernommene Sammlung)
            hashes.rechne(self.db, self.wurzel, "sammlung", self.r, self.jobs,
                          filter_pfad=lambda p: not self.ist_spezial(p))

    def _laden(self):
        self.r.phase("Plan erstellen")
        for r in kdb.dateien(self.db, self.bereich, SPALTEN):
            it = self._ableiten(r, self.bereich)
            self.F[(self.bereich, r["pfad"])] = it
        if self.modus == "import":
            for r in kdb.dateien(self.db, "sammlung", SPALTEN):
                if self.ist_spezial(r["pfad"]):
                    continue
                it = self._ableiten(r, "sammlung")
                self.B[("sammlung", r["pfad"])] = it
            # Gruppen im Bestand (für den Anschluss neuer Partner an vorhandene Namen)
            self.B_gruppen = defaultdict(set)
            for k, it in self.B.items():
                self.B_gruppen[(it["dir"].lower(), it["stem"].lower())].add(it["suffix"].lower())

    def _ableiten(self, r, bereich):
        it = dict(r)
        it["bereich"] = bereich
        parts = r["pfad"].split("/")
        it["name"] = parts[-1]
        it["dir"] = "/".join(parts[:-1])
        it["stem"], it["suffix"] = self.kl.split_stem(parts[-1])
        top = parts[0] if len(parts) > 1 else ""
        it["top"] = top
        if bereich == "sammlung":
            fy = self.struktur.jahr_von(top) if top else None
            it["in_jahresordner"] = bool(fy)
            if fy:
                it["folder_year"] = fy
                it["ek"] = f"{top}/{parts[1]}" if len(parts) > 2 else None
                it["en"] = parts[1] if len(parts) > 2 else ""
                it["sub"] = "/".join(parts[1:-1])
            else:
                it["folder_year"] = fuehrendes_jahr(top) if top else None
                it["ek"] = top or None
                it["en"] = top
                it["sub"] = "/".join(parts[:-1])
            it["ordnerpfad"] = ordnerpfad_text(it["dir"])
        else:
            it["in_jahresordner"] = False
            it["folder_year"] = (self.struktur.jahr_von(top) or fuehrendes_jahr(top)) if top else None
            it["ek"] = top
            it["en"] = top or self.quellname
            it["sub"] = "/".join(parts[:-1])
            it["ordnerpfad"] = ordnerpfad_text("/".join([self.quellname] + parts[:-1]))
        return it

    # ------------------------------------------------------------------ 1. Einordnen

    def _einordnen(self):
        st = self.e["struktur"]
        imp = self.modus == "import"
        self.groups = defaultdict(list)
        self.video_stems = {}
        for key, it in sorted(self.F.items()):
            p = it["pfad"]
            if not imp and self.ist_spezial(p):
                continue
            ent, ek = self._entscheidung(it)
            if ent == "behalten":
                self.statistik["unverändert (Ordner behalten)"] += 1
                if imp:
                    self.report["nicht_importiert"].append((p, f"Ordner '{ek}' ausgenommen"))
                continue
            if ent == "wurzel":
                if not imp:
                    self.wurzel_ordner.setdefault(ek, 0)
                    self.wurzel_ordner[ek] += 1
                continue
            it["aufloesen"] = ent == "aufloesen"
            art = it["art"]
            name = it["name"]
            if art == "muell":
                if imp:
                    self.report["nicht_importiert"].append((p, "Müll"))
                elif st["muell"] == "behalten":
                    self.statistik["Müll liegen gelassen"] += 1
                elif st["muell"] == "loeschen":
                    self.add("loeschen", p, grund="Müll")
                else:
                    self.add("verschieben", p, self.unique(f"{st['muellordner']}/{name}"), grund="Müll")
            elif art == "c1":
                if st["capture_one"] == "verwerfen":
                    if imp:
                        self.report["nicht_importiert"].append((p, "Capture One"))
                    else:
                        self.add("loeschen", p, grund="Capture One")
                else:
                    self._sonstiges(key)
            elif art == "sonstiges":
                self._sonstiges(key)
            elif art == "video" and st["videos"] == "eigener_ordner":
                self.video_stems[(it["dir"], it["stem"].lower())] = key
                self._video_eigen(key)
            elif (st["c1_trash"] == "eigener_ordner" and art in ("bild", "sidecar", "audio")
                  and it["folder_year"] and any(x.lower() == "trash" for x in p.split("/")[:-1])):
                ziel = self.unique(f"{self.struktur.jahresordner(it['folder_year'])}/{st['c1_trashordner']}/{name}")
                self._bewege(key, ziel, grund="Capture-One-Trash")
            else:
                self.groups[(it["dir"], it["stem"].lower())].append(key)

        for ek, n in sorted(self.wurzel_ordner.items()):
            ziel = self.unique(ek.rsplit("/", 1)[-1])
            self.add("ordner_verschieben", ek, ziel, anzahl=n)
            self.report["ordner"].append((ek, "an die Wurzel", ziel))
        for k, v in sorted(self.ordner.items()):
            if v not in ("wurzel",):
                self.report["ordner"].append((k, v, ""))

    def _bewege(self, key, ziel, **info):
        it = self.F[key]
        if self.modus == "import":
            self.add("importieren", it["pfad"], ziel, **info)
        else:
            self.add("verschieben", it["pfad"], ziel, **info)
        self.final[key] = ziel

    def _sonstiges(self, key):
        it = self.F[key]
        self._bewege(key, self.unique(f"{self.e['struktur']['sonstiges']}/{it['name']}"), grund="Sonstiges")

    def _video_eigen(self, key):
        it = self.F[key]
        self._bewege(key, self.unique(f"{self.e['struktur']['videoordner']}/{it['name']}"), grund="Video")

    # ------------------------------------------------------------------ 2. Gruppen

    def _media(self, key):
        return self.F[key]["art"] in MEDIEN

    def fmt_set(self, gid):
        return {self.F[m]["ext"] for m in self.G[gid] if self._media(m)}

    def suffix_set(self, gid):
        return {norm_ext(self.F[m]["suffix"].lower().lstrip(".")) for m in self.G[gid]}

    def primary_date(self, members):
        F = self.F
        media = [m for m in members if self._media(m)]

        def rank(m):
            ext = F[m]["ext"]
            if F[m]["art"] == "video":
                return len(DATE_PRIO) + 1
            if ext in RAW_EXT:
                return 0
            return DATE_PRIO.index(ext) if ext in DATE_PRIO else len(DATE_PRIO)
        media.sort(key=rank)
        for field, src in (("dto", "DateTimeOriginal"), ("createdate", "CreateDate")):
            for m in media:
                d = parse_dt(F[m][field])
                if d:
                    return d, F[m]["subsec"] or "", src
        return None, "", None

    def _gruppen(self):
        F = self.F
        st = self.e["struktur"]
        gid = 0
        for key, members in sorted(self.groups.items()):
            if any(self._media(m) for m in members):
                # gleiche Endung doppelt (IMG_1.JPG und img_1.jpg): eigene Gruppen
                seen = {}
                for m in sorted(members):
                    suf = F[m]["suffix"].lower()
                    target = seen.setdefault(suf, [])
                    target.append(m)
                rest = [ms[0] for ms in seen.values()]
                extra = [m for ms in seen.values() for m in ms[1:]]
                self.G[gid] = rest
                gid += 1
                for m in extra:
                    self.G[gid] = [m]
                    gid += 1
            else:
                vid = self.video_stems.get(key)
                for m in members:
                    if vid:
                        self._bewege(m, self.unique(f"{st['videoordner']}/{F[m]['name']}"), grund="Video-Sidecar")
                    else:
                        self._sonstiges(m)

        # RAW/JPEG (bzw. Bild/Live-Video) in verschiedenen Unterordnern desselben Ereignisses zusammenführen
        by_stem = defaultdict(list)
        for g, members in self.G.items():
            f = F[members[0]]
            by_stem[(f["top"], f["ek"], f["stem"].lower())].append(g)
        for gids in by_stem.values():
            if len(gids) < 2:
                continue
            gids.sort(key=lambda g: (-len(self.fmt_set(g)), g))
            keep = []
            for g in gids:
                d = self.primary_date(self.G[g])[0]
                for k in keep:
                    dk = self.primary_date(self.G[k])[0]
                    if not (self.fmt_set(g) & self.fmt_set(k)) and not (self.suffix_set(g) & self.suffix_set(k)) \
                            and (d is None or dk is None or d == dk):
                        self.report["zusammengefuehrt"].append((min(self.G[k])[1], min(self.G[g])[1]))
                        self.G[k].extend(self.G[g])
                        del self.G[g]
                        self.statistik["zusammengeführt"] += 1
                        break
                else:
                    keep.append(g)

    # ------------------------------------------------------------------ 3. Altbestand

    def _muster(self):
        n = self.e["namen"]
        out = []
        cur = Vorlage(n["vorlage"])
        out.append(("aktuelle Vorlage", cur, cur.muster(n["kuerzel"])))
        for t in n["frueher"]:
            try:
                v = Vorlage(t)
                out.append(("frühere Vorlage", v, v.muster(n["kuerzel"])))
            except ValueError:
                pass
        for t in ALTSCHEMATA:
            v = Vorlage(t)
            out.append(("Schema der Vorgängerversion", v, v.muster(None)))
        return out

    def _lies_name(self, stem):
        for quelle, v, rx in self.muster:
            r = v.lies(stem, muster=rx)
            if r is not None and ("nr" in r or "hash" in r):
                r["_streng"] = streng(v)
                # Nummern fremder, viel längerer Formate (z. B. AB22160115421) zählen nicht für den Zähler
                r["_zaehlt"] = "nr" in r and int(nr_format(v) or 0) <= int(self.nr_fmt or 0) + 1
                return quelle, r
        return None, None

    def _altbestand(self):
        self.muster = self._muster()
        self.nr_fmt = nr_format(Vorlage(self.e["namen"]["vorlage"]))
        F = self.F
        for g, members in self.G.items():
            stem = F[members[0]]["stem"]
            quelle, r = self._lies_name(stem)
            if r is None:
                continue
            # einfache Vorlagen könnten zufällig auf Kameranamen passen: dann muss die ImageUniqueID stimmen
            uids = {F[m]["uid"] for m in members if self._media(m) and F[m]["uid"]}
            if not r["_streng"] and uids and stem not in uids:
                continue
            if self.modus == "import" and stem.lower() in self.used_stems:
                continue    # Name schon vergeben: neu benennen
            if len({F[m]["stem"] for m in members}) > 1:
                continue
            self.GI.setdefault(g, {})["alt"] = r
            self.GI[g]["alt_quelle"] = quelle

    # ------------------------------------------------------------------ 4. Datum

    def _datieren(self):
        F = self.F
        konflikt = self.e["struktur"]["datum_konflikt"]
        for g, members in self.G.items():
            f0 = F[members[0]]
            fy = f0["folder_year"]
            gi = self.GI.setdefault(g, {})
            alt = gi.get("alt")
            d, subsec, src = self.primary_date(members)
            if alt and alt.get("datum"):
                date, tm = alt["datum"], alt.get("zeit") or (d[1] if d else "000000")
                year = fy if f0["in_jahresordner"] else int(date[:4])
                src = "Altbestand"
            elif d:
                date, tm = d
                year = int(date[:4])
                if konflikt == "ordner" and fy:
                    fdy = folder_date_year(f0["sub"])
                    if fdy and fdy != year and year != fy:
                        self.report["ordnerdatum"].append((f0["pfad"], fy, year, f"Jahr im Ordnernamen {fdy}"))
                        year = fy
                    elif src == "CreateDate" and year > fy and f0["in_jahresordner"]:
                        # nur CreateDate und jünger als der Ordner: Digitalisierung/Neuspeicherung
                        self.report["ordnerdatum"].append((f0["pfad"], fy, year, "CreateDate nach Ordnerjahr"))
                        year = fy
            else:
                texts = [F[m]["name"] for m in sorted(members)] + f0["sub"].split("/")[::-1]
                nd = name_date(texts)
                if nd and (not fy or int(nd[0][:4]) == fy):
                    (date, tm), src = nd, "Dateiname"
                elif fy:
                    date, tm, src = derive_date(texts, fy), "000000", "Ordnerjahr"
                else:
                    mt = datetime.fromtimestamp(min(F[m]["mtime"] or 0 for m in members))
                    date, tm, src = mt.strftime("%Y%m%d"), mt.strftime("%H%M%S"), "Dateidatum"
                year = int(date[:4])
                self.report["ohne_datum"].append((f0["pfad"], src, f"{date} {tm}"))
            gi.update(date=date, time=tm, subsec=subsec or "", year=year, src=src, folder_year=fy,
                      ek=f0["ek"], en=f0["en"], aufloesen=f0.get("aufloesen", False))

    # ------------------------------------------------------------------ 5. Dubletten

    def _item(self, k):
        return self.F.get(k) or self.B[k]

    def _keeper_key(self, k, media_group):
        it = self._item(k)
        if k[0] == "sammlung" and self.modus == "import":
            n_fmt = len(self.B_gruppen.get((it["dir"].lower(), it["stem"].lower()), ()))
        else:
            g = media_group.get(k)
            n_fmt = len(self.fmt_set(g)) if g is not None and g in self.G else 1
        return (bool(it["caption"]), self.modus == "import" and k[0] == "sammlung",
                it["folder_year"] is not None,
                not any(p.lower() in ORIGINAL_DIRS for p in it["pfad"].split("/")[:-1]),
                n_fmt, it["groesse"] or 0, -len(it["pfad"]), it["pfad"])

    def _dubletten(self):
        F = self.F
        media_group = {m: g for g, ms in self.G.items() for m in ms if self._media(m)}
        entsch = kdb.entscheidungen(self.db)
        keine = {k for k, a in entsch.items() if a == "keine_dublette"}
        bevorzugt = {k for k, a in entsch.items() if a == "bevorzugt"}
        items = {k: F[k] for k in media_group if k not in keine}
        if self.modus == "import":
            for k, it in self.B.items():
                if it["art"] in MEDIEN and (it["datahash"] or it["dhash"]) and k not in keine:
                    items[k] = it
        wurzeln = {"sammlung": self.wurzel, "import": self.quelle}

        def thumbs_fuer(keys):
            return hashes.thumbs(self.db, wurzeln, keys, self.r, self.jobs)

        self.r.phase("Dubletten suchen")
        cluster, abgelehnt = dubletten.finde(items, thumbs_fuer, self.e["dubletten"]["aehnliche"])
        for a, b, grund in abgelehnt:
            self.report["abgelehnt"].append((a[1], b if isinstance(b, str) else b[1], grund))
        self.statistik["ähnliche Paare verworfen"] = len(abgelehnt)

        px = lambda k: dubletten.pixels(self._item(k))
        geplant = []
        for c in cluster:
            mem = c["mitglieder"]
            # nur Bestand: geht diesen Lauf nichts an
            if all(k[0] == "sammlung" and self.modus == "import" for k in mem):
                continue
            wahl = [m for m in mem if m in bevorzugt]
            if wahl:
                keeper = wahl[0]
            else:
                top = max(px(m) for m in mem)
                keeper = max([m for m in mem if px(m) == top], key=lambda m: self._keeper_key(m, media_group))
            kg = media_group.get(keeper)
            others = [m for m in mem if m != keeper and (m not in media_group or media_group[m] != kg)]
            if not others:
                continue
            if keeper in F:
                ki = self.GI[kg]
                sort = (ki["date"], ki["time"], keeper[1])
            else:
                it = self._item(keeper)
                sort = ((parse_dt(it["dto"]) or ("99999999", ""))[0], "", keeper[1])
            geplant.append((sort, c, keeper, others))
        geplant.sort(key=lambda x: x[0])

        dz = kdb.zaehler_alle(self.db).get("dubletten", 0)
        self.dup_nr_start = dz
        for cid, (_, c, keeper, others) in enumerate(geplant, 1):
            mem = c["mitglieder"]
            top = max(px(m) for m in mem)
            keeper_in_f = keeper in F
            if keeper_in_f:
                jahr = self.GI[media_group[keeper]]["year"]
            else:
                jahr = self._jahr_der_datei(keeper[1]) or int(
                    ((parse_dt(self._item(keeper)["dto"]) or (f"{datetime.now().year}0000",))[0])[:4])
            moving = [m for m in others if (m in F and self.modus == "ordnen")
                      or (keeper_in_f and m[0] == "sammlung" and self.modus == "import")]
            nr = None
            if moving:
                dz += 1
                nr = dz
                self.keeper_copy[keeper] = (nr, jahr)
            for m in others:
                if m in F:
                    self.dup_files.setdefault(m, (nr, keeper))
                elif keeper_in_f:
                    self.ersetzt[m] = (nr, keeper)
            if keeper_in_f and self.modus == "import":
                ersetzte = [m for m in others if m[0] == "sammlung"]
                if ersetzte:
                    ziel = max(ersetzte, key=lambda m: self._keeper_key(m, media_group))
                    self.ersetzt_ziel[keeper] = ziel
            if not self._item(keeper)["caption"]:
                spender = [m for m in sorted(others, key=px, reverse=True) if self._item(m)["caption"]]
                if spender:
                    self.captions.append((spender[0], keeper))

            for m in mem:
                it = self._item(m)
                if m == keeper:
                    rolle = "bleibt" if (keeper_in_f or self.modus == "ordnen") else "vorhanden"
                elif m in F and self.modus == "import":
                    rolle = "nicht_importiert"
                elif m[0] == "sammlung" and self.modus == "import" and not keeper_in_f:
                    rolle = "vorhanden"
                elif m[0] == "sammlung" and self.modus == "import":
                    rolle = "ersetzt"
                else:
                    rolle = "kleiner" if px(m) < top else "dublette"
                if m != keeper and m not in others:
                    rolle = "partner"
                self.dup_cluster.append((cid, nr, m[0], m[1], rolle, c["art"], it["breite"], it["hoehe"],
                                         it["groesse"], it["caption"], int(c["unsicher"])))
                self.report["dubletten"].append((f"D{nr:05d}" if nr else f"#{cid}", rolle, m[0], m[1],
                                                 it["breite"], it["hoehe"], it["dto"] or "", it["datahash"] or ""))
            if self.modus == "import" and not keeper_in_f:
                for m in others:
                    if m in F:
                        self.report["schon_vorhanden"].append((m[1], keeper[1], c["art"]))
            if self.modus == "import" and keeper_in_f and keeper in self.ersetzt_ziel:
                grund = "mit Bildunterschrift" if self._item(keeper)["caption"] else "größere/bessere Version"
                self.report["ersetzt"].append((keeper[1], self.ersetzt_ziel[keeper][1], grund))
        self.dup_nr_ende = dz
        self.statistik["Dubletten-Gruppen"] = len(geplant)

    # ------------------------------------------------------------------ 6. Partner

    def _dup_pfad(self, nr, name, jahr):
        du = self.e["dubletten"]
        a, d = du["ablage"], du["ordner"]
        if a == "flach_praefix":
            p = f"{d}/D{nr:05d}_{name}"
        elif a == "gruppenordner":
            p = f"{d}/{nr:04d}/{name}"
        else:
            p = f"{self.struktur.jahresordner(jahr)}/{d}/{nr:04d}/{name}"
        return self.unique(p)

    def _als_dublette(self, m, nr, jahr):
        """Datei aus der Gruppe nehmen: ordnen -> nach _Dubletten, import -> nicht importieren."""
        it = self.F[m]
        if self.modus == "import":
            self.report["nicht_importiert"].append((it["pfad"], "Dublette"))
            return
        ziel = self._dup_pfad(nr, it["name"], jahr)
        media = self._media(m)
        self.add("dublette", it["pfad"], ziel, f"D{nr:05d}",
                 altname=it["name"] if media else None, ordnerpfad=it["ordnerpfad"] if media else None)
        self.final[m] = ziel

    def _partner(self):
        F = self.F
        # Bestand, der durch eine bessere Importversion ersetzt wird
        for m, (nr, keeper) in sorted(self.ersetzt.items()):
            it = self.B[m]
            # unter dem ursprünglichen Namen ablegen, wie beim Ordnen (der neue Name gehört jetzt der besseren Version)
            alt = it["pfn"] or it["usercomment"] or ""
            name = alt if alt and "/" not in alt and alt.lower().endswith(it["suffix"].lower()) else it["name"]
            ziel = self._dup_pfad(nr, name, self.keeper_copy[keeper][1])
            self.add("dublette", it["pfad"], ziel, f"D{nr:05d}", qb="sammlung")
            self.final[m] = ziel

        media_group = {m: g for g, ms in self.G.items() for m in ms if self._media(m)}
        for g in list(self.G):
            members = self.G[g]
            media = [m for m in members if self._media(m)]
            side = [m for m in members if not self._media(m)]
            moved = [m for m in media if m in self.dup_files]
            if not moved:
                continue
            nr, keeper = self.dup_files[moved[0]]
            jahr = self.keeper_copy.get(keeper, (None, self.GI[g]["year"]))[1]
            tied = [s for s in side if any(F[s]["suffix"].lower().startswith(F[m]["suffix"].lower() + ".")
                                           for m in moved)]
            for m in moved + tied:
                self._als_dublette(m, nr, jahr)
            remaining = [m for m in media + side if m not in moved and m not in tied]
            if not remaining:
                del self.G[g]
                continue
            rem_fmt = {F[m]["ext"] for m in remaining if self._media(m)}
            rem_suf = {norm_ext(F[m]["suffix"].lower().lstrip(".")) for m in remaining}
            kg = media_group.get(keeper)
            if keeper in F and kg in self.G and kg != g and not (rem_fmt & self.fmt_set(kg)) \
                    and not (rem_suf & self.suffix_set(kg)):
                self.report["zusammengefuehrt"].append((min(self.G[kg])[1], min(remaining)[1]))
                self.G[kg].extend(remaining)
                del self.G[g]
            elif keeper in self.B and self._passt_an(keeper, remaining):
                self.G[g] = remaining
                self.anschluss[g] = keeper
            elif rem_fmt:
                self.G[g] = remaining
            else:
                for m in remaining:
                    self._als_dublette(m, nr, jahr)
                del self.G[g]

        # Gruppen, deren bleibende Version eine Datei im Bestand ersetzt, übernehmen deren Namen
        for g, members in self.G.items():
            for m in members:
                if m in self.ersetzt_ziel:
                    self.anschluss[g] = self.ersetzt_ziel[m]

    def _passt_an(self, b_key, members):
        bi = self.B[b_key]
        vorhanden = self.B_gruppen.get((bi["dir"].lower(), bi["stem"].lower()), set())
        for m in members:
            if self.F[m]["suffix"].lower() in vorhanden:
                return False
        return True

    # ------------------------------------------------------------------ 7. Ziele und Namen

    def _scope(self, gi, zdir):
        z = self.e["namen"]["zaehler"]
        if z == "gesamt":
            return "gesamt"
        if z == "jahr":
            return f"jahr:{gi['year']}"
        if z == "monat":
            return f"monat:{gi['date'][:6]}"
        if z == "tag":
            return f"tag:{gi['date']}"
        if z == "sekunde":
            return f"sek:{gi['date']}_{gi['time']}"
        return f"ordner:{zdir.lower()}"

    def _zaehler_aus_bestand(self, zaehler):
        """Höchste vergebene Nummern aus den Namen im Bestand ablesen (z. B. Sammlung einer Vorgängerversion)."""
        for p in self.bestand:
            if self.ist_spezial(p):
                continue
            name = p.rsplit("/", 1)[-1]
            stem = self.kl.split_stem(name)[0]
            _, r = self._lies_name(stem)
            if not r or not r.get("_zaehlt"):
                continue
            datum = r.get("datum") or "00000000"
            year = self._jahr_der_datei(p) or int(datum[:4] or 0)
            gi = dict(year=year, date=datum, time=r.get("zeit") or "000000")
            k = self._scope(gi, p.rsplit("/", 1)[0] if "/" in p else "")
            zaehler[k] = max(zaehler.get(k, 0), r["nr"])

    def _ereignis(self, gi):
        if self.struktur.art != "jahr_ereignis" or gi["ek"] is None or gi.get("aufloesen"):
            return None
        name = self.ereignisnamen.get(gi["ek"]) or gi["en"]
        return gi["ek"], name, self.ev_datum.get((gi["year"], gi["ek"]), gi["date"])

    def _ziele_und_namen(self):
        F = self.F
        e = self.e
        vorlage = Vorlage(e["namen"]["vorlage"])
        hat_nr = "nr" in vorlage.namen()
        kuerzel = e["namen"]["kuerzel"]

        # Datum je Ereignis (frühestes vollständiges Datum) für den Ordnernamen
        self.ev_datum = {}
        sammel = defaultdict(list)
        for g in self.G:
            gi = self.GI[g]
            if gi["ek"] is not None:
                sammel[(gi["year"], gi["ek"])].append(gi["date"])
        for k, ds in sammel.items():
            voll = [d for d in ds if "00" not in (d[4:6], d[6:8])]
            self.ev_datum[k] = min(voll or ds)

        zaehler = dict(kdb.zaehler_alle(self.db))
        self.zaehler_alt = dict(zaehler)
        if self.modus == "import":
            # beim Ordnen zählt der Altbestand unten über seine Gruppen mit (mit Prüfung der ImageUniqueID)
            self._zaehler_aus_bestand(zaehler)

        # Altbestand: Name bleibt, Zähler läuft mit
        alt_gids, neu_gids = [], []
        for g in self.G:
            if g in self.anschluss:
                continue
            (alt_gids if self.GI[g].get("alt") else neu_gids).append(g)
        for g in alt_gids:
            gi = self.GI[g]
            zdir = self.struktur.zielordner(gi["year"], gi["date"], self._ereignis(gi))
            gi["zdir"] = zdir
            r = gi["alt"]
            if r.get("_zaehlt"):
                k = self._scope(gi, zdir)
                zaehler[k] = max(zaehler.get(k, 0), r["nr"])
        for g in sorted(alt_gids):
            gi = self.GI[g]
            zdir = gi["zdir"]
            for m in sorted(self.G[g]):
                it = F[m]
                self.final[m] = it["pfad"]
                if self.modus == "ordnen" and it["dir"].lower() == zdir.lower():
                    continue
                ziel = f"{zdir}/{it['name']}"
                if ziel.lower() in self.used and ziel.lower() != it["pfad"].lower():
                    ziel = self.unique(ziel)
                self.used.add(ziel.lower())
                self._bewege(m, ziel, grund="Altbestand")
            self.used_stems.add(F[self.G[g][0]]["stem"].lower())
            self.report["altbestand"].append((F[self.G[g][0]]["pfad"], zdir, gi.get("alt_quelle", "")))
        self.statistik["Altbestand (Name bleibt)"] = len(alt_gids)

        # Anschluss an vorhandene Namen im Bestand (Import)
        for g, b_key in sorted(self.anschluss.items()):
            bi = self.B[b_key]
            for m in sorted(self.G[g]):
                it = F[m]
                ziel = f"{bi['dir']}/{bi['stem']}{it['suffix'].lower()}" if bi["dir"] else \
                    f"{bi['stem']}{it['suffix'].lower()}"
                ersetzt = self.ersetzt_ziel.get(m) == b_key or (
                    any(self.ersetzt_ziel.get(x) == b_key for x in self.G[g])
                    and ziel.lower() == bi["pfad"].lower())
                if ziel.lower() in self.used and not ersetzt:
                    ziel = self.unique(ziel)
                self.used.add(ziel.lower())
                media = self._media(m)
                self.add("importieren", it["pfad"], ziel, f"G{g}",
                         altname=it["name"] if media else None, neuname=bi["stem"] if media else None,
                         ordnerpfad=it["ordnerpfad"] if media else None,
                         ersetzt=bi["pfad"] if ersetzt else None)
                self.final[m] = ziel

        # Neue Namen je Zählerbereich
        bereiche = defaultdict(list)
        for g in neu_gids:
            gi = self.GI[g]
            gi["zdir"] = self.struktur.zielordner(gi["year"], gi["date"], self._ereignis(gi))
            bereiche[self._scope(gi, gi["zdir"])].append(g)
        neu_zaehler = {}
        for k in sorted(bereiche):
            gids = sorted(bereiche[k], key=lambda g: (self.GI[g]["date"], self.GI[g]["time"],
                                                     self.GI[g]["subsec"], min(self.G[g])))
            nr = zaehler.get(k, 0)
            for g in gids:
                gi = self.GI[g]
                members = sorted(self.G[g])
                prim = self._primaer(members)
                werte = dict(datum=gi["date"], zeit=gi["time"], lauf=self.lauf, kuerzel=kuerzel,
                             ereignis=ereignis_name(self.ereignisnamen.get(gi["ek"]) or gi["en"] or ""),
                             ordner=F[members[0]]["top"] or self.quellname,
                             kamera=F[prim]["modell"] or "", serie=F[prim]["serie"] or "",
                             altname=F[prim]["stem"], hash=F[prim]["datahash"] or "", jahr=str(gi["year"]))
                versuch = 0
                while True:
                    if hat_nr:
                        nr += 1
                        stem = vorlage.render({**werte, "nr": nr})
                    else:
                        stem = vorlage.render(werte)
                        if versuch:
                            stem = f"{stem}_{versuch}"
                        versuch += 1
                    ziele = [f"{gi['zdir']}/{stem}{F[m]['suffix'].lower()}" for m in members]
                    frei = stem.lower() not in self.used_stems and not any(
                        z.lower() in self.used and z.lower() != F[m]["pfad"].lower()
                        for z, m in zip(ziele, members))
                    if frei:
                        break
                self.used_stems.add(stem.lower())
                if gi["folder_year"] and gi["year"] != gi["folder_year"]:
                    self.report["jahrwechsel"].append((F[members[0]]["pfad"], gi["folder_year"], gi["year"],
                                                       gi["src"], f"{gi['date']} {gi['time']}"))
                for z, m in zip(ziele, members):
                    it = F[m]
                    self.used.add(z.lower())
                    self.final[m] = z
                    if self.modus == "ordnen" and z == it["pfad"]:
                        continue
                    media = self._media(m)
                    self.add("umbenennen" if self.modus == "ordnen" else "importieren", it["pfad"], z, f"G{g}",
                             altname=it["name"] if media else None, neuname=stem if media else None,
                             ordnerpfad=it["ordnerpfad"] if media else None)
            if hat_nr:
                neu_zaehler[k] = nr
        self.neu_zaehler = {k: v for k, v in neu_zaehler.items() if v > self.zaehler_alt.get(k, 0)}
        # Zählerstände aus dem Bestand ebenfalls festhalten
        for k, v in zaehler.items():
            if v > self.zaehler_alt.get(k, 0):
                self.neu_zaehler[k] = max(v, self.neu_zaehler.get(k, 0))
        if self.dup_nr_ende > self.dup_nr_start:
            self.neu_zaehler["dubletten"] = self.dup_nr_ende
        self.statistik["Gruppen neu benannt"] = sum(len(v) for v in bereiche.values())

    def _primaer(self, members):
        media = [m for m in members if self._media(m)]
        return sorted(media, key=lambda m: (self.F[m]["ext"] not in RAW_EXT, m))[0]

    # ------------------------------------------------------------------ 8. Kopien, Captions, Metadaten

    def _kopien_und_captions(self):
        for keeper, (nr, jahr) in sorted(self.keeper_copy.items(), key=lambda kv: kv[1][0]):
            src = self.final.get(keeper, keeper[1])
            self.add("kopie", src, self._dup_pfad(nr, src.rsplit("/", 1)[-1], jahr), f"D{nr:05d}", qb="sammlung")
        for spender, keeper in self.captions:
            if spender in self.final or spender[0] == "sammlung":
                src, qb = self.final.get(spender, spender[1]), "sammlung"
            else:
                src, qb = spender[1], "import"     # nicht importierte Quelldatei
            dst = self.final.get(keeper, keeper[1])
            self.add("caption", src, dst, qb=qb)
        if self.e["metadaten"]["altbestand_angleichen"] and self.modus == "ordnen":
            for g, members in self.G.items():
                if not self.GI[g].get("alt"):
                    continue
                for m in members:
                    it = self.F[m]
                    if self._media(m) and not it["pfn"] and it["usercomment"]:
                        self.add("metadaten", self.final.get(m, it["pfad"]), None, qb="sammlung",
                                 altname=it["usercomment"])

    # ------------------------------------------------------------------ Speichern

    def _speichern(self):
        db = self.db
        plan_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        db.execute("DELETE FROM plan")
        db.executemany("INSERT INTO plan (aktion, quelle, ziel, gruppe, info) VALUES (?,?,?,?,?)",
                       [(a, q, z, g, json.dumps({k: v for k, v in i.items() if v is not None}, ensure_ascii=False))
                        for a, q, z, g, i in self.actions])
        db.execute("DELETE FROM dubletten")
        db.executemany("INSERT INTO dubletten VALUES (?,?,?,?,?,?,?,?,?,?,?)", self.dup_cluster)

        # Verteilung auf Zielordner (Vorschau als Baum)
        verteilung = Counter()
        for k, ziel in self.final.items():
            if k[0] == self.bereich or k in self.ersetzt:
                verteilung[ziel.rsplit("/", 1)[0] if "/" in ziel else "."] += 1
        self.report["zielordner"] = sorted(verteilung.items())

        db.execute("DELETE FROM bericht")
        db.executemany("INSERT INTO bericht (kategorie, daten) VALUES (?,?)",
                       [(k, json.dumps(row, ensure_ascii=False, default=str))
                        for k, rows in self.report.items() for row in rows])
        anzahl = Counter(a[0] for a in self.actions)
        kdb.meta_set(db, "plan_id", plan_id)
        kdb.meta_set(db, "plan_modus", self.modus)
        kdb.meta_set(db, "plan_quelle", self.quelle or "")
        kdb.meta_set(db, "plan_import_modus", self.import_modus)
        kdb.meta_set(db, "plan_lauf", self.lauf)
        kdb.meta_set_json(db, "plan_zaehler", self.neu_zaehler)
        kdb.meta_set_json(db, "plan_neue_ordner", sorted(self.struktur.neue_jahre))
        kdb.meta_set_json(db, "plan_wurzel_ordner", {k: z for a, k, z, _, _ in self.actions
                                                     if a == "ordner_verschieben"})
        kdb.meta_set(db, "plan_einstellungen_stand", kdb.meta_get(db, "einstellungen_geaendert", ""))
        kdb.meta_set(db, "plan_scan_stand", kdb.meta_get(db, f"scan_{self.bereich}", ""))
        zus = self._zusammenfassung(anzahl)
        kdb.meta_set_json(db, "plan_zusammenfassung", zus)
        db.commit()

        out = self.s.berichtordner("plan" if self.modus == "ordnen" else "import_plan")
        self._csv(out / "plan.csv", ["Aktion", "Quelle", "Ziel", "Gruppe", "alter Name", "neuer Name", "Ordnerpfad"],
                  [(a, q, z or "", g, i.get("altname") or "", i.get("neuname") or "", i.get("ordnerpfad") or "")
                   for a, q, z, g, i in self.actions])
        for k, (datei, kopf) in BERICHTE.items():
            if self.report.get(k):
                self._csv(out / datei, kopf, self.report[k])
        (out / "zusammenfassung.txt").write_text("\n".join(zus) + "\n", encoding="utf-8")
        kdb.meta_set(db, "plan_berichte", str(out))
        db.commit()
        for z in zus:
            self.r.meldung(z)
        return dict(plan_id=plan_id, anzahl=dict(anzahl), zusammenfassung=zus, berichte=str(out),
                    statistik=dict(self.statistik))

    @staticmethod
    def _csv(pfad, kopf, rows):
        with open(pfad, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh, delimiter=";")
            w.writerow(kopf)
            w.writerows(rows)

    def _zusammenfassung(self, anzahl):
        titel = {"ordnen": f"Plan vom {self.lauf} – Sammlung {self.wurzel}",
                 "import": f"Import-Plan vom {self.lauf} – {self.quelle} → {self.wurzel} ({self.import_modus})"}
        lines = [titel[self.modus], "", "Aktionen:"]
        lines += [f"  {k:20} {v:7}" for k, v in sorted(anzahl.items())] or ["  keine"]
        lines += ["", *(f"{k}: {v}" for k, v in sorted(self.statistik.items()))]
        if self.report.get("jahrwechsel"):
            lines.append(f"Jahrwechsel: {len(self.report['jahrwechsel'])}")
        if self.report.get("ohne_datum"):
            quellen = Counter(r[1] for r in self.report["ohne_datum"])
            lines.append(f"Ohne EXIF-Datum: {len(self.report['ohne_datum'])} "
                         f"({', '.join(f'{k} {v}' for k, v in sorted(quellen.items()))})")
        if self.modus == "import":
            lines.append(f"Schon in der Sammlung: {len(self.report.get('schon_vorhanden', []))}")
            lines.append(f"Ersetzen Bestandsdateien: {len(self.report.get('ersetzt', []))}")
            lines.append(f"Nicht importiert: {len(self.report.get('nicht_importiert', []))}")
        lines.append(f"Neue Jahresordner: {', '.join(sorted(self.struktur.neue_jahre)) or '-'}")
        return lines


def erstelle_plan(sammlung, reporter, **kw):
    return Planer(sammlung, reporter, **kw).erstelle()
