"""
tiff_komprimieren.py – komprimiert alle TIFFs eines Ordners verlustfrei
========================================================================
Aufruf:
  python tiff_komprimieren.py              → Ordner per Dialog auswaehlen
  python tiff_komprimieren.py <Ordner>     → Ordner direkt angeben

Die komprimierten TIFFs landen im Unterordner "komprimiert" mit gleichem
Dateinamen, die Originale bleiben unveraendert.
Deflate (zlib) ist verlustfrei: Bittiefe und alle Pixelwerte bleiben exakt erhalten.

Quellen:
  Pillow TIFF:    https://pillow.readthedocs.io/en/stable/handbook/image-file-formats.html#tiff
"""

import sys
import tkinter as tk
from tkinter import filedialog
from pathlib import Path

from PIL import Image


# Ordner bestimmen: Kommandozeile oder Auswahldialog
if len(sys.argv) > 1:
    ordner = Path(sys.argv[1])
else:
    root = tk.Tk()
    root.withdraw()   # nur den Dialog zeigen, kein leeres Hauptfenster
    auswahl = filedialog.askdirectory(title="Ordner mit TIFF-Bildern waehlen")
    if not auswahl:
        sys.exit("Kein Ordner gewaehlt.")
    ordner = Path(auswahl)

dateien = sorted(ordner.glob("*.tiff"))
if not dateien:
    sys.exit(f"Keine .tiff-Dateien in {ordner}")

ziel = ordner / "komprimiert"
ziel.mkdir(exist_ok=True)

groesse_vorher = groesse_nachher = 0
for i, datei in enumerate(dateien, start=1):
    neu = ziel / datei.name
    Image.open(datei).save(neu, compression="tiff_deflate")
    groesse_vorher  += datei.stat().st_size
    groesse_nachher += neu.stat().st_size
    print(f"{i}/{len(dateien)}  {datei.name}")

print(f"Fertig: {len(dateien)} TIFFs in {ziel}")
print(f"Groesse: {groesse_vorher / 1e6:.1f} MB -> {groesse_nachher / 1e6:.1f} MB "
      f"({100 * groesse_nachher / groesse_vorher:.0f} %)")
