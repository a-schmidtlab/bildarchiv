"""Metadaten schreiben: alter Name, Ordnerpfad, neuer Name (Felder aus den Einstellungen)."""

import csv
import os
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor

from . import exif
from .klassifikation import XMP_ONLY_EXT, ext_of, norm_ext

INHALTE = ("altname", "ordnerpfad", "neuname")
CAPTION_TAGS = ["-IPTC:Caption-Abstract", "-XMP-dc:Description", "-EXIF:ImageDescription"]


def _xmp_variante(tag):
    g, _, n = tag.partition(":")
    if g.upper().startswith("XMP"):
        return tag
    if g.upper() in ("EXIF", "EXIFIFD", "IFD0"):
        return f"XMP-exif:{n}"
    return None     # IPTC-IIM u. ä. gibt es nicht als XMP


def felder(einst, ext=None, nur_xmp=False):
    """{inhalt: [tags]} für eine Dateiendung (Videos/GIF: nur XMP)."""
    m = einst["metadaten"]
    xmp_only = nur_xmp or (ext is not None and norm_ext(ext) in XMP_ONLY_EXT)
    out = {k: [] for k in INHALTE}
    paare = [(m["altname"], "altname"), (m["ordnerpfad"], "ordnerpfad"), (m["neuname"], "neuname")]
    paare += [tuple(z.split("=", 1)) for z in m["zusatz"] if "=" in z]
    for tag, inhalt in paare:
        tag = (tag or "").strip()
        if not tag or inhalt not in out:
            continue
        t = _xmp_variante(tag) if xmp_only else tag
        if t and t not in out[inhalt]:
            out[inhalt].append(t)
    return out


def _pruef_tag(fd, werte):
    for inhalt in INHALTE:
        if werte.get(inhalt) and fd[inhalt]:
            return fd[inhalt][0], werte[inhalt]
    return None, None


def schreibe(wurzel, eintraege, einst, reporter=None, jobs=6):
    """eintraege: [(rel, {altname, ordnerpfad, neuname})]. Liefert die Menge der nicht beschreibbaren rel."""
    eintraege = [(r, w) for r, w in eintraege if any(w.get(k) for k in INHALTE)]
    if not eintraege:
        return set()
    if reporter:
        reporter.phase(f"Metadaten schreiben ({len(eintraege)} Dateien)", len(eintraege))
    # nach Felderprofil gruppieren (normale Dateien / nur XMP)
    profile = {}
    for rel, w in eintraege:
        xmp_only = norm_ext(ext_of(rel)) in XMP_ONLY_EXT
        profile.setdefault(xmp_only, []).append((rel, w))
    jobs_liste = []
    for xmp_only, liste in profile.items():
        fd = felder(einst, nur_xmp=xmp_only)
        for b in exif.chunks(liste, 100):
            jobs_liste.append((fd, b))

    def job(arg):
        fd, batch = arg
        spalten = [(inhalt, tag) for inhalt in INHALTE for tag in fd[inhalt]]
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["SourceFile", *(t for _, t in spalten)])
            for rel, werte in batch:
                w.writerow([os.path.join(wurzel, rel), *((werte.get(i) or "") for i, _ in spalten)])
            csvfile = fh.name
        try:
            exif.run([f"-csv={csvfile}", "-overwrite_original", "-P", "-m", "-q", "-q"],
                     [os.path.join(wurzel, r) for r, _ in batch])
        finally:
            os.unlink(csvfile)
        # Kontrolle: erstes beschriebenes Feld zurücklesen
        fehl = []
        pruef = {}
        for rel, werte in batch:
            tag, soll = _pruef_tag(fd, werte)
            if tag:
                pruef.setdefault(tag, []).append((rel, soll))
        for tag, liste in pruef.items():
            key = tag.split(":")[-1]
            res = exif.lies_json(["-fast", f"-{tag}"], [os.path.join(wurzel, r) for r, _ in liste])
            got = {os.path.relpath(x["SourceFile"], wurzel): str(x.get(key, "")) for x in res}
            fehl += [r for r, soll in liste if got.get(r) != str(soll)]
        return fehl, len(batch)

    failed, fertig = set(), 0
    with ThreadPoolExecutor(max_workers=jobs) as ex:
        for fehl, n in ex.map(job, jobs_liste):
            failed.update(fehl)
            fertig += n
            if reporter:
                reporter.fortschritt(fertig, len(eintraege))
    return failed


def sidecar(wurzel, rel, werte, einst):
    """XMP-Sidecar <rel>.xmp für nicht beschreibbare Dateien. Liefert den relativen Pfad oder None."""
    side = rel + ".xmp"
    fd = felder(einst, nur_xmp=True)
    args = ["-q", "-q", "-o", os.path.join(wurzel, side)]
    for inhalt in INHALTE:
        if werte.get(inhalt):
            args += [f"-{t}={werte[inhalt]}" for t in fd[inhalt]]
    if len(args) == 4:
        return None
    p = exif.run(args)
    return side if p.returncode == 0 and os.path.exists(os.path.join(wurzel, side)) else None


def uebertrage_caption(src_abs, dst_abs):
    p = subprocess.run([*exif.BASIS, "-tagsFromFile", src_abs, *CAPTION_TAGS,
                        "-overwrite_original", "-P", "-m", "-q", "-q", dst_abs], capture_output=True, text=True)
    return p.returncode == 0, p.stderr.strip()
