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
  OpenCV imwrite:                https://docs.opencv.org/4.x/d4/da8/group__imgcodecs.html
  NumPy save:                    https://numpy.org/doc/stable/reference/generated/numpy.save.html
"""

import threading
import queue
from pathlib import Path

import numpy as np
import cv2
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
        Gibt Modell, Sensorgroesse und Pixelformat zurueck (None bei Fehler).
        """
        try:
            self.cam = pylon.InstantCamera(pylon.TlFactory.GetInstance().CreateFirstDevice())
            self.cam.Open()
            self._configure_trigger(hardware=False)

            # SensorWidth/Height = volle Sensorgroesse, unabhaengig vom zuletzt gesetzten ROI
            n = self.cam.GetNodeMap()
            info = {
                "model":     self.cam.GetDeviceInfo().GetModelName(),
                "width":     n.GetNode("SensorWidth").GetValue(),
                "height":    n.GetNode("SensorHeight").GetValue(),
                "px_format": n.GetNode("PixelFormat").GetCurrentEntry().GetSymbolic(),
            }
            # Die Kamera behaelt einen alten ROI bis zum Neustart → auf vollen Sensor zuruecksetzen
            self.sensor = (info["width"], info["height"])
            self.set_roi(0, 0, *self.sensor)
            return info
        except Exception as e:
            self.cam = None
            self._log(f"# Kamera-Fehler: {e}")
            return None

    def close(self):
        """Stoppt eine laufende Aufnahme und schliesst die Kamera."""
        self.stop_acquisition()
        if self.cam:
            try:
                self.cam.Close()
            except Exception:
                pass
        self.cam = None

    @property
    def is_open(self) -> bool:
        return self.cam is not None

    # ── Kamera-Konfiguration ──────────────────────────────────────────────────

    def _configure_trigger(self, hardware: bool):
        """
        hardware=False → Freerun: Kamera nimmt selbst Bilder auf (fuer Vorschau)
        hardware=True  → Hardware-Trigger: wartet auf STM32-Signal an Line1
        """
        n = self.cam.GetNodeMap()
        n.GetNode("TriggerSelector").SetValue("FrameStart")
        if hardware:
            n.GetNode("TriggerSource").SetValue("Line1")
            n.GetNode("TriggerActivation").SetValue("RisingEdge")
            n.GetNode("TriggerMode").SetValue("On")
        else:
            n.GetNode("TriggerMode").SetValue("Off")

    def set_roi(self, x: int, y: int, w: int, h: int) -> tuple | None:
        """
        Setzt den ROI (Region of Interest) auf dem Sensor. Kleinerer ROI = hoehere Framerate.
        Die Kamera darf dabei NICHT grabben (Width/Height sind sonst gesperrt).
        Gibt die tatsaechlich gesetzten (gerundeten) Werte zurueck, None bei Fehler.
        """
        n = self.cam.GetNodeMap()

        def runden(wert, node):
            # Die Kamera akzeptiert nur Vielfache ihres Inkrements (meist 4 px)
            inc = n.GetNode(node).GetInc()
            return (wert // inc) * inc

        x, y = runden(x, "OffsetX"), runden(y, "OffsetY")
        w, h = runden(w, "Width"),   runden(h, "Height")
        try:
            # Erst Offset auf 0, sonst kann "alter Offset + neue Breite" kurz groesser als der Sensor sein
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
        erreichbare FPS zu erfahren. Danach wieder Vollbild, damit die Vorschau
        weiterhin den ganzen Sensor zeigt. Gibt (roi, fps) zurueck, (None, None) bei Fehler.
        """
        roi = self.set_roi(x, y, w, h)
        fps = self.get_resulting_fps() if roi else None
        self.set_roi(0, 0, *self.sensor)
        return roi, fps

    def get_resulting_fps(self) -> float | None:
        """
        Max. erreichbare Framerate. Die Kamera rechnet sie selbst aus ROI + Belichtung + USB.
        None wenn keine Kamera offen ist.
        """
        if not self.cam:
            return None
        return self.cam.GetNodeMap().GetNode("ResultingFrameRate").GetValue()

    def set_exposure(self, microseconds: int):
        """Setzt die Belichtungszeit in Mikrosekunden. Deaktiviert Auto-Belichtung."""
        if not self.cam:
            return
        try:
            n = self.cam.GetNodeMap()
            n.GetNode("ExposureAuto").SetValue("Off")
            n.GetNode("ExposureTime").SetValue(float(microseconds))
        except genicam.GenericException as e:
            self._log(f"# Belichtungsfehler: {e}")

    def grab_single_frame(self) -> np.ndarray | None:
        """Nimmt ein einzelnes Vollbild auf (Freerun). Fuer Vorschau und ROI-Auswahl."""
        try:
            result = self.cam.GrabOne(2000)   # 2000 ms Timeout
        except genicam.GenericException as e:
            self._log(f"# Grab-Fehler: {e}")
            return None
        img = result.GetArray().copy() if result.GrabSucceeded() else None
        result.Release()
        return img

    # ── Aufnahme mit Hardware-Trigger ─────────────────────────────────────────

    def start_acquisition(self, output_dir: Path, file_format: str,
                          prefix: str, num_channels: int, roi: list, max_images: int):
        """
        Startet die Aufnahme mit Hardware-Trigger.
          output_dir   : Verzeichnis fuer die gespeicherten Bilder
          file_format  : 'tiff' oder 'npy'
          prefix       : Dateiname-Praefix, z.B. 'IMG'
          num_channels : Anzahl LED-Kanaele (fuer den Kanal-Zaehler)
          roi          : [x, y, w, h] – erst hier wird der ROI wirklich auf die Kamera gesetzt
          max_images   : nach so vielen Bildern hoert der Grab-Thread auf (0 = unbegrenzt)
        """
        if not self.cam or self.acquisition_active:
            return

        if not self.set_roi(*roi):
            return
        output_dir.mkdir(parents=True, exist_ok=True)

        self._image_index     = 0
        self._led_ch          = 1
        self._num_ch          = num_channels
        self._max_images      = max_images
        self.acquisition_active = True

        # Queue leeren
        while not self._img_queue.empty():
            self._img_queue.get_nowait()

        # Writer-Thread starten
        threading.Thread(target=self._writer_loop,
                         args=(output_dir, file_format, prefix), daemon=True).start()

        # Kamera auf Hardware-Trigger umschalten
        self._configure_trigger(hardware=True)
        self.cam.StartGrabbing(pylon.GrabStrategy_OneByOne)

        # Grab-Thread starten
        threading.Thread(target=self._grab_loop, daemon=True).start()

    def stop_acquisition(self):
        """Stoppt die Aufnahme. Writer-Thread laeuft noch bis Queue leer ist."""
        if not self.acquisition_active:
            return
        self.acquisition_active = False
        if self.cam and self.cam.IsGrabbing():
            self.cam.StopGrabbing()
        self.set_roi(0, 0, *self.sensor)          # zurueck auf Vollbild fuer die Vorschau
        self._configure_trigger(hardware=False)   # Vorschau braucht Freerun, nicht Line1

    def _grab_loop(self):
        """
        Hintergrund-Thread: wartet auf Hardware-Trigger, holt Bild,
        legt es mit dem aktuellen Kanal in die Queue.
        """
        converter = pylon.ImageFormatConverter()
        converter.OutputPixelFormat = pylon.PixelType_Mono8   # 1 Byte pro Pixel (Graubild)
        grabbed = 0   # Anzahl Bilder, die in die Queue gelegt wurden

        while self.acquisition_active and self.cam.IsGrabbing():
            try:
                result = self.cam.RetrieveResult(100, pylon.TimeoutHandling_ThrowException)
            except genicam.TimeoutException:
                continue   # kein Trigger → weiter warten

            with result:
                if not result.GrabSucceeded():
                    self._log(f"# Grab-Fehler: {result.ErrorDescription}")
                    continue

                img = converter.Convert(result).GetArray().copy()
                ch  = self._led_ch

                # Kanal weiterschalten (synchron mit STM32)
                self._led_ch += 1
                if self._led_ch > self._num_ch:
                    self._led_ch = 1

                try:
                    self._img_queue.put_nowait((img, ch))
                    grabbed += 1
                except queue.Full:
                    self._log("# Warnung: Queue voll – Bild uebersprungen!")

            # Gewuenschte Anzahl erreicht → keine weiteren Bilder holen
            # (Writer speichert die Queue noch fertig, das GUI stoppt danach die Aufnahme)
            if self._max_images and grabbed >= self._max_images:
                break

    def _writer_loop(self, output_dir: Path, file_format: str, prefix: str):
        """
        Hintergrund-Thread: nimmt Bilder aus Queue und schreibt sie auf Disk.
        TIFF ohne Komprimierung oder NumPy .npy fuer maximale Schreibgeschwindigkeit.
        """
        while self.acquisition_active or not self._img_queue.empty():
            try:
                img, ch = self._img_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            self._image_index += 1
            name = f"{prefix}_{self._image_index:04d}_CH{ch}"

            if file_format == "npy":
                # NumPy raw: kein Overhead, schnellstes Format
                np.save(str(output_dir / (name + ".npy")), img)
            else:
                # TIFF, Compression=1 = keine Komprimierung (LZW waere 5, JPEG 6)
                # Quelle: https://docs.opencv.org/4.x/d4/da8/group__imgcodecs.html
                cv2.imwrite(str(output_dir / (name + ".tiff")), img,
                            [cv2.IMWRITE_TIFF_COMPRESSION, 1])

            self._on_count(self._image_index)
