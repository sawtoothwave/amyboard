# CC Mapping Reference

This document is the frozen baseline MIDI CC map for the AMYboard rebuild, and it now also documents the live behavior implemented in `sketch.py`.

## Frozen Baseline

- **MIDI Channel**: 1
- **Frozen CC Range**: 20-32, 40-47, 71, 74
- **Status**: `sketch.py` now implements this full map as a live 2-oscillator (A/B) + filter instrument with 6-voice polyphony. CC 20/24 use the stepped musical tuning map; CC 21/25 use the six-wave buckets; the filter, filter type, key scale, and both ADSR envelopes are wired to their CCs. The implementation column below records the live behavior.

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

## Live Implementation Notes

These describe how `sketch.py` currently maps each CC (0-127) to an AMY parameter. Value ranges here are implementation choices, not part of the frozen baseline, and can be tuned without changing the CC assignments.

| CC | Parameter | Live behavior |
|----|-----------|---------------|
| 20 / 24 | Osc A / B Pitch | Stepped musical tuning map (see below). Both oscillators reference 440 Hz, so they are unison at center. |
| 21 / 25 | Osc A / B Wave | Six-wave buckets: Sine, Pulse, Saw Down, Saw Up, Triangle, Noise (see below). |
| 22 / 26 | Osc A / B Duty | Pulse duty cycle, 0.05-0.95. |
| 23 / 27 | Osc A / B Level | Oscillator amplitude, 0.0-1.0 (scales that osc's amp envelope). |
| 74 | Filter Cutoff | Logarithmic, ~30 Hz to ~16 kHz. |
| 71 | Filter Resonance | 0.7-8.0 (AMY Q range). |
| 30 | Filter Env Amount | EG1 depth coefficient (octave-style), 0.0-2.0. |
| 31 | Filter Type | Four buckets across 0-127: LPF24, LPF, BPF, HPF. |
| 32 | Key Scale | Filter `note` tracking coefficient, 0.0-1.0 (0 = none, 1 = full keyboard tracking). |
| 40-43 | VCF A/D/S/R | Filter EG1 envelope. Times ~1-5000 ms (quadratic); sustain 0.0-1.0. |
| 44-47 | VCA A/D/S/R | Amp EG0 envelope. Times ~1-5000 ms (quadratic); sustain 0.0-1.0. |

A single shared filter processes both oscillators per voice. Each voice has three oscillators: a `SILENT` filter-head (osc 0) chained to Osc A (osc 1) chained to Osc B (osc 2). AMY sums A and B into the silent head's buffer, then applies the VCA envelope and one filter to that combined signal, so the filter affects Osc A and Osc B equally. Velocity sensitivity and the VCA envelope live on the head; Osc A/B carry steady mix levels. Parameter changes are applied live per-CC, so turning a knob never resets voices or cuts off held notes.

> Note: the original rebuild intent referenced Osc B to 220 Hz (an octave-down sub). The live build instead references both oscillators to 440 Hz (unison at center) per an explicit design decision; change `REF_HZ` handling in `sketch.py` if a per-oscillator reference is reintroduced.

## Rebuild Rule

Any future rebuild of `sketch.py` should treat the frozen CC assignment table as fixed unless the user explicitly changes the mapping.

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

The following areas are not part of the frozen baseline and are not implemented in the current build:

- LFO controls
- Effects controls (reverb / echo / chorus)
- OLED display and onboard encoder/button navigation
- Preset save/recall

## Tuning and Wave Maps (Implemented)

The two maps below are the specification the live `sketch.py` implements for the pitch (CC 20/24) and wave (CC 21/25) controls.

### Stepped Oscillator Tuning

The pitch tune controls use a stepped musical map rather than a smooth linear sweep.

In the live build both oscillators reference 440 Hz (unison at center). The original intent had Osc B reference 220 Hz for an octave-down sub; the same stepped shape applies regardless of reference.

- CC 60-68: dead zone at the reference pitch
- CC 52-59: fine detune from about -35 cents up to -5 cents
- CC 69-76: fine detune from about +5 cents up to +35 cents
- CC 40-51: fixed perfect fifth down, about -700 cents
- CC 77-88: fixed perfect fifth up, about +700 cents
- CC 24-39: one octave down
- CC 89-104: one octave up
- CC 0-23: two octaves down
- CC 105-127: two octaves up

This stepped map gives a musically useful center detune zone with fast access to fifths and octaves.

### Wave Buckets

The oscillator wave selection order is the six core analog waves (no wavetable, PCM or ALGO):

1. Sine
2. Pulse
3. Saw Down
4. Saw Up
5. Triangle
6. Noise

The live build uses equal-width CC buckets across 0-127:

- CC 0-20: Sine
- CC 21-41: Pulse
- CC 42-63: Saw Down
- CC 64-84: Saw Up
- CC 85-105: Triangle
- CC 106-127: Noise
