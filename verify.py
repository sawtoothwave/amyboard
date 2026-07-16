#!/usr/bin/env python3
"""
Pull /user/current/sketch.py off the AMYboard and compare it against a local file.

/user/current/sketch.py is the file the firmware boots -- i.e. the LAUNCHER
(wrapper_sketch.py), not a synth. So this answers "is the board's launcher what I
think it is?", and --sketch is almost always wrapper_sketch.py:

    python verify.py --sketch wrapper_sketch.py

Sketches live in /user/sketches/ and the launcher exec's the one named in
/user/launcher_state; they are verified by deploy_auto.py at deploy time, which
compares sha256 instead of reading the file back.

This is intentionally separate from deployment so transport issues during deploy
and content mismatches during verification stay independent.
"""

import argparse
import ast
import difflib
from pathlib import Path
import sys

from board_serial import BoardSerialSession, detect_port


# This tool reads /user/current/sketch.py -- the file the firmware boots, which is
# the LAUNCHER (wrapper_sketch.py), not a sketch. So --sketch is almost always
# wrapper_sketch.py. It is REQUIRED: it used to default to a root sketch.py, a
# leftover from before the launcher existed (back when /user/current/sketch.py WAS
# the polysynth). That default compared the launcher against an old synth and could
# never match. Sketches themselves live in /user/sketches/ and are verified by
# deploy_auto.py at deploy time.
BOARD_BOOT_FILE = '/user/current/sketch.py'
BEGIN_MARKER = '__READBACK_BEGIN__'
END_MARKER = '__READBACK_END__'


def load_text(path):
    return Path(path).read_text(encoding='utf-8')


def run_readback(port):
    command = (
        f"print('{BEGIN_MARKER}');"
        f"print(repr(open({BOARD_BOOT_FILE!r}).read()));"
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


def verify(port, sketch_path, loaded_path=None):
    output = run_readback(port)
    loaded_text = extract_readback(output)
    if loaded_path:
        write_loaded_file(loaded_path, loaded_text)

    local_text = load_text(sketch_path)
    label = loaded_path or BOARD_BOOT_FILE
    if local_text == loaded_text:
        print(f'Verified: {label} matches {sketch_path}')
        return 0

    print(f'Mismatch: {label} differs from {sketch_path}')
    print(diff_text(local_text, loaded_text, sketch_path, label), end='')
    return 1


def parse_args():
    parser = argparse.ArgumentParser(
        description=f'Read {BOARD_BOOT_FILE} (the LAUNCHER the firmware boots) off the '
                    'board and compare it to a local file. Usually: --sketch wrapper_sketch.py')
    parser.add_argument('--port', help='Serial port, e.g. /dev/cu.usbmodem1101. Auto-detected if omitted.')
    parser.add_argument('--sketch', required=True,
                        help=f'Local file to compare against {BOARD_BOOT_FILE} (REQUIRED -- '
                             'no default. Almost always wrapper_sketch.py, since that file '
                             'is the launcher, not a sketch.)')
    parser.add_argument('--loaded', default=None,
                        help='Optional path to write the board readback to (for debugging). '
                             'Omit to just diff without leaving a file behind.')
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
