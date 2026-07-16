# AMYboard Sketch
# DESCRIPTION: 2-oscillator (A/B) analog-style synth matching the frozen CC map.
#   Stepped musical tuning per osc, 6-way wave buckets (no wavetable/PCM/ALGO),
#   resonant filter with VCF envelope + key tracking, VCA envelope, plus a
#   per-voice LFO routed to pitch, PWM and filter, plus a control-rate analog
#   drift (smooth-random pitch wander -- tape wow/warble). 6-voice polyphony. MIDI ch12
#   notes (auto-routed to synth 12 by AMY) + CCs (20-32, 40-47, 71, 74, 76-80)
#   handled via midi.add_callback. (CV in/out support was attempted and removed --
#   see CV_attempt.md for what we learned.)
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
# Global pitch transpose in whole octaves (both oscs shift together, keyboard
# tracking preserved). Exists to reconcile the middle-C naming convention: a
# controller/sequencer that labels MIDI note 60 "C3" (Yamaha) sits one octave
# above AMY's rendering, so the default is -1 oct. Spare CC.
CC_OCTAVE      = 34
CC_FLT_ENV_AMT = 30
CC_FLT_TYPE    = 31
CC_KEY_SCALE   = 32
# Velocity -> filter cutoff depth (octaves at full velocity). A 'vel' coefficient
# on the filter head's filter_freq; unipolar (harder = brighter). Spare CC.
CC_VEL_FILT    = 33
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
# Velocity->amp sensitivity DEPTH (0 = ignore velocity, 1 = fully sensitive). A
# master output-stage response control (Output menu category). Spare CC.
CC_VEL_SENS    = 44
# Envelope curve SHAPE (AMY eg_type), one of 4 discrete types (see ENV_SHAPES).
# Filter env = eg1 on the filter head; amp env = eg0 on the sounding oscs.
CC_FLT_ENV_SHAPE = 45
CC_AMP_ENV_SHAPE = 46
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

# Analog drift (control-rate smooth-random pitch wander -- tape wow/warble). NOT
# an AMY oscillator: AMY's LFO shapes are all periodic (its NOISE is white, not a
# slow wander), so a slow ORGANIC drift is synthesized in loop() (see
# service_drift) and folded into both oscs' freq const. Two spare CCs in the gap
# after the osc block -- depth (amount) and rate (wander speed). Depth 0 = OFF
# (default), so existing patches are unchanged.
CC_DRIFT_DEPTH = 28
CC_DRIFT_RATE  = 29

# Global master effects (AMY's EQ / chorus / echo / reverb). Unlike every CC
# above, these are NOT per-osc/per-synth: AMY runs one instance of each on the
# final mix bus, so their handle_cc branches issue GLOBAL amy.send()s (no synth=/
# osc=). They still flow into presets like any other CC (the preset snapshot is
# just param_values). Grouped with gaps like the synth CCs; all stay clear of the
# reserved 120-127 channel-mode range. Chorus 'max delay' and echo 'max delay' are
# deliberately NOT CCs -- they size delay buffers (a reallocation would click on
# every preset load), so they are fixed once at init (see FX_* constants).
# Master output level -- AMY's global bus volume (post-FX, pre-softclip). Global
# like the effects, and per-patch (in presets) so a mono lead can run hot while a
# dense chord patch backs off to stay clean. Spare CC (not MIDI's CC 7 Channel
# Volume) to avoid any firmware auto-mapping; CC 7 could later alias to it if an
# external volume fader is wanted, the way the mod wheel aliases LFO->pitch.
CC_MASTER_VOL  = 84
CC_EQ_LOW      = 85
CC_EQ_MID      = 86
CC_EQ_HIGH     = 87
CC_CHO_LEVEL   = 90
CC_CHO_DEPTH   = 91
CC_CHO_RATE    = 92
CC_ECHO_LEVEL  = 95
CC_ECHO_TIME   = 96
CC_ECHO_FBK    = 97
CC_ECHO_TONE   = 98
CC_REV_LEVEL   = 100
CC_REV_DECAY   = 101
CC_REV_DAMP    = 102
CC_REV_XOVER   = 103

# The MIDI mod wheel is the standard vibrato-depth controller, so we treat CC 1 as
# an alias for CC_LFO_PITCH (handle_cc remaps it): a performer's wheel adds/removes
# vibrato out of the box, and it shares the one global LFO->pitch depth (so it also
# reflects in Param Control and is captured by presets).
CC_MODWHEEL    = 1

# Filter type buckets for CC 31 (4 even bands across 0-127).
FILTER_TYPES = [amy.FILTER_LPF24, amy.FILTER_LPF, amy.FILTER_BPF, amy.FILTER_HPF]

# Envelope shapes for CC 45/46 (4 even buckets across 0-127). AMY's eg_type values
# are 0=NORMAL, 1=LINEAR, 2=DX7, 3=TRUE_EXPONENTIAL; we present them ordered
# straightest -> most curved (Linear, Normal, True Exp), with DX7 last as the
# character option (exponential decay + a bespoke Yamaha attack). ENV_SHAPES holds
# the eg_type value per bucket; ENV_SHAPE_NAMES the label, same order.
ENV_SHAPES      = [1, 0, 3, 2]
ENV_SHAPE_NAMES = ('Linear', 'Normal', 'True Exp', 'DX7')

# Tuning reference: AMY's freq 'const' is the Hz at MIDI note 69 when the 'note'
# coefficient is 1.0, so const = REF_HZ * 2**(cents/1200) applies a fixed cents
# offset while still tracking the keyboard. Both oscs reference 440 (unison).
REF_HZ = 440.0

# Filter cutoff sweep range (Hz), mapped logarithmically from CC 74.
CUTOFF_MIN_HZ = 30.0
CUTOFF_MAX_HZ = 16000.0

# Filter envelope depth is an EG1 coefficient in octave-ish (logfreq) units.
# BIPOLAR (cc 30 centered at 64): the amount ranges -FLT_ENV_AMT_MAX..+MAX, where
# +ve opens the filter as the envelope rises and -ve inverts it (closes). ~1.0 is
# a few octaves of sweep; max keeps it musical, not extreme.
FLT_ENV_AMT_MAX = 2.0
VEL_FILT_DEPTH_MAX = 2.0   # velocity->cutoff, octaves at full velocity (0..+2)

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

# --- Analog-drift ranges (control-rate smooth-random pitch wander) -----------
# Depth is the +/- pitch excursion in cents at full knob (linear so the low end
# resolves finely -- a subtle few-cent wobble is the common analog setting). Rate
# is the wander SPEED: how many fresh random targets the wander eases through per
# second (logarithmic), from a very slow drift up to a fast warble. See
# service_drift() for the smooth-random generator.
DRIFT_DEPTH_MAX_CENTS = 100.0  # +/- cents at CC 127 (0 = OFF, the default); a full
                               # semitone each way at the top for extreme lo-fi
DRIFT_RATE_MIN_HZ     = 0.05   # ~1 new target / 20 s at CC 0 (slow drift)
DRIFT_RATE_MAX_HZ     = 12.0   # ~12 new targets / s at CC 127 (fast warble/flutter)
DRIFT_TICK_MS         = 25     # Minimum gap between drift updates -- a FLOOR, not the
                               # actual rate. Measured: the firmware calls loop() only
                               # every ~69 ms, so any value below ~69 means "every tick"
                               # and tuning within 0..69 does nothing at all. Do not
                               # reach for this as a "send rate" knob: it is inert in
                               # that range, and the two amy.send()s a tick issues cost
                               # only ~2 ms each -- they were never the expensive part.
                               # (The thing that made drift lag the menu was reading
                               # amy.millis(), at ~97 ms/call; service_drift() now uses
                               # time.ticks_ms(). See the note there.) Trade-off that
                               # remains real: AMY applies freq changes instantly (no
                               # ramp), so max-depth + max-rate is audibly grainy; the
                               # usual musical settings stay smooth.

# --- Master FX ranges (AMY global EQ / chorus / echo / reverb) ---------------
# The 0-127 CC -> real-value maps for the master effects. Level/depth/damp/decay
# are plain unit (cc_unit) 0..1 fractions and need no constant. Feedback-style
# knobs are capped below 1.0 so a runaway can't self-oscillate to a full-scale
# blast. The two "max delay" values are FIXED buffer sizes (see the CC block):
# changing them reallocates a delay line -- a click on every preset load -- so
# they are set once at init and the sweepable knobs move a read pointer within.
# AMY's wire values (verified against src/amy.c + src/parse.c): EQ bands are sent
# in dB -- AMY applies powf(10, dB/20) itself, so 0 dB = flat and we send dB with
# no conversion on our side. Chorus/reverb/echo 'level' are additive wet SENDS
# (out += level*wet; dry stays full), not wet/dry mixes, so 0 = bypass and there
# is no "100% wet" -- hence the "Level" naming.
MASTER_VOL_MAX      = 6.0    # AMY bus volume at CC 127 (CC 84 ~= 3.98 = +12 dB, the
                             # default); AMY scales output by 0.1*volume, so vol 1.0
                             # = the old -20 dBFS baseline, readout is dB relative to it
EQ_DB_MAX           = 12.0   # +/- dB per band at CC 0/127 (CC 64 = flat, 0 dB)
CHORUS_RATE_MIN_HZ  = 0.1    # chorus LFO sweep rate at CC 0
CHORUS_RATE_MAX_HZ  = 10.0   #                          at CC 127 (log curve)
CHORUS_MAX_DELAY    = 320    # fixed chorus delay-line length (samples); base delay
ECHO_TIME_MIN_MS    = 1      # echo tap time at CC 0
ECHO_TIME_MAX_MS    = 740    #                at CC 127 (just under the buffer below)
ECHO_MAX_DELAY_MS   = 743    # fixed echo buffer (AMY's own default max); sized once
ECHO_FBK_MAX        = 0.95   # echo feedback at CC 127 (< 1.0: repeats always decay)
ECHO_TONE_MAX       = 0.95   # one-pole damping coef in the feedback path at CC 127
REV_XOVER_MIN_HZ    = 100    # reverb damping-crossover freq at CC 0
REV_XOVER_MAX_HZ    = 8000   #                               at CC 127 (log curve)

# (CV in/out support was attempted here and removed -- see CV_attempt.md.)

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
DISPLAY_REFRESH_MS  = 100     # min gap between refreshes. This is a CEILING of ~10 fps,
                              # not the rate you get: loop() only runs every ~69 ms
                              # (measured), so the gate passes every 2nd tick and the
                              # real refresh rate is ~139 ms / ~7 fps. Any value in
                              # 0..69 would be inert (the gate could never fire).
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
    CC_DRIFT_DEPTH: 'DRIFT', CC_DRIFT_RATE: 'DRF HZ',
}

# Shared display state (owned by the display dispatcher, not by any one mode).
DISPLAY_OK = False
_display_last_render = 0      # ticks_ms of the last refresh (throttle gate)
_boot_ms = 0                  # ticks_ms at display init; gates the boot-banner wipe
_boot_cleared = False

# ---------------------------------------------------------------------------
# Live patch state (musical defaults; overwritten by incoming CCs)
# ---------------------------------------------------------------------------
# Global pitch transpose in whole octaves, applied to both oscs in osc_freq().
# Defaults to -1 so a "note 60 == C3" (Yamaha-convention) controller plays at the
# expected pitch; adjustable per-patch via CC_OCTAVE / the Osc-group Octave param.
octave = -1

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
vel_filt_depth = 0.0   # velocity->cutoff depth in octaves (0 = off); see CC_VEL_FILT

vcf_env = {'a': 0, 'd': 350, 's': 0.2, 'r': 300}
vca_env = {'a': 0, 'd': 200, 's': 1, 'r': 350}

# Velocity->amp sensitivity depth (see osc_amp). 0.30 default: a strong reduction
# from AMY's fully-sensitive 1.0, so a soft touch speaks near full while hard hits
# are unchanged -- tuned for a gentle player. Per-patch (captured by presets).
vel_sens = 0.30

# Envelope curve shapes (AMY eg_type). Default NORMAL (0) for both -- the analog-
# ish default. amp = eg0 on oscs A/B; filter = eg1 on the filter head. Per-patch.
amp_eg_type = 0
flt_eg_type = 0

# LFO defaults: depths start at 0 so the LFO is inaudible until a knob is moved.
lfo_freq        = 0.0
lfo_wave        = amy.SINE
lfo_pitch_depth = 0.0
lfo_pwm_depth   = 0.0
lfo_filt_depth  = 0.0
lfo_amp_a_depth = 0.0
lfo_amp_b_depth = 0.0

# Analog-drift state. Depth 0 = OFF (default) so a fresh boot and every legacy
# preset are unchanged. `_drift_cents` is the live wander offset that osc_freq()
# folds into both oscs' pitch; the rest is the smooth-random generator's own
# state, driven at control rate by service_drift(). _drift_rng is a tiny built-in
# LCG (seeded lazily from the clock) so we depend on no `random` module.
drift_depth_cents = 0.0    # +/- cents excursion at full wander (0 = off)
drift_rate_hz     = 0.40   # wander speed (targets/s); mirrors the 'Drift Rate'
                           # Param default (raw 48 ~ 0.40 Hz) so a fresh boot
                           # wanders sensibly the moment Amt is raised
_drift_cents   = 0.0       # current offset in cents, read by osc_freq()
_drift_prev    = 0.0       # previous random target (-1..1)
_drift_next    = 0.0       # next random target (-1..1)
_drift_phase   = 0.0       # 0..1 progress from prev -> next target
_drift_last_ms = 0         # time.ticks_ms() at the last serviced tick
_drift_seeded  = False
_drift_rng     = 1         # LCG state

# Master FX state (real values, not raw CCs), mirrored from handle_cc. Every
# effect starts OFF (level 0 / EQ flat), so a fresh boot and every legacy preset
# sound exactly as before -- FX are purely additive until dialed in. update_eq/
# chorus/echo/reverb re-send the whole effect from this dict whenever any one of
# its knobs moves (AMY's effect calls take the full parameter set at once).
fx = {
    'eq_l': 0.0, 'eq_m': 0.0, 'eq_h': 0.0,          # dB per band, 0 = flat (AMY converts)
    'cho_level': 0.0, 'cho_depth': 0.5, 'cho_rate': 0.5,
    'echo_level': 0.0, 'echo_time': 250, 'echo_fbk': 0.3, 'echo_tone': 0.0,
    'rev_level': 0.0, 'rev_decay': 0.85, 'rev_damp': 0.5, 'rev_xover': 3000.0,
}

# Master output level (AMY global bus volume). Default ~3.98 (+12 dB over AMY's
# quiet 1.0 baseline) so a single note sits near modular levels; per-patch, so a
# dense-chord patch can back this off to keep peaks under the soft-clip knee.
master_vol = 3.98


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


def cc_to_octave(cc):
    # Five equal-ish buckets across 0-127 -> whole-octave transpose -2..+2.
    cc = clamp(int(cc), 0, 127)
    if cc <= 25:
        return -2
    if cc <= 51:
        return -1
    if cc <= 76:
        return 0
    if cc <= 102:
        return 1
    return 2


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


def cc_bipolar(cc):
    # -1.0..+1.0 with 64 as center, the standard MIDI bipolar convention
    # (cc 0 -> -1.0, cc 64 -> 0.0, cc 127 -> +1.0).
    cc = clamp(int(cc), 0, 127)
    if cc <= 64:
        return (cc - 64) / 64.0
    return (cc - 64) / 63.0


def cc_to_flt_env_amt(cc):
    # Bipolar EG1 depth: +ve opens the filter as the envelope rises; -ve inverts
    # it so the envelope CLOSES the filter. Center (cc 64) = 0.0 = no effect.
    return cc_bipolar(cc) * FLT_ENV_AMT_MAX


def cc_to_filter_type(cc):
    cc = clamp(int(cc), 0, 127)
    idx = (cc * len(FILTER_TYPES)) // 128
    return FILTER_TYPES[min(idx, len(FILTER_TYPES) - 1)]


def cc_to_eg_type(cc):
    # 4 even buckets -> the AMY eg_type value for that bucket (ENV_SHAPES order).
    cc = clamp(int(cc), 0, 127)
    idx = (cc * len(ENV_SHAPES)) // 128
    return ENV_SHAPES[min(idx, len(ENV_SHAPES) - 1)]


def cc_to_time_ms(cc):
    u = cc_unit(cc)
    return int(ENV_TIME_MIN_MS + (u * u) * (ENV_TIME_MAX_MS - ENV_TIME_MIN_MS))


def cc_to_drift_depth(cc):
    # Linear +/- cents excursion (0 at CC 0). Linear so the low, musically useful
    # few-cent region resolves finely rather than being crushed by a log curve.
    return cc_unit(cc) * DRIFT_DEPTH_MAX_CENTS


def cc_to_drift_rate(cc):
    # Logarithmic wander speed (targets/second), like the LFO/chorus rates.
    return DRIFT_RATE_MIN_HZ * math.pow(
        DRIFT_RATE_MAX_HZ / DRIFT_RATE_MIN_HZ, cc_unit(cc))


def cc_to_lfo_freq(cc):
    return LFO_FREQ_MIN_HZ * math.pow(LFO_FREQ_MAX_HZ / LFO_FREQ_MIN_HZ, cc_unit(cc))


def cc_to_lfo_pitch(cc):
    u = cc_unit(cc)
    return (u * u) * LFO_PITCH_DEPTH_MAX           # octaves, quadratic


def cc_to_lfo_pwm(cc):
    return cc_unit(cc) * LFO_PWM_DEPTH_MAX


def cc_to_lfo_filt(cc):
    return cc_unit(cc) * LFO_FILT_DEPTH_MAX


def cc_to_vel_filt(cc):
    # Unipolar velocity->cutoff depth, 0..+VEL_FILT_DEPTH_MAX octaves.
    return cc_unit(cc) * VEL_FILT_DEPTH_MAX


def cc_to_lfo_amp(cc):
    return cc_unit(cc) * LFO_AMP_DEPTH_MAX


# --- Master FX 0-127 maps ----------------------------------------------------
def cc_to_master_vol(cc):
    # AMY bus volume, linear 0..MASTER_VOL_MAX (CC 84 ~= 3.98 = +12 dB default).
    return cc_unit(cc) * MASTER_VOL_MAX


def cc_to_eq_db(cc):
    # Bipolar band gain in dB: CC 64 = 0 dB (flat), 0/127 = -/+ EQ_DB_MAX. AMY's
    # eq wire value IS dB -- it applies powf(10, dB/20) itself (src/amy.c apply
    # delta) -- so we send this straight through, no gain conversion on our side.
    return cc_bipolar(cc) * EQ_DB_MAX


def cc_to_chorus_rate(cc):
    return CHORUS_RATE_MIN_HZ * math.pow(
        CHORUS_RATE_MAX_HZ / CHORUS_RATE_MIN_HZ, cc_unit(cc))


def cc_to_echo_time(cc):
    return int(ECHO_TIME_MIN_MS + cc_unit(cc) * (ECHO_TIME_MAX_MS - ECHO_TIME_MIN_MS))


def cc_to_echo_fbk(cc):
    return cc_unit(cc) * ECHO_FBK_MAX


def cc_to_echo_tone(cc):
    return cc_unit(cc) * ECHO_TONE_MAX


def cc_to_rev_xover(cc):
    return REV_XOVER_MIN_HZ * math.pow(
        REV_XOVER_MAX_HZ / REV_XOVER_MIN_HZ, cc_unit(cc))


# ---------------------------------------------------------------------------
# AMY graph builders
# ---------------------------------------------------------------------------
# Unity pass-through amp for the SILENT filter head: constant 1.0, no velocity
# or envelope (the VCA lives on A/B). map_60dB_to_01f(1.0) == 1.0, so amp == 1.
HEAD_AMP = {'const': 1.0, 'vel': 0, 'eg0': 0}


def osc_freq(cents):
    # const in Hz at note 69, note coef 1.0 -> tracks keyboard with cents offset.
    # `octave` shifts the reference by whole octaves (global transpose); folding it
    # into the exponent keeps note-tracking intact -- every note moves together.
    # `_drift_cents` is the control-rate analog-drift wander (service_drift), added
    # in cents so it rides on top of tuning/transpose and moves both oscs together.
    # 'mod' adds the shared LFO vibrato at the global depth (unit-per-octave), so
    # A and B track the same vibrato -- the standard mod-wheel form.
    return {'const': REF_HZ * math.pow(2.0, (cents + _drift_cents) / 1200.0 + octave),
            'note': 1, 'mod': lfo_pitch_depth}


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
    #
    # `vel` is the velocity-sensitivity DEPTH (vel_sens): AMY's amp coefs combine
    # in a log domain where a full-velocity note contributes 0 regardless of the
    # vel coef, so lowering it lifts SOFT notes toward full while leaving hard hits
    # unchanged (a velocity-depth control, no makeup gain needed). 1.0 = fully
    # velocity-sensitive, 0.0 = velocity-independent (organ-like).
    base = clamp(level, 0.0, 1.0) * math.pow(10.0, -3.0 * mod_depth)
    return {'const': base, 'vel': vel_sens, 'eg0': 1, 'mod': mod_depth}


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
            'mod': lfo_filt_depth, 'vel': vel_filt_depth}


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
             bp1=flt_bp(), eg1_type=flt_eg_type,
             mod_source=LFO_OSC,
             chained_osc=OSC_A)

    # Sounding oscs A and B carry the VCA: velocity + amp envelope (bp0) so they
    # release and self-terminate on note-off. eg0_type sets the amp env curve.
    amy.send(synth=SYNTH, osc=OSC_A,
             wave=a_wave, freq=osc_freq(a_cents), duty=osc_duty(a_duty),
             amp=osc_amp(a_level, lfo_amp_a_depth), bp0=vca_bp(), eg0_type=amp_eg_type,
             mod_source=LFO_OSC,
             chained_osc=OSC_B)

    amy.send(synth=SYNTH, osc=OSC_B,
             wave=b_wave, freq=osc_freq(b_cents), duty=osc_duty(b_duty),
             amp=osc_amp(b_level, lfo_amp_b_depth), bp0=vca_bp(), eg0_type=amp_eg_type,
             mod_source=LFO_OSC)

    # Per-voice LFO. amp=1.0 sets full modulation strength (per-target depth is
    # set by each 'mod' coef); no vel is sent and it is named as a mod_source,
    # so AMY keeps it silent and free-running.
    amy.send(synth=SYNTH, osc=LFO_OSC,
             wave=lfo_wave, freq=lfo_freq, amp=1.0)

    # Master effects are global (not tied to this synth), but init them here so
    # the fixed delay buffers exist and every effect is armed at its default
    # (all OFF) before any preset restore replays FX CCs.
    init_fx()


def update_octave():
    # Global transpose changed: re-send both sounding oscs' freq (each folds the
    # new `octave` into its const via osc_freq). Live -- held notes glide, not cut.
    amy.send(synth=SYNTH, osc=OSC_A, freq=osc_freq(a_cents))
    amy.send(synth=SYNTH, osc=OSC_B, freq=osc_freq(b_cents))


def update_filter_freq():
    amy.send(synth=SYNTH, osc=FILT_OSC, filter_freq=filter_freq_coefs())


def update_vca():
    # VCA envelope now lives on the sounding oscs, so update both A and B.
    amy.send(synth=SYNTH, osc=OSC_A, bp0=vca_bp())
    amy.send(synth=SYNTH, osc=OSC_B, bp0=vca_bp())


def update_vcf():
    amy.send(synth=SYNTH, osc=FILT_OSC, bp1=flt_bp())


def update_vca_shape():
    # Amp envelope (eg0) curve, carried by both sounding oscs.
    amy.send(synth=SYNTH, osc=OSC_A, eg0_type=amp_eg_type)
    amy.send(synth=SYNTH, osc=OSC_B, eg0_type=amp_eg_type)


def update_vcf_shape():
    # Filter envelope (eg1) curve, on the filter head.
    amy.send(synth=SYNTH, osc=FILT_OSC, eg1_type=flt_eg_type)


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


def update_vel_sens():
    # vel_sens lives inside osc_amp, so re-send the full amp for both sounding oscs.
    update_lfo_amp_a()
    update_lfo_amp_b()


# --- Analog drift (control-rate smooth-random pitch wander) ------------------
# Emulates tape wow / analog oscillator drift. AMY's own LFO can only produce
# PERIODIC shapes (its NOISE wave is full-rate white noise, not a slow wander), so
# a slow, organic, non-repeating drift can't come from the audio engine. Instead
# we generate a smooth-random bipolar "wander" here at control rate and fold it
# into both oscs' freq const (osc_freq), re-sending A and B when it moves. This is
# exactly the job MicroPython is fast enough for: sub-audio-rate parameter nudging.
def _drift_rand():
    # Tiny self-contained LCG -> bipolar float in [-1, 1). Avoids depending on a
    # `random` module being present on the board's MicroPython build.
    global _drift_rng
    _drift_rng = (_drift_rng * 1103515245 + 12345) & 0x7fffffff
    return (_drift_rng / 0x40000000) - 1.0


def service_drift():
    # Called every loop() tick. Off (depth 0) => no work and no sends, so the
    # default state costs nothing. Otherwise advance a phase from one random target
    # to the next and ease between them with a smoothstep (zero velocity at each
    # end => no kinks), giving an organic, aperiodic wander. Fresh targets are
    # picked on each crossing, so it never repeats.
    global _drift_cents, _drift_prev, _drift_next, _drift_phase
    global _drift_last_ms, _drift_seeded, _drift_rng
    if drift_depth_cents <= 0.0:
        return
    # MUST be time.ticks_ms(), never amy.millis(). They return the SAME millisecond
    # clock, but an amy.millis() read costs ~97 ms against ~12 us (both measured on
    # the board) -- so once per tick here is enough to stall loop() and lag the menu.
    # Do not "tidy" this back to amy.millis() for consistency with the amy.* calls
    # around it: every other clock read in this file is time.ticks_ms() too.
    now = time.ticks_ms()
    if not _drift_seeded:
        _drift_rng = (now & 0x7fffffff) or 1        # seed from the clock
        _drift_next = _drift_rand()
        _drift_last_ms = now
        _drift_seeded = True
        return
    dt = time.ticks_diff(now, _drift_last_ms)   # wraparound-safe (ticks_ms wraps)
    if dt < DRIFT_TICK_MS:
        return
    _drift_last_ms = now
    _drift_phase += (dt / 1000.0) * drift_rate_hz
    while _drift_phase >= 1.0:            # crossed into the next segment(s)
        _drift_phase -= 1.0
        _drift_prev = _drift_next
        _drift_next = _drift_rand()
    s = _drift_phase * _drift_phase * (3.0 - 2.0 * _drift_phase)   # smoothstep
    new_cents = (_drift_prev + (_drift_next - _drift_prev) * s) * drift_depth_cents
    # Re-send only when the pitch has moved >= ~1 cent. Caps the send rate and, by
    # keeping each step below the pitch JND, makes the control-rate updates read as
    # smooth even though AMY applies freq const changes immediately (no ramp).
    if abs(new_cents - _drift_cents) >= 1.0:
        _drift_cents = new_cents
        amy.send(synth=SYNTH, osc=OSC_A, freq=osc_freq(a_cents))
        amy.send(synth=SYNTH, osc=OSC_B, freq=osc_freq(b_cents))


def update_drift_depth():
    # When drift is turned OFF, snap the pitch back to base -- service_drift stops
    # running at depth 0, so it can't clear the residual offset itself.
    global _drift_cents
    if drift_depth_cents <= 0.0 and _drift_cents != 0.0:
        _drift_cents = 0.0
        amy.send(synth=SYNTH, osc=OSC_A, freq=osc_freq(a_cents))
        amy.send(synth=SYNTH, osc=OSC_B, freq=osc_freq(b_cents))


# --- Master FX senders -------------------------------------------------------
# GLOBAL sends (no synth=/osc=): AMY applies these once to the whole mix. We drive
# them through the CORE wire protocol -- amy.send(eq=/chorus=/reverb=/echo=) with
# comma-joined value strings -- rather than the amy.chorus()/reverb()/echo() Python
# helpers, because that path depends only on amy.send() (guaranteed present) and
# not on helper functions that may be absent on the board's AMY build. The field
# names + argument ORDER below are taken verbatim from src/parse.c:
#   eq      = "low,mid,high"                          (linear gains, 1.0 = flat)
#   chorus  = "level,max_delay,lfo_freq,depth"
#   reverb  = "level,liveness,damping,xover_hz"
#   echo    = "level,delay_ms,max_delay_ms,feedback,filter_coef"
# Each re-sends its effect's full parameter set from the `fx` dict. Guarded so a
# malformed send can never take down MIDI/menu handling (prints to serial).
def _fx_send(what, **kwargs):
    try:
        amy.send(**kwargs)
    except Exception as e:
        print('FX %s send failed:' % what, e)


def update_eq():
    _fx_send('eq', eq='%g,%g,%g' % (fx['eq_l'], fx['eq_m'], fx['eq_h']))


def update_chorus():
    # max_delay is fixed (buffer size); only level/depth/rate are live knobs.
    _fx_send('chorus', chorus='%g,%d,%g,%g' % (
        fx['cho_level'], CHORUS_MAX_DELAY, fx['cho_rate'], fx['cho_depth']))


def update_echo():
    # max_delay_ms is the fixed buffer; echo_time moves the tap within it.
    _fx_send('echo', echo='%g,%d,%g,%g,%g' % (
        fx['echo_level'], fx['echo_time'], ECHO_MAX_DELAY_MS,
        fx['echo_fbk'], fx['echo_tone']))


def update_reverb():
    _fx_send('reverb', reverb='%g,%g,%g,%g' % (
        fx['rev_level'], fx['rev_decay'], fx['rev_damp'], fx['rev_xover']))


def update_master_vol():
    # GLOBAL bus volume (post-FX, pre-softclip). AMY scales output by 0.1*volume.
    _fx_send('volume', volume=master_vol)


def init_fx():
    # Push the whole FX state to AMY once at startup so the (fixed) delay buffers
    # are allocated and every effect is in a known state before the first preset
    # replays its own FX CCs on top. Master volume too, so the patch is at its
    # (louder-than-AMY-default) level even before a preset loads.
    update_eq()
    update_chorus()
    update_echo()
    update_reverb()
    update_master_vol()


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

# The preset the user most recently loaded OR saved this session. It is the target
# of the Save menu's "Overwrite" shortcut, so an existing preset can be updated
# without retyping its whole name. Just the name string -- no reload is needed to
# know it. Empty until something is loaded or saved; a Save/Load path keeps it in
# sync. If the named preset is later deleted, the Save menu falls back to name
# entry (it re-checks existence), so a stale name can't overwrite the wrong thing.
_current_preset_name = ''


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


# --- Built-in INIT preset ---------------------------------------------------
# A VIRTUAL, write-protected preset representing the synth's init state (every
# param's PARAMS default). It is not stored in flash -- it is synthesized on
# demand, so it is always exactly the current defaults, can never go stale, and
# can't be overwritten or deleted (it isn't in _presets). It always appears first
# in the Load list, giving a one-click return to a known clean starting point.
INIT_PRESET_NAME = 'INIT'


def _init_preset_entry():
    # Raw 0-127 snapshot of the init defaults, shaped like a saved preset's 'cc'.
    return {'name': INIT_PRESET_NAME,
            'cc': {str(p.cc): int(p.default) for p in PARAMS}}


def _load_list():
    # Presets offered by the Load menu: the virtual INIT first, then saved ones.
    return [_init_preset_entry()] + _presets


def _restore_current_preset():
    # Boot: re-apply the preset the user last loaded/saved (its name is persisted
    # in settings), so a reset resumes that patch instead of the bare defaults.
    # Runs after init_synth() so the amy.send()s in _apply_preset land on the built
    # graph. INIT (or an unknown/deleted name) needs no work -- the defaults are
    # already loaded -- so it just records the current name (or stays on init).
    global _current_preset_name
    name = _settings.get('current_preset')
    if not name:
        return
    if name == INIT_PRESET_NAME:
        _current_preset_name = INIT_PRESET_NAME
        return
    i = _find_preset(name)
    if i < 0:
        return
    try:
        _apply_preset(_presets[i].get('cc'))
        _current_preset_name = name
    except Exception:
        pass


# ---------------------------------------------------------------------------
# CC dispatch -- each CC updates only its parameter live (no voice reset, so
# held notes are never cut off).
# ---------------------------------------------------------------------------
def handle_cc(cc, val):
    global octave
    global a_cents, a_wave, a_duty, a_level
    global b_cents, b_wave, b_duty, b_level
    global flt_cutoff, flt_res, flt_type, flt_env_amt, key_scale, vel_filt_depth
    global lfo_freq, lfo_wave, lfo_pitch_depth, lfo_pwm_depth, lfo_filt_depth
    global lfo_amp_a_depth, lfo_amp_b_depth, master_vol, vel_sens
    global amp_eg_type, flt_eg_type
    global drift_depth_cents, drift_rate_hz

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
    elif cc == CC_OCTAVE:
        octave = cc_to_octave(val)
        update_octave()
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
    elif cc == CC_VEL_FILT:
        vel_filt_depth = cc_to_vel_filt(val)
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
    elif cc == CC_VEL_SENS:
        vel_sens = cc_unit(val)
        update_vel_sens()
    elif cc == CC_FLT_ENV_SHAPE:
        flt_eg_type = cc_to_eg_type(val)
        update_vcf_shape()
    elif cc == CC_AMP_ENV_SHAPE:
        amp_eg_type = cc_to_eg_type(val)
        update_vca_shape()
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
    elif cc == CC_DRIFT_DEPTH:
        drift_depth_cents = cc_to_drift_depth(val)
        update_drift_depth()   # snaps pitch back to base if this turned drift OFF
    elif cc == CC_DRIFT_RATE:
        drift_rate_hz = cc_to_drift_rate(val)   # takes effect on the next segment
    # --- Master output + FX: update global state, then re-send --------------
    elif cc == CC_MASTER_VOL:
        master_vol = cc_to_master_vol(val)
        update_master_vol()
    elif cc == CC_EQ_LOW:
        fx['eq_l'] = cc_to_eq_db(val)   # dB on the wire; AMY converts to gain
        update_eq()
    elif cc == CC_EQ_MID:
        fx['eq_m'] = cc_to_eq_db(val)
        update_eq()
    elif cc == CC_EQ_HIGH:
        fx['eq_h'] = cc_to_eq_db(val)
        update_eq()
    elif cc == CC_CHO_LEVEL:
        fx['cho_level'] = cc_unit(val)
        update_chorus()
    elif cc == CC_CHO_DEPTH:
        fx['cho_depth'] = cc_unit(val)
        update_chorus()
    elif cc == CC_CHO_RATE:
        fx['cho_rate'] = cc_to_chorus_rate(val)
        update_chorus()
    elif cc == CC_ECHO_LEVEL:
        fx['echo_level'] = cc_unit(val)
        update_echo()
    elif cc == CC_ECHO_TIME:
        fx['echo_time'] = cc_to_echo_time(val)
        update_echo()
    elif cc == CC_ECHO_FBK:
        fx['echo_fbk'] = cc_to_echo_fbk(val)
        update_echo()
    elif cc == CC_ECHO_TONE:
        fx['echo_tone'] = cc_to_echo_tone(val)
        update_echo()
    elif cc == CC_REV_LEVEL:
        fx['rev_level'] = cc_unit(val)
        update_reverb()
    elif cc == CC_REV_DECAY:
        fx['rev_decay'] = cc_unit(val)
        update_reverb()
    elif cc == CC_REV_DAMP:
        fx['rev_damp'] = cc_unit(val)
        update_reverb()
    elif cc == CC_REV_XOVER:
        fx['rev_xover'] = cc_to_rev_xover(val)
        update_reverb()
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
# and is fully wrapped so a display fault can never disturb audio/MIDI.
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
    # currently selected. Any mode error is swallowed so audio/MIDI continue.
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


def fmt_octave(v):
    # Whole-octave transpose, bucketed like cc_to_octave(); 0 reads 'Center'.
    o = cc_to_octave(v)
    return 'Center' if o == 0 else '%+d oct' % o


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


def fmt_env_shape(v):
    # Four envelope curve types, bucketed like cc_to_eg_type() (ENV_SHAPE_NAMES).
    idx = (clamp(int(v), 0, 127) * len(ENV_SHAPE_NAMES)) // 128
    return ENV_SHAPE_NAMES[min(idx, len(ENV_SHAPE_NAMES) - 1)]


def fmt_flt_env(v):
    # Bipolar amount, signed octaves; exact center reads 'Center'. Mirrors
    # cc_to_flt_env_amt() so the label matches the sound.
    amt = round(cc_to_flt_env_amt(v), 1)
    return 'Center' if amt == 0 else '%+.1f oct' % amt


def fmt_vel_filt(v):
    # Unipolar velocity->cutoff depth in octaves; 0 reads 'Off'.
    amt = round(cc_to_vel_filt(v), 1)
    return 'Off' if amt == 0 else '+%.1f oct' % amt


def fmt_drift_depth(v):
    # Drift excursion in +/- cents; 0 reads 'Off' (the wander is disabled).
    c = int(round(cc_to_drift_depth(v)))
    return 'Off' if c == 0 else '+/-%dc' % c


def fmt_drift_rate(v):
    # Wander speed in Hz (random targets eased through per second).
    hz = cc_to_drift_rate(v)
    return '%.2f Hz' % hz if hz < 1.0 else '%.1f Hz' % hz


# --- Master FX readouts (each mirrors its cc_to_* map) -----------------------
def fmt_master_vol(v):
    # dB relative to AMY's old default (volume 1.0). vol 1 -> 0 dB, ~3 -> +10 dB,
    # near-zero -> 'Mute'. Coarse integer-dB scale, so this param is `stepped`.
    vol = cc_to_master_vol(v)
    if vol < 0.05:
        return 'Mute'
    db = int(round(20.0 * math.log10(vol)))
    return '0 dB' if db == 0 else '%+d dB' % db


def fmt_eq_db(v):
    # Signed dB; exact center reads 'Flat'. Rounded to whole dB for a compact read.
    db = int(round(cc_to_eq_db(v)))
    return 'Flat' if db == 0 else '%+d dB' % db


def fmt_pct(v):
    # Plain 0..100% unit knob (chorus/reverb levels, depth, damping, decay).
    return '%d%%' % int(round(cc_unit(v) * 100))


def fmt_echo_fbk(v):
    return '%d%%' % int(round(cc_to_echo_fbk(v) * 100))


def fmt_echo_tone(v):
    return '%d%%' % int(round(cc_to_echo_tone(v) * 100))


def fmt_echo_time(v):
    return '%d ms' % cc_to_echo_time(v)


def fmt_chorus_rate(v):
    return '%.1f Hz' % cc_to_chorus_rate(v)


def fmt_hz_khz(v):
    # Reverb damping crossover: Hz below 1k, kHz above, for a tidy short label.
    hz = cc_to_rev_xover(v)
    return '%d Hz' % int(round(hz)) if hz < 1000 else '%.1f kHz' % (hz / 1000.0)


def _bucket_centers(fmt):
    # One representative CC per distinct display bucket: the center of each run of
    # equal fmt() labels across 0..127. Derived from the SAME fmt the editor shows,
    # so the steps can never drift from the displayed buckets (e.g. 6 waveforms ->
    # 6 centers, 4 filter types -> 4 centers).
    labels = [fmt(cc) for cc in range(128)]
    steps = []
    i = 0
    while i < 128:
        j = i
        while j < 128 and labels[j] == labels[i]:
            j += 1
        steps.append((i + j - 1) // 2)      # center of this run
        i = j
    return steps


def _bucket_advance(steps, value, delta):
    # Snap to the nearest bucket, then move by the raw detent count (one detent =
    # one bucket; the list is short so quadratic accel would just overshoot).
    idx = min(range(len(steps)), key=lambda i: abs(steps[i] - value))
    idx = clamp(idx + int(delta), 0, len(steps) - 1)
    return steps[idx]


class _Param:
    __slots__ = ('label', 'cc', 'default', 'fmt', 'bipolar', 'steps', 'group', 'sub')

    def __init__(self, label, cc, default, fmt=None, bipolar=False, stepped=False,
                 group='', sub=''):
        self.label = label
        self.cc = cc
        self.default = default   # raw 0-127 value used until a CC/editor sets one
        self.fmt = fmt           # optional value(0-127) -> friendly label
        self.bipolar = bipolar   # editor readout shows a signed scale (0 = center)
        self.group = group       # Param Control category (see PARAM_GROUPS)
        self.sub = sub           # optional sub-bucket within a category (e.g. Env
                                 # -> VCF/VCA); '' = the category lists params flat
        # "Bucketed" params (a few discrete display values): one detent jumps to
        # the next distinct bucket instead of crawling through identical CCs.
        self.steps = _bucket_centers(fmt) if (stepped and fmt) else None


# The editable-parameter list. Adding a row here is all it takes to expose another
# parameter in the editor -- its `group` slots it under the right Param Control
# category automatically (see PARAM_GROUPS / _open_params). `default` is the raw
# 0-127 value that reproduces the patch's initial default (the double-click reset
# target); the fine ADSR-time defaults are the CCs closest to the initial ms.
# Labels are kept short enough that "NN. label" clears 128px in the per-category
# lists (numbering restarts at 1 per category, so counts stay single/low-double).
# A few are shortened from the requested names -- Filt Type, Kbd Track, the ADSR
# Atk/Dec/Sus/Rel forms, the space-free Lfo>X routing, and the FX Cho/Rev prefixes.
PARAMS = [
    _Param('Osc A Pitch', CC_OSC_A_PITCH,  64, fmt_osc_pitch, bipolar=True, stepped=True, group='Osc'),
    _Param('Osc A Shape', CC_OSC_A_WAVE,   52, fmt_wave, stepped=True, group='Osc'),
    _Param('Osc A Duty',  CC_OSC_A_DUTY,   64, group='Osc'),
    _Param('Osc A Level', CC_OSC_A_LEVEL, 127, group='Osc'),
    _Param('Osc B Pitch', CC_OSC_B_PITCH,  64, fmt_osc_pitch, bipolar=True, stepped=True, group='Osc'),
    _Param('Osc B Shape', CC_OSC_B_WAVE,    0, fmt_wave, stepped=True, group='Osc'),
    _Param('Osc B Duty',  CC_OSC_B_DUTY,   64, group='Osc'),
    _Param('Osc B Level', CC_OSC_B_LEVEL,   0, group='Osc'),
    _Param('Octave',      CC_OCTAVE,        38, fmt_octave, bipolar=True, stepped=True, group='Osc'),
    # VCF: filter controls. The filter envelope leads, in its own 'Env' sub-bucket
    # (ADSR + curve Shape; Shape default raw 48 = the 'Normal' bucket, stepped one
    # detent per curve type), then Cutoff/Resonance, the env AMOUNT, velocity->
    # cutoff depth, filter type, and keyboard tracking. The Env sub params must stay
    # contiguous -- _open_param_group places the "Env >" drill-in where they first
    # appear (first here, so it is VCF's first entry).
    _Param('A',          CC_VCF_ATK,       0, group='VCF', sub='Env'),
    _Param('D',          CC_VCF_DEC,      34, group='VCF', sub='Env'),
    _Param('S',          CC_VCF_SUS,      25, group='VCF', sub='Env'),
    _Param('R',          CC_VCF_REL,      31, group='VCF', sub='Env'),
    _Param('Shape',      CC_FLT_ENV_SHAPE, 48, fmt_env_shape, stepped=True, group='VCF', sub='Env'),
    _Param('Cutoff',      CC_FLT_CUTOFF,  127, group='VCF'),
    _Param('Resonance',   CC_FLT_RES,       0, group='VCF'),
    _Param('Filter Env',  CC_FLT_ENV_AMT,  64, fmt_flt_env, bipolar=True, group='VCF'),
    _Param('Vel>Filter',  CC_VEL_FILT,      0, fmt_vel_filt, group='VCF'),
    _Param('Filt Type',   CC_FLT_TYPE,     48, fmt_filter_type, stepped=True, group='VCF'),
    _Param('Kbd Track',   CC_KEY_SCALE,     0, group='VCF'),
    _Param('Lfo Freq',    CC_LFO_FREQ,      0, group='LFO'),
    _Param('Lfo Shape',   CC_LFO_WAVE,      0, fmt_wave, stepped=True, group='LFO'),
    _Param('Lfo>Pitch',   CC_LFO_PITCH,     0, group='LFO'),
    _Param('Lfo>Pwm',     CC_LFO_PWM,       0, group='LFO'),
    _Param('Lfo>Filter',  CC_LFO_FILT,      0, group='LFO'),
    _Param('Lfo>Amp A',   CC_LFO_AMP_A,     0, group='LFO'),
    _Param('Lfo>Amp B',   CC_LFO_AMP_B,     0, group='LFO'),
    # Analog drift: a control-rate smooth-random pitch wander (tape wow/warble),
    # separate from the audio LFO. Amt default 0 = off (patches unchanged); Rate
    # default raw 48 ~ 0.3 Hz, a gentle wander for when Amt is dialed up.
    _Param('Drift Amt',   CC_DRIFT_DEPTH,   0, fmt_drift_depth, group='LFO'),
    _Param('Drift Rate',  CC_DRIFT_RATE,   48, fmt_drift_rate, group='LFO'),
    # VCA: amp controls. The amp envelope in its own 'Env' sub-bucket (ADSR +
    # curve Shape), then velocity->amp sensitivity and the master output Level
    # (renamed from the old 'Output'; per-patch master volume, default +12 dB).
    _Param('A',          CC_VCA_ATK,       0, group='VCA', sub='Env'),
    _Param('D',          CC_VCA_DEC,      25, group='VCA', sub='Env'),
    _Param('S',          CC_VCA_SUS,     127, group='VCA', sub='Env'),
    _Param('R',          CC_VCA_REL,      34, group='VCA', sub='Env'),
    _Param('Shape',      CC_AMP_ENV_SHAPE, 48, fmt_env_shape, stepped=True, group='VCA', sub='Env'),
    _Param('Vel>Amp',    CC_VEL_SENS,   38, fmt_pct, group='VCA'),
    _Param('Level',      CC_MASTER_VOL, 84, fmt_master_vol, stepped=True, group='VCA'),
    # Master FX (global effects). Defaults leave every effect OFF: EQ flat (64),
    # chorus/echo/reverb level 0. Chorus depth/rate and reverb decay/damp/xover
    # carry musical defaults so raising just their Level lands on a usable sound.
    # `stepped` is set on the params whose readout is a COARSE discrete scale (dB,
    # log Hz) where several raw CCs share one label -- so one detent advances to
    # the next distinct value instead of clicking through dead steps (like the
    # wave/filter-type buckets). The near-continuous knobs (levels/depth/times, one
    # display value per detent already) stay smooth so fast sweeps keep their accel.
    _Param('EQ Low',     CC_EQ_LOW,     64, fmt_eq_db, bipolar=True, stepped=True, group='FX'),
    _Param('EQ Mid',     CC_EQ_MID,     64, fmt_eq_db, bipolar=True, stepped=True, group='FX'),
    _Param('EQ High',    CC_EQ_HIGH,    64, fmt_eq_db, bipolar=True, stepped=True, group='FX'),
    _Param('Cho Level',  CC_CHO_LEVEL,   0, fmt_pct, group='FX'),
    _Param('Cho Depth',  CC_CHO_DEPTH,  64, fmt_pct, group='FX'),
    _Param('Cho Rate',   CC_CHO_RATE,   44, fmt_chorus_rate, stepped=True, group='FX'),
    _Param('Echo Level', CC_ECHO_LEVEL,  0, fmt_pct, group='FX'),
    _Param('Echo Time',  CC_ECHO_TIME,  43, fmt_echo_time, group='FX'),
    _Param('Echo Fbk',   CC_ECHO_FBK,   40, fmt_echo_fbk, group='FX'),
    _Param('Echo Tone',  CC_ECHO_TONE,   0, fmt_echo_tone, group='FX'),
    _Param('Rev Level',  CC_REV_LEVEL,   0, fmt_pct, group='FX'),
    _Param('Rev Decay',  CC_REV_DECAY, 108, fmt_pct, group='FX'),
    _Param('Rev Damp',   CC_REV_DAMP,   64, fmt_pct, group='FX'),
    _Param('Rev Xover',  CC_REV_XOVER,  99, fmt_hz_khz, stepped=True, group='FX'),
]

# Param Control categories, in display order. Each opens a filtered PARAMS list;
# a param's `group` (above) decides where it lands, so the two never drift. VCF
# and VCA further split into sub-buckets via `sub` (see _open_param_group).
PARAM_GROUPS = ('Osc', 'VCF', 'LFO', 'VCA', 'FX')

# Last raw 0-127 value seen per editable CC, seeded with each param's default.
param_values = {p.cc: p.default for p in PARAMS}

# Ultra-short (<=5 char, 8px font -> <=40px, fits a ~42px grid cell) labels for the
# Param Control knob grid cells. The header shows each param's FULL label; the cells
# use these tight abbreviations. One dict (vs. touching every _Param) keeps it in one
# place. ADSR labels repeat across VCF/VCA but never collide (different groups/pages).
GRID_LABELS = {
    CC_OSC_A_PITCH: 'APIT', CC_OSC_A_WAVE: 'AWAV', CC_OSC_A_DUTY: 'ADTY', CC_OSC_A_LEVEL: 'ALVL',
    CC_OSC_B_PITCH: 'BPIT', CC_OSC_B_WAVE: 'BWAV', CC_OSC_B_DUTY: 'BDTY', CC_OSC_B_LEVEL: 'BLVL',
    CC_OCTAVE: 'OCT',
    CC_VCF_ATK: 'ATK', CC_VCF_DEC: 'DEC', CC_VCF_SUS: 'SUS', CC_VCF_REL: 'REL',
    CC_FLT_ENV_SHAPE: 'FSHP', CC_FLT_CUTOFF: 'CUT', CC_FLT_RES: 'RES', CC_FLT_ENV_AMT: 'FENV',
    CC_VEL_FILT: 'VFLT', CC_FLT_TYPE: 'FTYP', CC_KEY_SCALE: 'KEY',
    CC_LFO_FREQ: 'LHZ', CC_LFO_WAVE: 'LWAV', CC_LFO_PITCH: 'VIB', CC_LFO_PWM: 'PWM',
    CC_LFO_FILT: 'LFLT', CC_LFO_AMP_A: 'TRMA', CC_LFO_AMP_B: 'TRMB',
    CC_DRIFT_DEPTH: 'DRFT', CC_DRIFT_RATE: 'DRHZ',
    CC_VCA_ATK: 'ATK', CC_VCA_DEC: 'DEC', CC_VCA_SUS: 'SUS', CC_VCA_REL: 'REL',
    CC_AMP_ENV_SHAPE: 'ASHP', CC_VEL_SENS: 'VAMP', CC_MASTER_VOL: 'VOL',
    CC_EQ_LOW: 'EQLO', CC_EQ_MID: 'EQMD', CC_EQ_HIGH: 'EQHI',
    CC_CHO_LEVEL: 'CHLV', CC_CHO_DEPTH: 'CHDP', CC_CHO_RATE: 'CHRT',
    CC_ECHO_LEVEL: 'ECLV', CC_ECHO_TIME: 'ECTM', CC_ECHO_FBK: 'ECFB', CC_ECHO_TONE: 'ECTN',
    CC_REV_LEVEL: 'RVLV', CC_REV_DECAY: 'RVDC', CC_REV_DAMP: 'RVDP', CC_REV_XOVER: 'RVXO',
}

# Header (full-name) overrides for params whose _Param.label is a poor standalone
# name once flattened out of their sub-menu -- the ADSR envelopes (label 'A'/'D'/
# 'S'/'R') and the two env-shape params (label 'Shape'). We embed the VCF/VCA
# context here since the grid header no longer shows a separate group tag. Kept
# <=12 chars so "<name>: <value>" fits the 16-char header. Others use p.label.
GRID_HEADER_NAMES = {
    CC_VCF_ATK: 'Filt Attack', CC_VCF_DEC: 'Filt Decay',
    CC_VCF_SUS: 'Filt Sustain', CC_VCF_REL: 'Filt Release',
    CC_VCA_ATK: 'Amp Attack', CC_VCA_DEC: 'Amp Decay',
    CC_VCA_SUS: 'Amp Sustain', CC_VCA_REL: 'Amp Release',
    CC_FLT_ENV_SHAPE: 'Filt EnvShp', CC_AMP_ENV_SHAPE: 'Amp EnvShp',
}


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
# lists (e.g. the FX category's 14 params) advance a page at a time, so this also
# sets the page size shown by the "pg/total" marker. 8 fills the screen well (last
# row ends at y=114). Short menus (<=8 items) are a single page with no marker.
MENU_VISIBLE = 8
MENU_PAGE_Y = 116        # bottom row (128 - MENU_LINE_H) for the page marker; the
                         # last item row ends at y=114, leaving this row free
# Page marker = one small square per page, right-justified on MENU_PAGE_Y; the
# current page is a filled block, the others hollow outlines.
PAGE_SQ = 7              # square side (px)
PAGE_SQ_GAP = 3          # gap between squares
PAGE_SQ_MARGIN = 2       # right margin from the panel edge
MENU_LABEL_MAX = 18
MENU_IDLE_MS = 15000     # auto-close the menu to the display mode after this idle
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
NAME_ROW_Y   = 44        # name-entry: the word + inline active slot, one 1x row
# Cursor band = the track line +/- the tick, pushed as a unit each turn.
EDIT_TRACK_BAND_Y0 = EDIT_TRACK_Y - EDIT_TICK_H - 1
EDIT_TRACK_BAND_Y1 = EDIT_TRACK_Y + EDIT_TICK_H + 1
EDIT_REFRESH_MS = 16     # Min gap between editor redraws -- currently INERT, and kept
                         # only as a floor if the loop ever gets faster. loop() runs
                         # every ~69 ms (measured), so this 16 ms gate can never fire;
                         # redraws are already bounded at one per loop() because
                         # render() is called once per tick. It is not protecting
                         # against a MIDI-CC flood -- an earlier comment here claimed
                         # that, but CCs only mark the editor dirty from the callback
                         # and never draw. Deferring a detent is likewise not a risk:
                         # an incremental redraw is a couple of narrow windowed pushes.
                         # Raising this above ~69 ms WOULD start dropping frames.
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
# saves) and render as a back-arrow / check glyph in the active slot. Space is a
# real entry (a blank knocked-out slot). Scrolling CLAMPS at the ends (no wrap),
# so a fast spin lands on OK (end) or 'a' (start).
_NAME_RING = [c for c in 'abcdefghijklmnopqrstuvwxyz0123456789 '] + ['DEL', 'OK']


def _glyph_del(d, x, y, col):
    # Left-pointing arrow (backspace / delete) in an 8x8 cell at (x, y): a small
    # triangular head at the left widening rightward, then a short shaft. Spans
    # cols 1-6, leaving 1px padding on each side of the knocked-out slot.
    d.fill_rect(x + 1, y + 3, 1, 2, col)      # tip (1px left padding)
    d.fill_rect(x + 2, y + 2, 1, 4, col)
    d.fill_rect(x + 3, y + 1, 1, 6, col)      # widest part of the head
    d.fill_rect(x + 4, y + 3, 3, 2, col)      # shaft (1px shorter), ends at col 6


def _glyph_ok(d, x, y, col):
    # Check mark in an 8x8 cell at (x, y): a short down-right arm to a bottom
    # vertex, then an up-right arm. Drawn as overlapping 2px squares, spanning
    # cols 1-6 so it clears both sides of the knocked-out slot.
    for dx, dy in ((1, 3), (2, 4), (3, 5), (4, 3), (5, 1)):
        d.fill_rect(x + dx, y + dy, 2, 2, col)


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


# ---------------------------------------------------------------------------
# Param Control knob grid. A group's params are shown as a 3x4 grid of value
# bars (12/page) instead of a text list + single-slider editor. A CURSOR (bright
# bounding box) navigates cell-to-cell; a CLICK SELECTS the cell (knockout /
# reverse-video) and turning then adjusts that param live; click/back returns to
# the cursor. The header shows the focused param's FULL label + value (bucketed
# params show their word via fmt; bipolar bars anchor at center). Drawing is
# fill_rect/text only and redraws just the changed cell(s) to stay audio-safe.
# ---------------------------------------------------------------------------
GRID_COLS = 3
GRID_ROWS = 4
GRID_PER_PAGE = GRID_COLS * GRID_ROWS                 # 12 cells/page
GRID_HDR_H = 14                                        # header band height (px)
GRID_CELL_W = DISPLAY_WIDTH // GRID_COLS               # 42
GRID_CELL_H = (128 - GRID_HDR_H) // GRID_ROWS          # 28 (panel is 128 tall)
GRID_BAR_W = 32
GRID_BAR_H = 6          # bar height (px), thinned from 8 -> fewer lit OLED pixels to
                        # cut the current-coupled audio noise. Fill is GRID_BAR_H-4
                        # tall (2px here); lower further if more reduction is needed.

# Grid grayscale levels (0-255), in one place so brightness can be tuned by ear.
# Bright OLED pixels draw more current, which couples audible noise into the audio
# path (its tone shifts as the highlight lights up). Currently at the original
# bright values -- first noise lever is thinning the bars (above); dimming these is
# the next lever if needed.
GRID_C_LABEL    = 215   # unfocused cell label
GRID_C_BAR_OUT  = 110   # unfocused bar outline
GRID_C_BAR_FILL = 205   # unfocused bar fill
GRID_C_TICK     = 150   # bipolar center tick
GRID_C_CURSOR   = 255   # cursor box + its brightened content
GRID_C_KNOCK    = 255   # selected-cell knockout block
GRID_C_HDR_NAME = 210   # header param name
GRID_C_HDR_VAL  = 255   # header value
GRID_C_RULE     = 70    # header rule line
GRID_C_PAGE_OFF = 20    # inactive page-indicator mark. The panel is 4-bit (top
                        # nibble), so this is level 1 -- the dimmest still-visible
                        # step (below ~16 is fully off, which would hide the 2nd-page
                        # cue). If active/inactive still read alike, the panel can't
                        # go dimmer -> switch to a filled(active)/hollow(inactive) shape.


def _grid_disp(p, v):
    # Header value string: the param's friendly fmt (a WORD for bucketed params,
    # a unit'd number for others) when it has one, else the raw 0-127.
    if p.fmt:
        try:
            return p.fmt(v)
        except Exception:
            pass
    return str(v)


def _draw_grid_cell(d, slot, label, val01, bipolar, state):
    # slot 0..11 (position on the current page). state: 'none'|'cursor'|'selected'.
    col = slot % GRID_COLS
    row = slot // GRID_COLS
    x0 = col * GRID_CELL_W
    ctop = GRID_HDR_H + row * GRID_CELL_H
    cxc = x0 + GRID_CELL_W // 2
    d.fill_rect(x0, ctop, GRID_CELL_W, GRID_CELL_H, 0)      # clear cell first
    if state == 'selected':
        d.fill_rect(x0 + 2, ctop + 1, GRID_CELL_W - 4, GRID_CELL_H - 3, GRID_C_KNOCK)  # knockout
        fg = 0; barout = 0; barfill = 0; tick = 0
    else:
        fg = GRID_C_LABEL; barout = GRID_C_BAR_OUT; barfill = GRID_C_BAR_FILL; tick = GRID_C_TICK
        if state == 'cursor':
            fg = GRID_C_CURSOR; barout = GRID_C_CURSOR; barfill = GRID_C_CURSOR; tick = GRID_C_CURSOR
            bw = GRID_CELL_W - 2; bh = GRID_CELL_H - 1
            d.fill_rect(x0 + 1, ctop, bw, 1, GRID_C_CURSOR)
            d.fill_rect(x0 + 1, ctop + bh - 1, bw, 1, GRID_C_CURSOR)
            d.fill_rect(x0 + 1, ctop, 1, bh, GRID_C_CURSOR)
            d.fill_rect(x0 + bw, ctop, 1, bh, GRID_C_CURSOR)
    lx = cxc - (len(label) * CHAR_W) // 2
    d.text(label, max(x0 + 1, lx), ctop + 1, fg)
    bx = cxc - GRID_BAR_W // 2
    by = ctop + 12
    d.fill_rect(bx, by, GRID_BAR_W, 1, barout)
    d.fill_rect(bx, by + GRID_BAR_H - 1, GRID_BAR_W, 1, barout)
    d.fill_rect(bx, by, 1, GRID_BAR_H, barout)
    d.fill_rect(bx + GRID_BAR_W - 1, by, 1, GRID_BAR_H, barout)
    if bipolar:
        cxb = bx + GRID_BAR_W // 2
        d.fill_rect(cxb, by, 1, GRID_BAR_H, tick)          # center anchor tick
        half = (GRID_BAR_W - 4) // 2
        w = int(round(half * (val01 - 0.5) * 2))
        if w > 0:
            d.fill_rect(cxb, by + 2, w, GRID_BAR_H - 4, barfill)
        elif w < 0:
            d.fill_rect(cxb + w, by + 2, -w, GRID_BAR_H - 4, barfill)
    else:
        fw = int(round((GRID_BAR_W - 4) * val01))
        if fw > 0:
            d.fill_rect(bx + 2, by + 2, fw, GRID_BAR_H - 4, barfill)


def _draw_grid_header(d, full_label, disp):
    # Focused param's full name + value. No separate group tag (the grid implies the
    # group; ADSR/shape names embed VCF/VCA via GRID_HEADER_NAMES). Left-aligned name,
    # right-aligned value so the value is always visible; the name is clipped between.
    d.fill_rect(0, 0, DISPLAY_WIDTH, GRID_HDR_H, 0)
    maxc = DISPLAY_WIDTH // CHAR_W                          # 16 chars
    vs = str(disp)
    vw = len(vs)
    d.text(vs, DISPLAY_WIDTH - vw * CHAR_W, 2, GRID_C_HDR_VAL)   # value, right-aligned
    lab = '%s:' % full_label
    avail = maxc - vw - 1                                   # leave a 1-char gap
    if avail > 0:
        if len(lab) > avail:
            lab = lab[:avail]
        d.text(lab, 0, 2, GRID_C_HDR_NAME)                 # name, left-aligned
    d.fill_rect(0, GRID_HDR_H - 2, DISPLAY_WIDTH, 1, GRID_C_RULE)   # header rule


def _draw_grid_pages(d, page, npages):
    # Page indicator: one mark per page stacked in the 2px right margin (x >= 126),
    # which no cell ever draws or clears -- so it survives incremental cell redraws
    # without being repainted. Current page bright, others dim. Hidden for 1 page.
    if npages < 2:
        return
    w, h, gap = 2, 5, 4
    off_h = 3               # inactive mark is shorter (+dimmer) than the active one
    total = npages * h + (npages - 1) * gap
    y = (128 - total) // 2
    x = DISPLAY_WIDTH - w
    for i in range(npages):
        if i == page:
            d.fill_rect(x, y, w, h, GRID_C_HDR_VAL)                       # active: full + bright
        else:
            d.fill_rect(x, y + (h - off_h) // 2, w, off_h, GRID_C_PAGE_OFF)  # inactive: short + dim
        y += h + gap


class _GridLevel:
    # A Param Control group shown as the knob grid. `idx` is the cursor position in
    # `params` (flat, all of the group's params -- sub-buckets are flattened into
    # cells); `editing` distinguishes cursor (box) from selected (knockout). Live
    # values come from param_values, so no per-param value is cached here.
    __slots__ = ('group', 'params', 'idx', 'editing', 'entry_value', 'dirty',
                 'full', 'prev_idx', 'prev_page')

    def __init__(self, group):
        self.group = group
        self.params = [p for p in PARAMS if p.group == group]
        self.idx = 0
        self.editing = False
        self.entry_value = 0     # value snapshot when editing began (hold-to-revert)
        self.dirty = True
        self.full = True
        self.prev_idx = 0
        self.prev_page = 0


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
        self._close_after_toast = False  # when the toast dismisses, close to playing
                                         # (the display mode) instead of repainting
                                         # the menu -- used by the load confirmation
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
        elif isinstance(cur, _GridLevel) and cur.editing and cur.params[cur.idx].cc == cc:
            cur.dirty = True   # focused param moved by an external CC -> repaint it

    def service_pending(self, now):
        # Fire a deferred editor single-click (commit + exit to the list) once the
        # double-click window passes with no second click.
        if not self._click_pending_at:
            return
        if time.ticks_diff(now, self._click_pending_at) <= EDIT_DBLCLICK_MS:
            return
        self._click_pending_at = 0
        if not self.stack:
            return
        cur = self.cur
        if isinstance(cur, _EditLevel):
            self.stack.pop()          # keep the current value, back to the list
            self.dirty = True
            self._needs_clear = True
        elif isinstance(cur, _GridLevel) and cur.editing:
            cur.editing = False       # commit: keep value, back to the cursor
            cur.dirty = True

    def _root(self):
        # Preset actions live directly on the root now (no "Presets" submenu).
        return _MenuLevel('POLYSYNTH', [
            ('Param control', self._open_params),
            ('Save as preset', self._start_save),
            ('Load preset', self._open_load),
            ('Delete preset', self._open_delete),
            ('Display mode', self._open_display),
            ('Resume playing', self.close),
        ])

    def _open_params(self):
        # First level of Param Control: the categories (Osc/Filter/Env/LFO/FX).
        # Clicking one drills into its filtered parameter list. Splitting the
        # (now ~40) params this way keeps each list short instead of one long
        # multi-page scroll.
        items = [(name, (lambda g=name: self._open_param_group(g)))
                 for name in PARAM_GROUPS]
        self.stack.append(_MenuLevel('PARAM CONTROL', items))
        self.dirty = True
        self._needs_clear = True

    def _open_param_group(self, group):
        # A category: shown as the 3x4 knob grid (_GridLevel). All of the group's
        # params become cells (sub-buckets like the VCF/VCA Env ADSR are flattened
        # inline rather than drilled into). Cursor navigates; click selects to edit.
        self.stack.append(_GridLevel(group))
        self.dirty = True
        self._needs_clear = True

    def _open_param_sub(self, group, sub):
        # Numbered list of the params in one sub-bucket of a category.
        ps = [p for p in PARAMS if p.group == group and p.sub == sub]
        items = [('%d. %s' % (i + 1, p.label), (lambda p=p: self._edit_param(p)))
                 for i, p in enumerate(ps)]
        self.stack.append(_MenuLevel(sub.upper(), items))
        self.dirty = True
        self._needs_clear = True

    def _edit_param(self, p):
        # Open the 0-127 slider editor on this param's current value.
        v = int(param_values.get(p.cc, p.default))
        self.stack.append(_EditLevel(p, v))
        self.dirty = True
        self._needs_clear = True

    def _start_save(self):
        # If a preset is "current" (last loaded or saved this session and still
        # present), offer a chooser so it can be updated without retyping: Overwrite
        # <name> / Save as New / Cancel. Otherwise -- nothing to overwrite -- go
        # straight to name entry, exactly as before (first save of the session).
        name = _current_preset_name
        if name and name != INIT_PRESET_NAME and _find_preset(name) >= 0:
            # Header: "Current preset:" / <name> / blank line, then the actions --
            # the blank row separates the header from Overwrite for readability.
            # (INIT is write-protected, so it never reaches the Overwrite chooser.)
            self.stack.append(_MenuLevel('Current preset:\n%s\n' % name[:MENU_LABEL_MAX], [
                ('Overwrite', (lambda n=name: self._confirm_overwrite(n))),
                ('Save as new', self._start_name_entry),
                ('Cancel', self._pop),
            ]))
            self.dirty = True
            self._needs_clear = True
        else:
            self._start_name_entry()

    def _start_name_entry(self):
        # Open the name-entry screen; committing it saves the live patch under a
        # new (or typed-over) name.
        self.stack.append(_NameLevel())
        self.dirty = True
        self._needs_clear = True

    def _confirm_overwrite(self, name):
        # Y/N confirm before replacing an existing preset in place. The full name is
        # shown in the header (fits: <=12 chars + quotes + '?'), then a blank line
        # before Yes/No for readability. No or hold pops back to the Save chooser.
        self.stack.append(_MenuLevel('OVERWRITE\n"%s"?\n' % name[:MENU_LABEL_MAX], [
            ('Yes', (lambda n=name: self._do_save(n))),
            ('No', self._pop),
        ]))
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
        if name.upper() == INIT_PRESET_NAME:
            # "INIT" is reserved for the built-in write-protected preset.
            self.stack.append(_MenuLevel('NAME RESERVED', [
                ('"%s" is built-in' % INIT_PRESET_NAME, None),
                ('Back', self._pop),
            ]))
            self.dirty = True
            self._needs_clear = True
            return
        exists = _find_preset(name) >= 0
        if not exists and len(_presets) >= MAX_PRESETS:
            self.stack.append(_MenuLevel('PRESETS FULL', [
                ('Max %d reached' % MAX_PRESETS, None),
                ('Back', self._pop),
            ]))
        else:
            head = 'OVERWRITE\n"%s"?' if exists else 'SAVE\n"%s"?'
            self.stack.append(_MenuLevel(head % name[:MENU_LABEL_MAX], [
                ('Yes', (lambda n=name: self._do_save(n))),
                ('No', self._pop),
            ]))
        self.dirty = True
        self._needs_clear = True

    def _do_save(self, name):
        # Persist, flash a "PRESET SAVED!" toast, and drop to the main polysynth
        # menu (the toast auto-dismisses to it -- see render()). On success this
        # name becomes the session's "current" preset (the Overwrite target).
        global _current_preset_name
        ok = _save_preset(name)
        if ok:
            _current_preset_name = name
            _set_setting('current_preset', name)   # resume it after a reset
        self.stack = [self._root()]
        self._show_toast('PRESET SAVED!' if ok else 'SAVE FAILED')
        self.dirty = True
        self._needs_clear = True

    def _show_toast(self, msg):
        self._toast_msg = msg
        self._toast_until = time.ticks_add(time.ticks_ms(), TOAST_MS)
        self._toast_drawn = False

    def _open_load(self):
        # List presets -- the built-in INIT first, then saved ones -- clicking one
        # applies it live and returns to playing. The list is never empty (INIT is
        # always present), so there's no "(none saved)" case here.
        lst = _load_list()
        items = [(lst[i].get('name', '?')[:MENU_LABEL_MAX],
                  (lambda i=i: self._load_preset(i)))
                 for i in range(len(lst))]
        self.stack.append(_MenuLevel('LOAD PRESET', items))
        self.dirty = True
        self._needs_clear = True

    def _load_preset(self, i):
        # `i` indexes _load_list() (INIT at 0, saved after). Apply it live and
        # remember it as the session's "current" one (persisted so a reset resumes
        # it), so a later Save can overwrite it without retyping the name. Then flash
        # a "PRESET LOADED!" toast (mirrors the save toast) and return to playing
        # when it dismisses -- so keep a level on the stack (menu stays "open") so
        # render() is still called to draw the toast; _close_after_toast closes us
        # to the display mode once it times out (or on any input).
        global _current_preset_name
        lst = _load_list()
        if not (0 <= i < len(lst)):
            return
        entry = lst[i]
        try:
            _apply_preset(entry.get('cc'))
        except Exception:
            pass
        name = entry.get('name', '')
        if name:
            _current_preset_name = name
            _set_setting('current_preset', name)
        self.stack = [self._root()]
        self._show_toast('PRESET LOADED!')
        self._close_after_toast = True
        self.dirty = True
        self._needs_clear = True

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
        global _current_preset_name
        i = _find_preset(name)
        if i >= 0:
            del _presets[i]
            _write_presets()
        if name == _current_preset_name:
            # Don't leave the Overwrite target / boot-restore pointing at a gone
            # preset; fall back to init.
            _current_preset_name = ''
            _set_setting('current_preset', '')
        # Flash "DELETED!" then land back on the refreshed delete list (or the root
        # menu if that was the last one).
        self.stack = [self._root()]
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
            # A confirmation toast is showing: any input just dismisses it (it does
            # not also act on the menu underneath). A load toast then closes us to
            # playing; others repaint the menu.
            if delta or click or back:
                self._toast_msg = ''
                if self._close_after_toast:
                    self._close_after_toast = False
                    self.close()
                    launcher.repaint = True
                else:
                    self.dirty = True
                    self._needs_clear = True
            return
        if not self.is_open:
            return
        lvl = self.cur
        if isinstance(lvl, _GridLevel):
            self._handle_grid(lvl, delta, click, back)
            return
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
                if lvl.param.steps:
                    # Bucketed param: one detent = one bucket (skip identical CCs).
                    lvl.value = _bucket_advance(lvl.param.steps, lvl.value, delta)
                else:
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

    def _handle_grid(self, lvl, delta, click, back):
        # SELECTED (editing): turn adjusts live; single click commits (keeps value,
        # back to cursor -- deferred so a 2nd click can arrive); DOUBLE click resets
        # to the param default (stays editing); HOLD reverts to the entry value and
        # exits. CURSOR: turn moves cell-to-cell; click selects (snapshots the value
        # for revert); hold pops back to the group chooser.
        if lvl.editing:
            p = lvl.params[lvl.idx]
            if back:                             # hold: revert + exit editing
                self._click_pending_at = 0
                handle_cc(p.cc, lvl.entry_value)
                lvl.editing = False
                lvl.dirty = True
                return
            if delta:                            # turn cancels a pending click
                self._click_pending_at = 0
                v = int(param_values.get(p.cc, p.default))
                if p.steps:                      # bucketed: one detent = one bucket
                    v = _bucket_advance(p.steps, v, delta)
                else:
                    v = clamp(v + _accel(delta), 0, 127)
                handle_cc(p.cc, v)               # applies live + records param_values
                lvl.dirty = True
            if click:
                now = time.ticks_ms()
                if self._click_pending_at and \
                        time.ticks_diff(now, self._click_pending_at) <= EDIT_DBLCLICK_MS:
                    # double click: reset to the param default, stay editing.
                    self._click_pending_at = 0
                    handle_cc(p.cc, p.default)
                    lvl.dirty = True
                else:
                    # first click: defer commit-and-exit so a 2nd click can arrive
                    # (fired by service_pending once the window passes).
                    self._click_pending_at = now
            return
        if back:
            self.stack.pop()
            self.dirty = True
            self._needs_clear = True
            return
        if delta:
            lvl.idx = clamp(lvl.idx + delta, 0, len(lvl.params) - 1)
            lvl.dirty = True
        if click:
            p = lvl.params[lvl.idx]
            lvl.entry_value = int(param_values.get(p.cc, p.default))  # for hold-revert
            lvl.editing = True
            lvl.dirty = True

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

    def _edit_value(self, d, v, bipolar=False):
        # Readout row below the track: end labels + centered current value, all 1x
        # (the value the same size as the end labels). The raw value is 1x -- not
        # 2x -- so its refresh band is a single 8px row (~half the I2C of the old 2x
        # number), which keeps a fast sweep from holding the bus long enough to
        # starve the audio render. The 2x treatment is reserved for the friendly
        # bucket name (see _edit_label), which only redraws when it changes.
        #   unipolar: "0 <v> 127"
        #   bipolar : "-64 <+/-n> +63" with 0 = center (unity); the value the knob
        #             sits at 64 reads 0, so the sign shows which way it is offset.
        d.fill_rect(0, EDIT_ENDS_Y, DISPLAY_WIDTH, CHAR_H, 0)
        if bipolar:
            lo, hi = '-64', '+63'
            n = int(v) - 64
            vs = '0' if n == 0 else ('%+d' % n)
        else:
            lo, hi = '0', '127'
            vs = '%d' % v
        d.text(lo, 0, EDIT_ENDS_Y, 110)
        d.text(hi, DISPLAY_WIDTH - len(hi) * CHAR_W, EDIT_ENDS_Y, 110)
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

    def _render_grid(self, cur):
        # Full draw on open / resume / page-change (flushed progressively);
        # otherwise redraw only the changed cell(s) + header. The value is applied
        # live in _handle_grid, so sound tracks every detent even when a redraw is
        # throttled to the next frame.
        if not (self.dirty or cur.dirty):
            return
        now = time.ticks_ms()
        page = cur.idx // GRID_PER_PAGE
        full = cur.full or self._needs_clear or (page != cur.prev_page)
        if not full and time.ticks_diff(now, self._edit_last_render) < EDIT_REFRESH_MS:
            return
        self.dirty = False
        cur.dirty = False
        self._edit_last_render = now
        try:
            d = amyboard.display
            start = page * GRID_PER_PAGE
            page_params = cur.params[start:start + GRID_PER_PAGE]
            fp = cur.params[cur.idx]                     # focused param
            fv = int(param_values.get(fp.cc, fp.default))
            hlabel = GRID_HEADER_NAMES.get(fp.cc, fp.label).upper()
            hdisp = _grid_disp(fp, fv)
            if full:
                d.fill(0)
                _draw_grid_header(d, hlabel, hdisp)
                for i, p in enumerate(page_params):
                    gi = start + i
                    st = ('selected' if cur.editing else 'cursor') if gi == cur.idx else 'none'
                    v = int(param_values.get(p.cc, p.default))
                    _draw_grid_cell(d, i, GRID_LABELS.get(p.cc, p.label[:5].upper()),
                                    clamp(v, 0, 127) / 127.0, p.bipolar, st)
                _draw_grid_pages(d, page,
                                 (len(cur.params) + GRID_PER_PAGE - 1) // GRID_PER_PAGE)
                cur.full = False
                self._needs_clear = False
                self._panel_dirty_to = 128
                cur.prev_idx = cur.idx
                cur.prev_page = page
                _begin_flush(0, 127)
                return
            # Incremental: header (focused param/value changed) + the changed cells.
            _draw_grid_header(d, hlabel, hdisp)
            if not _push_rows(0, GRID_HDR_H - 1):
                amyboard.display_refresh()
            for gi in {cur.prev_idx, cur.idx}:           # dedup (same cell when editing)
                if start <= gi < start + len(page_params):
                    i = gi - start
                    p = page_params[i]
                    st = ('selected' if cur.editing else 'cursor') if gi == cur.idx else 'none'
                    v = int(param_values.get(p.cc, p.default))
                    _draw_grid_cell(d, i, GRID_LABELS.get(p.cc, p.label[:5].upper()),
                                    clamp(v, 0, 127) / 127.0, p.bipolar, st)
                    col = i % GRID_COLS
                    row = i // GRID_COLS
                    x0 = col * GRID_CELL_W
                    ctop = GRID_HDR_H + row * GRID_CELL_H
                    if not _push_window(x0, x0 + GRID_CELL_W - 1, ctop, ctop + GRID_CELL_H - 1):
                        amyboard.display_refresh()
            cur.prev_idx = cur.idx
            cur.prev_page = page
        except Exception:
            pass

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
                self._edit_value(d, v, p.bipolar)   # draws the ends + value readout row
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
            self._edit_value(d, v, p.bipolar)
            if not _push_window(40, 88, EDIT_ENDS_Y, EDIT_ENDS_Y + CHAR_H - 1):
                amyboard.display_refresh()
            if label != cur.prev_label:
                self._edit_label(d, label)
                if not _push_rows(EDIT_LABEL_Y, EDIT_LABEL_Y + EDIT_TEXT_H - 1):
                    amyboard.display_refresh()
                cur.prev_label = label
        except Exception:
            pass

    def _draw_name_line(self, d, cur):
        # One row: the committed name, then the active append slot rendered IN
        # PLACE at the end, knocked out (black on a white block). The candidate
        # scrolls in that slot; DEL/OK show as a back-arrow / check glyph so they
        # occupy a single cell just like a letter. A space is a blank white block
        # -- itself the "a space goes here" cue.
        y = NAME_ROW_Y
        d.fill_rect(0, y, DISPLAY_WIDTH, CHAR_H, 0)
        name = cur.name
        maxc = DISPLAY_WIDTH // CHAR_W
        if len(name) + 1 > maxc:               # keep the active slot on-screen
            name = name[-(maxc - 1):]
        item = _NAME_RING[cur.sel]
        total = (len(name) + 1) * CHAR_W        # committed chars + active slot
        sx = clamp((DISPLAY_WIDTH - total) // 2, 0, max(0, DISPLAY_WIDTH - total))
        if name:
            d.text(name, sx, y, 255)            # committed chars, normal
        ax = sx + len(name) * CHAR_W            # active slot origin
        d.fill_rect(ax, y, CHAR_W, CHAR_H, 255)  # knockout background (white)
        if item == 'DEL':
            _glyph_del(d, ax, y, 0)
        elif item == 'OK':
            _glyph_ok(d, ax, y, 0)
        elif item != ' ':
            d.text(item, ax, y, 0)              # candidate letter/digit, black

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
        # Preset-name entry, drawn 1x. On open/resume do a full clear; on a
        # turn/click just repaint the single word row (word + inline active slot),
        # so scrolling the ring stays snappy.
        if not (self.dirty or cur.dirty):
            return
        self.dirty = False
        cur.dirty = False
        full = cur.full or self._needs_clear
        try:
            d = amyboard.display
            if full:
                d.fill(0)
                d.text('NAME PRESET', 0, EDIT_TITLE_Y, 255)
                self._draw_name_line(d, cur)
                cur.full = False
                self._needs_clear = False
                self._panel_dirty_to = 128   # name entry owned the full screen
                _begin_flush(0, 127)
                return
            self._draw_name_line(d, cur)
            if not _push_rows(NAME_ROW_Y, NAME_ROW_Y + CHAR_H - 1):
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
            # A confirmation toast owns the screen until it times out. When it
            # dismisses, a load toast closes us to playing (the display mode);
            # others repaint the menu underneath.
            if time.ticks_diff(time.ticks_ms(), self._toast_until) < 0:
                if not self._toast_drawn:
                    self._draw_toast(self._toast_msg)
                    self._toast_drawn = True
                return
            self._toast_msg = ''
            if self._close_after_toast:
                self._close_after_toast = False
                self.close()
                launcher.repaint = True   # let the display mode redraw over us
                return
            self.dirty = True
            self._needs_clear = True
        if not self.is_open:
            return
        cur = self.cur
        if isinstance(cur, _GridLevel):
            self._render_grid(cur)
            return
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
_restore_current_preset()   # resume the last-loaded/saved preset (after init_synth
                            # so its amy.send()s land on the built graph)
init_display()
_restore_display_mode()


def loop():
    # Standalone (no wrapper): we are the sole encoder reader, so pump our own
    # reader first to fill launcher.delta/.click/.back. Wrapped: the wrapper has
    # already filled those around this call, so skip (and it clears them itself).
    if _STANDALONE:
        launcher.update()

    # Analog drift: advance the control-rate pitch wander (no-op when off). Done
    # before the menu so the audio keeps drifting even while the menu is open.
    service_drift()

    # Drive the encoder-driven menu from the launcher's input events first.
    _pump_menu()

    if menu.is_open:
        menu.render()                # the menu owns the OLED while open
    else:
        # Playing: after returning from our menu or a global Resume, repaint once
        # so the display mode redraws over any leftover menu/overlay pixels.
        if launcher.repaint:
            launcher.repaint = False
            _force_display_redraw()
        # Display last so a display error never blocks audio/MIDI, and vice versa.
        service_display()

    # Standalone: clear the one-shot events so the menu never re-consumes them
    # next tick (the wrapper does this itself after driving a wrapped sketch).
    if _STANDALONE:
        launcher.delta = 0
        launcher.click = False
        launcher.back = False