"""Erzeugt eine absichtlich unordentliche Testsammlung mit allen typischen Fällen."""

import csv
import os
import random
import shutil
import subprocess
import tempfile

from PIL import Image, ImageDraw


def bild(pfad, seed, groesse=(640, 480), fmt=None, quality=90):
    """Bild mit großen Farbflächen (eindeutiger dHash, robust gegen Verkleinern)."""
    rnd = random.Random(seed)
    im = Image.new("RGB", groesse, tuple(rnd.randrange(256) for _ in range(3)))
    d = ImageDraw.Draw(im)
    w, h = groesse
    for _ in range(7):
        x0, y0 = rnd.randrange(w), rnd.randrange(h)
        x1, y1 = x0 + rnd.randrange(w // 4, w // 2), y0 + rnd.randrange(h // 4, h // 2)
        farbe = tuple(rnd.randrange(256) for _ in range(3))
        (d.ellipse if rnd.random() < 0.5 else d.rectangle)([x0, y0, x1, y1], fill=farbe)
    os.makedirs(os.path.dirname(pfad), exist_ok=True)
    fmt = fmt or {"jpg": "JPEG", "tif": "TIFF", "dng": "TIFF", "png": "PNG", "bmp": "BMP"}[pfad.rsplit(".", 1)[1].lower()]
    if fmt == "JPEG":
        im.save(pfad, fmt, quality=quality)
    else:
        im.save(pfad, fmt)
    return im


def verkleinert(src, dst, faktor=0.5, quality=80):
    with Image.open(src) as im:
        k = im.resize((int(im.width * faktor), int(im.height * faktor)), Image.Resampling.LANCZOS)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        k.save(dst, "JPEG", quality=quality)


def video(pfad, datum, farbe="green"):
    os.makedirs(os.path.dirname(pfad), exist_ok=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"color=c={farbe}:s=64x48:d=1",
                    "-pix_fmt", "yuv420p", "-metadata", f"creation_time={datum}", pfad], check=True)


def datei(pfad, inhalt=b"x"):
    os.makedirs(os.path.dirname(pfad), exist_ok=True)
    with open(pfad, "wb") as fh:
        fh.write(inhalt)


def exif_setzen(eintraege):
    """eintraege: [(pfad, {tag: wert})] in einem exiftool-Aufruf."""
    tags = sorted({t for _, w in eintraege for t in w})
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["SourceFile", *tags])
        for p, werte in eintraege:
            w.writerow([p, *(werte.get(t, "") for t in tags)])
        name = fh.name
    try:
        subprocess.run(["exiftool", "-csv=" + name, "-overwrite_original", "-m", "-q", "-q",
                        *[p for p, _ in eintraege]], capture_output=True)
    finally:
        os.unlink(name)


def baue_sammlung(root):
    """Unordentliche Sammlung. Liefert ein dict mit erwarteten Eigenschaften."""
    j = lambda *p: os.path.join(root, *p)
    ex = []
    # RAW/JPEG-Paar mit Sidecars in einem Ereignis
    bild(j("2019 BILDER", "Hochzeit Meier", "IMG_0001.jpg"), 1)
    bild(j("2019 BILDER", "Hochzeit Meier", "IMG_0001.dng"), 1)
    datei(j("2019 BILDER", "Hochzeit Meier", "IMG_0001.xmp"), b"<x:xmpmeta/>")
    ex += [(j("2019 BILDER", "Hochzeit Meier", "IMG_0001.jpg"),
            {"DateTimeOriginal": "2019:07:14 15:30:12", "Model": "Canon EOS 5D"}),
           (j("2019 BILDER", "Hochzeit Meier", "IMG_0001.dng"),
            {"DateTimeOriginal": "2019:07:14 15:30:12", "Model": "Canon EOS 5D"})]
    # großes Bild ohne Caption + kleinere Web-Fassung mit Caption
    bild(j("2019 BILDER", "Hochzeit Meier", "IMG_0002.jpg"), 2)
    verkleinert(j("2019 BILDER", "Hochzeit Meier", "IMG_0002.jpg"), j("2019 BILDER", "Hochzeit Meier", "Web", "IMG_0002_web.jpg"))
    ex += [(j("2019 BILDER", "Hochzeit Meier", "IMG_0002.jpg"), {"DateTimeOriginal": "2019:07:14 16:00:00"}),
           (j("2019 BILDER", "Hochzeit Meier", "Web", "IMG_0002_web.jpg"),
            {"DateTimeOriginal": "2019:07:14 16:00:00", "IPTC:Caption-Abstract": "Brautpaar vor der Kirche"})]
    # bitgleiche Kopie in anderem Ordner
    bild(j("2019 BILDER", "Hochzeit Meier", "IMG_0003.jpg"), 3)
    ex += [(j("2019 BILDER", "Hochzeit Meier", "IMG_0003.jpg"), {"DateTimeOriginal": "2019:07:14 17:00:00"})]
    # Serienbilder derselben Sekunde (verschiedene Motive)
    bild(j("2019 BILDER", "Hochzeit Meier", "IMG_0004.jpg"), 4)
    bild(j("2019 BILDER", "Hochzeit Meier", "IMG_0005.jpg"), 5)
    ex += [(j("2019 BILDER", "Hochzeit Meier", "IMG_0004.jpg"), {"DateTimeOriginal": "2019:07:14 18:00:00"}),
           (j("2019 BILDER", "Hochzeit Meier", "IMG_0005.jpg"), {"DateTimeOriginal": "2019:07:14 18:00:00"})]
    # falscher Jahresordner (Aufnahme 2018)
    bild(j("2019 BILDER", "Urlaub", "IMG_0100.jpg"), 100)
    ex += [(j("2019 BILDER", "Urlaub", "IMG_0100.jpg"), {"DateTimeOriginal": "2018:08:01 10:00:00"})]
    # Scan ohne EXIF, Datum im Ordnernamen
    bild(j("2020 BILDER", "Scans 2020-05-01", "scan001.tif"), 200)
    # nicht beschreibbares Bild
    bild(j("2020 BILDER", "Diverses", "grafik.bmp"), 201)
    # Video im Jahresordner
    video(j("2020 BILDER", "Diverses", "MVI_0300.mov"), "2020-06-01T12:00:00Z")
    # Export-Ordner über mehrere Jahre + Live-Photo
    for i, (y, s) in enumerate([(2016, 500), (2018, 501), (2020, 502), (2021, 503)]):
        p = j("Apple Export", f"IMG_{5000 + i}.jpg")
        bild(p, s)
        ex.append((p, {"DateTimeOriginal": f"{y}:03:0{i + 1} 09:00:00"}))
    video(j("Apple Export", "IMG_5003.mov"), "2021-03-04T09:00:00Z", "red")
    # Müll und Sonstiges
    datei(j("2019 BILDER", "Thumbs.db"))
    datei(j("2019 BILDER", "Hochzeit Meier", "._IMG_0001.jpg"))
    datei(j(".DS_Store"))
    datei(j("2019 BILDER", "Hochzeit Meier", "Ablauf.pdf"), b"%PDF-1.4")
    # Altbestand einer früheren Version
    alt = j("2017 BILDER", "20170101_120000_AB20260929_00007.jpg")
    bild(alt, 700)
    ex += [(alt, {"DateTimeOriginal": "2017:01:01 12:00:00", "EXIF:ImageUniqueID": "20170101_120000_AB20260929_00007",
                  "EXIF:UserComment": "DSC_1234.JPG"})]
    exif_setzen(ex)
    # bitgleiche Kopie erst nach dem Setzen der Metadaten
    os.makedirs(j("2019 BILDER", "Kopie"), exist_ok=True)
    shutil.copy2(j("2019 BILDER", "Hochzeit Meier", "IMG_0003.jpg"), j("2019 BILDER", "Kopie", "IMG_0003.jpg"))
    return root


def baue_quelle(root, sammlung_root):
    """Importquelle: neue Bilder, eine bitgleiche Kopie aus der Sammlung, eine Fassung mit Caption."""
    j = lambda *p: os.path.join(root, *p)
    ex = []
    for i in range(3):
        p = j("Geburtstag Oma", f"DSC_{9000 + i}.jpg")
        bild(p, 900 + i)
        ex.append((p, {"DateTimeOriginal": f"2019:11:0{i + 1} 14:00:00"}))
    # neues Bild aus 2022 (neuer Jahresordner)
    p = j("Geburtstag Oma", "DSC_9100.jpg")
    bild(p, 910)
    ex.append((p, {"DateTimeOriginal": "2022:02:02 10:00:00"}))
    # schon in der Sammlung (IMG_0004), dazu ein RAW, das die Sammlung noch nicht hat
    for ext in ("jpg", "dng"):
        p = j("Hochzeit Kopie", f"IMG_0004.{ext}")
        bild(p, 4)
        ex.append((p, {"DateTimeOriginal": "2019:07:14 18:00:00"}))
    # IMG_0005 mit Bildunterschrift: ersetzt die Fassung ohne Caption in der Sammlung
    p = j("Hochzeit Kopie", "IMG_0005.jpg")
    bild(p, 5)
    ex.append((p, {"DateTimeOriginal": "2019:07:14 18:00:00", "IPTC:Caption-Abstract": "Tanz"}))
    exif_setzen(ex)
    return root
