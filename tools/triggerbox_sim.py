"""Run the triggerbox's REAL menu code on the host, with the board stubbed out.

Sibling of `grid_sim.py` (same trick: stub `amy` / `amyboard`, exec the sketch,
drive the actual SketchMenu). It exists for the same reason: the editor's gesture
state machine -- deferred single-click vs. double-click, hold-to-revert, and where
the ONE flash write happens -- is pure timing-dependent state, and the board has no
REPL while a sketch runs, so there is no way to inspect it there. A deploy can only
tell you "that felt wrong".

What it proves: which gesture lands which value, when the commit fires, and that a
commit is never silently dropped. What it does NOT prove: anything about audio, real
encoder feel, or panel timing -- see tools/README.md.

Usage:  python3 tools/triggerbox_sim.py [path/to/sketch.py]
Exits non-zero on failure, so it can gate a deploy.
"""
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(REPO, 'sketches', 'triggerbox.py')

NOW = [10000]            # simulated ticks_ms; tests advance it explicitly


class _FakeHW:
    width = 128
    col_addr = (0, 63)
    row_addr = (0, 127)

    def __init__(self):
        self.buffer = bytearray(128 * 128 // 2)

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
    # MicroPython's monotonic tick API, which CPython's `time` lacks. Time is under
    # the TEST's control here (not the wall clock) because the whole point is to
    # step across the double-click window deterministically.
    import time as _t
    _t.ticks_ms = lambda: NOW[0]
    _t.ticks_us = lambda: NOW[0] * 1000
    _t.ticks_diff = lambda a, b: a - b
    _t.ticks_add = lambda a, b: a + b
    return _t


def _install_stubs():
    _install_micropython_time()
    import gc as _gc
    if not hasattr(_gc, 'mem_free'):
        _gc.mem_free = lambda: 4 * 1024 * 1024      # MicroPython-only

    amy = types.ModuleType('amy')
    amy.send = lambda **kw: None
    amy.message = lambda **kw: ''
    amy.millis = lambda: NOW[0]
    for n in ('reset', 'volume', 'stop', 'start', 'live', 'pause'):
        setattr(amy, n, lambda *a, **k: None)
    amy.load_sample_bytes = lambda *a, **k: None

    amyboard = types.ModuleType('amyboard')
    amyboard.display = _FakeDisplay()
    amyboard.display_refresh = lambda *a, **k: None
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
    ns = {'__name__': 'triggerbox_sim'}
    exec(compile(open(path).read(), path, 'exec'), ns)
    # Never let the sim write the developer's real settings file: the sketch's
    # settings path is a board path, but be explicit rather than lucky.
    ns['_writes'] = []
    ns['_set_setting'] = lambda k, v: ns['_writes'].append(k)
    # Keep the REAL flush machinery (it is under test) and record what it pushes.
    ns['_pushed'] = []
    ns['_push_rows'] = lambda y0, y1: (ns['_pushed'].append((y0, y1)) or True)
    return ns


def drain(ns, ticks=40):
    """Run _service_flush like loop() would, until the panel is settled."""
    for _ in range(ticks):
        if not ns['_service_flush']():
            return True
    return False


FAILED = []


def check(cond, what):
    print('%-4s %s' % ('ok' if cond else 'FAIL', what))
    if not cond:
        FAILED.append(what)


def _open_editor(ns, slot=0, row_key='level'):
    """Menu opened at the SLOTS -> editor level, cursor parked on `row_key`."""
    menu = ns['SketchMenu']()
    menu.open()
    ed = ns['_SlotEditor'](slot)
    menu.stack.append(ed)
    ed.idx = [i for i, r in enumerate(ed.rows)
              if r[0] == 'param' and r[1] == row_key][0]
    return menu, ed


def main():
    ns = load()
    accel = ns['_accel']
    DBL = ns['EDIT_DBLCLICK_MS']
    slot_params = ns['slot_params']
    PARAM_BY_KEY = ns['PARAM_BY_KEY']

    print('\n--- encoder acceleration')
    check(accel(1) == 1 and accel(-1) == -1, 'one detent stays 1:1 (fine adjustment)')
    check(accel(3) == 9 and accel(-3) == -9, 'a 3-detent tick scales quadratically')
    check(accel(50) == 50 * ns['ENC_ACCEL_CAP'], 'a very fast spin is capped, not unbounded')

    print('\n--- which lists accelerate')
    menu = ns['SketchMenu']()
    menu.open()
    check(menu.cur.accel is False, 'the root menu scrolls 1:1')
    check(menu._build_slots_level().accel is False, 'the 12-row SLOTS list scrolls 1:1')
    menu._browse_slot = 0
    menu._open_dir(ns['sample_root'](), first=True)
    check(menu.cur.accel is True, 'the sample browser (long list) accelerates')

    print('\n--- browser scroll actually accelerates')
    lvl = ns['_MenuLevel']('LONG', [('f%d' % i, None) for i in range(200)], accel=True)
    lvl.handle(menu, 4, False, False)
    check(lvl.idx == 16, 'a 4-detent tick moves 16 rows on an accelerating list')
    slow = ns['_MenuLevel']('SHORT', [('f%d' % i, None) for i in range(200)])
    slow.handle(menu, 4, False, False)
    check(slow.idx == 4, 'the same tick moves 4 rows on a 1:1 list')
    lvl.idx = 199
    lvl.handle(menu, 9, False, False)
    check(lvl.idx == 199, 'acceleration still clamps at the end of the list')

    print('\n--- panel flush: the cursor is never left off-screen')
    H = ns['MENU_LINE_H']
    big = ns['_MenuLevel']('LONG', [('f%d' % i, None) for i in range(200)], accel=True)
    menu = ns['SketchMenu']()
    menu.open()
    menu.stack.append(big)
    menu.dirty = True
    big.render(menu)                                    # first paint (full)
    ns['_pushed'].clear()
    ticks = 0
    while ns['_service_flush']():
        ticks += 1
    check(ns['_pushed'][0][0] <= big._row_y(big.idx) <= ns['_pushed'][0][1],
          'a full repaint pushes the CURSOR band first')
    check(ticks <= 5, 'a full repaint settles in %d ticks (~%dms), not 11' % (ticks, ticks * 69))

    # An accelerated jump within one page: the old code flushed ONE band spanning
    # every row between the two, so the cursor was invisible for most of the sweep.
    big.idx = 0
    menu.dirty = True
    big.render(menu)
    drain(ns)
    ns['_pushed'].clear()
    big.handle(menu, 3, False, False)                   # 3 detents -> 9 rows
    check(big.idx == 9 and big.idx // ns['MENU_VISIBLE'] == 1, 'the jump crosses a page')
    big.idx = 6                                         # same page as row 0, 6 rows away
    big._shown_idx = 0
    menu.dirty = True
    big.render(menu)
    ns['_service_flush']()
    first = ns['_pushed'][0]
    y_new = big._row_y(6 - big.start)
    check(first == (y_new, y_new + H - 1),
          'a 6-row jump pushes the LANDED-ON row first, in one 12px band')
    drain(ns)
    check(len(ns['_pushed']) == 2,
          'and pushes exactly 2 bands (old + new row), not the span between them')

    print('\n--- backing out of a submenu repaints the whole screen')
    menu = ns['SketchMenu']()
    menu.open()
    menu._open_slots()
    under = menu.cur
    menu.dirty = True
    under.render(menu)                                  # SLOTS has painted itself
    check(under._shown_idx >= 0, 'the SLOTS list has painted state to reuse')
    menu._browse_slot = 0
    menu._push_level(ns['_SlotEditor'](0))              # a level draws over it
    menu._pop()
    check(under._shown_idx < 0 and under._shown_start < 0,
          'popping back to it forces a FULL repaint (no leftovers underneath)')
    menu._push_level(ns['_MenuLevel']('BROWSER', [('a', None)], accel=True))
    ed = ns['_SlotEditor'](0)
    menu.stack.insert(-1, ed)                           # editor sits under the browser
    ed.full = False
    ed._shown_idx = 0
    menu._pop()
    check(ed.full and ed._shown_idx < 0,
          'the slot editor also fully repaints when the browser closes')

    print('\n--- editor: single click commits (deferred past the double-click window)')
    menu, ed = _open_editor(ns)
    slot_params[0]['level'] = 1.0
    ed.handle(menu, 0, True, False)                     # click the row -> edit
    check(ed.editing and ed.entry_value == 1.0, 'click enters edit and snapshots the value')
    ed.handle(menu, 2, False, False)                    # turn: 2 detents -> 4 steps
    check(slot_params[0]['level'] == 1.2, 'a 2-detent turn applies accel to the value step')
    ns['_writes'].clear()
    ed.handle(menu, 0, True, False)                     # first click: deferred
    check(ed.editing and menu._click_pending_at, 'the click is HELD, not acted on yet')
    NOW[0] += DBL // 2
    menu.service_pending(NOW[0])
    check(ed.editing, 'still editing inside the double-click window')
    NOW[0] += DBL
    menu.service_pending(NOW[0])
    check(not ed.editing, 'window passed: the single click committed and exited')
    check(slot_params[0]['level'] == 1.2, 'the edited value survives the commit')
    check(ns['_writes'] == ['params'], 'exactly ONE flash write, at the commit')

    print('\n--- editor: double click resets to the default and keeps editing')
    menu, ed = _open_editor(ns)
    slot_params[0]['level'] = 1.9
    ed.handle(menu, 0, True, False)
    ns['_writes'].clear()
    ed.handle(menu, 0, True, False)                     # 1st click (pending)
    NOW[0] += DBL - 50
    ed.handle(menu, 0, True, False)                     # 2nd click inside the window
    check(slot_params[0]['level'] == PARAM_BY_KEY['level'][2],
          'double click restores the spec default')
    check(ed.editing, 'and stays in edit mode')
    check(not menu._click_pending_at, 'the pending single click was consumed')
    NOW[0] += DBL * 2
    menu.service_pending(NOW[0])
    check(ed.editing, 'a consumed double click does not later fire a stale commit')
    check(ns['_writes'] == [], 'a reset alone writes nothing to flash')

    print('\n--- editor: hold reverts to the entry value WITHOUT saving')
    menu, ed = _open_editor(ns, row_key='pan')
    slot_params[0]['pan'] = 0
    ed.handle(menu, 0, True, False)
    ed.handle(menu, 3, False, False)
    check(slot_params[0]['pan'] != 0, 'the turn moved pan live')
    ns['_writes'].clear()
    ed.handle(menu, 0, False, True)                     # hold
    check(slot_params[0]['pan'] == 0, 'hold puts the pre-edit value back')
    check(not ed.editing, 'and leaves edit mode')
    check(ns['_writes'] == [], 'a reverted edit never reaches flash')

    print('\n--- editor: a turn cancels a pending click (keeps editing)')
    menu, ed = _open_editor(ns)
    ed.handle(menu, 0, True, False)
    ed.handle(menu, 0, True, False)                     # pending commit
    ed.handle(menu, 1, False, False)                    # turn instead of a 2nd click
    check(not menu._click_pending_at, 'the pending click was dropped')
    NOW[0] += DBL * 2
    menu.service_pending(NOW[0])
    check(ed.editing, 'so the turn kept us editing rather than exiting')

    print('\n--- a pending commit is never dropped on suspend/close')
    for name in ('suspend', 'close'):
        menu, ed = _open_editor(ns)
        ed.handle(menu, 0, True, False)
        ed.handle(menu, 5, False, False)
        want = slot_params[0]['level']
        ed.handle(menu, 0, True, False)                 # pending
        ns['_writes'].clear()
        getattr(menu, name)()
        check(ns['_writes'] == ['params'],
              'an in-flight commit is flushed to flash on %s()' % name)
        check(slot_params[0]['level'] == want, '  ...keeping the edited value (%s)' % name)

    print('\n--- hold on the CURSOR (not editing) still pops the level')
    menu, ed = _open_editor(ns)
    depth = len(menu.stack)
    ed.handle(menu, 0, False, True)
    check(len(menu.stack) == depth - 1, 'hold leaves the editor when not editing')

    print()
    if FAILED:
        print('%d FAILED:' % len(FAILED))
        for f in FAILED:
            print('  -', f)
        return 1
    print('all checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
