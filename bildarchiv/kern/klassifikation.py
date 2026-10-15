"""Dateiarten erkennen: Bild, Sidecar, Video, Capture One, Müll, Sonstiges."""

RAW_EXT = {"cr2", "cr3", "crw", "nef", "nrw", "dng", "raf", "fff", "3fr", "arw",
           "srf", "sr2", "orf", "rw2", "pef", "srw", "iiq", "x3f", "mrw", "erf", "kdc"}
IMAGE_EXT = RAW_EXT | {"jpg", "jpeg", "tif", "tiff", "psd", "psb", "png", "gif",
                       "heic", "heif", "bmp", "jp2", "webp"}
SIDECAR_EXT = {"xmp", "acr", "xip", "aae"}
AUDIO_EXT = {"wav", "mp3"}
VIDEO_EXT = {"mp4", "mov", "avi", "mts", "m2ts", "m4v", "mpg", "mpeg", "wmv", "mkv", "3gp"}
C1_EXT = {"cos", "cop", "cot", "cof", "comask", "cosessiondb", "cocatalogdb", "coinfo",
          "cosessionbackup", "cocatalogbackup", "costyle", "cotemplate"}
JUNK_NAMES = {"thumbs.db", ".ds_store", "desktop.ini", "ws_ftp.log", "descript.ion", ".picasa.ini",
              "zbthumbnail.info", ".pmarrangement.dat", ".pm_lock", "pm.lock", ".dropbox", "icon\r"}
JUNK_EXT = {"tmp", "lock", "pm_lock", "thumb"}
# dHash + Miniatur für die Ähnlichkeitssuche
PHASH_EXT = {"jpg", "tif", "png", "heic", "webp"}
# Ersatz-Hash über Pixel, wenn exiftool kein ImageDataHash liefert
PIXEL_MD5_EXT = {"psd", "gif", "png", "tif", "bmp", "webp"}
EXT_NORM = {"jpeg": "jpg", "tiff": "tif", "heif": "heic"}
# Metadaten nur als XMP (kein EXIF-Block)
XMP_ONLY_EXT = VIDEO_EXT | {"gif"}
ORIGINAL_DIRS = {"originale", "original", "jpg original"}
# Reihenfolge, aus welchem Gruppenmitglied das Aufnahmedatum genommen wird (Videos zuletzt)
DATE_PRIO = ["raw", "heic", "tif", "psd", "jpg", "png"]

MEDIEN = ("bild", "video")


def ext_of(name):
    return name.rsplit(".", 1)[1].lower() if "." in name[1:] else ""


def norm_ext(ext):
    return EXT_NORM.get(ext, ext)


class Klassifikator:
    def __init__(self, sidecars_plus=(), muell_plus=()):
        self.sidecar = SIDECAR_EXT | {e.lower().lstrip(".") for e in sidecars_plus}
        # Einträge 'name.ext' = Dateiname, '*.ext' = Endung
        plus = [m.lower() for m in muell_plus]
        self.muell_namen = JUNK_NAMES | {m for m in plus if not m.startswith("*.")}
        self.muell_ext = JUNK_EXT | {m[2:] for m in plus if m.startswith("*.")}
        self.alle = IMAGE_EXT | self.sidecar | AUDIO_EXT | VIDEO_EXT

    def split_stem(self, name):
        """'IMG_1.CR2.xmp' -> ('IMG_1', '.CR2.xmp'), 'IMG_1.jpg' -> ('IMG_1', '.jpg')."""
        if "." not in name[1:]:
            return name, ""
        base, ext = name.rsplit(".", 1)
        e = ext.lower()
        if e not in self.alle or not base:
            return name, ""
        if e in self.sidecar | AUDIO_EXT and "." in base[1:]:
            b2, e2 = base.rsplit(".", 1)
            if b2 and e2.lower() in IMAGE_EXT | VIDEO_EXT:
                return b2, "." + e2 + "." + ext
        return base, "." + ext

    def art(self, rel):
        parts = rel.split("/")
        name = parts[-1]
        low = name.lower()
        ext = ext_of(name)
        if low.startswith("._") or low in self.muell_namen or ext in self.muell_ext \
                or low.startswith("~") or ext.startswith("~") or low == "icon" \
                or any(p.startswith(".") for p in parts[:-1]):
            return "muell"
        in_c1_dir = any(p.lower() == "captureone" or p.lower().endswith(".cocatalog") for p in parts[:-1])
        if ext in C1_EXT or (in_c1_dir and ext not in IMAGE_EXT | VIDEO_EXT):
            return "c1"
        if ext in IMAGE_EXT:
            return "bild"
        if ext in self.sidecar:
            return "sidecar"
        if ext in AUDIO_EXT:
            return "audio"
        if ext in VIDEO_EXT:
            return "video"
        return "sonstiges"
