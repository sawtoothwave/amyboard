#!/usr/bin/env python3
"""
Pull /user/current/sketch.py from AMYboard into sketch-loaded.py and compare it
against the local sketch.py.

This is intentionally separate from deployment so transport issues during deploy
and content mismatches during verification stay independent.
"""

import argparse
import ast
import difflib
from pathlib import Path
import sys

from board_serial import BoardSerialSession, detect_port


DEFAULT_SKETCH_PATH = 'sketch.py'
DEFAULT_LOADED_PATH = 'sketch-loaded.py'
BEGIN_MARKER = '__READBACK_BEGIN__'
END_MARKER = '__READBACK_END__'


def load_text(path):
    return Path(path).read_text(encoding='utf-8')


def run_readback(port):
    command = (
        f"print('{BEGIN_MARKER}');"
        "print(repr(open('/user/current/sketch.py').read()));"
        f"print('{END_MARKER}')"
    )
    with BoardSerialSession(port) as session:
        return session.run_command(command, timeout=20)


def extract_readback(output):
    lines = [line.strip() for line in output.splitlines()]
    try:
        begin_index = lines.index(BEGIN_MARKER)
        end_index = lines.index(END_MARKER, begin_index + 1)
    except ValueError as exc:
        raise RuntimeError('Did not find readback markers in board output.\n' + output) from exc

    if end_index <= begin_index + 1:
        raise RuntimeError('Did not find readback markers in board output.\n' + output)

    payload = '\n'.join(lines[begin_index + 1:end_index]).strip()
    if not payload:
        raise RuntimeError('Board readback payload was empty.')

    return ast.literal_eval(payload)


def write_loaded_file(path, content):
    Path(path).write_text(content, encoding='utf-8')


def diff_text(local_text, loaded_text, local_path, loaded_path):
    return ''.join(
        difflib.unified_diff(
            local_text.splitlines(keepends=True),
            loaded_text.splitlines(keepends=True),
            fromfile=local_path,
            tofile=loaded_path,
        )
    )


def verify(port, sketch_path, loaded_path):
    output = run_readback(port)
    loaded_text = extract_readback(output)
    write_loaded_file(loaded_path, loaded_text)

    local_text = load_text(sketch_path)
    if local_text == loaded_text:
        print(f'Verified: {loaded_path} matches {sketch_path}')
        return 0

    print(f'Mismatch: {loaded_path} differs from {sketch_path}')
    print(diff_text(local_text, loaded_text, sketch_path, loaded_path), end='')
    return 1


def parse_args():
    parser = argparse.ArgumentParser(description='Pull active AMYboard sketch and compare it to the local sketch.')
    parser.add_argument('--port', help='Serial port, e.g. /dev/cu.usbmodem1101. Auto-detected if omitted.')
    parser.add_argument('--sketch', default=DEFAULT_SKETCH_PATH, help='Path to local sketch file.')
    parser.add_argument('--loaded', default=DEFAULT_LOADED_PATH, help='Path to write the board readback file.')
    return parser.parse_args()


if __name__ == '__main__':
    try:
        args = parse_args()
        port = args.port or detect_port()
        sys.exit(verify(port, args.sketch, args.loaded))
    except KeyboardInterrupt:
        print('\nInterrupted by user')
        sys.exit(1)
    except Exception as exc:
        print(f'Error: {exc}')
        sys.exit(1)
