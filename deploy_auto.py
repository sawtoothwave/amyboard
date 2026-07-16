#!/usr/bin/env python3
"""
Deploy a Python file to the AMYboard over the MicroPython REPL and verify the
board copy is byte-for-byte identical to the local file.

The board's FatFs can read but not write the SD card (it is large-capacity
exFAT), so sketches are deployed to *internal flash*, which is always writable
over serial. A sketch lands in /user/sketches/<basename>, which is the folder the
wrapper launcher loads from first -- so no SD card is needed to iterate.

--sketch is REQUIRED. There is deliberately no default: the board hosts many
sketches, so any default would silently deploy the wrong one. (It used to default
to a stale root sketch.py, which is exactly the accident this prevents.)

Verification hashes the file ON the board and compares sha256 -- it does NOT read
the file back. Reading back a 158 KB sketch meant ~15 s of unthrottled serial and
died with "unterminated string literal" if a single byte dropped; hashing costs 64
bytes of output and is a stronger check. The full readback now only runs when the
hash MISMATCHES, to produce a diff (best effort -- we're already in an error path).

Examples:
    # Deploy a sketch to flash and verify (does not auto-boot it):
    python deploy_auto.py --sketch sketches/01_polysynth.py

    # Deploy a sketch AND boot straight into it (sets launcher_state, resets):
    python deploy_auto.py --sketch sketches/01_polysynth.py --activate

    # (Re)deploy the global wrapper launcher to the firmware boot file:
    python deploy_auto.py --sketch wrapper_sketch.py --dest /user/current/sketch.py --loaded launcher-loaded.py
"""

import argparse
import ast
import base64
import difflib
import hashlib
import os
from pathlib import Path
import sys

from board_serial import BoardSerialSession, detect_port


SKETCH_DEST_DIR = '/user/sketches'
STATE_FILE = '/user/launcher_state'
CHUNK_SIZE = 180
HASH_CHUNK = 512               # bytes read per hash update on the board
WRITE_MARKER = '__WRITE_OK__'
SIZE_MARKER = '__SIZE__'
SHA_MARKER = '__SHA__'
READBACK_BEGIN = '__READBACK_BEGIN__'
READBACK_END = '__READBACK_END__'


def load_text(path):
    return Path(path).read_text(encoding='utf-8')


def build_deploy_script(local_code, dest):
    encoded = base64.b64encode(local_code.encode('utf-8')).decode('ascii')
    chunks = [encoded[i:i + CHUNK_SIZE] for i in range(0, len(encoded), CHUNK_SIZE)]
    chunk_lines = ',\n'.join(f"    {chunk!r}" for chunk in chunks)
    parent = dest.rsplit('/', 1)[0] or '/'
    return f"""import os
import ubinascii

def _mkdirs(path):
    cur = ''
    for part in path.split('/'):
        if not part:
            continue
        cur += '/' + part
        try:
            os.mkdir(cur)
        except OSError:
            pass

_mkdirs({parent!r})
chunks = [
{chunk_lines}
]
with open({dest!r}, 'w') as handle:
    for chunk in chunks:
        handle.write(ubinascii.a2b_base64(chunk).decode())
print('{WRITE_MARKER}')
"""


def build_readback_command(dest):
    return (
        f"print('{READBACK_BEGIN}');"
        f"print(repr(open({dest!r}).read()));"
        f"print('{READBACK_END}')"
    )


def extract_readback(output):
    lines = [line.strip() for line in output.splitlines()]
    try:
        begin_index = lines.index(READBACK_BEGIN)
        end_index = lines.index(READBACK_END, begin_index + 1)
    except ValueError as exc:
        raise RuntimeError('Did not find readback markers in board output.') from exc

    if end_index <= begin_index + 1:
        raise RuntimeError('Did not find readback markers in board output.')

    payload = '\n'.join(lines[begin_index + 1:end_index]).strip()
    if not payload:
        raise RuntimeError('Board readback payload was empty.')

    return ast.literal_eval(payload)


def build_hash_script(dest):
    # Hash the file ON the board and print only size + hex digest (~80 bytes total),
    # instead of shipping the whole file back. Chunked so a large sketch never needs
    # to be held in the board's RAM all at once.
    return (
        "import uhashlib, ubinascii\n"
        "_h = uhashlib.sha256()\n"
        "_n = 0\n"
        "_f = open(%r, 'rb')\n"
        "while True:\n"
        "    _b = _f.read(%d)\n"
        "    if not _b:\n"
        "        break\n"
        "    _n += len(_b)\n"
        "    _h.update(_b)\n"
        "_f.close()\n"
        "print('%s' + str(_n))\n"
        "print('%s' + ubinascii.hexlify(_h.digest()).decode())\n"
    ) % (dest, HASH_CHUNK, SIZE_MARKER, SHA_MARKER)


def extract_hash(output):
    # Paste mode echoes the script, so match only on lines that START with a marker
    # (the echoed `print('__SHA__' + ...)` line starts with "print(", not the marker).
    size = sha = None
    for line in output.splitlines():
        line = line.strip()
        if line.startswith(SIZE_MARKER):
            try:
                size = int(line[len(SIZE_MARKER):])
            except ValueError:
                pass
        elif line.startswith(SHA_MARKER):
            sha = line[len(SHA_MARKER):].strip()
    if size is None or not sha:
        raise RuntimeError('Board did not report a hash for the deployed file.\n' + output)
    return size, sha


def write_loaded_file(path, content):
    Path(path).write_text(content, encoding='utf-8')


def report_mismatch(session, local_code, local_path, dest, board_size, board_sha):
    # Only reached when the hash already proved a mismatch. Try the full readback to
    # show WHAT differs -- best effort, since that readback is exactly what struggles
    # on a large file. Never let a failure here mask the real error.
    detail = ''
    try:
        out = session.run_command(build_readback_command(dest), timeout=30)
        loaded_code = extract_readback(out)
        detail = '\n' + ''.join(
            difflib.unified_diff(
                local_code.splitlines(keepends=True),
                loaded_code.splitlines(keepends=True),
                fromfile=local_path,
                tofile=dest,
            )
        )
    except Exception as exc:
        detail = f'\n(could not read the board copy back for a diff: {exc})'
    raise RuntimeError(
        'Deployment verification failed: board copy differs from local file.\n'
        f'  local: {len(local_code.encode("utf-8"))} bytes sha256={hashlib.sha256(local_code.encode("utf-8")).hexdigest()}\n'
        f'  board: {board_size} bytes sha256={board_sha}' + detail
    )


def deploy_and_verify(port, sketch_path, dest, loaded_path=None,
                      reset_after=True, activate=False):
    local_code = load_text(sketch_path)
    local_bytes = local_code.encode('utf-8')
    local_sha = hashlib.sha256(local_bytes).hexdigest()
    deploy_script = build_deploy_script(local_code, dest)

    print(f'Connecting to AMYboard on {port}...')
    print(f'Deploying {sketch_path} ({len(local_bytes)} bytes) -> {dest}...')

    with BoardSerialSession(port) as session:
        deploy_output = session.run_paste_script(deploy_script, timeout=60)
        if WRITE_MARKER not in {line.strip() for line in deploy_output.splitlines()}:
            raise RuntimeError('Board did not confirm write.\n' + deploy_output)

        board_size, board_sha = extract_hash(
            session.run_paste_script(build_hash_script(dest), timeout=45)
        )
        if board_size != len(local_bytes) or board_sha != local_sha:
            report_mismatch(session, local_code, sketch_path, dest, board_size, board_sha)
        print(f'Verified: board copy of {dest} matches {sketch_path} '
              f'({board_size} bytes, sha256 {board_sha[:12]}...)')

        if loaded_path:
            # The hash just proved the board copy is byte-identical to local_code, so
            # writing local_code here records exactly what is on the board -- without
            # dragging the whole file back over serial to learn what we already know.
            write_loaded_file(loaded_path, local_code)

        if activate:
            name = dest.rsplit('/', 1)[-1]
            print(f'Activating {name} (writing launcher_state, board will boot into it)...')
            session.run_paste_script(
                "with open(%r, 'w') as f: f.write(%r)\n" % (STATE_FILE, name),
                timeout=10,
            )

        if reset_after:
            print('Resetting board...')
            session.reset_board()


def parse_args():
    parser = argparse.ArgumentParser(
        description='Deploy a Python file to the AMYboard internal flash and verify it.'
    )
    parser.add_argument('--port', help='Serial port, e.g. /dev/cu.usbmodem1101. Auto-detected if omitted.')
    parser.add_argument('--sketch', required=True,
                        help='Local file to deploy (REQUIRED -- no default: the board hosts '
                             'many sketches, so guessing one would silently deploy the wrong '
                             'thing. e.g. sketches/01_polysynth.py)')
    parser.add_argument('--dest', default=None,
                        help='Board destination path. Defaults to %s/<basename>.' % SKETCH_DEST_DIR)
    parser.add_argument('--loaded', default=None,
                        help='Optional local path to save the board readback (for debugging).')
    parser.add_argument('--activate', action='store_true',
                        help='After deploy, set launcher_state to this sketch and reset so the board boots into it.')
    parser.add_argument('--no-reset', action='store_true', help='Skip the final board reset.')
    return parser.parse_args()


if __name__ == '__main__':
    try:
        args = parse_args()
        port = args.port or detect_port()
        dest = args.dest or (SKETCH_DEST_DIR + '/' + os.path.basename(args.sketch))
        deploy_and_verify(
            port=port,
            sketch_path=args.sketch,
            dest=dest,
            loaded_path=args.loaded,
            reset_after=not args.no_reset,
            activate=args.activate,
        )
        sys.exit(0)
    except KeyboardInterrupt:
        print('\nInterrupted by user')
        sys.exit(1)
    except Exception as exc:
        print(f'Error: {exc}')
        sys.exit(1)
