# AMYboard Sketch
# DESCRIPTION: MIDI drum kit on channel 11. Loads one WAV per folder from
#   /sd/samples into a user patch (1024), one PCM osc per sample, played on synth
#   11 like a stock kit. Channel-11 note-ons route to the oscs in AMY's C firmware
#   -- no Python in the trigger path, no jitter. Adjacent notes from C2 (48).
#
#   FOUR hard-won design choices (2026-07-20 debugging), each proven on hardware:
#
#   1. load_sample_bytes, NOT load_sample. The SD card intermittently drops out
#      (Errno 5 EIO). load_sample streams the file straight through AMY's transfer
#      protocol, so an EIO mid-transfer WEDGES AMY -- every later command silently
#      fails and nothing plays. Instead we read each WAV fully into memory first
#      (retrying/re-mounting on EIO until we have complete data), then hand raw PCM
#      to load_sample_bytes, which never touches the SD. A card hiccup can't reach
#      the engine.
#
#   2. Defer all SD access to ~1.5 s after boot (from loop()). Touching the card
#      while USB is still enumerating knocks USB-MIDI (and the serial link) offline.
#      Waiting until USB has settled keeps the board a live MIDI target.
#
#   3. Channel 11, not 10. Channel 10 is AMY's built-in GM drum kit and ignores a
#      user patch loaded over it (you get the stock 808 + double-triggers).
#      custom_kit.py uses 11 for the same reason. Point your drum pads at ch 11.
#
#   4. Full-range note map. synth_flags=3 ignores note-offs (one-shots ring out),
#      so a note with no map entry would play the melodic voice forever (a sustained
#      sine, or a pitched copy of a slot). We map all 0-127: real slots to their
#      osc at unity gain, everything else to a gain-0 (silent) command.
#
#   SD: put WAVs in /samples/<name>/ folders at the card root. FAT32, 16-bit PCM.

import amy, amyboard, os, time

SYNTH         = 11          # synth number == MIDI channel the kit answers on
KIT_PATCH     = 1024        # first user-patch slot (built-in patches end at 390)
PRESET_BASE   = 1000        # first memory-PCM preset (>=1000 clears the ROM banks)
NATIVE_NOTE   = 60          # note each sample is loaded at / the map plays on the osc
BASE_NOTE     = 48          # slot i -> MIDI note 48+i (48 = C2)
GAIN          = 5.0         # velocity scale in the note map (5.0 = unity)
MAX_SLOTS     = 12          # one voice can hold this many PCM oscs
MAX_BYTES     = 600000      # skip a WAV whose PCM is larger (keeps RAM/load sane)
READ_TRIES    = 6           # robust-read attempts per file (re-mount between)
SAMPLE_DIR    = '/sd/samples'
LOAD_DELAY_MS = 1500        # wait this long after boot before touching the SD


# --- WAV / SD helpers -------------------------------------------------------
def _u16(b, o):
    return int.from_bytes(b[o:o + 2], 'little')


def _u32(b, o):
    return int.from_bytes(b[o:o + 4], 'little')


def _mount():
    # Re-mount the card to revive it after a dropout. Called only on failure.
    try:
        amyboard.mount_sd()
    except Exception:
        pass


def _listdir(path):
    # List a folder; on failure re-mount once and retry. [] if it still fails.
    try:
        return sorted(os.listdir(path))
    except Exception:
        _mount()
        try:
            return sorted(os.listdir(path))
        except Exception:
            return []


def robust_read(path):
    # Read the WHOLE file into memory, re-mounting and retrying on EIO. Returns the
    # complete bytes or None. One f.read() per attempt -- no per-chunk SD contact,
    # so AMY is never in the read path.
    for attempt in range(READ_TRIES):
        try:
            with open(path, 'rb') as f:
                return f.read()
        except Exception:
            _mount()
            time.sleep(0.1)
    return None


def parse_wav(data):
    # (samplerate, channels, bits, pcm_bytes) or None. Screens for real PCM WAV.
    if not data or len(data) < 12 or data[0:4] != b'RIFF' or data[8:12] != b'WAVE':
        return None
    sr = ch = bits = 0
    fmt_ok = False
    i = 12
    while i + 8 <= len(data):
        cid = data[i:i + 4]
        sz = _u32(data, i + 4)
        body = data[i + 8:i + 8 + sz]
        if cid == b'fmt ':
            if len(body) < 16:
                return None
            fmt_ok = (_u16(body, 0) == 1)      # 1 = uncompressed PCM
            ch = _u16(body, 2)
            sr = _u32(body, 4)
            bits = _u16(body, 14)
        elif cid == b'data':
            if not (fmt_ok and sr and ch and bits):
                return None
            return (sr, ch, bits, body)
        i += 8 + sz + (sz & 1)
    return None


def _left_channel(pcm):
    # AMY plays a single (left) channel; de-interleave 16-bit stereo to mono.
    return b''.join(pcm[k:k + 2] for k in range(0, len(pcm) - 3, 4))


# --- Kit building -----------------------------------------------------------
def find_samples():
    # One (label, path) per subfolder of SAMPLE_DIR: its first .wav.
    out = []
    for sub in _listdir(SAMPLE_DIR):
        if sub.startswith('.'):
            continue
        folder = SAMPLE_DIR + '/' + sub
        for name in _listdir(folder):
            if name.lower().endswith('.wav') and not name.startswith('.'):
                out.append((sub, folder + '/' + name))
                break
        if len(out) >= MAX_SLOTS:
            break
    return out


def load_kit(samples):
    # For each sample: read bytes -> parse -> load_sample_bytes (no SD in AMY's
    # path). Returns the slots that actually loaded, in osc order.
    loaded = []
    for label, path in samples:
        data = robust_read(path)
        info = parse_wav(data)
        if info is None:
            print('skip (unreadable/not 16-bit PCM):', path)
            continue
        sr, ch, bits, pcm = info
        if bits != 16:
            print('skip (%d-bit):' % bits, path)
            continue
        if ch == 2:
            pcm = _left_channel(pcm)
        elif ch != 1:
            print('skip (%d channels):' % ch, path)
            continue
        if len(pcm) > MAX_BYTES:
            print('skip (too big):', path)
            continue
        try:
            amy.load_sample_bytes(pcm, preset=PRESET_BASE + len(loaded),
                                  midinote=NATIVE_NOTE, sr=sr)
            loaded.append((label, path))
        except Exception as e:
            print('load_sample_bytes failed:', path, e)
    n = len(loaded)
    if n == 0:
        return loaded

    # one PCM osc per sample in one voice; synth_flags=3 = notes-via-MIDI + ring-out
    bank = amy.message(num_voices=1, oscs_per_voice=n, synth_flags=3)
    for i in range(n):
        bank += amy.message(osc=i, wave=amy.PCM, preset=PRESET_BASE + i)
    amy.send(patch=KIT_PATCH, patch_string=bank)
    amy.send(synth=SYNTH, num_voices=1, patch=KIT_PATCH, synth_flags=3, grab_midi_notes=1)

    # full-range map: real slots -> osc at unity gain; all else silent (gain 0)
    slot = {}
    for i in range(n):
        slot[BASE_NOTE + i] = i
    for note in range(128):
        osc = slot.get(note, 0)
        gain = GAIN if note in slot else 0
        amy.send(synth=SYNTH,
                 midi_note_cmd="%d,0,0,%s,0," % (note, gain) +
                 amy.message(synth='%i', osc=osc, note=NATIVE_NOTE, vel='%v'))
    return loaded


# --- Display (screen-optional) ----------------------------------------------
def _note_name(nn):
    names = ('C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B')
    return '%s%d' % (names[nn % 12], nn // 12 - 2)   # middle C (60) = C3


def draw(n, msg=None):
    try:
        d = amyboard.display
        d.fill(0)
        d.text('DRUM KIT', 0, 0, 255)
        if msg == 'loading':
            d.text('loading...', 0, 16, 200)
        elif n:
            d.text('CH%d  %d slots' % (SYNTH, n), 0, 16, 255)
            d.text('%s-%s' % (_note_name(BASE_NOTE), _note_name(BASE_NOTE + n - 1)),
                   0, 32, 200)
        else:
            d.text('NO SAMPLES', 0, 16, 255)
            d.text(msg or SAMPLE_DIR, 0, 32, 180)
        d.show()
    except Exception:
        pass


def _run_load():
    loaded = load_kit(find_samples())
    n = len(loaded)
    for i, (label, path) in enumerate(loaded):
        print('slot %d  %s  <- %s' % (i, _note_name(BASE_NOTE + i), path))
    print('drumkit: %d slot(s), channel %d, notes %s-%s' %
          (n, SYNTH, _note_name(BASE_NOTE), _note_name(BASE_NOTE + max(n - 1, 0))))
    draw(n)


# --- Boot: splash only; the kit loads from loop() once USB has settled ------
draw(None, 'loading')
_started = None
_done = False


def loop(*_):
    global _started, _done
    if _done:
        return
    now = time.ticks_ms()
    if _started is None:
        _started = now
        return
    if time.ticks_diff(now, _started) < LOAD_DELAY_MS:
        return
    _done = True          # set before loading so a failure can't retry-storm
    _run_load()
