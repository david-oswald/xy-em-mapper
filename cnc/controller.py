"""Simple CNC serial controller for GRBL-like boards.

This module wraps common movement and connection operations with explicit
error handling so higher-level scripts can focus on toolpaths.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import serial


class CNCError(Exception):
    """Base error for CNC controller failures."""


class CNCConnectionError(CNCError):
    """Raised when serial connection or handshake fails."""


class CNCCommandError(CNCError):
    """Raised when the controller returns an error for a command."""


@dataclass
class CNCConfig:
    port: str
    baudrate: int = 115200
    timeout: float = 1.0


class CNCController:
    """Small GRBL-oriented CNC controller.

    The class sends plain G-code over serial and expects `ok` / `error` style
    responses as used by GRBL controllers.
    """

    def __init__(self, config: CNCConfig):
        self.config = config
        self._ser: serial.Serial | None = None

    def connect(self) -> None:
        """Open serial connection and initialize controller state."""
        if self._ser and self._ser.is_open:
            return

        try:
            self._ser = serial.Serial(
                self.config.port,
                self.config.baudrate,
                timeout=self.config.timeout,
            )
        except serial.SerialException as exc:
            raise CNCConnectionError(
                f"Could not open serial port {self.config.port}: {exc}"
            ) from exc

        # Let the board boot and flush stale boot text.
        time.sleep(2.0)
        self._ser.reset_input_buffer()
        self._ser.reset_output_buffer()

        # Wake up GRBL and verify communication.
        self._write_raw("\r\n\r\n")
        time.sleep(0.2)
        self._ser.reset_input_buffer()

        # Try to unlock if alarmed and enforce absolute positioning.
        self._send_command("$X", wait_for_ok=False)
        self._send_command("G90")
        self._send_command("G21")

    def close(self) -> None:
        if self._ser and self._ser.is_open:
            self._ser.close()

    def __enter__(self) -> "CNCController":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def move_to(
        self,
        x: float | None = None,
        y: float | None = None,
        z: float | None = None,
        feed_rate: float | None = None,
    ) -> None:
        """Move to an absolute XYZ coordinate using G1."""
        words = ["G1"]
        if x is not None:
            words.append(f"X{x:.3f}")
        if y is not None:
            words.append(f"Y{y:.3f}")
        if z is not None:
            words.append(f"Z{z:.3f}")
        if feed_rate is not None:
            words.append(f"F{feed_rate:.1f}")

        if len(words) == 1:
            raise CNCCommandError("move_to requires at least one axis or feed rate")

        self._send_command(" ".join(words))

    def rapid_to(
        self,
        x: float | None = None,
        y: float | None = None,
        z: float | None = None,
    ) -> None:
        """Rapid move to an absolute coordinate using G0."""
        words = ["G0"]
        if x is not None:
            words.append(f"X{x:.3f}")
        if y is not None:
            words.append(f"Y{y:.3f}")
        if z is not None:
            words.append(f"Z{z:.3f}")

        if len(words) == 1:
            raise CNCCommandError("rapid_to requires at least one axis")

        self._send_command(" ".join(words))

    def wait_until_idle(self, timeout_s: float = 30.0) -> None:
        """Poll status (`?`) until controller reports `Idle`."""
        if not self._ser or not self._ser.is_open:
            raise CNCConnectionError("Controller not connected")

        start = time.time()
        while True:
            self._write_raw("?")
            line = self._read_line()
            if line and "<Idle" in line:
                return
            if time.time() - start > timeout_s:
                raise CNCCommandError("Timed out waiting for controller to become idle")
            time.sleep(0.05)

    def _send_command(self, command: str, wait_for_ok: bool = True) -> None:
        if not self._ser or not self._ser.is_open:
            raise CNCConnectionError("Controller not connected")

        self._write_raw(command + "\n")
        if not wait_for_ok:
            return

        deadline = time.time() + max(2.0, self.config.timeout * 5)
        while time.time() < deadline:
            line = self._read_line()
            if not line:
                continue
            lower = line.lower()
            if lower == "ok":
                return
            if lower.startswith("error") or lower.startswith("alarm"):
                raise CNCCommandError(f"Controller rejected '{command}': {line}")

        raise CNCCommandError(f"No response for command '{command}'")

    def _write_raw(self, data: str) -> None:
        if not self._ser or not self._ser.is_open:
            raise CNCConnectionError("Controller not connected")
        self._ser.write(data.encode("ascii", errors="strict"))

    def _read_line(self) -> str:
        if not self._ser or not self._ser.is_open:
            raise CNCConnectionError("Controller not connected")
        raw = self._ser.readline()
        if not raw:
            return ""
        return raw.decode("ascii", errors="ignore").strip()
