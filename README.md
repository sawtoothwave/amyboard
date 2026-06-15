# AMYboard

Synthesizer control code for an AMYboard in a Eurorack environment.

## Overview

This project provides instrument/control code for an [AMYboard](https://github.com/shorepine/tulipcc/blob/main/docs/amyboard/README.md) synthesizer board, enabling it to function as a polyphonic synthesizer engine within a Eurorack system.

## Current Status

- The current `sketch.py` should be treated as discarded design work, not as a stable synth implementation.
- The serial deploy and verification workflow is the part of the repo currently considered reliable:
	- `deploy_auto.py`
	- `verify.py`
	- `board_serial.py`
- The frozen rebuild baseline for MIDI CC assignments lives in `docs/CC_MAPPING.md`.
- The most useful architectural reference for a future rebuild is `amy_patch_examples/sketch_5osc_analog.py`, specifically its explicit Python-defined synth graph.

## Control Setup

- **Keyboard**: Arturia Keystep Pro
- **Sequencer**: Squarp Hermod+
- **Parameter Controller**: Oxi One 16
- **Interface**: MIDI

## Planned Hardware Enhancements

- Adafruit 128x128 OLED display
- M5Stack I2C joystick

## Documentation

- [Development Guidelines](docs/AGENTS.md) - Agent collaboration rules and architectural guidance
- [Architecture](docs/ARCHITECTURE.md) - High-level system design
- [CC Mapping](docs/CC_MAPPING.md) - Frozen rebuild baseline for controller assignments

## Resources

- [AMYboard Documentation](https://github.com/shorepine/tulipcc/blob/main/docs/amyboard/README.md)
- [AMY Language & Hardware](https://github.com/shorepine/amy)
