# Architecture

## High-Level System Design

### MIDI Control Flow

```
Controllers → MIDI → AMYboard → Audio Output
  ├── Keystep Pro (Keyboard/Gate)      → MIDI Channel 1 (Note On/Off)
  ├── Hermod+ (Sequencer/Clock)        → MIDI Channel 1 (Note/Clock/CV)
  └── Oxi One 16 (Parameters)          → MIDI Channel 1 (CC 20-70)
```

### System Architecture

**AMYboard Hardware**
- ESP32-S3 microcontroller running MicroPython
- AMY synthesizer engine (real-time synthesis)
- USB-C for MIDI I/O and power
- Persistent storage at `/user/` for configuration

**Control Mapping**
- E16 pages 4, 8, 12 send MIDI CCs on channel 1
- CCs mapped to synth parameters via `sketch.py`
- Pages 1-3 reserved for MFT (Keystep Pro) control
- Settings saved to persistent storage on AMYboard

### Core Components

**sketch.py (Main Control Loop)**
- MIDI CC listener on channel 1
- Parameter mapping engine
- State persistence to `/user/amyboard_state.json`
- Synth parameter application via `amy` module

**OXI E16 Configuration**
- `e16-config/amyboard.json`: Source definition (pages 4, 8, 12)
- `e16-config/amyboard.oxie16`: Compiled scene file
- CC assignments documented in `docs/CC_MAPPING.md`

**AMY Synthesizer**
- Dual oscillators (A/B) with independent waveforms, pitch, duty cycle
- Multi-mode filter (LP/BP/HP) with ADSR modulation
- Dual ADSR envelopes (VCF + VCA)
- LFO with multiple targets (pitch, PWM, filter)
- Global effects (reverb, echo, chorus)

### Control Sections (E16 Pages)

**Page 4: Oscillators + Filter** (CCs 20-32)
- Osc A: pitch (440 Hz default), waveform, duty cycle, level
- Osc B: pitch (220 Hz default), waveform, duty cycle, level
- Filter: cutoff, resonance, envelope modulation, type, keyboard scaling

**Page 8: Modulators** (CCs 40-54)
- VCF ADSR: attack, decay, sustain, release
- VCA ADSR: attack, decay, sustain, release
- LFO: frequency, depth, waveform
- LFO Modulation: pitch amount, PWM amount, filter amount

**Page 12: Effects** (CCs 60-70)
- Echo: level, delay time, feedback
- Reverb: level, room size, dampening
- Chorus: level, frequency, depth

### Data Persistence

Settings are saved to `/user/amyboard_state.json` after each MIDI CC change.
On power-up, `sketch.py` loads this file and applies all settings.

### Future Enhancements

- OLED display (Adafruit 128x128) for parameter visualization
- I2C joystick for onboard navigation and preset management
- Preset management system with multiple save slots
- LFO modulation matrix expansion
- CV I/O integration for Eurorack modulation sources

## Development Approach

Prioritizes clarity and simplicity:
- CC mappings are documented and easily adjustable
- Default values are musically sensible (440 Hz / 220 Hz references)
- State management is explicit and persisted
- New parameters can be added by updating JSON + `sketch.py`
