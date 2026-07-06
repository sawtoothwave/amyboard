# Firmware notes & gotchas

Hard-won notes about the AMYboard firmware, current as of the latest
`amyboard-full-AMYBOARD.bin` build. Several of these caused long debugging
sessions; read before assuming a bug is in this repo's code.

## Correct firmware & flashing

- Image: `amyboard-full-AMYBOARD.bin` from the shorepine/tulipcc `amyboard`
  release (<https://github.com/shorepine/tulipcc/releases/tag/amyboard>).
- Browser flasher: <https://amyboard.com/editor>. Or esptool:
  `esptool.py write_flash 0x0 amyboard-full-AMYBOARD.bin`.
- **A reflash WIPES `/user`** — the launcher, `/user/sketches`, and
  `polysynth_settings.json` are all erased. Redeploy afterward (launcher to
  `/user/current/sketch.py`, then the sketch with `--activate`).

## Testing whether audio works

Use the official hardware test — a **free high oscillator, velocity-triggered**:

```python
import amy
amy.send(osc=110, wave=amy.SINE, freq=440, vel=1)   # a 440 Hz sine = audio OK
```

Do **not** test with `osc=0` (the default Juno-6 boot synth owns the low
oscillators — raw sends there conflict and produce *clicks*, not a tone), and use
`vel=` (gates the envelope), not bare `amp=`. The board also boots Juno-6 on
**MIDI channel 1** by default, so a channel mismatch reads as silence.

## `grab_midi_notes` — required for MIDI notes

`amy.send(synth=N, grab_midi_notes=0)` means the synth receives **no** MIDI notes
(forwarding disabled) on the current firmware. Symptom: notes are silent while CCs
(handled by our own `midi.add_callback`) still work. Use `grab_midi_notes=1` so
AMY routes the synth's channel notes to it. (Older firmware appeared to read `0`
as "own channel only," which is why older code used `0`.)

## Audio engine can come up dead after `machine.reset()` (firmware bug)

Intermittently, after a soft reset issued from a running sketch, the AMY audio
engine comes back **dead — slow clicking, no continuous render** — and only
another reboot restarts it. This is a firmware bug (reported upstream), not our
code. Consequences for this repo:

- **Don't design features around `machine.reset()`.** The on-device MIDI-channel
  picker was reverted to a placeholder for exactly this reason: applying a channel
  change needed a reset (AMY hardwires synth N ↔ MIDI channel N, and this build
  has **no `midi_channel` send keyword** to reassign a synth's channel live), and
  that reset kept killing audio. See `docs/MIDI_MAPPING.md`.
- A dead engine makes *any* subsequent parameter change (e.g. LFO rate) read as
  "audio cut out" — it's the engine, not the parameter.
- `amy.millis()` keeps advancing even when the render is dead, so it is **not** a
  usable liveness check.

## Do NOT call `amyboard.start_amy()` from a boot sketch

Calling `amyboard.start_amy()` from the REPL after boot is safe (idempotent). But
calling it from a boot sketch's **module top-level** causes a **hard chip fault →
reset loop** (an uncatchable panic — `try/except` does not help). During the fast
crash-loop, USB serial may not even enumerate, so it can't be recovered over
serial. Recover via **safe boot** (below) or reflash. We do **not** force-start
the engine at boot for this reason.

## Recovery from a boot loop

If a bad `sketch.py` crashes at boot: **hold the BOOT button while powering up.**
That skips running `sketch.py` (and runs a hardware self-test), giving a REPL.
Then delete/fix the boot file, e.g.:

```
mpremote resume fs rm :user/current/sketch.py
```

or reflash. If the crash is a fast hard-fault, USB serial won't enumerate and
serial recovery isn't possible — use safe boot or reflash.
