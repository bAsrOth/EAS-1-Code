"""
steuerung.py – PC-Steuerungsprogramm fuer LED-Kamera-Trigger
=============================================================
Layout: 3 Spalten (Serial+Kanaele | Timing+Aufnahme | Kamera+Vorschau)
        + Befehlslog unten

Starten:  python steuerung.py
Pakete:   pip install -r requirements.txt

Quellen:
  tkinter Doku:        https://docs.python.org/3/library/tkinter.html
  tkinter.ttk:         https://docs.python.org/3/library/tkinter.ttk.html
  ScrolledText:        https://docs.python.org/3/library/tkinter.scrolledtext.html
  Pillow ImageTk:      https://pillow.readthedocs.io/en/stable/reference/ImageTk.html
  OpenCV selectROI:    https://docs.opencv.org/4.x/
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageTk

from serial_ctrl import SerialController
from kamera_ctrl import KameraController


# ── Konfiguration ─────────────────────────────────────────────────────────────

NUM_CHANNELS = 8

# Maximalstrom pro Kanal in Ampere (= 100% Duty Cycle)
CH_MAX_CURRENT = [1.5, 1.5, 1.5, 1.0, 1.0, 1.5, 1.5, 1.5]

PREVIEW_W = 580   # Breite des Vorschau-Canvas in Pixeln
PREVIEW_H = 380   # Hoehe des Vorschau-Canvas in Pixeln


# ── Hauptfenster ──────────────────────────────────────────────────────────────

class App:

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("LED-Kamera-Trigger Steuerung")

        self.roi       = [0, 0, 0, 0]   # zuerst leerer ROI (x, y, w, h)
        self._last_img = None           # zuerst leeres Bild
        self._tk_img   = None           # Bild-Referenz fuer Vorschau (verhindert GC)

        # Controller (Logik in eigenen Dateien)
        self.serial = SerialController(log_fn=self._log)
        self.kamera = KameraController(log_fn=self._log, on_count_fn=self._on_count)

        # Haupt-Layout: 3 Spalten nebeneinander
        top = ttk.Frame(root, padding=6)
        top.pack(fill="both", expand=True)

        left   = ttk.Frame(top)
        middle = ttk.Frame(top)
        right  = ttk.Frame(top)
        left  .pack(side="left", fill="y",    padx=(0, 6))
        middle.pack(side="left", fill="y",    padx=(0, 6))
        right .pack(side="left", fill="both", expand=True)

        # Fügt die einzelnen Panels in die Spalten ein
        self._serial_panel(left)
        self._channel_panel(left)
        self._timing_panel(middle)
        self._storage_panel(middle)
        self._camera_panel(right)
        self._log_panel(root)

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)


    # Serielle Verbindung zum STM32-Board
    def _serial_panel(self, parent):
        f = ttk.LabelFrame(parent, text="STM32 Verbindung", padding=6)
        f.pack(fill="x", pady=(0, 6))

        # Port-Auswahl
        row1 = ttk.Frame(f)
        row1.pack(fill="x")
        ttk.Label(row1, text="Port:").pack(side="left")
        self.port_var   = tk.StringVar()
        # Nicht readonly: Ports, die Windows nicht auflistet (z.B. com0com), koennen eingetippt werden
        self.port_combo = ttk.Combobox(row1, textvariable=self.port_var, width=13)
        self.port_combo.pack(side="left", padx=4)
        ttk.Button(row1, text="↻",           command=self._ports_refresh).pack(side="left")
        self.conn_btn = ttk.Button(row1, text="Verbinden", command=self._serial_toggle)
        self.conn_btn.pack(side="left", padx=4)

        self.conn_lbl = ttk.Label(f, text="● Getrennt", foreground="red")
        self.conn_lbl.pack(anchor="w", pady=(4, 0))

        self._ports_refresh()

    # Refresh Buttom für COM-Ports
    def _ports_refresh(self):
        ports = self.serial.get_ports()
        self.port_combo["values"] = ports

        # Wählt automatisch den ersten Port aus, falls verfügbar
        if ports:
            self.port_combo.current(0)

    # Verbinden/Trennen der seriellen Verbindung
    def _serial_toggle(self):
        if self.serial.connected:
            self.serial.disconnect()
            self.conn_lbl.config(text="● Getrennt", foreground="red")
            self.conn_btn.config(text="Verbinden")
        else:
            if self.serial.connect(self.port_var.get()):
                self.conn_lbl.config(text="● Verbunden", foreground="green")
                self.conn_btn.config(text="Trennen")
            else:
                self.conn_lbl.config(text="● Fehler", foreground="orange")

    # Einstellungen der Channel
    def _channel_panel(self, parent):
        f = ttk.LabelFrame(parent, text="LED-Kanaele", padding=6)
        f.pack(fill="x", pady=(0, 6))

        # Kopfzeile
        ttk.Label(f, text="Kanal", width=7).grid(row=0, column=0)
        ttk.Label(f, text="Strom", width=6).grid(row=0, column=1)
        ttk.Label(f, text="Max",   width=8).grid(row=0, column=2)
        ttk.Label(f, text="Duty",  width=8).grid(row=0, column=3)

        # Pro Kanal je eine Zeile
        self.ch_active   = []   # BooleanVar: Kanal an/aus
        self.ch_current  = []   # StringVar:  Strom in Ampere
        self.ch_duty_lbl = []   # Label:      berechneter Duty zur Anzeige

        # For Schleife zum erstellen der Kanalzeilen
        for i in range(NUM_CHANNELS):
            active  = tk.BooleanVar(value=True)
            current = tk.StringVar(value="0.30")
            self.ch_active .append(active)
            self.ch_current.append(current)

            row = i + 1

            # Buttom um Channel Aktiv zu setzen
            ttk.Checkbutton(f, text=f"CH{i+1}", variable=active,
                            command=self._timing_update   # nur Anzeige, gesendet wird mit "Alle senden"
                            ).grid(row=row, column=0, sticky="w")

            # Eingabefeld für den Stromwert
            ttk.Entry(f, textvariable=current, width=6,
                      justify="right").grid(row=row, column=1, padx=2)
            
            # Label für den Maximalstrom
            ttk.Label(f, text=f"/ {CH_MAX_CURRENT[i]:.1f} A",
                      foreground="gray").grid(row=row, column=2, sticky="w")
            
            # Label für den Duty Cycle
            lbl = ttk.Label(f, text="= --", width=8, foreground="#666")
            lbl.grid(row=row, column=3)
            self.ch_duty_lbl.append(lbl)
        # Button um alle Kanalwerte zu senden
        ttk.Button(f, text="Alle senden", command=self._channels_send_all).grid(
            row=NUM_CHANNELS + 1, column=0, columnspan=4, pady=(6, 0), sticky="e")

    # Hier werden die Kanalwerte an das STM32-Board gesendet
    def _channel_send(self, ch: int):
        try:
            # Konversion von String zu Float, Komma wird zu Punkt gemacht
            current = float(self.ch_current[ch].get().replace(",", "."))
            # Berechnung des Duty Cycles
            duty_cycle = min(max(current / CH_MAX_CURRENT[ch], 0.0), 1.0)
        except ValueError:
            # Abfangen von ungültigen Eingaben
            self._log(f"# CH{ch+1}: Ungueltige Eingabe")
            return


        state = "ON" if self.ch_active[ch].get() else "OFF"
        self.ch_duty_lbl[ch].config(text=f"= {duty_cycle:.3f}")
        self.serial.send(f"SET_CH {ch+1} {state}")
        self.serial.send(f"SET_DUTY {ch+1} {duty_cycle:.4f}")

    # Senden aller Kanalwerte
    def _channels_send_all(self):
        for i in range(NUM_CHANNELS):
            self._channel_send(i)


    # ══════════════════════════════════════════════════════════════════════════
    # Mittlere Spalte
    # ══════════════════════════════════════════════════════════════════════════

    def _timing_panel(self, parent):
        f = ttk.LabelFrame(parent, text="Timing", padding=6)
        f.pack(fill="x", pady=(0, 6))

        # Kanal-FPS Eingabe
        row1 = ttk.Frame(f)
        row1.pack(fill="x", pady=2)
        ttk.Label(row1, text="Kanal FPS:", width=14).pack(side="left")
        self.channel_fps = tk.DoubleVar(value=30.0)
        ttk.Spinbox(row1, from_=1, to=160, increment=5,
                    textvariable=self.channel_fps, width=6).pack(side="left", padx=4)
        ttk.Label(row1, text="fps / Kanal").pack(side="left")
        self.channel_fps.trace_add("write", self._timing_update)

        # LED Onzeit Eingabe
        row2 = ttk.Frame(f)
        row2.pack(fill="x", pady=2)
        ttk.Label(row2, text="LED On-Zeit:", width=14).pack(side="left")
        self.led_on_ms = tk.DoubleVar(value=2.0)
        ttk.Spinbox(row2, from_=0.1, to=30.0, increment=0.1,
                    textvariable=self.led_on_ms, width=6).pack(side="left", padx=4)
        ttk.Label(row2, text="ms").pack(side="left")
        self.led_on_ms.trace_add("write", self._timing_update)

        ttk.Separator(f, orient="horizontal").pack(fill="x", pady=4)

        # Berechnete Werte (nur Anzeige): links Beschreibung, rechts Wert
        info = ttk.Frame(f)
        info.pack(fill="x")
        self.timing_vals = {}
        for row, name in enumerate(["Aktive Kanäle", "Gesamt-FPS", "Frame-Dauer", "LED an", "LED aus"]):
            ttk.Label(info, text=name + ":").grid(row=row, column=0, sticky="w")
            self.timing_vals[name] = ttk.Label(info)
            self.timing_vals[name].grid(row=row, column=1, sticky="w", padx=(8, 0))

        ttk.Button(f, text="Anwenden", command=self._timing_send).pack(anchor="w", pady=(6, 0))

        self._timing_update()

    def _timing_update(self, *_):
        """Berechnet Tick-Werte aus fps/ms-Eingaben und aktualisiert die Anzeige."""
        try:
            channel_fps   = self.channel_fps.get()
            on_ms    = self.led_on_ms.get()
            active   = sum(v.get() for v in self.ch_active)

            # Ein Frame = ein Kanal. Pro Sekunde laufen gesamt_fps Frames.
            # 1 Tick = 100 µs  →  1 s = 10000 Ticks
            gesamt_fps   = channel_fps * active
            frame_ticks  = round(10000.0 / gesamt_fps)
            led_off_tick = min(round(on_ms * 10), frame_ticks - 2)   # LED muss vor Frame-Ende aus sein

            self._frame_ticks_val  = frame_ticks
            self._led_off_tick_val = led_off_tick

            v = self.timing_vals
            v["Aktive Kanäle"].config(text=f"{active} von {NUM_CHANNELS}")
            v["Gesamt-FPS"]   .config(text=f"{gesamt_fps:.0f} fps  ({active} × {channel_fps:.0f})")
            v["Frame-Dauer"]  .config(text=f"{frame_ticks * 0.1:.1f} ms  ({frame_ticks} Ticks)")
            v["LED an"]       .config(text=f"{led_off_tick * 0.1:.1f} ms  (Tick 0 – {led_off_tick})")
            v["LED aus"]      .config(text=f"{(frame_ticks - led_off_tick) * 0.1:.1f} ms  "
                                           f"(Tick {led_off_tick} – {frame_ticks})")
        except (tk.TclError, ZeroDivisionError):
            pass

    def _timing_send(self):
        self.serial.send(f"SET_FRAME_TICKS {self._frame_ticks_val}")
        self.serial.send(f"SET_LED_OFF_TICK {self._led_off_tick_val}")

    # ─────────────────────────────────────────────────────────────────────────

    def _storage_panel(self, parent):
        f = ttk.LabelFrame(parent, text="Aufnahme", padding=6)
        f.pack(fill="x", pady=(0, 6))

        # Pfad
        row1 = ttk.Frame(f)
        row1.pack(fill="x", pady=2)
        ttk.Label(row1, text="Pfad:", width=8).pack(side="left")
        self.path_var = tk.StringVar(value=str(Path.home() / "Bilder"))
        ttk.Entry(row1, textvariable=self.path_var, width=20).pack(side="left", padx=4)
        ttk.Button(row1, text="…", width=2,
                   command=lambda: self.path_var.set(
                       filedialog.askdirectory() or self.path_var.get())
                   ).pack(side="left")

        # Praefix
        row2 = ttk.Frame(f)
        row2.pack(fill="x", pady=2)
        ttk.Label(row2, text="Praefix:", width=8).pack(side="left")
        self.prefix_var = tk.StringVar(value="IMG")
        ttk.Entry(row2, textvariable=self.prefix_var, width=10).pack(side="left", padx=4)

        # Format
        row3 = ttk.Frame(f)
        row3.pack(fill="x", pady=2)
        ttk.Label(row3, text="Format:", width=8).pack(side="left")
        self.fmt_var = tk.StringVar(value="tiff")
        ttk.Radiobutton(row3, text="TIFF",        variable=self.fmt_var, value="tiff").pack(side="left")
        ttk.Radiobutton(row3, text="NumPy .npy",  variable=self.fmt_var, value="npy" ).pack(side="left", padx=4)

        # Anzahl Bilder pro Run
        row_n = ttk.Frame(f)
        row_n.pack(fill="x", pady=2)
        ttk.Label(row_n, text="Anzahl:", width=8).pack(side="left")
        self.max_images_var = tk.IntVar(value=0)
        ttk.Spinbox(row_n, from_=0, to=1000000, increment=NUM_CHANNELS,
                    textvariable=self.max_images_var, width=8).pack(side="left", padx=4)
        ttk.Label(row_n, text="Bilder  (0 = unbegrenzt)").pack(side="left")

        # Start / Stop
        row4 = ttk.Frame(f)
        row4.pack(pady=(8, 2))
        self.start_btn = ttk.Button(row4, text="▶  Starten", command=self._acq_start, state="disabled")
        self.start_btn.pack(side="left", padx=2)
        self.stop_btn  = ttk.Button(row4, text="■  Stoppen", command=self._acq_stop,  state="disabled")
        self.stop_btn.pack(side="left", padx=2)

        self.count_lbl = ttk.Label(f, text="Bereit.")
        self.count_lbl.pack(anchor="w")

    def _acq_start(self):
        self._max_images = self.max_images_var.get()
        self.kamera.start_acquisition(
            output_dir   = Path(self.path_var.get()),
            file_format  = self.fmt_var.get(),
            prefix       = self.prefix_var.get(),
            num_channels = NUM_CHANNELS,
            roi          = self.roi,
            max_images   = self._max_images,
        )
        # Waehrend der Aufnahme keine Vorschau / ROI-Aenderung
        self.start_btn  .config(state="disabled")
        self.stop_btn   .config(state="normal")
        self.preview_btn.config(state="disabled")
        self.roi_btn    .config(state="disabled")

    def _acq_stop(self):
        self.kamera.stop_acquisition()
        self.start_btn  .config(state="normal")
        self.stop_btn   .config(state="disabled")
        self.preview_btn.config(state="normal")
        self.roi_btn    .config(state="normal")


    # ══════════════════════════════════════════════════════════════════════════
    # Rechte Spalte
    # ══════════════════════════════════════════════════════════════════════════

    def _camera_panel(self, parent):
        self._cam_frame = ttk.LabelFrame(parent, text="Kamera", padding=6)
        self._cam_frame.pack(fill="both", expand=True)
        f = self._cam_frame

        # Schaltflaechen
        btns = ttk.Frame(f)
        btns.pack(fill="x")
        self.cam_btn     = ttk.Button(btns, text="Kamera oeffnen",  command=self._cam_toggle)
        self.preview_btn = ttk.Button(btns, text="↻ Vorschau",      command=self._preview_grab,   state="disabled")
        self.roi_btn     = ttk.Button(btns, text="ROI auswaehlen",  command=self._roi_select,     state="disabled")
        self.cam_btn    .pack(side="left", padx=2)
        self.preview_btn.pack(side="left", padx=2)
        self.roi_btn    .pack(side="left", padx=2)

        # Kamera-Info (wird nach dem Oeffnen befuellt)
        self.cam_info_lbl = ttk.Label(f, text="Keine Kamera verbunden.")
        self.cam_info_lbl.pack(anchor="w", pady=(4, 0))

        # Belichtung
        exp_row = ttk.Frame(f)
        exp_row.pack(fill="x", pady=4)
        ttk.Label(exp_row, text="Belichtung:").pack(side="left")
        self.exp_var = tk.IntVar(value=2000)
        ttk.Spinbox(exp_row, from_=100, to=100000, textvariable=self.exp_var,
                    width=8, increment=100).pack(side="left", padx=4)
        ttk.Label(exp_row, text="µs").pack(side="left")
        ttk.Button(exp_row, text="Setzen",
                   command=self._exposure_set).pack(side="left", padx=6)

        # ROI-Anzeige + erreichbare FPS
        self.roi_lbl = ttk.Label(f, text="ROI: –")
        self.roi_lbl.pack(anchor="w")
        self.max_fps_lbl = ttk.Label(f, text="Max FPS: –")
        self.max_fps_lbl.pack(anchor="w")

        # Vorschau-Canvas (highlightthickness=0: sonst verdeckt ein 2-px-Fokusrand den Bildrand)
        self.canvas = tk.Canvas(f, width=PREVIEW_W, height=PREVIEW_H, highlightthickness=0)
        self.canvas.pack(pady=4, expand=True)

    def _exposure_set(self):
        self.kamera.set_exposure(self.exp_var.get())
        if not self.kamera.acquisition_active:   # ROI-Pruefung wuerde die Aufnahme stoeren
            self._roi_show(*self.roi)            # Max FPS haengt von der Belichtung ab

    def _roi_show(self, x, y, w, h):
        """Prueft den ROI an der Kamera (Rundung + erreichbare FPS) und zeigt ihn an."""
        roi, fps = self.kamera.roi_check(x, y, w, h)
        if not roi:
            return
        self.roi = list(roi)
        self.roi_lbl.config(text="ROI: x={}  y={}  w={}  h={}".format(*roi))
        self.max_fps_lbl.config(text=f"Max FPS mit ROI: {fps:.1f}")
        self._canvas_draw()

    def _cam_toggle(self):
        if self.kamera.is_open:
            self.kamera.close()
            self.cam_btn    .config(text="Kamera oeffnen")
            self.preview_btn.config(state="disabled")
            self.roi_btn    .config(state="disabled")
            self.start_btn  .config(state="disabled")
            self._cam_frame .config(text="Kamera")
            self.cam_info_lbl.config(text="Keine Kamera verbunden.")
            self.roi_lbl    .config(text="ROI: –")
            self.max_fps_lbl.config(text="Max FPS: –")
            self._last_img = None
            self.canvas.delete("all")
            return

        info = self.kamera.open()   # liefert Modell, Sensorgroesse, Pixelformat
        if not info:
            return
        self.cam_btn    .config(text="Kamera schliessen")
        self.preview_btn.config(state="normal")
        self.roi_btn    .config(state="normal")
        self.start_btn  .config(state="normal")
        self._cam_frame .config(text=f"Kamera  –  {info['model']}")
        self.cam_info_lbl.config(text=f"Sensor: {info['width']}×{info['height']} px"
                                      f"  |  Format: {info['px_format']}")
        self._roi_show(0, 0, info["width"], info["height"])

    # Holt einmalig ein Bild aus der Kamera
    def _preview_grab(self):
        img = self.kamera.grab_single_frame()
        if img is not None:
            self._last_img = img
            self._canvas_draw()

    # Zeigt das letzte Bild was gegrabbed wurde (Beim Start dunkle Fläche)
    def _canvas_draw(self):
        """
        Zeichnet das letzte Vollbild (oder eine dunkle Flaeche, solange es keins gibt)
        und den ROI als gruenen Rahmen darueber.
        """
        sw, sh     = self.kamera.sensor
        x, y, w, h = self.roi
        s = min(PREVIEW_W / sw, PREVIEW_H / sh)   # Sensor-Pixel → Canvas-Pixel

        self.canvas.delete("all")   # altes Bild entfernen, sonst stapeln sie sich
        if self._last_img is None:
            self.canvas.create_rectangle(0, 0, sw * s, sh * s, fill="#111", outline="")
        else:
            img = cv2.resize(self._last_img, (round(sw * s), round(sh * s)))
            # numpy → PIL → tkinter (Pillow wird benoetigt fuer die Konvertierung)
            self._tk_img = ImageTk.PhotoImage(Image.fromarray(img))
            self.canvas.create_image(0, 0, anchor="nw", image=self._tk_img)

        self.canvas.create_rectangle(x * s, y * s, (x + w) * s, (y + h) * s,
                                     outline="#2c2", width=2)

    def _roi_select(self):
        """
        Einzelbild grabben, cv2-Fenster oeffnen, Benutzer waehlt ROI per Maus.
        Quelle cv2.selectROI: https://docs.opencv.org/4.x/
        """
        img = self.kamera.grab_single_frame()
        if img is None:
            messagebox.showwarning("ROI", "Kein Bild von der Kamera erhalten.")
            return
        self._last_img = img   # dasselbe Bild danach auch in der Vorschau zeigen

        if img.ndim == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)

        win = "ROI auswaehlen  |  Enter = bestaetigen   C = abbrechen"
        cv2.namedWindow(win, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(win, 960, 600)
        x, y, w, h = cv2.selectROI(win, img, fromCenter=False, showCrosshair=True)
        cv2.destroyAllWindows()

        if w > 0 and h > 0:
            self._roi_show(x, y, w, h)
        else:
            self._canvas_draw()   # abgebrochen: neues Bild mit altem ROI zeigen

    # Befehlslog (untere Leiste)

    def _log_panel(self, parent):
        f = ttk.LabelFrame(parent, text="Befehlslog", padding=4)
        f.pack(fill="x", padx=6, pady=(0, 6))

        self.log_text = scrolledtext.ScrolledText(
            f, height=6, state="disabled",
            font=("Courier", 9), bg="white", fg="black",
        )
        self.log_text.pack(fill="x")

        ttk.Button(f, text="Log leeren", command=self._log_clear).pack(anchor="e", pady=(2, 0))

    def _log(self, text: str):
        line = f"[{time.strftime('%H:%M:%S')}]  {text}\n"
        self.root.after(0, self._log_write, line)

    def _log_write(self, line: str):
        self.log_text.config(state="normal")
        self.log_text.insert("end", line)
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def _log_clear(self):
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.config(state="disabled")


    # ══════════════════════════════════════════════════════════════════════════
    # Callbacks von KameraController + Beenden
    # ══════════════════════════════════════════════════════════════════════════

    def _on_count(self, count: int):
        """Wird vom Writer-Thread nach jedem gespeicherten Bild aufgerufen."""
        self.root.after(0, self._count_show, count)

    def _count_show(self, count: int):
        """Aktualisiert den Bild-Zaehler und stoppt, sobald die gewuenschte Anzahl gespeichert ist."""
        if self._max_images:
            self.count_lbl.config(text=f"{count} / {self._max_images} Bilder gespeichert")
            if count >= self._max_images and self.kamera.acquisition_active:
                self._acq_stop()
        else:
            self.count_lbl.config(text=f"{count} Bilder gespeichert")

    def _on_close(self):
        self.kamera.close()
        self.serial.disconnect()
        self.root.destroy()


# ── Start ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
