# Paste this entire block into the REPL at >>> prompt
import os
os.makedirs('/user/current', exist_ok=True)
with open('/user/current/sketch.py', 'w') as f:
    f.write("""import amy
import amyboard
import json
import time
try:
    import tulip
except:
    tulip = None

CC_MAP = {
    'osc_a_pitch': 20, 'osc_a_wave': 21, 'osc_a_duty': 22, 'osc_a_level': 23,
    'osc_b_pitch': 24, 'osc_b_wave': 25, 'osc_b_duty': 26, 'osc_b_level': 27,
    'filter_cutoff': 28, 'filter_reso': 29, 'filter_env_amt': 30, 'filter_type': 31, 'filter_keytrack': 32,
    'vcf_attack': 40, 'vcf_decay': 41, 'vcf_sustain': 42, 'vcf_release': 43,
    'vca_attack': 44, 'vca_decay': 45, 'vca_sustain': 46, 'vca_release': 47,
    'lfo_freq': 48, 'lfo_depth': 49, 'lfo_shape': 50,
    'lfo_osc_amt': 52, 'lfo_pwm_amt': 53, 'lfo_filt_amt': 54,
    'echo_level': 60, 'echo_delay': 61, 'echo_feedback': 62,
    'reverb_level': 64, 'reverb_live': 65, 'reverb_damp': 66,
    'chorus_level': 68, 'chorus_freq': 69, 'chorus_depth': 70,
}

def get_default_state():
    return {
        'osc_a_pitch': 64, 'osc_a_wave': 0, 'osc_a_duty': 64, 'osc_a_level': 100,
        'osc_b_pitch': 32, 'osc_b_wave': 0, 'osc_b_duty': 64, 'osc_b_level': 100,
        'filter_cutoff': 100, 'filter_reso': 0, 'filter_env_amt': 0, 'filter_type': 0, 'filter_keytrack': 0,
        'vcf_attack': 0, 'vcf_decay': 50, 'vcf_sustain': 100, 'vcf_release': 50,
        'vca_attack': 0, 'vca_decay': 50, 'vca_sustain': 100, 'vca_release': 50,
        'lfo_freq': 30, 'lfo_depth': 0, 'lfo_shape': 0,
        'lfo_osc_amt': 0, 'lfo_pwm_amt': 0, 'lfo_filt_amt': 0,
        'echo_level': 0, 'echo_delay': 30, 'echo_feedback': 50,
        'reverb_level': 0, 'reverb_live': 50, 'reverb_damp': 50,
        'chorus_level': 0, 'chorus_freq': 30, 'chorus_depth': 30,
    }

def load_state():
    try:
        with open('/user/amyboard_state.json') as f:
            return json.load(f)
    except:
        return get_default_state()

def save_state(state):
    with open('/user/amyboard_state.json', 'w') as f:
        json.dump(state, f)

def cc_to_freq(cc_value, base_hz=440.0):
    octave_offset = (cc_value - 64) / 32.0
    freq = base_hz * (2.0 ** octave_offset)
    return max(27.5, min(freq, 8000))

def apply_osc_settings(state, osc='A'):
    cc_base = 20 if osc == 'A' else 24
    pitch_param = f'osc_{osc.lower()}_pitch'
    wave_param = f'osc_{osc.lower()}_wave'
    duty_param = f'osc_{osc.lower()}_duty'
    level_param = f'osc_{osc.lower()}_level'
    
    base_hz = 440.0 if osc == 'A' else 220.0
    freq = cc_to_freq(state.get(pitch_param, 64), base_hz)
    wave = state.get(wave_param, 0) % 5
    duty = state.get(duty_param, 64) / 127.0
    level = state.get(level_param, 100) / 127.0
    
    amy.send(osc=0 if osc == 'A' else 1, wave=wave, freq=freq, duty=duty, amp=level)

def setup():
    global state
    amy.send(reset=1)
    state = load_state()
    apply_osc_settings(state, 'A')
    apply_osc_settings(state, 'B')

def loop():
    pass

if __name__ == '__main__':
    setup()
""")
print("✓ sketch.py deployed!")
