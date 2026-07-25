# AMYboard Sketch
# DESCRIPTION: 12-slot one-shot sample trigger box. Each slot holds a WAV loaded
#   on the board from /sd/samples (browsed folder-by-folder on the encoder) into
#   PSRAM, and fires on a MIDI channel 11 note-on at its assigned pitch (MIDI note
#   48, shown as C2, + slot by default). Built for short IDM-style percussion --
#   zaps, blips, clicks -- and retriggering a slot chokes and restarts it. Sample
#   length is bounded only by free memory at load time, not a fixed cap. No
#   sequencer, no patterns: notes in, samples out.
#
#   ENGINE: native routing (see drumkit.py). Every loaded slot becomes one PCM
#   oscillator inside a single-voice user patch; that patch loads on synth 11 with
#   grab_midi_notes=1, and a per-channel note map routes each slot's note to its
#   osc IN AMY'S C FIRMWARE. So a note-on turns into sound with no MicroPython in
#   the trigger path -- tight, jitter-free timing (proven on hardware). Nothing in
#   this sketch is in the trigger path at all -- not even a MIDI callback.
#   rebuild_engine() re-lays the patch + note map whenever slots change, entirely
#   off the trigger path. Channel 11, NOT 10: channel 10 is AMY's built-in GM drum
#   kit and would ignore our patch.
#
#   SD CARD: put your WAVs in a /samples folder at the root of a microSD card
#   (it appears to the board as /sd/samples) and drop the card in. Subfolders
#   are browsable. The card MUST be formatted FAT32 -- the firmware has no exFAT
#   support, and an exFAT card fails to mount SILENTLY, so /sd simply never
#   appears. With no usable card the sketch falls back to /user/samples in
#   internal flash, which works but is capped at ~2.19 MiB for the whole library.
#
#   LOADING is done the ROBUST way: read the whole WAV into memory (retrying on
#   the SD's intermittent EIO dropouts), parse it, then hand raw PCM to
#   load_sample_bytes -- AMY never touches the card, so a card hiccup can't wedge
#   the engine. AMY keeps only the LEFT channel of a stereo file, so samples are
#   effectively mono; a stereo WAV works but costs double the flash. 16-bit PCM
#   only -- AMY's header check does NOT verify bit depth, so an 8/24-bit file
#   would load "successfully" and play as noise; parse_wav_full() screens for that.

import amy, amyboard, time, json, os, gc

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
MIDI_CHANNEL = 11        # 1-based, as printed on hardware; the status byte
                         # carries MIDI_CHANNEL - 1 (= 10). Channel 11, NOT 10:
                         # channel 10 is AMY's built-in GM drum kit and IGNORES a
                         # user patch loaded over it (you get the stock 808 +
                         # double-triggers). We load our kit as synth 11 and let
                         # AMY route channel-11 notes to it natively.
SYNTH        = MIDI_CHANNEL   # AMY synth number == the MIDI channel we answer on
NUM_SLOTS    = 12        # pads; slot i -> MIDI note base_note + i

# NATIVE ROUTING (from drumkit.py). Unlike the old build, notes are NOT fired
# from a Python callback (the jitter path). Instead every loaded slot becomes one
# PCM oscillator inside a single-voice USER PATCH, that patch loads on synth 11
# with grab_midi_notes=1, and a per-channel note map routes each slot's note to
# its osc IN AMY'S C FIRMWARE. Note-on -> sound with no MicroPython in between, so
# timing is tight and jitter-free. rebuild_engine() (re)builds the patch + map
# whenever the slot assignments change -- off the trigger path, so it never costs
# latency. One osc per slot still gives choke-and-restart for free.
KIT_PATCH   = 1024       # user-patch slot for our kit (built-in patches end at 390)

# Sample preset numbers. AMY's built-in ROM PCM lives low (the drum kits are
# 384+), so user samples start well above it; an overlapping number would
# shadow a built-in until unloaded. Presets and patches are separate namespaces.
PRESET_BASE = 1000       # slot i -> memory-PCM preset 1000 + i

# Per-slot gain in the note map's velocity scale (5.0 = unity, like stock kits).
GAIN        = 5.0

# Every sample is loaded declaring this as its native note, and the note map
# plays its osc at this same note. AMY resamples by the DIFFERENCE between the
# played note and the sample's declared note, so playing the declared note is
# exactly 1.0x -- native pitch, no resampling. Pinning both ends to one constant
# makes that guaranteed rather than incidental.
NATIVE_NOTE = 60

READ_TRIES  = 6          # robust-read attempts per WAV (re-mount the SD between)
LOAD_SETTLE_MS = 1200    # wait this long after boot before the first SD load, so
                         # touching the card can't knock USB-MIDI offline mid-enum

# No fixed length cap (user preference): the real ceiling is the MicroPython heap,
# not PSRAM. robust_read pulls the WHOLE file into the MP heap (~2 MiB fixed)
# before load_sample_bytes copies it to PSRAM, and parsing/left-channel extraction
# allocate more on top -- so a file too big for the heap right now would OOM-crash
# mid-read. load_slot() guards against that DYNAMICALLY: it refuses (with a clear
# '!BIG') any file whose size, times this safety factor plus a reserve, exceeds
# gc.mem_free() at that moment. So the limit floats with how much you've already
# loaded instead of being a magic number, and shrinks as pads fill.
LOAD_HEAP_FACTOR  = 3          # reserve ~3x the file size (file + parsed copy +
                               #   mono copy + load_sample_bytes' own buffers)
LOAD_HEAP_RESERVE = 131072     # ...and keep at least this much heap free besides
_max_heap = 0                  # best-case free MP heap, captured once at boot (set
                               #   below). Loaded samples live in PSRAM, not the MP
                               #   heap, so this stays ~constant -- it's the ceiling
                               #   on how big a single file can EVER be to load.

# PSRAM sample-space budget. gc.mem_free() reports only the MicroPython heap, never
# sample RAM, so we can't ask the firmware how much PSRAM is left -- we keep our own
# running tally (slot_bytes) against this estimate instead. The board is N16R8 (8 MB
# PSRAM) with ~4 MB free for samples after the fixed 2 MB MP heap; we budget a
# conservative 3.5 MB so the "wont fit now" warning fires a little EARLY rather than
# letting a load fail. A slightly-off estimate is safe either way: an over-full load
# still fails gracefully with a FAILED toast, never a crash.
PSRAM_SAMPLE_BUDGET = 3_500_000

# Base MIDI note = slot 0. Default 48 = C2 in the convention where middle C
# (60) is C3; slots then run C2, C#2, D2, D#2, E2, F2. Controllers disagree
# about octave numbering, so this is menu-selectable -- if the box appears dead
# but MIDI is arriving, this is the first thing to check.
DEFAULT_BASE_NOTE = 48
base_note = _settings.get('base_note', DEFAULT_BASE_NOTE)

NOTE_NAMES = ('C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B')


def note_name(n):
    # MIDI note number -> name, using the middle-C-is-C3 convention (60 = C3)
    # that Ableton, Logic and Roland gear use -- so what the box displays matches
    # what the controller driving it calls the same note. Labels only: the slot
    # mapping is by MIDI note number and is unaffected by this.
    return '%s%d' % (NOTE_NAMES[n % 12], (n // 12) - 2)


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

# No fixed length cap: sample length is limited dynamically by free memory at load
# time (see the LOAD_HEAP_* constants and load_slot's heap guard), not by a magic
# number here. Longer files just mean a longer LOADING screen and less room left
# for other pads. The one real ceiling is the MicroPython heap during the read.

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


SD_BROWSE_TRIES = 4      # bounded re-mount+retry for browser SD ops (see below)


def sample_root():
    # Where the browser starts. Resolved on every browse rather than cached at
    # boot, so swapping the card doesn't require a reboot.
    #
    # ROBUST like the load path: the card drops out intermittently (EIO), and a
    # single failed check would wrongly conclude "no /sd/samples" and fall back to
    # empty flash -- exactly the "browser can't see the card mid-session" bug.
    # So we re-mount + retry a BOUNDED number of times (a boot-storm of mounts
    # hard-hangs the board; a few user-initiated retries are the proven-safe
    # envelope robust_read already uses). We only re-mount on a FAILED check, so a
    # healthy card returns on the first pass with no extra mounting.
    for _attempt in range(SD_BROWSE_TRIES):
        if _is_dir(SD_SAMPLE_DIR):
            return SD_SAMPLE_DIR
        try:
            amyboard.mount_sd()
        except Exception:
            pass
        time.sleep(0.1)
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
    # No fixed duration cap (user preference): length is bounded dynamically by
    # free memory at load time, checked in load_slot(), not by a magic number
    # here. A too-big-to-load-right-now file is reported as '!BIG' then.
    return None


def sample_secs(info):
    try:
        return info[3] / info[1]
    except Exception:
        return 0.0


def est_load_bytes(info):
    # Approximate the bytes the raw file occupies in the MP heap during load: the
    # PCM data chunk (frames x channels x bytes-per-sample), ~= file size less the
    # header. Computed from the header we already read -- no extra SD access.
    try:
        channels, samplerate, bits, frames = info
        return frames * channels * (bits // 8)
    except Exception:
        return 0


def est_psram_bytes(info):
    # Bytes the sample will occupy in PSRAM once loaded: MONO PCM (AMY keeps only
    # the left channel), so frames x bytes-per-sample regardless of channel count.
    # This is the figure the PSRAM budget / '~' warning compares against -- distinct
    # from est_load_bytes (the full stereo file size that must fit the read heap).
    try:
        channels, samplerate, bits, frames = info
        return frames * (bits // 8)
    except Exception:
        return 0


def too_big_ever(info):
    # True if this file could NEVER load, even on a freshly booted board with
    # nothing else resident: its raw size needs more MP heap (with the same safety
    # factor load_slot uses) than the board's best-case free heap. The browser
    # marks these '[x]' and makes them unclickable, but still SHOWS them -- a file
    # that is simply too large should not silently vanish from the card. Distinct
    # from the dynamic '!BIG' at load time, which means "too big RIGHT NOW" (clear
    # some pads and it may fit).
    if not _max_heap:
        return False
    est = est_load_bytes(info)
    return est > 0 and est * LOAD_HEAP_FACTOR + LOAD_HEAP_RESERVE > _max_heap


def list_dir(path):
    # (folders, wavs) in `path`, each sorted, wavs as (name, info, problem).
    # Anything unreadable is skipped rather than raising -- a half-transferred
    # file shouldn't take the browser down.
    #
    # ROBUST like sample_root(): os.listdir raises OSError (EIO) when the card has
    # dropped, so a single try would show an existing folder as empty. Re-mount +
    # retry, bounded, before giving up. Only the DIRECTORY listing is retried this
    # way; per-file header reads below stay single-try (a boot-storm of mounts
    # inside the file loop is the pattern that hard-hangs the board).
    folders = []
    wavs = []
    names = None
    for _attempt in range(SD_BROWSE_TRIES):
        try:
            names = os.listdir(path)
            break
        except Exception:
            try:
                amyboard.mount_sd()
            except Exception:
                pass
            time.sleep(0.1)
    if names is None:
        return ([], [])
    for name in names:
        # Hide OS bookkeeping: dotfiles (.DS_Store, ._AppleDouble resource forks,
        # .Spotlight-V100, .Trashes, .fseventsd) and Windows' "System Volume
        # Information". These are never samples and just clutter the browser.
        if name.startswith('.') or name == 'System Volume Information':
            continue
        full = path + '/' + name
        if _is_dir(full):
            folders.append(name)
        elif name.lower().endswith('.wav'):
            info = _wav_info(full)
            wavs.append((name, info, sample_problem(info)))
    folders.sort()
    wavs.sort()
    return (folders, wavs)


# --- Robust in-memory load path (from drumkit.py) ---------------------------
# The browser above reads WAV *headers* off the card (cheap, safe -- a plain
# open()/read(), never through AMY). LOADING a sample is the dangerous part, so
# it goes through these instead of amy.load_sample: read the whole file into
# memory (retrying on the SD's EIO dropouts), parse it ourselves, then load raw
# PCM via load_sample_bytes so AMY never touches the card. See load_slot().
def robust_read(path):
    # Read the WHOLE file into memory, re-mounting + retrying on EIO. Returns the
    # complete bytes or None. One f.read() per attempt -- no per-chunk SD contact.
    for _attempt in range(READ_TRIES):
        try:
            with open(path, 'rb') as f:
                return f.read()
        except Exception:
            try:
                amyboard.mount_sd()
            except Exception:
                pass
            time.sleep(0.1)
    return None


def parse_wav_full(data):
    # Parse an in-memory WAV. Returns (channels, samplerate, bits, frames, pcm)
    # or None. Same screening as _wav_info but also hands back the PCM data chunk,
    # so load_slot doesn't re-read the file.
    if not data or len(data) < 12 or data[0:4] != b'RIFF' or data[8:12] != b'WAVE':
        return None
    channels = samplerate = bits = 0
    fmt_ok = False
    i = 12
    while i + 8 <= len(data):
        cid = data[i:i + 4]
        csize = _u32(data, i + 4)
        body = data[i + 8:i + 8 + csize]
        if cid == b'fmt ':
            if len(body) < 16:
                return None
            fmt_ok = (_u16(body, 0) == 1)      # 1 = uncompressed PCM
            channels = _u16(body, 2)
            samplerate = _u32(body, 4)
            bits = _u16(body, 14)
        elif cid == b'data':
            if not (fmt_ok and channels and samplerate and bits):
                return None
            frames = len(body) // (channels * (bits // 8)) if bits else 0
            return (channels, samplerate, bits, frames, body)
        i += 8 + csize + (csize & 1)           # chunks are word-aligned
    return None


def _left_channel(pcm):
    # AMY plays a single (left) channel; de-interleave 16-bit stereo to mono.
    return b''.join(pcm[k:k + 2] for k in range(0, len(pcm) - 3, 4))


# ---------------------------------------------------------------------------
# Slots. slot_paths[i] is the sample file loaded into slot i (or None). Loading
# is the only expensive operation in this sketch, so it is deliberately never
# done from a MIDI callback or mid-render -- only from a menu selection or the
# deferred boot restore below.
# ---------------------------------------------------------------------------
slot_paths = [None] * NUM_SLOTS
slot_info = [None] * NUM_SLOTS      # cached _wav_info per loaded slot
slot_bytes = [0] * NUM_SLOTS        # PSRAM bytes the loaded sample occupies (mono
                                    #   PCM). Summed as our own PSRAM tally, since
                                    #   gc.mem_free() can't see sample RAM.


def psram_used():
    return sum(slot_bytes)


def psram_free(exclude_slot=None):
    # Estimated PSRAM sample space left. exclude_slot credits back a pad we're
    # about to overwrite (its old sample is unloaded before the new one loads).
    used = psram_used()
    if exclude_slot is not None:
        used -= slot_bytes[exclude_slot]
    return PSRAM_SAMPLE_BUDGET - used


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
    # Free the slot's PSRAM preset and forget the sample. Called before every load
    # so replacing a sample doesn't leak the old one -- presets are a malloc'd
    # list, so an unreplaced preset stays resident for the life of the sketch.
    # NOTE: unload_sample takes the PRESET number as its `patch=` argument (an AMY
    # API quirk -- the name is misleading). The caller rebuilds the engine after.
    try:
        amy.unload_sample(patch=PRESET_BASE + i)
    except Exception:
        pass
    slot_paths[i] = None
    slot_info[i] = None
    slot_bytes[i] = 0


def load_slot(i, path):
    # Load `path` into slot i, then rebuild the native kit. Returns None on
    # success or a short reason string.
    #
    # ROBUST LOAD (from drumkit.py, NOT the old amy.load_sample). We read the WHOLE
    # WAV into memory ourselves -- retrying + re-mounting on the SD's intermittent
    # EIO dropouts -- then hand raw PCM to load_sample_bytes, which never touches
    # the card. The old path streamed the file THROUGH AMY's transfer protocol, so
    # a mid-transfer EIO WEDGED the engine (every later send silently no-oped, kit
    # went silent). This path can't: a card hiccup is confined to our read loop.
    #
    # This BLOCKS for the read + base64 transfer, which is why every caller paints
    # a "LOADING" screen first. Never call it from loop()'s render path.
    #
    # HEAP GUARD (dynamic length limit): reading the whole file needs it to fit in
    # the MicroPython heap several times over (raw file + parsed copy + mono copy +
    # load_sample_bytes' buffers). Check the file size against free heap FIRST and
    # bail with '!BIG' before the read, so a too-large file is a clean refusal
    # rather than an OOM crash mid-read. gc.collect() first to measure true free.
    try:
        size = os.stat(path)[6]
    except Exception:
        size = 0
    gc.collect()
    if size and size * LOAD_HEAP_FACTOR + LOAD_HEAP_RESERVE > gc.mem_free():
        return 'BIG'
    data = robust_read(path)
    parsed = parse_wav_full(data)        # (channels, samplerate, bits, frames, pcm)
    if parsed is None:
        return 'BAD'
    channels, sr, bits, frames, pcm = parsed
    problem = sample_problem((channels, sr, bits, frames))
    if problem:
        return problem
    if channels == 2:
        pcm = _left_channel(pcm)
    data = None                          # free the raw file bytes before the copy
    gc.collect()                         #   to PSRAM; the mono `pcm` is all we need
    # PSRAM budget guard: the mono PCM is what actually lands in sample RAM. If it
    # won't fit the remaining budget (crediting back this pad's current sample,
    # which we're about to unload), refuse with 'FULL' -- the clean version of the
    # 'ERR' load_sample_bytes would otherwise throw. The browser's '~' marker warns
    # before you get here; this is the backstop.
    pcm_len = len(pcm)
    if pcm_len > psram_free(exclude_slot=i):
        return 'FULL'
    unload_slot(i)
    try:
        amy.load_sample_bytes(pcm, preset=PRESET_BASE + i,
                              midinote=NATIVE_NOTE, sr=sr)
    except Exception as e:
        print('load_sample_bytes failed:', path, e)
        return 'ERR'
    slot_paths[i] = path
    slot_info[i] = (channels, sr, bits, frames)
    slot_bytes[i] = pcm_len
    rebuild_engine()
    return None


def rebuild_engine():
    # (Re)build the native kit from the current slot assignments. Every LOADED slot
    # becomes one PCM osc in a single-voice user patch; the patch loads on synth 11
    # with grab_midi_notes=1; a full-range note map routes each slot's note to its
    # osc at unity gain and silences (gain 0) every other note so a stray note-on
    # can't ring the melodic voice forever under synth_flags=3. All off the trigger
    # path -- called only after a load / clear / base-note change.
    loaded = [i for i in range(NUM_SLOTS) if slot_paths[i] is not None]
    n = len(loaded)
    if n == 0:
        # Empty kit: a zero-voice synth can't allocate a note, so nothing sounds.
        try:
            amy.send(synth=SYNTH, num_voices=0)
        except Exception:
            pass
        return

    # one PCM osc per loaded slot in one voice; synth_flags=3 = notes-via-MIDI +
    # ring-out (ignore note-offs, so one-shots play to their end).
    bank = amy.message(num_voices=1, oscs_per_voice=n, synth_flags=3)
    osc_of = {}
    for k, i in enumerate(loaded):
        bank += amy.message(osc=k, wave=amy.PCM, preset=PRESET_BASE + i)
        osc_of[slot_note(i)] = k
    amy.send(patch=KIT_PATCH, patch_string=bank)
    amy.send(synth=SYNTH, num_voices=1, patch=KIT_PATCH,
             synth_flags=3, grab_midi_notes=1)

    # full-range map: loaded slots -> their osc at unity gain; all else silent.
    for note in range(128):
        if note in osc_of:
            amy.send(synth=SYNTH,
                     midi_note_cmd="%d,0,0,%s,0," % (note, GAIN) +
                     amy.message(synth='%i', osc=osc_of[note],
                                 note=NATIVE_NOTE, vel='%v'))
        else:
            amy.send(synth=SYNTH,
                     midi_note_cmd="%d,0,0,0,0," % note +
                     amy.message(synth='%i', osc=0, note=NATIVE_NOTE, vel='%v'))


def init_engine():
    # Silence every AMY synth first (the firmware boots a default Juno-6 on synth
    # 1, and would auto-route other channels too), then build our -- initially
    # empty -- kit on synth 11. rebuild_engine() re-arms synth 11 each time slots
    # change; here it just leaves synth 11 at zero voices until samples load.
    for s in range(1, 17):
        try:
            amy.send(synth=s, num_voices=0)
        except Exception:
            pass
    _ensure_sample_dir()
    rebuild_engine()


# ---------------------------------------------------------------------------
# MIDI. Notes are routed to the kit NATIVELY by AMY (grab_midi_notes=1) -- there
# is no MicroPython in the trigger path and no callback registered here. (An
# earlier build timestamped note-ons to flash the on-screen slot as it fired, but
# the panel's ~240ms full refresh can't keep up with even a slow drum part, so the
# flash was dropped; the monitor is now a static slot map, redrawn only on change.)
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
# Playing screen: the slot monitor. Shows all twelve slots as a 6-row x 2-column
# grid (left column = slots 0-5, right = 6-11), each with its trigger note and a
# short sample name. Twelve full-width rows would run off the 128px panel, so the
# grid is how 12 pads fit; the menu's slot list shows the untruncated names. It is
# a STATIC map -- redrawn only when the slot assignments change (a load, clear or
# base-note change marks it dirty) -- so an idle box never blits the panel. There
# is deliberately no per-hit flash: the panel's ~240ms full refresh can't track
# even a slow drum part, so a flash would only ever lag and stutter behind the
# audio.
# ---------------------------------------------------------------------------
MON_TOP_Y    = 18
MON_ROW_H    = 18            # 6 rows: 18,36,..,108 -- last text bottom ~116 < 128
MON_COL_W    = 64            # two 64px columns across the 128px panel
MON_ROWS     = 6            # rows per column (NUM_SLOTS split across 2 columns)


class SlotMonitor:
    def __init__(self):
        self._dirty = True

    def on_activate(self):
        self._dirty = True

    def render(self):
        # Static slot map: redraw only when the assignments changed.
        if not self._dirty:
            return
        self._dirty = False
        try:
            d = amyboard.display
            d.fill(0)
            d.text('TRIGGERBOX', 0, 2, 255)
            d.text('CH%d' % MIDI_CHANNEL,
                   DISPLAY_WIDTH - 4 * CHAR_W, 2, 150)
            for i in range(NUM_SLOTS):
                col = i // MON_ROWS               # 0 = left, 1 = right
                row = i % MON_ROWS
                x = col * MON_COL_W
                y = MON_TOP_Y + row * MON_ROW_H
                c = 200 if slot_paths[i] else 70
                d.text(note_name(slot_note(i)), x, y, c)
                d.text(slot_label(i)[:5], x + 3 * CHAR_W, y, c)
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
    monitor.render()


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


# Page-indicator brightness, matching polysynth's shared grid/menu marks so the two
# sketches read identically. The panel is 4-bit (top nibble): the current page is a
# full-brightness dash, every other page a level-1 dot -- the dimmest still-visible
# step (below ~16 is fully off, which would hide the "another page exists" cue).
GRID_C_HDR_VAL  = 255    # active page: bright dash
GRID_C_PAGE_OFF = 20     # inactive page: dim 2x2 dot


def _draw_page_dots(d, y, page, npages):
    # THE page indicator, ported verbatim from polysynth so the two read the same:
    # current page = a bright full-width DASH, every other page = a dim 2x2 DOT,
    # laid out as a CENTRED row at `y`. Callers guard npages < 2.
    w, h, gap = 5, 2, 4
    off_w = 2               # inactive: a dot, not a short dash
    span = npages * w + (npages - 1) * gap
    x = (DISPLAY_WIDTH - span) // 2
    for i in range(npages):
        if i == page:
            d.fill_rect(x, y, w, h, GRID_C_HDR_VAL)
        else:
            d.fill_rect(x + (w - off_w) // 2, y, off_w, h, GRID_C_PAGE_OFF)
        x += w + gap


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
    #
    # _shown_idx / _shown_start remember what this level last PAINTED, so a scroll
    # within a page can repaint just the two rows whose highlight changed instead
    # of the whole screen. A full-screen flush is 11 bands (~760 ms at one band per
    # ~69 ms tick) -- the dominant term in menu lag; a two-row flush is ~2 bands.
    __slots__ = ('title', 'items', 'idx', 'start', '_shown_idx', '_shown_start')

    def __init__(self, title, items):
        self.title = title
        self.items = items if items else [('(empty)', None)]
        self.idx = 0
        self.start = 0
        self._shown_idx = -1     # nothing painted yet -> first render is full
        self._shown_start = -1

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

    def _row_y(self, row):
        return MENU_TOP_Y + row * MENU_LINE_H

    def _paint_row(self, d, i, start, n):
        # Draw list row `i` into the framebuffer at its on-screen slot. Clears the
        # row first so a shorter label can't leave stale pixels behind.
        y = self._row_y(i - start)
        d.fill_rect(0, y, DISPLAY_WIDTH, MENU_LINE_H, 0)
        if i < n:
            _draw_menu_row(d, y, 'i', (i == self.idx, self.items[i][0]))

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

            # Incremental path: same page as last paint and only the cursor moved.
            # Repaint just the old + new cursor rows and flush that tight band; the
            # title, page dots and untouched rows already stand in the framebuffer.
            if (start == self._shown_start and self._shown_idx >= 0
                    and self._shown_idx != self.idx):
                y_old = self._row_y(self._shown_idx - start)
                y_new = self._row_y(self.idx - start)
                self._paint_row(d, self._shown_idx, start, n)
                self._paint_row(d, self.idx, start, n)
                self._shown_idx = self.idx
                _begin_flush(min(y_old, y_new),
                             max(y_old, y_new) + MENU_LINE_H - 1)
                return

            # Full repaint: first paint of this level, a page crossing, or a
            # non-scroll change (returned from a toast/submenu).
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
            self._shown_idx = self.idx
            self._shown_start = start
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
        # PSRAM left for THIS slot, crediting back whatever it holds now (loading a
        # pad unloads its old sample first). Drives the '~' "wont fit right now"
        # marker below.
        avail = psram_free(exclude_slot=self._browse_slot)
        for name, info, problem in wavs:
            if problem:
                # Show WHY it's unusable, TAG FIRST so the reason survives the row's
                # width truncation (a long filename would otherwise push the tag
                # off-screen and the file just looks inert on click). Still listed,
                # not hidden, so a file you expected to see doesn't just vanish.
                items.append(('!%s %s' % (problem, name), None))
            elif too_big_ever(info):
                # Too large to load even on an empty board: mark '[x]' up front and
                # make it unclickable, but keep it visible so a file that's simply
                # too big doesn't read as "missing from the card".
                items.append(('[x] %s' % name, None))
            elif est_psram_bytes(info) > avail:
                # Fits on an empty board but not in the PSRAM left RIGHT NOW: mark
                # '~' as a soft warning. Still CLICKABLE -- freeing a pad may make
                # room, and if it truly won't fit the load fails cleanly ('FULL').
                items.append(('~%s %.2fs' % (name, sample_secs(info)),
                              self._wav_opener(path + '/' + name)))
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
            rebuild_engine()          # drop the freed osc from the patch + map
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
        # sits on. Each shows the span the twelve slots would cover.
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
            rebuild_engine()          # slot notes moved -> rebuild the note map
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
init_display()

# Best-case free heap, measured once now (empty board, before any sample loads).
# Loaded samples occupy PSRAM, not this heap, so it stays ~constant -- making it a
# stable ceiling for "could this file EVER load" (see too_big_ever). Captured
# before the restore below so it reflects a truly empty board.
gc.collect()
_max_heap = gc.mem_free()

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
_boot_ticks = None      # ticks_ms of the first loop(); gates the SD settle delay


def _service_restore():
    # Load at most ONE saved sample per tick. Returns True while restoring, so
    # loop() can skip the menu/monitor and leave the notice on screen.
    global _boot_ticks
    if not _restore_queue:
        return False
    # Hold off the FIRST SD read until USB has settled: touching the card while
    # USB is still enumerating knocks USB-MIDI (and the serial link) offline
    # (drumkit.py's lesson #2). Only gates the first restore -- once loading has
    # started USB is up.
    now = time.ticks_ms()
    if _boot_ticks is None:
        _boot_ticks = now
    if time.ticks_diff(now, _boot_ticks) < LOAD_SETTLE_MS:
        return True                    # still settling; keep the splash, block menu
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
