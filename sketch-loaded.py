# AMYboard Sketch
# DESCRIPTION: 2-oscillator (A/B) analog-style synth matching the frozen CC map.
#   Stepped musical tuning per osc, 6-way wave buckets (no wavetable/PCM/ALGO),
#   resonant filter with VCF envelope + key tracking, VCA envelope. 6-voice
#   polyphony. MIDI ch1 notes (auto-routed to synth 1 by AMY) + CCs (20-32,
#   40-47, 71, 74) handled via midi.add_callback; CV1 1V/oct + CV2 gate.
#   See docs/CC_MAPPING.md for the authoritative control map.

import amy, amyboard, midi, math

SYNTH = 1
NUM_VOICES = 6
OSCS_PER_VOICE = 3

# Per-voice oscillator layout. Osc 0 is a SILENT "filter head": AMY sums the
# chained oscillators (A then B) into its buffer, then applies a single shared
# filter + VCA envelope to that sum. This is the only way one filter can affect
# both oscillators -- a non-silent head filters only itself and the chained
# oscillators are mixed in afterward (i.e. unfiltered).
FILT_OSC = 0
OSC_A    = 1
OSC_B    = 2

# ---------------------------------------------------------------------------
# Frozen CC map (docs/CC_MAPPING.md). MIDI channel 1.
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
RES_MIN = 0.7
RES_MAX = 8.0

# Envelope time range (ms) for ADSR CCs; quadratic for finer control low down.
ENV_TIME_MIN_MS = 1
ENV_TIME_MAX_MS = 5000

# CV input
CV_GATE_THRESHOLD = 1.0
CV1_BASE_NOTE = 60

# ---------------------------------------------------------------------------
# Live patch state (musical defaults; overwritten by incoming CCs)
# ---------------------------------------------------------------------------
a_cents = 0.0
a_wave  = amy.SAW_DOWN
a_duty  = 0.5
a_level = 0.80

b_cents = 0.0
b_wave  = amy.SAW_UP
b_duty  = 0.5
b_level = 0.70

flt_cutoff  = 2500.0
flt_res     = 1.2
flt_type    = amy.FILTER_LPF24
flt_env_amt = 0.8
key_scale   = 0.0

vcf_env = {'a': 5, 'd': 350, 's': 0.2, 'r': 300}
vca_env = {'a': 5, 'd': 200, 's': 0.85, 'r': 350}

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
        return -35.0 + (cc - 52) * (30.0 / 7.0)     # fine -35..-5 cents
    if cc <= 68:
        return 0.0                                  # dead zone at reference
    if cc <= 76:
        return 5.0 + (cc - 69) * (30.0 / 7.0)       # fine +5..+35 cents
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


def cv_volts_to_midi(volts):
    n = int(round(CV1_BASE_NOTE + volts * 12.0))
    return clamp(n, 0, 127)


# ---------------------------------------------------------------------------
# AMY graph builders
# ---------------------------------------------------------------------------
def osc_freq(cents):
    # const in Hz at note 69, note coef 1.0 -> tracks keyboard with cents offset.
    return {'const': REF_HZ * math.pow(2.0, cents / 1200.0), 'note': 1}


def osc_amp(level):
    # Per-oscillator mix level as a constant amp coefficient. vel/eg0 are zeroed
    # so A/B contribute a steady level; velocity and the VCA envelope contour
    # are applied once, by the SILENT filter head.
    return {'const': clamp(level, 0.0, 1.0), 'vel': 0, 'eg0': 0}


def vca_bp():
    # VCA amplitude envelope (EG0) on the filter head; shapes the summed A+B mix.
    return '%d,1,%d,%g,%d,0' % (vca_env['a'], vca_env['d'],
                                vca_env['s'], vca_env['r'])


def flt_bp():
    # EG1 filter envelope (peak 1.0; depth set by filter_freq eg1 coefficient).
    return '%d,1,%d,%g,%d,0' % (vcf_env['a'], vcf_env['d'],
                                vcf_env['s'], vcf_env['r'])


def filter_freq_coefs():
    return {'const': flt_cutoff, 'eg1': flt_env_amt, 'note': key_scale}


def init_synth():
    # Allocate voices once. AMY auto-routes incoming MIDI channel-1 notes to
    # this synth; note-ons propagate down the chain (head -> A -> B).
    amy.send(synth=SYNTH, num_voices=0)
    amy.send(synth=SYNTH, num_voices=NUM_VOICES, oscs_per_voice=OSCS_PER_VOICE)

    # Filter head: SILENT, so A+B sum into its buffer before one shared filter
    # and the VCA envelope are applied. Velocity sensitivity lives here.
    amy.send(synth=SYNTH, osc=FILT_OSC,
             wave=amy.SILENT,
             filter_type=flt_type, filter_freq=filter_freq_coefs(),
             resonance=flt_res,
             bp0=vca_bp(), bp1=flt_bp(),
             chained_osc=OSC_A)

    amy.send(synth=SYNTH, osc=OSC_A,
             wave=a_wave, freq=osc_freq(a_cents), duty=a_duty,
             amp=osc_amp(a_level),
             chained_osc=OSC_B)

    amy.send(synth=SYNTH, osc=OSC_B,
             wave=b_wave, freq=osc_freq(b_cents), duty=b_duty,
             amp=osc_amp(b_level))


def update_filter_freq():
    amy.send(synth=SYNTH, osc=FILT_OSC, filter_freq=filter_freq_coefs())


def update_vca():
    amy.send(synth=SYNTH, osc=FILT_OSC, bp0=vca_bp())


def update_vcf():
    amy.send(synth=SYNTH, osc=FILT_OSC, bp1=flt_bp())


# ---------------------------------------------------------------------------
# CC dispatch -- each CC updates only its parameter live (no voice reset, so
# held notes are never cut off).
# ---------------------------------------------------------------------------
def handle_cc(cc, val):
    global a_cents, a_wave, a_duty, a_level
    global b_cents, b_wave, b_duty, b_level
    global flt_cutoff, flt_res, flt_type, flt_env_amt, key_scale

    if cc == CC_OSC_A_PITCH:
        a_cents = cc_to_detune_cents(val)
        amy.send(synth=SYNTH, osc=OSC_A, freq=osc_freq(a_cents))
    elif cc == CC_OSC_A_WAVE:
        a_wave = cc_to_wave(val)
        amy.send(synth=SYNTH, osc=OSC_A, wave=a_wave)
    elif cc == CC_OSC_A_DUTY:
        a_duty = cc_to_duty(val)
        amy.send(synth=SYNTH, osc=OSC_A, duty=a_duty)
    elif cc == CC_OSC_A_LEVEL:
        a_level = cc_unit(val)
        amy.send(synth=SYNTH, osc=OSC_A, amp=osc_amp(a_level))
    elif cc == CC_OSC_B_PITCH:
        b_cents = cc_to_detune_cents(val)
        amy.send(synth=SYNTH, osc=OSC_B, freq=osc_freq(b_cents))
    elif cc == CC_OSC_B_WAVE:
        b_wave = cc_to_wave(val)
        amy.send(synth=SYNTH, osc=OSC_B, wave=b_wave)
    elif cc == CC_OSC_B_DUTY:
        b_duty = cc_to_duty(val)
        amy.send(synth=SYNTH, osc=OSC_B, duty=b_duty)
    elif cc == CC_OSC_B_LEVEL:
        b_level = cc_unit(val)
        amy.send(synth=SYNTH, osc=OSC_B, amp=osc_amp(b_level))
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


# ---------------------------------------------------------------------------
# MIDI (channel 1): AMY auto-routes notes to synth 1; this callback only needs
# to handle Control Change messages. Registered via midi.add_callback so it
# coexists with the firmware's default MIDI dispatch (which owns the low-level
# tulip.midi_callback hook).
# ---------------------------------------------------------------------------
def midi_cb(m):
    if not m or len(m) < 3:
        return
    if (m[0] & 0xF0) != 0xB0:        # Control Change only
        return
    if (m[0] & 0x0F) != 0:           # MIDI channel 1
        return
    handle_cc(m[1], m[2])


def setup_midi():
    midi.add_callback(midi_cb)


# ---------------------------------------------------------------------------
# Boot
# ---------------------------------------------------------------------------
init_synth()
setup_midi()


def loop():
    # Monophonic CV: CV1 = 1V/oct pitch, CV2 = gate.
    global cv_gate_active, cv_current_note
    try:
        cv1 = amyboard.cv_in(0)
        cv2 = amyboard.cv_in(1)
    except Exception:
        return

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