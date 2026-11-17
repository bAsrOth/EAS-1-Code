"""
kamera_ctrl.py – Basler-Kamera Steuerung und Bildaufnahme
==========================================================
Kapselt die pypylon-Kamera-API.
Unterstuetzt: Einzelbild-Vorschau, ROI-Einstellung, Hardware-Trigger-Aufnahme
mit asynchronem Speichern per Queue + Worker-Thread.

Quellen:
  pypylon GitHub (Basler):       https://github.com/basler/pypylon
  pypylon Beispiele:             https://github.com/basler/pypylon/tree/master/samples
  Basler GenICam-Nodes:          https://docs.baslerweb.com/camera-features
  Pillow TIFF:                   https://pillow.readthedocs.io/en/stable/handbook/image-file-formats.html#tiff
"""

import threading
import queue
from pathlib import Path

import numpy as np
from PIL import Image
from pypylon import pylon, genicam


class KameraController:
    """Verwaltet die Kamera, den Grab-Thread und den Writer-Thread."""

    def __init__(self, log_fn, on_count_fn):
        """
        log_fn      : Callback fuer Meldungen im GUI-Log.  Signatur: log_fn(text)
        on_count_fn : Callback fuer den Bild-Zaehler waehrend Aufnahme.
                      Signatur: on_count_fn(count: int)
        """
        self.cam            = None    # pypylon InstantCamera
        self.sensor         = (0, 0)  # volle Sensorgroesse (Breite, Hoehe), gesetzt in open()
        self._log           = log_fn
        self._on_count      = on_count_fn

        self.acquisition_active = False

        self._img_queue  = queue.Queue(maxsize=200)
        self._image_index = 0
        self._led_ch      = 1
        self._num_ch      = 8    # wird von aussen gesetzt

    # ── Kamera oeffnen / schliessen ───────────────────────────────────────────

    def open(self) -> dict | None:
        """
        Oeffnet die erste gefundene Basler-Kamera und setzt den ROI auf den vollen Sensor.
        Gibt Modell, Sensorgroesse und moegliche Pixelformate zurueck (None bei Fehler).
        """
        try:
            # Öffnet erste gefundene Kamera
            self.cam = pylon.InstantCamera(pylon.TlFactory.GetInstance().CreateFirstDevice())
            self.cam.Open()
            # Konfiguriert den Trigger
            self._configure_trigger()

            # Node aus GENICAM-Standard: SensorWidth, SensorHeight, PixelFormat, speichert gelesene Daten in Info ab
            n = self.cam.GetNodeMap()
            info = {
                "model":     self.cam.GetDeviceInfo().GetModelName(),
                "width":     n.GetNode("SensorWidth").GetValue(),
                "height":    n.GetNode("SensorHeight").GetValue(),
                # Holt relevanten Monoformate der Kamera
                "formats":   [f for f in self.cam.PixelFormat.Symbolics
                              if f in ("Mono8", "Mono10", "Mono12")],
            }
            # Die Kamera behaelt einen alten ROI bis zum Neustart → auf vollen Sensor zuruecksetzen
            self.sensor = (info["width"], info["height"])
            # ROI auf vollen Sensor setzen
            self.set_roi(0, 0, *self.sensor)
            # Übergeben von der Info-Dct an GUI
            return info
        # Kamera Fehler abfnagnen
        except Exception as e:
            self.cam = None
            self._log(f"# Kamera-Fehler: {e}")
            return None

    # Kamera schliessen, Aufnahme Stoppen
    def close(self):
        self.stop_acquisition()
        if self.cam:
            try:
                self.cam.Close()
            except Exception:
                pass
        self.cam = None

    # Funktion die Kamera-Status abfragt
    @property
    def is_open(self) -> bool:
        return self.cam is not None


    # Trigger-Konfiguration
    def _configure_trigger(self):
        """
        Hardware-Trigger: Jedes Bild startet erst mit der steigenden Flanke
        des STM32-Signals an Line1. Ausnahme: grab_single_frame() fuer die Vorschau.
        """
        n = self.cam.GetNodeMap()
        n.GetNode("TriggerSelector").SetValue("FrameStart")
        n.GetNode("TriggerSource").SetValue("Line1")
        n.GetNode("TriggerActivation").SetValue("RisingEdge")
        n.GetNode("TriggerMode").SetValue("On")

    # ROI der Kamera setzen
    # Bekommt ein Zentrum überreicht und Länge und Breite
    def set_roi(self, x: int, y: int, w: int, h: int) -> tuple | None:
        """
        Setzt den ROI (Region of Interest) auf dem Sensor. Kleinerer ROI = hoehere Framerate.
        Die Kamera darf dabei NICHT grabben (Width/Height sind sonst gesperrt).
        Gibt die tatsaechlich gesetzten (gerundeten) Werte zurueck, None bei Fehler.
        """
        n = self.cam.GetNodeMap()

        def runden(wert, node):
            # Kamera arbeitet in entweder 2px, 4px Schritten
            # Daher wird das Inkrment einmal abgefragt, dann der Wert auf das naechste Vielfache gerundet
            inc = n.GetNode(node).GetInc()
            # / / Ganzzahlig teilen
            return (wert // inc) * inc

        x, y = runden(x, "OffsetX"), runden(y, "OffsetY")
        w, h = runden(w, "Width"),   runden(h, "Height")
        try:
            # Erst Offset auf 0, damit kein alter Offset stört
            n.GetNode("OffsetX").SetValue(0)
            n.GetNode("OffsetY").SetValue(0)
            n.GetNode("Width").SetValue(w)
            n.GetNode("Height").SetValue(h)
            n.GetNode("OffsetX").SetValue(x)
            n.GetNode("OffsetY").SetValue(y)
            return x, y, w, h
        except genicam.GenericException as e:
            self._log(f"# ROI-Fehler: {e}")
            return None

    def roi_check(self, x: int, y: int, w: int, h: int) -> tuple:
        """
        Setzt den ROI kurz auf die Kamera, um die gerundeten Werte und die damit
        möglichen FPS zu erfahren.
        Danach wieder Vollbild, damit die Vorschau wieder den ganzen Sensor zeigt.
        """
        roi = self.set_roi(x, y, w, h)
        fps = self.get_resulting_fps() if roi else None
        self.set_roi(0, 0, *self.sensor)
        return roi, fps

    # Abfrage nach den möglichen FPS mit den folgenden Einstellungen
    def get_resulting_fps(self) -> float | None:
        if not self.cam:
            return None
        return self.cam.GetNodeMap().GetNode("ResultingFrameRate").GetValue()

    # Pixelformat setzen
    def set_pixel_format(self, fmt: str) -> bool:
        """
        Setzt die Bittiefe, z.B. 'Mono8' oder 'Mono12'
        """
        try:
            self.cam.GetNodeMap().GetNode("PixelFormat").SetValue(fmt)
            return True
        except genicam.GenericException as e:
            self._log(f"# Pixelformat-Fehler: {e}")
            return False
    # Setzt Belichtungszeit in Mikrosekunden
    def set_exposure(self, microseconds: int):
        """Setzt die Belichtungszeit in Mikrosekunden"""
        if not self.cam:
            return
        try:
            n = self.cam.GetNodeMap()
            # Schaltet Autoexposure aus
            n.GetNode("ExposureAuto").SetValue("Off")
            n.GetNode("ExposureTime").SetValue(float(microseconds))
        except genicam.GenericException as e:
            self._log(f"# Belichtungsfehler: {e}")

    # Holt einen einzelnen Frame zur Vorschau
    def grab_single_frame(self) -> np.ndarray | None:
        """
        Nimmt sofort ein einzelnes Vollbild auf, ohne auf den STM32 zu warten.
        Fuer Vorschau und ROI-Auswahl.
        """
        n = self.cam.GetNodeMap()
        # Kamera löst für ein Bild selbst aus
        n.GetNode("TriggerMode").SetValue("Off")
        try:
            result = self.cam.GrabOne(2000)        
        except genicam.GenericException as e:   # Falls USB Verbindung versagt
            self._log(f"# Grab-Fehler: {e}")
            return None
        finally:
            # danach wieder Hardware-Trigger
            n.GetNode("TriggerMode").SetValue("On")   
        img = result.GetArray().copy() if result.GrabSucceeded() else None
        result.Release()
        return img

    # ── Aufnahme mit Hardware-Trigger ─────────────────────────────────────────

    def start_acquisition(self, output_folder: Path, prefix: str,
                          num_channels: int, roi: list, max_images: int):
        """
        Startet die Aufnahme mit Hardware-Trigger.
            output_folder: Ordner fuer die gespeicherten Bilder (.tiff)
            prefix       : Dateiname-Praefix, z.B. 'IMG'
            num_channels : Anzahl der aktiven LED-Kanaele
            roi          : [x, y, w, h] – ROI der Aufnahme
            max_images   : nach so vielen Bildern Stoppt die Aufnahme (0 = unbegrenzt)
        """
        # Sollte die Kamera nicht offen sein oder bereits eine Aufnahme laufen stoppt die Funktion
        if not self.cam or self.acquisition_active:
            return

        # ROI wird gesetzt und wenn nichts zurück kommt, dann stoppt Funktion
        if not self.set_roi(*roi):
            return
        #Setzt den Ordner
        output_folder.mkdir(parents=True, exist_ok=True)

        # Setzt Variablen intial zum Starten
        self._image_index     = 0
        self._led_ch          = 1
        self._num_ch          = num_channels
        self._max_images      = max_images
        self.acquisition_active = True

        # Queue leeren
        while not self._img_queue.empty():
            self._img_queue.get_nowait()

        # Grobe Struktur:
        # 2 Threads die parallel laufen
        # writer Thread wartet auf Bilder aus dem Grab-Thread und schreibt sie auf als .tiff weg.
        # Grad Thread wartet auf Bild aus der Kamera, welches nach dem Hardware Trigger kommt und legt es in die Queue

        # Writer-Thread starten
        threading.Thread(target=self._writer_loop,
                         args=(output_folder, prefix), daemon=True).start()

        # Kamera ist seit open() auf Hardware-Trigger → Grabben starten, Bilder kommen mit jedem Trigger
        self.cam.StartGrabbing(pylon.GrabStrategy_OneByOne)

        # Grab-Thread starten
        threading.Thread(target=self._grab_loop, daemon=True).start()

    
    def stop_acquisition(self):
        """Stoppt die Aufnahme, Kamera-Grabben und Threads."""
        if not self.acquisition_active:
            return
        self.acquisition_active = False
        if self.cam and self.cam.IsGrabbing():
            self.cam.StopGrabbing()

        # Setzt ROI wieder auf Vollbild
        self.set_roi(0, 0, *self.sensor)   

    def _grab_loop(self):
        """
        Hintergrund-Thread: wartet auf Hardware-Trigger, holt Bild,
        legt es mit dem aktuellen Kanal in die Queue.
        """
        grabbed = 0   # Anzahl Bilder, die in die Queue gelegt wurden

        # Wenn Aufnahme läuft und Kamera bereits am arbeiten ist, dann Bilder holen
        while self.acquisition_active and self.cam.IsGrabbing():
            try:
                result = self.cam.RetrieveResult(100, pylon.TimeoutHandling_ThrowException)
            except genicam.TimeoutException:
                continue   # kein Trigger → weiter warten

            with result:
                if not result.GrabSucceeded():
                    self._log(f"# Grab-Fehler: {result.ErrorDescription}")
                    continue

                # Rohdaten ohne Umwandlung: Mono8 → uint8, Mono10/12 → uint16
                img = result.GetArray().copy()
                ch  = self._led_ch

                # Kanal weiterschalten 
                # Wenn die maximale Kanalzahl erreicht ist, wird wieder bei 1 begonnen
                self._led_ch += 1
                if self._led_ch > self._num_ch:
                    self._led_ch = 1

                try:
                    self._img_queue.put_nowait((img, ch))
                    grabbed += 1
                except queue.Full:
                    self._log("# Warnung: Queue voll – Bild uebersprungen!")

            # Stoppt wenn maximale Anzahl der Bilder erreiuht ist und Anzahl der Bilder != 0 ist (Dann unbegrenzt)
            if self._max_images and grabbed >= self._max_images:
                break

    def _writer_loop(self, output_folder: Path, prefix: str):
        """
        Hintergrund-Thread: nimmt Bilder aus Queue und schreibt sie als .tiff weg.
        Unkomprimiert, ansonsten zu langsam
        Mono8 → 8-bit-TIFF, Mono10/12 → 16-bit-TIFF
        """
        while self.acquisition_active or not self._img_queue.empty():
            try:
                img, ch = self._img_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            self._image_index += 1
            name = f"{prefix}_{self._image_index:04d}_CH{ch}"

            # Alternative: rohes NumPy-Array (.npy), minimal schneller, aber kein Bildformat
            # np.save(output_folder / (name + ".npy"), img)

            Image.fromarray(img).save(output_folder / (name + ".tiff"))

            self._on_count(self._image_index)
