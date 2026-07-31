# AMYboard Sketch -- ARCTOR
# DESCRIPTION: 2-oscillator (A/B) analog-style synth matching the frozen CC map.
#   Stepped musical tuning per osc, 6-way wave buckets (no wavetable/PCM/ALGO),
#   resonant filter with VCF envelope + key tracking, VCA envelope, plus a
#   per-voice LFO routed to pitch, PWM and filter, plus a control-rate analog
#   drift (smooth-random pitch wander -- tape wow/warble). 6-voice polyphony. MIDI ch12
#   notes (auto-routed to synth 12 by AMY) + CCs (20-32, 40-47, 71, 74, 76-80)
#   handled via midi.add_callback. (CV in/out support was attempted and removed --
#   see CV_attempt.md for what we learned.)
#   See docs/CC_MAPPING.md for the authoritative control map.
#
# ---------------------------------------------------------------------------
# BEFORE YOU RUN THIS -- the two things that will otherwise look like bugs
# ---------------------------------------------------------------------------
# FIRMWARE: needs the AMYboard build of 2026-07-27 or later. Portamento/glide
#   (CC 34) is sent per-oscillator with AMY's bare 'm' keyword, which older
#   builds do not carry -- on those, Glide silently does nothing while every
#   other control works. Check with: import os; os.uname().version
#
# MIDI CHANNEL: fixed at 12. Notes arrive on ch12 because AMY auto-routes MIDI
#   channel N to synth N and this instrument lives on synth 12; CCs are filtered
#   to the same channel. There is no on-device channel picker. To move it you
#   must edit BOTH coupled spots -- `SYNTH = 12` and the CC filter's `!= 11`
#   (zero-indexed, so 11 == channel 12) -- and they must agree.
#
# INSTALLING: this file is self-contained; it needs nothing else from the repo.
#   Two ways to run it, and it detects which by itself (see the launcher note
#   below): copy it to /user/sketches/ to pick it from the global launcher, or
#   copy it to /user/current/sketch.py to boot straight into it with no launcher
#   at all. VERIFIED on hardware both ways, 2026-07-30.
# ---------------------------------------------------------------------------

import amy, amyboard, midi, math, time, json

# --- Identity ---------------------------------------------------------------
# The instrument's name and version, shown on the menu root and the About screen.
# SETTINGS_FILE / PRESETS_FILE below are named from this too; see _LEGACY_* there
# for how a board written by the pre-rename build keeps its saved presets.
SKETCH_NAME  = 'ARCTOR'
VERSION      = '1.0'
VERSION_DATE = '2026_07_30'

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
#               leave the root via "Exit Menu" or the idle timeout.
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
            #                           to; leave via "Exit Menu" / the idle timeout.
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
SETTINGS_FILE = '/user/arctor_settings.json'


def _read_json(path):
    # Missing file, unreadable flash or malformed JSON all mean "no stored value"
    # -- a fresh board is the common case, not an error, and neither is worth
    # failing a boot over. Callers type-check what comes back.
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def _load_settings():
    d = _read_json(SETTINGS_FILE)
    if isinstance(d, dict):
        return d
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

# Portamento (glide) time. AMY exposes exactly ONE portamento parameter -- a time
# in ms (`portamento`, wire 'm', C `uint16_t portamento_ms`); there is no glide
# curve, legato flag or mode in the whole keyword table, so this one CC is the
# entire feature. A SPARE CC deliberately, not MIDI's standard CC 5 "Portamento
# Time": the same reasoning that kept master volume off CC 7 (see CC_MASTER_VOL)
# -- a standard number risks the firmware auto-mapping it out from under us.
CC_PORTA_TIME  = 34

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

# Filter type buckets for CC 31 (4 even bands across 0-127). FILTER_TYPE_NAMES
# holds the display label per bucket, same order -- cc_to_filter_type() and
# fmt_filter_type() index both with the same cc_bucket() call, so the type the
# synth applies and the name on screen can never disagree.
FILTER_TYPES      = [amy.FILTER_LPF24, amy.FILTER_LPF, amy.FILTER_BPF, amy.FILTER_HPF]
FILTER_TYPE_NAMES = ('LP 24', 'LP', 'BP', 'HP')

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
# Portamento glide-time ladder, in ms. Deliberately NOT a curve: glide is judged
# by feel, and the useful resolution is wildly uneven across the range. Fine where
# it matters (5 ms steps through the first 50 ms, the difference between a slur and
# a smear), coarse where it doesn't (100 ms steps past half a second, where nobody
# hears 640 vs 700). 25 rungs; index 0 = 0 ms = OFF, which is the default, so
# existing presets are unchanged. cc_to_porta_ms() buckets 0-127 across these and
# fmt_porta_ms() labels them -- adding or removing a rung re-voices any preset whose
# raw CC sits near a moved boundary, so treat the ladder as frozen once presets
# exist.
PORTA_MS_STEPS = (
    0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50,     # 5 ms   -- slur territory
    100, 150, 200, 250, 300, 350, 400, 450, 500,  # 50 ms  -- audible glide
    600, 700, 800, 900, 1000,                     # 100 ms -- lazy portamento
)

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
#       8KB framebuffer over the 400kHz I2C bus. MEASURED 2026-07-16: that costs
#       240ms of blocking time (an earlier "~150-180ms" here was the theoretical
#       8192*9/400kHz = 184ms, which ignores per-byte overhead -- reality is ~30%
#       worse, so budget from 240). _push_rows() windows it to the changed rows
#       instead: at 64 B/row, the 2 rows a cursor move touches cost ~4.5ms and a
#       12-row band ~19ms. DISPLAY_MAX_ROWS_PER_REFRESH caps rows-per-refresh so
#       a busy screen can never hold the bus long enough to delay a note-off.
# ---------------------------------------------------------------------------
DISPLAY_MAX_ENTRIES = 4       # parameters shown at once. Each is a 2-line group
                              # (name / CC + value) with a spacer, so this is params,
                              # not text rows -- see the geometry below.
DISPLAY_REFRESH_MS  = 100     # min gap between refreshes. This is a CEILING of ~10 fps,
                              # not the rate you get: loop() only runs every ~69 ms
                              # (measured), so the gate passes every 2nd tick and the
                              # real refresh rate is ~139 ms / ~7 fps. Any value in
                              # 0..69 would be inert (the gate could never fire).
DISPLAY_MAX_ROWS_PER_REFRESH = 2  # cap TEXT rows blitted per refresh so a busy screen
                                  # can't hold the I2C bus long enough to delay
                                  # note-offs; extra changed rows wait for the
                                  # next refresh (catches up within a few frames)
CC_EXPIRE_MS        = 6000    # drop a CC from the list this long after last touch
BOOT_CLEAR_MS       = 3000    # show the firmware boot banner this long, then wipe
# CC Monitor geometry. Each parameter is a GROUP of two 8px text lines at a 12px
# pitch (name, then CC + value). The groups are spread down the 128px panel: their
# text is 4 * 24 = 96px, and the leftover 32px is divided as evenly as integer
# pixels allow across the 3 gaps BETWEEN them (flush to the top and bottom edges,
# no outer margin -> gaps of 10/11/11 px). DISPLAY_ENTRY_Y holds each group's top
# y; slots are FIXED (not reflowed by how many CCs are active), so entries fill
# from the top and a new/expiring CC never shifts the others sideways in the diff.
DISPLAY_LINE_H      = 12      # vertical pixels per TEXT line (8px glyph + 4px lead)
DISPLAY_ENTRY_H     = 24      # a parameter group's two 12px text lines (gap is added between)
DISPLAY_ENTRY_Y     = tuple(i * (128 - DISPLAY_ENTRY_H) // (DISPLAY_MAX_ENTRIES - 1)
                            for i in range(DISPLAY_MAX_ENTRIES))   # (0, 34, 69, 104)
# --- COLOUR SCALE (read before touching any colour in this file) ------------
# Every colour argument to amyboard.display is a LEVEL 0-15, NOT a 0-255 intensity.
# The panel is 4bpp and MicroPython's GS4_HMSB framebuf masks the value with
# `col & 0x0f`, so the LOW nibble is what lands on screen and anything above 15 is
# silently folded. VERIFIED on hardware 2026-07-30 by drawing text at a spread of
# values and reading the nibbles back out of display._hw.buffer:
#
#     255 -> 15    244 ->  4    205 -> 13    215 ->  7
#     110 -> 14    100 ->  4     20 ->  4      8 ->  8
#
# Text is legible down to level 1 (checked by eye on the real panel), so the full
# range is usable. This file long passed 0-255 values, which is why 255 ("white")
# worked by luck -- 255 & 15 == 15 -- while 110 ("dim") rendered at level 14, one
# step off white. That is what made the About card and the menu list read flat.
# tools/grid_preview.py masked the TOP nibble until the same date, which is why no
# offline preview ever caught it.
#
# STILL ON THE 0-255 SCALE: the GRID_C_* block. Left alone deliberately -- re-tuning
# 11 constants changes how the instrument looks, which is a design call, not a bug
# fix. Their real levels are listed there.
DISPLAY_TEXT_COLOR  = 15      # full brightness (level, not intensity)
DISPLAY_WIDTH       = 128     # panel width in pixels
DISPLAY_CHAR_W      = 8       # font cell width, for right-aligning the value column

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
porta_ms = 0               # portamento/glide time in ms (0 = off, the default).
                           # AMY has no separate glide enable, so 0 IS the off
                           # switch -- see PORTA_MS_STEPS.
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


def cc_bucket(cc, n):
    # Even n-way split of 0..127 -> bucket index. Shared by every mapper/formatter
    # pair that picks from a discrete list (filter types, envelope shapes), so the
    # value the synth applies and the label on screen use the SAME boundaries.
    return min((clamp(int(cc), 0, 127) * n) // 128, n - 1)


# The six core analog waves: (highest CC of the bucket, AMY wave, display name).
# ONE table drives both cc_to_wave() and fmt_wave(), so the wave the synth picks
# and the name on the screen can never drift apart. The buckets are hand-set
# (~equal width) and FROZEN: moving a boundary would re-voice any preset whose
# raw wave CC sits on it.
WAVE_BUCKETS = (
    (20,  amy.SINE,     'Sine'),
    (41,  amy.PULSE,    'Pulse'),
    (63,  amy.SAW_DOWN, 'Saw Dn'),
    (84,  amy.SAW_UP,   'Saw Up'),
    (105, amy.TRIANGLE, 'Triangle'),
    (127, amy.NOISE,    'Noise'),
)


def _wave_bucket(cc):
    cc = clamp(int(cc), 0, 127)
    for top, wave, name in WAVE_BUCKETS:
        if cc <= top:
            return wave, name
    return WAVE_BUCKETS[-1][1], WAVE_BUCKETS[-1][2]


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
    return _wave_bucket(cc)[0]


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
    return FILTER_TYPES[cc_bucket(cc, len(FILTER_TYPES))]


def cc_to_eg_type(cc):
    # 4 even buckets -> the AMY eg_type value for that bucket (ENV_SHAPES order).
    return ENV_SHAPES[cc_bucket(cc, len(ENV_SHAPES))]


def cc_to_time_ms(cc):
    u = cc_unit(cc)
    return int(ENV_TIME_MIN_MS + (u * u) * (ENV_TIME_MAX_MS - ENV_TIME_MIN_MS))


def cc_to_porta_ms(cc):
    # Glide time, in ms, off a hand-written LADDER rather than a curve. The steps
    # coarsen where the ear stops resolving them: 5 ms apart through the first
    # 50 ms (where 5 ms is an audible difference in a fast lead slur), 50 ms apart
    # to half a second, then 100 ms to 1 s, by which point the exact number no
    # longer matters. 25 detents total -- `stepped` derives them from fmt_porta_ms,
    # so the knob lands on each rung and never crawls through identical CCs.
    return PORTA_MS_STEPS[cc_bucket(cc, len(PORTA_MS_STEPS))]


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
    # `_drift_cents` is the control-rate analog-drift wander (service_drift), added
    # in cents so it rides on top of tuning/transpose and moves both oscs together.
    # 'mod' adds the shared LFO vibrato at the global depth (unit-per-octave), so
    # A and B track the same vibrato -- the standard mod-wheel form.
    return {'const': REF_HZ * math.pow(2.0, (cents + _drift_cents) / 1200.0),
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
             portamento=porta_ms,
             chained_osc=OSC_B)

    amy.send(synth=SYNTH, osc=OSC_B,
             wave=b_wave, freq=osc_freq(b_cents), duty=osc_duty(b_duty),
             amp=osc_amp(b_level, lfo_amp_b_depth), bp0=vca_bp(), eg0_type=amp_eg_type,
             mod_source=LFO_OSC,
             portamento=porta_ms)

    # Per-voice LFO. amp=1.0 sets full modulation strength (per-target depth is
    # set by each 'mod' coef); no vel is sent and it is named as a mod_source,
    # so AMY keeps it silent and free-running.
    amy.send(synth=SYNTH, osc=LFO_OSC,
             wave=lfo_wave, freq=lfo_freq, amp=1.0)

    # Master effects are global (not tied to this synth), but init them here so
    # the fixed delay buffers exist and every effect is armed at its default
    # (all OFF) before any preset restore replays FX CCs.
    init_fx()


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


# --- Single-parameter senders (the `update` column of PARAMS) ----------------
def update_osc_a_freq():
    amy.send(synth=SYNTH, osc=OSC_A, freq=osc_freq(a_cents))


def update_osc_b_freq():
    amy.send(synth=SYNTH, osc=OSC_B, freq=osc_freq(b_cents))


def update_osc_a_wave():
    amy.send(synth=SYNTH, osc=OSC_A, wave=a_wave)


def update_osc_b_wave():
    amy.send(synth=SYNTH, osc=OSC_B, wave=b_wave)


def update_osc_a_duty():
    amy.send(synth=SYNTH, osc=OSC_A, duty=osc_duty(a_duty))


def update_osc_b_duty():
    amy.send(synth=SYNTH, osc=OSC_B, duty=osc_duty(b_duty))


def update_osc_a_level():
    # The level lives in the same amp coef set as the tremolo depth, so re-send
    # the whole set; then make sure the (possibly reaped) filter head wakes too.
    update_lfo_amp_a()
    keep_filter_head_alive()


def update_osc_b_level():
    update_lfo_amp_b()
    keep_filter_head_alive()


def update_resonance():
    amy.send(synth=SYNTH, osc=FILT_OSC, resonance=flt_res)


def update_filter_type():
    amy.send(synth=SYNTH, osc=FILT_OSC, filter_type=flt_type)


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
        update_osc_a_freq()
        update_osc_b_freq()


def update_porta():
    # Glide time onto the SOUNDING oscs. Sent per-osc, not per-synth: AMY's wire
    # code for portamento is a bare 'm', while every instrument-scoped keyword in
    # the table carries an 'i' prefix (synth_level 'iV', num_voices 'iv',
    # synth_flags 'if'), and the C declares portamento_ms inside `synthinfo` --
    # AMY's per-OSCILLATOR struct, the same one that holds `phase`. So this follows
    # the same path as wave/freq/duty/amp.
    #
    # VERIFIED BY EAR on hardware 2026-07-27 (firmware 82e69df, 2026-07-27): glide
    # sounds from the per-osc sends alone. The chain HEAD (FILT_OSC) does NOT need
    # it -- worth knowing, because a note-on propagates head -> A -> B, so the head
    # was the obvious other candidate and it turns out not to be involved.
    amy.send(synth=SYNTH, osc=OSC_A, portamento=porta_ms)
    amy.send(synth=SYNTH, osc=OSC_B, portamento=porta_ms)


def update_drift_depth():
    # When drift is turned OFF, snap the pitch back to base -- service_drift stops
    # running at depth 0, so it can't clear the residual offset itself.
    global _drift_cents
    if drift_depth_cents <= 0.0 and _drift_cents != 0.0:
        _drift_cents = 0.0
        update_osc_a_freq()
        update_osc_b_freq()


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
PRESETS_FILE = '/user/arctor_presets.json'
PRESET_NAME_MAX = 12      # longest preset name the name-entry screen accepts
MAX_PRESETS = 32          # generous backstop so a runaway can't fill flash


def _load_presets():
    d = _read_json(PRESETS_FILE)
    if isinstance(d, list):
        # Keep only well-formed entries so one bad record can't break the list.
        return [p for p in d
                if isinstance(p, dict) and isinstance(p.get('name'), str)
                and isinstance(p.get('cc'), dict)]
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
    # A preset is a COMPLETE patch: EVERY editable param is applied, taking its
    # saved value if the snapshot has one and its PARAMS default if it does not.
    # So a load lands on the same sound no matter what was playing before it.
    #
    # That second half is the fix for a real bug. This used to iterate the SAVED
    # map only, which left any param the snapshot didn't mention at its current
    # value -- and a preset saved before a param existed doesn't mention it. Load a
    # patch with glide, then load one saved before portamento shipped, and the
    # glide came with it; only INIT (synthesized from every default, so it names
    # every CC) cleared it. Found by ear while scanning presets on hardware
    # 2026-07-28. It was never a decision -- it was the shape of the loop.
    #
    # Skipping UNKNOWN CCs, on the other hand, IS deliberate and stays: a snapshot
    # may name a param that has since been retired, and that must be ignored
    # rather than raise.
    #
    # Cost is unchanged in practice: _capture_preset() dumps all of param_values,
    # so a preset already carries every CC and a load already replayed the lot.
    # This only tops legacy presets up to that same full set.
    if not isinstance(cc_map, dict):
        return
    saved = {}
    for k, v in cc_map.items():
        try:
            cc = int(k)
            val = clamp(int(v), 0, 127)
        except Exception:
            continue
        if cc in param_values:       # retired/unknown CC -> ignore
            saved[cc] = val
    # PARAMS order (not dict order): MicroPython dicts don't preserve insertion
    # order, so this also makes the replay sequence deterministic.
    for p in PARAMS:
        handle_cc(p.cc, saved.get(p.cc, p.default))


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


def _sorted_presets():
    # Saved presets in display order: alphabetical, case-insensitive (so 'arp' and
    # 'Bass' sort naturally), stable within equal keys. ONE definition of the order
    # so the Load and Delete lists agree. Storage order in the JSON is left
    # untouched -- this sorts only what the menus show.
    return sorted(_presets, key=lambda p: p.get('name', '').lower())


def _load_list():
    # Presets offered by the Load menu: the virtual INIT first (a fixed home row,
    # never sorted into the names), then the saved ones alphabetically.
    return [_init_preset_entry()] + _sorted_presets()


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
    # ONE table-driven path for every control change, whether it came from a
    # hardware knob, the on-device editor, or a preset replay: look the CC up in
    # PARAMS, record the raw 0-127 value for the editor, map it to its engine
    # value, store that where the engine reads it, and re-send the affected
    # slice of the AMY graph. What each parameter DOES lives in its PARAMS row
    # (defined later in the file; this only runs after boot, so the table
    # exists), not here.

    # The mod wheel is the standard vibrato controller: treat it as the LFO->pitch
    # depth so a performer's wheel works out of the box and shares that one param
    # (editor + presets stay in sync). Done before the param_values record below.
    if cc == CC_MODWHEEL:
        cc = CC_LFO_PITCH

    p = PARAM_BY_CC.get(cc)
    if p is None:
        return

    # Remember the raw value so the editor opens on the current value whether it
    # was last set by a knob or the editor.
    param_values[cc] = clamp(int(val), 0, 127)

    target, key = p.store
    target[key] = p.to_val(val)
    if p.update:
        p.update()
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
    menu.note_external_cc(m[1], m[2])       # let a selected grid cell track it live
                                            # (AFTER handle_cc: that writes the value
                                            # into param_values, which is what the cell
                                            # renders from -- this only marks it dirty)


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
# Progressive framebuffer flush. A full 128x128 refresh blits 8KB over the
# 400kHz I2C bus (240ms MEASURED) and blocks the single MicroPython thread long
# enough to drop a note-off. These helpers zero and/or push the framebuffer in
# bounded row BANDS spread across successive loop() calls, so no single refresh
# exceeds ~19ms (a 12-row band) instead of 240ms in one go.
#
# Cost of a full repaint: 128 rows / 12 = 11 bands, one per loop() tick at ~69ms
# => ~760ms wall-clock before the screen is fully redrawn. That is the dominant
# term in menu-open latency, and it is a deliberate trade: audio safety over
# redraw speed. Raising FLUSH_BAND_ROWS shortens it but lengthens each blit, so
# re-measure the note-off margin before touching it.
# Used when entering a display mode (which must clear the previous screen) and by
# the menu's full repaint.
# ---------------------------------------------------------------------------
FLUSH_BAND_ROWS = 12         # pixel-rows pushed per loop() while flushing. MEASURED:
                             # 12 rows (768B) = 19ms, 2 rows = 4.5ms, full 8KB = 240ms.
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


# Group tag for a parameter's readable monitor name. VCF/VCA read nicer as
# Filt/Amp; other groups already appear in the label itself.
_MON_GROUP = {'VCF': 'Filt', 'VCA': 'Amp'}


def _mon_name(p):
    # The parameter's readable name for the flat CC monitor, from its PARAMS row.
    # Usually just the editor label; but the short section-relative ones (the ADSR
    # A/D/S/R, Shape, Level) would be ambiguous without the grid's section header,
    # so they get a group prefix -- label 'A' in group VCF -> 'Filt A'.
    if len(p.label) > 5:
        return p.label
    return '%s %s' % (_MON_GROUP.get(p.group, p.group), p.label)


class CCMonitorMode(DisplayMode):
    # Live CC monitor: the most-recently-touched CCs, newest at the bottom, each
    # expiring CC_EXPIRE_MS after its last touch. Each is a two-line group -- the
    # parameter's name, then "CC <n>" with its value (the friendly, formatted value
    # the editor shows when the parameter has one, else the raw 0-127) -- drawn from
    # the one PARAMS table, so effects and everything else are labelled and nothing
    # can drift. An unmapped CC (e.g. the raw mod wheel, CC 1) shows its number.
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

    def _active_entries(self, now):
        # Expire stale entries (preserving order), drop the oldest from the top
        # if we exceed the group budget, and return (cc, value) pairs oldest-first
        # so the newest sits at the bottom and survivors shift up as items above
        # them fade.
        i = 0
        while i < len(self.entries):
            if time.ticks_diff(now, self.entries[i][2]) > CC_EXPIRE_MS:
                self.entries.pop(i)
            else:
                i += 1
        while len(self.entries) > DISPLAY_MAX_ENTRIES:
            self.entries.pop(0)
        return [(e[0], e[1]) for e in self.entries]

    def _frame(self, entries):
        # The target TEXT rows for the active entries -- two per group, positioned
        # by the geometry (group top at DISPLAY_ENTRY_Y[i], its two lines at
        # +0 / +DISPLAY_LINE_H). Each row is a self-describing tuple so a frame can
        # be diffed row-by-row against the last one:
        #   ('n', y, name)             -- the parameter name line
        #   ('v', y, cc_str, value)    -- the "CC <n>" (left) + value (right) line
        # Content comes from the PARAMS table via PARAM_BY_CC; an unmapped CC falls
        # back to its number with the raw value.
        rows = []
        for i, (cc, v) in enumerate(entries):
            ey = DISPLAY_ENTRY_Y[i]
            p = PARAM_BY_CC.get(cc)
            if p is None:
                rows.append(('n', ey, 'CC %d' % cc))
                rows.append(('v', ey + DISPLAY_LINE_H, '', str(v)))
            else:
                rows.append(('n', ey, _mon_name(p)))
                rows.append(('v', ey + DISPLAY_LINE_H, 'CC %d' % cc, _grid_disp(p, v)))
        return rows

    def _draw_row(self, d, row):
        # Clear the row's 12px band, then draw it: a name line left-aligned, or a
        # value line with "CC <n>" left and the value right-aligned to the edge.
        y = row[1]
        d.fill_rect(0, y, DISPLAY_WIDTH, DISPLAY_LINE_H, 0)
        if row[0] == 'n':
            d.text(row[2], 0, y, DISPLAY_TEXT_COLOR)
        else:
            left, right = row[2], row[3]
            d.text(left, 0, y, DISPLAY_TEXT_COLOR)
            d.text(right, DISPLAY_WIDTH - len(right) * DISPLAY_CHAR_W, y,
                   DISPLAY_TEXT_COLOR)

    def render(self, now):
        # Repaint only the TEXT rows that differ from the last frame, capped at
        # DISPLAY_MAX_ROWS_PER_REFRESH per call, so the I2C bus (and thus the
        # audio) is held as briefly as possible. The common case -- re-touching a
        # CC already listed -- changes only that group's value line, so one row
        # pushes; a fresh CC or an expiry shift redraws more, bounded and drained
        # over the next few refreshes.
        d = amyboard.display
        frame = self._frame(self._active_entries(now))

        # Idle: no active CCs -> clear just the rows we were using, once.
        if not frame:
            if self.blanked:
                return
            if self.prev:
                top = self.prev[0][1]
                bot = self.prev[-1][1] + DISPLAY_LINE_H
                d.fill_rect(0, top, DISPLAY_WIDTH, bot - top, 0)
                if not _push_rows(top, bot - 1):
                    amyboard.display_refresh()
            self.blanked = True
            self.prev = []
            return

        # Nothing visible changed since last frame.
        if frame == self.prev:
            return

        rows = max(len(frame), len(self.prev))
        # Track which rows have been committed so deferred ones retry next call.
        new_prev = list(self.prev)
        if len(new_prev) < len(frame):
            new_prev += [None] * (len(frame) - len(new_prev))
        pushed = 0
        for i in range(rows):
            if pushed >= DISPLAY_MAX_ROWS_PER_REFRESH:
                break                          # defer the rest to the next refresh
            new = frame[i] if i < len(frame) else None
            old = self.prev[i] if i < len(self.prev) else None
            if new == old:
                continue
            if new is not None:
                self._draw_row(d, new)
                y = new[1]
            else:
                y = old[1]                      # a removed row: clear its old band
                d.fill_rect(0, y, DISPLAY_WIDTH, DISPLAY_LINE_H, 0)
            # Push just this one row, so non-contiguous changes never drag
            # unchanged rows along (the bounding-span trap that let a busy
            # screen blit the whole frame and stall audio/MIDI).
            if not _push_rows(y, y + DISPLAY_LINE_H - 1):
                amyboard.display_refresh()
            new_prev[i] = new
            pushed += 1
        # Drop trailing rows that were removed and have now been cleared.
        while len(new_prev) > len(frame) and new_prev[-1] is None:
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


# Available display modes. The on-device menu (see SketchMenu) indexes this list
# to let the user pick which one drives the OLED.
#
# There was a third, 'Oscilloscope', removed 2026-07-31. It was only ever a
# placeholder that drew "not available yet" and idled -- a real scope needs a tap
# into AMY's output samples, which is not wired up. A menu entry that leads
# nowhere costs a user more than it gives. Re-add it as a mode class here when
# there is something behind it; nothing else has to change.
#
# A board that saved 'Oscilloscope' as its display mode is safe:
# _restore_display_mode() matches by NAME and keeps the default when there is no
# match, so it falls back to the CC monitor and the next pick overwrites it.
CC_MONITOR_MODE = CCMonitorMode()
SCREENSAVER_MODE = ScreensaverMode()
DISPLAY_MODES = [CC_MONITOR_MODE, SCREENSAVER_MODE]

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
# Editable parameters (Param Control menu). Each PARAMS row IS the parameter --
# CC, engine mapping, storage slot, AMY sender, and display -- so the on-device
# editor reuses the exact same handle_cc() path a hardware knob does; all
# value->sound mapping is shared, and exposing another parameter is a one-line
# addition to PARAMS. The fmt_* functions below turn a raw 0-127 value into a
# friendly label for parameters whose range is really discrete regions (pitch
# intervals, wave shapes, filter types); each reuses its parameter's cc_to_*
# map so label and sound cannot disagree. param_values tracks the last raw
# 0-127 value per CC (updated by BOTH incoming MIDI CCs and the editor) so the
# editor opens on the current value.
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
    # Names the wave from the SAME WAVE_BUCKETS row cc_to_wave() picks from, so
    # the label always matches the wave the synth actually selects.
    return _wave_bucket(v)[1]


def fmt_filter_type(v):
    # Same cc_bucket() index as cc_to_filter_type(), FILTER_TYPE_NAMES order.
    return FILTER_TYPE_NAMES[cc_bucket(v, len(FILTER_TYPES))]


def fmt_env_shape(v):
    # Same cc_bucket() index as cc_to_eg_type(), ENV_SHAPE_NAMES order.
    return ENV_SHAPE_NAMES[cc_bucket(v, len(ENV_SHAPES))]


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


def fmt_lfo_freq(v):
    # LFO rate in real Hz (0.2 .. 20), not the raw CC -- the CC is exponential, so
    # the number alone told you nothing about the speed. Two decimals below 1 Hz
    # where the steps are fine, one above; same convention as fmt_drift_rate.
    hz = cc_to_lfo_freq(v)
    return '%.2f Hz' % hz if hz < 1.0 else '%.1f Hz' % hz


def fmt_drift_rate(v):
    # Wander speed in Hz (random targets eased through per second).
    hz = cc_to_drift_rate(v)
    return '%.2f Hz' % hz if hz < 1.0 else '%.1f Hz' % hz


def fmt_porta_ms(v):
    # Glide time off the PORTA_MS_STEPS ladder. 0 reads 'Off' rather than '0 ms'
    # because it IS the off switch -- AMY has no separate portamento enable, so a
    # zero time is the only way to disable glide. The top rung reads '1.0 s' so the
    # scale's end is legible at a glance instead of as a four-digit millisecond
    # count. This is also what `stepped` scans to find the detents, so every rung
    # MUST render a distinct string -- two rungs sharing a label would silently
    # fuse into one detent.
    ms = cc_to_porta_ms(v)
    if ms == 0:
        return 'Off'
    return '%.1f s' % (ms / 1000.0) if ms >= 1000 else '%d ms' % ms


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
    __slots__ = ('label', 'cc', 'default', 'grid', 'to_val', 'store', 'update',
                 'fmt', 'bipolar', 'steps', 'group', 'section', 'newrow',
                 'hdr', 'halfcol')

    def __init__(self, label, cc, default, grid, to_val, store, update,
                 fmt=None, bipolar=False, stepped=False, group='', section='',
                 newrow=False, hdr='', halfcol=False):
        self.label = label
        self.cc = cc
        self.default = default   # raw 0-127 value used until a CC/editor sets one
        self.grid = grid         # ultra-short knob-grid cell label; see the HARD
                                 # LIMIT note above PARAMS before changing one
        self.to_val = to_val     # raw 0-127 -> engine value (a cc_to_* map)
        # `store` says where the mapped value lives so the AMY senders can read
        # it: a bare string names a module global ('flt_cutoff'); a (dict, key)
        # tuple names a slot in one of the state dicts (vcf_env / vca_env / fx).
        # Normalized to (dict, key) here so handle_cc does one uniform store.
        self.store = (globals(), store) if isinstance(store, str) else store
        self.update = update     # zero-arg sender pushing the change into the
                                 # AMY graph; None = picked up later (Drift Rate
                                 # reads on the next wander segment)
        self.fmt = fmt           # optional value(0-127) -> friendly label
        self.bipolar = bipolar   # editor readout shows a signed scale (0 = center)
        self.group = group       # Param Control category (see PARAM_GROUPS)
        self.section = section   # grid: draws a header row above this param's run and
                                 # lets its cells use shorter labels (the header now
                                 # carries the context the label used to). '' = no
                                 # header; a '' run following a sectioned one gets a
                                 # padding gap instead. See _grid_layout.
        self.newrow = newrow     # grid: force a row break BEFORE this cell even though
                                 # its row is not full -- for grouping inside a section
                                 # whose members don't split 4-at-a-time (LFO's five
                                 # destinations read as 3 + 2, not the 4 + 1 that plain
                                 # wrapping gives). Ignored if the cell already starts
                                 # a row.
        self.hdr = hdr           # grid: SPLIT this run's header row here. The run's
                                 # own `section` name then spans only the columns to
                                 # the LEFT of this cell, and `hdr` labels the columns
                                 # from this cell rightward -- two names on one header
                                 # row ("--DRIFT--  --PORTA--") instead of one centred
                                 # across the panel. The params stay ONE run, so the
                                 # vertical walk, page breaks and cross-group row
                                 # alignment are all untouched; only the header's
                                 # drawing changes. See _grid_layout / _draw_grid_section.
        self.halfcol = halfcol   # grid: shift this cell right by HALF a column. For
                                 # centring a LONE cell under a split header -- one
                                 # param beneath a 2-column name sits half a column
                                 # over so it is centred under the name rather than
                                 # shoved to the name's left edge. A second param in
                                 # that half needs no shift: the two then fill their
                                 # columns normally. Resolved in _grid_layout, which is
                                 # where GRID_CELL_W is in scope (this table is defined
                                 # before the grid constants).
        # "Bucketed" params (a few discrete display values): one detent jumps to
        # the next distinct bucket instead of crawling through identical CCs.
        self.steps = _bucket_centers(fmt) if (stepped and fmt) else None


# THE parameter table. One row per editable parameter is the WHOLE definition:
# everything the synth knows about a parameter -- its MIDI CC, engine mapping,
# where the mapped value lives, which AMY sender pushes it, and how the editor
# displays it -- lives in that one row. Adding a parameter is adding a row (plus
# its cc_to_*/update_* functions if no existing ones fit) and documenting the CC
# in docs/CC_MAPPING.md. handle_cc, the editor, the knob grid, presets, and the
# INIT patch all drive themselves from this table.
#
# Columns (positional): label, cc, default, grid, to_val, store, update; then
# keyword flags fmt / bipolar / stepped / group / section / newrow.
#   label    editor list name. Kept short enough that "NN. label" clears 128px
#            in the per-category lists (numbering restarts at 1 per category).
#   default  raw 0-127 value reproducing the patch's initial default (the
#            double-click reset target); the fine ADSR-time defaults are the
#            CCs closest to the initial ms.
#   grid     ultra-short knob-grid cell label. HARD LIMIT 4 CHARS: the 8px font
#            x 4 = 32px is exactly one grid cell (see "WHY 4 COLUMNS IS TIGHT"
#            at GRID_COLS); a 5-char label doesn't error, it silently spills
#            into the neighbouring cell. PREFER 3: 4-char labels ink to within
#            2px of the next cell and fuse into a wall ("EQLOEQMDEQHICHLV" --
#            measured with the board's own font, not guessed); 3 chars leave
#            ~4px of air. Section headers make 3 affordable by carrying the
#            group context (which oscillator, which effect), so labels repeat
#            freely across sections (PIT/ATK/LEV/SHP...) BY DESIGN -- the
#            header above the cell disambiguates. Only LFO is still on 4.
#   to_val   raw 0-127 -> engine value (shared cc_to_* maps).
#   store    where that value lives: a module-global name, or (dict, key).
#   update   zero-arg AMY sender that makes the stored value heard.
# `group` slots the param under its Param Control category (see PARAM_GROUPS);
# `section`/`newrow` shape the knob grid (see _Param). A few labels are
# shortened from the requested names -- Filt Type, Kbd Track, the ADSR
# Atk/Dec/Sus/Rel forms, the space-free Lfo>X routing, and the FX Cho/Rev
# prefixes.
PARAMS = [
    # Osc: two sectioned rows of 4 (an OSC A row, an OSC B row), then Drift. The
    # section headers are what let these cells drop to 3-char labels (PIT/WAV/DTY/LVL,
    # repeated per section) -- the header carries the A/B context that used to be
    # crammed into APIT/AWAV/... Keep each section's params contiguous and in this
    # order: they ARE the row.
    _Param('Osc A Pitch', CC_OSC_A_PITCH,  64, 'PIT', cc_to_detune_cents, 'a_cents', update_osc_a_freq, fmt=fmt_osc_pitch, bipolar=True, stepped=True, group='Osc', section='Osc A'),
    _Param('Osc A Shape', CC_OSC_A_WAVE,   52, 'WAV', cc_to_wave, 'a_wave', update_osc_a_wave, fmt=fmt_wave, stepped=True, group='Osc', section='Osc A'),
    _Param('Osc A Duty',  CC_OSC_A_DUTY,   64, 'DTY', cc_to_duty, 'a_duty', update_osc_a_duty, group='Osc', section='Osc A'),
    _Param('Osc A Level', CC_OSC_A_LEVEL, 127, 'LVL', cc_unit, 'a_level', update_osc_a_level, group='Osc', section='Osc A'),
    _Param('Osc B Pitch', CC_OSC_B_PITCH,  64, 'PIT', cc_to_detune_cents, 'b_cents', update_osc_b_freq, fmt=fmt_osc_pitch, bipolar=True, stepped=True, group='Osc', section='Osc B'),
    _Param('Osc B Shape', CC_OSC_B_WAVE,    0, 'WAV', cc_to_wave, 'b_wave', update_osc_b_wave, fmt=fmt_wave, stepped=True, group='Osc', section='Osc B'),
    _Param('Osc B Duty',  CC_OSC_B_DUTY,   64, 'DTY', cc_to_duty, 'b_duty', update_osc_b_duty, group='Osc', section='Osc B'),
    _Param('Osc B Level', CC_OSC_B_LEVEL,   0, 'LVL', cc_unit, 'b_level', update_osc_b_level, group='Osc', section='Osc B'),
    # Analog drift: a control-rate smooth-random pitch wander (tape wow/warble). It
    # lives on the Osc page because it IS a pitch control -- it rides on top of each
    # osc's tuning via osc_freq(). It sat on the LFO page next to the LFO's own Rate
    # and read as a second LFO, which it is not: the audio LFO is an AMY oscillator,
    # drift is Python (see service_drift). Amt default 0 = off (patches unchanged);
    # Rate default raw 48 ~ 0.3 Hz, a gentle wander for when Amt is dialed up.
    _Param('Drift Amt',   CC_DRIFT_DEPTH,   0, 'AMT', cc_to_drift_depth, 'drift_depth_cents', update_drift_depth, fmt=fmt_drift_depth, group='Osc', section='Drift'),
    _Param('Drift Rate',  CC_DRIFT_RATE,   48, 'HZ',  cc_to_drift_rate, 'drift_rate_hz', None, fmt=fmt_drift_rate, group='Osc', section='Drift'),
    # Shares the Drift RUN (so it lands on the same row, costing no vertical space
    # on a page that has none) but carries its own header name via `hdr`: the row
    # reads "--DRIFT--  --PORTA--". halfcol centres this lone cell under PORTA; a
    # second Porta param would drop into the last column and both would lose the
    # offset. See _Param.hdr / _grid_layout.
    _Param('Porta Time',  CC_PORTA_TIME,    0, 'TIME', cc_to_porta_ms, 'porta_ms', update_porta, fmt=fmt_porta_ms, stepped=True, group='Osc', section='Drift', hdr='Porta', halfcol=True),
    # VCF: filter controls. Order is chosen for the ROW-MAJOR 4-wide grid (see
    # GRID_COLS) -- these 11 land as three read-across rows: the filter proper
    # (Cutoff, Res, env Amount, Type), then the whole filter envelope (ADSR + curve
    # Shape; Shape default raw 48 = the 'Normal' bucket, stepped one detent per curve
    # type), then the two modulation depths. Reordering these MOVES CELLS on screen,
    # so keep the rows-of-4 grouping intact if you add a param.
    _Param('Cutoff',      CC_FLT_CUTOFF,  127, 'CUT', cc_to_cutoff, 'flt_cutoff', update_filter_freq, group='VCF', section='Filter'),
    _Param('Resonance',   CC_FLT_RES,       0, 'RES', cc_to_res, 'flt_res', update_resonance, group='VCF', section='Filter'),
    _Param('Filter Env',  CC_FLT_ENV_AMT,  64, 'ENV', cc_to_flt_env_amt, 'flt_env_amt', update_filter_freq, fmt=fmt_flt_env, bipolar=True, group='VCF', section='Filter'),
    _Param('Filt Type',   CC_FLT_TYPE,     48, 'TYP', cc_to_filter_type, 'flt_type', update_filter_type, fmt=fmt_filter_type, stepped=True, group='VCF', section='Filter'),
    _Param('A',          CC_VCF_ATK,       0, 'ATK', cc_to_time_ms, (vcf_env, 'a'), update_vcf, group='VCF', section='ADSR'),
    _Param('D',          CC_VCF_DEC,      34, 'DEC', cc_to_time_ms, (vcf_env, 'd'), update_vcf, group='VCF', section='ADSR'),
    _Param('S',          CC_VCF_SUS,      25, 'SUS', cc_unit, (vcf_env, 's'), update_vcf, group='VCF', section='ADSR'),
    _Param('R',          CC_VCF_REL,      31, 'REL', cc_to_time_ms, (vcf_env, 'r'), update_vcf, group='VCF', section='ADSR'),
    _Param('Shape',      CC_FLT_ENV_SHAPE, 48, 'SHP', cc_to_eg_type, 'flt_eg_type', update_vcf_shape, fmt=fmt_env_shape, stepped=True, group='VCF', section='Etc'),
    _Param('Kbd Track',   CC_KEY_SCALE,     0, 'KBD', cc_unit, 'key_scale', update_filter_freq, group='VCF', section='Etc'),
    _Param('Vel>Filter',  CC_VEL_FILT,      0, 'VEL', cc_to_vel_filt, 'vel_filt_depth', update_filter_freq, fmt=fmt_vel_filt, group='VCF', section='Etc'),
    # LFO: the oscillator itself (rate + shape), then where it goes. One LFO drives
    # every destination -- AMY allows one mod_source per oscillator, so the depths
    # below are all fed from this single LFO at independent strengths; they are not
    # separate LFOs and cannot have their own rates.
    _Param('Lfo Freq',    CC_LFO_FREQ,      0, 'HZ',  cc_to_lfo_freq, 'lfo_freq', update_lfo, fmt=fmt_lfo_freq, group='LFO', section='Wave'),
    _Param('Lfo Shape',   CC_LFO_WAVE,      0, 'SHP', cc_to_wave, 'lfo_wave', update_lfo, fmt=fmt_wave, stepped=True, group='LFO', section='Wave'),
    # Destinations. Five, deliberately split 3 + 2 (newrow on Amp A) rather than left
    # to wrap 4 + 1: the first row is the shared depths (both oscs move together), the
    # second is the per-oscillator tremolos.
    _Param('Lfo>Pitch',   CC_LFO_PITCH,     0, 'VIB', cc_to_lfo_pitch, 'lfo_pitch_depth', update_lfo_pitch, group='LFO', section='Dest'),
    _Param('Lfo>Pwm',     CC_LFO_PWM,       0, 'PWM', cc_to_lfo_pwm, 'lfo_pwm_depth', update_lfo_pwm, group='LFO', section='Dest'),
    _Param('Lfo>Filter',  CC_LFO_FILT,      0, 'FLT', cc_to_lfo_filt, 'lfo_filt_depth', update_filter_freq, group='LFO', section='Dest'),
    _Param('Lfo>Amp A',   CC_LFO_AMP_A,     0, 'LVA', cc_to_lfo_amp, 'lfo_amp_a_depth', update_lfo_amp_a, group='LFO', section='Dest', newrow=True),
    _Param('Lfo>Amp B',   CC_LFO_AMP_B,     0, 'LVB', cc_to_lfo_amp, 'lfo_amp_b_depth', update_lfo_amp_b, group='LFO', section='Dest'),
    # VCA: amp controls. The amp envelope (ADSR + curve Shape) as one section, then
    # velocity->amp sensitivity and the master output Level (renamed from the old
    # 'Output'; per-patch master volume, default +12 dB).
    _Param('A',          CC_VCA_ATK,       0, 'ATK', cc_to_time_ms, (vca_env, 'a'), update_vca, group='VCA', section='ADSR'),
    _Param('D',          CC_VCA_DEC,      25, 'DEC', cc_to_time_ms, (vca_env, 'd'), update_vca, group='VCA', section='ADSR'),
    _Param('S',          CC_VCA_SUS,     127, 'SUS', cc_unit, (vca_env, 's'), update_vca, group='VCA', section='ADSR'),
    _Param('R',          CC_VCA_REL,      34, 'REL', cc_to_time_ms, (vca_env, 'r'), update_vca, group='VCA', section='ADSR'),
    _Param('Shape',      CC_AMP_ENV_SHAPE, 48, 'SHP', cc_to_eg_type, 'amp_eg_type', update_vca_shape, fmt=fmt_env_shape, stepped=True, group='VCA', section='Etc'),
    _Param('Vel>Amp',    CC_VEL_SENS,   38, 'VEL', cc_unit, 'vel_sens', update_vel_sens, fmt=fmt_pct, group='VCA', section='Etc'),
    _Param('Level',      CC_MASTER_VOL, 84, 'LVL', cc_to_master_vol, 'master_vol', update_master_vol, fmt=fmt_master_vol, stepped=True, group='VCA', section='Etc'),
    # Master FX (global effects). Defaults leave every effect OFF: EQ flat (64),
    # chorus/echo/reverb level 0. Chorus depth/rate and reverb decay/damp/xover
    # carry musical defaults so raising just their Level lands on a usable sound.
    # `stepped` is set on the params whose readout is a COARSE discrete scale (dB,
    # log Hz) where several raw CCs share one label -- so one detent advances to
    # the next distinct value instead of clicking through dead steps (like the
    # wave/filter-type buckets). The near-continuous knobs (levels/depth/times, one
    # display value per detent already) stay smooth so fast sweeps keep their accel.
    _Param('EQ Low',     CC_EQ_LOW,     64, 'LO',  cc_to_eq_db, (fx, 'eq_l'), update_eq, fmt=fmt_eq_db, bipolar=True, stepped=True, group='FX', section='EQ'),
    _Param('EQ Mid',     CC_EQ_MID,     64, 'MID', cc_to_eq_db, (fx, 'eq_m'), update_eq, fmt=fmt_eq_db, bipolar=True, stepped=True, group='FX', section='EQ'),
    _Param('EQ High',    CC_EQ_HIGH,    64, 'HI',  cc_to_eq_db, (fx, 'eq_h'), update_eq, fmt=fmt_eq_db, bipolar=True, stepped=True, group='FX', section='EQ'),
    _Param('Cho Level',  CC_CHO_LEVEL,   0, 'LEV', cc_unit, (fx, 'cho_level'), update_chorus, fmt=fmt_pct, group='FX', section='Chorus'),
    _Param('Cho Depth',  CC_CHO_DEPTH,  64, 'DEP', cc_unit, (fx, 'cho_depth'), update_chorus, fmt=fmt_pct, group='FX', section='Chorus'),
    _Param('Cho Rate',   CC_CHO_RATE,   44, 'HZ',  cc_to_chorus_rate, (fx, 'cho_rate'), update_chorus, fmt=fmt_chorus_rate, stepped=True, group='FX', section='Chorus'),
    _Param('Echo Level', CC_ECHO_LEVEL,  0, 'LEV', cc_unit, (fx, 'echo_level'), update_echo, fmt=fmt_pct, group='FX', section='Echo'),
    _Param('Echo Time',  CC_ECHO_TIME,  43, 'TIM', cc_to_echo_time, (fx, 'echo_time'), update_echo, fmt=fmt_echo_time, group='FX', section='Echo'),
    _Param('Echo Fbk',   CC_ECHO_FBK,   40, 'FBK', cc_to_echo_fbk, (fx, 'echo_fbk'), update_echo, fmt=fmt_echo_fbk, group='FX', section='Echo'),
    _Param('Echo Tone',  CC_ECHO_TONE,   0, 'TON', cc_to_echo_tone, (fx, 'echo_tone'), update_echo, fmt=fmt_echo_tone, group='FX', section='Echo'),
    _Param('Rev Level',  CC_REV_LEVEL,   0, 'LEV', cc_unit, (fx, 'rev_level'), update_reverb, fmt=fmt_pct, group='FX', section='Reverb'),
    _Param('Rev Decay',  CC_REV_DECAY, 108, 'DEC', cc_unit, (fx, 'rev_decay'), update_reverb, fmt=fmt_pct, group='FX', section='Reverb'),
    _Param('Rev Damp',   CC_REV_DAMP,   64, 'DMP', cc_unit, (fx, 'rev_damp'), update_reverb, fmt=fmt_pct, group='FX', section='Reverb'),
    _Param('Rev Xover',  CC_REV_XOVER,  99, 'XVR', cc_to_rev_xover, (fx, 'rev_xover'), update_reverb, fmt=fmt_hz_khz, stepped=True, group='FX', section='Reverb'),
]

# CC -> row lookup for handle_cc, derived from the table so it can't drift.
PARAM_BY_CC = {p.cc: p for p in PARAMS}

# Param Control categories, in display order. Each opens that group's knob grid;
# a param's `group` (above) decides where it lands, so the two never drift, and its
# `section` decides which labelled row it joins there (see _grid_layout).
PARAM_GROUPS = ('Osc', 'VCF', 'LFO', 'VCA', 'FX')

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
# lists (e.g. the FX category's 14 params) advance a page at a time, so this also
# sets the page size shown by the "pg/total" marker. 8 fills the screen well (last
# row ends at y=114). Short menus (<=8 items) are a single page with no marker.
MENU_VISIBLE = 8
MENU_PAGE_Y = 116        # bottom row (128 - MENU_LINE_H) for the page indicator; the
                         # last item row ends at y=114, leaving this row free. The
                         # marks themselves are the shared grid style -- a bright dash
                         # for the current page, dim dots for the rest (_draw_page_dots).
MENU_LABEL_MAX = 18
MENU_IDLE_MS = 15000     # auto-close the menu to the display mode after this idle
TOAST_MS = 1200          # how long a confirmation toast (e.g. "PRESET SAVED!") shows

CHAR_W = 8               # framebuf font cell width (for centering text)
CHAR_H = 8               # framebuf font cell height
EDIT_TITLE_Y = 2         # param name (1x, static after open)
NAME_ROW_Y   = 44        # name-entry: the word + inline active slot, one 1x row
# Cursor band = the track line +/- the tick, pushed as a unit each turn.
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

# Menu list colours, on the 0-15 LEVEL scale (see the COLOUR SCALE note above).
# The unselected row was 110, i.e. level 14 -- one step off the selected row's 15,
# which is why a list read as a flat block with only the '>' marking the cursor.
MENU_C_SEL   = 15        # selected row (and its '>' marker)
MENU_C_UNSEL = 6         # unselected rows

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


def _draw_menu_row(d, y, kind, payload):
    # One diffable list row: 't' = title line, 'q' = page indicator, else an item.
    d.fill_rect(0, y, DISPLAY_WIDTH, MENU_LINE_H, 0)
    if kind == 't':
        # Title row: left-aligned header text, plus an optional right-aligned
        # marker.
        left, right = payload
        d.text(left, 0, y, MENU_C_SEL)
        if right:
            d.text(right, DISPLAY_WIDTH - len(right) * CHAR_W, y, MENU_C_SEL)
    elif kind == 'q':
        # Page indicator -- the SAME dash/dots marks the knob grid uses
        # (_draw_page_dots), vertically centred in this row so the two surfaces
        # read consistently.
        total, cur = payload
        _draw_page_dots(d, y + (MENU_LINE_H - 2) // 2, cur, total)
    else:
        sel, label = payload
        if sel:
            d.text('>', 0, y, MENU_C_SEL)
            d.text(label[:MENU_LABEL_MAX], 12, y, MENU_C_SEL)
        else:
            d.text(label[:MENU_LABEL_MAX], 12, y, MENU_C_UNSEL)


# ---------------------------------------------------------------------------
# Menu levels. The stack (owned by SketchMenu) holds one of three level types;
# each type owns its OWN input handling and rendering, so everything about a
# screen -- its state, what a turn/click/hold does there, and how it draws --
# lives in one class. SketchMenu keeps only what is genuinely shared: the
# stack + lifecycle (open/close/suspend/resume), the toast, the panel/flush
# bookkeeping, and the menu tree + preset workflows (the CONTENT of the
# levels). Adding a screen type = writing a level class with handle()/render()
# -- no dispatcher to extend.
#
# handle(menu, delta, click, back) and render(menu) receive the owning
# SketchMenu for that shared state (menu.dirty, menu._needs_clear, the flush
# extents, the deferred-click clock).
# ---------------------------------------------------------------------------
class _MenuLevel:
    __slots__ = ('title', 'items', 'idx', 'start')

    def __init__(self, title, items):
        self.title = title
        # items: list of (label, callback_or_None). None = non-selectable line.
        self.items = items if items else [('(empty)', None)]
        self.idx = 0
        self.start = 0    # index of the top visible item = current page origin
                          # (page-aligned; recomputed from idx in render)

    def handle(self, menu, delta, click, back):
        if back:                 # hold: pop one level (may close the menu)
            menu._pop()
            return
        if delta:
            # List scroll is 1:1 with detents (no acceleration -- that's only for
            # the value editor) and clamps at the ends instead of wrapping.
            self.idx = clamp(self.idx + delta, 0, len(self.items) - 1)
            menu.dirty = True
        if click:
            _, cb = self.items[self.idx]
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
                start = (self.idx // MENU_VISIBLE) * MENU_VISIBLE
                total_pages = (n + MENU_VISIBLE - 1) // MENU_VISIBLE
            cur_page = start // MENU_VISIBLE     # 0-based index of the shown page
            self.start = start
            # Current frame = title row(s) + visible item rows, as diffable tuples.
            # A title may hold newlines (used by confirm prompts) -> up to three 1x
            # header lines; items begin below them (a trailing '' line leaves a blank
            # gap). A plain single-line title behaves exactly as before.
            frame = []
            ty = 0
            for tline in self.title.split('\n')[:3]:
                frame.append((ty, 't', (tline, '')))
                ty += 9
            y = max(MENU_TOP_Y, ty)
            i = start
            while i < n and i < start + MENU_VISIBLE:
                frame.append((y, 'i', (i == self.idx, self.items[i][0])))
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
            if menu._needs_clear or menu._prev is None or len(menu._prev) != len(frame):
                d.fill(0)
                extent = 0
                for (ry, kind, payload) in frame:
                    _draw_menu_row(d, ry, kind, payload)
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
                flush_to = min(127, max(extent, menu._panel_dirty_to) - 1)
                _begin_flush(0, flush_to)
                menu._panel_dirty_to = extent
                menu._needs_clear = False
            else:
                changed = [j for j in range(len(frame))
                           if frame[j] != menu._prev[j]]
                if len(changed) <= 2:
                    # Cursor move within a static window: only the two selection
                    # rows changed -- push them now (responsive, ~2 short bands).
                    for j in changed:
                        ry, kind, payload = frame[j]
                        _draw_menu_row(d, ry, kind, payload)
                        if not _push_rows(ry, ry + MENU_LINE_H - 1):
                            amyboard.display_refresh()
                else:
                    # Window scrolled (only at an edge now, thanks to edge-scroll):
                    # every visible row shifted, so ~9 rows changed -- 9 * MENU_LINE_H
                    # = ~108 pixel-rows. Interpolating the measured blits (12 rows =
                    # 19ms, the full 128 = 240ms) that is ~170-200ms of I2C in one
                    # loop -- an earlier "~150ms" here understated it -- which starves
                    # AMY's audio render and makes the LFO/vibrato stutter. Draw
                    # them, then blit PROGRESSIVELY over just the changed span (one
                    # band per loop) so no single loop holds the bus for long.
                    ys = []
                    for j in changed:
                        ry, kind, payload = frame[j]
                        _draw_menu_row(d, ry, kind, payload)
                        ys.append(ry)
                    _begin_flush(min(ys), max(ys) + MENU_LINE_H - 1)
            menu._prev = frame
        except Exception as e:
            _render_fault('_MenuLevel.render', e)


class _ScanLevel(_MenuLevel):
    # Scan Presets: the Load list, but the CURSOR IS THE AUDITION. Every turn
    # applies the preset it lands on immediately -- no click -- and the level stays
    # open, so one continuous spin walks the whole set by ear. It reuses
    # _MenuLevel's rendering unchanged (same list, cursor and page marker), and
    # overrides only the input semantics:
    #
    #   turn  -- move AND load. WRAPS at both ends (last -> first, first -> last)
    #            instead of clamping, so a continued spin never dead-ends -- the
    #            one place in this menu where scrolling wraps, because here the
    #            ends aren't a boundary you're navigating to, they're just the
    #            seam in a loop you're listening through.
    #   click -- open Param Control on the preset you've landed on: find it by ear,
    #            then edit it. The scan level is REPLACED, not stacked under, so
    #            Param Control sits directly on the root and a hold out of it behaves
    #            exactly as it does when you enter Param Control the normal way --
    #            back to the ARCTOR root. (Keeping the scan underneath meant a hold dropped
    #            you back into scanning, which reads as a trapdoor.) What the grid
    #            shows is LIVE state (param_values), not the saved snapshot -- so any
    #            MIDI CC that arrived while this preset was up is already reflected
    #            there, and a Save->Overwrite stores the patch as actually heard.
    #   hold  -- back to the root menu (the preset stays loaded either way).
    #
    # Nothing is applied on OPEN: the cursor starts on the current preset (if it
    # is still in the list) and the patch you were already playing is untouched
    # until the first turn.
    #
    # Cost per step is one _apply_preset() -- the same live replay a Load does --
    # and no file I/O: the "current preset" pointer moves in RAM on every step but
    # is written to settings ONCE, by persist(), when the mode ends. A settings
    # write per detent would put flash I/O in the audio/MIDI path (see
    # docs/ARCHITECTURE.md on keeping loop() short). A fast spin is safe for the
    # same reason list scrolling is: handle() gets the tick's SUMMED delta, so we
    # apply only the preset actually landed on, never the ones skimmed past.
    __slots__ = ('entries', 'touched')

    def __init__(self, entries):
        _MenuLevel.__init__(self, 'SCAN PRESETS',
                            [(e.get('name', '?')[:MENU_LABEL_MAX], None)
                             for e in entries])
        # Snapshot of _load_list() taken at open: presets can't be added or
        # deleted while we're on this level, so the labels and `entries` stay
        # index-aligned for the life of the scan.
        self.entries = entries
        self.touched = False     # a preset was actually loaded (persist() no-ops
                                 # otherwise, so opening and backing straight out
                                 # never writes settings)
        for i, e in enumerate(entries):
            if e.get('name', '') == _current_preset_name:
                self.idx = i     # start where the patch already is
                break

    def _load(self, i):
        # Apply live, exactly as Load does, and move the session's "current"
        # pointer in RAM only -- so a Save->Overwrite during or after the scan
        # targets what you're hearing, without touching flash mid-scan.
        global _current_preset_name
        entry = self.entries[i]
        try:
            _apply_preset(entry.get('cc'))
        except Exception:
            pass
        name = entry.get('name', '')
        if name:
            _current_preset_name = name
            self.touched = True

    def persist(self):
        # End of the scan: write the landing preset's name once, so a reset
        # resumes it (the same thing Load persists). Idempotent -- every exit path
        # can call it.
        if not self.touched:
            return
        self.touched = False
        _set_setting('current_preset', _current_preset_name)

    def handle(self, menu, delta, click, back):
        if back:                 # hold: back to the root menu
            self.persist()
            menu._pop()
            return
        if delta:
            self.idx = (self.idx + delta) % len(self.entries)   # wrap, don't clamp
            self._load(self.idx)
            menu.dirty = True
        if click:                # found it by ear -> now edit it
            self.persist()       # the scan ends here (the level is about to go)
            menu._pop()          # drop this level, so Param Control opens ON the
            menu._open_params()  # root and a hold out of it lands there, not here


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
    # _GridLevel it owns a full/dirty pair driving its own render path.
    __slots__ = ('name', 'sel', 'dirty', 'full')

    def __init__(self):
        self.name = ''
        self.sel = 0
        self.dirty = True
        self.full = True

    def handle(self, menu, delta, click, back):
        # Turn scrolls the ring candidate, click commits it (append char /
        # backspace / confirm), hold cancels the whole name.
        if back:
            menu._pop()
            return
        if delta:
            # Clamp (no wrap) so a fast spin zips straight to OK at the end
            # (or 'a' at the start); acceleration lets a quick flick get there.
            self.sel = clamp(self.sel + _accel(delta), 0, len(_NAME_RING) - 1)
            self.dirty = True
        if click:
            item = _NAME_RING[self.sel]
            if item == 'OK':
                menu._commit_name(self)
            elif item == 'DEL':
                if self.name:
                    self.name = self.name[:-1]
                self.dirty = True
            elif len(self.name) < PRESET_NAME_MAX:
                self.name += item
                # Keep the candidate on the same letter for the next slot
                # (handy for double letters / similar chars).
                self.dirty = True

    def _draw_line(self, d):
        # One row: the committed name, then the active append slot rendered IN
        # PLACE at the end, knocked out (black on a white block). The candidate
        # scrolls in that slot; DEL/OK show as a back-arrow / check glyph so they
        # occupy a single cell just like a letter. A space is a blank white block
        # -- itself the "a space goes here" cue.
        y = NAME_ROW_Y
        d.fill_rect(0, y, DISPLAY_WIDTH, CHAR_H, 0)
        name = self.name
        maxc = DISPLAY_WIDTH // CHAR_W
        if len(name) + 1 > maxc:               # keep the active slot on-screen
            name = name[-(maxc - 1):]
        item = _NAME_RING[self.sel]
        total = (len(name) + 1) * CHAR_W        # committed chars + active slot
        sx = clamp((DISPLAY_WIDTH - total) // 2, 0, max(0, DISPLAY_WIDTH - total))
        if name:
            d.text(name, sx, y, MENU_C_SEL)      # committed chars, normal
        ax = sx + len(name) * CHAR_W            # active slot origin
        d.fill_rect(ax, y, CHAR_W, CHAR_H, MENU_C_SEL)  # knockout background (white)
        if item == 'DEL':
            _glyph_del(d, ax, y, 0)
        elif item == 'OK':
            _glyph_ok(d, ax, y, 0)
        elif item != ' ':
            d.text(item, ax, y, 0)              # candidate letter/digit, black

    def render(self, menu):
        # Preset-name entry, drawn 1x. On open/resume do a full clear; on a
        # turn/click just repaint the single word row (word + inline active slot),
        # so scrolling the ring stays snappy.
        if not (menu.dirty or self.dirty):
            return
        menu.dirty = False
        self.dirty = False
        full = self.full or menu._needs_clear
        try:
            d = amyboard.display
            if full:
                d.fill(0)
                d.text('NAME PRESET', 0, EDIT_TITLE_Y, MENU_C_SEL)
                self._draw_line(d)
                self.full = False
                menu._needs_clear = False
                menu._panel_dirty_to = 128   # name entry owned the full screen
                _begin_flush(0, 127)
                return
            self._draw_line(d)
            if not _push_rows(NAME_ROW_Y, NAME_ROW_Y + CHAR_H - 1):
                amyboard.display_refresh()
        except Exception as e:
            _render_fault('_NameLevel.render', e)


# ---------------------------------------------------------------------------
# About. A static credits/version card, pushed on the menu stack like any other
# level. It draws 1x text edge to edge (x=0) rather than through _MenuLevel,
# because a menu ITEM row is indented 12px and 12px leading -- 14 usable chars
# and 12px per line -- which cannot hold this text. Full width at CHAR_W=8 gives
# 16 chars per line, and 11 rows at a 10px pitch plus two 7px block gaps end at
# y=126 of 128.
#
# BOTH budgets are tight and BOTH are hand-set, so if you edit this text, re-run
# tools/about_preview.py -- it renders the card at true 1x off this very code and
# reports the extent plus any line over 16 chars. Over-long lines do not wrap;
# they are silently clipped at the panel edge. An earlier draft kept every word
# ("Version 1.0" and "2025_07_30" on their own rows, plus a "more info at"
# label) and did fit -- at a 9px pitch, i.e. 1px of leading, which rendered as an
# unreadable wall. The wording below is the version that buys real leading.
# ---------------------------------------------------------------------------
ABOUT_LINE_H = 10        # 8px glyph + 2px leading
ABOUT_GAP    = 7         # extra pixels where a block gap ('' line) falls
ABOUT_TOP_Y  = 0         # SAME y as a list page's title row (_MenuLevel.render starts
                         # its title at 0), so the header does not hop when you click
                         # in from the root -- both screens head with the word ARCTOR,
                         # which made a 2px shift very visible. The grid header (y=1)
                         # and name entry (EDIT_TITLE_Y=2) are each a pixel or two
                         # lower; they are not reached from a same-titled screen.
ABOUT_MAX_CH = DISPLAY_WIDTH // CHAR_W    # 16 -- the hard per-line character budget

# Colours here are LEVELS 0-15, not 0-255 intensities -- see the COLOUR SCALE note
# at DISPLAY_TEXT_COLOR. Confirmed legible on hardware down to level 1; 6 sits well
# clear of that floor while reading obviously subordinate to the 15 lines.
ABOUT_C_BRIGHT = 15      # the thing being credited
ABOUT_C_DIM    = 6       # its label


def _about_lines():
    # (text, bright). '' starts a block gap instead of a row. Dim = the label,
    # bright = the thing being credited, so the card scans without any rules.
    # NB the version row is 'v1.0  2026_07_30' = exactly 16 chars: a two-digit
    # minor ('v1.10') would push it over and lose the last date digit.
    return (
        (SKETCH_NAME, True),
        ('v%s  %s' % (VERSION, VERSION_DATE), False),
        ('', False),
        ('designer:', False),
        ('sawtoothwave', True),
        ('modified from', False),
        ('AMY code by', False),
        ('bwhitman & dpwe', True),
        ('', False),
        # github.com/sawtoothwave/amyboard/blob/main/arctor.md -- the /blob/main/
        # form, so it resolves in a browser as typed rather than 404ing. Wrapped at
        # '/' boundaries to stay inside the 16-char budget; still 4 rows, so the
        # card's height is unchanged. Concatenating these four in order must
        # reproduce the URL exactly -- grid_sim asserts that.
        ('github.com/', True),
        ('sawtoothwave/', True),
        ('amyboard/blob/', True),
        ('main/arctor.md', True),
    )


def _about_extent():
    # Bottom-most pixel row the card occupies; > 128 means the text no longer
    # fits and something has to be cut.
    y = ABOUT_TOP_Y
    for text, _ in _about_lines():
        y += ABOUT_GAP if not text else ABOUT_LINE_H
    return y


class _AboutLevel:
    # Static: nothing here changes with input, so it paints once and then only
    # repaints when the menu asks for a clear (returning from an overlay).
    #
    # ANY input dismisses it -- turn, click or hold alike. This is the one level
    # that breaks the universal turn=scroll / click=in / hold=out model, and
    # deliberately: there is nothing here to scroll and nothing to drill into, so
    # every gesture means the same thing ("I'm done reading"). A turn that did
    # nothing would read as a hang on a screen that gives no other feedback.
    __slots__ = ('title',)

    def __init__(self):
        self.title = 'ABOUT'      # SketchMenu/grid_sim identify levels by .title

    def handle(self, menu, delta, click, back):
        if delta or click or back:
            menu._pop()

    def render(self, menu):
        if not (menu.dirty or menu._needs_clear):
            return
        menu.dirty = False
        try:
            d = amyboard.display
            d.fill(0)
            y = ABOUT_TOP_Y
            for text, bright in _about_lines():
                if not text:
                    y += ABOUT_GAP
                    continue
                d.text(text[:ABOUT_MAX_CH], 0, y,
                       ABOUT_C_BRIGHT if bright else ABOUT_C_DIM)
                y += ABOUT_LINE_H
            menu._needs_clear = False
            menu._panel_dirty_to = 128     # the card owns the full screen
            _begin_flush(0, 127)
        except Exception as e:
            _render_fault('_AboutLevel.render', e)


# ---------------------------------------------------------------------------
# Param Control knob grid. A group's params are shown as a 4-wide grid of value
# bars instead of a text list + single-slider editor. A CURSOR (bright rule above and
# below the cell) navigates cell-to-cell; a CLICK SELECTS the cell (knockout /
# reverse-video) and turning then adjusts that param live; click/back returns to the
# cursor. The header shows the focused param's FULL label + value (bucketed params
# show their word via fmt; bipolar bars anchor at center). Drawing is fill_rect/text
# only and redraws just the changed cell(s) to stay audio-safe.
#
# The grid is ROW-MAJOR: params fill ACROSS a row of 4 before wrapping to the next,
# and the cursor travels the same way. So PARAMS order within a group is a LAYOUT
# decision -- each run of 4 consecutive params is a row, which is why the group lists
# are written in fours. Reordering a group MOVES CELLS on screen.
#
# SECTION HEADERS (_Param.section) cut a group into labelled runs -- Osc is an "OSC A"
# row, an "OSC B" row, then "DRIFT". They exist to make the CELLS
# readable: with the header carrying the context, a cell label drops from 4 chars to 3
# (APIT -> PIT), and 3 chars is 24px in a 32px cell, i.e. actual margins. 4-char labels
# packed 4-across with no gap were an unreadable wall. Sections cost vertical space, so
# a sectioned group can paginate where a flat one would not -- that trade is deliberate.
#
# Because a header row shifts everything beneath it, a cell's position is NO LONGER
# arithmetic on the cursor index. _grid_layout resolves each group to absolute (page,
# x, y) once at open, and both the full and incremental draws read positions from it.
# Add a param and the layout re-packs itself; nothing here needs a matching edit.
#
# WHY 4 COLUMNS IS TIGHT (the constraint behind several oddities below): 128/4 = 32 px
# per cell, and the framebuf font is a fixed 8 px cell (CHAR_W) with no smaller
# variant -- so a 4-char label is exactly 32 px, the WHOLE cell, with zero margin.
# Every grid label is <=4 chars for that reason; a 5-char label would silently spill
# into the next cell. It is also why the cursor is two horizontal RULES rather than
# the bounding box it was at 3 columns: a box's left/right edges cost 1 px each, and
# at 32 px there is no such px to give. Rules spend height (28 px/cell) instead, which
# is the dimension we have spare. Going back to a box means going back to 3-char
# labels; the two are a package.
# ---------------------------------------------------------------------------
GRID_COLS = 4           # columns/page. 4*32 = 128 consumes the FULL panel width, so
                        # there is no right margin -- which is why the page indicator
                        # lives in a reserved band at the BOTTOM (see GRID_PAGE_*).
GRID_HDR_H = 12         # top band: the focused param's VALUE only, right-aligned. It
                        # carried the param's NAME + an underline rule too, until both
                        # were found to visually collide with the first row of cells
                        # (the name reads as a cell label; the rule reads as a cursor).
                        # The cell cursor + section header already say WHICH param, so
                        # the name was redundant -- only the live value was not.
# Section header row = air above + 8px text + air below. The two pads are NOT equal on
# purpose: the gap above separates this header from the PREVIOUS section's knobs, the
# gap below ties it to its OWN knobs. Above must therefore be the larger of the two,
# or the header reads as belonging to the section it sits under. (It shipped with 1px
# above / 3px below, which did exactly that.)
GRID_SECT_RULE_W = 12   # max px of divider rule each side of a section name. Clipped
                        # to the room the name leaves, so a long title shortens both
                        # rules rather than overrunning the panel. Went edge-to-edge,
                        # then 24; at 12 these are short ticks flanking the name rather
                        # than lines leading to it -- confirmed legible on the panel.
GRID_SECT_TOP = 5       # air above the header text
GRID_SECT_BOT = 3       # air below the header text, before its cells
GRID_SECT_H = GRID_SECT_TOP + 8 + GRID_SECT_BOT        # 16
GRID_CELL_W = DISPLAY_WIDTH // GRID_COLS               # 32 (see "WHY 4 COLUMNS" above)
GRID_CELL_H = 21        # Cell content is fixed relative to its top: label text at +2
                        # (the font inks 7 of its 8 rows, so +2..+8), value bar at
                        # +11..+16. The cursor rule sits at +GRID_CELL_H-2 and the
                        # knockout ends at +GRID_CELL_H-3, so THIS constant alone sets
                        # the bottom padding. It was 28 (a 9px gap that read as the rule
                        # belonging to the NEXT row), then 22; 21 is the current floor --
                        # the px came out of the label/bar gap to fund GRID_SECT_TOP,
                        # since the budget below has no slack to take it from.
GRID_BAR_W = 26         # must clear GRID_CELL_W with a little air (was 32 at 3 cols,
                        # which would now overrun the 32px cell edge-to-edge)
GRID_PAGE_H = 5         # bottom band reserved for the page indicator. ALWAYS reserved,
                        # even single-page: _grid_layout has to know its vertical limit
                        # before it knows the page count, so reserving unconditionally
                        # keeps packing deterministic. Only 5px because the marks are
                        # 2px tall -- the band is sized to the indicator, not padded
                        # out, since every px here is a px the sections cannot have.
                        # THE WHOLE BUDGET, and why 3 sections/page is arithmetic
                        # rather than a hardcoded rule:
                        #   12 (header) + 3 * (16 section + 21 cell) = 123 = the limit
                        #   (128 - 5) exactly. A 4th section needs 160 -> it pages.
                        # Nothing here has slack: grow any constant and you get 2
                        # sections per page. Re-check this sum before touching one.
GRID_PAGE_Y = 124       # page-indicator marks' top edge, inside that band
GRID_BAR_H = 6          # bar height (px), thinned from 8 -> fewer lit OLED pixels to
                        # cut the current-coupled audio noise. Fill is GRID_BAR_H-4
                        # tall (2px here); lower further if more reduction is needed.

# Grid colours. !! THESE ARE STILL WRITTEN ON THE 0-255 SCALE AND SO DO NOT MEAN
# WHAT THEY SAY !! The framebuf masks to the low nibble (see the COLOUR SCALE note
# near DISPLAY_TEXT_COLOR), so the level each one actually paints is `value & 15`:
#
#   LABEL    215 ->  7     BAR_OUT  110 -> 14     BAR_FILL 205 -> 13
#   TICK     150 ->  6     CURSOR   255 -> 15     KNOCK    255 -> 15
#   SECT     200 ->  8     SECT_RUL  70 ->  6     HDR_NAME 210 ->  2
#   HDR_VAL  255 -> 15     PAGE_OFF  20 ->  4
#
# Two of those invert their intent: the "unfocused bar outline" (14) paints BRIGHTER
# than the "unfocused bar fill" (13), and the header group name (2) is far dimmer
# than "dimmer than its live value" implies. Left as-is ON PURPOSE -- rewriting 11
# constants changes how the instrument looks on every screen, which is a design call
# to make deliberately with eyes on the panel, not a bug fix to slip into a rename.
# When that happens: pick levels 0-15 directly, and delete this block comment.
#
# Original rationale, still valid: bright OLED pixels draw more current, which
# couples audible noise into the audio path (its tone shifts as the highlight lights
# up). First noise lever is thinning the bars (above); dimming these is the next.
GRID_C_LABEL    = 215   # unfocused cell label
GRID_C_BAR_OUT  = 110   # unfocused bar outline
GRID_C_BAR_FILL = 205   # unfocused bar fill
GRID_C_TICK     = 150   # bipolar center tick
GRID_C_CURSOR   = 255   # cursor box + its brightened content
GRID_C_KNOCK    = 255   # selected-cell knockout block
GRID_C_SECT     = 200   # section header text (e.g. "OSC A")
GRID_C_SECT_RUL = 70    # section header trailing rule
GRID_C_HDR_NAME = 210   # top-band group name ("OSC"), dimmer than its live value
GRID_C_HDR_VAL  = 255   # header value
GRID_C_PAGE_OFF = 20    # inactive page-indicator mark. The panel is 4-bit (top
                        # nibble), so this is level 1 -- the dimmest still-visible
                        # step (below ~16 is fully off, which would hide the 2nd-page
                        # cue). If active/inactive still read alike, the panel can't
                        # go dimmer -> switch to a filled(active)/hollow(inactive) shape.


_render_faults = set()          # render sites that have already reported once


def _render_fault(where, exc):
    # Report a render failure ONCE per site, then stay quiet.
    #
    # The render paths below deliberately swallow exceptions: a drawing fault must
    # never take audio down with it. But swallowing SILENTLY turns a hard error into
    # a blank screen with no clue, which is the worst possible failure mode -- a real
    # one (a call site left on an old signature, so every grid draw raised TypeError)
    # presented as "the menu doesn't open, then unfreezes", and cost a debug round on
    # hardware. Printing keeps the safety and buys back the traceback.
    #
    # Once-per-site matters: these run inside loop(), so an unconditional print would
    # emit ~14x/second forever and bury the first, most useful report. Deploying new
    # code resets the sketch, hence the set, so a fix always gets a fresh report.
    if where in _render_faults:
        return
    _render_faults.add(where)
    print('RENDER FAULT in %s: %s: %s' % (where, type(exc).__name__, exc))
    try:
        import sys
        sys.print_exception(exc)        # MicroPython's traceback printer
    except Exception:
        pass


def _grid_layout(params):
    # Resolve a group's params into absolute screen positions ONCE, at level open.
    # Before section headers a cell's position was pure arithmetic on the cursor index
    # (slot % GRID_COLS); a header row breaks that -- position now depends on how many
    # sections precede it -- so we precompute instead of deriving. Returns:
    #   cells   -- list PARALLEL to params: (page, x, y) of each param's cell
    #   heads   -- list of (page, y, text) section header rows to draw
    #   npages  -- total pages
    # Params are walked in order and cut into RUNS of equal `section`; each run is a
    # header row plus ceil(len/GRID_COLS) rows of cells. A run is kept whole on one
    # page -- we page-break BEFORE a run that would not fit, never mid-run, so a
    # header can never strand its cells on the next page.
    #
    # EVERY run reserves a header row, labelled or not. An unlabelled one draws
    # nothing but still holds the space, which does two jobs: it separates the run from
    # the one above, and -- because the slot is unconditional -- row N of one group
    # lands at the same y as row N of every
    # other, so paging between groups doesn't make the cells jump.
    limit = 128 - GRID_PAGE_H           # keep the page-indicator band clear
    cells = []
    heads = []
    page = 0
    y = GRID_HDR_H
    i = 0
    n = len(params)
    while i < n:
        sect = params[i].section
        j = i
        while j < n and params[j].section == sect:
            j += 1
        # Split the run into rows FIRST -- a row ends when it is full OR when the next
        # param asks to start one (_Param.newrow), so this is no longer derivable by
        # arithmetic on the index.
        rows = []
        row = []
        for k in range(i, j):
            if row and (params[k].newrow or len(row) == GRID_COLS):
                rows.append(row)
                row = []
            row.append(k)
        if row:
            rows.append(row)
        need = GRID_SECT_H + len(rows) * GRID_CELL_H
        if cells and y + need > limit:               # won't fit -> break before the run
            page += 1
            y = GRID_HDR_H
        # Header SEGMENTS: (x0, x1, TEXT) spans to centre a name within. Normally one
        # span across the full panel. A param carrying `hdr` splits the row at its
        # column: the run's own name keeps the columns to its left, the `hdr` name
        # takes the rest. Split at the cell's COLUMN boundary, not its offset x, so a
        # half-column-shifted cell still sits centred under its name.
        if sect:                                     # unlabelled: reserve, draw nothing
            # Only the FIRST row can split the header: the header row sits above the
            # whole run, so a split on a later row would label columns it isn't over.
            # That caps a split section at one row -- 4 cells, e.g. 2 + 2 -- which is
            # the shape it is for. A 5th param wraps and the halves stop lining up.
            split = 0
            for c, k in enumerate(rows[0] if rows else ()):
                if c and params[k].hdr:              # c=0 would leave an empty left half
                    split = c * GRID_CELL_W
                    break
            if split:
                segs = [(0, split, sect.upper()),
                        (split, DISPLAY_WIDTH, params[rows[0][c]].hdr.upper())]
            else:
                segs = [(0, DISPLAY_WIDTH, sect.upper())]
            heads.append((page, y, segs))
        y += GRID_SECT_H
        for r, row in enumerate(rows):
            for c, k in enumerate(row):
                x = c * GRID_CELL_W
                if params[k].halfcol:
                    x += GRID_CELL_W // 2
                cells.append((page, x, y + r * GRID_CELL_H))
        y += len(rows) * GRID_CELL_H
        i = j
    return cells, heads, page + 1


def _draw_grid_section(d, y, segs):
    # A section header row: the dim name CENTRED, with the dividing rule split to lead
    # and trail it -- "----- OSC A -----". The rule is what visually groups the cells
    # beneath; the text alone read as just another cell label. (It has been left-then-
    # rule, and rule-then-right; split-and-centred reads as a divider rather than as a
    # heading with a decoration attached, and stays symmetric whatever the name's
    # length.)
    ty = y + GRID_SECT_TOP
    d.fill_rect(0, y, DISPLAY_WIDTH, GRID_SECT_H, 0)
    for sx0, sx1, text in segs:
        _draw_grid_section_seg(d, ty, sx0, sx1, text)


def _draw_grid_section_seg(d, ty, sx0, sx1, text):
    # One name centred inside [sx0, sx1) with its rules. Split out from the row so a
    # header can carry TWO names side by side (see _Param.hdr) without either one
    # thinking it owns the panel. A full-width header is just the single-span case,
    # so the common path is unchanged.
    tw = len(text) * CHAR_W
    tx = sx0 + (sx1 - sx0 - tw) // 2
    d.text(text, tx, ty, GRID_C_SECT)
    # MIDDLE-aligned with the text: the font inks rows 0..6 of its 8px cell (row 7 is
    # blank), so +3 is the glyphs' true mid-height. Mid-height only works now that the
    # rule is SPLIT -- when it ran continuously behind the name it read as
    # strikethrough, which is what drove it to the baseline. Split, it stops short of
    # the text on both sides and reads as a divider the name sits inside.
    ry = ty + 3
    gap = 4                     # px of clear air each side of the name. The glyphs
                                # already carry ~1px of their own, so this reads as ~5.
    # Each rule is a fixed-length stub butted against the name, NOT a line to the panel
    # edge: it marks the name as a divider without drawing the eye out to the margins,
    # where there is nothing to look at. Both are clipped to whatever room the name
    # leaves, so a long section title shortens them symmetrically instead of pushing
    # them off-panel.
    # Clipped to this SEGMENT's bounds, not the panel's: two names on one row must not
    # run their rules into each other's half. With a full-width span these are 0 and
    # DISPLAY_WIDTH, i.e. exactly the old behaviour.
    lend = tx - gap                              # left rule ends here
    lx = max(sx0, lend - GRID_SECT_RULE_W)
    if lend > lx:
        d.fill_rect(lx, ry, lend - lx, 1, GRID_C_SECT_RUL)
    rx = tx + tw + gap                           # right rule starts here
    rend = min(sx1, rx + GRID_SECT_RULE_W)
    if rend > rx:
        d.fill_rect(rx, ry, rend - rx, 1, GRID_C_SECT_RUL)


def _grid_disp(p, v):
    # Header value string: the param's friendly fmt (a WORD for bucketed params,
    # a unit'd number for others) when it has one, else the raw 0-127.
    if p.fmt:
        try:
            return p.fmt(v)
        except Exception:
            pass
    return str(v)


def _draw_grid_cell(d, x0, ctop, label, val01, bipolar, state, reveal=None):
    # (x0, ctop) is the cell's absolute top-left, straight from _grid_layout -- the
    # caller no longer derives it from the cursor index, because section headers make
    # position depend on the run structure above the cell, not just its ordinal.
    # state: 'none'|'cursor'|'selected'.
    cxc = x0 + GRID_CELL_W // 2
    d.fill_rect(x0, ctop, GRID_CELL_W, GRID_CELL_H, 0)      # clear cell first
    if state == 'selected':
        # Knockout spans the FULL cell width: a 4-char label uses all 32px, so the
        # 2px-inset block this used at 3 columns would leave the label's outermost
        # glyph columns stranded white-on-black outside the block.
        d.fill_rect(x0, ctop + 1, GRID_CELL_W, GRID_CELL_H - 3, GRID_C_KNOCK)
        fg = 0; barout = 0; barfill = 0; tick = 0
    else:
        fg = GRID_C_LABEL; barout = GRID_C_BAR_OUT; barfill = GRID_C_BAR_FILL; tick = GRID_C_TICK
        if state == 'cursor':
            fg = GRID_C_CURSOR; barout = GRID_C_CURSOR; barfill = GRID_C_CURSOR; tick = GRID_C_CURSOR
            # ONE full-width rule UNDER the cell -- not a box (its vertical edges would
            # eat the 2px the label needs, see "WHY 4 COLUMNS IS TIGHT"), and not the
            # pair of rules it started as. Underneath rather than on top: a rule ABOVE
            # the cell sits in the gap between rows and reads as though it belongs to
            # the row above -- worst of all under a section header, where it looked like
            # the header's own underline. Below, it can only belong to this cell.
            d.fill_rect(x0, ctop + GRID_CELL_H - 2, GRID_CELL_W, 1, GRID_C_CURSOR)
    lx = cxc - (len(label) * CHAR_W) // 2
    # Floor at x0, not x0+1: a 4-char label centres to exactly x0 and has no px to
    # spare, and +1 would push its last glyph into the next cell.
    d.text(label, max(x0, lx), ctop + 2, fg)
    bx = cxc - GRID_BAR_W // 2
    by = ctop + 11        # 1px tighter to the label than it was, to fund GRID_SECT_TOP
    if reveal:
        # Hover reveal (see HOVER_REVEAL_MS): the CC number takes the bar's place for
        # a beat. Drawn 1px ABOVE the bar band because the glyph cell is 8px and the
        # bar is only 6 -- from ctop+10 it spans rows 10..17, clearing the cursor rule
        # at ctop+GRID_CELL_H-2. Floored at x0 like the label: '#103' is 4 glyphs = a
        # full 32px cell with nothing to spare, and +1 would spill into the neighbour.
        rx = cxc - (len(reveal) * CHAR_W) // 2
        d.text(reveal, max(x0, rx), by - 1, fg)
        return
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


def _draw_grid_header(d, group, disp):
    # Top band: the GROUP name left, the focused param's live VALUE right. It once
    # showed the param's full name + an underline rule instead; both fought the first
    # row of cells for the eye and went. The group name is safe where the param name
    # was not -- it is static, so it reads as a page title rather than as another cell
    # label competing for attention.
    # The value wins any fight for space: it changes as you turn, so it must never be
    # the thing that gets clipped.
    d.fill_rect(0, 0, DISPLAY_WIDTH, GRID_HDR_H, 0)
    vs = str(disp)
    d.text(vs, DISPLAY_WIDTH - len(vs) * CHAR_W, 1, GRID_C_HDR_VAL)
    avail = (DISPLAY_WIDTH // CHAR_W) - len(vs) - 1     # leave a 1-char gap
    if avail > 0:
        d.text(group[:avail], 0, 1, GRID_C_HDR_NAME)


def _draw_page_dots(d, y, page, npages):
    # THE page indicator, shared by the knob grid and the paginated list menus so
    # the two read identically. Current page = a bright full-width DASH; every
    # other page = a dim 2x2 DOT, centred as a row at `y`. The count of pages and
    # your position in it are both readable at a glance; the dot has to stay
    # visible (GRID_C_PAGE_OFF is the dimmest still-visible 4-bit step) since it is
    # the only cue that another page exists at all. Callers guard npages < 2.
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


def _draw_grid_pages(d, page, npages):
    # Knob-grid page indicator, in the reserved bottom band (GRID_PAGE_H). It sat
    # stacked in a 2px RIGHT margin when the grid was 3 columns of 42px (=126,
    # leaving x>=126 spare); at 4 columns of 32px the cells own the full width and
    # there is no such margin, so it moved down here. _grid_layout keeps this band
    # clear, so no cell ever draws over it and it survives incremental cell redraws
    # without being repainted.
    if npages < 2:
        return
    _draw_page_dots(d, GRID_PAGE_Y, page, npages)


GRID_EXT_MAX = 2        # Max externally-changed (MIDI/CC) cells repainted per tick;
                        # any others carry to the next tick. This is a MEASURED audio
                        # budget, not a guess -- on-device, min-of-7:
                        #   one cell (32x21, 336 B) = 9.5 ms
                        #   top band (128x12, 768 B) = 19.5 ms
                        #   full panel (8192 B)      = 241.7 ms
                        # loop() arrives every ~69 ms and the render blocks it, so the
                        # worst incremental frame must stay well inside that: header
                        # 19.5 + two cursor cells 19 + two ext cells 19 = 57.5 ms. The
                        # shipped code already spent 38.5 ms/frame without trouble, so
                        # 57.5 is ~1.5x a known-good figure -- but it IS the ceiling.
                        # Raising this trades audio headroom for CC-flood catch-up
                        # speed; 12 stale cells drain in 6 ticks (~0.4 s) at 2, which
                        # only a bulk CC dump can even provoke. One or two knobs moving
                        # -- the real case -- is caught up in a single tick.


# Hover CC reveal. Rest the cursor on a cell (without selecting it) and after
# HOVER_REVEAL_MS its value BAR starts alternating with the param's CC number, so
# you can map an E16 knob without leaving the grid or consulting docs/CC_MAPPING.md.
#
# Why these numbers:
# - 3 s is longer than any deliberate pass-through: spinning the cursor across a
#   page never trips it, so it only ever appears when you have actually stopped.
# - 2 s per phase is ~29 loop() ticks. loop() arrives every ~69 ms, so anything
#   under ~140 ms would render as a stutter rather than a swap. Started at 1 s;
#   2 s on hardware reads calmer and gives you time to actually read the number.
#   Costs one 32x21 cell push every 2 s (9.5 ms MEASURED, see GRID_EXT_MAX) --
#   well under 1% of the audio budget, on a surface that is idle by definition.
# - The reveal starts ON at 3 s (bar, then #CC, then bar...), so the information
#   appears the moment the dwell is recognised.
#
# MENU_IDLE_MS (15 s) is deliberately NOT touched: the editor still idles out to
# the display mode, which bounds the cycle to a 12 s window. That was the user's
# call -- an animation that runs forever would keep the panel busy indefinitely.
HOVER_REVEAL_MS = 3000
HOVER_CYCLE_MS = 2000


class _GridLevel:
    # A Param Control group shown as the knob grid. `idx` is the cursor position in
    # `params` (flat, all of the group's params); `editing` distinguishes cursor
    # (rule) from selected (knockout). Live
    # values come from param_values, so no per-param value is cached here.
    __slots__ = ('group', 'params', 'cells', 'heads', 'npages', 'cc_idx', 'ext',
                 'idx', 'editing', 'entry_value', 'dirty', 'full', 'prev_idx',
                 'prev_page', 'prev_hdisp', 'hover_at', 'hover_shown')

    def __init__(self, group):
        self.group = group
        self.params = [p for p in PARAMS if p.group == group]
        # Resolved once here, not per frame: the layout depends only on PARAMS, which
        # never changes at runtime. cells[i] = (page, x, y) for params[i].
        self.cells, self.heads, self.npages = _grid_layout(self.params)
        # cc -> cell index, so an incoming CC can find the cell it belongs to in O(1)
        # from the MIDI callback (which must stay cheap -- it runs in the ISR path and
        # records only, never draws).
        self.cc_idx = {p.cc: i for i, p in enumerate(self.params)}
        self.ext = set()         # cell indices moved by an EXTERNAL CC since the last
                                 # render, awaiting repaint (drained GRID_EXT_MAX/tick)
        self.idx = 0
        self.editing = False
        self.entry_value = 0     # value snapshot when editing began (hold-to-revert)
        self.dirty = True
        self.full = True
        self.prev_idx = 0
        self.prev_page = 0
        self.prev_hdisp = None   # last header string drawn; None = never drawn
        self.hover_at = time.ticks_ms()   # when the cursor last moved (hover clock)
        self.hover_shown = False          # is the CC reveal currently on screen?

    def _reveal(self, now):
        # Is the CC number showing this instant? Pure function of the hover clock, so
        # the phase can't drift: it is recomputed from elapsed time every tick rather
        # than toggled by a counter that a skipped frame would desync.
        if self.editing:
            return False         # selected = you're turning it; a swapping readout
                                 # would fight the value you're watching
        el = time.ticks_diff(now, self.hover_at)
        if el < HOVER_REVEAL_MS:
            return False
        return ((el - HOVER_REVEAL_MS) // HOVER_CYCLE_MS) % 2 == 0

    def _touch(self):
        # Any input restarts the dwell: the reveal is for a cell you've settled on.
        self.hover_at = time.ticks_ms()
        if self.hover_shown:
            self.hover_shown = False
            self.dirty = True    # the CC is on screen -- put the bar back

    def note_cc(self, cc):
        # An external CC moved a param: mark its cell stale for the next render.
        # Records state only (never draws) -- see SketchMenu.note_external_cc.
        i = self.cc_idx.get(cc)
        if i is not None:
            self.ext.add(i)
            self.dirty = True

    def commit_pending_click(self):
        # A deferred editor single-click's window passed with no second click:
        # commit (keep the value) and drop back to the cursor.
        if self.editing:
            self.editing = False
            self.dirty = True
            self._touch()        # start the dwell fresh on the cell you just left

    def handle(self, menu, delta, click, back):
        # SELECTED (editing): turn adjusts live; single click commits (keeps value,
        # back to cursor -- deferred so a 2nd click can arrive); DOUBLE click resets
        # to the param default (stays editing); HOLD reverts to the entry value and
        # exits. CURSOR: turn moves cell-to-cell; click selects (snapshots the value
        # for revert); hold pops back to the group chooser.
        if delta or click or back:
            self._touch()
        if self.editing:
            p = self.params[self.idx]
            if back:                             # hold: revert + exit editing
                menu._click_pending_at = 0
                handle_cc(p.cc, self.entry_value)
                self.editing = False
                self.dirty = True
                return
            if delta:                            # turn cancels a pending click
                menu._click_pending_at = 0
                v = int(param_values.get(p.cc, p.default))
                if p.steps:                      # bucketed: one detent = one bucket
                    v = _bucket_advance(p.steps, v, delta)
                else:
                    v = clamp(v + _accel(delta), 0, 127)
                handle_cc(p.cc, v)               # applies live + records param_values
                self.dirty = True
            if click:
                now = time.ticks_ms()
                if menu._click_pending_at and \
                        time.ticks_diff(now, menu._click_pending_at) <= EDIT_DBLCLICK_MS:
                    # double click: reset to the param default, stay editing.
                    menu._click_pending_at = 0
                    handle_cc(p.cc, p.default)
                    self.dirty = True
                else:
                    # first click: defer commit-and-exit so a 2nd click can arrive
                    # (fired by service_pending once the window passes).
                    menu._click_pending_at = now
            return
        if back:
            menu._pop()
            return
        if delta:
            self.idx = clamp(self.idx + delta, 0, len(self.params) - 1)
            self.dirty = True
        if click:
            p = self.params[self.idx]
            self.entry_value = int(param_values.get(p.cc, p.default))  # for hold-revert
            self.editing = True
            self.dirty = True

    def render(self, menu):
        # Full draw on open / resume / page-change (flushed progressively);
        # otherwise redraw only the changed cell(s) + header. The value is applied
        # live in handle(), so sound tracks every detent even when a redraw is
        # throttled to the next frame.
        now = time.ticks_ms()
        # The hover reveal is the ONLY self-starting redraw in the menu -- with the
        # encoder untouched nothing else marks us dirty -- so its phase is checked
        # BEFORE the dirty gate below, not after it.
        reveal = self._reveal(now)
        if reveal != self.hover_shown:
            self.hover_shown = reveal
            self.dirty = True
        if not (menu.dirty or self.dirty):
            return
        page = self.cells[self.idx][0]
        full = self.full or menu._needs_clear or (page != self.prev_page)
        if not full and time.ticks_diff(now, menu._edit_last_render) < EDIT_REFRESH_MS:
            return
        menu.dirty = False
        self.dirty = False
        menu._edit_last_render = now
        try:
            d = amyboard.display
            fp = self.params[self.idx]                   # focused param
            fv = int(param_values.get(fp.cc, fp.default))
            hdisp = _grid_disp(fp, fv)
            if full:
                # Opening the group, resuming from idle or turning a page all count as
                # arriving somewhere new: restart the dwell so the CC can't be mid-cycle
                # on a cell you have not actually rested on yet.
                self.hover_at = now
                self.hover_shown = False
                d.fill(0)
                _draw_grid_header(d, self.group.upper(), hdisp)
                for hpage, hy, hsegs in self.heads:
                    if hpage == page:
                        _draw_grid_section(d, hy, hsegs)
                for gi, p in enumerate(self.params):
                    cpage, cx, cy = self.cells[gi]
                    if cpage != page:
                        continue
                    st = ('selected' if self.editing else 'cursor') if gi == self.idx else 'none'
                    v = int(param_values.get(p.cc, p.default))
                    _draw_grid_cell(d, cx, cy, p.grid,
                                    clamp(v, 0, 127) / 127.0, p.bipolar, st)
                _draw_grid_pages(d, page, self.npages)
                self.full = False
                menu._needs_clear = False
                menu._panel_dirty_to = 128
                self.prev_idx = self.idx
                self.prev_page = page
                self.prev_hdisp = hdisp
                self.ext.clear()         # every cell was just drawn from param_values
                _begin_flush(0, 127)
                return
            # Incremental: the header (only if its text actually changed) + the cells
            # that moved -- the two cursor cells, plus any a CC moved out from under us.
            if hdisp != self.prev_hdisp:
                # Guarded because this band costs 19.5 ms MEASURED -- as much as two
                # cells -- and the value is unchanged on most frames (a cursor move to
                # a param that reads the same, or a CC on a cell we are not focused on).
                # Redrawing it unconditionally spent half the frame's budget restating
                # a fact.
                _draw_grid_header(d, self.group.upper(), hdisp)
                if not _push_rows(0, GRID_HDR_H - 1):
                    amyboard.display_refresh()
                self.prev_hdisp = hdisp
            todo = {self.prev_idx, self.idx}             # dedup (same cell when editing)
            if self.ext:
                # Externally-changed cells. Drop any not on this page -- switching page
                # is a full repaint, which draws them from param_values anyway.
                self.ext = set(i for i in self.ext if self.cells[i][0] == page)
                spare = [i for i in self.ext if i not in todo]
                todo |= set(spare[:GRID_EXT_MAX])        # bounded: see GRID_EXT_MAX
                self.ext.difference_update(todo)
                if self.ext:
                    self.dirty = True                    # more to drain next tick
            for gi in todo:
                cpage, cx, cy = self.cells[gi]
                if cpage == page:                        # the other cell may be off-page
                    p = self.params[gi]
                    st = ('selected' if self.editing else 'cursor') if gi == self.idx else 'none'
                    v = int(param_values.get(p.cc, p.default))
                    _draw_grid_cell(d, cx, cy, p.grid,
                                    clamp(v, 0, 127) / 127.0, p.bipolar, st,
                                    '#%d' % p.cc if (st == 'cursor' and self.hover_shown)
                                    else None)
                    # Push exactly the cell we redrew -- its rect comes from the layout,
                    # so section headers above it shift the window automatically and
                    # never get repainted (they can't change without a full redraw).
                    if not _push_window(cx, cx + GRID_CELL_W - 1, cy, cy + GRID_CELL_H - 1):
                        amyboard.display_refresh()
            self.prev_idx = self.idx
            self.prev_page = page
        except Exception as e:
            _render_fault('_GridLevel.render', e)


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
        self._repaint()
        self._panel_dirty_to = 128    # the display mode was full-screen behind us

    def close(self):
        # A scan in progress writes its landing preset to settings here, so every
        # way out of the MENU persists the same thing. The top of the stack is
        # enough: a scan level is only ever entered from the root and leaves the
        # stack the moment you act on it (a hold pops it, a click replaces it with
        # Param Control), and both of those call persist() themselves -- so if a
        # scan is still live when we close, it is what you are looking at.
        if self.stack and isinstance(self.cur, _ScanLevel):
            self.cur.persist()
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
        self._repaint()
        self._edit_last_render = 0
        self._panel_dirty_to = 128    # the display mode was full-screen while idle

    def note_external_cc(self, cc, val):
        # Called from the MIDI callback: mark whichever cell this CC drives so the
        # next render repaints it. Records state only (never draws) -- loop()'s render
        # picks it up -- so it stays audio-safe. The value itself is already in
        # param_values (handle_cc put it there, before us); this only says "that cell
        # is stale".
        #
        # ANY cell, not just the selected one. It used to be `cur.editing and
        # cur.params[cur.idx].cc == cc` -- correct for the old full-screen slider,
        # which showed exactly ONE param, so nothing else could be on screen to go
        # stale. The grid shows twelve, so that same condition silently left up to 11
        # bars lying about their parameter: turn an E16 knob for a visible-but-not-
        # selected param and its bar would not move until a FULL repaint (reopening
        # the group, changing page, or an idle-resume) happened to correct it.
        if self.suspended or not self.stack:
            return
        cur = self.cur
        if isinstance(cur, _GridLevel):
            cur.note_cc(cc)

    def service_pending(self, now):
        # Fire a deferred editor single-click (commit + exit to the list) once the
        # double-click window passes with no second click.
        if not self._click_pending_at:
            return
        if time.ticks_diff(now, self._click_pending_at) <= EDIT_DBLCLICK_MS:
            return
        self._click_pending_at = 0
        if self.stack and isinstance(self.cur, _GridLevel):
            self.cur.commit_pending_click()

    def _repaint(self):
        # Schedule a full clear + redraw of the current level on the next render
        # (vs. just `dirty`, which lets the level diff/redraw incrementally).
        self.dirty = True
        self._needs_clear = True

    def _push_level(self, lvl):
        self.stack.append(lvl)
        self._repaint()

    def _pop(self):
        if self.stack:
            self.stack.pop()
        self._repaint()

    # -- The menu tree + preset workflows (the CONTENT of the levels) ---------

    def _root(self):
        # Preset actions live directly on the root now (no "Presets" submenu).
        return _MenuLevel(SKETCH_NAME, [
            ('Param control', self._open_params),
            ('Save as preset', self._start_save),
            ('Load preset', self._open_load),
            ('Delete preset', self._open_delete),
            ('Scan presets', self._open_scan),
            ('Display mode', self._open_display),
            ('About', self._open_about),
            ('Exit menu', self.close),
        ])

    def _open_about(self):
        self._push_level(_AboutLevel())

    def _open_params(self):
        # First level of Param Control: the categories (Osc/Filter/Env/LFO/FX).
        # Clicking one drills into its filtered parameter list. Splitting the
        # (now ~40) params this way keeps each list short instead of one long
        # multi-page scroll.
        items = [(name, (lambda g=name: self._open_param_group(g)))
                 for name in PARAM_GROUPS]
        self._push_level(_MenuLevel('PARAM CONTROL', items))

    def _open_param_group(self, group):
        # A category: shown as the 4-column knob grid (_GridLevel). All of the group's
        # params become cells, grouped into labelled sections by _Param.section.
        # Cursor navigates; click selects to edit.
        self._push_level(_GridLevel(group))

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
            self._push_level(_MenuLevel('Current preset:\n%s\n' % name[:MENU_LABEL_MAX], [
                ('Overwrite', (lambda n=name: self._confirm_overwrite(n))),
                ('Save as new', self._start_name_entry),
                ('Cancel', self._pop),
            ]))
        else:
            self._start_name_entry()

    def _start_name_entry(self):
        # Open the name-entry screen; committing it saves the live patch under a
        # new (or typed-over) name.
        self._push_level(_NameLevel())

    def _confirm_overwrite(self, name):
        # Y/N confirm before replacing an existing preset in place. The full name is
        # shown in the header (fits: <=12 chars + quotes + '?'), then a blank line
        # before Yes/No for readability. No or hold pops back to the Save chooser.
        self._push_level(_MenuLevel('OVERWRITE\n"%s"?\n' % name[:MENU_LABEL_MAX], [
            ('Yes', (lambda n=name: self._do_save(n))),
            ('No', self._pop),
        ]))

    def _commit_name(self, lvl):
        # Called when the user clicks OK. Empty names are ignored (stay editing).
        # Otherwise confirm: "SAVE?" (or "OVERWRITE?" if the name already exists)
        # with Yes/No. Yes saves + returns to the Arctor menu; No -- or a hold --
        # pops back to the name-entry screen. A new name at the cap is blocked.
        name = lvl.name.strip()
        if not name:
            return
        if name.upper() == INIT_PRESET_NAME:
            # "INIT" is reserved for the built-in write-protected preset.
            self._push_level(_MenuLevel('NAME RESERVED', [
                ('"%s" is built-in' % INIT_PRESET_NAME, None),
                ('Back', self._pop),
            ]))
            return
        exists = _find_preset(name) >= 0
        if not exists and len(_presets) >= MAX_PRESETS:
            self._push_level(_MenuLevel('PRESETS FULL', [
                ('Max %d reached' % MAX_PRESETS, None),
                ('Back', self._pop),
            ]))
        else:
            head = 'OVERWRITE\n"%s"?' if exists else 'SAVE\n"%s"?'
            self._push_level(_MenuLevel(head % name[:MENU_LABEL_MAX], [
                ('Yes', (lambda n=name: self._do_save(n))),
                ('No', self._pop),
            ]))

    def _do_save(self, name):
        # Persist, flash a "PRESET SAVED!" toast, and drop to the main Arctor
        # menu (the toast auto-dismisses to it -- see render()). On success this
        # name becomes the session's "current" preset (the Overwrite target).
        global _current_preset_name
        ok = _save_preset(name)
        if ok:
            _current_preset_name = name
            _set_setting('current_preset', name)   # resume it after a reset
        self.stack = [self._root()]
        self._show_toast('PRESET SAVED!' if ok else 'SAVE FAILED')
        self._repaint()

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
        self._push_level(_MenuLevel('LOAD PRESET', items))

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
        self._repaint()

    def _open_scan(self):
        # Same list Load offers (INIT + saved, never empty) -- but scrolling it
        # loads as it goes and the level stays put. See _ScanLevel.
        self._push_level(_ScanLevel(_load_list()))

    def _delete_menu(self):
        # The delete list, rebuilt each time so it always reflects the current set.
        # Shown alphabetically (same order as Load, via _sorted_presets). Items
        # delete by NAME, not index, so reordering can never remove the wrong one.
        if not _presets:
            return _MenuLevel('DELETE PRESET', [('(none saved)', None)])
        items = [(p.get('name', '?')[:MENU_LABEL_MAX],
                  (lambda nm=p.get('name', '?'): self._confirm_delete(nm)))
                 for p in _sorted_presets()]
        return _MenuLevel('DELETE PRESET', items)

    def _open_delete(self):
        self._push_level(self._delete_menu())

    def _confirm_delete(self, name):
        # Destructive: the name is in the two-line header ("Delete preset\n<name>?")
        # and only Yes/No are selectable. No or hold pops back to the delete list.
        # A 12-char-max name + '?' always fits the 16-char header line. The trailing
        # newline leaves a blank line between the name and the Yes/No options.
        self._push_level(_MenuLevel('Delete preset\n%s?\n' % name, [
            ('Yes', (lambda n=name: self._do_delete(n))),
            ('No', self._pop),
        ]))

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
        self._repaint()

    def _open_display(self):
        items = [(m.name, (lambda m=m: self._pick_mode(m))) for m in DISPLAY_MODES]
        self._push_level(_MenuLevel('DISPLAY MODE', items))

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
                    self._repaint()
            return
        if not self.is_open:
            return
        # Each level type owns its own input semantics -- see the level classes.
        self.cur.handle(self, delta, click, back)

    def _draw_toast(self, msg):
        # Full-screen centered confirmation (1x), pushed progressively.
        try:
            d = amyboard.display
            d.fill(0)
            w = len(msg) * CHAR_W
            sx = clamp((DISPLAY_WIDTH - w) // 2, 0, max(0, DISPLAY_WIDTH - w))
            d.text(msg, sx, 60, MENU_C_SEL)
            self._panel_dirty_to = 128   # toast owned the full screen
            _begin_flush(0, 127)
        except Exception as e:
            _render_fault('_draw_toast', e)

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
            self._repaint()
        if not self.is_open:
            return
        # Each level type owns its own drawing -- see the level classes.
        self.cur.render(self)


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
        except Exception as e:
            _render_fault('_force_display_redraw', e)


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