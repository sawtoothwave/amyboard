#!/usr/bin/env python3
"""Serial REPL helpers for talking to the AMYboard directly."""

from __future__ import annotations

import time

import serial
from serial.tools import list_ports


BAUD_RATE = 115200
PROMPT = b'>>> '


class BoardSerialError(RuntimeError):
    """Raised when the AMYboard REPL does not respond as expected."""


def detect_port():
    candidates = []
    for port in list_ports.comports():
        device = port.device or ''
        description = (port.description or '').lower()
        if 'usbmodem' in device:
            candidates.append(port)
        elif 'usb serial' in description or 'jtag/serial' in description:
            candidates.append(port)

    if not candidates:
        raise BoardSerialError('No AMYboard serial port detected. Use --port to specify one.')

    if len(candidates) > 1:
        names = ', '.join(port.device for port in candidates)
        raise BoardSerialError(f'Multiple serial ports detected: {names}. Use --port to choose one.')

    return candidates[0].device


class BoardSerialSession:
    def __init__(self, port, baud_rate=BAUD_RATE, timeout=0.2):
        self.port = port
        self.baud_rate = baud_rate
        self.timeout = timeout
        self.serial = None

    def __enter__(self):
        self.serial = serial.Serial(
            self.port,
            self.baud_rate,
            timeout=self.timeout,
            rtscts=False,
            dsrdtr=False,
        )
        time.sleep(0.2)
        self.interrupt_to_prompt()
        return self

    def __exit__(self, exc_type, exc, exc_tb):
        if self.serial is not None:
            self.serial.close()
            self.serial = None

    def interrupt_to_prompt(self, timeout=5):
        self.serial.reset_input_buffer()
        self.serial.write(b'\r\x03\x03')
        self.serial.flush()
        return self._read_until_prompt(timeout)

    def run_command(self, command, timeout=10):
        if '\n' in command or '\r' in command:
            raise ValueError('run_command expects a single REPL line without newlines.')

        self.serial.write(command.encode('utf-8') + b'\r')
        self.serial.flush()
        return self._read_until_prompt(timeout)

    def run_paste_script(self, script, timeout=30):
        self.serial.reset_input_buffer()
        self.serial.write(b'\x05')
        self.serial.flush()
        banner = self._read_until_idle(timeout=2)
        if 'paste mode' not in banner.lower():
            raise BoardSerialError('Board did not enter paste mode.')

        payload = script
        if not payload.endswith('\n'):
            payload += '\n'

        self.serial.write(payload.encode('utf-8'))
        self.serial.write(b'\x04')
        self.serial.flush()
        return self._read_until_prompt(timeout)

    def reset_board(self, timeout=2):
        self.serial.write(b'import machine;machine.reset()\r')
        self.serial.flush()
        return self._read_until_idle(timeout)

    def _read_until_prompt(self, timeout, idle_window=0.2):
        # Wait specifically for the REPL prompt token. The running sketch keeps
        # executing in the background on this firmware (Ctrl-C does not kill it),
        # so its output interleaves with ours in bursts; idle-based stopping
        # would bail during a gap before the prompt arrives. Anchoring on the
        # prompt token reads through that chatter until the REPL is actually back.
        output = self._read_until(idle_window=idle_window, timeout=timeout, until=PROMPT)
        if PROMPT not in output:
            raise BoardSerialError('Board did not return to the MicroPython prompt.')
        return output.decode('utf-8', errors='replace')

    def _read_until_idle(self, timeout, idle_window=0.2):
        output = self._read_until(idle_window=idle_window, timeout=timeout)
        return output.decode('utf-8', errors='replace')

    def _read_until(self, idle_window, timeout, until=None):
        deadline = time.monotonic() + timeout
        last_data_at = None
        chunks = []
        buffer = b''

        while time.monotonic() < deadline:
            waiting = self.serial.in_waiting or 1
            data = self.serial.read(waiting)
            if data:
                chunks.append(data)
                last_data_at = time.monotonic()
                if until is not None:
                    buffer += data
                    if until in buffer:
                        return b''.join(chunks)
                continue

            # With an explicit sentinel, idle gaps are expected (background sketch
            # output is bursty); keep reading until the token appears or we time
            # out. Without one, fall back to idle-based stopping as before.
            if until is None and last_data_at is not None \
                    and (time.monotonic() - last_data_at) >= idle_window:
                return b''.join(chunks)

        if chunks:
            return b''.join(chunks)

        raise BoardSerialError('Timed out waiting for board output.')