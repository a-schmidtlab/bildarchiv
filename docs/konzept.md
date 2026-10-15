# Konzept und Aufbau

Dieses Dokument beschreibt, wie `bildarchiv` arbeitet: Begriffe, Ablauf, Regeln und Programmaufbau.
Eine Kurzanleitung steht in der [README](../README.md).

## Zweck

1. Eine **inkonsistente Ordnerstruktur analysieren** und eine **konsistente Struktur vorschlagen**
2. **Dubletten erkennen** (bitgleich und ähnlich) und nachvollziehbar ablegen
3. **Ordnernamen und alte Dateinamen in die Metadaten** schreiben
4. Bilder **eindeutig umbenennen**
5. **Weitere Ordner** später in die einmal festgelegte Struktur **importieren**

Das Werkzeug kennt keine Sonderfälle für bestimmte Sammlungen. Struktur, Namen und Ausnahmen ergeben
sich aus der Analyse und werden im Menü bestätigt oder geändert.

## Grundbegriffe

| Begriff | Bedeutung |
|---|---|
| **Sammlung** | Ein Wurzelordner, der nach einer festgelegten Struktur geordnet ist oder werden soll |
| **Einstellungen** | Zielstruktur, Namensvorlage, Metadatenfelder, Dubletten-Ablage usw. Sie werden beim ersten Ausführen **festgeschrieben** und von jedem Import wiederverwendet |
| **Arbeitsdatenbank** | SQLite mit Bestand, Hashes, Plan und Zählerständen. Erlaubt Abbruch und Fortsetzen |
| **Plan** | Liste aller geplanten Aktionen (Dry-Run). Bis zum Ausführen wird nichts verändert |
| **Journal** | Protokoll eines ausgeführten Plans, Grundlage für „Rückgängig“ |

**Ordnen und Importieren sind derselbe Vorgang** mit unterschiedlicher Quelle:

- *Ordnen*: Quelle = die Sammlung selbst, Dateien werden innerhalb der Sammlung verschoben
- *Importieren*: Quelle = ein fremder Ordner, Dateien werden in die Sammlung kopiert (oder verschoben)

Beide durchlaufen dieselbe Pipeline (Einordnen → Gruppen → Altbestand → Datum → Dubletten → Partner →
Ziele und Namen → Plan). Beim Import ist die Zielstruktur vorgegeben, und der Dublettenabgleich läuft
zusätzlich gegen den Bestand der Sammlung.

## Menüführung

```
 bildarchiv – Fotos
 /pfad/zur/Sammlung
  1  Analyse                        ✔  15.10. 13:36  48.210 Dateien
  2  Zielstruktur festlegen         ✔  Jahresordner, flach
  3  Dubletten prüfen               ●  1.284 Gruppen, 37 unsicher
  4  Metadaten & Dateinamen         ✔  {datum}_{zeit}_{kürzel}{lauf}_{nr:5}
  5  Vorschau (Dry-Run)             ✔  2.940 Aktionen geplant
  6  Ausführen                      ○  bereit
  ───────────────────────────
  7  Ordner importieren …           ✗  erst wenn die Struktur festgelegt ist
  8  Rückgängig machen …            ✗  noch nichts ausgeführt
  9  Einstellungen ansehen
  0  Andere Sammlung öffnen
```

- Jeder Schritt kann erneut geöffnet werden. Ein Plan verfällt, sobald sich Einstellungen,
  Dubletten-Entscheidungen oder der Bestand ändern
- Nach dem ersten Ausführen sind Struktur und Zähler festgeschrieben (nur noch ansehen)
- Lange Läufe (Analyse, Hashes, Ausführen) laufen als eigener Prozess. Das Menü kann geschlossen und
  später wieder geöffnet werden, der Fortschritt steht in der Arbeitsdatenbank
- Beim Start wird geprüft, ob exiftool und die Python-Pakete vorhanden sind

### 1 Analyse

Liest den Bestand (exiftool, parallel) und zeigt einen Befund:

```
Dateien      48.210   Bilder 31.500 · Videos 2.300 · Sidecars 6.900 · Sonstiges 412
Tiefe        bis 9 Ebenen, 1.140 Ordner
Müll         18.200 (._*, Thumbs.db, .DS_Store …)
Ordnermuster 26× „20011231 FOTOS ARCHIV 2001“ → Vorlage {jahr}1231 FOTOS ARCHIV {jahr}
Datum        94 % EXIF · 4 % aus Namen · 2 % ohne Datum
Abweichung   3.120 Bilder liegen in einem anderen Jahresordner als ihr Aufnahmejahr
Sonderfälle  50 Capture-One-Sessions · 6 Re-Edit-Ordner · 1 Export-Ordner · 410 Live-Photos
Namen        61 % kamera · 22 % frei · 17 % altbestand
```

Erkannt werden:

- **Müll**: `._*`, Thumbs.db, .DS_Store, desktop.ini, Tempdateien, Programm-Caches
- **Sidecars**: xmp, acr, xip, aae, Sprachnotizen (wav/mp3) mit gleichem Basisnamen
- **Capture One**: Session-/Katalogdateien, Ordner Capture/Selects/Output/Trash
- **Re-Edit-Ordner**: Unterordner, deren Bilder überwiegend Jahre neben dem Ordnerjahr liegen
- **Export-Ordner**: Ordner mit Bildern aus vielen Jahren, deren Name nach Export klingt
  (Export, Apple, Fotos, iCloud, Takeout …). Andere Sammelordner über viele Jahre, etwa Portfolios,
  werden zum Unverändert-Lassen vorgeschlagen
- **Live-Photos**: Bild + MOV mit gleichem Basisnamen
- **Vorhandenes Ordnermuster**: Das häufigste Namensmuster der obersten Ordner wird als Vorlage
  für die Jahresordner vorgeschlagen. Vorhandene Jahresordner behalten ihren Namen
- **Altbestand**: Dateien, die bereits nach einer bekannten Vorlage benannt sind (siehe unten)
- **Datumsquellen**: DateTimeOriginal → CreateDate → QuickTime-Datum (Videos) → Datum im Datei-/Ordnernamen
  → Ordnerjahr → Änderungsdatum. Kamera-Standarddaten (1970/1980/2000-01-01) gelten als „kein Datum“

Beim ersten Lauf werden die Vorschläge automatisch übernommen. Später erscheinen sie als Liste und
lassen sich mit einem Knopf übernehmen.

### 2 Zielstruktur festlegen

| Struktur | Beispiel |
|---|---|
| **Jahr, flach** | `2019 FOTOS/20190714_153012_AB20261015_00123.jpg` |
| **Jahr / Monat** | `2019 FOTOS/2019-07/…` (ohne bekannten Monat direkt im Jahresordner) |
| **Jahr / Ereignis** | `2019 FOTOS/2019-07-14 Hochzeit Meier/…` (Unterordner bleiben, Namen vereinheitlicht) |

Weitere Einstellungen:

- Name der Jahresordner (Vorlage, z. B. `{jahr}1231 FOTOS ARCHIV {jahr}`) und Erkennungsmuster für
  vorhandene Jahresordner
- Videos: im Jahresordner (mit Namensschema) oder eigener Ordner `Videos/` (Namen bleiben)
- Müll: löschen, nach `_Muell/` verschieben oder unverändert lassen
- Capture One: Einstellungsdateien verwerfen; Bilder aus Trash nach `JAHR/_C1_Trash/`
- Ordnerentscheidungen je Ordner: als Ereignis / auflösen / unverändert lassen / an die Wurzel
- Widerspruch zwischen Datum im Ordnernamen und EXIF-Jahr: Ordner gewinnt oder EXIF gewinnt
- Nicht-Bilddateien: `_Sonstiges/`

### 3 Dubletten prüfen

Verglichen wird nur innerhalb desselben Formats (RAW/JPEG/TIFF/PSD sind nie Dubletten voneinander):

- **bitgleich**: `ImageDataHash` von exiftool (Bild-/Mediendaten, Metadaten egal), Ersatz-Hash über
  die Pixel für PSD/GIF
- **ähnlich**: dHash + Pixelprüfung 64×64 (Miniaturen 16×16), gleiches Seitenverhältnis, gleiche
  Aufnahmesekunde. Serienbilder und unterschiedlich bearbeitete Fassungen werden nicht zusammengelegt.
  Eine kleine Fassung, die zu mehreren verschiedenen Bildern passt, bleibt erhalten

Bleibende Version: größte Auflösung, dann Caption > (beim Import) Bestand > aus einem Jahresordner >
nicht aus „Originale“ > mehr Formate in der Gruppe > größere Datei. Fehlt ihr die Caption, wird sie
aus einer anderen Version übernommen. Im Menü lässt sich die bleibende Version überstimmen oder eine
Gruppe auflösen („keine Dublette“).

Ablage (wählbar): flach in `_Dubletten/` mit Präfix `D00001_`, ein Ordner pro Gruppe
(`_Dubletten/0001/`) oder pro Jahr (`JAHR/_Dubletten/0001/`). Die bleibende Version wird zur Kontrolle
dorthin **kopiert**, alle anderen Versionen werden **verschoben**. Gelöscht wird nichts.

### 4 Metadaten & Dateinamen

| Inhalt | Standardfeld | Hinweis |
|---|---|---|
| alter Dateiname | `XMP-xmpMM:PreservedFileName` | genau dafür vorgesehen |
| alter Ordnerpfad | `XMP-iptcExt:Event` | IPTC Extension |
| neuer Name | `EXIF:ImageUniqueID` | bei Videos/GIF die XMP-Variante |

- Weitere Felder lassen sich ergänzen, z. B. `IPTC:SpecialInstructions=altname`
- Vorhandene Bildunterschriften (Caption-Abstract, XMP-dc:Description, ImageDescription) bleiben unangetastet
- Nicht beschreibbare Dateien (AVI, BMP, defekte Dateien) bekommen ein Sidecar `<neuer Name>.<ext>.xmp`
- Dubletten bekommen dieselben Metadaten, damit ihre Herkunft erhalten bleibt
- Zusammengehörige Dateien (RAW + JPEG + TIFF + PSD + Sidecars + Live-Photo-MOV) bekommen denselben
  Basisnamen. Auch RAW/JPEG in verschiedenen Unterordnern desselben Ereignisses werden über Basisname
  und Aufnahmezeit zusammengeführt
- Die Endung gehört nicht zur Vorlage. Sie wird übernommen und kleingeschrieben

### Namensvorlage

Die Vorlage besteht aus Text und Platzhaltern, ähnlich wie in Lightroom oder Photo Mechanic. Ein eigener
kleiner Parser wertet sie aus, es wird kein Python-Code ausgeführt. Aus derselben Vorlage entsteht ein
**Erkennungsmuster**. Darauf beruhen Altbestand-Erkennung, Eindeutigkeit und Import.

```
{datum}_{zeit}_{kürzel}{lauf}_{nr:5}                 → 20190714_153012_AB20261015_00123
{datum:JJJJ-MM-TT}_{zeit:hhmmss}_{nr:4}              → 2019-07-14_153012_0123
{datum}_{ereignis|slug|klein|max:30}_{nr:4}          → 20190714_hochzeit-meier_0123
{datum}_{zeit}[_{kamera|slug|klein}]_{nr:3}          → 20190714_153012_eos-5d_012
{datum}_{zeit}_{nr:2}          (Zähler pro Sekunde)  → 20190714_153012_01
```

| Platzhalter | Inhalt |
|---|---|
| `{datum}`, `{datum:JJJJ-MM-TT}` | Aufnahmedatum, Vorgabe `JJJJMMTT` |
| `{zeit}`, `{zeit:hh-mm-ss}` | Aufnahmezeit, Vorgabe `hhmmss` |
| `{jahr}`, `{monat}`, `{tag}` | Teile des Aufnahmedatums |
| `{nr:N}` | Zähler mit N Stellen |
| `{kürzel}` | frei wählbares Kürzel |
| `{lauf}` | Datum des Laufs (`JJJJMMTT`) |
| `{ereignis}` | Name des Ereignis-/Unterordners |
| `{ordner}` | oberster Quellordner |
| `{kamera}`, `{serie}` | Kameramodell, Seriennummer |
| `{altname}` | alter Dateiname ohne Endung |
| `{altnr}` | Nummer aus dem alten Namen (`IMG_4711` → `4711`) |
| `{hash:N}` | die ersten N Zeichen des Bild-Hashs |

Für Datum und Zeit gibt es **eigene Formatcodes** (`JJJJ MM TT hh mm ss`) statt `strftime`, weil
`strftime` kein „Tag unbekannt“ kennt. Nur teilweise bekannte Daten werden mit `00` aufgefüllt
(`20190700`, `20190000`), ohne Uhrzeit wird `000000` eingesetzt.

| Filter | Wirkung |
|---|---|
| `slug` | Umlaute → ae/oe/ue/ss, Leerzeichen und Sonderzeichen → `-` |
| `klein`, `gross` | Groß-/Kleinschreibung |
| `max:N` | auf N Zeichen kürzen |
| `oder:Text` | Ersatz, wenn der Wert fehlt, z. B. `{kamera\|oder:unbekannt}` |

**Optionale Teile** in `[...]` fallen ganz weg, wenn ein Platzhalter darin leer ist.

**Zählerbereich**: gesamt, pro Jahr (Vorgabe), pro Monat, pro Tag, pro Sekunde oder pro Zielordner.
Die Zählerstände stehen in der Arbeitsdatenbank und werden beim Import fortgesetzt. Innerhalb eines
Bereichs wird nach Aufnahmezeit (mit SubSec) und dann nach altem Pfad sortiert.

**Prüfung der Vorlage** (live im Menü):

1. **Eindeutigkeit**: `{nr}` oder `{hash}` ist Pflicht, und der Zählerbereich muss durch Vorlage oder
   Zielordner abgedeckt sein (abgelehnt wird z. B. „pro Tag“ ohne `{datum}` in einem flachen
   Jahresordner). Vor dem Ausführen wird jeder Zielname gegen Datenbank und Dateisystem geprüft,
   ohne Rücksicht auf Groß-/Kleinschreibung
2. **Zeichen und Länge**: Zeichen, die exFAT oder Windows verbieten, werden entfernt
3. **Sortierung**: Hinweis, wenn der Name nicht mit dem Datum beginnt
4. **Rückübersetzung**: Die Vorlage wird in einen regulären Ausdruck mit benannten Gruppen übersetzt.
   Zwei freie Textfelder ohne Trenner werden abgelehnt

Im Menü zeigt eine Vorschau die Vorlage an Beispielen aus dem Bestand: RAW/JPEG-Paar, ohne Datum,
Umlaute im Ordner, gleiche Aufnahmesekunde, langer Ordnername.

Eine geänderte Vorlage gilt nur für neue Bilder. Die alte bleibt als „frühere Vorlage“ gespeichert,
damit der Altbestand weiter erkannt wird:

```toml
[namen]
vorlage = "{datum}_{zeit}_{kürzel}{lauf}_{nr:5}"
zaehler = "jahr"
kuerzel = "AB"
frueher = ["{datum}_{zeit}_ABp{lauf}_{nr:5}"]
```

### Altbestand erkennen

- Erkennung über die Muster der aktuellen und aller früheren Vorlagen
- Bei unverwechselbaren Mustern (Datum + Kürzel bzw. Laufdatum oder fester Buchstabentext + Nummer)
  genügt der Name. Bei einfachen Vorlagen muss zusätzlich `ImageUniqueID` dem Basisnamen entsprechen
- Altbestand wird **nicht neu benannt** und bleibt in seinem Jahresordner. Seine Nummern fließen in
  den Zählerstand ein, sofern ihr Format zur aktuellen Vorlage passt
- Der alte Dateiname wird aus `PreservedFileName` gelesen, sonst aus `EXIF:UserComment`
- Optional „Altbestand angleichen“: `UserComment` → `PreservedFileName` übertragen

### 5 Vorschau (Dry-Run)

- Plan als Tabelle mit Filter nach Aktion und Suche
- Zusammenfassung mit Zahlen pro Aktion, Verteilung auf die Zielordner
- Berichte als Semikolon-CSV (öffnen direkt in LibreOffice/Excel)

### 6 Ausführen

1. Prüfung „Plan veraltet?“ (Einstellungen geändert, Quellen fehlen, Ziele existieren schon) → Abbruch mit Liste
2. Bestätigung durch Eintippen des Sammlungsnamens
3. Verschieben/Umbenennen/Löschen bzw. Importieren
4. Bildunterschriften übernehmen, Metadaten schreiben und zurücklesen, Sidecars für nicht beschreibbare Dateien
5. Kopien der bleibenden Versionen nach `_Dubletten/`
6. Leer gewordene Ordner entfernen
7. Arbeitsdatenbank nachführen, Zähler und Einstellungen festschreiben
8. Hash-Stichprobe (Bilddaten unverändert?), Sicherung der Datenbank in die Sammlung, Abschlussbericht

### 7 Ordner importieren

- Die Quelle wird mit derselben Analyse gelesen. Zielstruktur, Vorlage und Metadatenfelder kommen aus
  den festgeschriebenen Einstellungen der Sammlung
- Abgleich gegen die Sammlung über die gespeicherten Hashes, ohne die Sammlung erneut zu lesen
- **Schon vorhanden**: wird nicht importiert, nur berichtet. Ein neuer RAW-Partner eines vorhandenen
  Bildes übernimmt dessen Namen
- **Bessere Version** (Caption oder höhere Auflösung): ersetzt die Datei der Sammlung unter deren
  Namen, die alte wandert nach `_Dubletten/`
- **Dubletten innerhalb der Quelle**: nur die bleibende Version wird importiert
- **Neu**: bekommt das Namensschema, landet im richtigen Jahresordner, der Zähler läuft weiter
- Pro Unterordner der Quelle lässt sich entscheiden: als Ereignis, auflösen oder nicht importieren

### 8 Rückgängig machen

Liste aller Journale. Verschiebungen werden zurückgenommen, Kopien entfernt, Zähler und Festschreibung
auf den Stand vor dem Lauf gesetzt. Gelöschter Müll und geschriebene Metadaten lassen sich nicht
zurücknehmen.

## Speicherorte

| Was | Wo |
|---|---|
| Arbeitsdatenbank, Berichte, Journale | `~/.local/share/bildarchiv/<id>/` (`BILDARCHIV_DATEN`) |
| Liste zuletzt geöffneter Sammlungen | `~/.config/bildarchiv/sammlungen.toml` (`BILDARCHIV_KONFIG`) |
| Einstellungen der Sammlung (nach dem ersten Ausführen) | `<Sammlung>/_bildarchiv/einstellungen.toml` |
| Sicherung der Datenbank, Berichte | `<Sammlung>/_bildarchiv/datenbank/`, `<Sammlung>/_bildarchiv/berichte/` |

Vor dem ersten Ausführen wird in der Sammlung nichts geschrieben. Wird eine Sammlung auf einem anderen
Rechner geöffnet, stellt das Werkzeug die Datenbank aus der Sicherung in der Sammlung her.

## Programmaufbau

```
bildarchiv/
├── __main__.py            Kommandozeile (ohne Argument: Menü)
├── ablauf.py              die Arbeitsschritte für Menü und Kommandozeile
├── jobs.py                Schritte als Hintergrundprozess, Fortschritt in der Datenbank
├── sammlung.py            Sammlung: Wurzel, Einstellungen, Datenbank, Speicherorte
├── einstellungen.py       Vorgaben, TOML lesen/schreiben, Prüfung
├── reporter.py            Fortschritt: Konsole, Datenbank, still
├── kern/
│   ├── inventar.py        Scan, Metadaten lesen (inkrementell)
│   ├── exif.py            Aufrufe von exiftool
│   ├── klassifikation.py  Bild/Sidecar/Video/Capture One/Müll/Sonstiges
│   ├── datierung.py       Datumsquellen, Ereignisnamen
│   ├── analyse.py         Befund, Mustererkennung, Vorschläge
│   ├── struktur.py        Jahres-, Monats-, Ereignisordner
│   ├── hashes.py          ImageDataHash, dHash, Pixelprüfung
│   ├── dubletten.py       Clusterbildung bitgleich/ähnlich
│   ├── vorlage.py         Namensvorlage: Parser, Filter, Erkennungsmuster, Prüfung
│   ├── planung.py         Plan für Ordnen und Import
│   ├── metadaten.py       Felder schreiben/prüfen, Sidecars, Captions
│   ├── ausfuehren.py      Ausführen, Journal, Rückgängig
│   ├── altdaten.py        Übernahme aus der Vorgängerversion
│   └── db.py              Schema der Arbeitsdatenbank
└── menue/                 Textual-Oberfläche
```

Der Kern gibt nichts selbst aus, sondern meldet Fortschritt über einen `Reporter`. Das Menü startet
lange Schritte als eigenen Prozess (`bildarchiv job …`) und liest Fortschritt und Meldungen aus den
Tabellen `jobs` und `job_log`. Eine Sperre verhindert zwei gleichzeitige Läufe auf derselben Sammlung.
