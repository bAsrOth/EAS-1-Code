"""
Python-Programm zurBilderaufnahme
Stoppen: STRG+C

Notwendige Packages installieren: pip install pypylon numpy opencv-python pathlib
WICHTIG: Basler-Pylon Software muss vorher installiert werden für pypylon
"""

from pypylon import pylon, genicam
from pathlib import Path
import cv2
import time

NUM_CHANNELS = 8                # Anzahl LED-Kanäle
OUTPUT_DIR   = Path("Bilder")   # Ausgabeverzeichnis für die Bilder

# ─ Kamera öffnen 

factory = pylon.TlFactory.GetInstance()
cam = pylon.InstantCamera(factory.CreateFirstDevice())
cam.Open()

# Hardware-Trigger konfigurieren 
# Kamera-Konfigurationsobjekt erzeugen zum konfigurieren der internen Parameter
nodemap = cam.GetNodeMap()
# Frame starten wenn Trigger empfangen wird
nodemap.GetNode("TriggerSelector").SetValue("FrameStart")
# Triggerquelle: Optokoppler Line 1 
nodemap.GetNode("TriggerSource").SetValue("Line1")
# Triggeraktivierung: steigende Flanke
nodemap.GetNode("TriggerActivation").SetValue("RisingEdge")
# Triggermodus anschalten
nodemap.GetNode("TriggerMode").SetValue("On")

# Erstellen des Ausgabeverzeichnisses, falls es nicht existiert
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Wandelt Rohbildaten in Daten um die verarbeitbar sind
converter = pylon.ImageFormatConverter()
# Legt 8-bit BGR als Farbformat fest (3 Byte pro Pixel, in der Reihenfolge Blau, Grün, Rot)
converter.OutputPixelFormat = pylon.PixelType_BGR8packed

# Konfiguration der Kamera: Interner Puffer ist als FIFO konfiguriert
# Kamera sendet Bild wenn Python-Programm bereit ist, das Bild zu empfangen
cam.StartGrabbing(pylon.GrabStrategy_OneByOne)

print(f"Aufnahme läuft -> '{OUTPUT_DIR}/'   Stoppen mit STRG+C\n")

# Index Variablen
img_idx      = 0
led_ch       = 1
t_last   = time.perf_counter()

try:
    while cam.IsGrabbing():
        try:
            # Warten auf ein Bild der Kamera
            result = cam.RetrieveResult(500, pylon.TimeoutHandling_ThrowException)
        except genicam.TimeoutException:
            print("Kein Trigger empfangen – warte...")
            continue

        with result:
            # Fehler abfangen, falls das Bild nicht empfangen werden konnte
            if not result.GrabSucceeded():
                print(f"[Fehler] Grab fehlgeschlagen: {result.ErrorDescription}")
                continue
            
            # Array aus dem empfangenen Bild erstellen
            img = converter.Convert(result).GetArray()
            img_idx += 1
            #Erstellung des Dateinamens für das Bild: IMG_0001_CH1.tiff, IMG_0002_CH2.tiff, ...
            filename = f"IMG_{img_idx:04d}_CH{led_ch}.tiff"
            # Schreiben des Bildes im Ausgabeverzeichnis als TIFF
            # Mit Komprierungsflag = 1, was für keine Komprimierung steht
            cv2.imwrite(str(OUTPUT_DIR / filename), img, [cv2.IMWRITE_TIFF_COMPRESSION, 1])

            # Berechnung der FPS durch die Zeit seit dem letzten Bild
            t_now = time.perf_counter()
            fps   = 1.0 / (t_now - t_last)
            t_last = t_now
            # Ausgabe der FPS und des Dateinamens
            print(f"{filename}  {fps:.1f} fps")

            # Inkrementation des LED-Kanals bis Kanal 8
            led_ch += 1
            if led_ch > NUM_CHANNELS:
                led_ch = 1

# Wenn STRG+C gedrückt wird, wird die Schleife beendet
except KeyboardInterrupt:
    pass

# Schließen der Kamera und Beenden des Programms
cam.StopGrabbing()
cam.Close()
print(f"\nFertig – {img_idx} Bilder gespeichert.")
