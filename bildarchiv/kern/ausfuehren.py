"""Plan ausführen (mit Journal) und rückgängig machen."""

import csv
import json
import os
import random
import shutil
from collections import Counter, defaultdict
from datetime import datetime

from . import db as kdb
from . import exif, metadaten

REIHENFOLGE = ["ordner_verschieben", "loeschen", "dublette", "verschieben", "umbenennen", "importieren"]
PHASENTEXT = {
    "ordner_verschieben": "Ordner an die Wurzel verschieben", "loeschen": "Löschen",
    "dublette": "Dubletten ablegen", "verschieben": "Verschieben", "umbenennen": "Umbenennen",
    "importieren": "Importieren",
}
ROLLE = {"dublette": "dublette", "umbenennen": "master", "importieren": "master"}
ROLLE_GRUND = {"Müll": "muell", "Sonstiges": "sonstiges", "Video": "video", "Video-Sidecar": "video",
               "Capture-One-Trash": "c1_trash", "Altbestand": "master"}


class AusfuehrFehler(Exception):
    def __init__(self, probleme):
        super().__init__(f"{len(probleme)} Probleme")
        self.probleme = probleme


def lade_plan(db):
    return [dict(id=i, aktion=a, quelle=q, ziel=z, gruppe=g, info=json.loads(inf or "{}"))
            for i, a, q, z, g, inf in db.execute("SELECT id, aktion, quelle, ziel, gruppe, info FROM plan ORDER BY id")]


def kopiere(src, dst):
    """Daten kopieren, Zeitstempel übernehmen (Rechte nicht: exFAT kennt keine)."""
    shutil.copyfile(src, dst)
    try:
        st = os.stat(src)
        os.utime(dst, ns=(st.st_atime_ns, st.st_mtime_ns))
    except OSError:
        pass


def verschiebe(src, dst):
    try:
        os.rename(src, dst)
    except OSError as err:
        if err.errno != 18:     # EXDEV: anderes Laufwerk
            raise
        kopiere(src, dst)
        os.remove(src)


def _wurzeln(sammlung):
    q = kdb.meta_get(sammlung.db, "plan_quelle") or ""
    return {"sammlung": sammlung.wurzel, "import": q}


def pruefe(sammlung):
    """Ist der Plan ausführbar? Liefert eine Liste von Problemen (leer = ja)."""
    db = sammlung.db
    plan = lade_plan(db)
    if not plan:
        return ["Kein Plan vorhanden (oder nichts zu tun)."]
    pid = kdb.meta_get(db, "plan_id")
    if kdb.meta_get(db, "plan_ausgefuehrt") == pid:
        return ["Dieser Plan wurde bereits ausgeführt. Für neue Änderungen erst neu planen."]
    if kdb.meta_get(db, "plan_einstellungen_stand", "") != kdb.meta_get(db, "einstellungen_geaendert", ""):
        return ["Die Einstellungen wurden nach dem Plan geändert. Bitte neu planen."]
    w = _wurzeln(sammlung)
    if kdb.meta_get(db, "plan_modus") == "import" and not os.path.isdir(w["import"]):
        return [f"Quellordner nicht gefunden: {w['import']}"]
    # Plätze, die eine Bestandsdatei beim Import für ihre bessere Version freimacht
    frei = {r["quelle"].lower() for r in plan if r["aktion"] == "dublette" and r["info"].get("qb") == "sammlung"}
    probleme = []
    for r in plan:
        a, qb = r["aktion"], r["info"].get("qb", "sammlung")
        if a in ("kopie", "caption", "metadaten"):
            continue
        src = os.path.join(w[qb], r["quelle"])
        if not os.path.lexists(src):
            probleme.append(f"Quelle fehlt: {r['quelle']}")
        if r["ziel"]:
            dst = os.path.join(w["sammlung"], r["ziel"])
            if os.path.lexists(dst) and dst.lower() != src.lower() and r["ziel"].lower() not in frei:
                probleme.append(f"Ziel existiert schon: {r['ziel']}")
    return probleme


def _leere_ordner_entfernen(ordner, grenze):
    """Leer gewordene Ordner (und leere Eltern bis zur Grenze) entfernen."""
    entfernt = 0
    for d in sorted(ordner, key=lambda p: p.count("/"), reverse=True):
        while d and os.path.abspath(d) != os.path.abspath(grenze) and d.startswith(grenze):
            try:
                if os.path.isdir(d) and not os.listdir(d):
                    os.rmdir(d)
                    entfernt += 1
                else:
                    break
            except OSError:
                break
            d = os.path.dirname(d)
    return entfernt


def ausfuehren(sammlung, reporter, jobs=6):
    probleme = pruefe(sammlung)
    if probleme:
        raise AusfuehrFehler(probleme)
    db = sammlung.db
    e = sammlung.einstellungen
    plan = lade_plan(db)
    modus = kdb.meta_get(db, "plan_modus")
    import_modus = kdb.meta_get(db, "plan_import_modus") or "kopieren"
    w = _wurzeln(sammlung)
    W = w["sammlung"]
    stempel = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = sammlung.berichtordner("ausfuehren")
    journal_pfad = out / f"journal_{stempel}_{modus}.csv"
    jf = open(journal_pfad, "w", newline="", encoding="utf-8")
    jw = csv.writer(jf, delimiter=";")
    jw.writerow(["Aktion", "Quelle", "Ziel", "Status"])
    zaehl = Counter()
    fehler = []
    quellordner = set()

    # A. Dateioperationen
    for phase in REIHENFOLGE:
        rows = [r for r in plan if r["aktion"] == phase]
        if not rows:
            continue
        reporter.phase(PHASENTEXT[phase], len(rows))
        for i, r in enumerate(rows, 1):
            qb = r["info"].get("qb", "sammlung")
            src = os.path.join(w[qb], r["quelle"])
            dst = os.path.join(W, r["ziel"]) if r["ziel"] else ""
            aktion = phase if phase != "importieren" else f"import_{import_modus}"
            try:
                if phase == "loeschen":
                    os.remove(src)
                else:
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    if phase == "importieren" and import_modus == "kopieren":
                        kopiere(src, dst)
                    elif phase == "importieren":
                        verschiebe(src, dst)
                    else:
                        os.rename(src, dst)
                if phase != "importieren" or import_modus == "verschieben":
                    quellordner.add(os.path.dirname(src))
                status = "ok"
            except OSError as err:
                status = f"FEHLER {err}"
                fehler.append(f"{phase}: {r['quelle']}: {err}")
            r["ok"] = status == "ok"
            zaehl[phase] += r["ok"]
            jw.writerow([aktion, src, dst, status])
            reporter.fortschritt(i, len(rows))
            if i % 500 == 0:
                jf.flush()
    jf.flush()

    # B. Bildunterschriften übernehmen
    caps = [r for r in plan if r["aktion"] == "caption"]
    if caps:
        reporter.phase("Bildunterschriften übernehmen", len(caps))
        for i, r in enumerate(caps, 1):
            src = os.path.join(w[r["info"].get("qb", "sammlung")], r["quelle"])
            dst = os.path.join(W, r["ziel"])
            ok, err = metadaten.uebertrage_caption(src, dst)
            r["ok"] = ok
            zaehl["caption"] += ok
            jw.writerow(["caption", src, dst, "ok" if ok else f"FEHLER {err}"])
            reporter.fortschritt(i, len(caps))

    # C. Metadaten schreiben (an den neuen Orten)
    eintraege, werte_von = [], {}
    for r in plan:
        i = r["info"]
        if r["aktion"] in ("umbenennen", "dublette", "importieren") and r.get("ok"):
            werte = {k: i.get(k) for k in metadaten.INHALTE if i.get(k)}
            if werte:
                eintraege.append((r["ziel"], werte))
                werte_von[r["ziel"]] = werte
        elif r["aktion"] == "metadaten":
            werte = {"altname": i.get("altname")}
            eintraege.append((r["quelle"], werte))
            werte_von[r["quelle"]] = werte
    geschrieben = set()
    if eintraege:
        failed = metadaten.schreibe(W, eintraege, e, reporter, jobs)
        geschrieben = {rel for rel, _ in eintraege} - failed
        zaehl["metadaten"] = len(geschrieben)
        for rel in sorted(failed):
            side = metadaten.sidecar(W, rel, werte_von[rel], e)
            jw.writerow(["sidecar", os.path.join(W, rel), os.path.join(W, side) if side else "",
                         "ok" if side else "FEHLER Sidecar"])
            zaehl["sidecar"] += bool(side)
        if failed:
            reporter.meldung(f"Metadaten nicht in die Datei schreibbar: {len(failed)} (Sidecar angelegt: "
                             f"{zaehl['sidecar']})", "warnung")

    # D. Kopien der bleibenden Versionen in den Dubletten-Ordner
    kopien = [r for r in plan if r["aktion"] == "kopie"]
    if kopien:
        reporter.phase("Kopien der bleibenden Versionen", len(kopien))
        for i, r in enumerate(kopien, 1):
            src, dst = os.path.join(W, r["quelle"]), os.path.join(W, r["ziel"])
            try:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                kopiere(src, dst)
                status = "ok"
            except OSError as err:
                status = f"FEHLER {err}"
                fehler.append(f"kopie: {r['quelle']}: {err}")
            r["ok"] = status == "ok"
            zaehl["kopie"] += r["ok"]
            jw.writerow(["kopie", src, dst, status])
            reporter.fortschritt(i, len(kopien))
    jf.close()

    # E. Leer gewordene Ordner
    grenze = W if modus == "ordnen" else w["import"]
    if modus == "ordnen" or import_modus == "verschieben":
        zaehl["ordner_entfernt"] = _leere_ordner_entfernen(quellordner, grenze)

    # F. Datenbank nachführen
    reporter.phase("Datenbank nachführen")
    _db_nachfuehren(sammlung, plan, geschrieben, werte_von)
    kdb.meta_set_json(db, f"vorher:{journal_pfad.name}", dict(
        zaehler=kdb.zaehler_alle(db), festgeschrieben=sammlung.festgeschrieben,
        ordner=e["struktur"]["ordner"]))
    kdb.zaehler_setzen(db, kdb.meta_json(db, "plan_zaehler", {}))
    if modus == "ordnen":
        st = e["struktur"]
        for alt, neu in kdb.meta_json(db, "plan_wurzel_ordner", {}).items():
            st["ordner"].pop(alt, None)
            st["ordner"][neu] = "behalten"
        e["sammlung"]["festgeschrieben"] = True
    kdb.meta_set(db, "plan_ausgefuehrt", kdb.meta_get(db, "plan_id"))
    kdb.meta_set(db, "letzte_ausfuehrung", stempel)
    kdb.meta_set(db, "ausfuehrung_zeit", str(datetime.now().timestamp()))
    db.commit()
    sammlung.speichere_einstellungen()
    kdb.meta_set(db, "plan_einstellungen_stand", kdb.meta_get(db, "einstellungen_geaendert", ""))
    db.commit()

    # G. Kontrolle: Bilddaten nach dem Schreiben der Metadaten unverändert?
    abweichend = _hash_stichprobe(sammlung, geschrieben, reporter)

    # H. Berichte und Datenbank in die Sammlung
    ziel = sammlung.spezialordner / "berichte" / f"{stempel}_{modus}"
    ziel.mkdir(parents=True, exist_ok=True)
    shutil.copy2(journal_pfad, ziel / journal_pfad.name)
    plan_berichte = kdb.meta_get(db, "plan_berichte")
    if plan_berichte and os.path.isdir(plan_berichte):
        for p in os.listdir(plan_berichte):
            shutil.copy2(os.path.join(plan_berichte, p), ziel / p)
    gesichert = sammlung.sichere_datenbank(stempel)
    zus = [f"Ausgeführt am {stempel} ({modus})", ""]
    zus += [f"  {k:20} {v:7}" for k, v in sorted(zaehl.items())]
    if fehler:
        zus += ["", f"FEHLER: {len(fehler)} (siehe Journal)", *fehler[:20]]
    zus += ["", f"Hash-Stichprobe: {abweichend[0] - len(abweichend[1])}/{abweichend[0]} unverändert"]
    zus += [f"  ABWEICHUNG: {p}" for p in abweichend[1][:20]]
    zus += ["", f"Journal: {journal_pfad}", f"Datenbank gesichert: {gesichert}"]
    (out / "zusammenfassung.txt").write_text("\n".join(zus) + "\n", encoding="utf-8")
    shutil.copy2(out / "zusammenfassung.txt", ziel / "ausfuehrung.txt")
    for z in zus:
        reporter.meldung(z, "fehler" if z.startswith("FEHLER") or "ABWEICHUNG" in z else "info")
    return dict(journal=str(journal_pfad), anzahl=dict(zaehl), fehler=fehler, zusammenfassung=zus)


def _db_nachfuehren(sammlung, plan, geschrieben, werte_von):
    db = sammlung.db
    e = sammlung.einstellungen
    W = sammlung.wurzel
    quelle = kdb.meta_get(db, "plan_quelle") or ""
    spalten = [s for s in kdb.SPALTEN if s not in ("bereich", "pfad")] + ["thumb"]
    for phase in REIHENFOLGE + ["kopie"]:
        for r in plan:
            if r["aktion"] != phase or not r.get("ok"):
                continue
            q, z, i = r["quelle"], r["ziel"], r["info"]
            if phase == "loeschen":
                db.execute("DELETE FROM dateien WHERE bereich='sammlung' AND pfad=?", (q,))
            elif phase == "ordner_verschieben":
                n = len(q) + 1
                db.execute("UPDATE dateien SET herkunft=coalesce(herkunft, pfad), pfad=? || substr(pfad, ?) "
                           "WHERE bereich='sammlung' AND substr(pfad, 1, ?) = ?", (z + "/", n + 1, n, q + "/"))
            elif phase == "importieren":
                db.execute(f"INSERT OR REPLACE INTO dateien (bereich, pfad, {','.join(spalten)}) "
                           f"SELECT 'sammlung', ?, {','.join(spalten)} FROM dateien WHERE bereich='import' AND pfad=?",
                           (z, q))
                db.execute("UPDATE dateien SET herkunft=?, rolle=? WHERE bereich='sammlung' AND pfad=?",
                           (os.path.join(quelle, q), ROLLE_GRUND.get(i.get("grund"), "master"), z))
            elif phase == "kopie":
                db.execute(f"INSERT OR REPLACE INTO dateien (bereich, pfad, {','.join(spalten)}) "
                           f"SELECT 'sammlung', ?, {','.join(spalten)} FROM dateien WHERE bereich='sammlung' AND pfad=?",
                           (z, q))
                db.execute("UPDATE dateien SET rolle='dublette_kopie', herkunft=? WHERE bereich='sammlung' AND pfad=?",
                           (q, z))
            else:
                rolle = ROLLE.get(phase) or ROLLE_GRUND.get(i.get("grund"), "master")
                db.execute("DELETE FROM dateien WHERE bereich='sammlung' AND pfad=? AND ?<>?", (z, z, q))
                db.execute("UPDATE dateien SET pfad=?, herkunft=coalesce(herkunft, ?), rolle=? "
                           "WHERE bereich='sammlung' AND pfad=?", (z, q, rolle, q))
    # geschriebene Metadaten und neue Dateigrößen übernehmen
    m = e["metadaten"]
    for rel in geschrieben:
        werte = werte_von.get(rel, {})
        sets, args = [], []
        for inhalt, feld in (("neuname", m["neuname"]), ("altname", m["altname"])):
            if not werte.get(inhalt):
                continue
            tag = feld.split(":")[-1].lower()
            spalte = {"imageuniqueid": "uid", "preservedfilename": "pfn", "usercomment": "usercomment"}.get(tag)
            if spalte:
                sets.append(f"{spalte}=?")
                args.append(werte[inhalt])
        if sets:
            db.execute(f"UPDATE dateien SET {','.join(sets)} WHERE bereich='sammlung' AND pfad=?", (*args, rel))
    beruehrt = set(geschrieben) | {r["ziel"] for r in plan if r["aktion"] in ("caption", "kopie") and r.get("ok")}
    for r in plan:
        if r["aktion"] == "caption" and r.get("ok"):
            db.execute("UPDATE dateien SET caption=1 WHERE bereich='sammlung' AND pfad=?", (r["ziel"],))
    for rel in beruehrt:
        try:
            st = os.stat(os.path.join(W, rel))
            db.execute("UPDATE dateien SET groesse=?, mtime=? WHERE bereich='sammlung' AND pfad=?",
                       (st.st_size, st.st_mtime, rel))
        except OSError:
            pass
    db.commit()


def _hash_stichprobe(sammlung, geschrieben, reporter, je_endung=20):
    db = sammlung.db
    nach_ext = defaultdict(list)
    for rel in geschrieben:
        r = db.execute("SELECT ext, datahash FROM dateien WHERE bereich='sammlung' AND pfad=?", (rel,)).fetchone()
        if r and r[1] and not r[1].startswith("px:"):
            nach_ext[r[0]].append((rel, r[1]))
    probe = []
    for ext, liste in nach_ext.items():
        probe += random.sample(liste, min(je_endung, len(liste)))
    if not probe:
        return 0, []
    reporter.phase(f"Kontrolle der Bilddaten ({len(probe)} Stichproben)")
    res = exif.lies_json(["-api", "RequestTags=ImageDataHash", "-ImageDataHash"],
                         [os.path.join(sammlung.wurzel, p) for p, _ in probe])
    got = {os.path.relpath(x["SourceFile"], sammlung.wurzel): x.get("ImageDataHash") for x in res}
    abw = [p for p, h in probe if got.get(p) != h]
    kdb.meta_set_json(db, "hash_pruefung", {"stichprobe": len(probe), "abweichend": abw})
    db.commit()
    return len(probe), abw


# --------------------------------------------------------------------------- Rückgängig

def lies_journal(pfad):
    with open(pfad, encoding="utf-8") as fh:
        rows = list(csv.reader(fh, delimiter=";"))
    return [dict(aktion=r[0], quelle=r[1], ziel=r[2], status=r[3]) for r in rows[1:] if len(r) >= 4]


def journal_info(pfad):
    rows = lies_journal(pfad)
    return dict(anzahl=len(rows), ok=sum(r["status"].startswith("ok") for r in rows),
                arten=Counter(r["aktion"] for r in rows))


def rueckgaengig(sammlung, journal, reporter):
    db = sammlung.db
    W = sammlung.wurzel
    rows = lies_journal(journal)
    erledigt = kdb.meta_json(db, "rueckgaengig", {})
    name = os.path.basename(str(journal))
    if name in erledigt:
        raise AusfuehrFehler([f"Dieses Journal wurde bereits am {erledigt[name]} rückgängig gemacht."])
    reporter.phase("Rückgängig machen", len(rows))

    def rel(p):
        r = os.path.relpath(p, W)
        return None if r.startswith("..") else r

    zaehl = Counter()
    probleme = []
    ordner = set()
    for i, r in enumerate(reversed(rows), 1):
        if not r["status"].startswith("ok"):
            continue
        a, q, z = r["aktion"], r["quelle"], r["ziel"]
        try:
            if a in ("kopie", "sidecar", "import_kopieren"):
                os.remove(z)
                if rel(z):
                    db.execute("DELETE FROM dateien WHERE bereich='sammlung' AND pfad=?", (rel(z),))
                ordner.add(os.path.dirname(z))
                zaehl["entfernt"] += 1
            elif a in ("loeschen", "caption"):
                zaehl["nicht umkehrbar"] += 1
                continue
            elif a == "import_verschieben":
                os.makedirs(os.path.dirname(q), exist_ok=True)
                verschiebe(z, q)
                if rel(z):
                    db.execute("DELETE FROM dateien WHERE bereich='sammlung' AND pfad=?", (rel(z),))
                ordner.add(os.path.dirname(z))
                zaehl["zurückverschoben"] += 1
            else:
                os.makedirs(os.path.dirname(q), exist_ok=True)
                os.rename(z, q)
                rz, rq = rel(z), rel(q)
                if rz and rq:
                    if a == "ordner_verschieben":
                        n = len(rz) + 1
                        db.execute("UPDATE dateien SET pfad=? || substr(pfad, ?) WHERE bereich='sammlung' "
                                   "AND substr(pfad, 1, ?) = ?", (rq + "/", n + 1, n, rz + "/"))
                    else:
                        db.execute("UPDATE dateien SET pfad=? WHERE bereich='sammlung' AND pfad=?", (rq, rz))
                ordner.add(os.path.dirname(z))
                zaehl["zurückverschoben"] += 1
        except OSError as err:
            probleme.append(f"{a}: {z}: {err}")
        reporter.fortschritt(i, len(rows))
    _leere_ordner_entfernen(ordner, W)
    # Zählerstände und Festschreibung wie vor diesem Lauf (nur sinnvoll in umgekehrter Reihenfolge)
    vorher = kdb.meta_json(db, f"vorher:{name}")
    if vorher:
        db.execute("DELETE FROM zaehler")
        kdb.zaehler_setzen(db, vorher["zaehler"])
        e = sammlung.einstellungen
        e["sammlung"]["festgeschrieben"] = vorher["festgeschrieben"]
        e["struktur"]["ordner"] = vorher["ordner"]
        if not vorher["festgeschrieben"]:
            try:
                (sammlung.spezialordner / "einstellungen.toml").unlink()
            except OSError:
                pass
        sammlung.speichere_einstellungen()
        kdb.meta_set(db, "plan_einstellungen_stand", kdb.meta_get(db, "einstellungen_geaendert", ""))
    erledigt[name] = datetime.now().strftime("%Y-%m-%d %H:%M")
    kdb.meta_set_json(db, "rueckgaengig", erledigt)
    kdb.meta_set(db, "plan_ausgefuehrt", "")
    db.execute("DELETE FROM plan")
    db.commit()
    zus = [f"{k}: {v}" for k, v in sorted(zaehl.items())]
    if zaehl["nicht umkehrbar"]:
        zus.append("Gelöschte Dateien und übernommene Bildunterschriften lassen sich nur aus der Sicherung "
                   "wiederherstellen. Geschriebene Metadaten bleiben in den Dateien.")
    if probleme:
        zus += [f"FEHLER: {len(probleme)}", *probleme[:20]]
    for z in zus:
        reporter.meldung(z)
    return dict(anzahl=dict(zaehl), probleme=probleme)
