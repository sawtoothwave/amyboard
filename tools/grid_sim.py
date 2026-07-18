"""Run the polysynth's REAL menu code on the host, with the board stubbed out.

Why: some behaviour cannot be checked any other way. The grid's response to an
incoming MIDI CC needs a MIDI source; the board has no REPL while the sketch is
running, so a CC cannot be injected over serial either. Rather than ship that path
on reasoning alone, this stubs `amy` / `amyboard` / `midi`, execs the sketch, and
drives the actual SketchMenu.

What it proves: state-machine behaviour (which cells get marked stale, which get
repainted on which tick, what gets pushed to the panel). What it does NOT prove:
anything about real I2C timing or audio — see tools/README.md.

Usage:  python3 tools/grid_sim.py [path/to/sketch.py]
Exits non-zero on failure, so it can gate a deploy.
"""
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(REPO, 'sketches', '01_polysynth.py')

PUSHES = []          # (kind, x0, x1, y0, y1) recorded per panel write


class _FakeHW:
    width = 128
    col_addr = (0, 63)
    row_addr = (0, 127)

    def __init__(self):
        self.buffer = bytearray(128 * 128 // 2)
        self._win = [0, 0, 0, 0]

    def write_cmd(self, c):
        pass

    def write_data(self, b):
        pass


class _FakeDisplay:
    def __init__(self):
        self._hw = _FakeHW()

    def fill(self, c):
        pass

    def fill_rect(self, x, y, w, h, c):
        pass

    def text(self, s, x, y, c):
        pass


def _install_micropython_time():
    # MicroPython's monotonic tick API, which CPython's `time` lacks. Added to the
    # real module (the sketch does a plain `import time`). ticks_ms wraps on the
    # board; we do not simulate that here -- see [[amyboard-timing-gotchas]] for why
    # ticks_diff is always the right way to compare them.
    import time as _t
    if not hasattr(_t, 'ticks_ms'):
        _t.ticks_ms = lambda: int(_t.monotonic() * 1000)
        _t.ticks_us = lambda: int(_t.monotonic() * 1000000)
        _t.ticks_diff = lambda a, b: a - b
        _t.ticks_add = lambda a, b: a + b
    return _t


def _install_stubs():
    _install_micropython_time()
    amy = types.ModuleType('amy')
    amy.send = lambda **kw: None
    amy.millis = lambda: 0
    for n in ('reset', 'volume', 'stop', 'start', 'live', 'pause'):
        setattr(amy, n, lambda *a, **k: None)
    # The constants the sketch reads at module level. Values are arbitrary here --
    # nothing under test compares them, they only need to exist and be distinct.
    for i, n in enumerate(('SINE', 'PULSE', 'SAW_DOWN', 'SAW_UP', 'TRIANGLE', 'NOISE',
                           'SILENT', 'FILTER_LPF24', 'FILTER_LPF', 'FILTER_BPF',
                           'FILTER_HPF')):
        setattr(amy, n, i)

    amyboard = types.ModuleType('amyboard')
    amyboard.display = _FakeDisplay()
    amyboard.display_refresh = lambda *a, **k: PUSHES.append(('FULL_REFRESH', 0, 127, 0, 127))
    amyboard.init_display = lambda *a, **k: None
    amyboard.init_midi = lambda *a, **k: None
    amyboard.init_buttons = lambda *a, **k: None
    amyboard.read_encoder = lambda *a, **k: 0
    amyboard.read_buttons = lambda *a, **k: 0
    amyboard.set_neopixel = lambda *a, **k: None
    amyboard.start_amy = lambda *a, **k: None

    midi = types.ModuleType('midi')
    midi.add_callback = lambda cb: None

    for m in (amy, amyboard, midi):
        sys.modules[m.__name__] = m
    return amy, amyboard, midi


def load(path=SRC):
    _install_stubs()
    ns = {'__name__': 'polysynth_sim'}
    exec(compile(open(path).read(), path, 'exec'), ns)
    return ns


def _patch_push(ns):
    """Record what the sketch pushes to the panel, and how big each push is."""
    def _push_window(x0, x1, y0, y1):
        PUSHES.append(('WIN', x0, x1, y0, y1))
        return True

    def _push_rows(y0, y1):
        PUSHES.append(('ROWS', 0, 127, y0, y1))
        return True

    ns['_push_window'] = _push_window
    ns['_push_rows'] = _push_rows
    ns['_begin_flush'] = lambda y0, y1: PUSHES.append(('FLUSH', 0, 127, y0, y1))


def tick(menu, lvl):
    """Simulate one loop() call reaching the renderer.

    Renders are gated by EDIT_REFRESH_MS against the last render's timestamp. On the
    board loop() arrives every ~69 ms so the gate always passes; here the calls are
    microseconds apart and would be throttled away, so we age the timestamp to stand
    in for a real tick. Without this the sim silently renders NOTHING and every check
    "passes" by doing nothing.
    """
    menu._edit_last_render = 0
    menu.dirty = True
    lvl.render(menu)


def cells_pushed():
    """Which (x, y) cell rects were pushed since the last clear."""
    return {(p[1], p[3]) for p in PUSHES if p[0] == 'WIN'}


def header_pushed():
    return any(p[0] == 'ROWS' and p[3] == 0 for p in PUSHES)


FAILED = []


def check(cond, what):
    print('%-4s %s' % ('ok' if cond else 'FAIL', what))
    if not cond:
        FAILED.append(what)


def main():
    ns = load()
    _patch_push(ns)
    SketchMenu = ns['SketchMenu']
    GridLevel = ns['_GridLevel']
    param_values = ns['param_values']
    EXT_MAX = ns['GRID_EXT_MAX']

    menu = SketchMenu()
    menu.open()
    lvl = GridLevel('VCF')
    menu.stack.append(lvl)

    # settle: one full render, then start recording
    tick(menu, lvl)
    PUSHES.clear()

    ps = lvl.params
    print('\n--- VCF: %d params, cursor at %s' % (len(ps), ps[lvl.idx].label))

    # 1. a CC for a param that is NOT the cursor must mark that cell stale
    far = 5                                   # some cell away from the cursor
    assert far != lvl.idx
    cc = ps[far].cc
    param_values[cc] = 99
    menu.note_external_cc(cc, 99)
    check(far in lvl.ext, 'CC on a non-selected cell marks it stale (the old bug: it did not)')
    check(lvl.dirty, 'CC marks the level dirty so a render happens')

    # 2. that cell must actually get repainted
    PUSHES.clear()
    tick(menu, lvl)
    want = (lvl.cells[far][1], lvl.cells[far][2])
    check(want in cells_pushed(), 'the externally-changed cell is pushed to the panel')
    check(not lvl.ext, 'its stale mark is cleared once drawn')

    # 3. the header must NOT be repainted when the focused value did not change
    check(not header_pushed(),
          'header band (19.5ms) is SKIPPED when the focused value is unchanged')

    # 4. the header MUST be repainted when the focused value does change
    PUSHES.clear()
    fcc = ps[lvl.idx].cc
    param_values[fcc] = (param_values.get(fcc, 0) + 40) % 128
    menu.note_external_cc(fcc, param_values[fcc])
    tick(menu, lvl)
    check(header_pushed(), 'header IS repainted when the focused value changes')

    # 5. a flood must be bounded per tick, and must drain over subsequent ticks
    for i, p in enumerate(ps):
        param_values[p.cc] = 64
        menu.note_external_cc(p.cc, 64)
    flooded = set(lvl.ext)
    check(len(flooded) > EXT_MAX, 'flood staged %d cells (> cap of %d)' % (len(flooded), EXT_MAX))

    PUSHES.clear()
    tick(menu, lvl)
    n_first = len(cells_pushed())
    check(n_first <= EXT_MAX + 2,
          'first tick pushes at most cap+cursor cells (%d <= %d)' % (n_first, EXT_MAX + 2))
    check(bool(lvl.ext), 'the rest stay queued rather than being dropped')

    ticks = 1
    while lvl.ext and ticks < 50:
        lvl.dirty = True
        tick(menu, lvl)
        ticks += 1
    check(not lvl.ext, 'the queue fully drains (took %d ticks, ~%d ms at 69ms/tick)'
          % (ticks, ticks * 69))

    # 6. every PARAMS row must drive its whole pipeline: map the CC, store the
    # value, send to AMY. This exists because the table calls to_val/update
    # through variables, which arity_check.py cannot see (it only checks bare-name
    # calls) -- a signature typo in a row's functions would otherwise surface as
    # a TypeError swallowed by the render path on the board. Run BEFORE the frame
    # cost check below floods param_values anyway, so state stays comparable.
    pipe_fail = []
    for p in ns['PARAMS']:
        try:
            ns['handle_cc'](p.cc, 64)   # runs the row's to_val, store AND update
        except Exception as e:
            pipe_fail.append('%s: %r' % (p.label, e))
            continue
        if ns['param_values'][p.cc] != 64:
            pipe_fail.append('%s: raw value not recorded' % p.label)
    check(not pipe_fail, 'every PARAMS row maps, stores and sends (%d params)%s'
          % (len(ns['PARAMS']), (': ' + '; '.join(pipe_fail)) if pipe_fail else ''))

    # 7. worst-case frame cost, from the MEASURED per-push figures
    PUSHES.clear()
    lvl.idx = 0
    lvl.prev_idx = 3
    lvl.prev_hdisp = None                      # force the header too
    for p in ps[:4]:
        menu.note_external_cc(p.cc, 64)
    tick(menu, lvl)
    ms = 0.0
    for kind, x0, x1, y0, y1 in PUSHES:
        if kind == 'ROWS':
            ms += 19.5
        elif kind == 'WIN':
            ms += 9.5
    check(ms < 69.0, 'worst-case frame = %.1f ms, inside the ~69 ms loop() tick' % ms)

    print('\n%s' % ('ALL CHECKS PASSED' if not FAILED else 'FAILURES:\n  ' + '\n  '.join(FAILED)))
    return 1 if FAILED else 0


if __name__ == '__main__':
    sys.exit(main())
