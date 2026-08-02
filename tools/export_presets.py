"""Turn presets saved on a board into arctor.py's baked-in FACTORY_PRESETS.

The sounds are designed ON HARDWARE -- that is the only place they can be judged
-- so the board's /user/arctor_presets.json is the source of truth and this tool
is the one-way road from there into the source. It reads that file (over serial,
or from a local copy), validates every preset against the sketch's own PARAMS
table, and rewrites the generated block in sketches/arctor.py in place.

Validation is the point of having a tool at all. A hand-pasted block can carry a
name the on-device editor cannot reproduce, or a CC that no longer exists after a
param is renamed or retired -- neither of which fails loudly. A bad name is
un-editable on the board; a stale CC is silently skipped by _apply_preset, so the
preset just sounds subtly wrong, which is the worst way for this to break.

  python3 tools/export_presets.py --from-board            # read the board, rewrite arctor.py
  python3 tools/export_presets.py presets.json            # ...from a local file instead
  python3 tools/export_presets.py --from-board --dry-run  # print the block, change nothing
  python3 tools/export_presets.py presets.json --names "pad1,bass1"

Requires pyserial for --from-board (same dependency as deploy_auto.py).
"""
import argparse
import base64
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, REPO)

SKETCH = os.path.join(REPO, 'sketches', 'arctor.py')
BEGIN = '# BEGIN GENERATED FACTORY PRESETS -- tools/export_presets.py'
END = '# END GENERATED FACTORY PRESETS'
REMOTE = '/user/arctor_presets.json'

# Mirrors _NAME_RING's plain characters. A name outside this set can be stored and
# displayed but NOT retyped on the board, so it must never ship as a factory name.
NAME_OK = re.compile(r'^[a-z0-9 ]+$')

DUMP = """
import ubinascii
def _dump(path):
    print('BEGINDUMP')
    with open(path, 'rb') as f:
        while True:
            b = f.read(256)
            if not b:
                break
            print('C:' + ubinascii.b2a_base64(b).decode().strip())
    print('ENDDUMP')
"""


def read_board():
    """Pull the preset file over serial, base64 in marker-fenced chunks.

    Fenced because the REPL echoes what it receives, so markers are the only
    reliable way to tell board output from echo; chunked because a single
    multi-KB print is where these serial reads have dropped bytes before.
    """
    from board_serial import BoardSerialSession, detect_port
    with BoardSerialSession(detect_port()) as s:
        s.run_paste_script(DUMP, timeout=15)
        out = s.run_command("_dump('%s')" % REMOTE, timeout=30)
        out += s.run_command('pass', timeout=10)
    if 'BEGINDUMP' not in out or 'ENDDUMP' not in out:
        sys.exit('incomplete dump from the board; tail:\n%s' % out[-600:])
    body = out.split('BEGINDUMP', 1)[1].split('ENDDUMP', 1)[0]
    # Only the board's own chunk lines -- not the echoed source of _dump, which
    # also contains the literal 'C:'.
    chunks = re.findall(r'^C:([A-Za-z0-9+/=]+)\s*$', body, re.M)
    return json.loads(b''.join(base64.b64decode(c) for c in chunks).decode())


def sketch_facts():
    """The sketch's own PARAMS/limits, read by running it under the sim stubs.

    Imported rather than duplicated so this tool cannot drift from the sketch it
    writes into: rename a param's CC and the next export fails here.
    """
    import grid_sim as gs
    ns = gs.load(SKETCH)
    return ({p.cc for p in ns['PARAMS']}, ns['PRESET_NAME_MAX'],
            ns['MAX_PRESETS'], ns['INIT_PRESET_NAME'])


def validate(presets, ccs, name_max, max_presets, init_name):
    errs = []
    seen = set()
    for i, p in enumerate(presets):
        if not isinstance(p, dict) or not isinstance(p.get('name'), str) \
                or not isinstance(p.get('cc'), dict):
            errs.append('#%d: not a {name, cc} record' % i)
            continue
        name = p['name']
        if name in seen:
            errs.append('%r: duplicate name' % name)
        seen.add(name)
        if len(name) > name_max:
            errs.append('%r: %d chars, over the %d cap' % (name, len(name), name_max))
        if not NAME_OK.match(name):
            errs.append('%r: has characters the on-device name editor cannot type '
                        '(allowed: a-z 0-9 space)' % name)
        if name.upper() == init_name:
            errs.append('%r: reserved for the built-in preset' % name)
        for k, v in p['cc'].items():
            if not str(k).isdigit() or int(k) not in ccs:
                errs.append('%r: CC %s is not in PARAMS (renamed or retired?)'
                            % (name, k))
            elif not (isinstance(v, int) and 0 <= v <= 127):
                errs.append('%r: CC %s value %r is not an int 0-127' % (name, k, v))
        missing = len(ccs) - len([k for k in p['cc'] if str(k).isdigit()
                                  and int(k) in ccs])
        if missing:
            # Not fatal: _apply_preset resets anything absent to its default, which
            # is exactly right for a preset saved before a param existed. Worth
            # saying out loud, though, because it is usually a stale export.
            print('note: %r is missing %d of %d params (they will load as '
                  'defaults)' % (name, missing, len(ccs)))
    if len(presets) > max_presets:
        errs.append('%d presets, over MAX_PRESETS (%d)' % (len(presets), max_presets))
    return errs


def render(presets):
    """The generated block: one preset per record, CCs sorted numerically.

    Sorted so re-exporting an unchanged board is a no-op diff -- dict order out of
    JSON is insertion order, which would otherwise reshuffle the block on any
    save and bury the real change.
    """
    out = [BEGIN, 'FACTORY_PRESETS = (']
    for p in presets:
        out.append('    {')
        out.append("        'name': %r," % p['name'])
        out.append("        'cc': {")
        items = sorted(p['cc'].items(), key=lambda kv: int(kv[0]))
        line = '           '
        for k, v in items:
            piece = " '%s': %d," % (k, v)
            if len(line) + len(piece) > 88:
                out.append(line)
                line = '           '
            line += piece
        if line.strip():
            out.append(line)
        out.append('        },')
        out.append('    },')
    out.append(')')
    out.append(END)
    return '\n'.join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('source', nargs='?', help='local preset JSON (default: --from-board)')
    ap.add_argument('--from-board', action='store_true', help='read the file over serial')
    ap.add_argument('--names', help='comma-separated subset, in the order given')
    ap.add_argument('--dry-run', action='store_true', help='print the block, write nothing')
    args = ap.parse_args()

    if args.from_board:
        presets = read_board()
    elif args.source:
        presets = json.load(open(args.source))
    else:
        ap.error('pass a JSON path or --from-board')
    if not isinstance(presets, list):
        sys.exit('expected a list of presets, got %s' % type(presets).__name__)

    if args.names:
        want = [n.strip() for n in args.names.split(',') if n.strip()]
        by_name = {p.get('name'): p for p in presets if isinstance(p, dict)}
        missing = [n for n in want if n not in by_name]
        if missing:
            sys.exit('not on the board: %s\navailable: %s'
                     % (', '.join(missing), ', '.join(sorted(by_name))))
        presets = [by_name[n] for n in want]

    ccs, name_max, max_presets, init_name = sketch_facts()
    errs = validate(presets, ccs, name_max, max_presets, init_name)
    if errs:
        sys.exit('refusing to write:\n  ' + '\n  '.join(errs))

    block = render(presets)
    print('%d preset(s): %s' % (len(presets), ', '.join(p['name'] for p in presets)))
    if args.dry_run:
        print(block)
        return 0

    src = open(SKETCH).read()
    if BEGIN not in src or END not in src:
        sys.exit('markers not found in %s' % SKETCH)
    head, rest = src.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    open(SKETCH, 'w').write(head + block + tail)
    print('wrote %s (%d bytes of block)' % (SKETCH, len(block)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
