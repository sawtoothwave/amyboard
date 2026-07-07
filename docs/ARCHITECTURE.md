# Architecture

## High-Level System Design

### MIDI Control Flow

```
Controllers → MIDI → AMYboard → Audio Output
  ├── Keystep Pro (Keyboard/Gate)      → MIDI Channel 12 (Note On/Off)
  ├── Hermod+ (Sequencer/Clock)        → MIDI Channel 12 (Note/Clock/CV)
  └── Oxi e16 (Parameters)             → MIDI Channel 12 (CC 20-32, 40-47, 71, 74, 76-80)
```

### System Architecture

**AMYboard Hardware**
- ESP32-S3 microcontroller running MicroPython
- AMY synthesizer engine (real-time synthesis)
- USB-C for MIDI I/O and power
- Persistent storage at `/user/` for configuration

**Control Mapping**
- E16 pages 4, 8, 12 send MIDI CCs on channel 12
- The frozen rebuild baseline for CC assignments lives in `docs/CC_MAPPING.md`
- Pages 1-3 reserved for MFT (Keystep Pro) control

### Core Components

**sketches/01_polysynth.py (Main Control Loop)**
- The canonical "last good" instrument implementation; future enhancements build on it. (Root `sketch.py` is a scratch staging copy used while deploying/experimenting.)
- A 2-oscillator (A/B) analog-style synth with 6-voice polyphony, matching the frozen CC map in `docs/CC_MAPPING.md`.
- Per-voice oscillator graph: a `SILENT` filter-head (osc 0) chained to Osc A (osc 1) chained to Osc B (osc 2), plus a silent per-voice LFO (osc 3) used as the `mod_source` for the head, Osc A and Osc B. AMY sums A and B into the silent head, then applies one shared filter and the VCA envelope to that sum, so the filter affects both oscillators equally.
- Osc A/B have independent stepped tuning (CC 20/24), six-wave buckets excluding wavetable/PCM/ALGO (CC 21/25), duty (CC 22/26) and mix level (CC 23/27). Both oscillators reference 440 Hz (unison at center).
- Shared resonant filter with selectable type (CC 31: LPF24/LPF/BPF/HPF), cutoff (CC 74), resonance (CC 71), envelope amount (CC 30), key tracking (CC 32), plus VCF (CC 40-43) and VCA (CC 44-47) ADSR envelopes.
- Per-voice LFO with rate (CC 76) and six-wave waveshape (CC 78), routed to pitch/vibrato (CC 77), pulse-width (CC 79) and filter cutoff (CC 80) via each target's `mod` coefficient; depths default to 0 so the LFO is silent until a depth knob moves.
- MIDI channel-12 notes are auto-routed to synth 12 by AMY (synth number N == MIDI channel N); CCs are handled via `midi.add_callback(midi_cb)`, filtered to channel 12. CV1 provides 1V/oct monophonic pitch and CV2 a gate, polled from `loop()`.
- To stay channel-12-only, `init_synth()` zeroes the voice count of every synth except 12, removing the default instrument the firmware allocates on synth 1 (channel 1); a voiceless synth cannot sound. It also sets `grab_midi_notes=1` on synth 12 — required for note forwarding on current firmware (see [FIRMWARE_NOTES.md](FIRMWARE_NOTES.md)). Channel is fixed at 12 for now (the on-device picker is deferred — see [MIDI_MAPPING.md](MIDI_MAPPING.md)).
- Each CC updates only its parameter live, so turning a knob never resets voices or cuts off held notes.
- Display is driven by a pluggable, sketch-owned display mode selectable from the on-device menu (see [DISPLAY_MODES.md](DISPLAY_MODES.md)); the selection persists to `/user/polysynth_settings.json`.
- **Param Control**: an on-device editor (polysynth menu → Param Control) exposes all 26 synth parameters as numbered 0-127 sliders that drive the same CC path as the E16 knobs — turning adjusts live with encoder acceleration, double-click resets to the patch default, and an open editor reflects incoming MIDI for that CC. See [MENU.md](MENU.md).
- **Self-contained**: the sketch reads its own encoder when run without the wrapper, so it works as a single shareable file (full menu + parameter editor) with or without the launcher.

**wrapper_sketch.py (Global launcher)**
- The permanent boot program (deployed to `/user/current/sketch.py`); it shows a global menu or runs a chosen sketch and drives that sketch's `loop()`.
- The global menu is an **in-memory overlay** over the running sketch: `Resume` returns instantly with no audio dropout; only `Load Sketch` resets the board (required to tear down the previous sketch's MIDI callback).
- The launcher is the encoder reader **when it is present** and feeds each sketch abstract input (`launcher.delta/.click/.back`) while reading `launcher.menu_depth`. This is what lets one universal gesture — click in / hold out / turn scroll — span both the launcher and each sketch's own menu. A sketch run **without** the launcher (copied on as its own boot file) can read the encoder itself instead — the polysynth does, so it stays fully usable as a single file. The two never read the encoder at once. Full details in [MENU.md](MENU.md).

**OXI E16 Configuration**
- `e16-config/amyboard.json`: Source definition (pages 4, 8, 12)
- `e16-config/amyboard.oxie16`: Compiled scene file
- CC assignments documented in `docs/CC_MAPPING.md`

**Deployment / Verification**
- Sketches deploy to internal flash (`/user/sketches`), not the SD card: the board's FatFs can read but not write the large exFAT card. The wrapper launcher (`wrapper_sketch.py`, deployed to the boot file `/user/current/sketch.py`) loads from `/user/sketches` first.
- `board_serial.py`: direct serial REPL session helper
- `deploy_auto.py`: deploy a local file to the board (default `/user/sketches/<name>`) and verify an exact readback; `--activate` sets `launcher_state` and reboots into the sketch
- `verify.py`: read back the active board sketch and compare it to local source

### Control Sections (E16 Pages)

The intended page groupings and frozen CC assignments are documented in `docs/CC_MAPPING.md`.

### Future Enhancements

- Presets: save/load full patch state (Stage 4; `_capture_patch()`/`_apply_patch()` helpers already in place)
- Re-enable the on-device MIDI-channel picker once the firmware `machine.reset()` audio bug is fixed, or via a live no-reset approach
- CV I/O integration for Eurorack modulation sources
- Oscilloscope display mode (needs an AMY output-sample tap)

## Development Approach

Prioritizes clarity and simplicity:
- Keep the CC baseline fixed unless intentionally revised
- Treat `sketches/01_polysynth.py` as the canonical baseline and extend it rather than rebuilding from scratch
- Prefer explicit Python-defined synth graphs over hidden autogenerated patch state
- Keep deployment verification separate from synth experimentation
