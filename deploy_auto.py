#!/usr/bin/env python3
"""
Deploy sketch.py to AMYboard via an interactive mpremote REPL session and verify
that the board's active sketch is byte-for-byte identical to the local file.

This avoids the AMYboard web editor and writes a local sketch-loaded.py readback
after every deploy so deployment ambiguity is removed from debugging.
"""

import argparse
import ast
import base64
import difflib
from pathlib import Path
import sys

from board_serial import BoardSerialSession, detect_port


DEFAULT_SKETCH_PATH = 'sketch.py'
DEFAULT_LOADED_PATH = 'sketch-loaded.py'
CHUNK_SIZE = 180
WRITE_MARKER = '__WRITE_OK__'
READBACK_BEGIN = '__READBACK_BEGIN__'
READBACK_END = '__READBACK_END__'


def load_text(path):
    return Path(path).read_text(encoding='utf-8')


def build_deploy_script(local_code):
    encoded = base64.b64encode(local_code.encode('utf-8')).decode('ascii')
    chunks = [encoded[i:i + CHUNK_SIZE] for i in range(0, len(encoded), CHUNK_SIZE)]
    chunk_lines = ',\n'.join(f"    {chunk!r}" for chunk in chunks)
    return f"""import os
import ubinascii

try:
    os.mkdir('/user/current')
except OSError:
    pass
chunks = [
{chunk_lines}
]
with open('/user/current/sketch.py', 'w') as handle:
    for chunk in chunks:
        handle.write(ubinascii.a2b_base64(chunk).decode())
print('{WRITE_MARKER}')
"""


def build_readback_command():
    return (
        f"print('{READBACK_BEGIN}');"
        "print(repr(open('/user/current/sketch.py').read()));"
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


def ensure_identical(local_code, loaded_code, local_path, loaded_path):
    if local_code == loaded_code:
        return

    diff = ''.join(
        difflib.unified_diff(
            local_code.splitlines(keepends=True),
            loaded_code.splitlines(keepends=True),
            fromfile=local_path,
            tofile=loaded_path,
        )
    )
    raise RuntimeError(
        'Deployment verification failed: board sketch differs from local sketch.\n' + diff
    )


def deploy_and_verify(port, sketch_path, loaded_path, reset_after=True):
    local_code = load_text(sketch_path)
    deploy_script = build_deploy_script(local_code)

    print(f'Connecting to AMYboard on {port}...')
    print(f'Deploying {sketch_path} ({len(local_code)} bytes)...')

    outputs = []
    with BoardSerialSession(port) as session:
        deploy_output = session.run_paste_script(deploy_script, timeout=30)
        if WRITE_MARKER not in {line.strip() for line in deploy_output.splitlines()}:
            raise RuntimeError('Board did not confirm sketch write.\n' + deploy_output)
        outputs.append(deploy_output)

        readback_output = session.run_command(build_readback_command(), timeout=20)
        outputs.append(readback_output)

        loaded_code = extract_readback(''.join(outputs))
        write_loaded_file(loaded_path, loaded_code)
        ensure_identical(local_code, loaded_code, sketch_path, loaded_path)

        if reset_after:
            print('Resetting board...')
            session.reset_board()

    print(f'Verified: {loaded_path} matches {sketch_path}')


def parse_args():
    parser = argparse.ArgumentParser(
        description='Deploy sketch.py to AMYboard and verify the board copy matches locally.'
    )
    parser.add_argument('--port', help='Serial port, e.g. /dev/cu.usbmodem1101. Auto-detected if omitted.')
    parser.add_argument('--sketch', default=DEFAULT_SKETCH_PATH, help='Path to local sketch file.')
    parser.add_argument('--loaded', default=DEFAULT_LOADED_PATH, help='Path to write board readback file.')
    parser.add_argument('--no-reset', action='store_true', help='Skip the final board reset.')
    return parser.parse_args()


if __name__ == '__main__':
    try:
        args = parse_args()
        port = args.port or detect_port()
        deploy_and_verify(
            port=port,
            sketch_path=args.sketch,
            loaded_path=args.loaded,
            reset_after=not args.no_reset,
        )
        sys.exit(0)
    except KeyboardInterrupt:
        print('\nInterrupted by user')
        sys.exit(1)
    except Exception as exc:
        print(f'Error: {exc}')
        sys.exit(1)
