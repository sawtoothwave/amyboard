#!/usr/bin/env python3
"""Deploy a file to the AMYboard over WiFi (WebREPL) and verify it byte-for-byte.

The serial deploy tool (deploy_auto.py) needs a USB cable; this one needs only
the board's IP + WebREPL password, so you can iterate on the launcher while it
stays racked in the modular. It implements just enough of the WebREPL protocol
(WebSocket handshake + binary PUT_FILE / GET_FILE) to have zero dependencies
beyond the stdlib -- no mpremote/websocket libs required.

    python deploy_wifi.py --host 192.168.0.107 --password amyboard \
        --sketch wrapper_sketch.py --dest /user/current/sketch.py

Flow: PUT the file -> GET it back and compare bytes. It does NOT reset the board:
this firmware doesn't service the WebREPL REPL while the launcher runs (only the
file-transfer daemon responds), so machine.reset() can't be issued over WiFi. The
deployed file takes effect on the next reboot -- power-cycle the board, or use the
on-device Load Sketch (which resets). WiFi 'remembers last setting', so a board
that was on rejoins a few seconds after it reboots. CAUTION: if new code hangs at
boot you lose the WiFi link and must reconnect USB to recover.
"""

import argparse
import getpass
import socket
import struct
import sys

WEBREPL_REQ_S = "<2sBBQLH64s"
WEBREPL_PUT_FILE = 1
WEBREPL_GET_FILE = 2


class Websocket:
    """Minimal WebSocket client speaking the tiny subset WebREPL uses."""

    def __init__(self, sock):
        self.s = sock
        self.buf = b""

    def write(self, data):
        n = len(data)
        if n < 126:
            hdr = struct.pack(">BB", 0x82, n)          # binary frame, FIN
        else:
            hdr = struct.pack(">BBH", 0x82, 126, n)
        self.s.sendall(hdr + data)

    def _recvexactly(self, n):
        res = b""
        while n:
            chunk = self.s.recv(n)
            if not chunk:
                break
            res += chunk
            n -= len(chunk)
        return res

    def read(self, size, text_ok=False, size_match=True):
        while not self.buf:
            hdr = self._recvexactly(2)
            assert len(hdr) == 2, "socket closed"
            fl, sz = struct.unpack(">BB", hdr)
            if sz == 126:
                (sz,) = struct.unpack(">H", self._recvexactly(2))
            data = self._recvexactly(sz)
            if fl == 0x82 or (text_ok and fl == 0x81):
                self.buf = data
            # else: control/other frame -> drop, loop again
        d = self.buf[:size]
        self.buf = self.buf[size:]
        if size_match:
            assert len(d) == size, (len(d), size)
        return d


def _handshake(sock):
    f = sock.makefile("rwb", 0)
    f.write(
        b"GET / HTTP/1.1\r\nHost: amyboard\r\nConnection: Upgrade\r\n"
        b"Upgrade: websocket\r\nSec-WebSocket-Key: foo\r\n"
        b"Sec-WebSocket-Version: 13\r\nOrigin: http://amyboard\r\n\r\n"
    )
    f.readline()
    while f.readline() != b"\r\n":
        pass


def _login(ws, passwd):
    # Server streams "Password: " as text; answer after the ": ".
    while True:
        if ws.read(1, text_ok=True) == b":":
            assert ws.read(1, text_ok=True) == b" "
            break
    ws.write(passwd.encode("utf-8") + b"\r")


def _read_resp(ws):
    sig, code = struct.unpack("<2sH", ws.read(4))
    assert sig == b"WB", ("bad resp sig", sig)
    return code


def _put_file(ws, data, remote):
    dest = remote.encode("utf-8")
    rec = struct.pack(WEBREPL_REQ_S, b"WA", WEBREPL_PUT_FILE, 0, 0, len(data), len(dest), dest)
    ws.write(rec[:10])
    ws.write(rec[10:])
    assert _read_resp(ws) == 0, "board rejected PUT header"
    pos = 0
    while pos < len(data):
        ws.write(data[pos:pos + 1024])
        pos += 1024
    assert _read_resp(ws) == 0, "board rejected PUT data"


def _get_file(ws, remote):
    src = remote.encode("utf-8")
    rec = struct.pack(WEBREPL_REQ_S, b"WA", WEBREPL_GET_FILE, 0, 0, 0, len(src), src)
    ws.write(rec[:10])
    ws.write(rec[10:])
    assert _read_resp(ws) == 0, "board rejected GET header"
    out = b""
    while True:
        ws.write(b"\0")
        (sz,) = struct.unpack("<H", ws.read(2))
        if sz == 0:
            break
        while sz:
            chunk = ws.read(sz, size_match=False)
            out += chunk
            sz -= len(chunk)
    assert _read_resp(ws) == 0, "board rejected GET trailer"
    return out


def main():
    ap = argparse.ArgumentParser(description="Deploy a file to the AMYboard over WiFi (WebREPL).")
    ap.add_argument("--host", required=True, help="Board IP (see the WiFi submenu status line).")
    ap.add_argument("--port", type=int, default=8266)
    ap.add_argument("--password", help="WebREPL password (prompted if omitted).")
    ap.add_argument("--sketch", default="wrapper_sketch.py")
    ap.add_argument("--dest", default="/user/current/sketch.py")
    ap.add_argument("--reboot", action="store_true",
                    help="After a verified push, drop /user/reboot_request so a launcher "
                         "with the reboot hook resets to apply. (No effect until a "
                         "hook-bearing launcher is already running.)")
    ap.add_argument("--sentinel", default="/user/reboot_request",
                    help="Path of the remote-reboot sentinel file (must match the launcher).")
    args = ap.parse_args()

    passwd = args.password or getpass.getpass("WebREPL password: ")
    with open(args.sketch, "rb") as f:
        local = f.read()

    print(f"Connecting to ws://{args.host}:{args.port}/ ...")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(30)
    sock.connect((args.host, args.port))
    _handshake(sock)
    ws = Websocket(sock)
    _login(ws, passwd)

    print(f"Deploying {args.sketch} ({len(local)} bytes) -> {args.dest} ...")
    _put_file(ws, local, args.dest)
    print("Verifying readback ...")
    remote = _get_file(ws, args.dest)
    if remote != local:
        print(f"VERIFY FAILED: readback {len(remote)} bytes != local {len(local)} bytes.",
              file=sys.stderr)
        sock.close()
        return 2
    print(f"Verified: board copy of {args.dest} matches {args.sketch} byte-for-byte.")

    # This firmware doesn't service the WebREPL *REPL* while the launcher runs
    # (only the file-transfer daemon responds), so machine.reset() can't be issued
    # over WiFi directly. Instead, drop the sentinel file the launcher polls for.
    if args.reboot:
        print(f"Requesting remote reboot (dropping {args.sentinel}) ...")
        _put_file(ws, b"1", args.sentinel)
        sock.close()
        print("\nDeployed + verified + reboot requested. A launcher with the reboot")
        print("hook will reset within ~2s and (WiFi remembers last setting) rejoin a")
        print("few seconds later. If nothing happens, the running launcher predates")
        print("the hook -- power-cycle once, after which --reboot works wirelessly.")
    else:
        sock.close()
        print("\nDeployed + verified. The board is still running the OLD code in RAM.")
        print("To activate: power-cycle, on-device do Load Sketch, or re-run with")
        print("--reboot (works once a hook-bearing launcher is running).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
