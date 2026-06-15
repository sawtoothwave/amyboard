# AMYboard Sketch
# Code put here runs first, then loop() is called every 32nd note.
import amyboard, amy

try:
    import tulip
except ImportError:
    tulip = None

SYNTH = 1
CC_OSC_A_TUNE = 20
CC_OSC_A_WAVE = 21
CC_OSC_A_DUTY = 22
CC_OSC_A_LEVEL = 23
CC_OSC_B_TUNE = 24
CC_OSC_B_WAVE = 25
CC_OSC_B_DUTY = 26
CC_OSC_B_LEVEL = 27
CC_FILTER_ENV = 30
CC_FILTER_TYPE = 31
CC_FILTER_KEYTRACK = 32
CC_VCF_ATTACK = 40
CC_VCF_DECAY = 41
CC_VCF_SUSTAIN = 42
CC_VCF_RELEASE = 43
CC_VCA_ATTACK = 44
CC_VCA_DECAY = 45
CC_VCA_SUSTAIN = 46
CC_VCA_RELEASE = 47
CC_FILTER_RESO = 71
CC_FILTER_CUTOFF = 74
CONTROL_OSC_INDEX = 0
CONTROL_OSC_WAVE = 20
LFO_OSC_INDEX = 1
OSC_A_INDEX = 2
OSC_B_INDEX = 3
OSC_A_DEFAULT_HZ = 440.0
OSC_B_DEFAULT_HZ = 220.0
WAVE_BUCKETS = [amy.SINE, amy.PULSE, amy.SAW_DOWN, amy.SAW_UP, amy.TRIANGLE, amy.NOISE]
VCA_ADSR = [0, 50, 1.0, 50]
FILTER_ADSR = [0, 50, 1.0, 50]
FILTER_FREQ_DEFAULT = '800,0,,,0'
RESONANCE_DEFAULT = 2.0

def loop():
    pass

# Do not edit. Set automatically by the knobs on AMYboard Online.
_auto_generated_knobs = """
"""


def cents_to_ratio(cents):
    return 2.0 ** (cents / 1200.0)


def bp_from_adsr(attack_ms, decay_ms, sustain_level, release_ms):
    return '%d,1.0,%d,%.2f,%d,0.0' % (
        int(attack_ms),
        int(decay_ms),
        sustain_level,
        int(release_ms),
    )


def clamp(value, minimum, maximum):
    return max(minimum, min(maximum, value))


def cc_to_ms(cc_value, maximum_ms):
    cc_value = clamp(int(cc_value), 0, 127)
    return int(round((cc_value / 127.0) * maximum_ms))


def cc_to_sustain(cc_value):
    cc_value = clamp(int(cc_value), 0, 127)
    return cc_value / 127.0


def init_control_osc():
    amy.send(
        synth=SYNTH,
        osc=CONTROL_OSC_INDEX,
        reset=1,
        wave=CONTROL_OSC_WAVE,
        chained_osc=OSC_A_INDEX,
        filter_type=amy.FILTER_LPF24,
        filter_freq=FILTER_FREQ_DEFAULT,
        resonance=RESONANCE_DEFAULT,
    )


def init_lfo_osc():
    amy.send(
        synth=SYNTH,
        osc=LFO_OSC_INDEX,
        reset=1,
        wave=amy.TRIANGLE,
        freq=4.0,
        amp=0,
    )


def init_tone_oscs():
    amy.send(
        synth=SYNTH,
        osc=OSC_A_INDEX,
        reset=1,
        chained_osc=OSC_B_INDEX,
        wave=amy.SINE,
        freq='440,1',
        duty=0.5,
        amp='1,0,0,0,0,0,0',
    )
    amy.send(
        synth=SYNTH,
        osc=OSC_B_INDEX,
        reset=1,
        wave=amy.SINE,
        freq='220,1',
        duty=0.5,
        amp='1,0,0,0,0,0,0',
    )


def setup_synth():
    amy.reset()
    amy.send(synth=SYNTH, num_voices=4, oscs_per_voice=4)
    init_control_osc()
    init_lfo_osc()
    init_tone_oscs()
    apply_vca()
    apply_filter()
    amy.send(synth=SYNTH, midi_cc='255')
    amy.send(synth=SYNTH, midi_cc='%d,0,0.05,0.95,0,i%%iv2d%%v' % CC_OSC_A_DUTY)
    amy.send(synth=SYNTH, midi_cc='%d,0,0,1,0,i%%iv2a%%v,0,1' % CC_OSC_A_LEVEL)
    amy.send(synth=SYNTH, midi_cc='%d,0,0.05,0.95,0,i%%iv3d%%v' % CC_OSC_B_DUTY)
    amy.send(synth=SYNTH, midi_cc='%d,0,0,1,0,i%%iv3a%%v,0,1' % CC_OSC_B_LEVEL)
    amy.send(synth=SYNTH, midi_cc='%d,1,20,8000,0,i%%iv0F%%v' % CC_FILTER_CUTOFF)
    amy.send(synth=SYNTH, midi_cc='%d,1,0.5,16,0,i%%iv0R%%v' % CC_FILTER_RESO)
    amy.send(synth=SYNTH, midi_cc='%d,0,-4,4,0,i%%iv0F,,,,%%v' % CC_FILTER_ENV)
    amy.send(synth=SYNTH, midi_cc='%d,0,0,2,1,i%%iv0G%%V' % CC_FILTER_TYPE)
    amy.send(synth=SYNTH, midi_cc='%d,0,0,1,0,i%%iv0F,%%v' % CC_FILTER_KEYTRACK)


def wave_from_cc(cc_value):
    cc_value = clamp(int(cc_value), 0, 127)
    wave_index = min(len(WAVE_BUCKETS) - 1, (cc_value * len(WAVE_BUCKETS)) // 128)
    return WAVE_BUCKETS[wave_index]


def set_osc_wave(osc_index, cc_value):
    amy.send(synth=SYNTH, osc=int(osc_index), wave=wave_from_cc(cc_value))


def apply_vca():
    attack_ms, decay_ms, sustain_level, release_ms = VCA_ADSR
    envelope = bp_from_adsr(attack_ms, decay_ms, sustain_level, release_ms)
    amy.send(synth=SYNTH, osc=CONTROL_OSC_INDEX, amp='0,0,1,1,0,0,0', bp0=envelope)


def apply_filter():
    attack_ms, decay_ms, sustain_level, release_ms = FILTER_ADSR
    amy.send(synth=SYNTH, osc=CONTROL_OSC_INDEX, bp1=bp_from_adsr(attack_ms, decay_ms, sustain_level, release_ms))


def progressive_tune_hz(cc_value, base_hz):
    cc_value = max(0, min(127, int(cc_value)))

    if 60 <= cc_value <= 68:
        return base_hz

    if 52 <= cc_value <= 59:
        cents = -35.0 + (cc_value - 52) * (30.0 / 7.0)
        return base_hz * cents_to_ratio(cents)

    if 69 <= cc_value <= 76:
        cents = 5.0 + (cc_value - 69) * (30.0 / 7.0)
        return base_hz * cents_to_ratio(cents)

    if 40 <= cc_value <= 51:
        return base_hz * cents_to_ratio(-700.0)

    if 77 <= cc_value <= 88:
        return base_hz * cents_to_ratio(700.0)

    if 24 <= cc_value <= 39:
        return base_hz * 0.5

    if 89 <= cc_value <= 104:
        return base_hz * 2.0

    if cc_value < 24:
        return base_hz * 0.25

    return base_hz * 4.0


def set_osc_tune(osc_index, cc_value, base_hz):
    tuned_hz = progressive_tune_hz(cc_value, base_hz)
    amy.send(synth=SYNTH, osc=int(osc_index), freq='%.3f,1' % tuned_hz)


def handle_cc(cc_num, cc_val):
    if cc_num == CC_OSC_A_TUNE:
        set_osc_tune(OSC_A_INDEX, cc_val, OSC_A_DEFAULT_HZ)
    elif cc_num == CC_OSC_A_WAVE:
        set_osc_wave(OSC_A_INDEX, cc_val)
    elif cc_num == CC_OSC_B_TUNE:
        set_osc_tune(OSC_B_INDEX, cc_val, OSC_B_DEFAULT_HZ)
    elif cc_num == CC_OSC_B_WAVE:
        set_osc_wave(OSC_B_INDEX, cc_val)
    elif cc_num == CC_VCF_ATTACK:
        FILTER_ADSR[0] = cc_to_ms(cc_val, 500)
        apply_filter()
    elif cc_num == CC_VCF_DECAY:
        FILTER_ADSR[1] = cc_to_ms(cc_val, 2000)
        apply_filter()
    elif cc_num == CC_VCF_SUSTAIN:
        FILTER_ADSR[2] = cc_to_sustain(cc_val)
        apply_filter()
    elif cc_num == CC_VCF_RELEASE:
        FILTER_ADSR[3] = cc_to_ms(cc_val, 2000)
        apply_filter()
    elif cc_num == CC_VCA_ATTACK:
        VCA_ADSR[0] = cc_to_ms(cc_val, 500)
        apply_vca()
    elif cc_num == CC_VCA_DECAY:
        VCA_ADSR[1] = cc_to_ms(cc_val, 2000)
        apply_vca()
    elif cc_num == CC_VCA_SUSTAIN:
        VCA_ADSR[2] = cc_to_sustain(cc_val)
        apply_vca()
    elif cc_num == CC_VCA_RELEASE:
        VCA_ADSR[3] = cc_to_ms(cc_val, 2000)
        apply_vca()


def midi_callback(is_sysex):
    if is_sysex or tulip is None:
        return

    message = tulip.midi_in()
    while message is not None and len(message) > 0:
        status = message[0]
        if (status & 0xF0) == 0xB0:
            channel = (status & 0x0F) + 1
            if channel == 1:
                handle_cc(message[1], message[2])
        message = tulip.midi_in()


setup_synth()

if tulip is not None:
    try:
        amyboard.init_midi(type='A')
    except Exception:
        pass
    amy.send(synth=SYNTH, midi_cc=str(CC_OSC_A_TUNE))
    amy.send(synth=SYNTH, midi_cc=str(CC_OSC_B_TUNE))
    tulip.midi_callback(midi_callback)
