# AMYboard Sketch  --  SCRATCH DE-RISK PROTOTYPE (not the shipping kit)
# DESCRIPTION: Validates the ONE unproven assumption behind play-modes (Feature 2):
#   that a midi.add_callback can re-point a note-map entry BETWEEN native hits, so
#   the NEXT hit on a pad plays the NEXT sample -- reliably, with tight native
#   firing. If this holds, cyclic / round-robin / random all follow trivially.
#
#   Isolated from every other variable on purpose:
#     * Pool samples are SYNTHESIZED IN MEMORY (low tone / high tone / noise burst)
#       and loaded via load_sample_bytes -- NO SD card, so a card dropout can't
#       confound the result, yet it exercises the exact real path (PSRAM samples ->
#       native routing -> callback re-point).
#     * ONE pad only (test note C2 = 48). Everything else silent.
#
#   HOW IT WORKS
#     Three PCM oscs (0,1,2) live in one voice on synth 11 (grab_midi_notes=1).
#     The note map sends note 48 -> osc `_play_idx`. AMY fires that osc in C when
#     the note-on arrives (tight, no Python in the trigger path). Our callback then
#     advances _play_idx and re-sends ONLY note 48's map entry, so the NEXT hit
#     plays the next osc. Round-robin: 0,1,2,0,1,2...
#
#   WHAT TO LISTEN/WATCH FOR (report back):
#     1. Does each hit cycle low -> high -> noise -> low ... ? (re-point lands)
#     2. Does the timing stay tight / not jittery vs. drumkit.py? (native firing OK)
#     3. Machine-gun the pad fast: does it ever REPEAT a sample (Python didn't
#        re-point in time)? Memory predicts occasional repeats at high speed; the
#        screen's MIN-GAP + any "repeat?" note tells us the practical floor.
#
#   Point your drum pad / keys at MIDI channel 11, note C2 (48). Deploy + activate,
#   then hit that one pad.

import amy, amyboard, midi, time

SYNTH        = 11        # synth number == MIDI channel (11 avoids ch10 stock kit)
KIT_PATCH    = 1024      # user patch slot (built-in patches end at 390)
PRESET_BASE  = 1024      # memory-PCM presets >=1024 clear the ROM sample banks
TEST_NOTE    = 48        # C2 -- the single pad we test
NATIVE_NOTE  = 60        # note each osc is played at (all samples loaded here)
GAIN         = 5.0       # note-map velocity scale (5.0 = unity)
SR           = 22050     # sample rate, matches the Gamma kits
SETUP_MS     = 1200      # settle after boot before building the engine (USB safety)


# --- Synthesize the pool in memory (no SD, deterministic, obviously distinct) ---
def _s16(buf, i, v):
    if v > 32767:
        v = 32767
    elif v < -32768:
        v = -32768
    w = v & 0xFFFF
    buf[2 * i] = w & 0xFF
    buf[2 * i + 1] = (w >> 8) & 0xFF


def gen_square(freq, dur=0.16, amp=11000):
    # A decaying square wave -- clear pitched "beep".
    n = int(SR * dur)
    period = SR / freq
    buf = bytearray(2 * n)
    for i in range(n):
        env = 1.0 - i / n                       # linear decay to silence
        v = amp if (i % period) < (period / 2) else -amp
        _s16(buf, i, int(v * env))
    return buf


def gen_noise(dur=0.16, amp=10000):
    # A decaying noise burst (LCG) -- clearly NOT a tone, so it's unmistakable.
    n = int(SR * dur)
    buf = bytearray(2 * n)
    seed = 22222
    for i in range(n):
        seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        env = 1.0 - i / n
        v = ((seed >> 8) % 65536) - 32768
        _s16(buf, i, int(v * env * amp / 32768))
    return buf


# --- Shared state (written by the callback, read by loop() for the display) -----
POOL_N     = 3           # oscs in the pool
_play_idx  = 0           # osc the NEXT hit will fire (map currently points here)
_hits      = 0           # total note-ons seen on the test pad
_seq       = []          # rolling record of which osc each recent hit fired
_last_hit  = None        # ticks_ms of previous hit
_min_gap   = None        # smallest inter-hit interval seen (ms)
_repeats   = 0           # hits that fired the SAME osc as the previous hit
_ready     = False


def _remap(note, osc):
    # Re-send ONE note-map entry: note -> osc. This is the whole mechanism under
    # test -- a single amy.send, cheap enough to run per-hit from the callback.
    amy.send(synth=SYNTH,
             midi_note_cmd="%d,0,0,%s,0," % (note, GAIN) +
             amy.message(synth='%i', osc=osc, note=NATIVE_NOTE, vel='%v'))


def build_engine():
    # Load the 3 synthesized samples as presets, wrap them in one 3-osc voice,
    # route note 48 -> osc 0, silence every other note, and arm native grabbing.
    pool = [gen_square(160), gen_square(640), gen_noise()]
    for i, pcm in enumerate(pool):
        amy.load_sample_bytes(pcm, preset=PRESET_BASE + i,
                              midinote=NATIVE_NOTE, sr=SR)

    bank = amy.message(num_voices=1, oscs_per_voice=POOL_N, synth_flags=3)
    for i in range(POOL_N):
        bank += amy.message(osc=i, wave=amy.PCM, preset=PRESET_BASE + i)
    amy.send(patch=KIT_PATCH, patch_string=bank)
    amy.send(synth=SYNTH, num_voices=1, patch=KIT_PATCH,
             synth_flags=3, grab_midi_notes=1)

    # Full-range map: the test note -> osc 0; every other note silent (gain 0) so a
    # stray note can't ring the melodic voice forever under synth_flags=3.
    for note in range(128):
        if note == TEST_NOTE:
            _remap(note, 0)
        else:
            amy.send(synth=SYNTH,
                     midi_note_cmd="%d,0,0,0,0," % note +
                     amy.message(synth='%i', osc=0, note=NATIVE_NOTE, vel='%v'))


def midi_cb(m):
    # Runs on every raw MIDI message. For a note-on on the test pad: record which
    # osc just fired (the map's current target), then advance + re-point for the
    # NEXT hit. No audio is fired here -- AMY already fired natively on arrival.
    global _play_idx, _hits, _last_hit, _min_gap, _repeats
    if not _ready or not m or len(m) < 3:
        return
    if (m[0] & 0xF0) != 0x90 or (m[0] & 0x0F) != (SYNTH - 1):
        return
    if m[1] != TEST_NOTE or m[2] == 0:          # wrong pad or note-off
        return

    fired = _play_idx                            # what AMY just played
    now = time.ticks_ms()
    if _last_hit is not None:
        gap = time.ticks_diff(now, _last_hit)
        if _min_gap is None or gap < _min_gap:
            _min_gap = gap
    _last_hit = now
    _hits += 1

    _seq.append(fired)
    if len(_seq) > 8:
        _seq.pop(0)
    if len(_seq) >= 2 and _seq[-1] == _seq[-2]:
        _repeats += 1

    nxt = (fired + 1) % POOL_N                    # round-robin
    _play_idx = nxt
    _remap(TEST_NOTE, nxt)
    print('hit %d  fired osc %d  -> next %d  gap=%s' %
          (_hits, fired, nxt, '-' if _min_gap is None else _min_gap))


# --- Display (screen-optional; drawn only from loop(), never the callback) ------
def draw():
    try:
        d = amyboard.display
        d.fill(0)
        d.text('POOL DE-RISK', 0, 0, 255)
        if not _ready:
            d.text('building...', 0, 16, 200)
        else:
            d.text('CH11  note C2', 0, 16, 200)
            d.text('hits: %d' % _hits, 0, 32, 255)
            names = ('low', 'high', 'nois')
            d.text('seq: ' + ' '.join(names[i] for i in _seq[-4:]), 0, 48, 255)
            d.text('mingap: %s ms' % ('-' if _min_gap is None else _min_gap),
                   0, 64, 200)
            d.text('repeats: %d' % _repeats, 0, 80, 200)
        d.show()
    except Exception:
        pass


# --- Boot: splash, then build the engine once USB has settled -------------------
draw()
_started = None
_built = False


def loop(*_):
    global _started, _built, _ready
    if not _built:
        now = time.ticks_ms()
        if _started is None:
            _started = now
            return
        if time.ticks_diff(now, _started) < SETUP_MS:
            return
        _built = True
        build_engine()
        midi.add_callback(midi_cb)
        _ready = True
        print('pool_derisk ready: hit MIDI ch11 note %d (C2)' % TEST_NOTE)
        draw()
        return
    draw()
