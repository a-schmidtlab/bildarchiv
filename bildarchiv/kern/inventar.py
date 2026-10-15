"""Bestandsaufnahme: Dateien, Metadaten und Klassifikation in die Arbeitsdatenbank."""

import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from . import exif
from .klassifikation import ext_of, norm_ext, MEDIEN

SCAN_TAGS = ["-fast", "-FileSize#", "-DateTimeOriginal", "-SubSecTimeOriginal", "-CreateDate",
             "-Keys:CreationDate", "-Model", "-SerialNumber", "-InternalSerialNumber", "-ImageSize#",
             "-IPTC:Caption-Abstract", "-XMP-dc:Description", "-EXIF:ImageDescription",
             "-ImageUniqueID", "-XMP-xmpMM:PreservedFileName", "-UserComment"]
CAPTION_KEYS = ("Caption-Abstract", "Description", "ImageDescription")
SPEZIAL_TOP = "_bildarchiv"


def _txt(v):
    s = str(v if v is not None else "").strip()
    return s or None


def auswerten(r):
    """exiftool-JSON einer Datei -> Spalten der Tabelle dateien."""
    w = h = None
    size = str(r.get("ImageSize", "")).replace("x", " ").split()
    if len(size) == 2:
        try:
            w, h = int(float(size[0])), int(float(size[1]))
        except ValueError:
            pass
    # iPhone-Videos: Keys:CreationDate ist Ortszeit, CreateDate dagegen UTC
    dto = r.get("DateTimeOriginal") or r.get("CreationDate")
    uc = _txt(r.get("UserComment"))
    if uc and uc.lower().startswith("(binary"):
        uc = None
    return dict(
        dto=_txt(dto), subsec=_txt(r.get("SubSecTimeOriginal")), createdate=_txt(r.get("CreateDate")),
        modell=_txt(r.get("Model")), serie=_txt(r.get("SerialNumber") or r.get("InternalSerialNumber")),
        breite=w, hoehe=h,
        caption=int(any(str(r.get(k) or "").strip() for k in CAPTION_KEYS)),
        uid=_txt(r.get("ImageUniqueID")), pfn=_txt(r.get("PreservedFileName")), usercomment=uc, gelesen=1)


def lies_metadaten(wurzel, rels, jobs=6, reporter=None, batch=200):
    """Metadaten für relative Pfade -> {rel: spalten}."""
    out = {}
    if not rels:
        return out
    fertig = 0

    def job(b):
        return exif.lies_json(SCAN_TAGS, [os.path.join(wurzel, x) for x in b])

    with ThreadPoolExecutor(max_workers=jobs) as ex:
        for res in ex.map(job, list(exif.chunks(rels, batch))):
            for r in res:
                rel = os.path.relpath(r["SourceFile"], wurzel)
                out[rel] = auswerten(r)
            fertig += batch
            if reporter:
                reporter.fortschritt(min(fertig, len(rels)), len(rels))
    return out


def scan(db, wurzel, bereich, klassifikator, reporter, jobs=6, voll=False):
    """Bestand von wurzel in die Tabelle dateien (bereich). Unveränderte Dateien werden nicht neu gelesen."""
    reporter.phase(f"Dateien suchen in {wurzel}")
    alt = {p: (g, m, ge) for p, g, m, ge in db.execute(
        "SELECT pfad, groesse, mtime, gelesen FROM dateien WHERE bereich=?", (bereich,))}
    gefunden = {}
    n = 0
    for dirpath, dirnames, filenames in os.walk(wurzel):
        rel_dir = os.path.relpath(dirpath, wurzel)
        if rel_dir == ".":
            dirnames[:] = [d for d in dirnames if d != SPEZIAL_TOP]
        dirnames.sort()
        for fn in sorted(filenames):
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, wurzel)
            try:
                st = os.lstat(full)
            except OSError:
                continue
            if not os.path.isfile(full):
                continue
            gefunden[rel] = (st.st_size, st.st_mtime)
            n += 1
            if n % 2000 == 0:
                reporter.fortschritt(n)
    reporter.fortschritt(n, n)

    weg = [p for p in alt if p not in gefunden]
    neu_oder_geaendert = []
    for rel, (g, m) in gefunden.items():
        a = alt.get(rel)
        if voll or not a or a[0] != g or abs((a[1] or 0) - m) > 1:
            neu_oder_geaendert.append(rel)
    db.executemany("DELETE FROM dateien WHERE bereich=? AND pfad=?", [(bereich, p) for p in weg])

    zeilen = {}
    lesen = []
    for rel in neu_oder_geaendert:
        art = klassifikator.art(rel)
        g, m = gefunden[rel]
        zeilen[rel] = dict(bereich=bereich, pfad=rel, groesse=g, mtime=m, art=art,
                           ext=norm_ext(ext_of(rel.rsplit("/", 1)[-1])), gelesen=0)
        if art in MEDIEN:
            lesen.append(rel)
    reporter.meldung(f"{len(gefunden)} Dateien, davon neu oder geändert {len(neu_oder_geaendert)}, "
                     f"entfernt {len(weg)}")

    if lesen:
        reporter.phase("Metadaten lesen", len(lesen))
        meta = lies_metadaten(wurzel, lesen, jobs, reporter)
        for rel, werte in meta.items():
            if rel in zeilen:
                zeilen[rel].update(werte)
    spalten = ["bereich", "pfad", "groesse", "mtime", "art", "ext", "dto", "subsec", "createdate", "modell",
               "serie", "breite", "hoehe", "caption", "uid", "pfn", "usercomment", "gelesen"]
    # Hashes und Rollen gehen bei geänderten Dateien verloren (INSERT OR REPLACE)
    db.executemany(f"INSERT OR REPLACE INTO dateien ({','.join(spalten)}) VALUES ({','.join('?' * len(spalten))})",
                   [tuple(z.get(c) for c in spalten) for z in zeilen.values()])
    db.commit()

    # Klassifikation kann sich durch geänderte Einstellungen ändern (z. B. zusätzliche Sidecars)
    neu_art = []
    for p, art in db.execute("SELECT pfad, art FROM dateien WHERE bereich=?", (bereich,)).fetchall():
        a2 = klassifikator.art(p)
        if a2 != art:
            neu_art.append((a2, bereich, p))
    if neu_art:
        db.executemany("UPDATE dateien SET art=? WHERE bereich=? AND pfad=?", neu_art)
        nachlesen = [p for a, _, p in neu_art if a in MEDIEN]
        if nachlesen:
            meta = lies_metadaten(wurzel, nachlesen, jobs, reporter)
            for rel, w in meta.items():
                db.execute(f"UPDATE dateien SET {','.join(k + '=?' for k in w)} WHERE bereich=? AND pfad=?",
                           (*w.values(), bereich, rel))
        db.commit()
    arten = Counter(a for (a,) in db.execute("SELECT art FROM dateien WHERE bereich=?", (bereich,)))
    reporter.meldung("Kategorien: " + ", ".join(f"{k} {v}" for k, v in sorted(arten.items())))
    return arten
