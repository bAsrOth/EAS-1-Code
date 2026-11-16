# System-Gesamtplan: LED-Kamera-Trigger

## Übersicht

Das System besteht aus drei Teilen:

1. **STM32-Firmware** — steuert 8 LED-Kanäle per PWM und gibt Kamera-Trigger aus
2. **Basler-Kamera** — nimmt Bilder per Hardware-Trigger auf
3. **PC-Steuerungsprogramm** — konfiguriert STM32 über USB-Serial, steuert Kamera,
   zeigt Livevorschau, ermöglicht ROI-Auswahl und verwaltet Bildaufnahme

---

## Teil 1 — STM32 Timing-Umstellung auf 238fps

### Ziel

Von ~4fps/Kanal (30ms Frame) auf ~238fps gesamt (~30fps/Kanal bei 8 Kanälen).

### TIM3-Änderung: 1ms → 100µs Tick

| Parameter | Vorher | Nachher |
|-----------|--------|---------|
| PSC | 95 | 9 |
| ARR | 999 | 959 |
| Tick-Periode | 1 ms | 100 µs |
| IRQ-Rate | 1 kHz | 10 kHz |

Berechnung: (PSC+1) × (ARR+1) / 96 MHz = 10 × 960 / 96.000.000 = **100 µs**

### Neue Frame-Struktur (42 Ticks = 4,2 ms ≈ 238 fps gesamt)

| sys_tick | Aktion |
|----------|--------|
| 0 | Kanal weiterschalten + LED einschalten |
| 1 | CAM_TRIG → HIGH |
| 2 | CAM_TRIG → LOW (100 µs Puls) |
| 20 | LED ausschalten (2 ms On-Zeit) |
| 41 | Wrap → 0 |

### Konfigurations-Defines in `main.c`

```c
#define FRAME_TICKS    42   // 42 × 100µs = 4,2ms pro Frame
#define LED_ON_TICK     0
#define TRIG_HIGH_TICK  1
#define TRIG_LOW_TICK   2
#define LED_OFF_TICK   20
```

### Debounce-Anpassung

Bei 10 kHz Tick: 20 ms Debounce = **200 Zähler** (war 20 bei 1 kHz).

### Dateien

- `Core/Src/timer.c` — PSC/ARR für TIM3
- `Core/Src/main.c` — Defines, Sequencer-Ticks, Debounce-Zähler, Wrap-Bedingung

### VSCode-Portierung (STM32 VS Code Extension)

Kein Code ändert sich — nur das Build-System wechselt von CubeIDE zu CMake.

**Schritte (manuell durch User):**
1. `stmicroelectronics.stm32-vscode-extension` im VSCode Marketplace installieren
2. STM32CubeCLT installieren (Pflicht-Abhängigkeit der Extension)
3. VSCode: `F1 → STM32: Import a local CubeIDE project` → Projektordner wählen
4. Extension generiert `CMakeLists.txt`, `.vscode/launch.json`, `.vscode/tasks.json`

**Nach dem Import prüfen:**
- `CMakeLists.txt` muss die manuellen Quellen enthalten:
  `Core/Src/timer.c`, `Core/Src/gpio.c`, `Core/Src/clock.c`
- `.gitignore` ergänzen: `build/`, `.vscode/`

---

## Teil 2 — USB Serial (STM32 → PC)

### Fähigkeit des STM32F412

Der STM32F412 besitzt **USB OTG Full Speed** auf den Pins:
- **PA11** — USB_OTG_FS_DM
- **PA12** — USB_OTG_FS_DP

USB CDC (Virtual COM Port) wird als ST-Middleware unterstützt. Am PC erscheint
das Board als normaler COM-Port, kein Treiber nötig (Windows 10/11, Linux, macOS).

### Aktueller Stand (bestätigt durch Projekt-Analyse)

- **PA11 und PA12 sind frei** — keine Kollision mit bestehenden Pins
- USB CDC ist noch nicht konfiguriert, kein `USB_DEVICE/`-Ordner vorhanden
- Die PLL ist bereits auf 192 MHz PLLQ gesetzt (`RCC.USBFreq_Value=192000000`),
  USB braucht 48 MHz (192 / 4) → **PLLQ=4 muss im .ioc gesetzt werden**, das ist
  der einzige zusätzliche RCC-Parameter

### CubeMX-Konfiguration (in CubeIDE oder CubeMX)

1. `RCC → PLLQ = 4` setzen (damit USB 48 MHz bekommt)
2. `Connectivity → USB_OTG_FS → Device_Only` aktivieren
3. `Middleware → USB_DEVICE → Class: CDC (Virtual Port Com)`
4. Generieren → erzeugt `USB_DEVICE/`-Ordner mit `usbd_cdc_if.c`

### Protokoll-Design (ASCII, zeilenbasiert)

Einfaches Text-Protokoll, ein Befehl pro Zeile (`\n`-terminiert):

```
SET_CH 3 ON          → Kanal 3 aktivieren
SET_CH 3 OFF         → Kanal 3 deaktivieren
SET_DUTY 3 0.20      → Duty Cycle Kanal 3 = 20%
SET_CHANNELS 8       → NUM_CHANNELS = 8
SET_FRAME_TICKS 42   → FRAME_TICKS = 42
GET_STATUS           → Antwortet mit aktuellem Zustand als JSON-Zeile
```

STM32 antwortet jeweils mit `OK\n` oder `ERR <grund>\n`.

### Firmware-Erweiterung

- `USB_DEVICE/App/usbd_cdc_if.c` → `CDC_Receive_FS()` Callback parst eingehende Befehle
- Kleine Parse-Funktion in `main.c` oder neue `usb_command.c`
- Laufzeit-Parameter (Duty, NUM_CHANNELS, FRAME_TICKS) als globale `volatile`-Variablen,
  die sowohl vom USB-Callback als auch vom TIM3-IRQ gelesen werden

---

## Teil 3 — Kamera: ROI und Livevorschau

### Basler a2A1920-160ucBAS — Technische Daten

| Eigenschaft | Wert |
|-------------|------|
| Sensor | 1920 × 1200 px |
| Max. Framerate | 160 fps (Vollbild) |
| Schnittstelle | USB3 Vision |
| Pixelformat | Mono8, BayerRG8, BGR8 u.a. |
| Trigger | Hardware (Line1, RisingEdge) ✓ |

### ROI über pypylon API

ROI ist vollständig über GenICam-Nodes einstellbar:

```python
nodemap = cam.GetNodeMap()
nodemap.GetNode("OffsetX").SetValue(roi_x)
nodemap.GetNode("OffsetY").SetValue(roi_y)
nodemap.GetNode("Width").SetValue(roi_w)
nodemap.GetNode("Height").SetValue(roi_h)
```

**Einschränkungen:** `Width` und `OffsetX` müssen auf ein Vielfaches von
`nodemap.GetNode("Width").GetInc()` (meist 4 px) ausgerichtet sein.

Kleinere ROI → höhere erreichbare Framerate (weniger Daten pro Frame).

### Livevorschau mit ROI-Auswahl

Im PC-Programm: Kamera kurz in Freerun-Modus schalten (kein Hardware-Trigger),
Frames kontinuierlich grabben, in einem Canvas anzeigen, User zieht Rechteck mit
der Maus → ROI übernehmen, dann zurück in Hardware-Trigger-Modus.

---

## Teil 4 — Schnelles Speichern

### Problem

| Format | Größe (1920×1200 Mono8) | Schreibzeit (SSD) |
|--------|--------------------------|-------------------|
| TIFF unkomprimiert | 2,2 MB | ~4 ms |
| PNG | 2,2 MB komprimiert | 20–100 ms |
| NumPy `.npy` | 2,2 MB | ~3 ms |
| Raw `.bin` | 2,2 MB | ~2 ms |

Bei 30 Bildern/s: 30 × 2,2 MB = **66 MB/s** — für eine SATA-SSD (500 MB/s) oder
NVMe (3+ GB/s) problemlos. Das eigentliche Bottleneck ist der Python-GIL beim
synchronen Schreiben im Aufnahme-Loop.

### Lösung: Asynchroner Queue-Writer

```
Kamera-Thread      →  Queue (RAM-Puffer)  →  Writer-Thread
RetrieveResult()       max. 200 Frames        cv2.imwrite / np.save
```

- Aufnahme-Thread legt Bild + Metadaten (Kanal, Index) in eine `queue.Queue`
- Writer-Thread läuft parallel, schreibt Bilder ohne den Grab-Loop zu blockieren
- Bei Vollbild (1920×1200 Mono8) passen ~200 Bilder in 440 MB RAM

### Empfohlenes Speicherformat

**NumPy `.npy`** für rohe Aufnahmen (kleinste CPU-Last, verlustfrei):
```python
np.save(str(OUTPUT_DIR / filename), img)  # ~2–3 ms
```

Nachgelagerte Konvertierung zu TIFF/PNG per Batch-Skript möglich.

Alternativ für direkte Weiterverarbeitung: **TIFF** bleibt sinnvoll, dann aber
zwingend mit dem Queue-Writer.

---

## Teil 5 — PC-Steuerungsprogramm

### Technologie-Stack

| Komponente | Bibliothek |
|------------|------------|
| GUI-Framework | **PySide6** (Qt6, offiziell, gut dokumentiert) |
| Serielle Kommunikation | `pyserial` |
| Kamera | `pypylon` |
| Bildanzeige | `cv2` → `QPixmap` konvertiert |
| Bildverarbeitung | `numpy` |

### Programmstruktur

```
pc_tool/
├── main.py               Einstiegspunkt, QApplication
├── main_window.py        Hauptfenster mit Tab-Widget
├── tabs/
│   ├── channel_tab.py    Kanal-Ein/Aus + Duty Cycle (8 Zeilen)
│   ├── timing_tab.py     FRAME_TICKS, LED_OFF_TICK einstellbar
│   ├── camera_tab.py     ROI-Auswahl, Livevorschau, Belichtungszeit
│   └── storage_tab.py    Ausgabepfad, Dateiname-Muster, Format
├── serial_controller.py  Verbindung + Befehlssender (pyserial)
├── camera_controller.py  pypylon-Wrapper (Grab, ROI, Preview)
└── image_writer.py       Async Queue-Writer (Thread)
```

### Ablauf ROI-Auswahl

1. User klickt „Livevorschau starten"
2. `camera_controller` schaltet auf Freerun (TriggerMode Off)
3. Frames werden in `camera_tab.py` als `QPixmap` angezeigt
4. User zieht Rechteck mit Maus auf das Vorschau-Bild
5. Rechteck wird auf Sensor-Koordinaten skaliert und auf Inkrement gerundet
6. User klickt „ROI übernehmen" → `camera_controller` setzt die 4 Nodes
7. Vorschau zeigt neuen ROI-Ausschnitt
8. User klickt „Aufnahme starten" → Kamera wechselt zurück zu Hardware-Trigger

---

## Verifikation

| Test | Erwartetes Ergebnis |
|------|---------------------|
| Logikanalysator Frame-Periode | ~4,2 ms |
| Trigger-Puls | ~100 µs HIGH |
| LED-On-Zeit | ~2 ms |
| Python fps-Ausgabe | ~238 fps |
| USB CDC COM-Port sichtbar | Ja (Geräte-Manager Windows) |
| ROI-Einstellung über GUI | Nodemap-Werte gesetzt, kleineres Bild |
| Queue-Writer | Keine Frame-Drops bei 30 fps, Bilder auf Disk |
