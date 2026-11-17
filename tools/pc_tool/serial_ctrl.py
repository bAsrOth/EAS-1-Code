"""
serial_ctrl.py – Serielle Verbindung zum STM32

Quellen:
  pyserial Dokumentation:  https://pyserial.readthedocs.io/en/latest/
  pyserial list_ports:     https://pyserial.readthedocs.io/en/latest/tools.html
"""

import serial
import serial.tools.list_ports


class SerialController:
    def __init__(self, log_fn):
        """
        log_fn: Callback-Funktion die aufgerufen wird wenn ein Befehl gesendet wird.
                Wird benutzt um den Befehl im GUI-Log anzuzeigen.
                Signatur: log_fn(text: str)
        """
        self.port   = None     # offener serial.Serial-Port (oder None)
        self._log   = log_fn

    def get_ports(self) -> list[str]:
        """Gibt alle verfuegbaren seriellen Ports zurueck.
        Windows: ['COM3', 'COM4', ...]   Linux: ['/dev/ttyACM0', ...]"""
        return [p.device for p in serial.tools.list_ports.comports()]

    def connect(self, port_name: str) -> bool:
        """
        Oeffnet den angegebenen Port mit 115200 Baud.
        Bei USB-CDC ist die Baudrate eigentlich egal, aber STM32 ist standardmaessig auf 115200 eingestellt.
        Gibt True zurueck wenn erfolgreich.
        """
        try:
            self.port = serial.Serial(port_name, baudrate=115200, timeout=1)
            return True
        except serial.SerialException as e:
            self._log(f"# Fehler: {e}")
            return False


    def disconnect(self):
        """Schliesst den seriellen Port."""
        if self.port and self.port.is_open:
            self.port.close()
        self.port = None

    # Kann ausgelesen werden um zu pruefen ob der Port offen ist
    @property
    def connected(self) -> bool:
        return self.port is not None and self.port.is_open

    def send(self, command: str) -> bool:
        """
        Sendet einen Befehl als ASCII-Zeile (wird mit '\n' abgeschlossen).
        Gibt True zurueck wenn erfolgreich, False wenn nicht verbunden.

        Befehlsformat:
          SET_CH 1 ON          Kanal 1 einschalten
          SET_CH 1 OFF         Kanal 1 ausschalten
          SET_DUTY 1 0.200     Duty Cycle Kanal 1 = 20%
          SET_FRAME_TICKS 42   Frame-Laenge = 42 Ticks
          SET_LED_OFF_TICK 20  LED aus ab Tick 20
        """
        if not self.connected:
            return False
        try:
            self.port.write((command + "\n").encode("ascii"))
            self._log(command)   # Befehl im GUI-Log anzeigen
            return True
        except serial.SerialException as e:
            self._log(f"# Sendefehler: {e}")
            return False
