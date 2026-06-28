#!/usr/bin/env python3
"""
Deploy a Python file to the AMYboard over the MicroPython REPL and verify the
board copy is byte-for-byte identical to the local file.

The board's FatFs can read but not write the SD card (it is large-capacity
exFAT), so sketches are deployed to *internal flash*, which is always writable
over serial. By default a sketch lands in /user/sketches/<basename>, which is
the folder the wrapper launcher loads from first -- so no SD card is needed to
iterate on a sketch.

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
import os
from pathlib import Path
import sys

from board_serial import BoardSerialSession, detect_port


DEFAULT_SKETCH_PATH = 'sketch.py'
SKETCH_DEST_DIR = '/user/sketches'
STATE_FILE = '/user/launcher_state'
CHUNK_SIZE = 180
WRITE_MARKER = '__WRITE_OK__'
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


def write_loaded_file(path, content):
    Path(path).write_text(content, encoding='utf-8')


def ensure_identical(local_code, loaded_code, local_path, dest):
    if local_code == loaded_code:
        return

    diff = ''.join(
        difflib.unified_diff(
            local_code.splitlines(keepends=True),
            loaded_code.splitlines(keepends=True),
            fromfile=local_path,
            tofile=dest,
        )
    )
    raise RuntimeError(
        'Deployment verification failed: board copy differs from local file.\n' + diff
    )


def deploy_and_verify(port, sketch_path, dest, loaded_path=None,
                      reset_after=True, activate=False):
    local_code = load_text(sketch_path)
    deploy_script = build_deploy_script(local_code, dest)

    print(f'Connecting to AMYboard on {port}...')
    print(f'Deploying {sketch_path} ({len(local_code)} bytes) -> {dest}...')

    outputs = []
    with BoardSerialSession(port) as session:
        deploy_output = session.run_paste_script(deploy_script, timeout=30)
        if WRITE_MARKER not in {line.strip() for line in deploy_output.splitlines()}:
            raise RuntimeError('Board did not confirm write.\n' + deploy_output)
        outputs.append(deploy_output)

        readback_output = session.run_command(build_readback_command(dest), timeout=20)
        outputs.append(readback_output)

        loaded_code = extract_readback(''.join(outputs))
        if loaded_path:
            write_loaded_file(loaded_path, loaded_code)
        ensure_identical(local_code, loaded_code, sketch_path, dest)
        print(f'Verified: board copy of {dest} matches {sketch_path}')

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
    parser.add_argument('--sketch', default=DEFAULT_SKETCH_PATH, help='Local file to deploy.')
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
