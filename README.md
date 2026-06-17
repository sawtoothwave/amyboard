# AMYboard

Synthesizer control code for an AMYboard in a Eurorack environment.

## Overview

This project provides instrument/control code for an [AMYboard](https://github.com/shorepine/tulipcc/blob/main/docs/amyboard/README.md) synthesizer board, enabling it to function as a polyphonic synthesizer engine within a Eurorack system.

## Current Status

- `sketch.py` is the canonical instrument implementation: a 2-oscillator (A/B)
  analog-style synth with 6-voice polyphony, a shared resonant filter with VCF
  envelope and key tracking, a VCA envelope, and a per-voice LFO routed to
  pitch, PWM and filter cutoff. It implements the frozen CC map and updates each
  parameter live (no voice reset, so held notes are never cut off).
- All MIDI is received on **channel 12**: AMY auto-routes channel-12 notes to
  synth 12, and Control Changes are handled via `midi.add_callback(midi_cb)`.
  CV1 provides 1V/oct monophonic pitch and CV2 a gate.
- The frozen baseline for MIDI CC assignments lives in `docs/CC_MAPPING.md`.
- The serial deploy and verification workflow is reliable:
	- `deploy_auto.py`
	- `verify.py`
	- `board_serial.py`
- Additional synth graph references live in `amy_patch_examples/`, notably
  `sketch_5osc_analog.py` and its explicit Python-defined synth graph.

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
- [MIDI Mapping](docs/MIDI_MAPPING.md) - Control surface roles and channel assignment
- [E16 Setup](docs/E16_SETUP.md) - Oxi e16 configuration and deployment notes

## Resources

- [AMYboard Documentation](https://github.com/shorepine/tulipcc/blob/main/docs/amyboard/README.md)
- [AMY Language & Hardware](https://github.com/shorepine/amy)
