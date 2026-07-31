"""Run Arctor's REAL menu code on the host, with the board stubbed out.

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
SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(REPO, 'sketches', 'arctor.py')

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
    ns = {'__name__': 'arctor_sim'}
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


def hover_checks(ns):
    """Hover CC reveal: rest on a cell and its bar alternates with the CC number.

    Timing is driven off the level's own hover clock, so the sim ages that clock
    rather than sleeping -- the phase is a pure function of elapsed time, which is
    exactly what makes that possible.
    """
    print('\n--- Hover CC reveal')
    import time as _t
    REVEAL, CYCLE = ns['HOVER_REVEAL_MS'], ns['HOVER_CYCLE_MS']
    menu = ns['SketchMenu']()
    menu.open()
    lvl = ns['_GridLevel']('VCF')
    menu.stack.append(lvl)
    tick(menu, lvl)                                  # settle: full draw
    cell = lvl.cells[lvl.idx]

    def age(ms):
        """Pretend the cursor has been resting here for `ms`."""
        lvl.hover_at = _t.ticks_ms() - ms

    def frame():
        PUSHES.clear()
        menu._edit_last_render = 0
        lvl.render(menu)                             # NOT tick(): must not force dirty
        return {(p[1], p[3]) for p in PUSHES if p[0] == 'WIN'}

    age(REVEAL - 500)
    check(not frame() and not lvl.hover_shown,
          'below %d ms of dwell nothing happens (a cursor passing through is safe)'
          % REVEAL)

    age(REVEAL + 100)
    pushed = frame()
    check(lvl.hover_shown and (cell[1], cell[2]) in pushed and len(pushed) == 1,
          'at %d ms the CC appears, pushing exactly ONE cell (%d of them)'
          % (REVEAL, len(pushed)))

    check(not frame(), 'and it does not redraw again within the same phase')

    age(REVEAL + CYCLE + 100)
    pushed = frame()                                 # render first -- it is the render
    check(not lvl.hover_shown and (cell[1], cell[2]) in pushed,   # that flips the phase
          'a cycle later the bar is back, in one cell push')
    age(REVEAL + 2 * CYCLE + 100)
    frame()
    check(lvl.hover_shown, 'and it alternates (on again the cycle after)')

    # An input mid-reveal must put the bar back immediately, not at the next phase.
    menu.handle(1, False, False)
    check(not lvl.hover_shown and lvl.dirty,
          'moving the cursor cancels the reveal at once')

    # Selected cells never reveal -- you are turning the value and watching the bar.
    lvl.editing = True
    age(REVEAL + 100)
    frame()
    check(not lvl.hover_shown, 'a SELECTED cell never reveals (it would fight the edit)')
    lvl.editing = False

    # What actually gets drawn: '#<cc>' in place of the bar, for the focused param.
    p = lvl.params[lvl.idx]
    drawn = []
    d = ns['amyboard'].display
    prev_text = d.text
    d.text = lambda s, x, y, c: drawn.append(s)
    age(REVEAL + 100)
    frame()
    d.text = prev_text
    check('#%d' % p.cc in drawn,
          "the cell draws '#%d' (the param's real CC), not a label or a value" % p.cc)


def preset_apply_checks(ns):
    """A preset is a COMPLETE patch: loading one lands on the same sound whatever
    played before it.

    The regression this guards: _apply_preset used to replay only the CCs the
    saved map named, so a preset saved BEFORE a param existed inherited that
    param from whatever was loaded previously. Portamento was the one that showed
    it (heard while scanning on hardware 2026-07-28) -- glide followed you from
    preset to preset until you hit INIT, the only entry naming every CC.
    """
    print('\n--- Preset apply')
    handle_cc = ns['handle_cc']
    apply_preset = ns['_apply_preset']
    PARAMS = ns['PARAMS']
    porta = ns['CC_PORTA_TIME']
    porta_default = ns['PARAM_BY_CC'][porta].default

    # A legacy preset: saved before portamento, so it names every CC except that one.
    legacy = {str(p.cc): 40 for p in PARAMS if p.cc != porta}
    handle_cc(porta, 90)                       # ... and glide is on when we load it
    on = ns['porta_ms']
    apply_preset(legacy)
    check(on > 0 and ns['porta_ms'] == ns['cc_to_porta_ms'](porta_default),
          'a param the preset predates resets to its default, not the last patch '
          '(glide %d ms -> %d ms)' % (on, ns['porta_ms']))
    check(ns['param_values'][PARAMS[0].cc] == 40, 'saved values still land')

    # The half of the old policy that WAS deliberate: a retired param's CC must be
    # ignored rather than raise, so an old snapshot stays loadable.
    try:
        apply_preset({'126': 64, str(PARAMS[0].cc): 7})
        ok = ns['param_values'][PARAMS[0].cc] == 7
    except Exception as e:
        ok = 'raised %r' % e
    check(ok is True, 'an unknown/retired CC in a snapshot is still skipped, not fatal')

    # Completeness, stated directly: a load touches EVERY param, not just the ones
    # the snapshot happened to name.
    seen = []
    ns['handle_cc'] = lambda cc, val: (seen.append(cc), handle_cc(cc, val))[1]
    apply_preset(legacy)
    ns['handle_cc'] = handle_cc
    check(set(seen) == {p.cc for p in PARAMS},
          'a load applies every param (%d of %d), so nothing can leak in from the '
          'previous patch' % (len(set(seen)), len(PARAMS)))


def colour_scale_checks(ns):
    """Colour constants must be LEVELS 0-15, not 0-255 intensities.

    The GS4_HMSB framebuf masks colour with `col & 0x0f`, so a 0-255 value renders
    at `value & 15` and the mask hides the mistake completely -- 255 works by luck
    (255 & 15 == 15) while 110 lands on 14, one step off white. That is what made
    the About card and the menu list read as flat blocks, and nothing caught it:
    the board cannot be eyeballed from a test, and grid_preview.py was masking the
    TOP nibble, so its PNGs showed tones the panel never drew. This check is the
    tripwire that would have.
    """
    print('\n--- Colour scale (0-15 levels)')
    GRID_EXEMPT = ('GRID_C_LABEL', 'GRID_C_BAR_OUT', 'GRID_C_BAR_FILL', 'GRID_C_TICK',
                   'GRID_C_CURSOR', 'GRID_C_KNOCK', 'GRID_C_SECT', 'GRID_C_SECT_RUL',
                   'GRID_C_HDR_NAME', 'GRID_C_HDR_VAL', 'GRID_C_PAGE_OFF')
    bad = [(k, v) for k, v in ns.items()
           if isinstance(v, int) and not isinstance(v, bool)
           and (k.endswith('_COLOR') or '_C_' in k)
           and k not in GRID_EXEMPT and not 0 <= v <= 15]
    check(not bad, 'every colour constant is a 0-15 level (offenders: %s)' % (bad or 'none'))

    # The grid block is knowingly still on the old scale. Assert that it has not
    # QUIETLY been half-converted -- a mix would be worse than either scale.
    grid = [(k, ns[k]) for k in GRID_EXEMPT if k in ns]
    check(all(v > 15 or v in (0, 255) or v == ns['GRID_C_PAGE_OFF'] for _, v in grid),
          'the GRID_C_* block is still uniformly on the 0-255 scale (documented, '
          'not yet re-tuned): %s' % [(k, v, v & 15) for k, v in grid])

    check(ns['ABOUT_C_BRIGHT'] - ns['ABOUT_C_DIM'] >= 4,
          'About bright/dim are >=4 levels apart (%d vs %d) -- the old 15-vs-14 gap '
          'is what read flat' % (ns['ABOUT_C_BRIGHT'], ns['ABOUT_C_DIM']))
    check(ns['MENU_C_SEL'] - ns['MENU_C_UNSEL'] >= 4,
          'menu selected/unselected are >=4 levels apart (%d vs %d)'
          % (ns['MENU_C_SEL'], ns['MENU_C_UNSEL']))
    check(ns['ABOUT_C_DIM'] >= 1 and ns['MENU_C_UNSEL'] >= 1,
          'neither dim level is 0 (level 0 is off; text is legible down to 1, '
          'checked by eye on the panel 2026-07-30)')


def flash_store_checks(ns):
    """The two flash stores load, and degrade instead of failing a boot.

    A malformed or missing store must never stop the synth coming up -- a board
    that boots silent because one JSON went bad is far worse than one that boots
    with no presets. Driven by swapping _read_json for a fake filesystem, so the
    real logic runs.

    This replaces an earlier pair of legacy-fallback checks. Up to 2026-07-30 the
    sketch also read the pre-rename /user/polysynth_*.json when the current file
    was absent, so a board running the old build kept its patch library across the
    rename. That bridge was removed once the board's files were copied to the new
    names; a board that never made the crossing will come up with no presets, its
    old file still on flash under the old name.
    """
    print('\n--- Flash stores')
    real_read = ns['_read_json']
    P, S = ns['PRESETS_FILE'], ns['SETTINGS_FILE']

    check('arctor' in P and 'arctor' in S and 'polysynth' not in P + S,
          'both stores are on the arctor names (%s, %s)' % (P, S))
    check(not any(k.startswith('_LEGACY') for k in ns),
          'no legacy-path constants remain in the sketch')

    def fake(fs):
        ns['_read_json'] = lambda path: fs.get(path)

    fake({P: [{'name': 'bass', 'cc': {'74': 11}}, {'name': 'pad', 'cc': {'74': 99}}]})
    check([p['name'] for p in ns['_load_presets']()] == ['bass', 'pad'],
          'saved presets load')

    fake({})
    check(ns['_load_presets']() == [] and ns['_load_settings']() == {},
          'a fresh board loads empty, not a fault')

    fake({P: {'not': 'a list'}, S: ['not', 'a dict']})
    check(ns['_load_presets']() == [] and ns['_load_settings']() == {},
          'a malformed store degrades to empty rather than crashing boot')

    fake({P: [{'name': 'ok', 'cc': {'74': 1}}, {'bad': 'record'}, {'name': 5, 'cc': {}}]})
    check([p['name'] for p in ns['_load_presets']()] == ['ok'],
          'one bad record does not take the whole list down')

    fake({S: {'display_mode': 'Screensaver', 'current_preset': 'agne'}})
    check(ns['_load_settings']() == {'display_mode': 'Screensaver',
                                     'current_preset': 'agne'},
          'settings load (display mode + current preset survive a reboot)')

    ns['_read_json'] = real_read


def about_checks(ns):
    """About: the static credits card.

    Two things can silently break it and neither is visible from reading the
    code. (1) The TEXT budget -- the card is hand-wrapped to 16 chars and packed
    to 126 of 128 pixel rows, so an edit to the wording or to VERSION overflows
    the panel or clips a line, and the board just draws it wrong. (2) The root
    menu is now at exactly MENU_VISIBLE items, so adding one more paginates the
    main menu (About would land alone on page 2). Both are caught here.
    """
    print('\n--- About')
    SketchMenu = ns['SketchMenu']
    AboutLevel = ns['_AboutLevel']

    menu = SketchMenu()
    menu.open()
    labels = [lbl for lbl, _ in menu.cur.items]
    check(labels.index('About') == labels.index('Resume playing') - 1,
          'About sits directly above Resume playing on the root')

    dict(menu.cur.items)['About']()             # open it the way a click does
    check(isinstance(menu.cur, AboutLevel), 'clicking About pushes the card')

    lines = ns['_about_lines']()
    over = [t for t, _ in lines if len(t) > ns['ABOUT_MAX_CH']]
    check(not over, 'every line fits the %d-char panel width (else clipped): %r'
          % (ns['ABOUT_MAX_CH'], over))
    ext = ns['_about_extent']()
    check(ext <= 128, 'the card fits the panel vertically (%d of 128 px)' % ext)

    # The URL is split across rows purely to fit 16 chars; the rows joined back
    # together must still be the real link. A wrap that drops or duplicates a
    # character produces a card that looks fine and sends people to a 404.
    URL = 'github.com/sawtoothwave/amyboard/blob/main/arctor.md'
    texts = [t for t, _ in lines]
    start = texts.index('github.com/')
    joined = ''.join(texts[start:])
    check(joined == URL, 'the wrapped URL rows rejoin to the real link (got %r)' % joined)
    check('/blob/main/' in joined,
          'it is the /blob/main/ form, which resolves in a browser as typed')

    PUSHES.clear()
    menu.dirty = True
    menu.cur.render(menu)
    check(('FLUSH', 0, 127, 0, 127) in PUSHES,
          'it paints as one full-screen flush (it owns the panel while up)')
    check(menu._panel_dirty_to == 128,
          'and declares the full screen dirty, so the NEXT level clears all of it')

    # EVERY gesture dismisses -- the one level that breaks turn=scroll/click=in/
    # hold=out, because there is nothing to scroll or drill into here.
    for label, (delta, click, back) in (('a turn', (3, False, False)),
                                        ('a reverse turn', (-1, False, False)),
                                        ('a click', (0, True, False)),
                                        ('a hold', (0, False, True))):
        if not isinstance(menu.cur, AboutLevel):
            dict(menu.cur.items)['About']()
        menu.handle(delta, click, back)
        check(menu.is_open and not isinstance(menu.cur, AboutLevel),
              '%s dismisses the card back to the root' % label)


def scan_checks(ns):
    """Scan Presets: the level that loads as you scroll.

    Same reason as the CC checks above -- the behaviour under test is a state
    machine (which preset is applied on which turn, and WHEN the settings write
    happens), and the board gives no way to observe either while a sketch runs.
    Driven through menu.handle() so the real root-menu wiring and dispatch are
    exercised, not just the level in isolation.
    """
    print('\n--- Scan Presets')
    SketchMenu = ns['SketchMenu']
    ScanLevel = ns['_ScanLevel']
    PARAMS = ns['PARAMS']

    # Two saved presets, each pinning the first three params to its own values, so
    # "which preset is loaded" is readable straight off param_values.
    ccs = [p.cc for p in PARAMS[:3]]
    ns['_presets'][:] = [{'name': 'bass', 'cc': {str(c): 11 for c in ccs}},
                         {'name': 'arp', 'cc': {str(c): 99 for c in ccs}}]
    ns['_current_preset_name'] = ''

    applied = []                      # every preset actually replayed
    real_apply = ns['_apply_preset']
    ns['_apply_preset'] = lambda cc_map: (applied.append(cc_map), real_apply(cc_map))[1]
    writes = []                       # every settings write (flash I/O on the board)
    ns['_set_setting'] = lambda k, v: writes.append((k, v))

    menu = SketchMenu()
    menu.open()
    root = menu.cur
    check(len(root.items) <= ns['MENU_VISIBLE'],
          'root menu still fits one page (%d of %d rows, no pagination)'
          % (len(root.items), ns['MENU_VISIBLE']))
    labels = [lbl for lbl, _ in root.items]
    check(labels.index('Scan presets') == labels.index('Load preset') + 1,
          'Scan presets sits directly after Load preset on the root')

    dict(root.items)['Scan presets']()          # open it the way a click does
    lvl = menu.cur
    n = len(lvl.entries)
    check(isinstance(lvl, ScanLevel) and n == 3,
          'opens a scan level over INIT + both presets (%d entries)' % n)
    check(not applied, 'opening loads NOTHING (the patch you were playing stands)')

    menu.handle(1, False, False)                # one detent
    check(len(applied) == 1 and lvl.idx == 1,
          'a turn loads the preset it lands on, with no click')
    check(not writes, 'no settings write per step (flash stays out of the audio path)')

    menu.handle(-1, False, False)               # back to the top of the list...
    menu.handle(-1, False, False)               # ...and past it
    check(lvl.idx == n - 1, 'counter-clockwise past the first wraps to the last')
    menu.handle(1, False, False)
    check(lvl.idx == 0, 'clockwise past the last wraps back to the first')

    applied.clear()
    menu.handle(5, False, False)                # a fast spin arrives as one summed delta
    check(lvl.idx == 5 % n and len(applied) == 1,
          'a fast spin applies only the preset landed on, not the ones skimmed past')

    tick(menu, lvl)                             # the list still draws (it is a _MenuLevel)
    check(any(p[0] in ('FLUSH', 'ROWS') for p in PUSHES), 'the scan list renders')

    # A click opens Param Control ON the preset you landed on -- and what the grid
    # shows must be LIVE state, so a MIDI CC that arrived while this preset was up
    # is already reflected there rather than the saved snapshot's value.
    saved_cc = ccs[0]
    landed = lvl.entries[lvl.idx].get('name')
    saved_val = int(lvl.entries[lvl.idx]['cc'][str(saved_cc)])
    ns['midi_cb']([0xB0 | (ns['SYNTH'] - 1), saved_cc, 77])   # a knob turn mid-scan
    menu.handle(0, True, False)                 # click
    check(menu.is_open and menu.cur.title == 'PARAM CONTROL',
          'a click opens Param Control on the scanned preset')
    check(not any(isinstance(l, ScanLevel) for l in menu.stack)
          and menu.stack[0].title == 'ARCTOR' and len(menu.stack) == 2,
          'it REPLACES the scan level, sitting directly on the root (no trapdoor '
          'back into scanning on a hold)')
    check(writes == [('current_preset', landed)],
          'the click ended the scan, so the pointer is written once, here')

    grp = ns['PARAM_BY_CC'][saved_cc].group
    dict(menu.cur.items)[grp]()                 # drill into that param's grid
    grid = menu.cur
    shown = ns['param_values'][saved_cc]
    check(saved_cc in [p.cc for p in grid.params] and shown == 77 != saved_val,
          'the grid shows the LIVE CC-informed value (%d), not the preset\'s saved '
          'one (%d)' % (shown, saved_val))

    menu.handle(0, False, True)                 # hold: out of the grid...
    menu.handle(0, False, True)                 # ...out of Param Control
    check(menu.cur.title == 'ARCTOR',
          'a hold out of Param Control lands on the root, exactly as it does when '
          'you enter it the normal way')
    check(writes == [('current_preset', landed)],
          'still exactly ONE settings write across the whole scan')

    # ... and the other way out: a hold pops back to the root and persists the same.
    writes.clear()
    menu.open()
    dict(menu.cur.items)['Scan presets']()
    lvl = menu.cur
    menu.handle(1, False, False)
    landed = lvl.entries[lvl.idx].get('name')
    menu.handle(0, False, True)                 # hold
    check(menu.is_open and menu.cur is not lvl,
          'a hold leaves scan mode back at the root menu (not to playing)')
    check(writes == [('current_preset', landed)], 'the hold exit persists once too')
    check(ns['_current_preset_name'] == landed,
          'the scanned preset becomes the session current (Save->Overwrite target)')


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

    hover_checks(ns)
    preset_apply_checks(ns)
    scan_checks(ns)
    colour_scale_checks(ns)
    flash_store_checks(ns)
    about_checks(ns)

    print('\n%s' % ('ALL CHECKS PASSED' if not FAILED else 'FAILURES:\n  ' + '\n  '.join(FAILED)))
    return 1 if FAILED else 0


if __name__ == '__main__':
    sys.exit(main())
