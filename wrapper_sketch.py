# AMYboard global launcher / wrapper menu
# ============================================================================
# This single file is the PERMANENT launcher. It is deployed to
# /user/current/sketch.py (the file the firmware boots) and is never
# overwritten. Instead of swapping the booted file, it reads a tiny state file
# to decide, on each cold boot, whether to show the global menu or run a sketch
# that lives on the SD card.
#
# Firmware execution model (verified against the working sketch.py + the
# amy_patch_examples encoder sketch): the firmware runs this file's top-level
# code ONCE, then calls the module-level loop() repeatedly (~60 ms). We never
# run our own `while True` loop -- doing so would block MIDI/audio servicing and
# hang the deploy readback.
#
# Two modes, chosen at boot from STATE_FILE:
#   "menu"        -> draw the wrapper menu; encoder turn/click/hold navigates.
#   "<name>.py"   -> mount SD and exec /sd/sketches/<name>.py. That sketch's own
#                    module-level loop() is then driven from our loop().
#
# Transitions are a clean cold boot (machine.reset()), which gives each side a
# fresh AMY/display state (brief audio dropout is accepted, per design):
#   menu  : click a sketch     -> write its filename to STATE_FILE -> reset.
#   sketch: long-press encoder -> write "menu" to STATE_FILE       -> reset.
#
# The launcher owns the loop, so the long-press "home" gesture is GLOBAL: any
# sketch can be exited back to the menu without the sketch implementing
# anything. A loaded sketch (e.g. 01_polysynth.py) therefore needs no changes.
# ============================================================================

import amy, amyboard, machine, time
import os

# --- Config -----------------------------------------------------------------
STATE_FILE = '/user/launcher_state'   # flash: always writable, survives reboot
# Candidate sketch folders, in priority order. The first one that exists (and,
# preferably, already holds a .py) wins, so sketches can live on the SD card or
# on internal flash. Add any number of NN_name.py files there.
SKETCH_DIRS = ('/sd/sketches', '/user/sketches', '/sketches')
MENU_STATE = 'menu'

# Adafruit Seesaw rotary encoder + push button (front-panel I2C).
SEESAW_ADDR = 0x36
BTN_PIN = 24
HOLD_MS = 600            # long-press threshold = "back / home"
INVERT_DELTA = False     # turning the encoder right scrolls DOWN the list

# OLED layout (128x128 SSD1327, firmware-owned amyboard.display).
LINE_H = 12
TOP_Y = 18
VISIBLE = 8
LABEL_MAX = 18


# --- Encoder ----------------------------------------------------------------
class Encoder:
    """Reads the Seesaw encoder count + button and reports (delta, click, hold).

    delta : signed detents since the last update (CW positive after invert).
    click : True once on a SHORT press release (select / enter).
    hold  : True once when the press passes HOLD_MS (back / home).
    All hardware access is guarded so a missing/unplugged accessory degrades to
    "no input" instead of crashing the launcher.
    """

    def __init__(self):
        try:
            amyboard.init_buttons(pins=(BTN_PIN,), seesaw_dev=SEESAW_ADDR)
        except Exception:
            pass
        self._last = self._count()
        self._btn_down = False
        self._down_at = 0
        self._hold_fired = False

    def _count(self):
        try:
            return amyboard.read_encoder(seesaw_dev=SEESAW_ADDR)
        except Exception:
            return 0

    def _pressed(self):
        try:
            return bool(amyboard.read_buttons(pins=(BTN_PIN,), seesaw_dev=SEESAW_ADDR)[0])
        except Exception:
            return False

    def update(self):
        c = self._count()
        delta = c - self._last
        self._last = c
        if INVERT_DELTA:
            delta = -delta

        click = False
        hold = False
        pressed = self._pressed()
        now = time.ticks_ms()
        if pressed and not self._btn_down:
            self._btn_down = True
            self._down_at = now
            self._hold_fired = False
        elif pressed and self._btn_down:
            if not self._hold_fired and time.ticks_diff(now, self._down_at) >= HOLD_MS:
                hold = True
                self._hold_fired = True
        elif (not pressed) and self._btn_down:
            self._btn_down = False
            if not self._hold_fired:
                click = True
        return delta, click, hold


# --- Menu -------------------------------------------------------------------
class _Level:
    __slots__ = ('title', 'items', 'idx')

    def __init__(self, title, items):
        self.title = title
        # items: list of (label, callback_or_None). None = non-selectable line.
        self.items = items if items else [('(empty)', None)]
        self.idx = 0


class Menu:
    """Stack-based menu. turn = scroll, click = enter/select, hold = up a level."""

    def __init__(self, on_launch):
        self.on_launch = on_launch
        self.stack = [self._root()]
        self.dirty = True

    def _root(self):
        return _Level('AMYBOARD', [
            ('Load Sketch', self._open_sketches),
            ('Display Mode', self._open_display),
        ])

    @property
    def cur(self):
        return self.stack[-1]

    def _list_sketches(self):
        try:
            files = os.listdir(_resolve_sketch_dir())
        except Exception:
            return []
        names = [f for f in files if f.endswith('.py') and not f.startswith('.')]
        names.sort()
        return names

    def _open_sketches(self):
        names = self._list_sketches()
        if names:
            # default-arg binding captures each name by value in the closure
            items = [(n, lambda n=n: self.on_launch(n)) for n in names]
        else:
            items = [('(no sketches found)', None)]
        self.stack.append(_Level('LOAD SKETCH', items))

    def _open_display(self):
        # TODO: wire up real display modes (idle takeover). Navigation only.
        modes = ['CC Monitor', 'CV Monitor', 'Screensaver', 'Oscilloscope', 'Blank']
        self.stack.append(_Level('DISPLAY MODE', [(m, None) for m in modes]))

    def update(self, delta, click, hold):
        lvl = self.cur
        if delta:
            n = len(lvl.items)
            lvl.idx = (lvl.idx + delta) % n
            self.dirty = True
        if hold:
            if len(self.stack) > 1:
                self.stack.pop()
                self.dirty = True
        elif click:
            _, cb = lvl.items[lvl.idx]
            if cb:
                cb()
                self.dirty = True

    def render(self):
        if not self.dirty:
            return
        self.dirty = False
        try:
            d = amyboard.display
            d.fill(0)
            lvl = self.cur
            d.text(lvl.title, 0, 0, 255)
            n = len(lvl.items)
            start = 0
            if n > VISIBLE:
                start = lvl.idx - VISIBLE // 2
                if start < 0:
                    start = 0
                if start > n - VISIBLE:
                    start = n - VISIBLE
            y = TOP_Y
            i = start
            while i < n and i < start + VISIBLE:
                label, _ = lvl.items[i]
                if i == lvl.idx:
                    d.text('>', 0, y, 255)
                    d.text(label[:LABEL_MAX], 12, y, 255)
                else:
                    d.text(label[:LABEL_MAX], 12, y, 110)
                y += LINE_H
                i += 1
            amyboard.display_refresh()
        except Exception:
            # No display attached yet -> menu still works headlessly.
            pass


# --- Launcher ---------------------------------------------------------------
def _read_state():
    try:
        with open(STATE_FILE) as f:
            return f.read().strip()
    except Exception:
        return MENU_STATE


def _write_state(s):
    try:
        with open(STATE_FILE, 'w') as f:
            f.write(s)
    except Exception:
        pass


def _mount_sd():
    try:
        amyboard.mount_sd()
    except Exception:
        pass


_resolved_dir = None


def _resolve_sketch_dir():
    """Pick the sketch folder to use: first candidate that already holds a .py,
    else the first that simply exists, else the first candidate as a default.
    Cached for the life of this boot."""
    global _resolved_dir
    if _resolved_dir:
        return _resolved_dir
    first_existing = None
    for p in SKETCH_DIRS:
        try:
            entries = os.listdir(p)
        except Exception:
            continue
        if first_existing is None:
            first_existing = p
        if any(e.endswith('.py') and not e.startswith('.') for e in entries):
            _resolved_dir = p
            return p
    _resolved_dir = first_existing or SKETCH_DIRS[0]
    return _resolved_dir


def launch_sketch(name):
    """Menu click callback: persist choice and cold-boot into the sketch."""
    _write_state(name)
    machine.reset()


def go_home():
    """Global long-press: persist 'menu' and cold-boot back to the menu."""
    _write_state(MENU_STATE)
    machine.reset()


_mode = None          # 'menu' or 'sketch'
_menu = None
_encoder = None
_sketch_loop = None


def _start_menu():
    global _mode, _menu, _encoder
    try:
        amyboard.init_display()
    except Exception:
        pass
    _encoder = Encoder()
    _menu = Menu(launch_sketch)
    _menu.render()
    _mode = 'menu'


def _start_sketch(name):
    global _mode, _encoder, _sketch_loop
    _encoder = Encoder()                      # drives the global home gesture
    ns = {'__name__': '__main__'}
    try:
        with open(_resolve_sketch_dir() + '/' + name) as f:
            src = f.read()
        exec(src, ns)                         # runs the sketch's top-level setup
        _sketch_loop = ns.get('loop')         # its module-level loop(), if any
        _mode = 'sketch'
    except Exception as e:
        # Never brick the board on a bad/missing sketch: revert to the menu.
        print('Sketch load failed:', name, e)
        _write_state(MENU_STATE)
        try:
            amyboard.init_display()
            d = amyboard.display
            d.fill(0)
            d.text('LOAD FAILED', 0, 0, 255)
            d.text(name[:LABEL_MAX], 0, 16, 255)
            amyboard.display_refresh()
        except Exception:
            pass
        _start_menu()


# Boot once: pick a mode from the state file.
_mount_sd()
_state = _read_state()
if _state and _state != MENU_STATE:
    _start_sketch(_state)
else:
    _start_menu()


def loop():
    # Firmware calls this repeatedly (~60 ms).
    if _mode == 'sketch':
        # Global "home": a long-press always returns to the wrapper menu,
        # regardless of what the loaded sketch does.
        try:
            _, _, hold = _encoder.update()
            if hold:
                go_home()
        except Exception:
            pass
        if _sketch_loop:
            _sketch_loop()
    else:
        delta, click, hold = _encoder.update()
        if delta or click or hold:
            _menu.update(delta, click, hold)
        _menu.render()
