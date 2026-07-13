# CV Input Attempt — Findings & Postmortem

**Goal:** make the AMYboard's CV inputs usable as a 1V/oct pitch + gate input for
the polysynth sketch (`sketches/01_polysynth.py`) — a headline use case for a
Eurorack module.

**Outcome:** removed for now. After extensive iteration we could not make CV pitch
reliably in-tune enough to be musically useful on this hardware. The gate path
worked well; the *pitch* path is the problem. This documents what we learned so a
future attempt starts ahead instead of re-walking the whole path.

---

## Hardware / firmware API

- **2 CV inputs**, 3.5mm jacks, **ADS1015 12-bit ADC** @ I2C `0x48`, ±10V range.
- **2 CV outputs**, **GP8413 12-bit DAC** @ I2C `0x58`, ~±10V.
- `amyboard.cv_in(ch)` → cached volts (a firmware FreeRTOS task polls the ADC and
  caches the value ~once per audio block, ≈5.8ms — `cv_in` itself does no I2C).
- `amyboard.cv_out(v, ch)` → drive the DAC (volts). `amyboard.ads1015_raw(ch)` →
  raw ADC via a separate Python I2C read.

## The core problem: the input is too coarse, nonlinear, and miscalibrated for clean pitch

1. **Resolution.** 12-bit over ~20V ≈ **6 cents/step**. The tulipcc docs themselves
   warn it's "sometimes too coarse for fine vibrato via CV pitch modulation." That's
   the noise floor.
2. **Systematic miscalibration (firmware).** The firmware raw→volts conversion
   (`tulip/amyboard/amyboard_support.c`, `cv_read_task`) uses calibration constants
   whose *comments* say ±5V but whose *formula* maps them to −10…0V. Net effect on
   our rig: a large consistent **offset (~360 cents)** and a **scale error
   (~20 cents/oct)**. Because it's in the firmware conversion, it's likely the same
   on every board with that firmware (not per-unit) — but there is **no runtime
   firmware calibration hook** (`cv_cal()` writes `cal.csv`, but nothing reads it).
3. **Nonlinearity (hardware).** Even after a linear offset+scale calibration that
   pins two points in tune, the **middle of the range bows flat** — ADC integral
   nonlinearity + the analog front end. A linear (offset+gain) calibration can only
   fit a *straight line*; it cannot follow a curve.
4. **Missing codes / DNL.** Around specific input voltages (~0.6V and ~3.3V on our
   rig) the reading jumps or skips a code — first seen as "wrong note at one spot,"
   later as localized flat octaves.

## AMY's two native CV-pitch modes (both tried; each has a catch)

AMY (the audio engine) can do CV pitch two ways, both **in the audio thread** (no
Python polling). This is the right layer — do NOT poll `cv_in` in `loop()`.

**A. Continuous tracking** — put `ext0` on the oscillator freq coef:
`freq={'const':…, 'note':1, 'ext0':1}`. AMY reads CV0 every audio block:
`freq = 440·2^(note_contribution + ext0·CV0)`, so `ext0=1.0` is 1V/oct.
- ✅ tracks accurately; no dependence on gate timing.
- ❌ reads the noisy ADC continuously, so ADC noise/steps jitter the *sustained*
  pitch → audible shimmer / "digital aliasing" on held notes.

**B. Sample-at-gate** — `cv_trigger` with the optional pitch args samples the pitch
once at the gate edge and holds it:
`cv_trigger='<gate_cv>,<v_trig>,<v_reset>,<pitch_cv>,<scale>,<offset>,i<synth>l<vel>n%v'`.
Per `amy/src/cv_trigger.c`: `note = midi_note_for_logfreq(offset + scale·CV)`, where
logfreq is **octaves relative to note 69 (A4/440Hz)** — so `scale` is octaves-per-volt
(1.0 = 1V/oct) and `offset` is `(base_note−69)/12`.
- ✅ pitch is stable for the whole note (no continuous-noise shimmer).
- ❌ samples once at the gate; if the source's **gate leads the pitch CV** (common
  with some MIDI→CV converters) it latches the *previous* note's pitch → notes come
  out wrong / untunable.

**Gate** (both modes): `cv_trigger` fires note-on/off wire commands when the gate CV
crosses a threshold, in the audio thread — precise, no dropped gates.
Note-on `i<synth>l<vel>n<note>`, note-off `i<synth>l0`. Use a hysteresis gap between
the on- and off-thresholds. This part worked reliably.

## Calibration

- Two linear knobs: **offset** (shift the note reference — a *fractional* MIDI note
  works, AMY is microtonal) and **scale** (V/oct, via `ext0` in mode A or the
  `scale` param in mode B).
- They **interact** (scale pivots at CV0 = 0V, not at the base note), so calibration
  is iterative, like a hardware VCO's scale+offset trims: set offset at a low note,
  set scale at a high note, repeat.
- Calibration is genuinely required (firmware miscalibration) but can only make the
  response a straight line — it **cannot remove the nonlinear bow** (problem #3).
  To spread the residual, calibrate at ~⅓ and ~⅔ of the used range rather than the
  extremes, minimizing worst-case deviation.

## Dead ends worth NOT repeating

- **Python poll + quantize to MIDI note** (`loop()` reads `cv_in`, rounds to nearest
  note): lossy, laggy, flickers between adjacent notes on noise, drops/gobbles gates.
  Rounding a noisy signal to semitones turns small errors into *wrong notes*. Go
  straight to AMY's native path instead.
- **Median filter / hysteresis / sample-and-hold + settle-delay in Python**:
  band-aids on the poll approach; each traded one artifact for another
  (flicker ↔ lag ↔ dropped gates). The native path makes them unnecessary.
- **CV-output mirror** (echo `cv_in`→`cv_out` every loop): the per-loop DAC writes
  share the I2C bus with the ADC read that feeds pitch tracking; they contend and
  glitch the pitch. If re-added, throttle hard (≥50ms) or decouple.

## Recommendation for a future attempt

- Start from AMY's **native path** (mode A continuous is simplest and tracks best),
  never Python polling.
- Expect to expose **offset + scale** calibration to the user (per-rig: the source /
  MIDI→CV converter's own calibration matters too).
- The floor is ~6 cents + a few cents of nonlinear bow — fine for lo-fi / analog-
  flavored pitch, **not** for precise chromatic playing. Precise tuning would need a
  nonlinear correction table, which requires custom firmware or a Python read path
  (reintroducing the polling problems).
- Consider whether **gate-only** (rock-solid) with MIDI/fixed pitch, or **CV as
  modulation** (filter/amp, where cents don't matter), is a better use of the CV
  input than 1V/oct pitch.

## Source references

- Firmware CV read/convert: tulipcc `tulip/amyboard/amyboard_support.c` (`cv_read_task`).
- Firmware CV Python API: tulipcc `tulip/shared/amyboard-py/amyboard.py`
  (`cv_in` / `cv_out` / `ads1015_raw` / `cv_cal`).
- AMY `cv_trigger`: amy `src/cv_trigger.c`, `src/parse.c`; docs
  `docs/amyboard/modular.md`, `src/…/synth.md` (CtrlCoefficients, logfreq).
