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
import json
import os
import shutil
import struct
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(REPO, 'sketches', 'triggerbox.py')

NOW = [10000]            # simulated ticks_ms; tests advance it explicitly
PUSHES = []              # one entry per whole-frame panel push


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
    amy.unload_sample = lambda *a, **k: None
    amy.PCM = 10                                    # AMY's PCM wave constant

    amyboard = types.ModuleType('amyboard')
    amyboard.display = _FakeDisplay()
    amyboard.display_refresh = lambda *a, **k: PUSHES.append('refresh')
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
    # Stub the FILE WRITE, not _set_setting. Settings writes are now DEFERRED until
    # the pads are quiet (a flash write blocks ~150ms and made notes late), and
    # that deferral is under test -- stubbing _set_setting would bypass it. This
    # also keeps the sim away from the developer's real files.
    ns['_writes'] = []
    ns['_write_settings'] = lambda: ns['_writes'].append('write')
    # Record the row WINDOWS pushed to the panel. A full frame is 8KB over a 400kHz
    # bus (~190ms of the board not calling us), so what matters now is how few rows
    # go out per change -- not how few calls.
    ns['_push_rows'] = lambda y0, y1: (PUSHES.append((y0, y1)) or True)
    return ns


def settle(ns, menu=None):
    """Let deferred housekeeping run: advance past the quiet window and service it."""
    NOW[0] += ns['NOTE_QUIET_MS'] + 1
    return ns['service_settings']()


def _write_wav(path, secs=0.5, rate=16000, bits=16, channels=1):
    """A real WAV on disk, so list_dir/_wav_info run for true instead of stubbed."""
    frames = int(rate * secs)
    data = frames * channels * (bits // 8)
    with open(path, 'wb') as f:
        f.write(b'RIFF' + struct.pack('<I', 36 + data) + b'WAVE')
        f.write(b'fmt ' + struct.pack('<IHHIIHH', 16, 1, channels, rate,
                                      rate * channels * (bits // 8),
                                      channels * (bits // 8), bits))
        f.write(b'data' + struct.pack('<I', data) + b'\0' * data)


def _write_wav_with_junk(path, junk_bytes, secs=0.3, rate=16000):
    """A valid WAV with a big LIST chunk before `data` -- the case that pushes the
    data header past _wav_info's single 512-byte read and onto the slow path."""
    frames = int(rate * secs)
    data = frames * 2
    junk = b'\0' * junk_bytes
    with open(path, 'wb') as f:
        f.write(b'RIFF' + struct.pack('<I', 36 + len(junk) + 8 + data) + b'WAVE')
        f.write(b'fmt ' + struct.pack('<IHHIIHH', 16, 1, 1, rate, rate * 2, 2, 16))
        f.write(b'LIST' + struct.pack('<I', len(junk)) + junk)
        f.write(b'data' + struct.pack('<I', data) + b'\0' * data)


def make_sample_dir():
    """A folder of WAVs: two good, one 8-bit (rejected), plus a subfolder."""
    d = tempfile.mkdtemp(prefix='tbx-sim-')
    _write_wav(os.path.join(d, 'kick.wav'), secs=0.25)
    _write_wav(os.path.join(d, 'snare.wav'), secs=0.50)
    _write_wav(os.path.join(d, 'broken.wav'), secs=0.10, bits=8)
    os.mkdir(os.path.join(d, 'sub'))
    return d


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
    check(menu._build_slots_level().accel is False,
          'the %d-row slot list scrolls 1:1' % ns['NUM_SLOTS'])
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

    print('\n--- wav headers: one read, with a walk for awkward files')
    hdr_dir = tempfile.mkdtemp(prefix='tbx-sim-hdr-')
    plain = os.path.join(hdr_dir, 'plain.wav')
    padded = os.path.join(hdr_dir, 'padded.wav')
    _write_wav(plain, secs=0.25, rate=16000)
    _write_wav_with_junk(padded, junk_bytes=900, secs=0.30, rate=16000)
    reads = []
    real_open = ns['open'] if 'open' in ns else open
    ns['open'] = lambda p, m='r': (reads.append(p), real_open(p, m))[1]
    info = ns['_wav_info'](plain)
    check(info == (1, 16000, 16, 4000), 'a normal header parses: %r' % (info,))
    check(len(reads) == 1, 'and costs exactly ONE file open (%d)' % len(reads))
    reads.clear()
    info = ns['_wav_info'](padded)
    check(info == (1, 16000, 16, 4800),
          'a 900-byte LIST chunk before data still parses: %r' % (info,))
    check(len(reads) == 2, 'via the slow walk, not by calling it BAD')
    check(ns['sample_problem'](info) is None, 'so the row stays clickable')
    ns['open'] = real_open

    print('\n--- a rejected header says WHY, and a dropout is not held against it')
    notwav = os.path.join(hdr_dir, 'notes.wav')
    with open(notwav, 'wb') as f:
        f.write(b'this is not a wav file at all')
    check(ns['sample_problem'](ns['_wav_info'](notwav)) == ns['WAV_NOWAV'],
          "a non-WAV reads '!NOWAV', not a blanket '!BAD'")
    adpcm = os.path.join(hdr_dir, 'adpcm.wav')
    with open(adpcm, 'wb') as f:                  # valid RIFF, unusable fmt tag
        f.write(b'RIFF' + struct.pack('<I', 36) + b'WAVE')
        f.write(b'fmt ' + struct.pack('<IHHIIHH', 16, 0x11, 1, 16000,
                                      16000, 2, 16))
        f.write(b'data' + struct.pack('<I', 0))
    check(ns['sample_problem'](ns['_wav_info'](adpcm)) == ns['WAV_FMT'],
          "a non-PCM fmt reads '!FMT'")
    # A card dropout must NOT be remembered: the file is probably fine, and the
    # browser has to be willing to look again. This is the failure mode that would
    # otherwise leave good samples permanently marked bad for the whole session.
    ns['_info_cache'].clear()
    boom = os.path.join(hdr_dir, 'flaky.wav')
    _write_wav(boom, secs=0.2)
    fail_next = [True]

    down = [True]

    def _flaky_open(p, m='r'):
        if p == boom and down[0]:
            raise OSError(5, 'EIO')
        return real_open(p, m)

    ns['open'] = _flaky_open
    check(ns['sample_problem'](ns['_wav_info'](boom)) == ns['WAV_EIO'],
          "a read that keeps throwing reads '!EIO'")
    check(boom not in ns['_info_cache'], 'and the dropout is NOT cached')
    down[0] = False
    check(ns['sample_problem'](ns['_wav_info'](boom)) is None,
          'so the very next look reads the file fine')
    # A ONE-OFF hiccup should never reach the screen at all. The browser reading
    # once while the loader retried is what made a card dropout look like "loading
    # a sample breaks browsing".
    ns['_info_cache'].clear()
    once = [True]

    def _hiccup_hdr(p, m='r'):
        if p == boom and once[0]:
            once[0] = False
            raise OSError(5, 'EIO')
        return real_open(p, m)

    ns['open'] = _hiccup_hdr
    check(ns['sample_problem'](ns['_wav_info'](boom)) is None,
          'a single dropout never reaches the row: the header read retries')

    # THE post-load EIO fix. On the board, loading a sample drops the card off the
    # bus: listdir('/sd') returns [] and every deeper path raises EIO until
    # mount_sd() is called. The browser's header read was the one card access with
    # no re-mount behind it, so it was the only one that could never recover -- and
    # because list_dir is cached (and the boot prescan pre-caches every folder) the
    # listing still looked healthy while every row under it read EIO.
    ns['_info_cache'].clear()
    revived = []
    down_until_mount = [True]

    def _dead_until_mount(p, m='r'):
        if p == boom and down_until_mount[0]:
            raise OSError(5, 'EIO')
        return real_open(p, m)

    def _mount_fixes_it(*a, **k):
        revived.append(1)
        down_until_mount[0] = False        # what mount_sd() really does, measured

    ns['amyboard'].mount_sd = _mount_fixes_it
    ns['open'] = _dead_until_mount
    check(ns['sample_problem'](ns['_wav_info'](boom)) is None,
          'a card that is DOWN until re-mounted still yields a good row')
    check(len(revived) >= 1, '  ...because the header read re-mounted it')

    print('\n--- reads escalate to a re-mount only when plain retries fail')
    # Both directions matter and they pull opposite ways. Remounting on the FIRST
    # error breaks every single-shot read that follows (a load that hiccups once
    # still reports success, so the damage is invisible). NEVER remounting means a
    # genuinely sick mount can't recover and the load just fails.
    mounts = []
    ns['amyboard'].mount_sd = lambda *a, **k: mounts.append(1)
    flaky2 = os.path.join(hdr_dir, 'flaky2.wav')
    _write_wav(flaky2, secs=0.2)
    tries = [0]

    def _hiccup_open(p, m='r'):
        if p == flaky2:
            tries[0] += 1
            if tries[0] == 1:
                raise OSError(5, 'EIO')
        return real_open(p, m)

    ns['open'] = _hiccup_open
    check(ns['robust_read'](flaky2) is not None,
          'a one-off dropout still reads, on a plain retry')
    check(mounts == [], 'and does NOT remount a card that is merely noisy')

    mounts.clear()
    dead = [True]

    def _dead_open(p, m='r'):
        if p == flaky2 and dead[0]:
            raise OSError(5, 'EIO')
        return real_open(p, m)

    ns['open'] = _dead_open
    check(ns['robust_read'](flaky2) is None,
          'a card that never answers gives up rather than hanging')
    check(len(mounts) >= 1, 'but not before escalating to a re-mount')
    check(len(mounts) < ns['READ_TRIES'],
          'and not on every single attempt (%d re-mounts of %d tries)'
          % (len(mounts), ns['READ_TRIES']))
    ns['open'] = real_open
    shutil.rmtree(hdr_dir, ignore_errors=True)

    print('\n--- the sample root is resolved ONCE, not on every browse')
    probes = []
    real_is_dir = ns['_is_dir']
    ns['_is_dir'] = lambda p: (probes.append(p), real_is_dir(p))[1]
    ns['forget_sample_root']()
    ns['sample_root']()
    first = len(probes)
    for _ in range(5):
        ns['sample_root']()
    check(first > 0 and len(probes) == first,
          'five more browses cost ZERO extra filesystem probes (%d then %d)'
          % (first, len(probes)))
    ns['forget_sample_root']()
    ns['sample_root']()
    check(len(probes) > first, 'and forgetting the cache makes it probe again')
    ns['_is_dir'] = real_is_dir

    print('\n--- browser: opening a folder reads NO wav headers')
    sample_dir = make_sample_dir()
    reads = []
    real_wav_info = ns['_wav_info']
    ns['_wav_info'] = lambda p: (reads.append(p), real_wav_info(p))[1]
    menu = ns['SketchMenu']()
    menu.open()
    menu._browse_slot = 0
    menu._open_dir(sample_dir, first=True)
    lvl = menu.cur
    labels = [it[0] for it in lvl.items]
    check(reads == [], 'opening the folder did %d header reads (want 0)' % len(reads))
    check(labels[0] == '/sub', 'the subfolder is listed first, from the directory entry alone')
    check('kick.wav' in labels and 'snare.wav' in labels,
          'every wav is on screen immediately, by name')
    check(not any(l.endswith('s') and l[0].isalpha() for l in labels),
          'no row claims a duration yet -- nothing has been read')
    check(all(it[1] is not None for it in lvl.items[1:]),
          'un-hydrated rows are still clickable (load_slot re-validates)')

    print('\n--- browser: headers fill in ONE per tick')
    n = len(lvl.items) - 1                              # every row but the folder
    for i in range(n):
        before = len(reads)
        did = menu.service_hydrate()
        check(did and len(reads) == before + 1,
              'tick %d reads exactly one header' % (i + 1))
    check(menu.service_hydrate() is False, 'and stops once every row is hydrated')
    labels = [it[0] for it in lvl.items]
    kick = [l for l in labels if l.startswith('kick')][0]
    broken = [l for l in labels if 'broken' in l][0]
    check(kick == 'kick.wav 0.25s', 'a good row gained its duration: %r' % kick)
    check(broken.startswith('!8BIT'), 'the 8-bit file is marked and rejected: %r' % broken)
    check([it[1] for it in lvl.items if 'broken' in it[0]][0] is None,
          'and the rejected row is no longer clickable')

    print('\n--- the card is left alone while the pads are being played')
    menu2 = ns['SketchMenu']()
    menu2.open()
    menu2._browse_slot = 0
    ns['_dir_cache'].clear()
    ns['_info_cache'].clear()
    menu2._open_dir(sample_dir, first=True)
    ns['note_activity_cb']((0x90, 38, 100))             # a pad was just hit
    check(ns['pads_quiet']() is False, 'a note-on marks the pads as active')
    check(menu2.service_hydrate() is False,
          'no header is read while notes are playing (this was 554ms of late notes)')
    NOW[0] += ns['NOTE_QUIET_MS'] + 1
    check(ns['pads_quiet']() is True, 'and quiet again after NOTE_QUIET_MS')
    check(menu2.service_hydrate() is True, 'so hydration resumes in the gap')

    print('\n--- flash writes and folder scans wait for a gap in the playing')
    ns['_settings_dirty'] = False
    ns['_writes'].clear()
    ns['_set_setting']('base_note', 48)
    ns['note_activity_cb']((0x90, 38, 100))             # mid-pattern
    check(ns['service_settings']() is False and ns['_writes'] == [],
          'a settings change does NOT write flash while you are playing')
    NOW[0] += ns['NOTE_QUIET_MS'] + 1
    check(ns['service_settings']() is True, 'and lands in the next gap')
    ns['_set_setting']('base_note', 36)
    ns['note_activity_cb']((0x90, 38, 100))
    ns['flush_settings']()
    check(ns['_settings_dirty'] is False,
          'flush_settings forces it out for a blocking moment (a load)')

    ns['_dir_cache'].clear()
    ns['sample_root'] = lambda: sample_dir
    ns['start_prescan']()
    ns['note_activity_cb']((0x90, 38, 100))
    scanned = 0
    for _ in range(6):
        if ns['service_prescan']():
            scanned += 1
    check(scanned >= 2 and sample_dir in ns['_dir_cache'],
          'the prescan walks the tree into cache ahead of you (%d folders)' % scanned)
    check(os.path.join(sample_dir, 'sub') in ns['_dir_cache'],
          'including subfolders, so the first visit is free too')

    print('\n--- revisiting a folder costs no filesystem access')
    listed = []
    real_ilistdir = os.ilistdir if hasattr(os, 'ilistdir') else None
    real_listdir = os.listdir
    os.listdir = lambda p: (listed.append(p), real_listdir(p))[1]
    ns['list_dir'](sample_dir)                          # already cached from above
    check(listed == [], 'a cached folder does not hit the filesystem at all')
    ns['_dir_cache'].clear()
    ns['list_dir'](sample_dir)
    check(len(listed) == 1, 'and reads it again once the cache is cleared')
    os.listdir = real_listdir

    print('\n--- a header is never read twice')
    reads2 = []
    real_open2 = open
    ns['open'] = lambda p, m='r': (reads2.append(p), real_open2(p, m))[1]
    target = os.path.join(sample_dir, 'kick.wav')
    ns['_info_cache'].clear()
    ns['_wav_info'](target)
    first_reads = len(reads2)
    for _ in range(4):
        ns['_wav_info'](target)
    check(first_reads == 1 and len(reads2) == 1,
          'four more lookups of the same file: %d reads total' % len(reads2))
    ns['open'] = real_open2

    print('\n--- browser: only the VISIBLE page hydrates')
    NOW[0] += ns['NOTE_QUIET_MS'] + 1        # pads quiet, so hydration may run
    many = tempfile.mkdtemp(prefix='tbx-sim-many-')
    for i in range(40):
        _write_wav(os.path.join(many, 'f%02d.wav' % i), secs=0.1)
    menu._open_dir(many)
    lvl = menu.cur
    lvl.idx = 30                                        # jump to a later page
    lvl.start = 24                                      # (what render() would set)
    reads.clear()
    menu.service_hydrate()
    check(reads and reads[0].endswith('f24.wav'),
          'the first header read is on the VISIBLE page, not row 0 (%s)'
          % (reads[0].rsplit('/', 1)[-1] if reads else 'none'))
    # Drain the visible page, then confirm it STOPS rather than grinding through
    # the other 32 files at 7-10ms a tick for no one's benefit.
    for _ in range(ns['MENU_VISIBLE'] * 2):
        menu.service_hydrate()
    reads.clear()
    check(menu.service_hydrate() is False and reads == [],
          'off-screen rows are left alone until you scroll to them')
    lvl.start = 0                                       # scroll back to the top
    check(menu.service_hydrate() is True,
          'and they hydrate once they come into view')
    ns['_wav_info'] = real_wav_info
    for tmp in (sample_dir, many):
        try:
            shutil.rmtree(tmp)
        except OSError:
            pass

    print('\n--- panel: only changed rows go out, bounded per tick')
    big = ns['_MenuLevel']('LONG', [('f%d' % i, None) for i in range(200)], accel=True)
    menu = ns['SketchMenu']()
    menu.open()
    menu.stack.append(big)
    menu.dirty = True
    PUSHES.clear()
    big.render(menu)                                    # first paint (full)
    rows = sum(y1 - y0 + 1 for y0, y1 in PUSHES)
    check(rows <= ns['PUSH_ROWS_PER_TICK'],
          'a full repaint sends at most one band this tick (%d rows), not 128' % rows)
    drained = 0
    while ns['_push_q'] and drained < 40:
        ns['_service_push']()
        drained += 1
    covered = set()
    for y0, y1 in PUSHES:
        covered.update(range(y0, y1 + 1))
    check(covered == set(range(128)),
          'and the whole screen still lands, spread over %d ticks' % (drained + 1))

    # The old banded flush deferred drawing while a repaint was in flight, so a fast
    # scroll left the cursor drawn nowhere for several ticks. Nothing is deferred
    # now: each move draws AND pushes before the next one arrives.
    H = ns['MENU_LINE_H']
    for n, delta in enumerate((3, -1, 5, 2)):
        del ns['_push_q'][:]
        PUSHES.clear()
        was_page = big.idx // ns['MENU_VISIBLE']
        big.handle(menu, delta, False, False)
        big.render(menu)
        if big.idx // ns['MENU_VISIBLE'] != was_page:
            continue                                    # page crossing: full repaint
        y_new = big._row_y(big.idx - big.start)
        check(PUSHES and PUSHES[0] == (y_new, y_new + H - 1),
              'move %d (%+d): the row moved TO goes out first' % (n + 1, delta))
        ns['_service_push']()
        rows = sum(y1 - y0 + 1 for y0, y1 in PUSHES)
        check(rows == 2 * H, 'and only those 2 rows are sent (%d rows)' % rows)

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
    check(ns['_writes'] == [] and ns['_settings_dirty'],
          'the commit marks settings dirty, it does NOT write flash mid-performance')
    check(settle(ns) is True and ns['_writes'] == ['write'],
          'exactly ONE flash write, once the pads go quiet')

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
        settle(ns)
        check(ns['_writes'] == ['write'],
              'an in-flight commit still reaches flash on %s()' % name)
        check(slot_params[0]['level'] == want, '  ...keeping the edited value (%s)' % name)

    print('\n--- hold on the CURSOR (not editing) still pops the level')
    menu, ed = _open_editor(ns)
    depth = len(menu.stack)
    ed.handle(menu, 0, False, True)
    check(len(menu.stack) == depth - 1, 'hold leaves the editor when not editing')

    # -- Kits ----------------------------------------------------------------
    # These drive the REAL menu callbacks and the REAL JSON file (redirected to a
    # temp path), so the round-trip through flash is exercised rather than mocked.
    # The samples are real WAVs on disk, so load_slot runs for true.
    kit_dir = tempfile.mkdtemp(prefix='tbx-sim-kits-')
    ns['KITS_FILE'] = os.path.join(kit_dir, 'kits.json')
    kit_samples = make_sample_dir()          # its own, outliving the browser tests'
    kick = os.path.join(kit_samples, 'kick.wav')
    snare = os.path.join(kit_samples, 'snare.wav')
    slot_paths = ns['slot_paths']
    slot_missing = ns['slot_missing']

    def drain_restore():
        """Run the restore queue to completion, past the boot settle gate."""
        ns['_service_restore']()                    # first call arms _boot_ticks
        NOW[0] += ns['LOAD_SETTLE_MS'] + 1
        for _ in range(ns['NUM_SLOTS'] + 2):
            if not ns['_service_restore']():
                break

    print('\n--- kits: a kit round-trips through flash')
    menu = ns['SketchMenu']()
    menu.open()
    check(ns['load_slot'](0, kick) is None, 'kick.wav loads into slot 0')
    check(ns['load_slot'](1, snare) is None, 'snare.wav loads into slot 1')
    slot_params[0]['pan'] = 50
    menu._do_save('rock')
    check([k['name'] for k in ns['_kits']] == ['rock'], 'saving stores one kit')
    on_disk = json.load(open(ns['KITS_FILE']))
    check(on_disk[0]['slots'][:2] == [kick, snare],
          'the kit on disk holds the sample PATHS, not audio')
    check(on_disk[0]['params'][0]['pan'] == 50, 'and each pad\'s params')
    check(ns['_current_kit_name'] == 'rock', 'the saved kit becomes current')
    check(ns['_kit_dirty'] is False, 'and starts out unmodified')
    check(ns['kit_display_name']() == 'rock', 'the idle screen names it')

    print('\n--- kits: the * marks unsaved work, and only real edits set it')
    ns['mark_kit_dirty']()
    check(ns['kit_display_name']() == 'rock*', 'an edit marks the kit modified')
    long_name = 'a' * ns['KIT_NAME_MAX']
    ns['_current_kit_name'] = long_name
    check(ns['kit_display_name']().endswith('*'),
          'a name at the length cap still shows its *')
    check(len(ns['kit_display_name']()) <= ns['KIT_HEADLINE_MAX'],
          'and the headline still clears the CH readout')
    ns['_current_kit_name'] = 'rock'

    print('\n--- kits: EMPTY is the virtual "new kit"')
    lst = ns['_kit_load_list']()
    check(lst[0]['name'] == ns['EMPTY_KIT_NAME'], 'EMPTY heads the Load list')
    menu.open()
    menu._load_kit(0)
    drain_restore()
    check(all(p is None for p in slot_paths), 'loading EMPTY clears every pad')
    check(slot_params[0]['pan'] == 0, 'and resets every param to its default')
    check(ns['_current_kit_name'] == '', 'EMPTY leaves NO current kit to overwrite')
    check(ns['kit_display_name']() == 'TRIGGERBOX', 'so the idle screen says so')

    print('\n--- kits: loading one drops the cached slot list')
    # The slot list is built once and reused, so a kit load has to invalidate it --
    # otherwise 'Kit parameters' shows the PREVIOUS kit's sample names.
    check(ns['load_slot'](0, kick) is None, 'a pad is loaded again')
    menu.open()
    menu._open_slots()
    cached = menu._slots_level
    check(cached is not None and 'kick' in cached.items[0][0],
          'the list is cached, showing the loaded pad')
    menu.open()
    menu._load_kit(0)                                   # EMPTY
    drain_restore()
    check(menu._slots_level is None, 'the kit load dropped the stale cache')
    menu.open()
    menu._open_slots()
    check('kick' not in menu._slots_level.items[0][0],
          'so the rebuilt list shows the pad as empty')

    print('\n--- kits: loading one refills the pads from the card')
    lst = ns['_kit_load_list']()
    idx = [i for i, k in enumerate(lst) if k['name'] == 'rock'][0]
    menu.open()
    menu._load_kit(idx)
    check(not menu.is_open, 'the menu closes so the progress notice owns the panel')
    check(len(ns['_restore_queue']) == 2, 'both samples are QUEUED, not loaded inline')
    check(ns['_settings']['slots'][:2] == [kick, snare],
          'the kit is persisted before the first load, so a power cut resumes it')
    drain_restore()
    check(slot_paths[:2] == [kick, snare], 'both pads come back')
    check(slot_params[0]['pan'] == 50, 'with their saved params')
    check(ns['_kit_dirty'] is False, 'a freshly loaded kit is not modified')

    print('\n--- kits: a sample that moved leaves a LABELLED empty pad')
    os.remove(snare)
    menu.open()
    menu._load_kit(idx)
    drain_restore()
    check(slot_paths[1] is None, 'the pad stays empty -- there is nothing to play')
    check(slot_missing[1] and slot_missing[1][0] == snare,
          'but it remembers the file it wanted')
    check(slot_missing[1][1] == 'GONE',
          "and knows it is MISSING, not corrupt ('GONE' vs 'BAD')")
    check(ns['slot_label'](1) == '!snare', 'and the UI shows it tag-first')
    check(ns['wanted_paths']()[1] == snare,
          'the wanted path is what gets persisted, so the card can bring it back')
    check(slot_paths[0] == kick, 'the other pads load normally')
    _write_wav(snare, secs=0.50)
    check(ns['load_slot'](1, snare) is None, 'restoring the file lets it load again')
    check(slot_missing[1] is None, 'which clears the marker')

    print('\n--- kits: overwrite, reserved names, and the full cap')
    menu.open()
    menu._do_save('rock')
    check(len(ns['_kits']) == 1, 'saving the same name overwrites in place')
    menu.open()
    menu._start_save()
    labels = [it[0] for it in menu.cur.items]
    check('Overwrite' in labels and 'Save as new' in labels,
          'with a kit current, Save offers Overwrite')
    check(menu.cur.idx == 1, 'and the cursor skips the unclickable name row')

    class _FakeName:
        name = ns['EMPTY_KIT_NAME'].lower()
    before = len(ns['_kits'])
    menu.open()
    menu._commit_name(_FakeName())
    check(menu.cur.title == 'NAME RESERVED', 'EMPTY is refused as a kit name')
    check(len(ns['_kits']) == before, 'and nothing is saved')

    print('\n--- kits: naming one on the encoder')
    menu.open()
    menu._start_name_entry()
    lvl = menu.cur
    ring = ns['_NAME_RING']
    for ch in 'jazz':
        lvl.sel = ring.index(ch)
        lvl.handle(menu, 0, True, False)
    check(lvl.name == 'jazz', 'clicking the ring builds the name')
    lvl.sel = ring.index('DEL')
    lvl.handle(menu, 0, True, False)
    check(lvl.name == 'jaz', 'DEL backspaces')
    lvl.sel = ring.index('z')
    lvl.handle(menu, 0, True, False)
    lvl.sel = 0
    lvl.handle(menu, 99, False, False)
    check(lvl.sel == len(ring) - 1, 'a fast spin clamps on OK rather than wrapping')
    lvl.handle(menu, 0, True, False)                    # OK
    check(menu.cur.title == 'SAVE KIT?', 'OK asks to confirm the new name')
    menu.cur.items[menu.cur.idx][1]()                   # Yes
    check('jazz' in [k['name'] for k in ns['_kits']], 'confirming saves it')
    check(ns['_current_kit_name'] == 'jazz', 'and it becomes the current kit')
    lvl2 = ns['_NameLevel']()
    for _ in range(ns['KIT_NAME_MAX'] + 5):
        lvl2.sel = ring.index('a')
        lvl2.handle(menu, 0, True, False)
    check(len(lvl2.name) == ns['KIT_NAME_MAX'], 'the name cap holds')

    print('\n--- kits: the Load list is EMPTY first, then alphabetical')
    menu.open()
    menu._do_save('Brushes')
    menu.open()
    menu._do_save('anvil')
    names = [k['name'] for k in ns['_kit_load_list']()]
    check(names[0] == ns['EMPTY_KIT_NAME'], 'EMPTY stays pinned first')
    check(names[1:] == sorted(names[1:], key=str.lower),
          'the rest sort case-insensitively: %r' % (names[1:],))
    for n in ('jazz', 'Brushes', 'anvil'):
        menu.open()
        menu._do_delete(n)

    print('\n--- the root menu is the agreed IA')
    menu.open()
    check([it[0] for it in menu.cur.items] == [
        'Kit parameters', 'Save kit', 'Load kit', 'Delete kit',
        'MIDI base note', 'Resume playing'], 'root items, in order')
    check(len(menu.cur.items) <= ns['MENU_VISIBLE'], 'and still one page')

    print('\n--- kits: delete removes the snapshot, not what is playing')
    menu.open()
    menu._do_save('rock')
    menu.open()
    menu._do_delete('rock')
    check(ns['_kits'] == [], 'the kit is gone')
    check(json.load(open(ns['KITS_FILE'])) == [], 'and gone from flash')
    check(ns['_current_kit_name'] == '', 'the Overwrite target is cleared')
    check(slot_paths[:2] == [kick, snare], 'the live pads keep playing')
    menu.open()
    menu._open_delete()
    check(menu.cur.items[0][1] is None, 'an empty Delete list has nothing to click')
    shutil.rmtree(kit_dir, ignore_errors=True)
    shutil.rmtree(kit_samples, ignore_errors=True)

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
