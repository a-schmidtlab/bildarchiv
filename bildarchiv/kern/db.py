"""Arbeitsdatenbank einer Sammlung (SQLite)."""

import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS dateien (
    bereich TEXT NOT NULL,          -- 'sammlung' | 'import'
    pfad TEXT NOT NULL,             -- relativ zur Wurzel des Bereichs
    groesse INT, mtime REAL, art TEXT, ext TEXT,
    dto TEXT, subsec TEXT, createdate TEXT, modell TEXT, serie TEXT,
    breite INT, hoehe INT, caption INT,
    uid TEXT,                       -- ImageUniqueID
    pfn TEXT,                       -- XMP-xmpMM:PreservedFileName
    usercomment TEXT,               -- EXIF:UserComment (alter Name in der Vorgängerversion)
    gelesen INT DEFAULT 0,          -- Metadaten gelesen
    datahash TEXT, dhash TEXT, aspect REAL, thumb BLOB,
    rolle TEXT, herkunft TEXT,
    PRIMARY KEY (bereich, pfad));
CREATE INDEX IF NOT EXISTS idx_dateien_hash ON dateien(ext, datahash);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS plan (
    id INTEGER PRIMARY KEY, aktion TEXT, quelle TEXT, ziel TEXT, gruppe TEXT, info TEXT);
CREATE TABLE IF NOT EXISTS bericht (
    id INTEGER PRIMARY KEY, kategorie TEXT, daten TEXT);
CREATE TABLE IF NOT EXISTS dubletten (
    cluster INT, nr INT, bereich TEXT, pfad TEXT, rolle TEXT, art TEXT,
    breite INT, hoehe INT, groesse INT, caption INT, unsicher INT);
CREATE TABLE IF NOT EXISTS zaehler (schluessel TEXT PRIMARY KEY, wert INT);
CREATE TABLE IF NOT EXISTS entscheidungen (
    bereich TEXT, pfad TEXT, art TEXT, PRIMARY KEY (bereich, pfad));
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY, schritt TEXT, args TEXT, status TEXT, pid INT,
    phase TEXT, fertig INT, gesamt INT, gestartet REAL, beendet REAL, ergebnis TEXT);
CREATE TABLE IF NOT EXISTS job_log (id INTEGER PRIMARY KEY, job INT, zeit REAL, stufe TEXT, text TEXT);
"""

SPALTEN = ["bereich", "pfad", "groesse", "mtime", "art", "ext", "dto", "subsec", "createdate", "modell",
           "serie", "breite", "hoehe", "caption", "uid", "pfn", "usercomment", "gelesen", "datahash", "dhash",
           "aspect", "rolle", "herkunft"]


def oeffne(pfad):
    Path(pfad).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(pfad, timeout=30, check_same_thread=False)
    db.executescript(SCHEMA)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    return db


def meta_get(db, key, default=None):
    r = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return r[0] if r else default


def meta_set(db, key, value):
    db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, value))


def meta_json(db, key, default=None):
    v = meta_get(db, key)
    return json.loads(v) if v else default


def meta_set_json(db, key, value):
    meta_set(db, key, json.dumps(value, ensure_ascii=False))


def zaehler_alle(db):
    return dict(db.execute("SELECT schluessel, wert FROM zaehler"))


def zaehler_setzen(db, werte):
    db.executemany("INSERT INTO zaehler VALUES (?,?) ON CONFLICT(schluessel) DO UPDATE SET "
                   "wert=max(wert, excluded.wert)", list(werte.items()))


def dateien(db, bereich, spalten=None, wo="", args=()):
    spalten = spalten or SPALTEN
    q = f"SELECT {','.join(spalten)} FROM dateien WHERE bereich=? {wo}"
    return [dict(zip(spalten, r)) for r in db.execute(q, (bereich, *args))]


def entscheidungen(db):
    return {(b, p): a for b, p, a in db.execute("SELECT bereich, pfad, art FROM entscheidungen")}
