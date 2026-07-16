# AMYboard global launcher / wrapper menu
# ============================================================================
# This single file is the PERMANENT launcher. It is deployed to
# /user/current/sketch.py (the file the firmware boots) and is never
# overwritten. On each cold boot it reads a tiny state file to decide whether to
# show the global menu or run a chosen sketch from flash.
#
# Firmware execution model (verified against the working sketch.py + the
# amy_patch_examples encoder sketch): the firmware runs this file's top-level
# code ONCE, then calls the module-level loop() repeatedly (~69 ms -- MEASURED
# on-device 2026-07-16: 69.4 ms avg, ~99 ms max; an earlier "~60 ms" here was a
# guess and was wrong). We never run our own `while True` loop -- doing so would
# block MIDI/audio servicing and hang the deploy readback.
#
# That ~69 ms is the single most load-bearing number in this codebase, because it
# is the floor on input latency: an encoder detent waits up to a full tick just to
# be SEEN, and anything drawn in bands takes one tick per band. It also makes any
# throttle constant below ~69 ms INERT -- a `dt < N` gate can never fire when the
# caller only arrives every 69 ms. Do not add or tune such a gate without checking
# it against this number. A sketch's whole loop() body typically uses only ~7 ms of
# the tick; the rest is firmware + our encoder read, so sketch-side optimisation
# cannot move menu latency much.
#
# Runtime states (no reset between the first two):
#   sketch  : a sketch is exec'd and resident; its module-level loop() is driven
#             from our loop(). Its MIDI callback + audio keep running.
#   overlay : the GLOBAL menu is drawn OVER a still-resident sketch. We stop
#             calling the sketch's loop() (display/CV pause) but its MIDI
#             callback + audio stay live, so held notes never drop. Choosing
#             "Resume" just flips back to `sketch` -- no reset, instant.
#   menu    : cold boot with no sketch resident -> the global menu with only
#             "Load Sketch".
#
# Only ONE transition resets the board: "Load Sketch" -> write the choice to
# STATE_FILE -> machine.reset(). The reset is REQUIRED for correctness, not just
# cleanliness: exec'ing a new sketch without a reset would leave the previous
# sketch's midi.add_callback registered, so two synths would answer MIDI. A cold
# boot tears the old namespace + callbacks down.
#
# --- Universal navigation gesture -------------------------------------------
# ONE rule everywhere: click goes IN, hold backs OUT.
#   turn  : scroll within a menu.
#   click : enter / select / drill in.
#   hold  : auto-REPEATS -- each ~600 ms of continuous hold acts once more.
#
#   From "playing" (no menu open) the three obvious gestures -- turn, click, and
#   a short hold -- ALL open the sketch's own menu. Once a menu is open, a hold
#   backs out one level per tick, so continuing to hold from playing escalates
#   to the global menu. The ladder, deepest -> shallowest:
#
#     name-entry -> sub-menu -> sketch (root) menu -> GLOBAL menu
#                                    ^ first hold from playing opens this
#
#   A hold out of the sketch's root menu goes straight to global -- it never
#   stops at "playing", so it never flashes the display mode on the way. Playing
#   is the closed-menu base state, re-entered by a "Resume Playing" click or the
#   menu's idle timeout.
#
# --- Launcher <-> sketch contract -------------------------------------------
# To make hold-to-back-out span both the sketch's own menu levels AND the final
# hop out to global without two encoder readers fighting, the LAUNCHER is the
# single encoder reader. It injects a `launcher` object into each sketch's
# namespace and feeds the sketch abstract input events through it:
#   launcher.delta      : encoder detents this tick (signed)   [launcher -> sketch]
#   launcher.click      : True on a short click this tick       [launcher -> sketch]
#   launcher.back       : True this tick when the sketch should pop ONE of its
#                         own menu levels (a hold while menu_depth > 0)
#                                                               [launcher -> sketch]
#   launcher.menu_depth : how many levels deep the sketch's own menu is; 0 means
#                         "playing". The sketch keeps this current.
#                                                               [sketch -> launcher]
#   launcher.repaint    : launcher sets True on Resume so the sketch forces a
#                         full redraw (the overlay clobbered the screen).
#                                                               [launcher -> sketch]
#   launcher.resumed    : launcher sets True on Resume so the sketch closes its
#                         own menu -> Resume always returns to playing.
#                                                               [launcher -> sketch]
# A hold becomes `back` when the sketch still has menu levels to pop, or opens
# the global overlay once the sketch is back at playing (menu_depth == 0). A
# sketch that ignores all of this (never sets menu_depth) simply jumps straight
# to the global menu on a hold -- which is exactly right, since it has nothing of
# its own to back out of. Sketches must consume input via these fields rather
# than reading the encoder directly, so there is only ever one reader.
# ============================================================================

import amy, amyboard, machine, time
import os

# --- Config -----------------------------------------------------------------
STATE_FILE = '/user/launcher_state'   # flash: always writable, survives reboot
# Candidate sketch folders, in priority order. The first one that exists (and,
# preferably, already holds a .py) wins. Internal flash (/user/sketches) is
# listed first: it is always writable over the serial REPL, so sketches can be
# deployed straight to the board with deploy_auto.py and never need the SD card.
# (The board's FatFs can read but not write the 128 GB exFAT SD card, so /sd is
# kept only as an optional read-only archive fallback.) Add any number of
# NN_name.py files to the chosen folder.
SKETCH_DIRS = ('/user/sketches', '/sd/sketches', '/sketches')
MENU_STATE = 'menu'

# Adafruit Seesaw rotary encoder + push button (front-panel I2C).
SEESAW_ADDR = 0x36
BTN_PIN = 24
HOLD_MS = 600            # one "back out a level" pop per this much continuous hold
INVERT_DELTA = False     # turning the encoder right scrolls DOWN the list

# OLED layout (128x128 SSD1327, firmware-owned amyboard.display).
LINE_H = 12
TOP_Y = 18
VISIBLE = 8
LABEL_MAX = 18


# --- Audio-safe display push ------------------------------------------------
# A full 128x128 refresh blits ~8KB over the 400kHz I2C bus (~150ms) and blocks
# the single MicroPython thread long enough to drop a note-off -- and the global
# menu opens OVER a sketch that is still sounding. So the menu never does a full
# refresh: it pushes only changed rows (a cursor move touches two), and its full
# repaint (open / level change) is flushed in bounded row BANDS spread across
# successive loop() calls so no single blit exceeds a few tens of ms.
FLUSH_BAND_ROWS = 12         # pixel-rows pushed per loop() while flushing (~19ms)
_flush_active = False
_flush_y = 0
_flush_y1 = 127


def _push_rows(y0, y1):
    # Windowed refresh: send only framebuffer rows [y0, y1] (SSD1327 only).
    # Return False for anything else / on any error so the caller can fall back.
    try:
        hw = amyboard.display._hw
    except Exception:
        hw = None
    if hw is None or not hasattr(hw, 'col_addr') or not hasattr(hw, 'row_addr'):
        return False
    try:
        y0 = max(0, min(127, int(y0)))
        y1 = max(0, min(127, int(y1)))
        if y1 < y0:
            return False
        row_bytes = hw.width // 2          # 64 bytes/row at 128px wide, 4bpp
        hw.write_cmd(0x15)                 # SSD1327 SET_COL_ADDR
        hw.write_cmd(hw.col_addr[0])
        hw.write_cmd(hw.col_addr[1])
        hw.write_cmd(0x75)                 # SSD1327 SET_ROW_ADDR
        hw.write_cmd(y0)
        hw.write_cmd(y1)
        hw.write_data(memoryview(hw.buffer)[y0 * row_bytes:(y1 + 1) * row_bytes])
        return True
    except Exception:
        return False


def _begin_flush(y0, y1):
    global _flush_active, _flush_y, _flush_y1
    _flush_active = True
    _flush_y = max(0, min(127, int(y0)))
    _flush_y1 = max(0, min(127, int(y1)))


def _service_flush():
    # Push one band per call; return True while a flush is still in progress.
    global _flush_active, _flush_y
    if not _flush_active:
        return False
    a = _flush_y
    b = min(_flush_y1, a + FLUSH_BAND_ROWS - 1)
    if not _push_rows(a, b):
        try:
            amyboard.display_refresh()        # non-SSD1327 fallback
        except Exception:
            pass
        _flush_active = False
        return True
    _flush_y = b + 1
    if _flush_y > _flush_y1:
        _flush_active = False
    return True


# --- Launcher <-> sketch API -------------------------------------------------
class _LauncherAPI:
    """Injected into every sketch's namespace as the global `launcher`. Carries
    abstract encoder events into the sketch and the sketch's menu depth back
    out. See the contract block at the top of this file."""
    __slots__ = ('delta', 'click', 'back', 'menu_depth', 'repaint', 'resumed')

    def __init__(self):
        self.reset()

    def reset(self):
        self.delta = 0
        self.click = False
        self.back = False
        self.menu_depth = 0
        self.repaint = False
        self.resumed = False


_api = _LauncherAPI()


# --- Encoder ----------------------------------------------------------------
class Encoder:
    """Reads the Seesaw encoder count + button and reports (delta, click, hold).

    delta : signed detents since the last update (CW positive after invert).
    click : True once on a SHORT press release (no hold fired) -> select / enter.
    hold  : True once per HOLD_MS of continuous press (auto-repeat) -> back out a
            level. Firing repeatedly while held is what lets one long press walk
            out through several levels.
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
        self._hold_count = 0     # number of HOLD_MS pops already fired this press

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
            self._hold_count = 0
        elif pressed and self._btn_down:
            n = time.ticks_diff(now, self._down_at) // HOLD_MS
            if n > self._hold_count:
                self._hold_count = n     # one pop per HOLD_MS boundary crossed
                hold = True
        elif (not pressed) and self._btn_down:
            self._btn_down = False
            if self._hold_count == 0:    # released before any hold -> a click
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
    """Stack-based global menu. turn = scroll, click = enter/select,
    hold = back out one level (pop the stack)."""

    def __init__(self, on_launch, on_resume=None, has_sketch=False):
        self.on_launch = on_launch
        self.on_resume = on_resume
        self.has_sketch = has_sketch
        self.stack = [self._root()]
        self.dirty = True
        self._needs_clear = True     # full repaint (vs per-row diff) next draw
        self._prev = None            # last drawn frame, for row-level diffing

    def _root(self):
        items = []
        # "Resume" only exists when a sketch is resident to return to.
        if self.has_sketch and self.on_resume:
            items.append(('Resume', self.on_resume))
        items.append(('Load Sketch', self._open_sketches))
        items.append(('WiFi', self._open_wifi))
        return _Level('AMYBOARD', items)

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
        self._needs_clear = True

    def _wifi_menu_items(self):
        # Status line (non-selectable) + a single toggle whose label tracks the
        # current state. Rebuilt on open and after each toggle so both refresh.
        on = wifi_is_enabled()
        return [
            (_wifi_status_line(), None),
            ('Turn WiFi Off' if on else 'Turn WiFi On', self._toggle_wifi),
        ]

    def _open_wifi(self):
        self.stack.append(_Level('WIFI', self._wifi_menu_items()))
        self._needs_clear = True

    def _toggle_wifi(self):
        # Flip the persisted enable flag, then connect/disconnect right now. The
        # join is blocking (a few seconds) -- acceptable for a deliberate menu
        # action; under the overlay AMY keeps sounding held notes meanwhile.
        _wifi_set_enabled(not wifi_is_enabled())
        if wifi_is_enabled():
            _wifi_connect()
        else:
            _wifi_disconnect()
        self.stack[-1] = _Level('WIFI', self._wifi_menu_items())
        self._needs_clear = True

    def update(self, delta, click, hold):
        lvl = self.cur
        if delta:
            n = len(lvl.items)
            # Clamp at the ends -- the cursor stops at the top/bottom, no wrap.
            lvl.idx = max(0, min(n - 1, lvl.idx + delta))
            self.dirty = True
        if hold:
            # Back out one level. At the root there is nothing to pop -> the
            # global menu is the top of the ladder.
            if len(self.stack) > 1:
                self.stack.pop()
                self.dirty = True
                self._needs_clear = True
        elif click:
            _, cb = lvl.items[lvl.idx]
            if cb:
                cb()
                self.dirty = True

    def _draw_row(self, d, y, kind, payload):
        d.fill_rect(0, y, 128, LINE_H, 0)
        if kind == 't':
            d.text(payload, 0, y, 255)
        else:
            sel, label = payload
            if sel:
                d.text('>', 0, y, 255)
                d.text(label[:LABEL_MAX], 12, y, 255)
            else:
                d.text(label[:LABEL_MAX], 12, y, 110)

    def render(self):
        # If a progressive full-repaint flush is in flight, keep pushing bands
        # and defer any new drawing until the panel is settled.
        if _flush_active:
            _service_flush()
            return
        if not self.dirty:
            return
        self.dirty = False
        try:
            d = amyboard.display
            lvl = self.cur
            n = len(lvl.items)
            start = 0
            if n > VISIBLE:
                start = lvl.idx - VISIBLE // 2
                if start < 0:
                    start = 0
                if start > n - VISIBLE:
                    start = n - VISIBLE
            # Current frame = title row + visible item rows, as diffable tuples.
            frame = [(0, 't', lvl.title)]
            y = TOP_Y
            i = start
            while i < n and i < start + VISIBLE:
                frame.append((y, 'i', (i == lvl.idx, lvl.items[i][0])))
                y += LINE_H
                i += 1
            # Full repaint on open / level change, pushed progressively in bands
            # so audio under the overlay isn't stalled. Otherwise push ONLY the
            # rows that changed (a cursor move touches two).
            if self._needs_clear or self._prev is None or len(self._prev) != len(frame):
                d.fill(0)
                for (ry, kind, payload) in frame:
                    self._draw_row(d, ry, kind, payload)
                _begin_flush(0, 127)
                self._needs_clear = False
            else:
                for idx in range(len(frame)):
                    if frame[idx] != self._prev[idx]:
                        ry, kind, payload = frame[idx]
                        self._draw_row(d, ry, kind, payload)
                        if not _push_rows(ry, ry + LINE_H - 1):
                            amyboard.display_refresh()
            self._prev = frame
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


# --- WiFi (optional, user-toggled) ------------------------------------------
# WiFi REMEMBERS its last on/off setting across reboots: the menu toggle writes
# the choice to flash (WIFI_STATE_FILE), and each cold boot reads it back and
# reconnects if it was left on. So the board comes up however you last left it --
# leave it on to keep it reachable across resets (incl. deploys), turn it off and
# it stays off. Credentials live in a JSON file on flash (WIFI_CONF), NOT in this
# committed launcher, so the repo stays secret-free. Create it once from the REPL:
#   import json
#   json.dump({'ssid': 'my-net', 'password': 'pw', 'webrepl_password': 'amyboard'},
#             open('/user/wifi.json', 'w'))
# Every call here is fully defensive: a missing/bad config or a failed join can
# never raise out of the boot path and brick the board (cf. the start_amy note).
WIFI_CONF = '/user/wifi.json'
WIFI_STATE_FILE = '/user/wifi_enabled'   # remembers last on/off across reboots
_wifi_ip = None                          # last known IP, for the status line


def wifi_is_enabled():
    try:
        with open(WIFI_STATE_FILE) as f:
            return f.read().strip() == '1'
    except Exception:
        return False


def _wifi_set_enabled(on):
    try:
        with open(WIFI_STATE_FILE, 'w') as f:
            f.write('1' if on else '0')
    except Exception:
        pass


def _wifi_load_conf():
    try:
        import json
        with open(WIFI_CONF) as f:
            return json.load(f)
    except Exception:
        return None


def _wifi_connect():
    """Join WiFi + start WebREPL from the on-flash config. Returns the IP (str)
    or None. Never raises: a bad/missing config just leaves the board offline."""
    global _wifi_ip
    conf = _wifi_load_conf()
    if not conf or not conf.get('ssid'):
        print('WiFi: no /user/wifi.json (ssid) -- staying offline')
        _wifi_ip = None
        return None
    try:
        _wifi_ip = amyboard.wifi(conf['ssid'], conf.get('password', ''))
    except Exception as e:
        print('WiFi connect failed:', e)
        _wifi_ip = None
        return None
    # WebREPL is best-effort -- a failure here must not undo a good connection.
    try:
        import webrepl
        webrepl.start(password=conf.get('webrepl_password', 'amyboard'))
    except Exception as e:
        print('WebREPL start failed:', e)
    print('WiFi up:', _wifi_ip)
    return _wifi_ip


def _wifi_disconnect():
    """Best-effort teardown. Even if the radio can't be fully brought down on
    this firmware, the persisted flag is now off, so the next boot stays
    offline."""
    global _wifi_ip
    _wifi_ip = None
    try:
        import webrepl
        webrepl.stop()
    except Exception:
        pass
    try:
        import network
        network.WLAN(network.STA_IF).active(False)
    except Exception:
        pass


def _wifi_status_line():
    if not wifi_is_enabled():
        return 'Status: off'
    if _wifi_ip:
        return _wifi_ip[:LABEL_MAX]
    return 'Status: on (no IP)'


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
    """Menu click callback: persist choice and cold-boot into the sketch. The
    reset is required so the previous sketch's MIDI callback is torn down."""
    _write_state(name)
    machine.reset()


def resume_sketch():
    """Menu 'Resume' click: dismiss the overlay and hand control back to the
    still-resident sketch. No reset -> held notes/audio never dropped. `resumed`
    tells the sketch to close its own menu (Resume returns to playing) and
    `repaint` tells it to redraw the screen the overlay clobbered."""
    global _overlay
    _overlay = False
    _api.resumed = True
    _api.repaint = True


_mode = None          # 'menu' | 'sketch'
_overlay = False      # True while the global menu is drawn over a live sketch
_menu = None
_encoder = None
_sketch_loop = None


def _open_overlay():
    """Raise the global menu over the running sketch (no reset)."""
    global _overlay, _menu
    _overlay = True
    _menu = Menu(launch_sketch, on_resume=resume_sketch, has_sketch=True)
    _menu.render()


def _start_menu():
    """Cold-boot menu: no sketch resident, so only 'Load Sketch'."""
    global _mode, _menu, _encoder, _overlay
    _overlay = False
    try:
        amyboard.init_display()
    except Exception:
        pass
    if _encoder is None:
        _encoder = Encoder()
    _menu = Menu(launch_sketch, on_resume=None, has_sketch=False)
    _menu.render()
    _mode = 'menu'


def _start_sketch(name):
    global _mode, _encoder, _sketch_loop
    _encoder = Encoder()                      # sole encoder reader
    _api.reset()
    ns = {'__name__': '__main__', 'launcher': _api}
    try:
        with open(_resolve_sketch_dir() + '/' + name) as f:
            src = f.read()
        exec(src, ns)                         # runs the sketch's top-level setup
        _sketch_loop = ns.get('loop')         # its module-level loop(), if any
        _mode = 'sketch'
    except Exception as e:
        # Never brick the board on a bad/missing sketch: clear the state so the
        # next cold boot shows the menu (avoids a boot-loop into a bad sketch)
        # and fall back to the menu now.
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


# NOTE: do NOT call amyboard.start_amy() here at boot. It HARD-FAULTS at this
# early stage (a chip-level panic that try/except cannot catch), which reset-loops
# the board -- recover only via safe boot (hold BOOT during power-up) or reflash.
# It is safe to call from the REPL post-boot, but not from the launcher's boot
# path. The audio engine's auto-start is a firmware concern; the intermittent
# "no audio after a machine.reset()" is a firmware bug (reported upstream).

# Boot once: pick a mode from the state file.
_mount_sd()
_state = _read_state()
if _state and _state != MENU_STATE:
    _start_sketch(_state)
else:
    _start_menu()

# Rejoin WiFi if it was left on (persisted flag) -- the board comes up however
# you last left it. Runs AFTER the synth/menu is up so audio comes alive first;
# the blocking join then adds a few seconds before the first loop(). Fully
# guarded -- never bricks boot.
if wifi_is_enabled():
    _wifi_connect()


# --- Reboot: on-device gesture + remote request -----------------------------
# Two ways to reboot the board, both just calling machine.reset():
#   * On-device: at the GLOBAL menu root, keep holding the encoder. After
#     REBOOT_HOLD_MS of continuous hold a countdown shows, then it resets. A turn
#     or release cancels. (A normal hold-to-open-global is well under this.)
#   * Remote: deploy_wifi.py drops REBOOT_SENTINEL on flash; loop() polls for it
#     (every REBOOT_POLL_EVERY ticks, ~2s), and if present deletes it and resets.
#     This is how a WiFi deploy self-activates -- WebREPL can push files here but
#     can't run REPL commands while the launcher owns the thread, so it cannot
#     machine.reset() directly.
REBOOT_HOLD_MS = 5000
REBOOT_SENTINEL = '/user/reboot_request'
REBOOT_POLL_EVERY = 32          # loop() ticks between sentinel stats (~2s)
_reboot_poll = 0
_reboot_arm_ms = None           # ticks_ms when the hold-to-reboot arm began
_reboot_last_remaining = -1


def _reboot():
    try:
        machine.reset()
    except Exception:
        pass


def _check_reboot_request():
    # Remote reboot: a WiFi deploy drops REBOOT_SENTINEL. Delete it FIRST so a
    # wedged reset can't turn into a boot loop, then reset.
    try:
        os.stat(REBOOT_SENTINEL)
    except Exception:
        return False
    try:
        os.remove(REBOOT_SENTINEL)
    except Exception:
        pass
    _reboot()
    return True


def _draw_reboot_countdown(remaining):
    # Redraw only when the whole-second changes (a handful of blits, not per-tick).
    global _reboot_last_remaining
    if remaining == _reboot_last_remaining:
        return
    _reboot_last_remaining = remaining
    try:
        d = amyboard.display
        d.fill(0)
        d.text('HOLD TO REBOOT', 4, 36, 255)
        d.text('release=cancel', 4, 54, 128)
        d.text('reboot in ' + str(remaining), 4, 78, 255)
        amyboard.display_refresh()
    except Exception:
        pass


def _service_reboot_hold(delta, click):
    # On-device hold-to-reboot, active ONLY at the global menu root. Returns True
    # when it owns the display this tick (caller then skips the menu render).
    global _reboot_arm_ms, _reboot_last_remaining
    at_root = _menu is not None and len(_menu.stack) == 1
    held = _encoder is not None and _encoder._btn_down
    if delta or click or not (at_root and held):
        # Any turn/click/release cancels an in-progress countdown.
        if _reboot_arm_ms is not None:
            _reboot_arm_ms = None
            _reboot_last_remaining = -1
            if _menu is not None:
                _menu.dirty = True
                _menu._needs_clear = True      # repaint the menu over the countdown
        return False
    now = time.ticks_ms()
    if _reboot_arm_ms is None:
        _reboot_arm_ms = now
    elapsed = time.ticks_diff(now, _reboot_arm_ms)
    if elapsed < 900:
        return False                           # ignore the brief hold that opened global
    if elapsed >= REBOOT_HOLD_MS:
        _reboot()
        return True
    _draw_reboot_countdown((REBOOT_HOLD_MS - elapsed + 999) // 1000)
    return True


def loop():
    # Firmware calls this repeatedly (~69 ms measured -- see the header note; any
    # sub-69 ms throttle you add in here will never fire).
    global _reboot_poll
    # Remote reboot request (dropped by a WiFi deploy) -- honored in every mode.
    _reboot_poll += 1
    if _reboot_poll >= REBOOT_POLL_EVERY:
        _reboot_poll = 0
        if _check_reboot_request():
            return
    if _overlay:
        # Global menu is up over a paused (but still-sounding) sketch.
        delta, click, hold = _encoder.update()
        if _service_reboot_hold(delta, click):
            return
        if delta or click or hold:
            _menu.update(delta, click, hold)
        _menu.render()
    elif _mode == 'sketch':
        # The launcher is the sole encoder reader. Translate a hold into either
        # "back out one of the sketch's own levels" or, once the sketch is at
        # playing, "open the global overlay". delta/click/back are handed to the
        # sketch through the injected `launcher` object.
        delta, click, hold = _encoder.update()
        back = False
        if hold:
            # Hold ladder (auto-repeats every HOLD_MS of continuous press):
            #   depth >= 2 (submenu): pop one level back toward the root menu.
            #   depth == 1 (root menu): open the GLOBAL menu (no "playing" stop,
            #                           so it never flashes the display mode).
            #   depth == 0 (playing): OPEN the sketch's own menu (delivered as a
            #                         click). Keep holding and the next tick -- now
            #                         at depth 1 -- escalates to global. So from
            #                         idle, turn/click/hold all reach the sketch
            #                         menu, and only a longer hold reaches global.
            if _api.menu_depth >= 2:
                back = True
            elif _api.menu_depth >= 1:
                _open_overlay()
                return
            else:
                click = True                  # playing -> open the sketch menu
        _api.delta = delta
        _api.click = click
        _api.back = back
        if _sketch_loop:
            _sketch_loop()
        # Clear one-shot events so the sketch never re-consumes them next tick.
        _api.delta = 0
        _api.click = False
        _api.back = False
    else:
        # Cold-boot menu (no sketch resident).
        delta, click, hold = _encoder.update()
        if _service_reboot_hold(delta, click):
            return
        if delta or click or hold:
            _menu.update(delta, click, hold)
        _menu.render()
