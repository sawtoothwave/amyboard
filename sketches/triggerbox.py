# AMYboard Sketch
# DESCRIPTION: 6-slot one-shot sample trigger box. Each slot holds a WAV loaded
#   from /sd/samples (browsed folder-by-folder on the encoder) into PSRAM, and
#   fires on a MIDI channel 10 note-on at its assigned pitch (C3 + slot by
#   default). Built for short IDM-style percussion -- zaps, blips, clicks --
#   so samples are capped at 3 seconds and retriggering a slot chokes and
#   restarts it. No sequencer, no patterns: notes in, samples out.
#
#   SD CARD: put your WAVs in a /samples folder at the root of a microSD card
#   (it appears to the board as /sd/samples) and drop the card in. Subfolders
#   are browsable. The card MUST be formatted FAT32 -- the firmware has no exFAT
#   support, and an exFAT card fails to mount SILENTLY, so /sd simply never
#   appears. With no usable card the sketch falls back to /user/samples in
#   internal flash, which works but is capped at ~2.19 MiB for the whole library.
#
#   Engine: amy.load_sample() into PSRAM (see docs/SAMPLES.md for the why).
#   AMY keeps only the LEFT channel of a stereo file, so samples are effectively
#   mono; a stereo WAV works but costs double the flash and loads noticeably
#   slower. 16-bit PCM only -- AMY's header check does NOT verify bit depth, so
#   an 8/24-bit file would load "successfully" and play as noise. _wav_info()
#   below screens for that rather than letting it reach the speakers.

import amy, amyboard, midi, time, json, os

# --- Launcher integration ---------------------------------------------------
# Identical contract to polysynth.py: this sketch always talks to a
# "launcher-shaped" input object (the global `launcher`). It CONSUMES abstract
# encoder events -- launcher.delta (detents), launcher.click (short press),
# launcher.back (hold = pop one of our menu levels) -- and REPORTS
# launcher.menu_depth (how deep our own menu is; 0 = playing).
# launcher.repaint is set True after a Resume so we redraw the screen an overlay
# clobbered. Only WHO FILLS that object changes with how we're run:
#
#   Wrapped  -- the global launcher (wrapper_sketch.py) exec's us with a
#               `launcher` injected into our namespace. It is the sole encoder
#               reader and fills the events; once we report depth 0 a further
#               hold escapes out to the GLOBAL menu.
#   Standalone -- run as a plain boot sketch (no wrapper), `launcher` is unbound,
#               so we build our OWN _StandaloneLauncher that reads the Seesaw
#               encoder directly and fills the same events, keeping this file a
#               self-contained, shareable single sketch. It replicates the
#               wrapper's hold-ladder MINUS the global-escape rung: a hold at our
#               root menu does nothing (there is no wrapper to escape to); leave
#               the root via "Resume Playing" or the idle timeout.
#
# Detection is free: `launcher` is defined iff the wrapper injected it.
STANDALONE_SEESAW_ADDR   = 0x36     # Adafruit Seesaw rotary encoder + push button
STANDALONE_BTN_PIN       = 24       # (front-panel I2C; mirrors wrapper_sketch.py)
STANDALONE_HOLD_MS       = 600      # one back-out pop per this much continuous hold
STANDALONE_INVERT_DELTA  = False    # True => turning right scrolls DOWN


class _StandaloneLauncher:
    # Self-contained encoder reader used ONLY when this sketch runs without the
    # wrapper. Presents the same fields the wrapper's injected `launcher` does, so
    # every bit of menu code below is identical in both modes. update() reads the
    # Seesaw encoder + button and fills delta/click/back for _pump_menu() to
    # consume, applying the standalone hold-ladder. All hardware access is guarded
    # so a missing/unplugged encoder (or a screenless board) degrades to "no
    # input" instead of crashing -- the sampler still boots and triggers.
    __slots__ = ('delta', 'click', 'back', 'menu_depth', 'repaint', 'resumed',
                 '_last', '_btn_down', '_down_at', '_hold_count')

    def __init__(self):
        self.delta = 0
        self.click = False
        self.back = False
        self.menu_depth = 0
        self.repaint = False
        self.resumed = False
        try:
            amyboard.init_buttons(pins=(STANDALONE_BTN_PIN,),
                                  seesaw_dev=STANDALONE_SEESAW_ADDR)
        except Exception:
            pass
        self._last = self._count()
        self._btn_down = False
        self._down_at = 0
        self._hold_count = 0     # number of HOLD_MS pops already fired this press

    def _count(self):
        try:
            return amyboard.read_encoder(seesaw_dev=STANDALONE_SEESAW_ADDR)
        except Exception:
            return 0

    def _pressed(self):
        try:
            return bool(amyboard.read_buttons(
                pins=(STANDALONE_BTN_PIN,),
                seesaw_dev=STANDALONE_SEESAW_ADDR)[0])
        except Exception:
            return False

    def update(self):
        # Read the raw encoder, derive (delta, click, hold), then translate the
        # hold into our abstract events via the standalone ladder. Called once per
        # loop() from loop() itself.
        self.delta = 0
        self.click = False
        self.back = False

        c = self._count()
        delta = c - self._last
        self._last = c
        if STANDALONE_INVERT_DELTA:
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
            n = time.ticks_diff(now, self._down_at) // STANDALONE_HOLD_MS
            if n > self._hold_count:
                self._hold_count = n         # one pop per HOLD_MS boundary crossed
                hold = True
        elif (not pressed) and self._btn_down:
            self._btn_down = False
            if self._hold_count == 0:        # released before any hold -> a click
                click = True

        if hold:
            # Standalone hold-ladder (mirrors the wrapper's, minus global escape):
            #   depth >= 2 (submenu): pop one level back toward the root.
            #   depth == 0 (playing): open our menu (delivered as a click).
            #   depth == 1 (root menu): nothing -- there is no wrapper to escape
            #                           to; leave via "Resume Playing" / timeout.
            if self.menu_depth >= 2:
                self.back = True
            elif self.menu_depth == 0:
                click = True

        self.delta = delta
        self.click = click


try:
    launcher
    _STANDALONE = False
except NameError:
    launcher = _StandaloneLauncher()
    _STANDALONE = True


# ---------------------------------------------------------------------------
# Persistent settings. A tiny JSON dict in internal flash (always writable at
# runtime, survives reboot) remembering which sample each slot holds and the
# MIDI base note. Writes happen only on explicit selection -- never per frame --
# so flash wear is a non-issue.
# ---------------------------------------------------------------------------
SETTINGS_FILE = '/user/triggerbox_settings.json'


def _load_settings():
    try:
        with open(SETTINGS_FILE) as f:
            d = json.load(f)
        if isinstance(d, dict):
            return d
    except Exception:
        pass
    return {}


_settings = _load_settings()


def _write_settings():
    # Persist the whole settings dict. Guarded so a flash-write fault never
    # disturbs audio/MIDI -- the settings just won't survive the next reboot.
    try:
        with open(SETTINGS_FILE, 'w') as f:
            json.dump(_settings, f)
    except Exception:
        pass


def _set_setting(key, value):
    _settings[key] = value
    _write_settings()


# ---------------------------------------------------------------------------
# Engine layout
# ---------------------------------------------------------------------------
MIDI_CHANNEL = 10        # 1-based, as printed on hardware; the status byte
                         # carries MIDI_CHANNEL - 1 (= 9). AMY would auto-route
                         # channel 10 to synth 10, but we zero every synth below
                         # and handle note-on ourselves, so nothing else claims
                         # these notes.
NUM_SLOTS    = 6

# One oscillator per slot, high in the range: the firmware's default Juno-6
# synth owns the LOW oscillators at boot, and raw sends there conflict (see
# docs/FIRMWARE_NOTES.md). One osc per slot IS the choke behaviour -- a
# retrigger re-sends note-on to the same oscillator, which restarts playback
# from zero and cuts whatever was still ringing. Nothing to arbitrate.
SLOT_OSC_BASE = 100      # slots occupy osc 100 .. 100 + NUM_SLOTS - 1

# Sample preset numbers. AMY's built-in ROM PCM lives low (the drum kits are
# 384+), so user samples start well above it; an overlapping number would
# shadow a built-in until unloaded.
PRESET_BASE = 1024       # slot i -> preset 1024 + i

# Every sample is loaded declaring this as its native note, and every trigger
# sends this same note. AMY resamples by the DIFFERENCE between the played note
# and the sample's declared note, so playing the declared note is exactly 1.0x
# -- native pitch, no resampling. Pinning both ends to one constant makes that
# guaranteed rather than incidental.
NATIVE_NOTE = 60

# Base MIDI note = slot 0. Default 48 = C3 in the convention where middle C
# (60) is C4; slots then run C3, C#3, D3, D#3, E3, F3. Controllers disagree
# about octave numbering, so this is menu-selectable -- if the box appears dead
# but MIDI is arriving, this is the first thing to check.
DEFAULT_BASE_NOTE = 48
base_note = _settings.get('base_note', DEFAULT_BASE_NOTE)

NOTE_NAMES = ('C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B')


def note_name(n):
    # MIDI note number -> name, using the middle-C-is-C4 convention that matches
    # DEFAULT_BASE_NOTE above.
    return '%s%d' % (NOTE_NAMES[n % 12], (n // 12) - 1)


# ---------------------------------------------------------------------------
# Sample library. Samples are browsed from the SD card (/sd/samples), falling
# back to internal flash (/user/samples) when no usable card is present, and the
# browser walks either one folder at a time.
#
# WHY SD IS THE PRIMARY ROOT: /user is a 2.19 MiB littlefs partition, and every
# sample you want to BROWSE occupies it, not just the six you load. That capped
# the whole library at roughly 13-26 one-second WAVs. An SD card removes that
# ceiling entirely, which leaves PSRAM (~4 MB free, or ~666 KB per slot across
# six) as the only real budget -- and no percussive one-shot comes close to it.
#
# We browse from SD but always LOAD INTO PSRAM (amy.load_sample), never stream
# from the card (amy.disk_sample). Streaming would buy stereo and unlimited
# length, but AMY does its streaming reads inside the audio render path, chunked
# into 64-byte VFS calls (~9 FatFs/SPI round-trips per 256-frame block per
# voice), with an 8196-byte buffer on a 12 KB render-task stack and no exception
# guard on the read hook. Multi-voice streaming from SD is unbenchmarked and
# looks fragile; loading into RAM is the safe route and costs us only stereo,
# which load_sample discards anyway.
# ---------------------------------------------------------------------------
SD_SAMPLE_DIR    = '/sd/samples'      # preferred: put /samples at the card root
FLASH_SAMPLE_DIR = '/user/samples'    # fallback when no usable card is mounted

# Hard cap on sample length. With SD the limit is no longer STORAGE -- it's load
# time. load_sample() base64-chunks the audio through AMY's message parser at
# 188 raw bytes per message, so a 3-second 44.1k sample is ~1400 chunks and
# blocks for the whole transfer. PSRAM would allow ~7 seconds per slot; this cap
# is about how long you're willing to stare at a LOADING screen, not space.
MAX_SAMPLE_SECS = 3.0

# AMY keeps 2 bytes per frame per channel, and de-interleaves stereo down to the
# left channel before sending -- so PSRAM cost is frames * 2 regardless of the
# file's channel count. Flash cost is the file's real size, which stereo doubles.
BYTES_PER_FRAME = 2


def _is_dir(path):
    try:
        return bool(os.stat(path)[0] & 0x4000)    # S_IFDIR
    except Exception:
        return False


def sd_mounted():
    return _is_dir('/sd')


def try_mount_sd():
    # The firmware mounts the card at boot via amyboard.mount_sd(), but that call
    # swallows failures silently -- so a card inserted late, or one that lost its
    # mount, leaves /sd simply absent with no error anywhere. Retrying here is
    # cheap and makes "insert card, reopen the browser" work.
    #
    # It will NOT rescue an exFAT card: the firmware has no exFAT support at all,
    # so the mount fails every time. Reformat as FAT32.
    if sd_mounted():
        return True
    try:
        amyboard.mount_sd()
    except Exception:
        pass
    return sd_mounted()


def sample_root():
    # Where the browser starts. Resolved on every browse rather than cached at
    # boot, so swapping the card doesn't require a reboot.
    if try_mount_sd() and _is_dir(SD_SAMPLE_DIR):
        return SD_SAMPLE_DIR
    return FLASH_SAMPLE_DIR


def _ensure_sample_dir():
    # Create the FLASH fallback only, so there is somewhere obvious to drop files
    # on a card-less board. The SD folder is never created: the card is the
    # user's to organise, and a stray empty /sd/samples would mask "no card" by
    # making the fallback look like it didn't trigger.
    try:
        if not _is_dir(FLASH_SAMPLE_DIR):
            os.mkdir(FLASH_SAMPLE_DIR)
    except Exception:
        pass


def _u32(b, o):
    return int.from_bytes(b[o:o + 4], 'little')


def _u16(b, o):
    return int.from_bytes(b[o:o + 2], 'little')


def _wav_info(path):
    # Parse enough of a WAV header to decide whether AMY can play it correctly.
    # Returns (channels, samplerate, bits, frames) or None if unreadable/not a
    # PCM WAV.
    #
    # Bit depth is the one that matters most: AMY's own header check verifies
    # audio_format == 1 but NEVER checks bits-per-sample, and then computes frame
    # count assuming 16-bit. An 8- or 24-bit file loads "successfully" and plays
    # as garbage, which is a miserable thing to debug by ear. We screen it here.
    try:
        with open(path, 'rb') as f:
            hdr = f.read(12)
            if len(hdr) < 12 or hdr[0:4] != b'RIFF' or hdr[8:12] != b'WAVE':
                return None
            channels = samplerate = bits = 0
            fmt_ok = False
            frames = 0
            while True:
                ch = f.read(8)
                if len(ch) < 8:
                    break
                cid = ch[0:4]
                csize = _u32(ch, 4)
                if cid == b'fmt ':
                    body = f.read(csize)
                    if len(body) < 16:
                        return None
                    fmt_ok = (_u16(body, 0) == 1)     # 1 = uncompressed PCM
                    channels = _u16(body, 2)
                    samplerate = _u32(body, 4)
                    bits = _u16(body, 14)
                elif cid == b'data':
                    if channels and bits:
                        frames = csize // (channels * (bits // 8))
                    break
                else:
                    f.seek(csize + (csize & 1), 1)    # chunks are word-aligned
            if not fmt_ok or not channels or not samplerate or not bits:
                return None
            return (channels, samplerate, bits, frames)
    except Exception:
        return None


def sample_problem(info):
    # Why this file can't be a slot sample, or None if it's fine. Returned as a
    # short tag the browser can show inline, so a rejected file explains itself
    # in the list instead of silently failing on click.
    if info is None:
        return 'BAD'
    channels, samplerate, bits, frames = info
    if bits != 16:
        return '%dBIT' % bits         # AMY assumes 16 and would play noise
    if channels > 2:
        return 'CH%d' % channels
    if not frames or not samplerate:
        return 'EMPTY'
    if frames / samplerate > MAX_SAMPLE_SECS:
        return 'LONG'
    return None


def sample_secs(info):
    try:
        return info[3] / info[1]
    except Exception:
        return 0.0


def list_dir(path):
    # (folders, wavs) in `path`, each sorted, wavs as (name, info, problem).
    # Anything unreadable is skipped rather than raising -- a half-transferred
    # file shouldn't take the browser down.
    folders = []
    wavs = []
    try:
        names = os.listdir(path)
    except Exception:
        return ([], [])
    for name in names:
        full = path + '/' + name
        if _is_dir(full):
            folders.append(name)
        elif name.lower().endswith('.wav'):
            info = _wav_info(full)
            wavs.append((name, info, sample_problem(info)))
    folders.sort()
    wavs.sort()
    return (folders, wavs)


# ---------------------------------------------------------------------------
# Slots. slot_paths[i] is the sample file loaded into slot i (or None). Loading
# is the only expensive operation in this sketch, so it is deliberately never
# done from a MIDI callback or mid-render -- only from a menu selection or the
# deferred boot restore below.
# ---------------------------------------------------------------------------
slot_paths = [None] * NUM_SLOTS
slot_info = [None] * NUM_SLOTS      # cached _wav_info per loaded slot
slot_hits = [0] * NUM_SLOTS         # ticks_ms of the last trigger (for the flash)


def slot_note(i):
    return base_note + i


def slot_label(i):
    # Short display name: the filename without directories or .wav.
    p = slot_paths[i]
    if not p:
        return '-'
    name = p.rsplit('/', 1)[-1]
    if name.lower().endswith('.wav'):
        name = name[:-4]
    return name


def unload_slot(i):
    # Free the slot's PSRAM and silence its oscillator. Called before every load
    # so replacing a sample doesn't leak the old one -- presets are a malloc'd
    # list, so an unreplaced preset stays resident for the life of the sketch.
    try:
        amy.send(osc=SLOT_OSC_BASE + i, vel=0)
    except Exception:
        pass
    try:
        amy.unload_sample(patch=PRESET_BASE + i)
    except Exception:
        pass
    slot_paths[i] = None
    slot_info[i] = None


def load_slot(i, path):
    # Load `path` into slot i. Returns None on success or a short reason string.
    #
    # This BLOCKS for as long as the transfer takes (hundreds of chunks for a
    # 1-second sample), which is why every caller paints a "LOADING" screen
    # first. Callers must not invoke this from loop()'s render path.
    info = _wav_info(path)
    problem = sample_problem(info)
    if problem:
        return problem
    unload_slot(i)
    try:
        amy.load_sample(path, preset=PRESET_BASE + i, midinote=NATIVE_NOTE)
    except Exception as e:
        print('load_sample failed:', path, e)
        return 'ERR'
    slot_paths[i] = path
    slot_info[i] = info
    return None


def trigger_slot(i, velocity):
    # Fire slot i. One oscillator per slot means this inherently chokes: the
    # note-on restarts the same oscillator from frame zero, cutting whatever was
    # still sounding. Sending NATIVE_NOTE (the note the sample declared at load)
    # plays it back at exactly its recorded pitch.
    if slot_paths[i] is None:
        return
    try:
        amy.send(osc=SLOT_OSC_BASE + i, wave=amy.PCM, preset=PRESET_BASE + i,
                 note=NATIVE_NOTE, vel=velocity)
        slot_hits[i] = time.ticks_ms()
    except Exception:
        pass


def init_engine():
    # Silence every AMY synth. The firmware allocates a default Juno-6 instrument
    # on synth 1 (MIDI channel 1) at boot, and AMY would otherwise auto-route
    # channel-10 notes to synth 10 as well. A synth with zero voices can't
    # allocate a note, so all sixteen stay quiet and our own callback is the only
    # thing that responds to incoming notes.
    for s in range(1, 17):
        try:
            amy.send(synth=s, num_voices=0)
        except Exception:
            pass
    _ensure_sample_dir()


# ---------------------------------------------------------------------------
# MIDI. AMY routes nothing to us (every synth is silenced above), so this
# callback IS the instrument: it turns channel-10 note-ons into slot triggers.
# Note-offs are ignored on purpose -- these are one-shots, and a short percussive
# hit should ring out on its own rather than being cut by key release.
# ---------------------------------------------------------------------------
def midi_cb(m):
    if not m or len(m) < 3:
        return
    if (m[0] & 0x0F) != (MIDI_CHANNEL - 1):
        return
    if (m[0] & 0xF0) != 0x90:        # note-on only
        return
    if m[2] == 0:                    # running-status note-off
        return
    slot = m[1] - base_note
    if 0 <= slot < NUM_SLOTS:
        trigger_slot(slot, m[2] / 127.0)


def setup_midi():
    midi.add_callback(midi_cb)


# ---------------------------------------------------------------------------
# Display infrastructure. Lifted from polysynth.py: all drawing happens from
# loop() -- never from the MIDI callback -- and is fully wrapped so a display
# fault can never disturb audio/MIDI.
# ---------------------------------------------------------------------------
DISPLAY_WIDTH  = 128
DISPLAY_HEIGHT = 128
BOOT_CLEAR_MS  = 3000         # show the firmware boot banner this long, then wipe
DISPLAY_OK = False
_boot_ms = 0
_boot_cleared = False

CHAR_W = 8                    # framebuf font cell width
CHAR_H = 8                    # framebuf font cell height

_render_faults = set()        # render sites that have already reported once


def _render_fault(where, exc):
    # Report a render failure ONCE per site, then stay quiet. The render paths
    # swallow exceptions so a drawing fault never takes audio down -- but
    # swallowing SILENTLY turns a hard error into a blank screen with no clue.
    # Once-per-site matters: these run inside loop(), so an unconditional print
    # would emit ~14x/second and bury the first, most useful report.
    if where in _render_faults:
        return
    _render_faults.add(where)
    print('RENDER FAULT in %s: %s: %s' % (where, type(exc).__name__, exc))
    try:
        import sys
        sys.print_exception(exc)
    except Exception:
        pass


def init_display():
    global DISPLAY_OK, _boot_ms
    try:
        amyboard.init_display()
    except Exception:
        pass
    try:
        DISPLAY_OK = amyboard.display is not None
    except Exception:
        DISPLAY_OK = False
    _boot_ms = time.ticks_ms()


def _push_rows(y0, y1):
    # Windowed refresh: send only framebuffer rows [y0, y1] to the panel instead
    # of the whole 8KB frame. Only the SSD1327 is handled directly (it lacks a
    # partial show() in firmware); return False for anything else so the caller
    # falls back to a normal full refresh.
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


# Progressive framebuffer flush. A full 128x128 refresh blits 8KB over the
# 400kHz I2C bus (240ms MEASURED) and blocks the single MicroPython thread long
# enough to drop a trigger. These helpers push the framebuffer in bounded row
# BANDS spread across successive loop() calls, so no single refresh exceeds
# ~19ms (a 12-row band) instead of 240ms in one go.
FLUSH_BAND_ROWS = 12         # MEASURED: 12 rows (768B) = 19ms, full 8KB = 240ms
_flush_active = False
_flush_y = 0
_flush_y1 = 127


def _begin_flush(y0, y1):
    global _flush_active, _flush_y, _flush_y1
    _flush_active = True
    _flush_y = max(0, min(127, int(y0)))
    _flush_y1 = max(0, min(127, int(y1)))


def _service_flush():
    # Push one band per call. Returns True while a flush is still in progress so
    # the caller defers its own drawing until the panel is settled.
    global _flush_active, _flush_y
    if not _flush_active:
        return False
    a = _flush_y
    b = min(_flush_y1, a + FLUSH_BAND_ROWS - 1)
    if not _push_rows(a, b):
        # Panel can't do windowed pushes -> one full refresh and give up chunking.
        try:
            amyboard.display_refresh()
        except Exception:
            pass
        _flush_active = False
        return True
    _flush_y = b + 1
    if _flush_y > _flush_y1:
        _flush_active = False
    return True


def _boot_wipe(now):
    # One-time boot wipe: leave the firmware banner up for BOOT_CLEAR_MS, then
    # clear the whole panel once so our output doesn't overprint leftover pixels.
    # Returns True while still booting (caller should not draw yet).
    global _boot_cleared
    if _boot_cleared:
        return False
    if time.ticks_diff(now, _boot_ms) < BOOT_CLEAR_MS:
        return True
    try:
        amyboard.display.fill(0)
        amyboard.display_refresh()
    except Exception:
        pass
    _boot_cleared = True
    return True


def _blocking_notice(line1, line2=''):
    # Paint a centered two-line notice and push it in ONE full refresh rather
    # than progressively. Used only around load_slot(), which blocks anyway --
    # there are no loop() ticks available to service a banded flush, so the
    # progressive path would leave the screen half-drawn for the whole load.
    if not DISPLAY_OK:
        return
    try:
        d = amyboard.display
        d.fill(0)
        w = len(line1) * CHAR_W
        d.text(line1, max(0, (DISPLAY_WIDTH - w) // 2), 56, 255)
        if line2:
            w2 = len(line2) * CHAR_W
            d.text(line2, max(0, (DISPLAY_WIDTH - w2) // 2), 68, 150)
        amyboard.display_refresh()
    except Exception as e:
        _render_fault('_blocking_notice', e)


# ---------------------------------------------------------------------------
# Playing screen: the slot monitor. Shows all six slots, their trigger notes and
# sample names, and flashes a slot briefly when it fires. Redrawn only when
# something changes (a hit starting or a flash expiring), so an idle box is not
# blitting the panel on every tick.
# ---------------------------------------------------------------------------
HIT_FLASH_MS = 120           # how long a triggered slot stays highlighted
MON_TOP_Y    = 20
MON_ROW_H    = 16


class SlotMonitor:
    def __init__(self):
        self._lit = [False] * NUM_SLOTS
        self._dirty = True

    def on_activate(self):
        self._dirty = True

    def render(self, now):
        # Recompute which slots are lit; redraw only if that changed.
        changed = False
        for i in range(NUM_SLOTS):
            lit = bool(slot_hits[i]) and \
                time.ticks_diff(now, slot_hits[i]) < HIT_FLASH_MS
            if lit != self._lit[i]:
                self._lit[i] = lit
                changed = True
        if not (changed or self._dirty):
            return
        self._dirty = False
        try:
            d = amyboard.display
            d.fill(0)
            d.text('TRIGGERBOX', 0, 2, 255)
            d.text('CH%d' % MIDI_CHANNEL,
                   DISPLAY_WIDTH - 3 * CHAR_W, 2, 150)
            for i in range(NUM_SLOTS):
                y = MON_TOP_Y + i * MON_ROW_H
                lit = self._lit[i]
                if lit:
                    d.fill_rect(0, y - 2, DISPLAY_WIDTH, MON_ROW_H - 2, 90)
                col = 255 if lit else (200 if slot_paths[i] else 70)
                d.text(note_name(slot_note(i)), 0, y, col)
                d.text(slot_label(i)[:12], 4 * CHAR_W, y, col)
            _begin_flush(0, 127)
        except Exception as e:
            _render_fault('SlotMonitor.render', e)


monitor = SlotMonitor()


def service_display():
    now = time.ticks_ms()
    if not DISPLAY_OK:
        return
    if _boot_wipe(now):
        return
    if _service_flush():
        return
    monitor.render(now)


# ---------------------------------------------------------------------------
# On-device menu (encoder-driven). Reachable by a short CLICK while playing and
# owns the OLED while open. Follows the launcher's universal rule -- turn =
# scroll, click = select / drill in, hold = back out one level -- with our depth
# reported so a hold past our top level escapes to the GLOBAL menu.
#
# The sample browser needs no level class of its own: descending into a folder
# just pushes another _MenuLevel built from that folder's contents, so "hold to
# go up a folder" falls straight out of the existing pop-one-level ladder.
# ---------------------------------------------------------------------------
MENU_LINE_H   = 12
MENU_TOP_Y    = 18
MENU_VISIBLE  = 8            # visible list rows = one PAGE (see paginated render)
MENU_PAGE_Y   = 116          # bottom row for the page indicator
MENU_LABEL_MAX = 18
MENU_IDLE_MS  = 15000        # auto-close the menu back to the monitor when idle
TOAST_MS      = 1200         # how long a confirmation toast shows


def clamp(value, lo, hi):
    return lo if value < lo else (hi if value > hi else value)


def _draw_page_dots(d, y, page, npages):
    # Page indicator: a bright dash for the current page, dim dots for the rest.
    if npages <= 1:
        return
    w = npages * 6
    x = DISPLAY_WIDTH - w
    for i in range(npages):
        if i == page:
            d.fill_rect(x + i * 6, y, 5, 2, 255)
        else:
            d.fill_rect(x + i * 6 + 2, y, 2, 2, 90)


def _draw_menu_row(d, y, kind, payload):
    # One list row: 't' = title line, 'q' = page indicator, else an item.
    d.fill_rect(0, y, DISPLAY_WIDTH, MENU_LINE_H, 0)
    if kind == 't':
        left, right = payload
        d.text(left, 0, y, 255)
        if right:
            d.text(right, DISPLAY_WIDTH - len(right) * CHAR_W, y, 255)
    elif kind == 'q':
        total, cur = payload
        _draw_page_dots(d, y + (MENU_LINE_H - 2) // 2, cur, total)
    else:
        sel, label = payload
        if sel:
            d.text('>', 0, y, 255)
            d.text(label[:MENU_LABEL_MAX], 12, y, 255)
        else:
            d.text(label[:MENU_LABEL_MAX], 12, y, 110)


class _MenuLevel:
    # A scrollable list of (label, callback). The whole menu -- root, slot list,
    # folder browser, note picker -- is built from this one type; a level's
    # identity is entirely in the items it was constructed with.
    __slots__ = ('title', 'items', 'idx', 'start')

    def __init__(self, title, items):
        self.title = title
        self.items = items if items else [('(empty)', None)]
        self.idx = 0
        self.start = 0

    def handle(self, menu, delta, click, back):
        if back:                 # hold: pop one level (may close the menu)
            menu._pop()
            return
        if delta:
            # List scroll is 1:1 with detents and clamps at the ends.
            self.idx = clamp(self.idx + delta, 0, len(self.items) - 1)
            menu.dirty = True
        if click:
            cb = self.items[self.idx][1]
            if cb:
                cb()
                menu.dirty = True

    def render(self, menu):
        if not menu.dirty:
            return
        menu.dirty = False
        try:
            d = amyboard.display
            n = len(self.items)
            # Pagination: the visible window is a fixed PAGE aligned to page
            # boundaries, so moving within a page leaves the window still and only
            # a page crossing repaints the whole list.
            if n <= MENU_VISIBLE:
                start = 0
                total_pages = 1
                page = 0
            else:
                page = self.idx // MENU_VISIBLE
                start = page * MENU_VISIBLE
                total_pages = (n + MENU_VISIBLE - 1) // MENU_VISIBLE
            self.start = start

            d.fill(0)
            _draw_menu_row(d, 2, 't', (self.title, ''))
            for row in range(MENU_VISIBLE):
                i = start + row
                y = MENU_TOP_Y + row * MENU_LINE_H
                if i >= n:
                    d.fill_rect(0, y, DISPLAY_WIDTH, MENU_LINE_H, 0)
                    continue
                _draw_menu_row(d, y, 'i', (i == self.idx, self.items[i][0]))
            if total_pages > 1:
                _draw_menu_row(d, MENU_PAGE_Y, 'q', (total_pages, page))
            _begin_flush(0, 127)
        except Exception as e:
            _render_fault('_MenuLevel.render', e)


class SketchMenu:
    def __init__(self):
        self.stack = []          # empty => closed (playing)
        self.dirty = False
        self.suspended = False   # idled out: state kept, monitor shown
        self._toast_msg = ''
        self._toast_until = 0
        self._toast_drawn = False
        self._browse_slot = 0    # which slot the open browser is choosing for

    @property
    def is_open(self):
        # "Open" = we own the screen and take input. A suspended menu is NOT open
        # (the monitor shows) but the stack is kept for resume.
        return len(self.stack) > 0 and not self.suspended

    @property
    def depth(self):
        # Reported to the launcher for the hold-ladder. Stays non-zero while
        # suspended so a hold is delivered to us (to resume) rather than escaping.
        return len(self.stack)

    @property
    def cur(self):
        return self.stack[-1]

    def open(self):
        self.stack = [self._root()]
        self.dirty = True

    def close(self):
        self.stack = []
        self.suspended = False

    def suspend(self):
        self.suspended = True

    def resume(self):
        self.suspended = False
        self.dirty = True

    def _push_level(self, lvl):
        self.stack.append(lvl)
        self.dirty = True

    def _pop(self):
        if self.stack:
            self.stack.pop()
        self.dirty = True

    def _show_toast(self, msg):
        self._toast_msg = msg
        self._toast_until = time.ticks_add(time.ticks_ms(), TOAST_MS)
        self._toast_drawn = False

    # -- Menu tree -----------------------------------------------------------

    def _root(self):
        return _MenuLevel('TRIGGERBOX', [
            ('Slots', self._open_slots),
            ('MIDI base note', self._open_base_note),
            ('Resume playing', self.close),
        ])

    def _open_slots(self):
        items = []
        for i in range(NUM_SLOTS):
            items.append(('%s %s' % (note_name(slot_note(i)), slot_label(i)),
                          self._slot_opener(i)))
        self._push_level(_MenuLevel('SLOTS', items))

    def _slot_opener(self, i):
        def go():
            self._browse_slot = i
            self._open_dir(sample_root(), first=True)
        return go

    def _open_dir(self, path, first=False):
        # Build a browser level for one directory. Folders push a deeper level;
        # WAVs load into the slot being edited. A hold pops back up one folder,
        # which is exactly the launcher's back-out gesture -- nothing special.
        folders, wavs = list_dir(path)
        items = []
        if first:
            # Only offered at the top of a browse, where "clear this slot" reads
            # as a slot action rather than a property of some nested folder.
            items.append(('[Clear slot]', self._clear_opener()))
        for name in folders:
            items.append(('/' + name, self._dir_opener(path + '/' + name)))
        for name, info, problem in wavs:
            if problem:
                # Show WHY it's unusable inline. Still listed (not hidden) so a
                # file you expected to see doesn't just vanish.
                items.append(('%s !%s' % (name, problem), None))
            else:
                items.append(('%s %.2fs' % (name, sample_secs(info)),
                              self._wav_opener(path + '/' + name)))
        if len(items) == (1 if first else 0):
            # Say WHY it's empty. "No samples" on a board whose card silently
            # failed to mount is the single most confusing state this sketch can
            # be in, so name the actual cause instead of leaving the user to
            # guess between "wrong folder", "bad card" and "broken sketch".
            if first and path == FLASH_SAMPLE_DIR:
                if not sd_mounted():
                    items.append(('(no SD card)', None))
                    items.append(('FAT32 only', None))
                else:
                    items.append(('(no /sd/samples)', None))
                items.append(('using /user', None))
            else:
                items.append(('(no samples)', None))
        # The top of a browse is titled by the slot you're filling; deeper levels
        # are titled by the folder, so nested folders don't all read "SLOT C3"
        # and leave you unable to tell how far down you are.
        if first:
            title = 'SLOT %s' % note_name(slot_note(self._browse_slot))
        else:
            title = path.rsplit('/', 1)[-1][:MENU_LABEL_MAX].upper()
        self._push_level(_MenuLevel(title, items))

    def _dir_opener(self, path):
        def go():
            self._open_dir(path)
        return go

    def _clear_opener(self):
        def go():
            i = self._browse_slot
            unload_slot(i)
            self._save_slots()
            # Back to the slot list so the cleared slot is visible immediately.
            self._pop()
            self._refresh_slots()
            self._show_toast('SLOT CLEARED')
        return go

    def _wav_opener(self, path):
        def go():
            i = self._browse_slot
            # load_slot() blocks for the whole transfer, so say so first -- with a
            # single full refresh, since no loop() ticks will run to service a
            # progressive one.
            _blocking_notice('LOADING...', path.rsplit('/', 1)[-1][:14])
            problem = load_slot(i, path)
            self._save_slots()
            # Unwind the browser (however deep in subfolders) back to the slot
            # list, then rebuild it so the new sample name shows.
            while len(self.stack) > 2:
                self.stack.pop()
            self._refresh_slots()
            self._show_toast('LOADED' if not problem else 'FAILED: ' + problem)
        return go

    def _refresh_slots(self):
        # Rebuild the slot list in place, preserving the cursor, so a load or
        # clear is reflected without bouncing the user back to the root.
        if len(self.stack) < 2:
            return
        keep = self.stack[-1].idx
        self.stack.pop()
        self._open_slots()
        self.cur.idx = clamp(keep, 0, len(self.cur.items) - 1)

    def _open_base_note(self):
        # Controllers disagree about octave numbering, so rather than a free
        # numeric editor this offers the handful of C's a drum part realistically
        # sits on. Each shows the span the six slots would cover.
        items = []
        for n in (36, 48, 60):
            span = '%s-%s' % (note_name(n), note_name(n + NUM_SLOTS - 1))
            mark = '*' if n == base_note else ' '
            items.append(('%s%s (%s)' % (mark, note_name(n), span),
                          self._base_note_setter(n)))
        self._push_level(_MenuLevel('BASE NOTE', items))

    def _base_note_setter(self, n):
        def go():
            global base_note
            base_note = n
            _set_setting('base_note', n)
            self._pop()
            self._show_toast('BASE ' + note_name(n))
        return go

    def _save_slots(self):
        _set_setting('slots', slot_paths)

    # -- Input / render ------------------------------------------------------

    def handle(self, delta, click, back):
        if self._toast_msg:
            # A toast is showing: any input just dismisses it (it does not also
            # act on the menu underneath).
            if delta or click or back:
                self._toast_msg = ''
                self.dirty = True
            return
        if not self.is_open:
            return
        self.cur.handle(self, delta, click, back)

    def _draw_toast(self, msg):
        try:
            d = amyboard.display
            d.fill(0)
            w = len(msg) * CHAR_W
            d.text(msg, clamp((DISPLAY_WIDTH - w) // 2, 0,
                              max(0, DISPLAY_WIDTH - w)), 60, 255)
            _begin_flush(0, 127)
        except Exception as e:
            _render_fault('_draw_toast', e)

    def render(self):
        # If a progressive repaint is in flight, keep pushing bands and defer any
        # new drawing until the panel is settled.
        if _flush_active:
            _service_flush()
            return
        if self._toast_msg:
            if time.ticks_diff(time.ticks_ms(), self._toast_until) < 0:
                if not self._toast_drawn:
                    self._draw_toast(self._toast_msg)
                    self._toast_drawn = True
                return
            self._toast_msg = ''
            self.dirty = True
        if not self.is_open:
            return
        self.cur.render(self)


menu = SketchMenu()
_prev_menu_open = False
_last_input_ms = 0


def _pump_menu():
    # Feed the launcher's abstract input to the menu, report our depth, and flag
    # a repaint whenever we drop back to playing so the monitor redraws over the
    # menu's leftover pixels. After MENU_IDLE_MS with no input the menu suspends
    # back to the monitor, keeping its stack for resume.
    global _prev_menu_open, _last_input_ms
    now = time.ticks_ms()
    # Returning from the global overlay: close our own menu so Resume always
    # lands on playing, repaint the monitor, and start a fresh idle window.
    if launcher.resumed:
        launcher.resumed = False
        menu.close()
        launcher.repaint = True
        _last_input_ms = now
        _prev_menu_open = False
    have_input = launcher.delta or launcher.click or launcher.back
    if have_input:
        _last_input_ms = now

    if menu.suspended:
        # Idled out: the monitor is showing. ANY input wakes us back to exactly
        # where we were (the waking input just resumes, it doesn't act). While
        # suspended we report depth >= 2 so the launcher delivers a hold to us
        # rather than escaping to the global menu.
        if have_input:
            menu.resume()
        _prev_menu_open = menu.is_open
        launcher.menu_depth = max(2, menu.depth) if menu.suspended else menu.depth
        return

    if menu.is_open:
        menu.handle(launcher.delta, launcher.click, launcher.back)
        if menu.is_open and time.ticks_diff(now, _last_input_ms) >= MENU_IDLE_MS:
            menu.suspend()
            launcher.repaint = True
    elif launcher.click or launcher.delta:   # a click OR a turn opens our menu
        menu.open()
    if _prev_menu_open and not menu.is_open:
        launcher.repaint = True
    _prev_menu_open = menu.is_open
    launcher.menu_depth = max(2, menu.depth) if menu.suspended else menu.depth


# ---------------------------------------------------------------------------
# Boot
# ---------------------------------------------------------------------------
init_engine()
setup_midi()
init_display()

# Restore saved slots LAZILY, one per loop() tick, rather than here at top level.
# Each load_slot() blocks for the whole base64 transfer, so restoring six samples
# inline would stall the launcher for seconds with a frozen screen and look like
# a hang. Draining the queue from loop() keeps boot responsive and lets the
# monitor paint progress as slots fill in.
_restore_queue = []
try:
    saved = _settings.get('slots') or []
    for i in range(min(NUM_SLOTS, len(saved))):
        if saved[i]:
            _restore_queue.append((i, saved[i]))
except Exception:
    _restore_queue = []
_restore_total = len(_restore_queue)


def _service_restore():
    # Load at most ONE saved sample per tick. Returns True while restoring, so
    # loop() can skip the menu/monitor and leave the notice on screen.
    if not _restore_queue:
        return False
    i, path = _restore_queue.pop(0)
    # Progress counts the QUEUE, not NUM_SLOTS -- only saved slots are restored,
    # so a box with two samples saved shows 1/2 and 2/2, not 5/6 and 6/6.
    _blocking_notice('RESTORING...', '%s %d/%d' % (
        note_name(slot_note(i)), _restore_total - len(_restore_queue),
        _restore_total))
    if load_slot(i, path):
        # The file moved, was deleted, or no longer passes validation. Drop it
        # from the saved set so the stale path doesn't retry on every boot.
        slot_paths[i] = None
        _set_setting('slots', slot_paths)
    if not _restore_queue:
        monitor.on_activate()      # repaint the monitor over the notice
    return True


def loop():
    # Standalone (no wrapper): we are the sole encoder reader, so pump our own
    # reader first to fill launcher.delta/.click/.back. Wrapped: the wrapper has
    # already filled those around this call, so skip (and it clears them itself).
    if _STANDALONE:
        launcher.update()

    if _service_restore():
        # Still restoring saved slots: MIDI triggers already work for whatever
        # has loaded, but the encoder is ignored until the queue drains.
        if _STANDALONE:
            launcher.delta = 0
            launcher.click = False
            launcher.back = False
        return

    _pump_menu()

    if menu.is_open:
        menu.render()                # the menu owns the OLED while open
    else:
        # Playing: after returning from our menu or a global Resume, repaint once
        # so the monitor redraws over any leftover menu/overlay pixels.
        if launcher.repaint:
            launcher.repaint = False
            monitor.on_activate()
        # Display last so a display error never blocks audio/MIDI, and vice versa.
        service_display()

    # Standalone: clear the one-shot events so the menu never re-consumes them
    # next tick (the wrapper does this itself after driving a wrapped sketch).
    if _STANDALONE:
        launcher.delta = 0
        launcher.click = False
        launcher.back = False
