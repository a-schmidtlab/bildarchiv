"""Namensvorlagen für Dateien und Ordner.

Syntax:
    {platzhalter}                     {datum}, {nr}, {kürzel}
    {platzhalter:format}              {datum:JJJJ-MM-TT}, {zeit:hh-mm}, {nr:5}, {hash:6}
    {platzhalter|filter|filter:arg}   {ereignis|slug|klein|max:30}, {kamera|oder:unbekannt}
    [ ... ]                           optionaler Teil, entfällt, wenn ein Platzhalter darin leer ist
    {{ }} [[ ]]                       Klammern als Text

Aus derselben Vorlage entsteht ein regulärer Ausdruck, mit dem bereits benannte Dateien
erkannt und ihre Bestandteile (Datum, Nummer …) zurückgelesen werden.
"""

import re
import unicodedata
from dataclasses import dataclass, field

VERBOTEN = set('<>:"/\\|?*')

# Platzhalter -> Art
ARTEN = {
    "datum": "datum", "lauf": "datum", "zeit": "zeit",
    "jahr": "jahr", "monat": "monat", "tag": "tag",
    "nr": "zahl", "hash": "hex", "altnr": "ziffern",
    "kuerzel": "kuerzel",
    "ereignis": "text", "ordner": "text", "kamera": "text", "serie": "text", "altname": "text",
}
ALIAS = {"kürzel": "kuerzel"}
STANDARD_FMT = {"datum": "JJJJMMTT", "lauf": "JJJJMMTT", "zeit": "hhmmss", "nr": "4", "hash": "8"}
FILTER = {"slug", "klein", "gross", "max", "oder"}
# Platzhalter, die leer sein können (dann besser in [...] oder mit |oder:…)
KANN_LEER = {"ereignis", "kamera", "serie", "altnr", "ordner"}

DATUM_TOKEN = re.compile(r"(JJJJ|JJ|MM|TT)")
ZEIT_TOKEN = re.compile(r"(hh|mm|ss)")
TRENNER = set("-_. ")

BESCHREIBUNG = {
    "datum": "Aufnahmedatum (Format JJJJ MM TT, Vorgabe JJJJMMTT)",
    "zeit": "Aufnahmezeit (Format hh mm ss, Vorgabe hhmmss)",
    "jahr": "Aufnahmejahr", "monat": "Aufnahmemonat", "tag": "Aufnahmetag",
    "nr": "Zähler (Format = Stellen, z. B. {nr:5})",
    "kuerzel": "frei wählbares Kürzel",
    "lauf": "Datum des Laufs (Format wie datum)",
    "ereignis": "Name des Ereignis-/Unterordners",
    "ordner": "oberster Quellordner",
    "kamera": "Kameramodell", "serie": "Seriennummer der Kamera",
    "altname": "alter Dateiname ohne Endung",
    "altnr": "Nummer aus dem alten Namen (IMG_4711 → 4711)",
    "hash": "Anfang des Bild-Hashs (Format = Zeichen)",
}

BEISPIELE = {
    "Archiv": "{datum}_{zeit}_{kürzel}{lauf}_{nr:5}",
    "Kurz": "{datum:JJJJ-MM-TT}_{zeit}_{nr:4}",
    "Mit Ereignis": "{datum}_{ereignis|slug|klein|max:30}_{nr:4}",
    "Mit Kamera": "{datum}_{zeit}[_{kamera|slug|klein}]_{nr:4}",
}
# Schema der Vorgängerversion (Kürzel beliebig), damit deren Namen als Altbestand erkannt werden
ALTSCHEMATA = ["{datum}_{zeit}_{kürzel}{lauf}_{nr:5}"]


class VorlagenFehler(ValueError):
    def __init__(self, text, pos=None):
        super().__init__(text if pos is None else f"{text} (Zeichen {pos + 1})")
        self.text, self.pos = text, pos


@dataclass
class Text:
    wert: str


@dataclass
class Feld:
    name: str
    fmt: str
    filter: list = field(default_factory=list)   # [(name, arg)]

    @property
    def art(self):
        return ARTEN[self.name]


@dataclass
class Optional:
    teile: list


@dataclass
class Meldung:
    stufe: str      # 'fehler' | 'warnung' | 'hinweis'
    text: str


# --------------------------------------------------------------------------- Parser

def _parse_feld(spec, pos):
    teile = spec.split("|")
    name, _, fmt = teile[0].partition(":")
    name = name.strip().lower()
    name = ALIAS.get(name, name)
    if name not in ARTEN:
        raise VorlagenFehler(f"Unbekannter Platzhalter {{{name}}}. Erlaubt: "
                             + ", ".join("{" + ("kürzel" if n == "kuerzel" else n) + "}" for n in ARTEN), pos)
    art = ARTEN[name]
    fmt = fmt.strip() or STANDARD_FMT.get(name, "")
    if art == "datum":
        rest = DATUM_TOKEN.sub("", fmt)
        if not DATUM_TOKEN.search(fmt) or set(rest) - TRENNER:
            raise VorlagenFehler(f"Datumsformat '{fmt}' ungültig (erlaubt: JJJJ JJ MM TT und - _ . Leerzeichen)", pos)
    elif art == "zeit":
        rest = ZEIT_TOKEN.sub("", fmt)
        if not ZEIT_TOKEN.search(fmt) or set(rest) - TRENNER:
            raise VorlagenFehler(f"Zeitformat '{fmt}' ungültig (erlaubt: hh mm ss und - _ . Leerzeichen)", pos)
    elif art in ("zahl", "hex"):
        if not fmt.isdigit() or not 1 <= int(fmt) <= 12:
            raise VorlagenFehler(f"{{{name}:{fmt}}}: Format muss eine Stellenzahl von 1 bis 12 sein", pos)
        fmt = str(int(fmt))
    elif fmt:
        raise VorlagenFehler(f"{{{name}}} hat kein Format", pos)

    filt = []
    for f in teile[1:]:
        fname, sep, arg = f.partition(":")
        fname = fname.strip().lower()
        if fname not in FILTER:
            raise VorlagenFehler(f"Unbekannter Filter '{fname}'. Erlaubt: {', '.join(sorted(FILTER))}", pos)
        if fname == "max":
            if not arg.strip().isdigit() or int(arg) < 1:
                raise VorlagenFehler("Filter max braucht eine Zahl, z. B. max:30", pos)
            arg = int(arg)
        elif fname == "oder":
            if not arg:
                raise VorlagenFehler("Filter oder braucht einen Ersatztext, z. B. oder:unbekannt", pos)
            if set(arg) & VERBOTEN:
                raise VorlagenFehler(f"Ersatztext '{arg}' enthält ein unzulässiges Zeichen", pos)
        elif sep:
            raise VorlagenFehler(f"Filter {fname} hat kein Argument", pos)
        else:
            arg = None
        filt.append((fname, arg))
    return Feld(name, fmt, filt)


def parse(text):
    teile = []
    cur = teile
    buf = []
    opt_start = None

    def flush():
        if buf:
            cur.append(Text("".join(buf)))
            buf.clear()

    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "{":
            if text.startswith("{{", i):
                buf.append("{")
                i += 2
                continue
            j = text.find("}", i)
            if j < 0:
                raise VorlagenFehler("'{' ohne schließende '}'", i)
            flush()
            cur.append(_parse_feld(text[i + 1:j], i))
            i = j + 1
        elif c == "}":
            if text.startswith("}}", i):
                buf.append("}")
                i += 2
                continue
            raise VorlagenFehler("'}' ohne öffnende '{'", i)
        elif c == "[":
            if text.startswith("[[", i):
                buf.append("[")
                i += 2
                continue
            if cur is not teile:
                raise VorlagenFehler("Optionale Teile [...] lassen sich nicht verschachteln", i)
            flush()
            opt = Optional([])
            teile.append(opt)
            cur, opt_start = opt.teile, i
            i += 1
        elif c == "]":
            if cur is not teile:
                flush()
                cur = teile
                i += 1
                continue
            if text.startswith("]]", i):
                buf.append("]")
                i += 2
                continue
            raise VorlagenFehler("']' ohne öffnende '['", i)
        else:
            if c in VERBOTEN or ord(c) < 32:
                raise VorlagenFehler(f"Das Zeichen '{c}' ist in Dateinamen nicht erlaubt", i)
            buf.append(c)
            i += 1
    if cur is not teile:
        raise VorlagenFehler("'[' ohne schließende ']'", opt_start)
    flush()
    return teile


# --------------------------------------------------------------------------- Werte

UMLAUTE = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"})


def slug(s):
    s = unicodedata.normalize("NFKD", s.translate(UMLAUTE))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^A-Za-z0-9]+", "-", s)
    return s.strip("-")


def bereinige(s):
    """Unzulässige Zeichen aus einem eingesetzten Wert entfernen."""
    return "".join("-" if c in VERBOTEN or ord(c) < 32 else c for c in str(s))


def altnr_aus(altname):
    m = re.findall(r"\d+", altname or "")
    return m[-1] if m else ""


def _datum_fmt(fmt, d):
    teile = {"JJJJ": d[:4], "JJ": d[2:4], "MM": d[4:6], "TT": d[6:8]}
    return DATUM_TOKEN.sub(lambda m: teile[m[1]], fmt)


def _zeit_fmt(fmt, t):
    teile = {"hh": t[:2], "mm": t[2:4], "ss": t[4:6]}
    return ZEIT_TOKEN.sub(lambda m: teile[m[1]], fmt)


def _feldwert(f, w):
    art = f.art
    if art == "datum":
        d = str(w.get(f.name) or "")
        v = _datum_fmt(f.fmt, d) if len(d) == 8 and d.isdigit() else ""
    elif art == "zeit":
        t = str(w.get("zeit") or "")
        v = _zeit_fmt(f.fmt, t) if len(t) == 6 and t.isdigit() else ""
    elif art == "jahr":
        v = str(w.get("jahr") or str(w.get("datum") or "")[:4])
    elif art == "monat":
        v = str(w.get("monat") or str(w.get("datum") or "")[4:6])
    elif art == "tag":
        v = str(w.get("tag") or str(w.get("datum") or "")[6:8])
    elif art == "zahl":
        nr = w.get("nr")
        v = str(int(nr)).zfill(int(f.fmt)) if nr is not None else ""
    elif art == "hex":
        h = re.sub(r"[^0-9a-f]", "", str(w.get("hash") or "").lower().split(":")[-1])
        v = h[:int(f.fmt)]
    elif art == "ziffern":
        v = str(w.get("altnr") or altnr_aus(w.get("altname")))
    else:
        v = str(w.get(f.name) or "")
    v = bereinige(v)
    for fname, arg in f.filter:
        if fname == "slug":
            v = slug(v)
        elif fname == "klein":
            v = v.lower()
        elif fname == "gross":
            v = v.upper()
        elif fname == "max":
            v = v[:arg].rstrip("-_ .")
        elif fname == "oder" and not v:
            v = arg
    return v


# --------------------------------------------------------------------------- Vorlage

class Vorlage:
    def __init__(self, text):
        self.text = text
        self.teile = parse(text)

    def __repr__(self):
        return f"Vorlage({self.text!r})"

    def felder(self, mit_optional=True):
        for t in self.teile:
            if isinstance(t, Feld):
                yield t
            elif isinstance(t, Optional) and mit_optional:
                yield from (x for x in t.teile if isinstance(x, Feld))

    def namen(self, mit_optional=True):
        return {f.name for f in self.felder(mit_optional)}

    def render(self, werte):
        out = []
        for t in self.teile:
            if isinstance(t, Text):
                out.append(t.wert)
            elif isinstance(t, Feld):
                out.append(_feldwert(t, werte))
            else:
                part, leer = [], False
                for x in t.teile:
                    if isinstance(x, Text):
                        part.append(x.wert)
                    else:
                        v = _feldwert(x, werte)
                        leer |= not v
                        part.append(v)
                if not leer:
                    out.extend(part)
        return "".join(out).strip(" ").rstrip(". ")

    # ------------------------------------------------------------------ Erkennen

    def muster(self, kuerzel=None):
        """Regulärer Ausdruck für mit dieser Vorlage erzeugte Namen.

        kuerzel=None erkennt ein beliebiges Kürzel aus Buchstaben (für Altschemata)."""
        gesehen = set()

        def gruppe(name, rx):
            if name in gesehen:
                return f"(?P={name})"
            gesehen.add(name)
            return f"(?P<{name}>{rx})"

        def feld_rx(f):
            art = f.art
            if art in ("datum", "zeit"):
                tok = DATUM_TOKEN if art == "datum" else ZEIT_TOKEN
                out = []
                for part in tok.split(f.fmt):
                    if not part:
                        continue
                    if tok.fullmatch(part):
                        out.append(gruppe(f"{f.name}_{part.lower()}", r"\d{4}" if part == "JJJJ" else r"\d{2}"))
                    else:
                        out.append(re.escape(part))
                rx = "".join(out)
            elif art == "jahr":
                rx = gruppe("jahr", r"\d{4}")
            elif art in ("monat", "tag"):
                rx = gruppe(f.name, r"\d{2}")
            elif art == "zahl":
                rx = gruppe("nr", r"\d{%s,}" % f.fmt)
            elif art == "hex":
                rx = gruppe("hash", r"[0-9a-f]{%s}" % f.fmt)
            elif art == "ziffern":
                rx = gruppe("altnr", r"\d+")
            elif art == "kuerzel":
                rx = re.escape(kuerzel) if kuerzel is not None else gruppe("kuerzel", r"[A-Za-z]+?")
            else:
                namen = {n for n, _ in f.filter}
                if "slug" in namen:
                    base = r"[a-z0-9-]+?" if "klein" in namen else (
                        r"[A-Z0-9-]+?" if "gross" in namen else r"[A-Za-z0-9-]+?")
                else:
                    base = r"[^/]+?"
                rx = gruppe(f.name, base)
            oder = [arg for n, arg in f.filter if n == "oder"]
            if oder and art not in ("datum", "zeit"):
                rx = f"(?:{rx}|{re.escape(oder[-1])})"
            return rx

        out = []
        for t in self.teile:
            if isinstance(t, Text):
                out.append(re.escape(t.wert))
            elif isinstance(t, Feld):
                out.append(feld_rx(t))
            else:
                inner = "".join(re.escape(x.wert) if isinstance(x, Text) else feld_rx(x) for x in t.teile)
                out.append(f"(?:{inner})?")
        return re.compile("^" + "".join(out) + "$")

    def lies(self, name, kuerzel=None, muster=None):
        """Bestandteile eines Namens zurücklesen, None wenn er nicht zur Vorlage passt."""
        m = (muster or self.muster(kuerzel)).fullmatch(name)
        if not m:
            return None
        g = {k: v for k, v in m.groupdict().items() if v is not None}
        res = {}
        for key in ("datum", "lauf"):
            if f"{key}_jjjj" in g:
                res[key] = g[f"{key}_jjjj"] + g.get(f"{key}_mm", "00") + g.get(f"{key}_tt", "00")
        if "datum" not in res and "jahr" in g:
            res["datum"] = g["jahr"] + g.get("monat", "00") + g.get("tag", "00")
        if any(k.startswith("zeit_") for k in g):
            res["zeit"] = g.get("zeit_hh", "00") + g.get("zeit_mm", "00") + g.get("zeit_ss", "00")
        if "nr" in g:
            res["nr"] = int(g["nr"])
        for k in ("kuerzel", "hash", "ereignis", "kamera", "serie", "altname", "ordner", "jahr"):
            if k in g:
                res[k] = g[k]
        return res


# --------------------------------------------------------------------------- Prüfung

SCOPE_BRAUCHT = {
    "gesamt": set(), "ordner": set(),
    "jahr": {"jahr"}, "monat": {"jahr", "monat"},
    "tag": {"jahr", "monat", "tag"}, "sekunde": {"jahr", "monat", "tag", "zeit"},
}
SCOPE_TEXT = {"jahr": "jedes Jahr", "monat": "jeden Monat", "tag": "jeden Tag", "sekunde": "jede Sekunde"}
ORDNER_DECKT = {"jahr": {"jahr"}, "jahr_monat": {"jahr", "monat"}, "jahr_ereignis": {"jahr"}}
TEIL_TEXT = {"jahr": "das Jahr", "monat": "den Monat", "tag": "den Tag", "zeit": "die Uhrzeit"}


def _bestandteile(v):
    """Welche Datums-/Zeitbestandteile die Vorlage sicher (außerhalb von [...]) enthält."""
    have = set()
    for f in v.felder(mit_optional=False):
        if f.name == "datum":
            toks = set(DATUM_TOKEN.findall(f.fmt))
            if "JJJJ" in toks:
                have.add("jahr")
            if "MM" in toks:
                have.add("monat")
            if "TT" in toks:
                have.add("tag")
        elif f.name in ("jahr", "monat", "tag"):
            have.add(f.name)
        elif f.name == "zeit" and set(ZEIT_TOKEN.findall(f.fmt)) >= {"hh", "mm", "ss"}:
            have.add("zeit")
    return have


def pruefe(text, zaehler="jahr", struktur="jahr", kuerzel="BA"):
    """Dateinamen-Vorlage prüfen. Liefert eine Liste von Meldungen (leer = alles gut)."""
    try:
        v = Vorlage(text)
    except VorlagenFehler as e:
        return [Meldung("fehler", str(e))]
    out = []
    pflicht = v.namen(mit_optional=False)
    if not text.strip():
        return [Meldung("fehler", "Die Vorlage ist leer.")]
    if "nr" not in pflicht and "hash" not in pflicht:
        if v.namen() & {"nr", "hash"}:
            out.append(Meldung("fehler", "{nr} bzw. {hash} darf nicht in einem optionalen Teil [...] stehen."))
        else:
            out.append(Meldung("fehler", "Die Vorlage braucht {nr} (oder {hash}), sonst sind die Namen nicht eindeutig."))
    if "nr" not in v.namen() and "hash" in pflicht:
        kurz = min(int(f.fmt) for f in v.felder(False) if f.name == "hash")
        if kurz < 8:
            out.append(Meldung("warnung", f"{{hash:{kurz}}} ist kurz. Bei gleichem Anfang wird _1, _2 … angehängt."))

    # Zählerbereich: Namen dürfen sich im selben Ordner nicht wiederholen
    have = _bestandteile(v)
    if "nr" in pflicht and zaehler in SCOPE_BRAUCHT:
        fehlt = SCOPE_BRAUCHT[zaehler] - have - ORDNER_DECKT.get(struktur, set())
        if zaehler in ("monat", "tag", "sekunde") and fehlt:
            teile = " und ".join(TEIL_TEXT[t] for t in sorted(fehlt, key=list(TEIL_TEXT).index))
            out.append(Meldung("fehler", f"Der Zähler beginnt {SCOPE_TEXT[zaehler]} neu, die Vorlage enthält aber "
                                         f"nicht {teile}. Namen würden sich im selben Ordner wiederholen."))
        elif "hash" not in pflicht:
            global_fehlt = SCOPE_BRAUCHT[zaehler] - have
            if zaehler == "ordner":
                out.append(Meldung("warnung", "Zähler pro Ordner: Namen sind nur innerhalb ihres Ordners eindeutig."))
            elif global_fehlt:
                teile = " und ".join(TEIL_TEXT[t] for t in sorted(global_fehlt, key=list(TEIL_TEXT).index))
                out.append(Meldung("warnung", f"Ohne {teile} im Namen sind die Namen nur innerhalb ihres "
                                              f"Ordners eindeutig, nicht in der ganzen Sammlung."))

    # Rücklesbarkeit: zwei freie Textfelder direkt hintereinander
    flach = []
    for t in v.teile:
        flach.extend(t.teile if isinstance(t, Optional) else [t])
    for a, b in zip(flach, flach[1:]):
        if isinstance(a, Feld) and isinstance(b, Feld) and a.art in ("text", "kuerzel") and b.art in ("text", "kuerzel"):
            out.append(Meldung("fehler", f"{{{a.name}}} und {{{b.name}}} stehen ohne Trenner hintereinander. "
                                         "Die Namen ließen sich nicht sicher zurücklesen."))
        elif isinstance(a, Feld) and isinstance(b, Feld) and {a.art, b.art} & {"text"} \
                and {a.art, b.art} & {"zahl", "ziffern"}:
            out.append(Meldung("warnung", f"{{{a.name}}} und {{{b.name}}} stehen ohne Trenner hintereinander."))

    # leere Werte
    for f in v.felder(mit_optional=False):
        if f.name in KANN_LEER and not any(n == "oder" for n, _ in f.filter):
            out.append(Meldung("hinweis", f"{{{f.name}}} kann leer sein. Besser in [...] setzen "
                                          f"oder |oder:… verwenden."))
    # Sortierung
    erstes = next((t for t in v.teile if not isinstance(t, Text)), None)
    if not (isinstance(erstes, Feld) and erstes.name in ("datum", "jahr")
            and (erstes.name == "jahr" or erstes.fmt.startswith("JJJJ"))):
        out.append(Meldung("hinweis", "Die Namen beginnen nicht mit dem Datum (JJJJ…). "
                                      "Die alphabetische Reihenfolge ist dann nicht chronologisch."))
    # Länge
    lang = v.render(dict(datum="20190714", zeit="153012", lauf="20261015", nr=10 ** 6, kuerzel=kuerzel,
                         ereignis="x" * 60, ordner="x" * 60, kamera="x" * 30, serie="x" * 16,
                         altname="x" * 40, altnr="1234", hash="f" * 32))
    if len(lang) > 120:
        out.append(Meldung("warnung", f"Namen können bis zu {len(lang)} Zeichen lang werden. "
                                      "Mit |max:N lässt sich das begrenzen."))
    return out


def pruefe_ordner(text, art):
    """Ordnervorlage prüfen: art = 'jahresordner' | 'monatsordner' | 'ereignisordner'."""
    try:
        v = Vorlage(text)
    except VorlagenFehler as e:
        return [Meldung("fehler", str(e))]
    have = _bestandteile(v)
    namen = v.namen(mit_optional=False)
    out = []
    if not text.strip():
        out.append(Meldung("fehler", "Die Vorlage ist leer."))
    if art == "jahresordner" and "jahr" not in have:
        out.append(Meldung("fehler", "Der Jahresordner braucht {jahr}."))
    if art == "monatsordner" and "monat" not in have:
        out.append(Meldung("fehler", "Der Monatsordner braucht {monat}."))
    if art == "ereignisordner" and "ereignis" not in namen:
        out.append(Meldung("fehler", "Der Ereignisordner braucht {ereignis}."))
    if "nr" in v.namen():
        out.append(Meldung("fehler", "{nr} ist in Ordnernamen nicht möglich."))
    return out


def hat_fehler(meldungen):
    return any(m.stufe == "fehler" for m in meldungen)
