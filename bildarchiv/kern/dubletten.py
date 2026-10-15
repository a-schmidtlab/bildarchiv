"""Dublettensuche: bitgleich (ImageDataHash) und ähnlich (dHash + Pixelprüfung).

Verglichen wird nur innerhalb derselben Endung. RAW/JPEG/TIFF/PSD sind nie Dubletten voneinander.
"""

from collections import defaultdict

from .datierung import parse_dt
from .hashes import ASPECT_TOL, DHASH_MAX_DIST, pixel_match, popcount

BANDS = [(0, 13), (13, 13), (26, 13), (39, 13), (52, 12)]


class UF:
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        self.p[self.find(a)] = self.find(b)


def pixels(it):
    return (it.get("breite") or 0) * (it.get("hoehe") or 0)


def _kompatibel(a, b):
    if a["ext"] != b["ext"]:
        return False
    if not a.get("aspect") or not b.get("aspect") or abs(a["aspect"] / b["aspect"] - 1) > ASPECT_TOL:
        return False
    da, db_ = parse_dt(a.get("dto")), parse_dt(b.get("dto"))
    if da and db_:
        if da != db_:
            return False
        if a.get("subsec") and b.get("subsec") and a["subsec"] != b["subsec"]:
            return False
    return True


def finde(items, thumbs_fuer, aehnliche=True):
    """items: {schluessel: dict(ext, datahash, dhash, aspect, dto, subsec, breite, hoehe)}.

    thumbs_fuer(schluessel_menge) -> {schluessel: 64x64-Graustufen-Bytes}
    Liefert (cluster, abgelehnt):
      cluster  = [dict(mitglieder=[…], art='bitgleich'|'aehnlich', kleiner={…}, unsicher=bool)]
      abgelehnt = [(a, b, grund)]
    """
    uf = UF()
    by_hash = defaultdict(list)
    for k, it in items.items():
        if it.get("datahash"):
            by_hash[(it["ext"], it["datahash"])].append(k)
    for ks in by_hash.values():
        for x in ks[1:]:
            uf.union(ks[0], x)

    abgelehnt = []
    kleiner = set()
    if aehnliche:
        dh = {}
        for k, it in items.items():
            v = it.get("dhash")
            if v:
                iv = int(v, 16)
                if 6 <= popcount(iv) <= 58:
                    dh[k] = iv
        index = defaultdict(list)
        for k, v in dh.items():
            for bi, (off, ln) in enumerate(BANDS):
                index[(items[k]["ext"], bi, (v >> off) & ((1 << ln) - 1))].append(k)
        seen, cand = set(), []
        for ks in index.values():
            if len(ks) < 2:
                continue
            for i, x in enumerate(ks):
                for y in ks[i + 1:]:
                    pair = (x, y) if x < y else (y, x)
                    if pair in seen:
                        continue
                    seen.add(pair)
                    a, b = items[x], items[y]
                    if a.get("datahash") and a["datahash"] == b.get("datahash"):
                        continue
                    if popcount(dh[x] ^ dh[y]) <= DHASH_MAX_DIST and _kompatibel(a, b):
                        cand.append(pair)

        thumbs = thumbs_fuer({k for pr in cand for k in pr}) if cand else {}
        down = defaultdict(set)     # kleinere Version -> passende größere Versionen
        for x, y in cand:
            tx, ty = thumbs.get(x), thumbs.get(y)
            a, b = items[x], items[y]
            same = pixels(a) == pixels(b)
            small_px = min(max(a.get("breite") or 0, a.get("hoehe") or 0),
                           max(b.get("breite") or 0, b.get("hoehe") or 0))
            if tx and ty and pixel_match(tx, ty, same, small_px):
                if same:
                    uf.union(x, y)
                else:
                    s, l = (x, y) if pixels(a) < pixels(b) else (y, x)
                    down[s].add(l)
            else:
                abgelehnt.append((x, y, "gleiche Auflösung" if same else "verschiedene Auflösung"))
        # Kleinere Versionen nur zuordnen, wenn alle passenden größeren Bilder schon dasselbe Bild sind
        # (sonst könnte eine Miniatur zwei Serienbilder verbinden). Größte zuerst, damit Ketten aufgehen.
        for s in sorted(down, key=lambda k: pixels(items[k]), reverse=True):
            roots = {uf.find(l) for l in down[s]}
            if len(roots) == 1:
                uf.union(s, next(iter(down[s])))
                kleiner.add(s)
            else:
                abgelehnt.append((s, " | ".join(str(x[1]) for x in sorted(down[s])), "passt zu mehreren Bildern"))

    gruppen = defaultdict(list)
    for k in items:
        if k in uf.p:
            gruppen[uf.find(k)].append(k)
    cluster = []
    for c in gruppen.values():
        if len(c) < 2:
            continue
        c.sort()
        bitgleich = len({items[k].get("datahash") or None for k in c}) == 1 and items[c[0]].get("datahash")
        cluster.append(dict(mitglieder=c, art="bitgleich" if bitgleich else "aehnlich",
                            kleiner={k for k in c if k in kleiner},
                            unsicher=any(k in kleiner for k in c)))
    return cluster, abgelehnt
