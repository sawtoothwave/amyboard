"""
AMYboard synthesizer sketch
Controlled via MIDI CCs from OXI e16 on channel 1
Persists settings across power cycles
"""

import amy
import amyboard
import json
import time

# ============================================================================
# CC NUMBER MAPPINGS
# ============================================================================

CC_MAP = {
    # Oscillator A (20-23)
    'osc_a_pitch': 20,
    'osc_a_wave': 21,
    'osc_a_duty': 22,
    'osc_a_level': 23,
    
    # Oscillator B (24-27)
    'osc_b_pitch': 24,
    'osc_b_wave': 25,
    'osc_b_duty': 26,
    'osc_b_level': 27,
    
    # Filter (28-32)
    'filter_cutoff': 28,
    'filter_reso': 29,
    'filter_env': 30,
    'filter_type': 31,
    'filter_key_scale': 32,
    
    # VCF Envelope (40-43)
    'vcf_attack': 40,
    'vcf_decay': 41,
    'vcf_sustain': 42,
    'vcf_release': 43,
    
    # VCA Envelope (44-47)
    'vca_attack': 44,
    'vca_decay': 45,
    'vca_sustain': 46,
    'vca_release': 47,
    
    # LFO (48-54)
    'lfo_freq': 48,
    'lfo_depth': 49,
    'lfo_shape': 50,
    'lfo_osc_amt': 52,
    'lfo_pwm_amt': 53,
    'lfo_filt_amt': 54,
    
    # Effects (60-70)
    'echo_level': 60,
    'echo_delay': 61,
    'echo_feedback': 62,
    'reverb_level': 64,
    'reverb_live': 65,
    'reverb_damp': 66,
    'chorus_level': 68,
    'chorus_freq': 69,
    'chorus_depth': 70,
}

# ============================================================================
# STATE STORAGE
# ============================================================================

STATE_FILE = '/user/amyboard_state.json'

def load_state():
    """Load settings from persistent storage"""
    try:
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    except:
        return get_default_state()

def save_state(state):
    """Save settings to persistent storage"""
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f)
    except Exception as e:
        print(f"Error saving state: {e}")

def get_default_state():
    """Return default synth configuration"""
    return {
        'osc_a_pitch': 64,      # 440Hz
        'osc_a_wave': 0,        # sine
        'osc_a_duty': 64,       # 50%
        'osc_a_level': 100,
        
        'osc_b_pitch': 32,      # 220Hz
        'osc_b_wave': 0,        # sine
        'osc_b_duty': 64,       # 50%
        'osc_b_level': 100,
        
        'filter_cutoff': 100,
        'filter_reso': 0,
        'filter_env': 0,
        'filter_type': 0,       # lowpass
        'filter_key_scale': 0,
        
        'vcf_attack': 0,
        'vcf_decay': 50,
        'vcf_sustain': 100,
        'vcf_release': 50,
        
        'vca_attack': 0,
        'vca_decay': 50,
        'vca_sustain': 100,
        'vca_release': 50,
        
        'lfo_freq': 30,
        'lfo_depth': 0,
        'lfo_shape': 0,         # sine
        'lfo_osc_amt': 0,
        'lfo_pwm_amt': 0,
        'lfo_filt_amt': 0,
        
        'echo_level': 0,
        'echo_delay': 30,
        'echo_feedback': 50,
        
        'reverb_level': 0,
        'reverb_live': 50,
        'reverb_damp': 50,
        
        'chorus_level': 0,
        'chorus_freq': 30,
        'chorus_depth': 30,
    }

# ============================================================================
# FREQUENCY HELPERS
# ============================================================================

def cc_to_freq(cc_value, base_hz=440.0):
    """
    Map MIDI CC value (0-127) to frequency.
    Assumes exponential scaling across 5 octaves (27.5 Hz to 880 Hz).
    CC 64 = base_hz, CC 96 = base_hz * 2, CC 32 = base_hz / 2
    """
    # Map CC 0-127 to octaves relative to base
    octave_offset = (cc_value - 64) / 32.0  # ±2 octaves
    freq = base_hz * (2.0 ** octave_offset)
    return max(27.5, min(freq, 8000))  # Clamp to reasonable range

def wave_index(cc_value):
    """Map CC value to waveform index"""
    waveforms = ['sine', 'tri', 'saw', 'pulse', 'noise']
    idx = (cc_value * len(waveforms)) // 128
    return idx % len(waveforms)

def filter_type_index(cc_value):
    """Map CC value to filter type: 0=lowpass, 1=bandpass, 2=highpass"""
    types = [0, 1, 2]  # AMY filter type values
    return types[(cc_value * 3) // 128]

def envelope_time_ms(cc_value, min_ms=1, max_ms=5000):
    """Map CC value to envelope time in milliseconds"""
    return int(min_ms + (cc_value / 127.0) * (max_ms - min_ms))

# ============================================================================
# SYNTH CONTROL FUNCTIONS
# ============================================================================

def apply_osc_settings(state):
    """Apply oscillator settings to synth 1"""
    # Oscillator A (osc 0, 1)
    amy.send(
        synth=1, osc=0,
        wave=wave_index(state['osc_a_wave']),
        freq=cc_to_freq(state['osc_a_pitch'], 440.0),
        amp=state['osc_a_level'] / 127.0,
        pw=state['osc_a_duty'] / 127.0,
    )
    
    # Oscillator B (osc 2, 3)
    amy.send(
        synth=1, osc=2,
        wave=wave_index(state['osc_b_wave']),
        freq=cc_to_freq(state['osc_b_pitch'], 220.0),
        amp=state['osc_b_level'] / 127.0,
        pw=state['osc_b_duty'] / 127.0,
    )

def apply_filter_settings(state):
    """Apply filter settings"""
    # Map CC values to AMY filter parameters
    cutoff_freq = 100 + (state['filter_cutoff'] / 127.0) * 7900  # 100-8000 Hz
    resonance = state['filter_reso'] / 127.0  # 0-1
    filter_type = filter_type_index(state['filter_type'])
    
    # Apply to both oscillators
    amy.send(
        synth=1, osc=0,
        filter_freq=cutoff_freq,
        filter_res=resonance,
        filter_type=filter_type,
    )
    amy.send(
        synth=1, osc=2,
        filter_freq=cutoff_freq,
        filter_res=resonance,
        filter_type=filter_type,
    )

def apply_vcf_envelope(state):
    """Apply VCF envelope settings"""
    attack = envelope_time_ms(state['vcf_attack'], 1, 500)
    decay = envelope_time_ms(state['vcf_decay'], 1, 2000)
    sustain = state['vcf_sustain'] / 127.0
    release = envelope_time_ms(state['vcf_release'], 1, 2000)
    
    # Envelope 0 modulates filter (via filter_freq parameter)
    amy.send(
        synth=1, osc=0,
        adsr_target=2,  # filter target
        attack=attack,
        decay=decay,
        sustain=sustain,
        release=release,
    )
    amy.send(
        synth=1, osc=2,
        adsr_target=2,
        attack=attack,
        decay=decay,
        sustain=sustain,
        release=release,
    )

def apply_vca_envelope(state):
    """Apply VCA envelope settings"""
    attack = envelope_time_ms(state['vca_attack'], 1, 500)
    decay = envelope_time_ms(state['vca_decay'], 1, 2000)
    sustain = state['vca_sustain'] / 127.0
    release = envelope_time_ms(state['vca_release'], 1, 2000)
    
    # Envelope 1 modulates amplitude (default)
    amy.send(
        synth=1, osc=0,
        adsr_target=1,  # amplitude target
        attack=attack,
        decay=decay,
        sustain=sustain,
        release=release,
    )
    amy.send(
        synth=1, osc=2,
        adsr_target=1,
        attack=attack,
        decay=decay,
        sustain=sustain,
        release=release,
    )

def apply_lfo_settings(state):
    """Apply LFO settings"""
    lfo_freq = 0.1 + (state['lfo_freq'] / 127.0) * 19.9  # 0.1 - 20 Hz
    lfo_wave = wave_index(state['lfo_shape'])
    
    # LFO configuration would go here
    # (Requires osc 4+ reserved for LFO modulation)
    pass

def apply_effect_settings(state):
    """Apply reverb, echo, and chorus"""
    # Echo
    echo_ms = int(50 + (state['echo_delay'] / 127.0) * 950)  # 50-1000ms
    echo_feedback = state['echo_feedback'] / 127.0
    echo_level = state['echo_level'] / 127.0
    
    amy.send(
        synth=1,
        echo_feedback=echo_feedback,
        echo_delay_ms=echo_ms,
    )
    
    # Reverb
    reverb_level = state['reverb_level'] / 127.0
    reverb_live = state['reverb_live'] / 127.0
    reverb_damp = state['reverb_damp'] / 127.0
    
    amy.send(
        synth=1,
        reverb_level=reverb_level,
    )
    
    # Chorus (if supported)
    chorus_level = state['chorus_level'] / 127.0
    chorus_freq = 0.5 + (state['chorus_freq'] / 127.0) * 4.5  # 0.5-5 Hz
    chorus_depth = state['chorus_depth'] / 127.0
    
    pass  # Chorus parameters depend on AMY version

def apply_all_settings(state):
    """Apply all settings to the synth"""
    apply_osc_settings(state)
    apply_filter_settings(state)
    apply_vcf_envelope(state)
    apply_vca_envelope(state)
    apply_lfo_settings(state)
    apply_effect_settings(state)

# ============================================================================
# MIDI HANDLING
# ============================================================================

state = load_state()

def midi_callback(is_sysex):
    """Handle incoming MIDI messages"""
    global state
    
    if is_sysex:
        return
    
    message = tulip.midi_in()
    while message is not None and len(message) > 0:
        status = message[0]
        
        # CC message (0xB0 + channel)
        if (status & 0xF0) == 0xB0:
            channel = (status & 0x0F) + 1
            if channel == 1:  # Only respond to channel 1
                cc_num = message[1]
                cc_val = message[2]
                
                # Find which parameter this CC controls
                for param_name, cc_num_map in CC_MAP.items():
                    if cc_num_map == cc_num:
                        state[param_name] = cc_val
                        
                        # Apply the change
                        if 'osc_a' in param_name or 'osc_b' in param_name:
                            apply_osc_settings(state)
                        elif 'filter' in param_name:
                            apply_filter_settings(state)
                        elif 'vcf' in param_name:
                            apply_vcf_envelope(state)
                        elif 'vca' in param_name:
                            apply_vca_envelope(state)
                        elif 'lfo' in param_name:
                            apply_lfo_settings(state)
                        elif any(x in param_name for x in ['echo', 'reverb', 'chorus']):
                            apply_effect_settings(state)
                        
                        # Save state
                        save_state(state)
                        break
        
        message = tulip.midi_in()

# ============================================================================
# SETUP & INITIALIZATION
# ============================================================================

def setup():
    """Initialize the synthesizer"""
    global state
    
    # Reset AMY
    amy.reset()
    
    # Set up synth 1 as a 4-voice polyphonic synth on MIDI channel 1
    amy.send(synth=1, patch=0, num_voices=4)
    
    # Apply saved settings
    apply_all_settings(state)
    
    # Set up MIDI callback
    tulip.midi_callback(midi_callback)
    
    print("AMYboard initialized")
    print(f"Synth 1 ready on MIDI channel 1")
    print(f"Loaded {len(state)} settings from storage")

def loop():
    """Main loop - minimal work here since MIDI callback handles updates"""
    pass

# Run setup on boot
setup()
