# AMYboard Sketch
# DESCRIPTION: 2-oscillator (A/B) analog-style synth matching the frozen CC map.
#   Stepped musical tuning per osc, 6-way wave buckets (no wavetable/PCM/ALGO),
#   resonant filter with VCF envelope + key tracking, VCA envelope, plus a
#   per-voice LFO routed to pitch, PWM and filter. 6-voice polyphony. MIDI ch12
#   notes (auto-routed to synth 12 by AMY) + CCs (20-32, 40-47, 71, 74, 76-80)
#   handled via midi.add_callback; CV1 1V/oct + CV2 gate.
#   See docs/CC_MAPPING.md for the authoritative control map.

import amy, amyboard, midi, math, time

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
# CCs; waveshape (78), PWM depth (79) and filter depth (80) use spare CCs.
CC_LFO_FREQ    = 76
CC_LFO_PITCH   = 77
CC_LFO_WAVE    = 78
CC_LFO_PWM     = 79
CC_LFO_FILT    = 80

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
# (CC 80) is octave-style depth, matching the filter envelope amount.
LFO_FREQ_MIN_HZ = 0.05
LFO_FREQ_MAX_HZ = 20.0
LFO_PITCH_DEPTH_MAX = 0.5    # octaves (quadratic curve; full = +/- 6 semitones)
LFO_PWM_DEPTH_MAX   = 0.45   # duty modulation depth around the set duty
LFO_FILT_DEPTH_MAX  = 2.0    # octaves (matches FLT_ENV_AMT_MAX)

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


def osc_amp(level):
    # Per-oscillator amp for the sounding oscs A/B: the mix level (const) is
    # multiplied by note velocity (vel) and the VCA envelope (eg0 -> bp0). Giving
    # A/B their own VCA means they fade out and self-terminate on note-off rather
    # than depending on the SILENT head to silence them.
    return {'const': clamp(level, 0.0, 1.0), 'vel': 1, 'eg0': 1}


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
    # Allocate voices once. AMY auto-routes incoming MIDI channel-12 notes to
    # this synth; note-ons propagate down the chain (head -> A -> B).
    amy.send(synth=SYNTH, num_voices=0)
    amy.send(synth=SYNTH, num_voices=NUM_VOICES, oscs_per_voice=OSCS_PER_VOICE)

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
             amp=osc_amp(a_level), bp0=vca_bp(),
             mod_source=LFO_OSC,
             chained_osc=OSC_B)

    amy.send(synth=SYNTH, osc=OSC_B,
             wave=b_wave, freq=osc_freq(b_cents), duty=osc_duty(b_duty),
             amp=osc_amp(b_level), bp0=vca_bp(),
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


# ---------------------------------------------------------------------------
# CC dispatch -- each CC updates only its parameter live (no voice reset, so
# held notes are never cut off).
# ---------------------------------------------------------------------------
def handle_cc(cc, val):
    global a_cents, a_wave, a_duty, a_level
    global b_cents, b_wave, b_duty, b_level
    global flt_cutoff, flt_res, flt_type, flt_env_amt, key_scale
    global lfo_freq, lfo_wave, lfo_pitch_depth, lfo_pwm_depth, lfo_filt_depth

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
        amy.send(synth=SYNTH, osc=OSC_A, amp=osc_amp(a_level))
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
        amy.send(synth=SYNTH, osc=OSC_B, amp=osc_amp(b_level))
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
        # Clear the panel and force a fresh redraw on the next render().
        global _display_last_render
        try:
            amyboard.display.fill(0)
            amyboard.display_refresh()
        except Exception:
            pass
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


# Available display modes. A future push-encoder menu will index this list to
# let the user pick which one drives the OLED.
CC_MONITOR_MODE = CCMonitorMode()
DISPLAY_MODES = [CC_MONITOR_MODE]

# The mode currently driving the OLED. Defaults to the CC monitor; swap it with
# set_display_mode() (no menu yet, so this is the only active mode for now).
active_display_mode = CC_MONITOR_MODE


def set_display_mode(mode):
    # Switch the active display mode (intended for a future encoder menu). Clears
    # the panel and lets the incoming mode redraw from scratch.
    global active_display_mode
    active_display_mode = mode
    if DISPLAY_OK:
        try:
            mode.on_activate()
        except Exception:
            pass


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
    if time.ticks_diff(now, _display_last_render) < DISPLAY_REFRESH_MS:
        return
    try:
        active_display_mode.render(now)
    except Exception:
        pass
    _display_last_render = now


# ---------------------------------------------------------------------------
# Boot
# ---------------------------------------------------------------------------
init_synth()
setup_midi()
init_display()


def loop():
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

    # Display last so a CV read error never blocks the screen, and vice versa.
    service_display()