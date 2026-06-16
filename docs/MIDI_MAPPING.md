# MIDI Mapping

All MIDI is received on **channel 1**. AMY auto-routes channel-1 note-on/off to
synth 1, so the keyboard and sequencer play the polyphonic engine directly.
Control Changes are handled in `sketch.py` via `midi.add_callback(midi_cb)`.

The authoritative parameter map (which CC drives which synth parameter, value
ranges, stepped tuning, and wave buckets) lives in
[CC_MAPPING.md](CC_MAPPING.md). This file describes the role of each physical
control surface.

## Control Surface Assignment

### Arturia Keystep Pro

- **Keys**: Polyphonic note input (channel 1) → 6-voice AMY synth
- **Velocity**: Drives note-on strength (velocity sensitivity is applied by the
  filter-head oscillator in each voice)
- **Mod Wheel / Pitch Bend**: Not yet mapped

### Squarp Hermod+

- **Sequencer Output**: Melodic sequences on channel 1
- **Clock**: Sync timing reference (no clock-driven behavior in `sketch.py` yet)
- **CV**: External CV can reach the board's CV inputs — CV1 is 1V/oct mono pitch
  and CV2 is a gate, polled in `loop()`

### Oxi e16

- **Encoders**: Real-time parameter control via the frozen CC range
  (20-32, 40-47, 71, 74, 76-80). See [CC_MAPPING.md](CC_MAPPING.md) for the full map.
- **Buttons / scene pages**: Layout intent is documented under "Controller
  Pages" in [CC_MAPPING.md](CC_MAPPING.md)

## Implementation Notes

- Each CC updates only its own parameter live, so moving a control never resets
  voices or cuts held notes.
- CC value ranges are implementation choices in `sketch.py` and can be retuned
  without changing the frozen CC assignments.
- Not yet implemented: effects (reverb / echo / chorus), mod
  wheel / pitch bend routing, and onboard OLED/encoder navigation.
