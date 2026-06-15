"""
Minimal AMYboard oscillator test sketch.

This sketch keeps the known-good AMY native MIDI CC mapping approach and adds
oscillator B controls only.
"""

import amy
import amyboard

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
CC_FILTER_CUTOFF = 74
CC_FILTER_RESO = 71
CC_FILTER_ENV = 30
CC_FILTER_TYPE = 31
CC_FILTER_KEYTRACK = 32
OSC_A_DEFAULT_HZ = 440.0
OSC_B_DEFAULT_HZ = 440.0
FILTER_TYPE_DEFAULT = amy.FILTER_LPF


def wave_from_cc(cc_value):
    cc_value = max(0, min(127, int(cc_value)))
    waves = [amy.SINE, amy.PULSE, amy.SAW_UP, amy.SAW_DOWN, amy.TRIANGLE, amy.NOISE]
    wave_index = min(len(waves) - 1, (cc_value * len(waves)) // 128)
    return waves[wave_index]


def cents_to_ratio(cents):
    return 2.0 ** (cents / 1200.0)


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


def set_osc_tune(osc_index, cc_value):
    base_hz = OSC_A_DEFAULT_HZ if int(osc_index) == 0 else OSC_B_DEFAULT_HZ
    tuned_hz = progressive_tune_hz(cc_value, base_hz)
    amy.send(synth=SYNTH, osc=int(osc_index), freq='%.3f,1' % tuned_hz)


def set_osc_wave(osc_index, cc_value):
    amy.send(synth=SYNTH, osc=int(osc_index), wave=wave_from_cc(cc_value))


def midi_callback(is_sysex):
    if is_sysex or tulip is None:
        return

    message = tulip.midi_in()
    while message is not None and len(message) > 0:
        status = message[0]
        if (status & 0xF0) == 0xB0:
            channel = (status & 0x0F) + 1
            if channel == 1:
                cc_num = message[1]
                cc_val = message[2]
                if cc_num == CC_OSC_A_TUNE:
                    set_osc_tune(0, cc_val)
                elif cc_num == CC_OSC_B_TUNE:
                    set_osc_tune(1, cc_val)
        message = tulip.midi_in()


def setup():
    print('Minimal CC20 test sketch starting')

    try:
        amyboard.init_midi(type='A')
        print('TRS MIDI initialized for Type A')
    except Exception as exc:
        print('TRS MIDI init skipped:', exc)

    amy.reset()

    # Four voices, two oscillators per voice. Notes on MIDI channel 1 should play this synth.
    amy.send(synth=SYNTH, num_voices=4, oscs_per_voice=2)

    # Oscillator A: first of two identical parallel oscillator paths.
    amy.send(
        synth=SYNTH,
        osc=0,
        chained_osc=1,
        wave=amy.SAW_DOWN,
        freq='440,1',
        amp={'vel': 1, 'eg0': 1},
        duty=0.5,
        filter_type=FILTER_TYPE_DEFAULT,
        filter_freq='800,0,,,0',
        resonance=2.0,
        bp1='0,1,300,0.25,800,0',
        bp0='0,1,1000,0',
    )

    # Oscillator B: identical parallel path feeding the same filter control set.
    amy.send(
        synth=SYNTH,
        osc=1,
        wave=amy.SAW_DOWN,
        freq='440,1',
        amp={'vel': 1, 'eg0': 1},
        duty=0.5,
        filter_type=FILTER_TYPE_DEFAULT,
        filter_freq='800,0,,,0',
        resonance=2.0,
        bp1='0,1,300,0.25,800,0',
        bp0='0,1,1000,0',
    )

    # Clear any old CC mappings, then map oscillator controls directly.
    amy.send(synth=SYNTH, midi_cc='255')
    amy.send(synth=SYNTH, midi_cc='%d,0,0,5,0,i%%iv0w%%V' % CC_OSC_A_WAVE)
    amy.send(synth=SYNTH, midi_cc='%d,0,0.05,0.95,0,i%%iv0d%%v' % CC_OSC_A_DUTY)
    amy.send(synth=SYNTH, midi_cc='%d,0,0,1,0,i%%iv0a%%v,0,1' % CC_OSC_A_LEVEL)

    amy.send(synth=SYNTH, midi_cc='%d,0,0,5,0,i%%iv1w%%V' % CC_OSC_B_WAVE)
    amy.send(synth=SYNTH, midi_cc='%d,0,0.05,0.95,0,i%%iv1d%%v' % CC_OSC_B_DUTY)
    amy.send(synth=SYNTH, midi_cc='%d,0,0,1,0,i%%iv1a%%v,0,1' % CC_OSC_B_LEVEL)

    amy.send(synth=SYNTH, midi_cc='%d,1,50,8000,0,i%%iv0F%%v' % CC_FILTER_CUTOFF)
    amy.send(synth=SYNTH, midi_cc='%d,1,50,8000,0,i%%iv1F%%v' % CC_FILTER_CUTOFF)
    amy.send(synth=SYNTH, midi_cc='%d,1,0.5,16,0,i%%iv0R%%v' % CC_FILTER_RESO)
    amy.send(synth=SYNTH, midi_cc='%d,1,0.5,16,0,i%%iv1R%%v' % CC_FILTER_RESO)
    amy.send(synth=SYNTH, midi_cc='%d,0,0,4,0,i%%iv0F,,,,%%v' % CC_FILTER_ENV)
    amy.send(synth=SYNTH, midi_cc='%d,0,0,4,0,i%%iv1F,,,,%%v' % CC_FILTER_ENV)
    amy.send(synth=SYNTH, midi_cc='%d,0,0,1,0,i%%iv0F,%%v' % CC_FILTER_KEYTRACK)
    amy.send(synth=SYNTH, midi_cc='%d,0,0,1,0,i%%iv1F,%%v' % CC_FILTER_KEYTRACK)
    amy.send(synth=SYNTH, midi_cc='%d,0,0,2,1,i%%iv0G%%V' % CC_FILTER_TYPE)
    amy.send(synth=SYNTH, midi_cc='%d,0,0,2,1,i%%iv1G%%V' % CC_FILTER_TYPE)

    if tulip is not None:
        tulip.midi_callback(midi_callback)
        print('tulip MIDI callback registered for tune CCs only')
    else:
        print('tulip MIDI unavailable, progressive tuning disabled')

    print('Minimal oscillator sketch ready')
    print('Send notes on MIDI channel 1')
    print('CC20-23 control oscillator A')
    print('CC24-27 control oscillator B')
    print('CC74 cutoff, CC71 resonance, CC30 env, CC31 type, CC32 keytrack')
    print('Tuning defaults: osc A = 440 Hz, osc B = 440 Hz')
    print('CC20/24: dead zone around 440, detune nearby, then fifths and octaves')


def loop():
    pass


setup()
