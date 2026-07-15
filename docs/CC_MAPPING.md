# CC Mapping Reference

The MIDI CC map for the AMYboard polysynth (`sketches/01_polysynth.py`), received on **MIDI channel 12**. Each CC updates its parameter live — no voice reset, so held notes are never cut. Several CCs follow AMYboard/standard-MIDI defaults for backwards-compat — filter cutoff (74), resonance (71), the amp (VCA) ADSR (attack 73, decay 75, sustain 79, release 72), LFO rate (76) and vibrato depth (77); the rest use spare CCs.

**CC range:** 1 (mod wheel), 20-33, 40-46, 71-87, 90-92, 95-98, 100-103. Value ranges in the table are implementation choices (tunable without changing the CC assignments). The e16 controller's page/knob layout lives in [E16_SETUP.md](E16_SETUP.md).

## CC Map

| CC | Parameter | Behavior |
|----|-----------|----------|
| 20 / 24 | Osc A / B Pitch | Stepped musical tuning map (see below). Both oscillators reference 440 Hz, so they are unison at center. |
| 21 / 25 | Osc A / B Wave | Six-wave buckets: Sine, Pulse, Saw Down, Saw Up, Triangle, Noise (see below). |
| 22 / 26 | Osc A / B Duty | Pulse duty cycle, 0.05-0.95. |
| 23 / 27 | Osc A / B Level | Oscillator amplitude, 0.0-1.0 (scales that osc's amp envelope). |
| 34 | Octave | Global whole-octave transpose, five buckets: -2, -1, Center (0), +1, +2. Shifts both oscillators together while preserving keyboard tracking (folded into each osc's `freq` const). **Default -1 oct** to reconcile the middle-C naming convention — a controller/sequencer that labels MIDI note 60 as "C3" (Yamaha) otherwise plays an octave above expectation. |
| 74 | Filter Cutoff | Logarithmic, ~30 Hz to ~16 kHz. |
| 71 | Filter Resonance | ~0.0-6.0 (musical span of AMY's wider Q range). |
| 30 | Filter Env Amount | **Bipolar** EG1 depth coefficient (octave-style), -2.0..+2.0, centered at CC 64 (=0, no effect). Positive opens the filter as the envelope rises; negative inverts it so the envelope closes the filter. |
| 31 | Filter Type | Four buckets across 0-127: LPF24, LPF, BPF, HPF. |
| 32 | Key Scale | Filter `note` tracking coefficient, 0.0-1.0 (0 = none, 1 = full keyboard tracking). |
| 33 | Velocity → Filter | Velocity-to-cutoff depth, **unipolar** 0.0-2.0 octaves (0 = off). Adds a `vel` coefficient to the filter head's `filter_freq`, so harder hits open the filter. |
| 40-43 | VCF A/D/S/R | Filter EG1 envelope. Times ~1-5000 ms (quadratic); sustain 0.0-1.0. |
| 45 | VCF Env Shape | Filter EG1 envelope curve, four buckets: Linear, Normal, True Exp, DX7 (AMY `eg1_type`; default Normal). |
| 73/75/79/72 | VCA A/D/S/R | Amp EG0 envelope (attack 73, decay 75, sustain 79, release 72). Times ~1-5000 ms (quadratic); sustain 0.0-1.0. |
| 46 | VCA Env Shape | Amp EG0 envelope curve, four buckets: Linear, Normal, True Exp, DX7 (AMY `eg0_type`; default Normal). |
| 44 | Velocity → Amp | Velocity-to-amp sensitivity depth, 0-100% (0 = ignore velocity/organ-like, 100% = fully velocity-sensitive; default 30%). Scales the amp `vel` coefficient, lifting soft playing while leaving hard hits unchanged. |
| 76 | LFO Freq | LFO rate, logarithmic ~0.2-20 Hz. |
| 78 | LFO Waveshape | Six-wave buckets (same map as CC 21/25): Sine, Pulse, Saw Down, Saw Up, Triangle, Noise. |
| 77 | LFO → Pitch (Vibrato) | Global vibrato depth on both oscillators, quadratic, 0 to ±12 semitones (1 octave), via each osc's `freq` `mod` coef. Also driven by the mod wheel (CC 1). Held vibrato is smooth; sweeping the depth on a held note has a minor zipper (AMY applies `freq` coefficients immediately — no ramp — so a depth change steps the pitch; see the LFO notes below). |
| 83 | LFO → PWM | Pulse-width modulation depth on Osc A + B duty, 0.0-0.45. |
| 80 | LFO → Filter | Filter-cutoff modulation depth, 0.0-2.0 octaves (matches CC 30 env amount). |
| 81 | LFO → Osc A Amp (Tremolo) | Downward tremolo depth on Osc A, 0.0-0.5. The LFO modulates Osc A's amplitude (via its amp `mod` coef) so the peak never exceeds Osc A's set level and the trough ducks toward silence at full depth (never above level, never below 0). |
| 82 | LFO → Osc B Amp (Tremolo) | Downward tremolo depth on Osc B, 0.0-0.5 (independent of Osc A; same bounded `mod`-coef routing). |
| 28 | Drift Amount | Analog-drift pitch wander depth, **±0-100 cents** (0 = off, default; a full semitone each way at the top for extreme lo-fi). A control-rate smooth-random modulator (tape wow/warble), **not** an AMY oscillator — see the Analog Drift note below. Moves both oscillators together. |
| 29 | Drift Rate | Drift wander speed, logarithmic ~0.05-12 Hz (new random target eased through per second). Default raw 48 ≈ 0.40 Hz. Only audible once Drift Amount > 0. |
| 1 | Mod Wheel → Vibrato | Standard mod-wheel vibrato. Remapped to CC 77, so it sets the same global LFO→pitch depth (reflected in Param Control and captured by presets). |

## Master Output & Effects

Global controls on AMY's master bus (post-voice, one instance each — not per-voice). Per-patch and captured by presets. Effects default to **off** (EQ flat, chorus/echo/reverb level 0), so a patch sounds identical until they are dialed in. Effect "Level" is an **additive wet send** (dry stays at full), not a wet/dry mix, so there is no per-effect "100% wet". Signal chain: voices → EQ → chorus → echo → reverb → Master Level → soft-clip → out.

| CC | Parameter | Behavior |
|----|-----------|----------|
| 84 | Master Level | Output volume (AMY global bus `volume`, post-FX). ~Mute up to +16 dB relative to AMY's quiet default; default **+12 dB**. Stepped in whole dB. AMY soft-clips peaks above ~-1 dBFS. |
| 85 / 86 / 87 | EQ Low / Mid / High | 3-band master EQ, **bipolar** ±12 dB per band (CC 64 = flat). Sent as dB; AMY converts to linear gain. |
| 90 / 91 / 92 | Chorus Level / Depth / Rate | Chorus wet send, modulation depth, and LFO rate (~0.1-10 Hz). `max_delay` fixed at init. |
| 95 / 96 / 97 / 98 | Echo Level / Time / Fbk / Tone | Echo wet send, delay time (~1-740 ms), feedback (0-95%), and feedback-path damping (0-95%). `max_delay_ms` buffer fixed at init. |
| 100 / 101 / 102 / 103 | Reverb Level / Decay / Damp / Xover | Reverb wet send, liveness/decay, HF damping, and damping crossover (~100 Hz-8 kHz, log). |

## Synth & Modulation Notes

A single shared filter processes both oscillators per voice. Each voice has three oscillators: a `SILENT` filter-head (osc 0) chained to Osc A (osc 1) chained to Osc B (osc 2). AMY sums A and B into the silent head's buffer, then applies one filter to that combined signal, so the filter affects Osc A and Osc B equally. Velocity sensitivity and the VCA (amp) envelope live on Osc A/B themselves, so each sounding oscillator fades and self-terminates on note-off rather than relying on the head to silence it (this prevents occasional stuck/over-sustained notes). The head is a unity pass-through that carries only the filter and its EG1 filter envelope. Parameter changes are applied live per-CC, so turning a knob never resets voices or cuts off held notes.

A fourth per-voice oscillator (osc 3) is the LFO. It is named as the `mod_source` of the head, Osc A and Osc B, so AMY keeps it silent and free-running and routes its bipolar output into their `mod` control coefficients: Osc A/B `freq` (vibrato, CC 77 — one global depth shared by both oscillators, also driven by the mod wheel CC 1), Osc A/B `duty` (PWM, CC 83), the filter head's `filter_freq` (CC 80) and Osc A/B `amp` (tremolo, CC 81 for A and CC 82 for B, independent per-oscillator depths). One shared LFO drives all targets, so A and B share its rate (CC 76), waveshape (CC 78) and phase. Vibrato, PWM and filter depths are common to both oscillators; only tremolo depth is per-oscillator.

Two notes on smoothness, both rooted in AMY applying **amplitude** changes with a per-audio-block ramp but **frequency** changes immediately (no ramp, except portamento — which only smooths note changes):

- **Tremolo** (an amp mod) stays smooth *even while you sweep its depth*, because AMY ramps the amp — unlike automating the level CC from the ~15 Hz sketch loop, which stair-steps ("zippers"). AMY's amp `mod` is logarithmic (`amp = level × vel × eg0 × 10**(3 × depth × lfo)`), so the sketch pre-scales each osc's base amp by `10**(-3 × depth)`: the tremolo is strictly *downward*, with the LFO peak landing exactly on the oscillator's set level and the trough ducking toward 0 — never above level.
- **Vibrato** (a freq mod) is smooth *while held*, but sweeping its depth on a sounding note has a minor zipper: AMY applies `freq` coefficients immediately, so each depth step shifts the pitch by (Δdepth × the LFO's current value). This is inherent to AMY (no coefficient slew) and is why vibrato is a single **global** depth rather than per-oscillator: a smooth *per-oscillator* vibrato would need a dedicated vibrato LFO per osc, which AMY's one-`mod_source`-per-oscillator rule can't provide alongside PWM/tremolo. Global vibrato is also the standard mod-wheel form.

LFO depths default to 0, so the LFO is inaudible until a depth knob (or the mod wheel) is moved.

**Analog Drift (CC 28/29).** A separate modulator from the audio LFO, emulating tape wow / analog oscillator drift. AMY's LFO can only produce *periodic* shapes (its `NOISE` wave is full-rate white noise, not a slow wander), so a slow, organic, non-repeating drift is synthesized **at control rate in `loop()`** (`service_drift`) rather than by the audio engine: a smooth-random bipolar "wander" (random targets eased through with a smoothstep, so it never repeats and has no velocity kinks) is folded into both oscillators' `freq` const, moving them together. It re-sends only when the pitch has moved ≥ ~1 cent, keeping each step below the pitch JND so the control-rate updates read as smooth even though AMY applies `freq` changes immediately (no ramp). Depth 0 = off, so it costs nothing and leaves existing patches unchanged; extreme depth *and* rate together is intentionally gritty (larger per-step jumps). Lives in the **LFO** Param Control group as *Drift Amt* / *Drift Rate*, and is captured by presets.

Both oscillators reference 440 Hz (`REF_HZ` in `sketch.py`), so they are unison at the center of the tuning map. To reintroduce a per-oscillator reference (for example an octave-down sub on Osc B), change `REF_HZ` handling in `sketch.py`.

## Rebuild Rule

`sketch.py` is the canonical "last good" implementation. Future enhancements should build on it rather than starting over, and should treat the CC map as fixed unless the user explicitly changes the mapping.

## Deferred Controls

Implemented: the full effects section (EQ / chorus / echo / reverb), master output level, velocity→filter and velocity→amp depth, per-envelope curve shapes, and analog drift (CC 28/29) — all editable on-device (Param Control is now grouped into Osc / VCF / LFO / VCA / FX, with VCF and VCA each led by an **Env** sub-menu) and captured by presets. Deferred: pitch-bend routing. See [MENU.md](MENU.md) and [DISPLAY_MODES.md](DISPLAY_MODES.md).

## Tuning and Wave Maps (Implemented)

The two maps below are the specification the live `sketch.py` implements for the pitch (CC 20/24) and wave (CC 21/25) controls.

### Stepped Oscillator Tuning

The pitch tune controls use a stepped musical map rather than a smooth linear sweep. Both oscillators reference 440 Hz, so the map is unison at center; the same stepped shape applies to each oscillator independently (CC 20 for Osc A, CC 24 for Osc B).

- CC 0-23: two octaves down
- CC 24-39: one octave down
- CC 40-51: fixed perfect fifth down, about -700 cents
- CC 52-59: fine detune from about -35 cents up to -1 cent
- CC 60-68: dead zone at the reference pitch
- CC 69-76: fine detune from about +1 cent up to +35 cents
- CC 77-88: fixed perfect fifth up, about +700 cents
- CC 89-104: one octave up
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
