# AMYboard

Synthesizer control code for an AMYboard in a Eurorack environment.

## Overview

This project provides instrument/control code for an [AMYboard](https://github.com/shorepine/tulipcc/blob/main/docs/amyboard/README.md) synthesizer board, enabling it to function as a polyphonic synthesizer engine within a Eurorack system.

## Current Status

- `sketches/01_polysynth.py` is the canonical instrument implementation: a
  2-oscillator (A/B) analog-style synth with 6-voice polyphony, a shared
  resonant filter with VCF envelope and key tracking, a VCA envelope, and a
  per-voice LFO routed to pitch, PWM and filter cutoff. It implements the frozen
  CC map and updates each parameter live (no voice reset, so held notes are never
  cut off).
- All MIDI is received on **channel 12 only**: AMY maps synth number N to MIDI
  channel N, so the instrument lives on synth 12 and auto-routes channel-12
  notes. Because the firmware allocates a default instrument on synth 1 (channel
  1) at boot, `init_synth()` zeroes the voice count of every synth except 12 — a
  synth with no voices cannot sound, so channels other than 12 stay silent.
  Control Changes are handled via `midi.add_callback(midi_cb)`. CV1 provides
  1V/oct monophonic pitch and CV2 a gate.
- The frozen baseline for MIDI CC assignments lives in `docs/CC_MAPPING.md`.
- Additional synth graph references live in `amy_patch_examples/`, notably
  `sketch_5osc_analog.py` and its explicit Python-defined synth graph.

## Sketches & Deployment

- **Canonical sketches** live in `sketches/`. Root `sketch.py` is a scratch
  staging copy that gets overwritten freely while experimenting; promote a
  working sketch into `sketches/` once it is solid.
- **`wrapper_sketch.py`** is the global launcher: it is deployed to the firmware
  boot file (`/user/current/sketch.py`) and, on each cold boot, shows a menu or
  runs a chosen sketch. A long-press on the encoder returns any sketch to the
  menu.
- **Deploy to internal flash, not the SD card.** The board's FatFs can read but
  not write its large exFAT SD card, so sketches are deployed to
  `/user/sketches` (always writable over serial), which the launcher loads from
  first. The deploy tooling — `deploy_auto.py`, `verify.py`, `board_serial.py` —
  sends the file over the MicroPython REPL and verifies an exact byte-for-byte
  readback. See `DEPLOYMENT_COMMAND.txt`. Typical loop:

  ```
  python deploy_auto.py --sketch sketches/01_polysynth.py            # deploy + verify
  python deploy_auto.py --sketch sketches/01_polysynth.py --activate # deploy + boot into it
  ```

## Control Setup

- **Keyboard**: Arturia Keystep Pro
- **Sequencer**: Squarp Hermod+
- **Parameter Controller**: Oxi e16
- **Interface**: MIDI (channel 12)

## Planned Hardware Enhancements

- Adafruit 128x128 OLED display
- M5Stack I2C joystick

## Documentation

- [Development Guidelines](docs/AGENTS.md) - Agent collaboration rules and architectural guidance
- [Architecture](docs/ARCHITECTURE.md) - High-level system design
- [CC Mapping](docs/CC_MAPPING.md) - Frozen CC baseline and live parameter behavior
- [Display Modes](docs/DISPLAY_MODES.md) - Pluggable OLED display modes (CC Monitor, etc.)
- [MIDI Mapping](docs/MIDI_MAPPING.md) - Control surface roles and channel assignment
- [E16 Setup](docs/E16_SETUP.md) - Oxi e16 configuration and deployment notes

## Resources

- [AMYboard Documentation](https://github.com/shorepine/tulipcc/blob/main/docs/amyboard/README.md)
- [AMY Language & Hardware](https://github.com/shorepine/amy)
