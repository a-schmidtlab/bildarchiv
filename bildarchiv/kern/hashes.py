"""Bild-Hashes für die Dublettensuche.

datahash: ImageDataHash von exiftool (Bild-/Mediendaten ohne Metadaten), bzw. 'px:<md5 der Pixel>'
          für PSD/GIF …; '' = nicht ermittelbar
dhash:    Differenz-Hash (64 bit) einer Graustufen-Miniatur, '' = nicht ermittelbar
thumb:    64x64-Graustufen für die Pixelprüfung, nur für Kandidaten
"""

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from multiprocessing import get_context

from . import exif
from .klassifikation import PHASH_EXT, PIXEL_MD5_EXT

DHASH_MAX_DIST = 4
ASPECT_TOL = 0.02
# Pixelprüfung auf 64x64-Graustufen, Blöcke 8x8. Gleiche Auflösung: Rohwerte (0–255);
# verschiedene Auflösung: tonwertnormiert (z-Werte), damit Web-/Agenturfassungen erkannt werden.
SAME_RES_MAD, SAME_RES_BLOCK = 1.5, 4.0
DIFF_RES_MAD, DIFF_RES_BLOCK = 0.10, 0.40
# Mini-Vorschaubilder (< TINY_PX lange Seite) werden auf 16x16 verglichen
TINY_PX, TINY_MAD = 256, 0.05


def _pool(jobs):
    return get_context("fork").Pool(jobs)


def load_gray(full):
    """Bild orientierungsrichtig als Graustufen-PIL-Bild (verkleinert dekodiert)."""
    from PIL import Image, ImageOps
    import numpy as np
    Image.MAX_IMAGE_PIXELS = None
    with Image.open(full) as src:
        src.draft("L", (256, 256))
        try:
            im = ImageOps.exif_transpose(src)
        except Exception:
            im = src
        if im.mode not in ("L", "RGB", "RGBA", "CMYK", "P", "LA", "1"):
            a = np.asarray(im).astype("float64")
            if a.ndim == 3:
                a = a.mean(axis=2)
            a = (a / max(a.max(), 1) * 255).astype("uint8")
            im = Image.fromarray(a)
        g = im.convert("L")
        g.load()
        return g


def thumb_file(full):
    try:
        from PIL import Image
        return full, load_gray(full).resize((64, 64), Image.Resampling.LANCZOS).tobytes()
    except Exception:
        return full, None


def visual_file(full):
    try:
        from PIL import Image
        import numpy as np
        im = load_gray(full)
        w, h = im.size
        px = np.asarray(im.resize((9, 8), Image.Resampling.LANCZOS), dtype=np.int16)
        val = 0
        for b in (px[:, 1:] > px[:, :-1]).flatten():
            val = (val << 1) | int(b)
        thumb = im.resize((64, 64), Image.Resampling.LANCZOS).tobytes()
        return full, f"{val:016x}", w / h if h else None, thumb
    except Exception:
        return full, None, None, None


def pixel_md5(full):
    try:
        import hashlib
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = None
        with Image.open(full) as im:
            im = im.convert("RGB")
            return full, "px:" + hashlib.md5(im.tobytes()).hexdigest()
    except Exception:
        return full, None


def pixel_match(ta, tb, same_res, small_px):
    import numpy as np
    a = np.frombuffer(ta, dtype=np.uint8).reshape(64, 64).astype(np.float32)
    b = np.frombuffer(tb, dtype=np.uint8).reshape(64, 64).astype(np.float32)
    if not same_res and small_px < TINY_PX:
        a, b = (x.reshape(16, 4, 16, 4).mean(axis=(1, 3)) for x in (a, b))
        a, b = ((x - x.mean()) / (x.std() + 1e-6) for x in (a, b))
        return float(np.abs(a - b).mean()) <= TINY_MAD
    if same_res:
        lim_mad, lim_block = SAME_RES_MAD, SAME_RES_BLOCK
    else:
        a = (a - a.mean()) / (a.std() + 1e-6)
        b = (b - b.mean()) / (b.std() + 1e-6)
        lim_mad, lim_block = DIFF_RES_MAD, DIFF_RES_BLOCK
    d = np.abs(a - b)
    block = d.reshape(8, 8, 8, 8).mean(axis=(1, 3)).max()
    return float(d.mean()) <= lim_mad and float(block) <= lim_block


def popcount(x):
    return bin(x).count("1")


def image_data_hashes(wurzel, rels, jobs, reporter=None):
    """ImageDataHash für relative Pfade -> {rel: hash oder ''}."""
    out = {r: "" for r in rels}
    fertig = 0

    def job(batch):
        res = exif.lies_json(["-api", "RequestTags=ImageDataHash", "-ImageDataHash"],
                             [os.path.join(wurzel, x) for x in batch])
        got = {os.path.relpath(r["SourceFile"], wurzel): r.get("ImageDataHash") or "" for r in res}
        return [(x, got.get(x, "")) for x in batch]

    with ThreadPoolExecutor(max_workers=jobs) as ex:
        for res in ex.map(job, list(exif.chunks(rels, 100))):
            for x, h in res:
                out[x] = h
            fertig += len(res)
            if reporter:
                reporter.fortschritt(fertig, len(rels))
    return out


def rechne(db, wurzel, bereich, reporter, jobs=6, filter_pfad=None):
    """Fehlende Hashes für Bilder und Videos eines Bereichs ergänzen."""
    rows = db.execute("SELECT pfad, art, ext, datahash, dhash FROM dateien WHERE bereich=? "
                      "AND art IN ('bild','video')", (bereich,)).fetchall()
    if filter_pfad:
        rows = [r for r in rows if filter_pfad(r[0])]
    need_data = [r[0] for r in rows if r[3] is None]
    need_vis = [r[0] for r in rows if r[1] == "bild" and r[2] in PHASH_EXT and r[4] is None]
    if need_data or need_vis:
        reporter.phase(f"Hashes berechnen (ImageDataHash {len(need_data)}, Miniaturen {len(need_vis)})",
                       len(need_data) + len(need_vis))
        lock = threading.Lock()
        data_res, state = [], {"data": 0, "vis": 0}

        def exif_worker():
            def job(batch):
                res = exif.lies_json(["-api", "RequestTags=ImageDataHash", "-ImageDataHash"],
                                     [os.path.join(wurzel, x) for x in batch])
                got = {os.path.relpath(r["SourceFile"], wurzel): r.get("ImageDataHash") or "" for r in res}
                return [(got.get(x, ""), bereich, x) for x in batch]
            with ThreadPoolExecutor(max_workers=max(2, jobs // 2)) as ex:
                for res in ex.map(job, list(exif.chunks(need_data, 100))):
                    with lock:
                        data_res.extend(res)
                        state["data"] += len(res)

        def flush(vis):
            with lock:
                items = data_res[:]
                data_res.clear()
            db.executemany("UPDATE dateien SET datahash=? WHERE bereich=? AND pfad=?", items)
            db.executemany("UPDATE dateien SET dhash=?, aspect=?, thumb=? WHERE bereich=? AND pfad=?", vis)
            db.commit()
            vis.clear()

        pool = _pool(jobs) if need_vis else None    # vor dem Thread starten (fork)
        th = threading.Thread(target=exif_worker)
        th.start()
        vis = []
        if pool:
            for full, dh, asp, tb in pool.imap_unordered(
                    visual_file, [os.path.join(wurzel, p) for p in need_vis], chunksize=8):
                vis.append((dh or "", asp, tb or b"", bereich, os.path.relpath(full, wurzel)))
                state["vis"] += 1
                if state["vis"] % 200 == 0:
                    flush(vis)
                    reporter.fortschritt(state["data"] + state["vis"])
            pool.close()
            pool.join()
        while th.is_alive():
            th.join(1)
            flush(vis)
            reporter.fortschritt(state["data"] + state["vis"])
        flush(vis)
        reporter.fortschritt(state["data"] + state["vis"], len(need_data) + len(need_vis))

    # Ersatz-Hash über Pixel (PSD, GIF …)
    todo = [p for p, ext in db.execute("SELECT pfad, ext FROM dateien WHERE bereich=? AND art='bild' "
                                       "AND datahash=''", (bereich,)) if ext in PIXEL_MD5_EXT
            and (not filter_pfad or filter_pfad(p))]
    if todo:
        reporter.phase(f"Pixel-Hash für {len(todo)} Dateien ohne ImageDataHash", len(todo))
        with _pool(jobs) as pool:
            res = []
            for i, x in enumerate(pool.imap_unordered(pixel_md5, [os.path.join(wurzel, p) for p in todo],
                                                      chunksize=4), 1):
                res.append(x)
                reporter.fortschritt(i, len(todo))
        db.executemany("UPDATE dateien SET datahash=? WHERE bereich=? AND pfad=?",
                       [(h, bereich, os.path.relpath(f, wurzel)) for f, h in res if h])
        db.commit()


def thumbs(db, wurzeln, schluessel, reporter, jobs=6):
    """Miniaturen für (bereich, pfad) sicherstellen und liefern. wurzeln: {bereich: pfad}."""
    out = {}
    for chunk in exif.chunks(sorted(schluessel), 400):
        for b in {k[0] for k in chunk}:
            ps = [p for bb, p in chunk if bb == b]
            q = f"SELECT pfad, thumb FROM dateien WHERE bereich=? AND pfad IN ({','.join('?' * len(ps))})"
            for p, t in db.execute(q, (b, *ps)):
                if t is not None:
                    out[(b, p)] = t
    need = [k for k in schluessel if k not in out]
    if need:
        reporter.phase(f"Miniaturen für die Pixelprüfung ({len(need)})", len(need))
        full = {os.path.join(wurzeln[b], p): (b, p) for b, p in need}
        with _pool(jobs) as pool:
            for i, (f, tb) in enumerate(pool.imap_unordered(thumb_file, list(full), chunksize=8), 1):
                b, p = full[f]
                out[(b, p)] = tb or b""
                db.execute("UPDATE dateien SET thumb=? WHERE bereich=? AND pfad=?", (tb or b"", b, p))
                reporter.fortschritt(i, len(need))
        db.commit()
    return out
