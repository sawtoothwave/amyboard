# AMYboard Sketch
# DESCRIPTION: 2-oscillator (A/B) analog-style synth matching the frozen CC map.
#   Stepped musical tuning per osc, 6-way wave buckets (no wavetable/PCM/ALGO),
#   resonant filter with VCF envelope + key tracking, VCA envelope, plus a
#   per-voice LFO routed to pitch, PWM and filter. 6-voice polyphony. MIDI ch12
#   notes (auto-routed to synth 12 by AMY) + CCs (20-32, 40-47, 71, 74, 76-80)
#   handled via midi.add_callback; CV1 1V/oct + CV2 gate.
#   See docs/CC_MAPPING.md for the authoritative control map.

import amy, amyboard, midi, math, time, json

# --- Launcher integration ---------------------------------------------------
# The global launcher (wrapper_sketch.py) exec's this sketch with a `launcher`
# object injected into our namespace. It is the SOLE encoder reader and feeds us
# abstract input events -- launcher.delta (detents), launcher.click (short
# press), launcher.back (hold = pop one of our menu levels) -- while reading
# launcher.menu_depth to know how deep our own menu is (0 = playing); once we
# report depth 0 a further hold escapes out to the GLOBAL menu. launcher.repaint
# is set True after a Resume so we redraw the screen the overlay clobbered.
# If this sketch is ever run WITHOUT the launcher (e.g. straight from the REPL),
# fall back to an inert stub so the synth still boots and plays; only the
# encoder-driven menu goes dark.
try:
    launcher
except NameError:
    class _NoLauncher:
        delta = 0
        click = False
        back = False
        menu_depth = 0
        repaint = False
        resumed = False
    launcher = _NoLauncher()

# --- Persistent settings ----------------------------------------------------
# A tiny JSON dict in internal flash (always writable at runtime, survives
# reboot/reload) remembering user choices like the selected display mode. Writes
# happen only on explicit selection -- never per frame -- so flash wear is a
# non-issue. Stage 3 (MIDI channel) reuses this same store.
SETTINGS_FILE = '/user/polysynth_settings.json'


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


def _set_setting(key, value):
    # Update one key and persist. Guarded so a flash-write fault never disturbs
    # audio/MIDI -- the setting just won't survive the next reboot.
    _settings[key] = value
    try:
        with open(SETTINGS_FILE, 'w') as f:
            json.dump(_settings, f)
    except Exception:
        pass

# AMY maps synth numbers 1-16 to MIDI channels 1-16, so synth 12 receives all
# notes (auto-routed) and is the target for the CC callback below on channel 12.
SYNTH = 12
NUM_VOICES = 6
OSCS_PER_VOICE = 4

# Per-voice oscillator layout. Osc 0 is a SILENT "filter head": AMY sums the
# chained oscillators (A then B) into its buffer, then applies a single shared
# filter to that sum. This is the only way one filter can affect both
# oscillators -- a non-silent head filters only itself and the chained
# oscillators are mixed in afterward (i.e. unfiltered). The VCA (velocity + amp
# envelope) lives on the sounding oscs A and B, not the head, so they fade and
# self-terminate on note-off and can never out-live (and get stranded by) the
# head's reaper. Osc 3 is a per-voice LFO: it is named as the mod_source of the
# head + A + B, so AMY keeps it silent and routes its output to their
# freq/duty/filter_freq 'mod' coefs.
FILT_OSC = 0
OSC_A    = 1
OSC_B    = 2
LFO_OSC  = 3

# ---------------------------------------------------------------------------
# Frozen CC map (docs/CC_MAPPING.md). MIDI channel 12.
# ---------------------------------------------------------------------------
CC_OSC_A_PITCH = 20
CC_OSC_A_WAVE  = 21
CC_OSC_A_DUTY  = 22
CC_OSC_A_LEVEL = 23
CC_OSC_B_PITCH = 24
CC_OSC_B_WAVE  = 25
CC_OSC_B_DUTY  = 26
CC_OSC_B_LEVEL = 27
CC_FLT_ENV_AMT = 30
CC_FLT_TYPE    = 31
CC_KEY_SCALE   = 32
CC_VCF_ATK     = 40
CC_VCF_DEC     = 41
CC_VCF_SUS     = 42
CC_VCF_REL     = 43
CC_VCA_ATK     = 44
CC_VCA_DEC     = 45
CC_VCA_SUS     = 46
CC_VCA_REL     = 47
CC_FLT_RES     = 71
CC_FLT_CUTOFF  = 74
# LFO controls. Freq (76) and pitch/osc depth (77) use the default AMYboard LFO
# CCs; waveshape (78), PWM depth (79), filter depth (80) and the per-oscillator
# amp/tremolo depths (81 osc A, 82 osc B) use spare CCs.
CC_LFO_FREQ    = 76
CC_LFO_PITCH   = 77
CC_LFO_WAVE    = 78
CC_LFO_PWM     = 79
CC_LFO_FILT    = 80
CC_LFO_AMP_A   = 81
CC_LFO_AMP_B   = 82

# Filter type buckets for CC 31 (4 even bands across 0-127).
FILTER_TYPES = [amy.FILTER_LPF24, amy.FILTER_LPF, amy.FILTER_BPF, amy.FILTER_HPF]

# Tuning reference: AMY's freq 'const' is the Hz at MIDI note 69 when the 'note'
# coefficient is 1.0, so const = REF_HZ * 2**(cents/1200) applies a fixed cents
# offset while still tracking the keyboard. Both oscs reference 440 (unison).
REF_HZ = 440.0

# Filter cutoff sweep range (Hz), mapped logarithmically from CC 74.
CUTOFF_MIN_HZ = 30.0
CUTOFF_MAX_HZ = 16000.0

# Filter envelope depth is an EG1 coefficient in octave-ish (logfreq) units,
# where ~1.0 is a few octaves of sweep. Max keeps it musical, not extreme.
FLT_ENV_AMT_MAX = 2.0

# Resonance (AMY range 0.5-16); keep the usable musical span.
RES_MIN = 0.0
RES_MAX = 6.0

# Envelope time range (ms) for ADSR CCs; quadratic for finer control low down.
ENV_TIME_MIN_MS = 1
ENV_TIME_MAX_MS = 5000

# LFO ranges. Rate is logarithmic (CC 76). Depths apply the LFO's bipolar
# output through each target's 'mod' coef: pitch (CC 77) is quadratic in octave
# units so low knob = subtle vibrato; PWM (CC 79) sweeps the pulse duty; filter
# (CC 80) is octave-style depth, matching the filter envelope amount; amp/
# tremolo (CC 81 osc A, CC 82 osc B) is a downward tremolo on each sounding
# osc's amplitude, bounded to that osc's own level (LFO peak = level, trough ->
# 0) and ramped inside the AMY audio engine (no loop()-rate zippering).
LFO_FREQ_MIN_HZ = 0.2
LFO_FREQ_MAX_HZ = 20.0
LFO_PITCH_DEPTH_MAX = 0.5    # octaves (quadratic curve; full = +/- 6 semitones)
LFO_PWM_DEPTH_MAX   = 0.45   # duty modulation depth around the set duty
LFO_FILT_DEPTH_MAX  = 2.0    # octaves (matches FLT_ENV_AMT_MAX)
LFO_AMP_DEPTH_MAX   = 0.5    # tremolo depth (per osc); full ~ -60 dB dip to silence

# CV input
CV_GATE_THRESHOLD = 1.0
CV1_BASE_NOTE = 60

# ---------------------------------------------------------------------------
# Display modes. The OLED (firmware-owned amyboard.display) is driven by a
# pluggable "display mode": exactly one mode is active at a time and owns what
# the screen shows. The first mode is "CC Monitor" (live CC values); more modes
# (e.g. a patch view or settings menu, once the push encoder is installed) can
# be added to DISPLAY_MODES and selected by swapping active_display_mode via
# set_display_mode().
#
# Every mode must respect the same audio-safety rules, because MicroPython runs
# the whole sketch on one thread and a long OLED blit blocks audio + MIDI:
#   (1) the MIDI callback only records state (never draws),
#   (2) loop() drives drawing at a throttled rate (DISPLAY_REFRESH_MS) and only
#       when content changed,
#   (3) drawing pushes ONLY the framebuffer rows that changed -- the SSD1327 has
#       no partial-refresh in firmware, so a full display.show() blits the whole
#       8KB framebuffer over the 400kHz I2C bus (~150-180ms of blocking time);
#       _push_rows() windows it to the changed rows (~1KB / ~5-20ms), and
#       DISPLAY_MAX_ROWS_PER_REFRESH caps rows-per-refresh so a busy screen can
#       never hold the bus long enough to delay a note-off.
# ---------------------------------------------------------------------------
DISPLAY_MAX_LINES   = 6       # rows of CCs shown at once (newest at bottom)
DISPLAY_REFRESH_MS  = 100     # min gap between refreshes (~10 fps cap)
DISPLAY_MAX_ROWS_PER_REFRESH = 2  # cap rows blitted per refresh so a busy screen
                                  # can't hold the I2C bus long enough to delay
                                  # note-offs; extra changed rows wait for the
                                  # next refresh (catches up within a few frames)
CC_EXPIRE_MS        = 6000    # drop a CC from the list this long after last touch
BOOT_CLEAR_MS       = 3000    # show the firmware boot banner this long, then wipe
DISPLAY_LINE_H      = 16      # vertical pixels per row
DISPLAY_TOP_Y       = 4       # y of the first row
DISPLAY_TEXT_COLOR  = 255     # full-brightness grayscale
DISPLAY_WIDTH       = 128     # panel width in pixels

# Short labels (<=7 chars) for the frozen CC map; unknown CCs fall back to "CC".
# Used by the CC Monitor display mode.
CC_LABELS = {
    CC_OSC_A_PITCH: 'A PIT',  CC_OSC_A_WAVE: 'A WAV',
    CC_OSC_A_DUTY:  'A DTY',  CC_OSC_A_LEVEL: 'A LVL',
    CC_OSC_B_PITCH: 'B PIT',  CC_OSC_B_WAVE: 'B WAV',
    CC_OSC_B_DUTY:  'B DTY',  CC_OSC_B_LEVEL: 'B LVL',
    CC_FLT_ENV_AMT: 'F ENV',  CC_FLT_TYPE: 'F TYP',  CC_KEY_SCALE: 'KEY',
    CC_VCF_ATK: 'VCF A',  CC_VCF_DEC: 'VCF D',
    CC_VCF_SUS: 'VCF S',  CC_VCF_REL: 'VCF R',
    CC_VCA_ATK: 'VCA A',  CC_VCA_DEC: 'VCA D',
    CC_VCA_SUS: 'VCA S',  CC_VCA_REL: 'VCA R',
    CC_FLT_RES: 'RES',    CC_FLT_CUTOFF: 'CUTOFF',
    CC_LFO_FREQ: 'LFO HZ',  CC_LFO_PITCH: 'LFO PT',
    CC_LFO_WAVE: 'LFO WV',  CC_LFO_PWM: 'LFO PW',  CC_LFO_FILT: 'LFO FL',
    CC_LFO_AMP_A: 'A TREM', CC_LFO_AMP_B: 'B TREM',
}

# Shared display state (owned by the display dispatcher, not by any one mode).
DISPLAY_OK = False
_display_last_render = 0      # ticks_ms of the last refresh (throttle gate)
_boot_ms = 0                  # ticks_ms at display init; gates the boot-banner wipe
_boot_cleared = False

# ---------------------------------------------------------------------------
# Live patch state (musical defaults; overwritten by incoming CCs)
# ---------------------------------------------------------------------------
a_cents = 0.0
a_wave  = amy.SAW_DOWN
a_duty  = 0.5
a_level = 1.0

b_cents = 0.0
b_wave  = amy.SINE
b_duty  = 0.5
b_level = 0.00

flt_cutoff  = 16000.0
flt_res     = 0.0
flt_type    = amy.FILTER_LPF
flt_env_amt = 0.0
key_scale   = 0.0

vcf_env = {'a': 0, 'd': 350, 's': 0.2, 'r': 300}
vca_env = {'a': 0, 'd': 200, 's': 1, 'r': 350}

# LFO defaults: depths start at 0 so the LFO is inaudible until a knob is moved.
lfo_freq        = 0.0
lfo_wave        = amy.SINE
lfo_pitch_depth = 0.0
lfo_pwm_depth   = 0.0
lfo_filt_depth  = 0.0
lfo_amp_a_depth = 0.0
lfo_amp_b_depth = 0.0

cv_gate_active  = False
cv_current_note = 69


# ---------------------------------------------------------------------------
# CC -> value helpers
# ---------------------------------------------------------------------------
def clamp(value, lo, hi):
    return max(lo, min(hi, value))


def cc_unit(cc):
    return clamp(int(cc), 0, 127) / 127.0


def cc_to_detune_cents(cc):
    # Stepped musical tuning map (docs/CC_MAPPING.md): center dead zone, fine
    # detune wings, then fixed perfect fifths and octaves out to two octaves.
    cc = clamp(int(cc), 0, 127)
    if cc <= 23:
        return -2400.0                              # two octaves down
    if cc <= 39:
        return -1200.0                              # one octave down
    if cc <= 51:
        return -700.0                               # perfect fifth down
    if cc <= 59:
        return -35.0 + (cc - 52) * (34.0 / 7.0)     # fine -35..-1 cents
    if cc <= 68:
        return 0.0                                  # dead zone at reference
    if cc <= 76:
        return 1.0 + (cc - 69) * (34.0 / 7.0)       # fine +1..+35 cents
    if cc <= 88:
        return 700.0                                # perfect fifth up
    if cc <= 104:
        return 1200.0                               # one octave up
    return 2400.0                                   # two octaves up


def cc_to_wave(cc):
    # Equal-width buckets across the six core analog waves.
    cc = clamp(int(cc), 0, 127)
    if cc <= 20:
        return amy.SINE
    if cc <= 41:
        return amy.PULSE
    if cc <= 63:
        return amy.SAW_DOWN
    if cc <= 84:
        return amy.SAW_UP
    if cc <= 105:
        return amy.TRIANGLE
    return amy.NOISE


def cc_to_duty(cc):
    return 0.05 + cc_unit(cc) * 0.90                # 0.05..0.95


def cc_to_cutoff(cc):
    return CUTOFF_MIN_HZ * math.pow(CUTOFF_MAX_HZ / CUTOFF_MIN_HZ, cc_unit(cc))


def cc_to_res(cc):
    return RES_MIN + cc_unit(cc) * (RES_MAX - RES_MIN)


def cc_to_flt_env_amt(cc):
    return cc_unit(cc) * FLT_ENV_AMT_MAX


def cc_to_filter_type(cc):
    cc = clamp(int(cc), 0, 127)
    idx = (cc * len(FILTER_TYPES)) // 128
    return FILTER_TYPES[min(idx, len(FILTER_TYPES) - 1)]


def cc_to_time_ms(cc):
    u = cc_unit(cc)
    return int(ENV_TIME_MIN_MS + (u * u) * (ENV_TIME_MAX_MS - ENV_TIME_MIN_MS))


def cc_to_lfo_freq(cc):
    return LFO_FREQ_MIN_HZ * math.pow(LFO_FREQ_MAX_HZ / LFO_FREQ_MIN_HZ, cc_unit(cc))


def cc_to_lfo_pitch(cc):
    u = cc_unit(cc)
    return (u * u) * LFO_PITCH_DEPTH_MAX           # octaves, quadratic


def cc_to_lfo_pwm(cc):
    return cc_unit(cc) * LFO_PWM_DEPTH_MAX


def cc_to_lfo_filt(cc):
    return cc_unit(cc) * LFO_FILT_DEPTH_MAX


def cc_to_lfo_amp(cc):
    return cc_unit(cc) * LFO_AMP_DEPTH_MAX


def cv_volts_to_midi(volts):
    n = int(round(CV1_BASE_NOTE + volts * 12.0))
    return clamp(n, 0, 127)


# ---------------------------------------------------------------------------
# AMY graph builders
# ---------------------------------------------------------------------------
# Unity pass-through amp for the SILENT filter head: constant 1.0, no velocity
# or envelope (the VCA lives on A/B). map_60dB_to_01f(1.0) == 1.0, so amp == 1.
HEAD_AMP = {'const': 1.0, 'vel': 0, 'eg0': 0}


def osc_freq(cents):
    # const in Hz at note 69, note coef 1.0 -> tracks keyboard with cents offset.
    # 'mod' adds LFO pitch modulation (vibrato) in unit-per-octave depth.
    return {'const': REF_HZ * math.pow(2.0, cents / 1200.0), 'note': 1,
            'mod': lfo_pitch_depth}


def osc_duty(duty):
    # Pulse duty as a constant, plus LFO 'mod' depth for pulse-width modulation.
    return {'const': clamp(duty, 0.0, 1.0), 'mod': lfo_pwm_depth}


def osc_amp(level, mod_depth=0.0):
    # Per-oscillator amp for the sounding oscs A/B. AMY computes the live amp as
    #   amp = const * velocity * eg0 * 10**(3 * mod_depth * lfo)   (lfo in -1..1)
    # so the LFO 'mod' is a *logarithmic* tremolo around the base; left alone its
    # positive half would boost amp far above the set level (up to +/-60 dB at
    # depth 1). To bound the tremolo to the oscillator's own level -- peak never
    # louder than `level` (as if the level knob were full) and trough never below
    # 0 -- we pre-scale the base down by 10**(-3*mod_depth). The LFO peak (lfo=+1)
    # then lands exactly on `level` and the trough ducks toward silence. The mix
    # level (const) is still multiplied by note velocity (vel) and the VCA
    # envelope (eg0 -> bp0), and AMY ramps it at the audio block rate so the
    # tremolo stays perfectly smooth (no loop()-rate stepping).
    base = clamp(level, 0.0, 1.0) * math.pow(10.0, -3.0 * mod_depth)
    return {'const': base, 'vel': 1, 'eg0': 1, 'mod': mod_depth}


def vca_bp():
    # VCA amplitude envelope (EG0) carried by oscs A and B; shapes each osc's mix.
    return '%d,1,%d,%g,%d,0' % (vca_env['a'], vca_env['d'],
                                vca_env['s'], vca_env['r'])


def flt_bp():
    # EG1 filter envelope (peak 1.0; depth set by filter_freq eg1 coefficient).
    return '%d,1,%d,%g,%d,0' % (vcf_env['a'], vcf_env['d'],
                                vcf_env['s'], vcf_env['r'])


def filter_freq_coefs():
    return {'const': flt_cutoff, 'eg1': flt_env_amt, 'note': key_scale,
            'mod': lfo_filt_depth}


def init_synth():
    # Respond on MIDI channel 12 ONLY. AMY auto-routes MIDI channel N to synth N,
    # and the firmware allocates a default instrument on synth 1 (MIDI channel 1)
    # at boot. Since this sketch lives on synth 12, that default channel-1 synth
    # would survive and sound whenever channel-1 notes arrive. Silence every synth
    # except ours by zeroing its voice count -- a synth with no voices cannot
    # allocate an incoming note, so those channels stay quiet.
    for other in range(1, 17):
        if other != SYNTH:
            amy.send(synth=other, num_voices=0)

    # Allocate voices once. AMY auto-routes incoming MIDI channel-12 notes to
    # this synth; note-ons propagate down the chain (head -> A -> B).
    amy.send(synth=SYNTH, num_voices=0)
    amy.send(synth=SYNTH, num_voices=NUM_VOICES, oscs_per_voice=OSCS_PER_VOICE)
    # Belt-and-suspenders: keep synth 12 from grabbing notes on channels that have
    # no dedicated synth, so it only ever responds to its own channel 12.
    amy.send(synth=SYNTH, grab_midi_notes=0)

    # Filter head: SILENT, so A+B sum into its buffer before one shared filter is
    # applied. The head is a unity pass-through (amp=HEAD_AMP, no VCA envelope);
    # it carries only the filter and its EG1 filter envelope (bp1). It naturally
    # falls silent once A+B have faded, so it needs no amp release of its own.
    amy.send(synth=SYNTH, osc=FILT_OSC,
             wave=amy.SILENT,
             amp=HEAD_AMP,
             filter_type=flt_type, filter_freq=filter_freq_coefs(),
             resonance=flt_res,
             bp1=flt_bp(),
             mod_source=LFO_OSC,
             chained_osc=OSC_A)

    # Sounding oscs A and B carry the VCA: velocity + amp envelope (bp0) so they
    # release and self-terminate on note-off.
    amy.send(synth=SYNTH, osc=OSC_A,
             wave=a_wave, freq=osc_freq(a_cents), duty=osc_duty(a_duty),
             amp=osc_amp(a_level, lfo_amp_a_depth), bp0=vca_bp(),
             mod_source=LFO_OSC,
             chained_osc=OSC_B)

    amy.send(synth=SYNTH, osc=OSC_B,
             wave=b_wave, freq=osc_freq(b_cents), duty=osc_duty(b_duty),
             amp=osc_amp(b_level, lfo_amp_b_depth), bp0=vca_bp(),
             mod_source=LFO_OSC)

    # Per-voice LFO. amp=1.0 sets full modulation strength (per-target depth is
    # set by each 'mod' coef); no vel is sent and it is named as a mod_source,
    # so AMY keeps it silent and free-running.
    amy.send(synth=SYNTH, osc=LFO_OSC,
             wave=lfo_wave, freq=lfo_freq, amp=1.0)


def update_filter_freq():
    amy.send(synth=SYNTH, osc=FILT_OSC, filter_freq=filter_freq_coefs())


def update_vca():
    # VCA envelope now lives on the sounding oscs, so update both A and B.
    amy.send(synth=SYNTH, osc=OSC_A, bp0=vca_bp())
    amy.send(synth=SYNTH, osc=OSC_B, bp0=vca_bp())


def update_vcf():
    amy.send(synth=SYNTH, osc=FILT_OSC, bp1=flt_bp())


def keep_filter_head_alive():
    # The SILENT filter head (FILT_OSC) applies the shared filter to the summed
    # A+B output. If BOTH A and B fall silent, AMY's zero-amp reaper suspends the
    # head. Raising a level (CC 23/27) then revives just that sounding osc while
    # the head is still suspended, so for a moment the osc renders directly to
    # the bus, bypassing the filter. (Loudness is unaffected now that the VCA
    # travels with the osc -- this only keeps the filter in the path.) Re-
    # asserting the head's amp revives it (AMY treats an amp change as a wake-up)
    # on the same control change. Cheap and harmless when the head is already
    # alive; it never revives a released note (revival needs an unset note_off).
    amy.send(synth=SYNTH, osc=FILT_OSC, amp=HEAD_AMP)


def update_lfo():
    amy.send(synth=SYNTH, osc=LFO_OSC, wave=lfo_wave, freq=lfo_freq)


def update_lfo_pitch():
    amy.send(synth=SYNTH, osc=OSC_A, freq={'mod': lfo_pitch_depth})
    amy.send(synth=SYNTH, osc=OSC_B, freq={'mod': lfo_pitch_depth})


def update_lfo_pwm():
    amy.send(synth=SYNTH, osc=OSC_A, duty={'mod': lfo_pwm_depth})
    amy.send(synth=SYNTH, osc=OSC_B, duty={'mod': lfo_pwm_depth})


def update_lfo_amp_a():
    # Depth changes the tremolo's base pre-scale (see osc_amp), so re-send the
    # full amp set for Osc A, not just the 'mod' coef.
    amy.send(synth=SYNTH, osc=OSC_A, amp=osc_amp(a_level, lfo_amp_a_depth))


def update_lfo_amp_b():
    amy.send(synth=SYNTH, osc=OSC_B, amp=osc_amp(b_level, lfo_amp_b_depth))


# ---------------------------------------------------------------------------
# CC dispatch -- each CC updates only its parameter live (no voice reset, so
# held notes are never cut off).
# ---------------------------------------------------------------------------
def handle_cc(cc, val):
    global a_cents, a_wave, a_duty, a_level
    global b_cents, b_wave, b_duty, b_level
    global flt_cutoff, flt_res, flt_type, flt_env_amt, key_scale
    global lfo_freq, lfo_wave, lfo_pitch_depth, lfo_pwm_depth, lfo_filt_depth
    global lfo_amp_a_depth, lfo_amp_b_depth

    if cc == CC_OSC_A_PITCH:
        a_cents = cc_to_detune_cents(val)
        amy.send(synth=SYNTH, osc=OSC_A, freq=osc_freq(a_cents))
    elif cc == CC_OSC_A_WAVE:
        a_wave = cc_to_wave(val)
        amy.send(synth=SYNTH, osc=OSC_A, wave=a_wave)
    elif cc == CC_OSC_A_DUTY:
        a_duty = cc_to_duty(val)
        amy.send(synth=SYNTH, osc=OSC_A, duty=osc_duty(a_duty))
    elif cc == CC_OSC_A_LEVEL:
        a_level = cc_unit(val)
        amy.send(synth=SYNTH, osc=OSC_A, amp=osc_amp(a_level, lfo_amp_a_depth))
        keep_filter_head_alive()
    elif cc == CC_OSC_B_PITCH:
        b_cents = cc_to_detune_cents(val)
        amy.send(synth=SYNTH, osc=OSC_B, freq=osc_freq(b_cents))
    elif cc == CC_OSC_B_WAVE:
        b_wave = cc_to_wave(val)
        amy.send(synth=SYNTH, osc=OSC_B, wave=b_wave)
    elif cc == CC_OSC_B_DUTY:
        b_duty = cc_to_duty(val)
        amy.send(synth=SYNTH, osc=OSC_B, duty=osc_duty(b_duty))
    elif cc == CC_OSC_B_LEVEL:
        b_level = cc_unit(val)
        amy.send(synth=SYNTH, osc=OSC_B, amp=osc_amp(b_level, lfo_amp_b_depth))
        keep_filter_head_alive()
    elif cc == CC_FLT_CUTOFF:
        flt_cutoff = cc_to_cutoff(val)
        update_filter_freq()
    elif cc == CC_FLT_RES:
        flt_res = cc_to_res(val)
        amy.send(synth=SYNTH, osc=FILT_OSC, resonance=flt_res)
    elif cc == CC_FLT_ENV_AMT:
        flt_env_amt = cc_to_flt_env_amt(val)
        update_filter_freq()
    elif cc == CC_FLT_TYPE:
        flt_type = cc_to_filter_type(val)
        amy.send(synth=SYNTH, osc=FILT_OSC, filter_type=flt_type)
    elif cc == CC_KEY_SCALE:
        key_scale = cc_unit(val)
        update_filter_freq()
    elif cc == CC_VCF_ATK:
        vcf_env['a'] = cc_to_time_ms(val)
        update_vcf()
    elif cc == CC_VCF_DEC:
        vcf_env['d'] = cc_to_time_ms(val)
        update_vcf()
    elif cc == CC_VCF_SUS:
        vcf_env['s'] = cc_unit(val)
        update_vcf()
    elif cc == CC_VCF_REL:
        vcf_env['r'] = cc_to_time_ms(val)
        update_vcf()
    elif cc == CC_VCA_ATK:
        vca_env['a'] = cc_to_time_ms(val)
        update_vca()
    elif cc == CC_VCA_DEC:
        vca_env['d'] = cc_to_time_ms(val)
        update_vca()
    elif cc == CC_VCA_SUS:
        vca_env['s'] = cc_unit(val)
        update_vca()
    elif cc == CC_VCA_REL:
        vca_env['r'] = cc_to_time_ms(val)
        update_vca()
    elif cc == CC_LFO_FREQ:
        lfo_freq = cc_to_lfo_freq(val)
        update_lfo()
    elif cc == CC_LFO_PITCH:
        lfo_pitch_depth = cc_to_lfo_pitch(val)
        update_lfo_pitch()
    elif cc == CC_LFO_WAVE:
        lfo_wave = cc_to_wave(val)
        update_lfo()
    elif cc == CC_LFO_PWM:
        lfo_pwm_depth = cc_to_lfo_pwm(val)
        update_lfo_pwm()
    elif cc == CC_LFO_FILT:
        lfo_filt_depth = cc_to_lfo_filt(val)
        update_filter_freq()
    elif cc == CC_LFO_AMP_A:
        lfo_amp_a_depth = cc_to_lfo_amp(val)
        update_lfo_amp_a()
    elif cc == CC_LFO_AMP_B:
        lfo_amp_b_depth = cc_to_lfo_amp(val)
        update_lfo_amp_b()
# ---------------------------------------------------------------------------
# MIDI (channel 12): AMY auto-routes notes to synth 12; this callback only needs
# to handle Control Change messages. Registered via midi.add_callback so it
# coexists with the firmware's default MIDI dispatch (which owns the low-level
# tulip.midi_callback hook).
# ---------------------------------------------------------------------------
def midi_cb(m):
    if not m or len(m) < 3:
        return
    if (m[0] & 0xF0) != 0xB0:        # Control Change only
        return
    if (m[0] & 0x0F) != 11:          # MIDI channel 12
        return
    active_display_mode.on_cc(m[1], m[2])   # cheap: record state for the display
    handle_cc(m[1], m[2])


def setup_midi():
    midi.add_callback(midi_cb)


# ---------------------------------------------------------------------------
# Display infrastructure (shared by every display mode). All drawing happens via
# service_display(), called only from loop() -- never from the MIDI callback --
# and is fully wrapped so a display fault can never disturb audio/MIDI/CV.
# ---------------------------------------------------------------------------
def init_display():
    # The firmware already owns/initializes the panel (it prints the boot
    # banner), so we just confirm amyboard.display is reachable. We still call
    # init_display() defensively in case a fresh handle is needed; any error is
    # swallowed so the synth boots regardless of display state.
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
    # falls back to a normal full refresh. Any failure also falls back.
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


def _boot_wipe(now):
    # One-time boot wipe: leave the firmware banner up for BOOT_CLEAR_MS, then
    # clear the whole panel once so mode output doesn't overprint leftover
    # pixels. Returns True while still booting (caller should not draw yet).
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


# ---------------------------------------------------------------------------
# Progressive framebuffer flush. A full 128x128 refresh blits ~8KB over the
# 400kHz I2C bus (~150-180ms) and blocks the single MicroPython thread long
# enough to drop a note-off. These helpers zero and/or push the framebuffer in
# bounded row BANDS spread across successive loop() calls, so no single refresh
# exceeds a few tens of ms (the same budget a two-row menu redraw already uses).
# Used when entering a display mode (which must clear the previous screen) and by
# the menu's full repaint.
# ---------------------------------------------------------------------------
FLUSH_BAND_ROWS = 12         # pixel-rows pushed per loop() while flushing (~19ms)
_flush_active = False
_flush_y = 0
_flush_y1 = 127


def _begin_flush(y0, y1):
    # Schedule a progressive push of framebuffer rows [y0, y1] across loop calls.
    global _flush_active, _flush_y, _flush_y1
    _flush_active = True
    _flush_y = max(0, min(127, int(y0)))
    _flush_y1 = max(0, min(127, int(y1)))


def _begin_clear():
    # Zero the framebuffer (cheap RAM op) and schedule a progressive blit of the
    # cleared panel, so entering a display mode never stalls audio with one big
    # refresh.
    if not DISPLAY_OK:
        return
    try:
        amyboard.display.fill(0)
    except Exception:
        return
    _begin_flush(0, 127)


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


# ---------------------------------------------------------------------------
# Display modes. Subclass DisplayMode and add an instance to DISPLAY_MODES to
# make a new screen available; set_display_mode() switches the active one (a
# future push-encoder menu will call it).
# ---------------------------------------------------------------------------
class DisplayMode:
    # Human-readable name, e.g. for a future mode-select menu.
    name = 'mode'

    def on_cc(self, cc, val):
        # Called from the MIDI callback for every channel-12 CC while this mode
        # is active. Must stay cheap and must NOT draw (record state only).
        pass

    def on_activate(self):
        # Called when this mode becomes the active one (e.g. via the menu).
        # Clear the panel and reset any cached frame so it redraws from scratch.
        pass

    def render(self, now):
        # Called from loop() at the throttled refresh rate. Draw to the panel,
        # pushing only changed rows (see audio-safety rules above).
        pass


class CCMonitorMode(DisplayMode):
    # Live CC monitor: shows the most-recently-touched CCs and their raw 0-127
    # values, newest at the bottom, each expiring CC_EXPIRE_MS after its last
    # touch.
    name = 'CC Monitor'

    def __init__(self):
        # entries: insertion-ordered [cc, value, last_touch_ticks_ms]; oldest at
        # index 0 (top of screen), newest at the end (bottom).
        self.entries = []
        self.prev = []        # (cc, value) currently shown, by row
        self.blanked = False

    def on_cc(self, cc, val):
        # Update an existing entry in place (so a sweep doesn't reshuffle the
        # list -> single-row redraw), or append a brand-new CC at the bottom.
        # render() does the pixel work later.
        now = time.ticks_ms()
        for entry in self.entries:
            if entry[0] == cc:
                entry[1] = val
                entry[2] = now
                return
        self.entries.append([cc, val, now])

    def on_activate(self):
        # Progressively clear the panel and force a fresh redraw on next render().
        global _display_last_render
        _begin_clear()
        self.prev = []
        self.blanked = True
        _display_last_render = time.ticks_ms()

    def _label(self, cc):
        return CC_LABELS.get(cc, 'CC')

    def _active_lines(self, now):
        # Expire stale entries (preserving order), drop the oldest from the top
        # if we exceed the row budget, and return (cc, value) pairs oldest-first
        # so the newest sits at the bottom and survivors shift up as items above
        # them fade.
        i = 0
        while i < len(self.entries):
            if time.ticks_diff(now, self.entries[i][2]) > CC_EXPIRE_MS:
                self.entries.pop(i)
            else:
                i += 1
        while len(self.entries) > DISPLAY_MAX_LINES:
            self.entries.pop(0)
        return [(e[0], e[1]) for e in self.entries]

    def render(self, now):
        # Repaint only the rows that differ from the last frame, capped at
        # DISPLAY_MAX_ROWS_PER_REFRESH per call, so the I2C bus (and thus the
        # audio) is held as briefly as possible.
        d = amyboard.display
        lines = self._active_lines(now)

        # Idle: no active CCs -> clear just the rows we were using, once.
        if not lines:
            if self.blanked:
                return
            if self.prev:
                span = DISPLAY_TOP_Y + len(self.prev) * DISPLAY_LINE_H
                d.fill_rect(0, DISPLAY_TOP_Y, DISPLAY_WIDTH,
                            len(self.prev) * DISPLAY_LINE_H, 0)
                if not _push_rows(DISPLAY_TOP_Y, span - 1):
                    amyboard.display_refresh()
            self.blanked = True
            self.prev = []
            return

        # Nothing visible changed since last frame.
        if lines == self.prev:
            return

        rows = max(len(lines), len(self.prev))
        # Track which rows have been committed so deferred ones retry next call.
        new_prev = list(self.prev)
        if len(new_prev) < len(lines):
            new_prev += [None] * (len(lines) - len(new_prev))
        pushed = 0
        for i in range(rows):
            if pushed >= DISPLAY_MAX_ROWS_PER_REFRESH:
                break                          # defer the rest to the next refresh
            new = lines[i] if i < len(lines) else None
            old = self.prev[i] if i < len(self.prev) else None
            if new == old:
                continue
            y = DISPLAY_TOP_Y + i * DISPLAY_LINE_H
            d.fill_rect(0, y, DISPLAY_WIDTH, DISPLAY_LINE_H, 0)
            if new is not None:
                cc, v = new
                d.text('%-3d %-6s %3d' % (cc, self._label(cc), v),
                       0, y, DISPLAY_TEXT_COLOR)
            # Push just this one row, so non-contiguous changes never drag
            # unchanged rows along (the bounding-span trap that let a busy
            # screen blit the whole frame and stall audio/MIDI).
            if not _push_rows(y, y + DISPLAY_LINE_H - 1):
                amyboard.display_refresh()
            new_prev[i] = new
            pushed += 1
        # Drop trailing rows that were removed and have now been cleared.
        while len(new_prev) > len(lines) and new_prev[-1] is None:
            new_prev.pop()
        self.prev = new_prev
        self.blanked = False


class ScreensaverMode(DisplayMode):
    # Minimal screensaver: a small dot drifting around the panel. Only the band
    # of rows spanning the dot's old + new position is pushed each step, so the
    # I2C bus (and audio) is never held for a full-frame blit.
    name = 'Screensaver'
    SIZE = 6
    STEP_MS = 120

    def __init__(self):
        self.x = DISPLAY_WIDTH // 2
        self.y = 64
        self.dx = 3
        self.dy = 2
        self.last = 0

    def on_activate(self):
        global _display_last_render
        _begin_clear()
        self.last = 0
        _display_last_render = time.ticks_ms()

    def render(self, now):
        if time.ticks_diff(now, self.last) < self.STEP_MS:
            return
        self.last = now
        d = amyboard.display
        ox, oy = self.x, self.y
        nx, ny = ox + self.dx, oy + self.dy
        if nx < 0 or nx > DISPLAY_WIDTH - self.SIZE:
            self.dx = -self.dx
            nx = ox + self.dx
        if ny < 0 or ny > 127 - self.SIZE:
            self.dy = -self.dy
            ny = oy + self.dy
        self.x, self.y = nx, ny
        d.fill_rect(ox, oy, self.SIZE, self.SIZE, 0)
        d.fill_rect(nx, ny, self.SIZE, self.SIZE, DISPLAY_TEXT_COLOR)
        y0 = min(oy, ny)
        y1 = max(oy, ny) + self.SIZE - 1
        if not _push_rows(y0, y1):
            amyboard.display_refresh()


class OscilloscopeMode(DisplayMode):
    # Placeholder: a real scope needs a tap into AMY's output samples, which is
    # not wired up yet. Selectable so the menu is complete, but it only shows a
    # one-time notice and idles (no per-frame work, no bus load).
    name = 'Oscilloscope'

    def __init__(self):
        self.drawn = False

    def on_activate(self):
        global _display_last_render
        self.drawn = False
        _begin_clear()
        _display_last_render = time.ticks_ms()

    def render(self, now):
        if self.drawn:
            return
        self.drawn = True
        try:
            d = amyboard.display
            d.text('OSCILLOSCOPE', 0, 44, DISPLAY_TEXT_COLOR)
            d.text('not available yet', 0, 64, 110)
            if not _push_rows(40, 79):
                amyboard.display_refresh()
        except Exception:
            pass


# Available display modes. The on-device menu (see SketchMenu) indexes this list
# to let the user pick which one drives the OLED.
CC_MONITOR_MODE = CCMonitorMode()
SCREENSAVER_MODE = ScreensaverMode()
OSCILLOSCOPE_MODE = OscilloscopeMode()
DISPLAY_MODES = [CC_MONITOR_MODE, SCREENSAVER_MODE, OSCILLOSCOPE_MODE]

# The mode currently driving the OLED. Defaults to the CC monitor; swap it with
# set_display_mode() (no menu yet, so this is the only active mode for now).
active_display_mode = CC_MONITOR_MODE


def set_display_mode(mode):
    # Switch the active display mode (called by the menu's mode picker). Clears
    # the panel and lets the incoming mode redraw from scratch.
    global active_display_mode
    active_display_mode = mode
    if DISPLAY_OK:
        try:
            mode.on_activate()
        except Exception:
            pass


def _restore_display_mode():
    # On boot, re-select the mode saved by the last _pick_mode() so the panel
    # comes back the way the user left it. Matched by name (robust to list
    # reordering); unknown/missing -> keep the default. No on_activate() here --
    # loop()'s first service_display() draws it after the boot wipe.
    global active_display_mode
    name = _settings.get('display_mode')
    if not name:
        return
    for m in DISPLAY_MODES:
        if m.name == name:
            active_display_mode = m
            return


def service_display():
    # Throttled dispatch to the active display mode: handles the one-time boot
    # wipe, bounds the refresh rate, and routes drawing to whatever mode is
    # currently selected. Any mode error is swallowed so audio/MIDI/CV continue.
    global _display_last_render
    if not DISPLAY_OK:
        return
    now = time.ticks_ms()
    if _boot_wipe(now):
        return
    if _service_flush():        # progressive clear/flush in progress -> defer
        return
    if time.ticks_diff(now, _display_last_render) < DISPLAY_REFRESH_MS:
        return
    try:
        active_display_mode.render(now)
    except Exception:
        pass
    _display_last_render = now


# ---------------------------------------------------------------------------
# On-device menu (encoder-driven). Reachable by a short CLICK while playing and
# owns the OLED while open. It follows the launcher's universal rule -- turn =
# scroll, click = select / drill in, hold = back out one level -- but the
# launcher delivers those to us as launcher.delta / .click / .back, and we
# report our nesting via launcher.menu_depth so that a hold past our top level
# escapes to the GLOBAL menu. Rendering mirrors the launcher's own menu: a full
# repaint on each navigation step (blits happen only on discrete input, never
# continuously, so audio is disturbed at most briefly while navigating).
# ---------------------------------------------------------------------------
MENU_LINE_H = 12
MENU_TOP_Y = 18
MENU_VISIBLE = 8
MENU_LABEL_MAX = 18
MENU_IDLE_MS = 10000     # auto-close the menu to the display mode after this idle


class _MenuLevel:
    __slots__ = ('title', 'items', 'idx')

    def __init__(self, title, items):
        self.title = title
        # items: list of (label, callback_or_None). None = non-selectable line.
        self.items = items if items else [('(empty)', None)]
        self.idx = 0


class SketchMenu:
    def __init__(self):
        self.stack = []          # empty => closed (playing)
        self.dirty = False
        self._needs_clear = True # force a full repaint (vs per-row diff) next draw
        self._prev = None        # last drawn frame, for row-level diffing

    @property
    def is_open(self):
        return len(self.stack) > 0

    @property
    def depth(self):
        return len(self.stack)

    @property
    def cur(self):
        return self.stack[-1]

    def open(self):
        self.stack = [self._root()]
        self.dirty = True
        self._needs_clear = True

    def close(self):
        self.stack = []

    def _root(self):
        return _MenuLevel('POLYSYNTH', [
            ('MIDI Control', self._todo),
            ('Presets', self._open_presets),
            ('Display Mode', self._open_display),
            ('MIDI Channel', self._todo),
            ('Resume Playing', self.close),
        ])

    def _open_presets(self):
        # Stage 4 replaces these leaves with the real save/load flows.
        self.stack.append(_MenuLevel('PRESETS', [
            ('Save State as Preset', self._todo),
            ('Load Preset', self._todo),
        ]))
        self.dirty = True
        self._needs_clear = True

    def _open_display(self):
        items = [(m.name, (lambda m=m: self._pick_mode(m))) for m in DISPLAY_MODES]
        self.stack.append(_MenuLevel('DISPLAY MODE', items))
        self.dirty = True
        self._needs_clear = True

    def _pick_mode(self, mode):
        set_display_mode(mode)
        _set_setting('display_mode', mode.name)   # remember across reboot/reload
        self.close()             # close so the chosen mode is immediately visible

    def _todo(self):
        self.stack.append(_MenuLevel('COMING SOON', [('(not built yet)', None)]))
        self.dirty = True
        self._needs_clear = True

    def handle(self, delta, click, back):
        if not self.is_open:
            return
        if back:                 # hold: pop one level (may close the menu)
            self.stack.pop()
            self.dirty = True
            self._needs_clear = True
            return
        lvl = self.cur
        if delta:
            n = len(lvl.items)
            lvl.idx = (lvl.idx + delta) % n
            self.dirty = True
        if click:
            _, cb = lvl.items[lvl.idx]
            if cb:
                cb()
                self.dirty = True

    def _draw_row(self, d, y, kind, payload):
        d.fill_rect(0, y, DISPLAY_WIDTH, MENU_LINE_H, 0)
        if kind == 't':
            d.text(payload, 0, y, 255)
        else:
            sel, label = payload
            if sel:
                d.text('>', 0, y, 255)
                d.text(label[:MENU_LABEL_MAX], 12, y, 255)
            else:
                d.text(label[:MENU_LABEL_MAX], 12, y, 110)

    def render(self):
        # If a progressive full-repaint flush is in flight, keep pushing bands
        # and defer any new drawing until the panel is settled.
        if _flush_active:
            _service_flush()
            return
        if not self.dirty or not self.is_open:
            return
        self.dirty = False
        try:
            d = amyboard.display
            lvl = self.cur
            n = len(lvl.items)
            start = 0
            if n > MENU_VISIBLE:
                start = lvl.idx - MENU_VISIBLE // 2
                if start < 0:
                    start = 0
                if start > n - MENU_VISIBLE:
                    start = n - MENU_VISIBLE
            # Current frame = title row + visible item rows, as diffable tuples.
            frame = [(0, 't', lvl.title)]
            y = MENU_TOP_Y
            i = start
            while i < n and i < start + MENU_VISIBLE:
                frame.append((y, 'i', (i == lvl.idx, lvl.items[i][0])))
                y += MENU_LINE_H
                i += 1
            # Full repaint on open / level change (clears whatever was on screen
            # before), pushed progressively in bands so audio isn't stalled.
            # Otherwise push ONLY the rows that changed -- a cursor move touches
            # just two rows -- so navigating never holds the I2C bus for long.
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
                        if not _push_rows(ry, ry + MENU_LINE_H - 1):
                            amyboard.display_refresh()
            self._prev = frame
        except Exception:
            pass


menu = SketchMenu()
_prev_menu_open = False
_last_input_ms = 0


def _pump_menu():
    # Feed the launcher's abstract input to the menu, report our depth, and flag
    # a repaint whenever we drop back to playing so the display mode redraws over
    # the menu's leftover pixels. After MENU_IDLE_MS with no encoder input the
    # menu auto-closes back to the active (selected) display mode.
    global _prev_menu_open, _last_input_ms
    now = time.ticks_ms()
    # Returning from the global overlay: close our own menu so Resume always
    # lands on playing, repaint the display mode, and start a fresh idle window.
    if launcher.resumed:
        launcher.resumed = False
        menu.close()
        launcher.repaint = True
        _last_input_ms = now
        _prev_menu_open = False
    if launcher.delta or launcher.click or launcher.back:
        _last_input_ms = now
    if menu.is_open:
        menu.handle(launcher.delta, launcher.click, launcher.back)
        if menu.is_open and time.ticks_diff(now, _last_input_ms) >= MENU_IDLE_MS:
            menu.close()         # idle timeout -> show the active display mode
    elif launcher.click or launcher.delta:   # a click OR a turn opens our menu
        menu.open()              # the opening input just opens (no scroll yet)
    if _prev_menu_open and not menu.is_open:
        launcher.repaint = True
    _prev_menu_open = menu.is_open
    launcher.menu_depth = menu.depth


def _force_display_redraw():
    if DISPLAY_OK:
        try:
            active_display_mode.on_activate()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Boot
# ---------------------------------------------------------------------------
init_synth()
setup_midi()
init_display()
_restore_display_mode()


def _service_cv():
    # Monophonic CV: CV1 = 1V/oct pitch, CV2 = gate.
    global cv_gate_active, cv_current_note
    try:
        cv1 = amyboard.cv_in(0)
        cv2 = amyboard.cv_in(1)

        gate_high = cv2 >= CV_GATE_THRESHOLD
        new_note = cv_volts_to_midi(cv1)

        if gate_high and not cv_gate_active:
            cv_current_note = new_note
            cv_gate_active = True
            amy.send(synth=SYNTH, note=cv_current_note, vel=0.8)
        elif gate_high and cv_gate_active and new_note != cv_current_note:
            amy.send(synth=SYNTH, note=cv_current_note, vel=0)
            cv_current_note = new_note
            amy.send(synth=SYNTH, note=cv_current_note, vel=0.8)
        elif not gate_high and cv_gate_active:
            cv_gate_active = False
            amy.send(synth=SYNTH, note=cv_current_note, vel=0)
    except Exception:
        pass


def loop():
    # Drive the encoder-driven menu from the launcher's input events first.
    _pump_menu()

    # The instrument stays live even while the menu is open: keep servicing CV
    # (notes/audio also keep running via the MIDI callback regardless).
    _service_cv()

    if menu.is_open:
        menu.render()                # the menu owns the OLED while open
        return

    # Playing: after returning from our menu or a global Resume, repaint once so
    # the display mode redraws over any leftover menu/overlay pixels.
    if launcher.repaint:
        launcher.repaint = False
        _force_display_redraw()

    # Display last so a CV read error never blocks the screen, and vice versa.
    service_display()