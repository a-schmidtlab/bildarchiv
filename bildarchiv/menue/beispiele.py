"""Beispiele aus dem Bestand für die Live-Vorschau der Namensvorlage."""

from collections import defaultdict
from datetime import datetime

from ..kern.datierung import ereignis_name, fuehrendes_jahr, name_date, parse_dt
from ..kern.klassifikation import RAW_EXT, Klassifikator
from ..kern.struktur import Struktur, spezialpruefer
from ..kern.vorlage import ALTSCHEMATA, Vorlage


def beispiele(s, anzahl=6):
    """Liste von dict(anzeige, werte, endungen, grund) – bewusst schwierige Fälle zuerst."""
    e = s.einstellungen
    kl = Klassifikator(e["klassifikation"]["sidecars_plus"], e["klassifikation"]["muell_plus"])
    ist_spezial = spezialpruefer(e)
    rows = s.db.execute("SELECT pfad, art, dto, createdate, modell, serie, datahash FROM dateien "
                        "WHERE bereich='sammlung' AND art IN ('bild','video','sidecar') LIMIT 50000").fetchall()
    rows = [r for r in rows if not ist_spezial(r[0])]
    tops = {r[0].split("/", 1)[0] for r in rows if "/" in r[0]}
    struktur = Struktur(e, tops)
    gruppen = defaultdict(list)
    for r in rows:
        d, name = (r[0].rsplit("/", 1) + [""])[:2] if "/" in r[0] else ("", r[0])
        stem, suf = kl.split_stem(name)
        gruppen[(d, stem.lower())].append((r, suf))
    gruppen = {k: v for k, v in gruppen.items() if any(x[0][1] in ("bild", "video") for x in v)}
    # schon benannte Dateien (Altbestand) werden nicht umbenannt: keine Beispiele
    muster = [Vorlage(t).muster(None) for t in ALTSCHEMATA + e["namen"]["frueher"]]
    muster.append(Vorlage(e["namen"]["vorlage"]).muster(e["namen"]["kuerzel"]))
    gruppen = {k: v for k, v in gruppen.items() if not any(m.fullmatch(k[1]) for m in muster)}

    def info(key):
        mitglieder = gruppen[key]
        media = [m for m in mitglieder if m[0][1] in ("bild", "video")]
        media.sort(key=lambda m: m[1].lower().lstrip(".") not in RAW_EXT)
        r = media[0][0]
        pfad = r[0]
        parts = pfad.split("/")
        top = parts[0] if len(parts) > 1 else ""
        fy = struktur.jahr_von(top) if top else None
        if fy:
            ev = parts[1] if len(parts) > 2 else ""
        else:
            ev = top
        d = parse_dt(r[2]) or parse_dt(r[3])
        grund = ""
        if d:
            datum, zeit = d
        else:
            nd = name_date([parts[-1]] + parts[:-1][::-1])
            if nd:
                (datum, zeit), grund = nd, "Datum aus dem Namen"
            else:
                j = fy or (fuehrendes_jahr(top) if top else None) or datetime.now().year
                datum, zeit, grund = f"{j}0000", "000000", "ohne Datum"
        stem = kl.split_stem(parts[-1])[0]
        endungen = sorted({m[1].lower() for m in mitglieder}, key=lambda x: (x.lower().lstrip(".") not in RAW_EXT, x))
        sufs = [m[1] for m in sorted(mitglieder, key=lambda m: (m[1].lower().lstrip(".") not in RAW_EXT, m[1].lower()))]
        anzeige = stem + sufs[0] + "".join(f" + {x}" for x in sufs[1:])
        werte = dict(datum=datum, zeit=zeit, kamera=r[4] or "", serie=r[5] or "", altname=stem,
                     hash=r[6] or "", ereignis=ereignis_name(ev) if ev else "", ordner=top,
                     kuerzel=e["namen"]["kuerzel"], lauf=datetime.now().strftime("%Y%m%d"), jahr=datum[:4])
        return dict(anzeige=anzeige, werte=werte, endungen=endungen, grund=grund, ordner="/".join(parts[:-1]))

    gewaehlt = []

    def nimm(key, grund):
        if key and key not in [g[0] for g in gewaehlt] and len(gewaehlt) < anzahl:
            gewaehlt.append((key, grund))

    keys = sorted(gruppen)
    nimm(next((k for k in keys if sum(1 for m in gruppen[k] if m[0][1] == "bild") > 1), None), "RAW/JPEG-Paar")
    nimm(next((k for k in keys if not any(parse_dt(m[0][2]) or parse_dt(m[0][3]) for m in gruppen[k])), None),
         "ohne EXIF-Datum")
    nimm(next((k for k in keys if any(ord(c) > 127 for c in k[0])), None), "Umlaute im Ordner")
    nach_zeit = defaultdict(list)
    for k in keys:
        dto = next((m[0][2] for m in gruppen[k] if m[0][2]), None)
        if dto:
            nach_zeit[dto].append(k)
    serie = next((v for v in nach_zeit.values() if len(v) > 1), None)
    if serie:
        nimm(serie[0], "gleiche Aufnahmesekunde")
        nimm(serie[1], "gleiche Aufnahmesekunde")
    nimm(max(keys, key=lambda k: len(k[0].split("/")[-1]), default=None), "langer Ordnername")
    for k in keys:
        nimm(k, "")
    out = []
    for k, grund in gewaehlt:
        i = info(k)
        i["grund"] = ", ".join(x for x in (grund, i["grund"]) if x)
        out.append(i)
    out.sort(key=lambda i: (i["werte"]["datum"], i["werte"]["zeit"]))
    return out


def _bereich(zaehler, w, ordner):
    return {"gesamt": "", "jahr": w["datum"][:4], "monat": w["datum"][:6], "tag": w["datum"],
            "sekunde": w["datum"] + w["zeit"], "ordner": ordner}.get(zaehler, "")


def vorschau(text, beispiele_liste, zaehler="jahr"):
    """[(anzeige, neuer Name, grund)] oder Fehlertext. Nummern pro Zählerbereich ab 1."""
    try:
        v = Vorlage(text)
    except ValueError as err:
        return str(err)
    out = []
    nummern = {}
    for b in beispiele_liste:
        k = _bereich(zaehler, b["werte"], b["ordner"])
        nummern[k] = nr = nummern.get(k, 0) + 1
        stem = v.render({**b["werte"], "nr": nr})
        endungen = ", ".join(x.lower() for x in b["endungen"])
        out.append((b["anzeige"], f"{stem}  [{endungen}]", b["grund"]))
    return out
