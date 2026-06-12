# CC Mapping Reference

This document defines all MIDI CC assignments for AMYboard control via the OXI e16.

## Overview

- **MIDI Channel**: 1
- **CC Range**: 20-70 (organized by section)
- **Architecture**: Pages 4, 8, and 12 of the e16 scene map to these CCs

## Page 4: Oscillators + Filter

| Knob | Parameter | CC | Range | Notes |
|------|-----------|----|----|-------|
| 1 | Osc A Pitch | 20 | 0-127 | 440 Hz at CC 64; -2 to +2 octaves |
| 2 | Osc A Wave | 21 | 0-127 | Sine, Tri, Saw, Pulse, Noise |
| 3 | Osc A Duty | 22 | 0-127 | Pulse width; 64 = 50% |
| 4 | Osc A Level | 23 | 0-127 | Amplitude; 100 ≈ unity |
| 5 | Osc B Pitch | 24 | 0-127 | 220 Hz at CC 32 (octave below A) |
| 6 | Osc B Wave | 25 | 0-127 | Sine, Tri, Saw, Pulse, Noise |
| 7 | Osc B Duty | 26 | 0-127 | Pulse width; 64 = 50% |
| 8 | Osc B Level | 27 | 0-127 | Amplitude; 100 ≈ unity |
| 9 | Filter Cutoff | 28 | 0-127 | 100 Hz to 8 kHz; 100 ≈ typical |
| 10 | Filter Resonance | 29 | 0-127 | Filter Q; 0 = flat, 127 = self-oscillating |
| 11 | Filter Envelope | 30 | 0-127 | Envelope modulation depth |
| 12 | Filter Type | 31 | 0-127 | 0-42 = LP, 43-84 = BP, 85-127 = HP |
| 13 | Key Scale | 32 | 0-127 | Cutoff tracking; 0 = off |

## Page 8: Modulators (Envelopes + LFO)

| Knob | Parameter | CC | Range | Notes |
|------|-----------|----|----|-------|
| 1 | VCF Attack | 40 | 0-127 | 1-500 ms |
| 2 | VCF Decay | 41 | 0-127 | 1-2000 ms |
| 3 | VCF Sustain | 42 | 0-127 | 0-1.0 |
| 4 | VCF Release | 43 | 0-127 | 1-2000 ms |
| 5 | VCA Attack | 44 | 0-127 | 1-500 ms (note-on envelope) |
| 6 | VCA Decay | 45 | 0-127 | 1-2000 ms |
| 7 | VCA Sustain | 46 | 0-127 | 0-1.0 |
| 8 | VCA Release | 47 | 0-127 | 1-2000 ms |
| 9 | LFO Frequency | 48 | 0-127 | 0.1-20 Hz |
| 10 | LFO Depth | 49 | 0-127 | Modulation amount |
| 11 | LFO Shape | 50 | 0-127 | Sine, Tri, Saw, Pulse, Noise |
| 12 | (empty) | 51 | — | — |
| 13 | LFO → Pitch Amt | 52 | 0-127 | Modulation to oscillator frequency |
| 14 | LFO → PWM Amt | 53 | 0-127 | Modulation to pulse width |
| 15 | LFO → Filter Amt | 54 | 0-127 | Modulation to filter cutoff |
| 16 | (empty) | 55 | — | — |

## Page 12: Effects

| Knob | Parameter | CC | Range | Notes |
|------|-----------|----|----|-------|
| 1 | Echo Level | 60 | 0-127 | Effect wet amount; 0 = off |
| 2 | Echo Delay | 61 | 0-127 | 50-1000 ms |
| 3 | Echo Feedback | 62 | 0-127 | 0-1.0 (feedback loop) |
| 4 | (empty) | 63 | — | — |
| 5 | Reverb Level | 64 | 0-127 | Effect wet amount; 0 = off |
| 6 | Reverb Live | 65 | 0-127 | Room size / liveliness |
| 7 | Reverb Dampening | 66 | 0-127 | High-frequency damping |
| 8 | (empty) | 67 | — | — |
| 9 | Chorus Level | 68 | 0-127 | Effect wet amount; 0 = off |
| 10 | Chorus Frequency | 69 | 0-127 | 0.5-5 Hz (modulation rate) |
| 11 | Chorus Depth | 70 | 0-127 | Modulation amount |
| 12 | (empty) | 71 | — | — |

## Frequency Mapping

### Oscillator A (CC 20)
- CC 0: ~27.5 Hz (A0)
- **CC 64: 440 Hz (A4 reference)**
- CC 127: ~880 Hz (A5)
- Range: ±2 octaves from 440 Hz

### Oscillator B (CC 24)
- CC 0: ~13.75 Hz (A-1)
- **CC 32: 220 Hz (A3 reference)**
- CC 64: ~440 Hz
- CC 127: ~880 Hz
- Range: ±2 octaves from 220 Hz

## Waveform Selection

All waveform selectors (Osc A/B Wave, LFO Shape) use:
- CC 0-25: Sine
- CC 26-51: Triangle
- CC 52-76: Sawtooth
- CC 77-102: Pulse/Square
- CC 103-127: Noise

## Filter Type Selection

Filter Type (CC 31):
- CC 0-42: Low-pass (default)
- CC 43-84: Band-pass
- CC 85-127: High-pass

## Default Values (On Power-Up)

All knobs have push-to-reset functionality. Pressing a knob returns it to the default value shown below:

### Page 4
- Osc A Pitch: CC 64 (440 Hz)
- Osc A Wave: CC 0 (sine)
- Osc A Duty: CC 64 (50%)
- Osc A Level: CC 100
- Osc B Pitch: CC 32 (220 Hz)
- Osc B Wave: CC 0 (sine)
- Osc B Duty: CC 64 (50%)
- Osc B Level: CC 100
- Filter Cutoff: CC 100
- Filter Resonance: CC 0 (flat)
- Filter Envelope: CC 0 (off)
- Filter Type: CC 0 (low-pass)
- Key Scale: CC 0 (off)

### Page 8
- VCF ADSR: 0ms, 50ms, 100%, 50ms
- VCA ADSR: 0ms, 50ms, 100%, 50ms
- LFO Freq: CC 30 (~2 Hz)
- LFO Depth: CC 0 (off)
- LFO Shape: CC 0 (sine)
- LFO Modulation: CC 0 (off)

### Page 12
- Echo Level: CC 0 (off)
- Echo Delay: CC 30 (~500 ms)
- Echo Feedback: CC 50 (~50%)
- Reverb Level: CC 0 (off)
- Reverb Live: CC 50
- Reverb Dampening: CC 50
- Chorus Level: CC 0 (off)
- Chorus Freq: CC 30 (~2.5 Hz)
- Chorus Depth: CC 30
