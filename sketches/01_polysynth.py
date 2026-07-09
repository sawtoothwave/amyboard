# AMYboard Sketch
# DESCRIPTION: 2-oscillator (A/B) analog-style synth matching the frozen CC map.
#   Stepped musical tuning per osc, 6-way wave buckets (no wavetable/PCM/ALGO),
#   resonant filter with VCF envelope + key tracking, VCA envelope, plus a
#   per-voice LFO routed to pitch, PWM and filter. 6-voice polyphony. MIDI ch12
#   notes (auto-routed to synth 12 by AMY) + CCs (20-32, 40-47, 71, 74, 76-80)
#   handled via midi.add_callback; CV1 1V/oct + CV2 gate.
#   See docs/CC_MAPPING.md for the authoritative control map.

import amy, amyboard, midi, math, time, json
try:
    import framebuf                # for 2x-scaled text in the parameter editor
except Exception:
    framebuf = None

# --- Launcher integration ---------------------------------------------------
# This sketch always talks to a "launcher-shaped" input object (the global
# `launcher`): it CONSUMES abstract encoder events -- launcher.delta (detents),
# launcher.click (short press), launcher.back (hold = pop one of our menu levels)
# -- and REPORTS launcher.menu_depth (how deep our own menu is; 0 = playing).
# launcher.repaint is set True after a Resume so we redraw the screen an overlay
# clobbered. Only WHO FILLS that object changes with how we're run:
#
#   Wrapped  -- the global launcher (wrapper_sketch.py) exec's us with a
#               `launcher` injected into our namespace. It is the sole encoder
#               reader and fills the events; once we report depth 0 a further
#               hold escapes out to the GLOBAL menu. Nothing below runs.
#   Standalone -- run as a plain boot sketch (no wrapper), `launcher` is unbound,
#               so we build our OWN _StandaloneLauncher that reads the Seesaw
#               encoder directly and fills the same events. This is what makes
#               the sketch a self-contained, shareable single file: full on-
#               device menu with or without the wrapper. It replicates the
#               wrapper's hold-ladder MINUS the global-escape rung: a hold at our
#               root menu simply does nothing (there is no wrapper to escape to);
#               leave the root via "Resume Playing" or the idle timeout.
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
    # input" instead of crashing -- the synth still boots and plays.
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
        # Read the raw encoder, derive (delta, click, hold) exactly like the
        # wrapper's Encoder, then translate the hold into our abstract events via
        # the standalone ladder. Called once per loop() from loop() itself.
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

# AMY maps synth numbers 1-16 to MIDI channels 1-16, so synth 12 receives all
# notes (auto-routed) and is the target for the CC callback below on channel 12.
# Channel is FIXED at 12 for now: the on-device channel picker is deferred because
# applying it required a machine.reset(), which trips a firmware bug that leaves
# the audio engine dead until another reboot (see docs/MIDI_MAPPING.md). Re-enable
# once that's fixed (or via a live, no-reset approach) by reading a saved value
# here, e.g. SYNTH = _settings.get('midi_channel', 12).
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
# The amp (VCA) envelope uses AMYboard/standard-MIDI default ADSR CCs for
# backwards-compat: attack 73, decay 75, sustain 79, release 72. The filter (VCF)
# envelope keeps its own CCs (40-43) -- AMYboard's default synth has no 2nd env.
CC_VCA_ATK     = 73
CC_VCA_DEC     = 75
CC_VCA_SUS     = 79
CC_VCA_REL     = 72
CC_FLT_RES     = 71    # standard MIDI "Harmonic/Timbre"
CC_FLT_CUTOFF  = 74    # standard MIDI "Brightness"
# LFO controls. Aligned to the standard MIDI vibrato CCs where they exist -- rate
# (76) and vibrato depth (77) -- for backwards-compat; the rest use spare CCs.
# handle_cc processes every CC itself (the LFO is a bespoke mod_source, so AMY
# never auto-maps these). Vibrato is GLOBAL (both oscs share one depth) -- the
# standard mod-wheel form; see the mod-wheel alias below. PWM sits at 83 because
# the amp-envelope sustain (79) took its former slot.
CC_LFO_FREQ    = 76    # standard MIDI vibrato rate
CC_LFO_PITCH   = 77    # standard MIDI vibrato depth
CC_LFO_WAVE    = 78
CC_LFO_FILT    = 80
CC_LFO_AMP_A   = 81
CC_LFO_AMP_B   = 82
CC_LFO_PWM     = 83

# The MIDI mod wheel is the standard vibrato-depth controller, so we treat CC 1 as
# an alias for CC_LFO_PITCH (handle_cc remaps it): a performer's wheel adds/removes
# vibrato out of the box, and it shares the one global LFO->pitch depth (so it also
# reflects in Param Control and is captured by presets).
CC_MODWHEEL    = 1

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
LFO_PITCH_DEPTH_MAX = 1.0    # octaves (quadratic curve; full = +/- 12 semitones)
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
    CC_LFO_FREQ: 'LFO HZ',  CC_LFO_PITCH: 'VIB',   CC_MODWHEEL: 'MOD WH',
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
    # 'mod' adds the shared LFO vibrato at the global depth (unit-per-octave), so
    # A and B track the same vibrato -- the standard mod-wheel form.
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
    # Enable MIDI note forwarding to this synth. On current AMY firmware
    # grab_midi_notes=0 means "receive NO MIDI notes" -- it disables forwarding
    # entirely and silences the instrument (CCs still arrive via our own
    # midi.add_callback, which is why only notes went missing). Set 1 so AMY
    # routes this synth's channel (= SYNTH) notes to it. (Older firmware read 0 as
    # "own channel only", hence the previous value.) Other channels stay silent
    # because every other synth's voice count is zeroed above.
    amy.send(synth=SYNTH, grab_midi_notes=1)

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
    # Global vibrato: both sounding oscs share one depth.
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


# The full live patch: every parameter a CC can change. These capture/apply
# helpers are the basis for the preset feature (Stage 4, save/load). The two
# envelopes are dicts; the rest are scalars.
_PATCH_KEYS = (
    'a_cents', 'a_wave', 'a_duty', 'a_level',
    'b_cents', 'b_wave', 'b_duty', 'b_level',
    'flt_cutoff', 'flt_res', 'flt_type', 'flt_env_amt', 'key_scale',
    'lfo_freq', 'lfo_wave', 'lfo_pitch_depth', 'lfo_pwm_depth', 'lfo_filt_depth',
    'lfo_amp_a_depth', 'lfo_amp_b_depth',
)


def _capture_patch():
    g = globals()
    d = {k: g[k] for k in _PATCH_KEYS}
    d['vcf_env'] = dict(vcf_env)
    d['vca_env'] = dict(vca_env)
    return d


def _apply_patch(d):
    # Load a captured patch back into the live globals. Missing/unknown keys are
    # ignored (fall back to whatever the global already holds), so an older saved
    # patch stays compatible if the parameter set changes.
    if not isinstance(d, dict):
        return
    g = globals()
    for k in _PATCH_KEYS:
        if k in d:
            g[k] = d[k]
    if isinstance(d.get('vcf_env'), dict):
        vcf_env.update(d['vcf_env'])
    if isinstance(d.get('vca_env'), dict):
        vca_env.update(d['vca_env'])


# NOTE: the on-device MIDI-channel picker (and its set_midi_channel/reboot +
# boot-time patch-restore) was removed for now -- applying a channel change
# needed a machine.reset(), which trips a firmware audio-engine bug (see
# docs/MIDI_MAPPING.md). The capture/apply helpers above are kept for presets.


# --- Presets ----------------------------------------------------------------
# Named patch snapshots saved to internal flash. A preset stores the RAW 0-127
# value of every editable CC (a copy of param_values below) -- i.e. the exact
# knob positions -- NOT the derived globals. Loading replays those values through
# handle_cc, the same path a physical knob turn takes: each parameter's correct
# live-update runs (including the pitch/PWM LFO 'mod' coefs that init_synth does
# not re-send), the Param Control editor stays accurate (it reads param_values),
# and held notes are never cut (no voice reallocation). One JSON file holds an
# ordered list of {'name', 'cc'} entries; the whole file is rewritten on save
# (tiny, so flash wear is a non-issue -- writes happen only on an explicit save).
PRESETS_FILE = '/user/polysynth_presets.json'
PRESET_NAME_MAX = 12      # longest preset name the name-entry screen accepts
MAX_PRESETS = 32          # generous backstop so a runaway can't fill flash


def _load_presets():
    try:
        with open(PRESETS_FILE) as f:
            d = json.load(f)
        if isinstance(d, list):
            # Keep only well-formed entries so one bad record can't break the list.
            return [p for p in d
                    if isinstance(p, dict) and isinstance(p.get('name'), str)
                    and isinstance(p.get('cc'), dict)]
    except Exception:
        pass
    return []


_presets = _load_presets()


def _write_presets():
    # Persist the whole list. Guarded so a flash-write fault never disturbs
    # audio/MIDI; returns success so the UI can report it.
    try:
        with open(PRESETS_FILE, 'w') as f:
            json.dump(_presets, f)
        return True
    except Exception:
        return False


def _capture_preset():
    # Raw 0-127 snapshot of every editable CC -- the exact knob positions. JSON
    # object keys must be strings, so stringify the CC numbers (parsed back on
    # load). This is the save/load payload (see _apply_preset for the replay).
    return {str(cc): int(v) for cc, v in param_values.items()}


def _apply_preset(cc_map):
    # Replay a saved snapshot through handle_cc -- the canonical live-apply path.
    # Unknown/uneditable CCs are skipped, so an older preset stays compatible if
    # the parameter set changes (its stale CCs are ignored; untouched params keep
    # their current value rather than resetting).
    if not isinstance(cc_map, dict):
        return
    for k, v in cc_map.items():
        try:
            cc = int(k)
            val = clamp(int(v), 0, 127)
        except Exception:
            continue
        if cc in param_values:
            handle_cc(cc, val)


def _find_preset(name):
    for i, p in enumerate(_presets):
        if p.get('name') == name:
            return i
    return -1


def _save_preset(name):
    # Overwrite an existing same-name preset in place, else append a new one.
    # The MAX_PRESETS cap is enforced by the UI before it reaches a NEW name.
    entry = {'name': name, 'cc': _capture_preset()}
    i = _find_preset(name)
    if i >= 0:
        _presets[i] = entry
    else:
        _presets.append(entry)
    return _write_presets()


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

    # The mod wheel is the standard vibrato controller: treat it as the LFO->pitch
    # depth so a performer's wheel works out of the box and shares that one param
    # (editor + presets stay in sync). Done before the param_values record below.
    if cc == CC_MODWHEEL:
        cc = CC_LFO_PITCH

    # Remember the raw value for any CC the Param Control editor can edit, so it
    # opens on the current value whether it was last set by a knob or the editor.
    if cc in param_values:
        param_values[cc] = clamp(int(val), 0, 127)

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
# MIDI: AMY auto-routes notes to the synth matching their channel (= SYNTH, the
# selected channel), so this callback only needs to handle Control Change
# messages, filtered to that same channel. Registered via midi.add_callback so it
# coexists with the firmware's default MIDI dispatch (which owns the low-level
# tulip.midi_callback hook).
# ---------------------------------------------------------------------------
def midi_cb(m):
    if not m or len(m) < 3:
        return
    if (m[0] & 0xF0) != 0xB0:        # Control Change only
        return
    if (m[0] & 0x0F) != (SYNTH - 1):   # our selected channel (synth N = ch N)
        return
    active_display_mode.on_cc(m[1], m[2])   # cheap: record state for the display
    handle_cc(m[1], m[2])
    menu.note_external_cc(m[1], m[2])       # let an open param editor track it live


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


def _push_window(x0, x1, y0, y1):
    # Windowed sub-RECTANGLE refresh: push only the columns [x0, x1] of rows
    # [y0, y1] instead of full-width rows. The parameter editor uses this so a
    # detent blits just the narrow strip around the moving cursor (and the
    # centered value) -- a few hundred bytes / a few ms of I2C -- rather than the
    # whole 128px band. The SSD1327 is 4bpp (2 pixels/byte) and auto-increments
    # column-then-row inside the addressed window, but the source bytes for a
    # sub-column window are strided by row in the framebuffer, so we gather the
    # rectangle into one contiguous buffer and send it in a single write. Same
    # SSD1327-only guard + full-refresh fallback as _push_rows.
    try:
        hw = amyboard.display._hw
    except Exception:
        hw = None
    if hw is None or not hasattr(hw, 'col_addr') or not hasattr(hw, 'row_addr'):
        return False
    try:
        w_px = hw.width
        x0 = max(0, min(w_px - 1, int(x0)))
        x1 = max(0, min(w_px - 1, int(x1)))
        if x1 < x0:
            x0, x1 = x1, x0
        y0 = max(0, min(127, int(y0)))
        y1 = max(0, min(127, int(y1)))
        if y1 < y0:
            return False
        row_bytes = w_px // 2              # 64 bytes/row at 128px wide, 4bpp
        c0 = x0 // 2                       # byte column = 2 pixels
        c1 = x1 // 2
        base = hw.col_addr[0]              # panel's first byte-column (any offset)
        cw = c1 - c0 + 1
        buf = hw.buffer
        out = bytearray(cw * (y1 - y0 + 1))
        o = 0
        for y in range(y0, y1 + 1):
            s = y * row_bytes + c0
            out[o:o + cw] = buf[s:s + cw]
            o += cw
        hw.write_cmd(0x15)                 # SSD1327 SET_COL_ADDR
        hw.write_cmd(base + c0)
        hw.write_cmd(base + c1)
        hw.write_cmd(0x75)                 # SSD1327 SET_ROW_ADDR
        hw.write_cmd(y0)
        hw.write_cmd(y1)
        hw.write_data(out)
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
# Editable parameters (Param Control menu). Each descriptor pairs a human label
# with the CC that already drives that parameter, so the on-device editor reuses
# the exact same handle_cc() path a hardware knob does -- all value->sound
# mapping is shared, and exposing another parameter is a one-line addition to
# PARAMS. `fmt` (optional) turns a raw 0-127 value into a friendly label for
# parameters whose range is really discrete regions (pitch intervals, wave
# shapes, filter types). param_values tracks the last raw 0-127 value per CC
# (updated by BOTH incoming MIDI CCs and the editor) so the editor opens on the
# current value.
# ---------------------------------------------------------------------------
def fmt_osc_pitch(v):
    # Osc pitch CC maps to stepped intervals plus two fine-detune wings; name the
    # fixed intervals and show cents for the fine/zero region -- reusing the very
    # same cc_to_detune_cents() map the synth applies, so label and sound agree.
    cents = cc_to_detune_cents(v)
    named = {-2400: '-2 oct', -1200: '-1 oct', -700: '-5th',
             700: '+5th', 1200: '+1 oct', 2400: '+2 oct'}
    if cents in named:
        return named[cents]
    c = int(round(cents))
    return ('%+dc' % c) if c else 'Unison'


def fmt_wave(v):
    # Name the six core waves, bucketed exactly like cc_to_wave() so the label
    # always matches the wave the synth actually selects.
    v = clamp(int(v), 0, 127)
    if v <= 20:
        return 'Sine'
    if v <= 41:
        return 'Pulse'
    if v <= 63:
        return 'Saw Dn'
    if v <= 84:
        return 'Saw Up'
    if v <= 105:
        return 'Triangle'
    return 'Noise'


def fmt_filter_type(v):
    # Four filter types, bucketed like cc_to_filter_type() (FILTER_TYPES order).
    names = ('LP 24', 'LP', 'BP', 'HP')
    idx = (clamp(int(v), 0, 127) * len(names)) // 128
    return names[min(idx, len(names) - 1)]


class _Param:
    __slots__ = ('label', 'cc', 'default', 'fmt')

    def __init__(self, label, cc, default, fmt=None):
        self.label = label
        self.cc = cc
        self.default = default   # raw 0-127 value used until a CC/editor sets one
        self.fmt = fmt           # optional value(0-127) -> friendly label


# The editable-parameter list, shown numbered in the menu. Adding a row here is
# all it takes to expose another parameter in the editor. `default` is the raw
# 0-127 value that reproduces the patch's initial default (the double-click reset
# target); the fine ADSR-time defaults are the CCs closest to the initial ms.
# Labels are kept <=14 chars incl. the "NN. " prefix so nothing clips at 128px;
# a few are shortened from the requested names (see notes) -- Filt Type, Kbd
# Track, the ADSR Atk/Dec/Sus/Rel forms, and the space-free Lfo>X routing.
PARAMS = [
    _Param('Osc A Pitch', CC_OSC_A_PITCH,  64, fmt_osc_pitch),
    _Param('Osc A Shape', CC_OSC_A_WAVE,   52, fmt_wave),
    _Param('Osc A Duty',  CC_OSC_A_DUTY,   64),
    _Param('Osc A Level', CC_OSC_A_LEVEL, 127),
    _Param('Osc B Pitch', CC_OSC_B_PITCH,  64, fmt_osc_pitch),
    _Param('Osc B Shape', CC_OSC_B_WAVE,    0, fmt_wave),
    _Param('Osc B Duty',  CC_OSC_B_DUTY,   64),
    _Param('Osc B Level', CC_OSC_B_LEVEL,   0),
    _Param('Cutoff',      CC_FLT_CUTOFF,  127),
    _Param('Resonance',   CC_FLT_RES,       0),
    _Param('Filter Env',  CC_FLT_ENV_AMT,   0),
    _Param('Filt Type',   CC_FLT_TYPE,     48, fmt_filter_type),
    _Param('Kbd Track',   CC_KEY_SCALE,     0),
    _Param('Vcf Atk',     CC_VCF_ATK,       0),
    _Param('Vcf Dec',     CC_VCF_DEC,      34),
    _Param('Vcf Sus',     CC_VCF_SUS,      25),
    _Param('Vcf Rel',     CC_VCF_REL,      31),
    _Param('Vca Atk',     CC_VCA_ATK,       0),
    _Param('Vca Dec',     CC_VCA_DEC,      25),
    _Param('Vca Sus',     CC_VCA_SUS,     127),
    _Param('Vca Rel',     CC_VCA_REL,      34),
    _Param('Lfo Freq',    CC_LFO_FREQ,      0),
    _Param('Lfo Shape',   CC_LFO_WAVE,      0, fmt_wave),
    _Param('Lfo>Pitch',   CC_LFO_PITCH,     0),
    _Param('Lfo>Pwm',     CC_LFO_PWM,       0),
    _Param('Lfo>Filter',  CC_LFO_FILT,      0),
    _Param('Lfo>Amp A',   CC_LFO_AMP_A,     0),
    _Param('Lfo>Amp B',   CC_LFO_AMP_B,     0),
]

# Last raw 0-127 value seen per editable CC, seeded with each param's default.
param_values = {p.cc: p.default for p in PARAMS}


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
# Visible list rows = one PAGE (see the paginated windowing in render). Long
# lists (Param Control's 28 params) advance a page at a time, so this also sets
# the page size shown by the "pg/total" marker. 8 fills the screen well (last row
# ends at y=114). Short menus (<=8 items) are a single page with no marker.
MENU_VISIBLE = 8
MENU_PAGE_Y = 116        # bottom row (128 - MENU_LINE_H) for the page marker; the
                         # last item row ends at y=114, leaving this row free
# Page marker = one small square per page, right-justified on MENU_PAGE_Y; the
# current page is a filled block, the others hollow outlines.
PAGE_SQ = 7              # square side (px)
PAGE_SQ_GAP = 3          # gap between squares
PAGE_SQ_MARGIN = 2       # right margin from the panel edge
MENU_LABEL_MAX = 18
MENU_IDLE_MS = 10000     # auto-close the menu to the display mode after this idle
TOAST_MS = 1200          # how long a confirmation toast (e.g. "PRESET SAVED!") shows

# Parameter-editor (Param Control) layout: a 0-127 track with a cursor, the raw
# value floating over the cursor, and (for discrete params) a friendly label.
# Rows are laid out so the per-turn moving parts (value number, cursor) sit in
# non-overlapping bands that can be pushed on their own -- see _render_edit. The
# value and friendly label are drawn 2x (see _text2x); the title + end labels
# stay 1x.
CHAR_W = 8               # framebuf font cell width (for centering text)
CHAR_H = 8               # framebuf font cell height
EDIT_TEXT_SCALE = 2      # value/label magnification
EDIT_TEXT_W = CHAR_W * EDIT_TEXT_SCALE   # 2x glyph width
EDIT_TEXT_H = CHAR_H * EDIT_TEXT_SCALE   # 2x glyph height (band height)
EDIT_TITLE_Y = 2         # param name (1x, static after open)
EDIT_LABEL_Y = 24        # friendly discrete label (2x; redrawn only on change)
EDIT_VALUE_Y = 50        # raw 0-127 value (2x, over the cursor; per-turn band)
EDIT_TRACK_Y = 88        # the 0-127 track line
EDIT_LINE_X0 = 6
EDIT_LINE_X1 = 121
EDIT_TICK_H  = 6         # cursor half-height above/below the track
EDIT_ENDS_Y  = 100       # "0" / "127" end labels (1x, static)
# Cursor band = the track line +/- the tick, pushed as a unit each turn.
EDIT_TRACK_BAND_Y0 = EDIT_TRACK_Y - EDIT_TICK_H - 1
EDIT_TRACK_BAND_Y1 = EDIT_TRACK_Y + EDIT_TICK_H + 1
EDIT_REFRESH_MS = 16     # min gap between editor redraws. Low (~1 loop) because each
                         # incremental redraw is now a couple of narrow windowed
                         # pushes (a few ms), so a detent isn't deferred an extra
                         # loop; still non-zero to cap a MIDI-CC flood at 1/loop.
EDIT_DBLCLICK_MS = 400   # two clicks within this window = double-click (reset);
                         # a single click's exit is deferred this long to detect it

# Encoder acceleration. The launcher hands us the detent COUNT this tick, so a
# fast spin already arrives as a bigger delta; we amplify that so rapid turns
# cover ground while a single detent stays 1:1 for fine adjustment. Applied in
# the sketch (not the launcher) so it works wrapped AND standalone.
ENC_ACCEL_CAP = 10       # max per-detent multiplier on a fast spin


def _accel(delta):
    a = abs(delta)
    if a <= 1:
        return delta                     # one detent = one step (precise)
    return delta * min(a, ENC_ACCEL_CAP)  # faster spins step quadratically further


def _text2x(d, s, x, y, color):
    # Draw text at 2x scale. framebuf's only font is 8x8 and it has no scaling
    # API, so render the string into a 1-bit temp buffer, then blit each set
    # pixel as a 2x2 block. Falls back to 1x if framebuf is unavailable.
    if framebuf is None:
        d.text(s, x, y, color)
        return
    try:
        w = len(s) * CHAR_W
        if w <= 0:
            return
        buf = bytearray(((w + 7) // 8) * CHAR_H)
        tmp = framebuf.FrameBuffer(buf, w, CHAR_H, framebuf.MONO_HLSB)
        tmp.text(s, 0, 0, 1)
        for py in range(CHAR_H):
            yy = y + py * 2
            for px in range(w):
                if tmp.pixel(px, py):
                    d.fill_rect(x + px * 2, yy, 2, 2, color)
    except Exception:
        d.text(s, x, y, color)


class _MenuLevel:
    __slots__ = ('title', 'items', 'idx', 'start')

    def __init__(self, title, items):
        self.title = title
        # items: list of (label, callback_or_None). None = non-selectable line.
        self.items = items if items else [('(empty)', None)]
        self.idx = 0
        self.start = 0    # index of the top visible item = current page origin
                          # (page-aligned; recomputed from idx in render)


class _EditLevel:
    # A parameter-adjustment "level" pushed on the menu stack. Turning adjusts the
    # value LIVE (applied via handle_cc, so you hear it as you dial); a CLICK keeps
    # the current value and pops back to the parameter list; a HOLD (back) reverts
    # to entry_value and pops. entry_value is the snapshot taken when the editor
    # opened -- the only state hold-to-restore needs.
    __slots__ = ('param', 'value', 'entry_value', 'dirty', 'full', 'prev_label',
                 'prev_cx')

    def __init__(self, param, value):
        self.param = param
        self.value = value
        self.entry_value = value
        self.dirty = True         # something changed -> redraw needed
        self.full = True          # next draw is a full clear+draw (open/resume)
        self.prev_label = None    # last drawn friendly label (redraw on change)
        self.prev_cx = None       # last cursor pixel-x (windowed-push union span)


# Name-entry ring: turning scrolls the active slot through these; a click acts on
# the current one. Plain chars append (the candidate stays put for the next slot);
# the two special tokens act instead of appending (DEL backspaces, OK confirms/
# saves). Space is a real entry (drawn as a placeholder). Scrolling CLAMPS at the
# ends (no wrap), so a fast spin lands on OK (end) or 'a' (start).
_NAME_RING = [c for c in 'abcdefghijklmnopqrstuvwxyz0123456789 '] + ['DEL', 'OK']


class _NameLevel:
    # Preset-name entry pushed on the menu stack. `name` is the committed string so
    # far; `sel` indexes _NAME_RING for the in-progress slot. Turn scrolls the
    # candidate, click commits it, hold (back) cancels the whole name. Like
    # _EditLevel it owns a full/dirty pair driving its own render path.
    __slots__ = ('name', 'sel', 'dirty', 'full')

    def __init__(self):
        self.name = ''
        self.sel = 0
        self.dirty = True
        self.full = True


class SketchMenu:
    def __init__(self):
        self.stack = []          # empty => closed (playing)
        self.dirty = False
        self._needs_clear = True # force a full repaint (vs per-row diff) next draw
        self._prev = None        # last drawn frame, for row-level diffing
        self.suspended = False   # editor idled out: state kept, display mode shown
        self._click_pending_at = 0   # ticks of a deferred editor single-click (0=none)
        self._edit_last_render = 0   # ticks of the last editor redraw (throttle gate)
        self._toast_msg = ''         # transient confirmation (e.g. "PRESET SAVED!")
        self._toast_until = 0        # ticks_ms when the toast auto-dismisses
        self._toast_drawn = False    # toast painted once already (don't re-flush)
        self._panel_dirty_to = 128   # rows currently holding content on the panel;
                                     # a full-repaint clears down to at least here so
                                     # a taller predecessor screen (or a full-screen
                                     # display mode / editor / toast) never leaves
                                     # stale pixels below a shorter menu. 128 = full.

    @property
    def is_open(self):
        # "Open" = we own the screen and take edit input. A suspended editor is
        # NOT open (the display mode shows) but the stack is kept for resume.
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
        self._needs_clear = True
        self._panel_dirty_to = 128    # the display mode was full-screen behind us

    def close(self):
        self.stack = []
        self.suspended = False
        self._click_pending_at = 0

    def suspend(self):
        # Idle timeout at any level: keep the stack (level + cursor / editor
        # value), hide the menu so the display mode shows, resume on next input.
        self.suspended = True
        self._click_pending_at = 0

    def resume(self):
        # Wake a suspended editor back to exactly where it was, re-syncing from
        # the live value (which MIDI may have moved while we were idle).
        self.suspended = False
        self.dirty = True
        self._needs_clear = True
        self._edit_last_render = 0
        self._panel_dirty_to = 128    # the display mode was full-screen while idle
        if self.stack and isinstance(self.cur, _EditLevel):
            cur = self.cur
            cur.value = int(param_values.get(cur.param.cc, cur.value))
            cur.full = True
            cur.dirty = True

    def note_external_cc(self, cc, val):
        # Called from the MIDI callback: if the open editor is on this CC, reflect
        # the incoming value live. Records state only (never draws) -- loop()'s
        # render picks it up -- so it stays audio-safe.
        if self.suspended or not self.stack:
            return
        cur = self.cur
        if isinstance(cur, _EditLevel) and cur.param.cc == cc:
            cur.value = clamp(int(val), 0, 127)
            cur.dirty = True

    def service_pending(self, now):
        # Fire a deferred editor single-click (commit + exit to the list) once the
        # double-click window passes with no second click.
        if not self._click_pending_at:
            return
        if time.ticks_diff(now, self._click_pending_at) <= EDIT_DBLCLICK_MS:
            return
        self._click_pending_at = 0
        if self.stack and isinstance(self.cur, _EditLevel):
            self.stack.pop()          # keep the current value, back to the list
            self.dirty = True
            self._needs_clear = True

    def _root(self):
        return _MenuLevel('POLYSYNTH', [
            ('Param Control', self._open_params),
            ('Presets', self._open_presets),
            ('Display Mode', self._open_display),
            ('Resume Playing', self.close),
        ])

    def _open_params(self):
        # Numbered list of editable parameters; clicking one opens its editor.
        items = [('%d. %s' % (i + 1, p.label), (lambda p=p: self._edit_param(p)))
                 for i, p in enumerate(PARAMS)]
        self.stack.append(_MenuLevel('PARAM CONTROL', items))
        self.dirty = True
        self._needs_clear = True

    def _edit_param(self, p):
        # Open the 0-127 slider editor on this param's current value.
        v = int(param_values.get(p.cc, p.default))
        self.stack.append(_EditLevel(p, v))
        self.dirty = True
        self._needs_clear = True

    def _presets_menu(self):
        return _MenuLevel('PRESETS', [
            ('Save State as Preset', self._start_save),
            ('Load Preset', self._open_load),
            ('Delete Preset', self._open_delete),
        ])

    def _open_presets(self):
        self.stack.append(self._presets_menu())
        self.dirty = True
        self._needs_clear = True

    def _start_save(self):
        # Open the name-entry screen; committing it saves the live patch.
        self.stack.append(_NameLevel())
        self.dirty = True
        self._needs_clear = True

    def _commit_name(self, lvl):
        # Called when the user clicks OK. Empty names are ignored (stay editing).
        # Otherwise confirm: "SAVE?" (or "OVERWRITE?" if the name already exists)
        # with Yes/No. Yes saves + returns to the polysynth menu; No -- or a hold --
        # pops back to the name-entry screen. A new name at the cap is blocked.
        name = lvl.name.strip()
        if not name:
            return
        exists = _find_preset(name) >= 0
        if not exists and len(_presets) >= MAX_PRESETS:
            self.stack.append(_MenuLevel('PRESETS FULL', [
                ('Max %d reached' % MAX_PRESETS, None),
                ('Back', self._pop),
            ]))
        else:
            self.stack.append(_MenuLevel('OVERWRITE?' if exists else 'SAVE?', [
                ('Yes', (lambda n=name: self._do_save(n))),
                ('No', self._pop),
            ]))
        self.dirty = True
        self._needs_clear = True

    def _do_save(self, name):
        # Persist, flash a "PRESET SAVED!" toast, and drop to the main polysynth
        # menu (the toast auto-dismisses to it -- see render()).
        ok = _save_preset(name)
        self.stack = [self._root()]
        self._show_toast('PRESET SAVED!' if ok else 'SAVE FAILED')
        self.dirty = True
        self._needs_clear = True

    def _show_toast(self, msg):
        self._toast_msg = msg
        self._toast_until = time.ticks_add(time.ticks_ms(), TOAST_MS)
        self._toast_drawn = False

    def _open_load(self):
        # List saved presets; clicking one applies it live and returns to playing.
        if not _presets:
            self.stack.append(_MenuLevel('LOAD PRESET', [('(none saved)', None)]))
        else:
            items = [(_presets[i].get('name', '?')[:MENU_LABEL_MAX],
                      (lambda i=i: self._load_preset(i)))
                     for i in range(len(_presets))]
            self.stack.append(_MenuLevel('LOAD PRESET', items))
        self.dirty = True
        self._needs_clear = True

    def _load_preset(self, i):
        try:
            _apply_preset(_presets[i].get('cc'))
        except Exception:
            pass
        self.close()             # apply + return to playing so it's heard at once

    def _delete_menu(self):
        # The delete list, rebuilt each time so it always reflects the current set
        # (indices shift as presets are removed). Items delete by NAME, not index,
        # so a stale closure can never remove the wrong preset.
        if not _presets:
            return _MenuLevel('DELETE PRESET', [('(none saved)', None)])
        items = [(_presets[i].get('name', '?')[:MENU_LABEL_MAX],
                  (lambda nm=_presets[i].get('name', '?'): self._confirm_delete(nm)))
                 for i in range(len(_presets))]
        return _MenuLevel('DELETE PRESET', items)

    def _open_delete(self):
        self.stack.append(self._delete_menu())
        self.dirty = True
        self._needs_clear = True

    def _confirm_delete(self, name):
        # Destructive: the name is in the two-line header ("Delete preset\n<name>?")
        # and only Yes/No are selectable. No or hold pops back to the delete list.
        # A 12-char-max name + '?' always fits the 16-char header line. The trailing
        # newline leaves a blank line between the name and the Yes/No options.
        self.stack.append(_MenuLevel('Delete preset\n%s?\n' % name, [
            ('Yes', (lambda n=name: self._do_delete(n))),
            ('No', self._pop),
        ]))
        self.dirty = True
        self._needs_clear = True

    def _do_delete(self, name):
        i = _find_preset(name)
        if i >= 0:
            del _presets[i]
            _write_presets()
        # Flash "DELETED!" then land back on the refreshed delete list (or the
        # Presets menu if that was the last one).
        self.stack = [self._root(), self._presets_menu()]
        if _presets:
            self.stack.append(self._delete_menu())
        self._show_toast('DELETED!')
        self.dirty = True
        self._needs_clear = True

    def _pop(self):
        if self.stack:
            self.stack.pop()
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

    def handle(self, delta, click, back):
        if self._toast_msg:
            # A "saved" toast is showing: any input just dismisses it (it does not
            # also act on the menu underneath), then the menu repaints.
            if delta or click or back:
                self._toast_msg = ''
                self.dirty = True
                self._needs_clear = True
            return
        if not self.is_open:
            return
        lvl = self.cur
        if isinstance(lvl, _EditLevel):
            # Parameter editor: turn adjusts LIVE, single click keeps + exits,
            # double click resets to the patch default, hold reverts + exits.
            if back:                 # hold: restore the entry value, then pop
                self._click_pending_at = 0
                handle_cc(lvl.param.cc, lvl.entry_value)
                self.stack.pop()
                self.dirty = True
                self._needs_clear = True
                return
            if delta:                # turn: move the cursor + apply live
                self._click_pending_at = 0     # a turn cancels a pending click
                lvl.value = clamp(lvl.value + _accel(delta), 0, 127)
                handle_cc(lvl.param.cc, lvl.value)
                lvl.dirty = True
            if click:
                now = time.ticks_ms()
                if self._click_pending_at and \
                        time.ticks_diff(now, self._click_pending_at) <= EDIT_DBLCLICK_MS:
                    # Double click: reset to the patch default, stay in the editor.
                    self._click_pending_at = 0
                    lvl.value = lvl.param.default
                    handle_cc(lvl.param.cc, lvl.value)
                    lvl.dirty = True
                else:
                    # First click: defer commit+exit so a 2nd click can arrive
                    # (fired by service_pending once the window passes).
                    self._click_pending_at = now
            return
        if isinstance(lvl, _NameLevel):
            # Name entry: turn scrolls the ring candidate, click commits it (append
            # char / backspace / confirm), hold cancels the whole name.
            if back:
                self.stack.pop()
                self.dirty = True
                self._needs_clear = True
                return
            if delta:
                # Clamp (no wrap) so a fast spin zips straight to OK at the end
                # (or 'a' at the start); acceleration lets a quick flick get there.
                lvl.sel = clamp(lvl.sel + _accel(delta), 0, len(_NAME_RING) - 1)
                lvl.dirty = True
            if click:
                item = _NAME_RING[lvl.sel]
                if item == 'OK':
                    self._commit_name(lvl)
                elif item == 'DEL':
                    if lvl.name:
                        lvl.name = lvl.name[:-1]
                    lvl.dirty = True
                elif len(lvl.name) < PRESET_NAME_MAX:
                    lvl.name += item
                    # Keep the candidate on the same letter for the next slot
                    # (handy for double letters / similar chars).
                    lvl.dirty = True
            return
        if back:                 # hold: pop one level (may close the menu)
            self.stack.pop()
            self.dirty = True
            self._needs_clear = True
            return
        if delta:
            # List scroll is 1:1 with detents (no acceleration -- that's only for
            # the value editor) and clamps at the ends instead of wrapping.
            n = len(lvl.items)
            lvl.idx = clamp(lvl.idx + delta, 0, n - 1)
            self.dirty = True
        if click:
            _, cb = lvl.items[lvl.idx]
            if cb:
                cb()
                self.dirty = True

    def _draw_row(self, d, y, kind, payload):
        d.fill_rect(0, y, DISPLAY_WIDTH, MENU_LINE_H, 0)
        if kind == 't':
            # Title row: left-aligned header text, plus an optional right-aligned
            # marker.
            left, right = payload
            d.text(left, 0, y, 255)
            if right:
                d.text(right, DISPLAY_WIDTH - len(right) * CHAR_W, y, 255)
        elif kind == 'q':
            # Page squares: one per page, right-justified; current page filled.
            total, cur = payload
            pitch = PAGE_SQ + PAGE_SQ_GAP
            x = DISPLAY_WIDTH - PAGE_SQ_MARGIN - (total * pitch - PAGE_SQ_GAP)
            sy = y + (MENU_LINE_H - PAGE_SQ) // 2
            for i in range(total):
                d.fill_rect(x, sy, PAGE_SQ, PAGE_SQ, 255)
                if i != cur:                 # hollow outline for non-current pages
                    d.fill_rect(x + 1, sy + 1, PAGE_SQ - 2, PAGE_SQ - 2, 0)
                x += pitch
        else:
            sel, label = payload
            if sel:
                d.text('>', 0, y, 255)
                d.text(label[:MENU_LABEL_MAX], 12, y, 255)
            else:
                d.text(label[:MENU_LABEL_MAX], 12, y, 110)

    def _edit_label(self, d, label):
        # Friendly discrete label, centered, 2x. Cleared band + redraw so a
        # shorter label never leaves stale characters behind.
        d.fill_rect(0, EDIT_LABEL_Y, DISPLAY_WIDTH, EDIT_TEXT_H, 0)
        if label:
            w = len(label) * EDIT_TEXT_W
            sx = clamp((DISPLAY_WIDTH - w) // 2, 0, max(0, DISPLAY_WIDTH - w))
            _text2x(d, label, sx, EDIT_LABEL_Y, 255)

    def _edit_value(self, d, v):
        # Readout row below the track: "0   <value>   127", all 1x (the value the
        # same size as the end labels). The raw value is 1x -- not 2x -- so its
        # refresh band is a single 8px row (~half the I2C of the old 2x number),
        # which keeps a fast sweep from holding the bus long enough to starve the
        # audio render. The 2x treatment is reserved for the friendly bucket name
        # (see _edit_label), which only redraws when it actually changes.
        d.fill_rect(0, EDIT_ENDS_Y, DISPLAY_WIDTH, CHAR_H, 0)
        d.text('0', 0, EDIT_ENDS_Y, 110)
        d.text('127', DISPLAY_WIDTH - 3 * CHAR_W, EDIT_ENDS_Y, 110)
        vs = '%d' % v
        w = len(vs) * CHAR_W
        vx = clamp((DISPLAY_WIDTH - w) // 2, 0, max(0, DISPLAY_WIDTH - w))
        d.text(vs, vx, EDIT_ENDS_Y, 255)

    def _edit_track(self, d, cx):
        # The 0-127 track line plus the cursor tick, drawn together in the cursor
        # band (cleared first so the old tick position is erased).
        band_h = EDIT_TRACK_BAND_Y1 - EDIT_TRACK_BAND_Y0 + 1
        d.fill_rect(0, EDIT_TRACK_BAND_Y0, DISPLAY_WIDTH, band_h, 0)
        d.fill_rect(EDIT_LINE_X0, EDIT_TRACK_Y, EDIT_LINE_X1 - EDIT_LINE_X0, 1, 180)
        d.fill_rect(clamp(cx - 1, 0, DISPLAY_WIDTH - 3),
                    EDIT_TRACK_Y - EDIT_TICK_H, 3, EDIT_TICK_H * 2 + 1, 255)

    def _render_edit(self, cur):
        # 0-127 slider editor. On open/resume (cur.full) do one full clear+draw
        # flushed in audio-safe bands. On a turn/MIDI update push ONLY the moving
        # bands -- the cursor and the 1x value readout row -- (and the 2x friendly
        # bucket name only when it changes), so dialing stays snappy AND the small
        # per-detent I2C doesn't starve the audio render. The value is applied live
        # in handle() regardless, so sound tracks every detent even when a redraw
        # is throttled to the next frame.
        if not (self.dirty or cur.dirty):
            return
        now = time.ticks_ms()
        if not cur.full and time.ticks_diff(now, self._edit_last_render) < EDIT_REFRESH_MS:
            return                    # throttle incremental redraws (keep dirty)
        self.dirty = False
        cur.dirty = False
        self._edit_last_render = now
        try:
            d = amyboard.display
            p = cur.param
            v = cur.value
            span = EDIT_LINE_X1 - EDIT_LINE_X0
            cx = EDIT_LINE_X0 + int(round(v * span / 127.0))
            label = p.fmt(v) if p.fmt else None
            if cur.full:
                d.fill(0)
                d.text(p.label.upper()[:MENU_LABEL_MAX], 0, EDIT_TITLE_Y, 255)
                self._edit_label(d, label)
                self._edit_track(d, cx)
                self._edit_value(d, v)      # draws the 0 / value / 127 readout row
                cur.prev_label = label
                cur.prev_cx = cx
                cur.full = False
                self._panel_dirty_to = 128   # editor owned the full screen
                # Full clear on open/resume: the prior screen (a display mode's
                # screensaver dot can sit anywhere) must be wiped before the editor
                # draws. The per-turn snappiness comes from the windowed incremental
                # pushes below, not from trimming this one-time open flush.
                _begin_flush(0, 127)
                return
            # Incremental: push the moving parts as NARROW COLUMN WINDOWS instead
            # of full-width bands, so a detent is only a few ms of I2C -- snappier
            # AND less bus time than before (strictly safer for the audio render).
            #   * cursor band: window spans just old->new cursor x (+/-2 for the
            #     tick width); a single detent moves ~1px, so the strip is tiny
            #     (an accelerated jump widens it but is a one-off).
            #   * value readout: a fixed central window covers every 1-3 digit
            #     value while leaving the static "0"/"127" end labels untouched.
            # The value is applied live in handle() regardless, so sound tracks
            # every detent even when a redraw is throttled to the next frame.
            self._edit_track(d, cx)
            px = cur.prev_cx if cur.prev_cx is not None else cx
            if not _push_window(min(px, cx) - 2, max(px, cx) + 2,
                                EDIT_TRACK_BAND_Y0, EDIT_TRACK_BAND_Y1):
                amyboard.display_refresh()
            cur.prev_cx = cx
            self._edit_value(d, v)
            if not _push_window(40, 88, EDIT_ENDS_Y, EDIT_ENDS_Y + CHAR_H - 1):
                amyboard.display_refresh()
            if label != cur.prev_label:
                self._edit_label(d, label)
                if not _push_rows(EDIT_LABEL_Y, EDIT_LABEL_Y + EDIT_TEXT_H - 1):
                    amyboard.display_refresh()
                cur.prev_label = label
        except Exception:
            pass

    def _name_row(self, d, y, s):
        # Clear one 8px row and draw 1x text centered in it.
        d.fill_rect(0, y, DISPLAY_WIDTH, CHAR_H, 0)
        w = len(s) * CHAR_W
        sx = clamp((DISPLAY_WIDTH - w) // 2, 0, max(0, DISPLAY_WIDTH - w))
        d.text(s, sx, y, 255)

    def _draw_toast(self, msg):
        # Full-screen centered confirmation (1x), pushed progressively.
        try:
            d = amyboard.display
            d.fill(0)
            w = len(msg) * CHAR_W
            sx = clamp((DISPLAY_WIDTH - w) // 2, 0, max(0, DISPLAY_WIDTH - w))
            d.text(msg, sx, 60, 255)
            self._panel_dirty_to = 128   # toast owned the full screen
            _begin_flush(0, 127)
        except Exception:
            pass

    def _render_name(self, cur):
        # Preset-name entry, drawn 1x (2x was too slow). On open/resume do a full
        # clear; on a turn/click push only the two rows that move -- the name and
        # the candidate -- so scrolling the ring stays snappy.
        if not (self.dirty or cur.dirty):
            return
        self.dirty = False
        cur.dirty = False
        full = cur.full or self._needs_clear
        try:
            d = amyboard.display
            # Name so far + a cursor block. Tail-truncated to fit (you append at the
            # end, so the tail is the part worth showing).
            disp = cur.name + '_'
            maxc = DISPLAY_WIDTH // CHAR_W
            if len(disp) > maxc:
                disp = disp[-maxc:]
            # Current ring candidate -- the thing a click acts on.
            item = _NAME_RING[cur.sel]
            cand = item if item in ('DEL', 'OK') else \
                ('[space]' if item == ' ' else item)
            if full:
                d.fill(0)
                d.text('NAME PRESET', 0, EDIT_TITLE_Y, 255)
                self._name_row(d, EDIT_LABEL_Y, disp)
                self._name_row(d, EDIT_VALUE_Y, cand)
                cur.full = False
                self._needs_clear = False
                self._panel_dirty_to = 128   # name entry owned the full screen
                _begin_flush(0, 127)
                return
            self._name_row(d, EDIT_LABEL_Y, disp)
            if not _push_rows(EDIT_LABEL_Y, EDIT_LABEL_Y + CHAR_H - 1):
                amyboard.display_refresh()
            self._name_row(d, EDIT_VALUE_Y, cand)
            if not _push_rows(EDIT_VALUE_Y, EDIT_VALUE_Y + CHAR_H - 1):
                amyboard.display_refresh()
        except Exception:
            pass

    def render(self):
        # If a progressive full-repaint flush is in flight, keep pushing bands
        # and defer any new drawing until the panel is settled.
        if _flush_active:
            _service_flush()
            return
        if self._toast_msg:
            # A confirmation toast owns the screen until it times out, then the
            # menu underneath repaints.
            if time.ticks_diff(time.ticks_ms(), self._toast_until) < 0:
                if not self._toast_drawn:
                    self._draw_toast(self._toast_msg)
                    self._toast_drawn = True
                return
            self._toast_msg = ''
            self.dirty = True
            self._needs_clear = True
        if not self.is_open:
            return
        cur = self.cur
        if isinstance(cur, _EditLevel):
            self._render_edit(cur)
            return
        if isinstance(cur, _NameLevel):
            self._render_name(cur)
            return
        if not self.dirty:
            return
        self.dirty = False
        try:
            d = amyboard.display
            lvl = self.cur
            n = len(lvl.items)
            # Pagination: the visible window is a fixed PAGE of MENU_VISIBLE items
            # aligned to page boundaries (page = idx // MENU_VISIBLE). Moving the
            # cursor WITHIN a page leaves the window fixed, so a step repaints only
            # the two selection rows (fast, no freeze); the whole-page repaint fires
            # only when the cursor crosses into a new page -- once every MENU_VISIBLE
            # items, not on every step past a sliding edge (the old edge-scroll,
            # which re-flushed the full window each step and made long lists feel
            # laggy). A row of page squares at the bottom-right (its own row below
            # the list) keeps you oriented across pages without crowding the header.
            if n <= MENU_VISIBLE:
                start = 0
                total_pages = 1
            else:
                start = (lvl.idx // MENU_VISIBLE) * MENU_VISIBLE
                total_pages = (n + MENU_VISIBLE - 1) // MENU_VISIBLE
            cur_page = start // MENU_VISIBLE     # 0-based index of the shown page
            lvl.start = start
            # Current frame = title row(s) + visible item rows, as diffable tuples.
            # A title may hold newlines (used by confirm prompts) -> up to three 1x
            # header lines; items begin below them (a trailing '' line leaves a blank
            # gap). A plain single-line title behaves exactly as before.
            frame = []
            ty = 0
            for tline in lvl.title.split('\n')[:3]:
                frame.append((ty, 't', (tline, '')))
                ty += 9
            y = max(MENU_TOP_Y, ty)
            i = start
            while i < n and i < start + MENU_VISIBLE:
                frame.append((y, 'i', (i == lvl.idx, lvl.items[i][0])))
                y += MENU_LINE_H
                i += 1
            # Page marker on its own row at the bottom-right (multi-page lists only),
            # so the header stays uncrowded. It sits below the last item row and is
            # diffed like any other row: unchanged while paging within a page (so a
            # cursor move is still a 2-row push), redrawn on a page cross.
            if total_pages > 1:
                frame.append((MENU_PAGE_Y, 'q', (total_pages, cur_page)))
            # Full repaint on open / level change (clears whatever was on screen
            # before), pushed progressively in bands so audio isn't stalled.
            # Otherwise push ONLY the rows that changed -- a cursor move touches
            # just two rows -- so navigating never holds the I2C bus for long.
            if self._needs_clear or self._prev is None or len(self._prev) != len(frame):
                d.fill(0)
                extent = 0
                for (ry, kind, payload) in frame:
                    self._draw_row(d, ry, kind, payload)
                    h = MENU_LINE_H if kind == 'i' else 9
                    if ry + h > extent:
                        extent = ry + h
                if total_pages > 1:          # the bottom page row clears a full band
                    extent = max(extent, MENU_PAGE_Y + MENU_LINE_H)
                # Flush only the occupied rows -- but at least as far as the panel
                # was last painted, so a taller predecessor screen's leftover rows
                # (a longer list, or a full-screen editor/toast/display mode) are
                # still cleared. Short menus (root, Presets, confirm) thus repaint
                # in far fewer bands than a blind 0..127 flush.
                flush_to = min(127, max(extent, self._panel_dirty_to) - 1)
                _begin_flush(0, flush_to)
                self._panel_dirty_to = extent
                self._needs_clear = False
            else:
                changed = [j for j in range(len(frame))
                           if frame[j] != self._prev[j]]
                if len(changed) <= 2:
                    # Cursor move within a static window: only the two selection
                    # rows changed -- push them now (responsive, ~2 short bands).
                    for j in changed:
                        ry, kind, payload = frame[j]
                        self._draw_row(d, ry, kind, payload)
                        if not _push_rows(ry, ry + MENU_LINE_H - 1):
                            amyboard.display_refresh()
                else:
                    # Window scrolled (only at an edge now, thanks to edge-scroll):
                    # every visible row shifted, so ~9 rows changed. Pushing them
                    # all synchronously is ~150ms of I2C in one loop, which starves
                    # AMY's audio render and makes the LFO/vibrato stutter. Draw
                    # them, then blit PROGRESSIVELY over just the changed span (one
                    # band per loop) so no single loop holds the bus for long.
                    ys = []
                    for j in changed:
                        ry, kind, payload = frame[j]
                        self._draw_row(d, ry, kind, payload)
                        ys.append(ry)
                    _begin_flush(min(ys), max(ys) + MENU_LINE_H - 1)
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
    have_input = launcher.delta or launcher.click or launcher.back
    if have_input:
        _last_input_ms = now

    if menu.suspended:
        # Idled out: the display mode is showing. ANY input -- turn, click, or
        # hold -- wakes us back to exactly where we were (the waking input just
        # resumes, it doesn't act). While suspended we report depth >= 2 so the
        # launcher delivers a hold to us (as back) instead of escaping to the
        # global menu; a hold only reaches global from an ACTIVE (non-idle) root.
        if have_input:
            menu.resume()
        _prev_menu_open = menu.is_open
        launcher.menu_depth = max(2, menu.depth) if menu.suspended else menu.depth
        return

    if menu.is_open:
        menu.handle(launcher.delta, launcher.click, launcher.back)
        menu.service_pending(now)             # fire a deferred editor single-click
        if menu.is_open and time.ticks_diff(now, _last_input_ms) >= MENU_IDLE_MS:
            # Idle timeout SUSPENDS at whatever level we're on: the display mode
            # takes over the screen but the menu stack (level + cursor position,
            # or the editor's value) is kept, so the next input resumes us exactly
            # where we left off. Closing only happens on explicit user action.
            menu.suspend()
            launcher.repaint = True           # redraw the display mode over us
    elif launcher.click or launcher.delta:   # a click OR a turn opens our menu
        menu.open()              # the opening input just opens (no scroll yet)
    if _prev_menu_open and not menu.is_open:
        launcher.repaint = True
    _prev_menu_open = menu.is_open
    # While suspended (incl. the tick idle fires) report depth >= 2 so a hold is
    # delivered to us to resume, not routed to the global menu by the launcher.
    launcher.menu_depth = max(2, menu.depth) if menu.suspended else menu.depth


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
    # Standalone (no wrapper): we are the sole encoder reader, so pump our own
    # reader first to fill launcher.delta/.click/.back. Wrapped: the wrapper has
    # already filled those around this call, so skip (and it clears them itself).
    if _STANDALONE:
        launcher.update()

    # Drive the encoder-driven menu from the launcher's input events first.
    _pump_menu()

    # The instrument stays live even while the menu is open: keep servicing CV
    # (notes/audio also keep running via the MIDI callback regardless).
    _service_cv()

    if menu.is_open:
        menu.render()                # the menu owns the OLED while open
    else:
        # Playing: after returning from our menu or a global Resume, repaint once
        # so the display mode redraws over any leftover menu/overlay pixels.
        if launcher.repaint:
            launcher.repaint = False
            _force_display_redraw()
        # Display last so a CV read error never blocks the screen, and vice versa.
        service_display()

    # Standalone: clear the one-shot events so the menu never re-consumes them
    # next tick (the wrapper does this itself after driving a wrapped sketch).
    if _STANDALONE:
        launcher.delta = 0
        launcher.click = False
        launcher.back = False