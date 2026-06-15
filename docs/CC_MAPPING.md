# CC Mapping Reference

This document is the frozen baseline MIDI CC map for the AMYboard rebuild.

`sketch.py` is currently considered a dead end and is not the source of truth for control behavior. Until a rebuild is complete, this file is the authoritative mapping reference.

## Frozen Baseline

- **MIDI Channel**: 1
- **Frozen CC Range**: 20-32, 40-47, 71, 74
- **Status**: verified as the intended controller layout, not as a verified live implementation

## Frozen CC Assignments

| Knob | Parameter | CC | Range | Notes |
|------|-----------|----|----|-------|
| 1 | Osc A Pitch | 20 | 0-127 | Baseline pitch control |
| 2 | Osc A Wave | 21 | 0-127 | Baseline waveform control |
| 3 | Osc A Duty | 22 | 0-127 | Baseline pulse-width / duty control |
| 4 | Osc A Level | 23 | 0-127 | Baseline oscillator level control |
| 5 | Osc B Pitch | 24 | 0-127 | Baseline pitch control |
| 6 | Osc B Wave | 25 | 0-127 | Baseline waveform control |
| 7 | Osc B Duty | 26 | 0-127 | Baseline pulse-width / duty control |
| 8 | Osc B Level | 27 | 0-127 | Baseline oscillator level control |
| 9 | Filter Cutoff | 74 | 0-127 | Baseline filter cutoff control |
| 10 | Filter Resonance | 71 | 0-127 | Baseline filter resonance control |
| 11 | Filter Envelope Amount | 30 | 0-127 | Baseline filter envelope depth control |
| 12 | Filter Type | 31 | 0-127 | Baseline filter mode control |
| 13 | Key Scale | 32 | 0-127 | Baseline filter keyboard tracking control |
| 14 | VCF Attack | 40 | 0-127 | Baseline filter envelope attack |
| 15 | VCF Decay | 41 | 0-127 | Baseline filter envelope decay |
| 16 | VCF Sustain | 42 | 0-127 | Baseline filter envelope sustain |
| 17 | VCF Release | 43 | 0-127 | Baseline filter envelope release |
| 18 | VCA Attack | 44 | 0-127 | Baseline amp envelope attack |
| 19 | VCA Decay | 45 | 0-127 | Baseline amp envelope decay |
| 20 | VCA Sustain | 46 | 0-127 | Baseline amp envelope sustain |
| 21 | VCA Release | 47 | 0-127 | Baseline amp envelope release |

## Rebuild Rule

Any future rebuild of `sketch.py` should treat the table above as fixed unless the user explicitly changes the mapping.

## Controller Pages

These page groupings are retained only as controller-layout intent. They do not imply that any current sketch implements the full page behavior.

### Page 4: Oscillators + Filter

- Osc A Pitch: CC 20
- Osc A Wave: CC 21
- Osc A Duty: CC 22
- Osc A Level: CC 23
- Osc B Pitch: CC 24
- Osc B Wave: CC 25
- Osc B Duty: CC 26
- Osc B Level: CC 27
- Filter Envelope Amount: CC 30
- Filter Type: CC 31
- Key Scale: CC 32
- Filter Resonance: CC 71
- Filter Cutoff: CC 74

### Page 8: Envelopes

- VCF Attack: CC 40
- VCF Decay: CC 41
- VCF Sustain: CC 42
- VCF Release: CC 43
- VCA Attack: CC 44
- VCA Decay: CC 45
- VCA Sustain: CC 46
- VCA Release: CC 47

## Deferred Controls

The following areas were previously discussed but are not part of the frozen baseline and should not be assumed for a rebuild unless they are reintroduced deliberately:

- LFO controls
- Effects controls
- Detailed value ranges and defaults for any CC
- Filter type bucket definitions

## Preserved Rebuild Notes

The following behaviors are worth preserving as rebuild intent. They are not frozen as verified live behavior, but they should be considered strong candidates for the next implementation.

### Intended Stepped Oscillator Tuning

The intended pitch behavior for the oscillator tune controls was a stepped musical map rather than a smooth linear sweep.

For Osc A, the reference frequency was intended to be 440 Hz. For Osc B, the same shape was intended to apply around its own reference frequency, typically 220 Hz.

- CC 60-68: dead zone at the reference pitch
- CC 52-59: fine detune from about -35 cents up to -5 cents
- CC 69-76: fine detune from about +5 cents up to +35 cents
- CC 40-51: fixed perfect fifth down, about -700 cents
- CC 77-88: fixed perfect fifth up, about +700 cents
- CC 24-39: one octave down
- CC 89-104: one octave up
- CC 0-23: two octaves down
- CC 105-127: two octaves up

This stepped map is preserved because it gave a musically useful center detune zone with fast access to fifths and octaves.

### Intended Wave Buckets

The intended oscillator wave selection order is the six-wave set already reflected in `amy_patch_examples/sketch_5osc_analog.py`:

1. Sine
2. Pulse
3. Saw Down
4. Saw Up
5. Triangle
6. Noise

If the rebuild uses equal-width CC buckets across 0-127, the previously intended split was:

- CC 0-20: Sine
- CC 21-41: Pulse
- CC 42-63: Saw Down
- CC 64-84: Saw Up
- CC 85-105: Triangle
- CC 106-127: Noise
