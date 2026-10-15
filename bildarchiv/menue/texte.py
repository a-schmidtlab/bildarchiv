"""Textbausteine für Menü und Kommandozeile."""

from ..einstellungen import TEXTE


def zahl(n):
    return f"{n:,}".replace(",", ".") if isinstance(n, int) else str(n)


def befund_text(b, e=None):
    a = b["arten"]
    z = []
    z.append(f"Dateien      {zahl(b['dateien'])}   Bilder {zahl(a.get('bild', 0))} · Videos {zahl(a.get('video', 0))}"
             f" · Sidecars {zahl(a.get('sidecar', 0) + a.get('audio', 0))} · Sonstiges {zahl(a.get('sonstiges', 0))}")
    z.append(f"Tiefe        bis {b['tiefe']} Ebenen, {zahl(b['ordner'])} Ordner")
    z.append(f"Müll         {zahl(b['muell'])} (._*, Thumbs.db, .DS_Store …)")
    if b["ordnermuster"]:
        m = b["ordnermuster"][0]
        z.append(f"Ordnermuster {m['anzahl']}× „{m['beispiele'][0]}“ → Vorlage {m['vorlage']}")
        for m in b["ordnermuster"][1:3]:
            z.append(f"             {m['anzahl']}× „{m['beispiele'][0]}“")
    else:
        z.append("Ordnermuster keine Jahresordner erkannt")
    z.append(f"Jahresordner {len(b['jahresordner'])} erkannt")
    d = b["datum"]
    gesamt = sum(d.values()) or 1
    z.append(f"Datum        {d.get('EXIF', 0) * 100 // gesamt} % EXIF · {d.get('Name', 0) * 100 // gesamt} % aus Namen"
             f" · {d.get('ohne', 0) * 100 // gesamt} % ohne Datum")
    z.append(f"Abweichung   {zahl(b['abweichung'])} Bilder liegen in einem anderen Jahresordner als ihr Aufnahmejahr")
    sonder = []
    if b["c1"]:
        sonder.append(f"{b['c1_sessions']} Capture-One-Sessions ({zahl(b['c1'])} Dateien)")
    if b["reedit"]:
        sonder.append(f"{b['reedit']} Re-Edit-Ordner")
    if b["export"]:
        sonder.append(f"{b['export']} Export-Ordner")
    if b.get("sammelordner"):
        sonder.append(f"{b['sammelordner']} Sammelordner über viele Jahre")
    if b["live_photos"]:
        sonder.append(f"{zahl(b['live_photos'])} Live-Photos")
    z.append("Sonderfälle  " + (" · ".join(sonder) or "keine"))
    n = b["namen"]
    gn = sum(n.values()) or 1
    z.append("Namen        " + " · ".join(f"{v * 100 // gn} % {k}" for k, v in sorted(n.items(), key=lambda x: -x[1])))
    if b["kuerzel"]:
        z.append("Altbestand   " + ", ".join(f"{zahl(v)} mit Kürzel {k}" for k, v in sorted(b["kuerzel"].items()))
                 + " (werden nicht neu benannt)")
    if b.get("vorschlaege"):
        z.append("")
        z.append("Vorschläge für die Einstellungen:")
        for v in b["vorschlaege"]:
            w = v["wert"]
            if isinstance(w, dict):
                w = f"{len(w)} Ordnerentscheidungen"
            elif isinstance(w, list):
                w = ", ".join(w)
            z.append(f"  {v['gruppe']}.{v['schluessel']} = {TEXTE.get(w, w) if isinstance(w, str) else w}")
    return z


def einstellungen_text(e):
    s, n, m, d = e["struktur"], e["namen"], e["metadaten"], e["dubletten"]
    z = [f"Struktur        {TEXTE[s['art']]}",
         f"Jahresordner    {s['jahresordner']}"]
    if s["art"] == "jahr_monat":
        z.append(f"Monatsordner    {s['monatsordner']}")
    if s["art"] == "jahr_ereignis":
        z.append(f"Ereignisordner  {s['ereignisordner']}")
    z += [f"Videos          {TEXTE[s['videos']]}" + (f" ({s['videoordner']})" if s["videos"] == "eigener_ordner" else ""),
          f"Müll            {TEXTE[s['muell']]}",
          f"Capture One     {TEXTE[s['capture_one']]}, Trash: {TEXTE[s['c1_trash']]}",
          f"Datumskonflikt  {TEXTE[s['datum_konflikt']]}",
          f"Dubletten       {TEXTE[d['ablage']]} in {d['ordner']}" + ("" if d["aehnliche"] else " (nur bitgleiche)"),
          f"Namensvorlage   {n['vorlage']}  (Kürzel {n['kuerzel']}, Zähler {TEXTE.get(n['zaehler'], n['zaehler'])})",
          f"alter Name   →  {m['altname']}",
          f"Ordnerpfad   →  {m['ordnerpfad']}",
          f"neuer Name   →  {m['neuname']}"]
    if m["zusatz"]:
        z.append(f"zusätzlich      {', '.join(m['zusatz'])}")
    if n["frueher"]:
        z.append(f"frühere Namen   {', '.join(n['frueher'])}")
    entsch = s["ordner"]
    if entsch:
        z.append(f"Ordner          {len(entsch)} Entscheidungen")
    return z
