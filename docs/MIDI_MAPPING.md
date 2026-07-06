# MIDI Mapping

All MIDI is received on **channel 12 only**. AMY maps synth number N to MIDI
channel N, so the engine lives on synth 12 and auto-routes channel-12 note-on/off
directly. `init_synth()` zeroes the voice count of every other synth (including
the firmware's default synth on channel 1), so notes on any channel but 12 stay
silent. Control Changes are handled in `sketches/01_polysynth.py` via
`midi.add_callback(midi_cb)`, filtered to the same channel.

**Notes require `grab_midi_notes=1`.** `init_synth()` sets
`amy.send(synth=SYNTH, grab_midi_notes=1)`. On current firmware `grab_midi_notes=0`
disables **all** MIDI note forwarding to the synth — symptom: notes are silent
while CCs still arrive. See [FIRMWARE_NOTES.md](FIRMWARE_NOTES.md).

**MIDI transport.** Both USB-MIDI (direct USB-C from a computer/DAW — the board
enumerates as a MIDI device) and TRS/DIN MIDI (type A) work.

**Channel is fixed at 12 for now.** An on-device channel picker was prototyped but
**reverted to a placeholder**: AMY hardwires synth N ↔ channel N (this build has no
`midi_channel` reassign keyword), so changing channel required a `machine.reset()`,
which trips a firmware audio-engine bug. See [FIRMWARE_NOTES.md](FIRMWARE_NOTES.md);
revisit once fixed or via a live, no-reset approach.

The authoritative parameter map (which CC drives which synth parameter, value
ranges, stepped tuning, and wave buckets) lives in
[CC_MAPPING.md](CC_MAPPING.md). This file describes the role of each physical
control surface.

## Control Surface Assignment

### Arturia Keystep Pro

- **Keys**: Polyphonic note input (channel 12) → 6-voice AMY synth
- **Velocity**: Drives note-on strength (velocity sensitivity is applied by the
  filter-head oscillator in each voice)
- **Mod Wheel / Pitch Bend**: Not yet mapped

### Squarp Hermod+

- **Sequencer Output**: Melodic sequences on channel 12
- **Clock**: Sync timing reference (no clock-driven behavior in the sketch yet)
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
- CC value ranges are implementation choices in `sketches/01_polysynth.py` and can be retuned
  without changing the frozen CC assignments.
- Onboard OLED + encoder navigation is implemented (see [MENU.md](MENU.md) and
  [DISPLAY_MODES.md](DISPLAY_MODES.md)).
- Not yet implemented: effects (reverb / echo / chorus), mod wheel / pitch bend
  routing, presets (Stage 4), and an on-encoder CC editor (next up — edit CCs
  directly from the encoder without the control surface).
