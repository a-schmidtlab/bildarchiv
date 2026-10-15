"""Analyse des Bestands: Befund und Vorschläge für die Einstellungen."""

import re
from collections import Counter, defaultdict

from . import db as kdb
from .datierung import JAHR_RE, fuehrendes_jahr, name_date, parse_dt
from .klassifikation import Klassifikator, MEDIEN
from .struktur import Struktur, spezialpruefer
from .vorlage import ALTSCHEMATA, Vorlage

KAMERA_RE = re.compile(r"^(IMG|DSC|DSCF|DSCN|_MG|_DSC|PXL|GOPR|GH\d|DJI|MVI|CIMG|SDIM|EPSN|PICT|HPIM|KIF|IMAG|"
                       r"VID|MOV|P\d|R\d{2}|L\d{2}|WP)[_\- ]?\d", re.I)
HANDY_RE = re.compile(r"^(?:IMG[-_])?\d{8}[-_]\d{6}|^IMG-\d{8}-WA\d+|^Screenshot", re.I)
# Ordnernamen, die nach einem Export aussehen (werden zum Auflösen vorgeschlagen)
EXPORT_RE = re.compile(r"export|apple|photos|fotos|icloud|takeout|mediathek|import|camera ?roll|dcim|handy|iphone",
                       re.I)

REEDIT_MIN, REEDIT_SHARE, REEDIT_YEARS = 5, 0.3, 2
EXPORT_JAHRE = 3


def _gen(s):
    out = []
    for m in re.finditer(r"\d+|\D+", s):
        t = m.group()
        out.append(r"\d{%d}" % len(t) if t.isdigit() else re.escape(t))
    return "".join(out)


def ordnermuster(name):
    """'20011231 FOTOS ARCHIV 2001' -> (regex, vorlage, jahr) oder None."""
    jahre = list(JAHR_RE.finditer(name))
    if not jahre:
        return None
    y = jahre[-1]
    regex = "^" + _gen(name[:y.start()]) + r"(?P<jahr>(?:19|20)\d{2})" + _gen(name[y.end():]) + "$"
    esc = name.replace("{", "{{").replace("}", "}}").replace("[", "[[").replace("]", "]]")
    vorlage = esc.replace(y.group(), "{jahr}")
    return regex, vorlage, int(y.group())


def namensart(stem, muster):
    for quelle, v, rx in muster:
        r = v.lies(stem, muster=rx)
        if r is not None and "nr" in r:
            return "altbestand", r.get("kuerzel")
    if HANDY_RE.match(stem):
        return "handy", None
    if KAMERA_RE.match(stem):
        return "kamera", None
    return "frei", None


def analysiere(sammlung, bereich="sammlung", wurzel=None):
    db = sammlung.db
    e = sammlung.einstellungen
    kl = Klassifikator(e["klassifikation"]["sidecars_plus"], e["klassifikation"]["muell_plus"])
    rows = kdb.dateien(db, bereich, ["pfad", "art", "ext", "dto", "createdate", "uid", "groesse"])
    if bereich == "sammlung":
        ist_spezial = spezialpruefer(e)
        rows = [r for r in rows if not ist_spezial(r["pfad"])]
    tops = Counter()
    unter = defaultdict(set)
    tiefe, ordner = 0, set()
    for r in rows:
        parts = r["pfad"].split("/")
        tiefe = max(tiefe, len(parts) - 1)
        for i in range(1, len(parts)):
            ordner.add("/".join(parts[:i]))
        if len(parts) > 1:
            tops[parts[0]] += 1
        if len(parts) > 2:
            unter[parts[0]].add(parts[1])

    # Ordnermuster der obersten Ebene
    muster_zaehl = Counter()
    muster_info = {}
    for t in tops:
        m = ordnermuster(t)
        if m:
            muster_zaehl[m[0]] += 1
            muster_info.setdefault(m[0], dict(regex=m[0], vorlage=m[1], beispiele=[]))
            if len(muster_info[m[0]]["beispiele"]) < 3:
                muster_info[m[0]]["beispiele"].append(t)
    muster_liste = [dict(**muster_info[k], anzahl=n) for k, n in muster_zaehl.most_common()]

    struktur = Struktur(e, tops.keys(), unter)
    # Altschemata zuerst: liefern das verwendete Kürzel mit
    altmuster = []
    for t in ALTSCHEMATA:
        v = Vorlage(t)
        altmuster.append(("Schema der Vorgängerversion", v, v.muster(None)))
    n = e["namen"]
    cur = Vorlage(n["vorlage"])
    altmuster.append(("aktuelle Vorlage", cur, cur.muster(n["kuerzel"])))
    for t in n["frueher"]:
        v = Vorlage(t)
        altmuster.append(("frühere Vorlage", v, v.muster(n["kuerzel"])))

    arten = Counter(r["art"] for r in rows)
    muell = [r["pfad"] for r in rows if r["art"] == "muell"]
    c1 = [r["pfad"] for r in rows if r["art"] == "c1"]
    c1_sessions = {r["pfad"].rsplit("/", 1)[0] for r in rows if r["pfad"].lower().endswith(".cosessiondb")}
    datum = Counter()
    ohne_datum, abweichung = [], []
    namen = Counter()
    kuerzel = Counter()
    jahre = Counter()
    stems = defaultdict(set)
    ek_stat = defaultdict(lambda: dict(n=0, jahre=Counter(), folder_year=None, in_jahresordner=False))
    for r in rows:
        if r["art"] not in MEDIEN:
            continue
        p = r["pfad"]
        parts = p.split("/")
        name = parts[-1]
        stem, _ = kl.split_stem(name)
        stems[("/".join(parts[:-1]), stem.lower())].add(r["art"])
        art, kz = namensart(stem, altmuster)
        namen[art] += 1
        if kz:
            kuerzel[kz] += 1
        top = parts[0] if len(parts) > 1 else ""
        fy = struktur.jahr_von(top) if top else None
        d = parse_dt(r["dto"]) or parse_dt(r["createdate"])
        if d:
            datum["EXIF"] += 1
            y = int(d[0][:4])
        else:
            nd = name_date([name] + parts[:-1][::-1])
            if nd:
                datum["Name"] += 1
                y = int(nd[0][:4])
            else:
                datum["ohne"] += 1
                ohne_datum.append(p)
                y = fy or (fuehrendes_jahr(top) if top else None)
        if y:
            jahre[y] += 1
        if fy and d and y != fy:
            abweichung.append((p, fy, y))
        if fy:
            ek = f"{top}/{parts[1]}" if len(parts) > 2 else None
        else:
            ek = top or None
        if ek:
            s = ek_stat[ek]
            s["n"] += 1
            s["folder_year"] = fy or (fuehrendes_jahr(top) if top else None)
            s["in_jahresordner"] = bool(fy)
            if d:
                s["jahre"][int(d[0][:4])] += 1

    live = sum(1 for v in stems.values() if v >= {"bild", "video"})
    ordner_liste = []
    for ek, s in sorted(ek_stat.items()):
        datiert = sum(s["jahre"].values())
        art = ""
        vorschlag = "ereignis"
        if s["in_jahresordner"] and s["folder_year"] and datiert >= REEDIT_MIN:
            weit = sum(n for y, n in s["jahre"].items() if abs(y - s["folder_year"]) >= REEDIT_YEARS)
            if weit / datiert >= REEDIT_SHARE:
                art, vorschlag = "Re-Edit (Aufnahmen aus anderen Jahren)", "wurzel"
        if not s["in_jahresordner"] and len(s["jahre"]) >= EXPORT_JAHRE:
            if EXPORT_RE.search(ek):
                art, vorschlag = "Export (viele Jahre)", "aufloesen"
            else:
                art, vorschlag = "Sammelordner über viele Jahre (z. B. Portfolio)", "behalten"
        ordner_liste.append(dict(ordner=ek, bilder=s["n"], datiert=datiert,
                                 jahre=f"{min(s['jahre'])}–{max(s['jahre'])}" if s["jahre"] else "",
                                 ordnerjahr=s["folder_year"], art=art, vorschlag=vorschlag,
                                 entscheidung=e["struktur"]["ordner"].get(ek, "")))

    befund = dict(
        dateien=len(rows), arten=dict(arten), tiefe=tiefe, ordner=len(ordner),
        muell=len(muell), muell_beispiele=muell[:200],
        c1=len(c1), c1_sessions=len(c1_sessions),
        ordnermuster=muster_liste,
        jahresordner=sorted(struktur.jahre.items()),
        datum=dict(datum), ohne_datum=ohne_datum[:500],
        abweichung=len(abweichung), abweichung_beispiele=abweichung[:500],
        namen=dict(namen), kuerzel=dict(kuerzel), live_photos=live,
        jahre=sorted(jahre.items()), ordnerliste=ordner_liste,
        reedit=sum(1 for o in ordner_liste if o["vorschlag"] == "wurzel"),
        export=sum(1 for o in ordner_liste if o["vorschlag"] == "aufloesen"),
        sammelordner=sum(1 for o in ordner_liste if o["vorschlag"] == "behalten"),
        video_ordner=[t for t in tops if t.lower() in ("videos", "video", "filme")],
    )
    befund["vorschlaege"] = vorschlaege(e, befund)
    return befund


def vorschlaege(e, befund):
    """Vorgeschlagene Einstellungen als {(gruppe, schlüssel): wert} (nur Abweichungen)."""
    v = {}
    ml = befund["ordnermuster"]
    if ml and ml[0]["anzahl"] >= 2:
        if e["struktur"]["jahresordner"] != ml[0]["vorlage"]:
            v[("struktur", "jahresordner")] = ml[0]["vorlage"]
        if e["struktur"]["jahresordner_muster"] != ml[0]["regex"]:
            v[("struktur", "jahresordner_muster")] = ml[0]["regex"]
    kz = befund["kuerzel"]
    if kz:
        haeufig = max(kz, key=kz.get)
        if e["namen"]["kuerzel"] != haeufig and re.fullmatch(r"[A-Za-z]{1,8}", haeufig):
            v[("namen", "kuerzel")] = haeufig
        frueher = list(e["namen"]["frueher"])
        for k in sorted(kz):
            t = ALTSCHEMATA[0].replace("{kürzel}", k)
            if t not in frueher:
                frueher.append(t)
        if frueher != e["namen"]["frueher"]:
            v[("namen", "frueher")] = frueher
    if befund["video_ordner"] and befund["arten"].get("video", 0) and e["struktur"]["videos"] != "eigener_ordner":
        v[("struktur", "videos")] = "eigener_ordner"
        v[("struktur", "videoordner")] = befund["video_ordner"][0]
    ordner = dict(e["struktur"]["ordner"])
    videoordner = {v.lower() for v in befund["video_ordner"]}
    # Jahresordner (auch nach dem gerade vorgeschlagenen Muster) bekommen nie eine Ordnerentscheidung
    muster = v.get(("struktur", "jahresordner_muster")) or e["struktur"]["jahresordner_muster"]
    try:
        jahres_rx = re.compile(muster) if muster else Vorlage(e["struktur"]["jahresordner"]).muster()
    except (re.error, ValueError):
        jahres_rx = None
    for o in befund["ordnerliste"]:
        if o["ordner"].lower() in videoordner:
            continue
        if "/" not in o["ordner"] and jahres_rx and jahres_rx.match(o["ordner"]):
            ordner.pop(o["ordner"], None)
            continue
        if o["vorschlag"] != "ereignis" and o["ordner"] not in ordner:
            ordner[o["ordner"]] = o["vorschlag"]
    if ordner != e["struktur"]["ordner"]:
        v[("struktur", "ordner")] = ordner
    return [dict(gruppe=g, schluessel=k, wert=w) for (g, k), w in v.items()]


def uebernehmen(e, vorschlaege):
    for x in vorschlaege:
        e[x["gruppe"]][x["schluessel"]] = x["wert"]
