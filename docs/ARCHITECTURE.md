# Architecture

## High-Level System Design

### MIDI Control Flow

```
Controllers → MIDI → AMYboard → Audio Output
  ├── Keystep Pro (Keyboard/Gate)      → MIDI Channel 12 (Note On/Off)
  ├── Hermod+ (Sequencer/Clock)        → MIDI Channel 12 (Note/Clock/CV)
  └── Oxi e16 (Parameters)             → MIDI Channel 12 (CC 1, 20-34, 40-46, 71-87, 90-103)
```

### System Architecture

**AMYboard Hardware**
- ESP32-S3 microcontroller running MicroPython
- AMY synthesizer engine (real-time synthesis)
- USB-C for MIDI I/O and power
- Persistent storage at `/user/` for configuration

**Control Mapping**
- E16 pages 1-5 (OSC / VCF / LFO / VCA / FX) send MIDI CCs on channel 12, one page
  per Arctor param group
- The frozen rebuild baseline for CC assignments lives in `docs/CC_MAPPING.md`

### Core Components

**sketches/arctor.py (Main Control Loop)**
- The canonical "last good" instrument implementation; future enhancements build on it. (Root `sketch.py` is a scratch staging copy used while deploying/experimenting.)
- A 2-oscillator (A/B) analog-style synth with 6-voice polyphony, matching the CC map in [docs/CC_MAPPING.md](CC_MAPPING.md).
- Per-voice oscillator graph: a `SILENT` filter-head (osc 0) chained to Osc A (osc 1) chained to Osc B (osc 2), plus a silent per-voice LFO (osc 3) used as the `mod_source` for the head, Osc A and Osc B. AMY sums A and B into the silent head, then applies one shared filter and the VCA envelope to that sum, so the filter affects both oscillators equally.
- Osc A/B have independent stepped tuning (CC 20/24), six-wave buckets excluding wavetable/PCM/ALGO (CC 21/25), duty (CC 22/26) and mix level (CC 23/27). Both oscillators reference 440 Hz (unison at center).
- Shared resonant filter with selectable type (CC 31: LPF24/LPF/BPF/HPF), cutoff (CC 74), resonance (CC 71), envelope amount (CC 30), key tracking (CC 32), plus VCF (CC 40-43) and VCA (CC 73/75/79/72, the AMYboard/std-MIDI ADSR defaults) envelopes.
- Per-voice LFO with rate (CC 76) and six-wave waveshape (CC 78), routed to global pitch/vibrato (CC 77, also driven by the mod wheel CC 1), pulse-width (CC 83), filter cutoff (CC 80) and per-oscillator amplitude/tremolo (CC 81 osc A / 82 osc B) via each target's `mod` coefficient. Vibrato, PWM and filter depths are shared by both oscillators; only tremolo depth is per-oscillator. Vibrato is global (the standard mod-wheel form) because AMY's one-`mod_source`-per-oscillator rule can't give a smooth per-osc vibrato alongside PWM/tremolo — see [CC_MAPPING.md](CC_MAPPING.md). Depths default to 0 so the LFO is silent until a depth knob (or the mod wheel) moves.
- MIDI channel-12 notes are auto-routed to synth 12 by AMY (synth number N == MIDI channel N); CCs are handled via `midi.add_callback(midi_cb)`, filtered to channel 12. CV1 provides 1V/oct monophonic pitch and CV2 a gate, polled from `loop()`.
- To stay channel-12-only, `init_synth()` zeroes the voice count of every synth except 12, removing the default instrument the firmware allocates on synth 1 (channel 1); a voiceless synth cannot sound. It also sets `grab_midi_notes=1` on synth 12 — required for note forwarding on current firmware (see [FIRMWARE_NOTES.md](FIRMWARE_NOTES.md)). Channel is fixed at 12 for now (the on-device picker is deferred — see [MIDI_MAPPING.md](MIDI_MAPPING.md)).
- Each CC updates only its parameter live, so turning a knob never resets voices or cuts off held notes.
- Display is driven by a pluggable, sketch-owned display mode selectable from the on-device menu (see [DISPLAY_MODES.md](DISPLAY_MODES.md)); the selection persists to `/user/arctor_settings.json`.
- **Param Control**: an on-device editor (Arctor menu → Param Control) exposes every synth parameter as a cell in a per-group **knob grid** (Osc / VCF / LFO / VCA / FX), 4 columns wide and cut into labelled sections. A click selects a cell and turning adjusts it live with encoder acceleration, driving the same CC path as the E16 knobs; double-click resets to the patch default, hold reverts; a selected cell reflects incoming MIDI for that CC. See [MENU.md](MENU.md) and [CC_MAPPING.md](CC_MAPPING.md).
- **Self-contained**: the sketch reads its own encoder when run without the wrapper, so it works as a single shareable file (full menu + parameter editor) with or without the launcher.

**wrapper_sketch.py (Global launcher)**
- The permanent boot program (deployed to `/user/current/sketch.py`); it shows a global menu or runs a chosen sketch and drives that sketch's `loop()`.
- The global menu is an **in-memory overlay** over the running sketch: `Resume` returns instantly with no audio dropout; only `Load Sketch` resets the board (required to tear down the previous sketch's MIDI callback).
- The launcher is the encoder reader **when it is present** and feeds each sketch abstract input (`launcher.delta/.click/.back`) while reading `launcher.menu_depth`. This is what lets one universal gesture — click in / hold out / turn scroll — span both the launcher and each sketch's own menu. A sketch run **without** the launcher (copied on as its own boot file) can read the encoder itself instead — Arctor does, so it stays fully usable as a single file. The two never read the encoder at once. Full details in [MENU.md](MENU.md).

**OXI E16 Configuration**
- `e16-config/arctor.json`: Source definition (pages 1-5; every `PARAMS` row)
- `e16 templates/arctor.oxie16`: Compiled scene file (built from `arctor.json`)
- CC assignments documented in `docs/CC_MAPPING.md`; page/knob layout in `docs/E16_SETUP.md`

**Deployment / Verification**
- Sketches deploy to internal flash (`/user/sketches`), not the SD card: the board's FatFs can read but not write the large exFAT card. The wrapper launcher (`wrapper_sketch.py`, deployed to the boot file `/user/current/sketch.py`) loads from `/user/sketches` first.
- `board_serial.py`: direct serial REPL session helper
- `deploy_auto.py`: deploy a local file to the board (`/user/sketches/<name>`) and verify it by comparing sha256 of the board copy against the local file; `--activate` sets `launcher_state` and reboots into the sketch. `--sketch` is required — there is no default, because the board hosts many sketches and guessing one would silently deploy the wrong thing.
- `verify.py`: read `/user/current/sketch.py` (the boot file — i.e. the **launcher**, not a sketch) off the board and compare it to a local file; normally `--sketch wrapper_sketch.py`. Sketches themselves are verified by `deploy_auto.py` at deploy time.

### Control Sections (E16 Pages)

The CC map is in [docs/CC_MAPPING.md](CC_MAPPING.md); the e16 page/knob layout is in [docs/E16_SETUP.md](E16_SETUP.md).

### Future Enhancements

- Re-enable the on-device MIDI-channel picker once the firmware `machine.reset()` audio bug is fixed, or via a live no-reset approach
- CV I/O integration for Eurorack modulation sources
- Oscilloscope display mode (needs an AMY output-sample tap; the placeholder mode was removed 2026-07-31)

## Development Approach

Prioritizes clarity and simplicity:
- Keep the CC baseline fixed unless intentionally revised
- Treat `sketches/arctor.py` as the canonical baseline and extend it rather than rebuilding from scratch
- Prefer explicit Python-defined synth graphs over hidden autogenerated patch state
- Keep deployment verification separate from synth experimentation
