# Firmware notes & gotchas

Hard-won notes about the AMYboard firmware, verified against the
**`82e69df-dirty on 2026-07-27`** build (see "Which build is on the board?"
below). Several of these caused long debugging sessions; read before assuming a
bug is in this repo's code.

## Correct firmware & flashing

- Image: `amyboard-full-AMYBOARD.bin` from the shorepine/tulipcc `amyboard`
  release (<https://github.com/shorepine/tulipcc/releases/tag/amyboard>).
- Two ways to update, and they differ in whether you lose `/user`:
  - **In-place upgrade at <https://amyboard.com/editor>** — **`/user` SURVIVES.**
    VERIFIED 2026-07-27: upgrading from the 2026-07-18 build to 2026-07-27 left all
    11 files intact at byte-identical sizes (launcher, both sketches, presets, kits,
    settings, wifi.json). This is the normal way to update.
  - **esptool writing the whole image** (`esptool.py write_flash 0x0
    amyboard-full-AMYBOARD.bin`) — **WIPES `/user`.** The launcher,
    `/user/sketches`, `arctor_presets.json`, `triggerbox_kits.json` and the rest
    are all erased. Redeploy afterward (launcher to `/user/current/sketch.py`, then
    the sketch with `--activate`), and restore the JSON state files by hand.
  Reserve the full flash for actual recovery. An earlier version of this file said
  "a reflash WIPES /user" without the distinction, which is true only of the second.
- **Back up `/user` before a full flash.** Samples live on the SD card and are not
  touched by either path, but presets and kits exist ONLY on the board.

## Which build is on the board?

`os.uname()` over the serial REPL carries the build commit and date — use it before
guessing from behaviour:

```python
import os; os.uname()
# (sysname='esp32', ..., release='1.24.0-preview',
#  version='82e69df-dirty on 2026-07-27', machine='AMYboard with ESP32S3')
```

The `amyboard` release tag is *rolling* — it gets re-pointed at new builds, so it
pins nothing on its own. `amy._KW_MAP` is the authoritative list of keywords the
build accepts (unknown kwargs raise `ValueError`, they are not silently dropped),
and `amy.message(**kw)` is a safe way to test one: it builds a wire string and
returns it without sending, touching the engine, or making a sound.

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

## Panel pushes cost bus time, and that time is stolen from MIDI

**MEASURED 2026-07-26 on hardware.** The single biggest cause of audible timing lag
in a sketch is not slow Python — it is pushing the whole framebuffer.

`amyboard.display_refresh()` **returns in ~3ms and that is not what it costs.** It
hands all 8KB to the panel bus (~400kHz), and the *firmware* blocks on that transfer
before calling the sketch's `loop()` again. That is ~190ms in which MIDI is not
serviced, so notes queue and arrive late. Any benchmark that times how long the call
takes to return will report ~3ms and be wrong; 8192 bytes at 400kHz cannot be 3ms,
and if a number implies 3MB/s on this bus, the instrument is the problem.

Windowed writes (`_push_rows(y0, y1)`, set col/row address then one `write_data`)
are honest: they block inside the caller's own tick, and cost scales with rows —
roughly **1.5ms per pixel row**, so a 12px menu row is ~18ms and a full frame is
~190ms either way.

**Rules for any sketch here that plays notes:**

- Redraw and push **only the rows that changed**. A one-row change (e.g. a parameter
  value) must not cost a full frame.
- Spread a full repaint across ticks — a band per tick — so no single call blocks the
  board. Push the row the cursor moved *to* first.
- **Never defer the DRAWING**, only the pushing. Deferring draws is what made the
  triggerbox cursor appear to vanish mid-scroll: input kept moving it while the panel
  showed neither position.
- Arctor has always done this (see its display header) and stays smooth; triggerbox
  regressed by replacing it with full-frame blits on the strength of a return-time
  benchmark, and spent a session rediscovering the above.

## Measuring timing lag: two instruments that work

Both live in `sketches/triggerbox.py` behind `DEBUG_BROWSE`. Read them over the REPL
via `sys.modules['sketch']._sketch_loop.__globals__[...]`; scripts in
`tools/`-adjacent scratch use `board_serial.BoardSerialSession`.

1. **Note-arrival ruler.** `midi.add_callback` writes `ticks_ms()` of each note-on
   into a preallocated list, tagged with what the sketch was doing. Hold a steady
   pattern; the intervals should be constant, and deviation *is* the defect — in
   milliseconds, with no listening test. Build this FIRST when chasing lag.
2. **Loop-gap log.** Record `ticks_diff(now, previous_loop_entry)` *and* how long the
   previous tick's own body ran. **Timing only your own code is not enough:** if the
   firmware stops calling `loop()`, every tick looks short and the stall is invisible.
   A gap with ~3% attributable to the sketch means something else owns the CPU.

Two traps, both hit in one session:

- `gc.mem_free()` walks the whole heap: **~15ms per call** on this ~3.5MB PSRAM heap.
  Sampling it per tick added ~31ms to every tick and made the box audibly jittery
  while completely idle — the probe *was* the symptom.
- **Never drive an audio test from a REPL script.** With the sketch stopped, the
  firmware loop is not running, so MIDI is not delivered: the test measures silence
  and then a burst of queued notes. Test arms must run from `loop()`.

## SD access delays note delivery too

Also measured on the triggerbox branch. On a flaky card these were worth hundreds of
milliseconds each, all on interactive paths:

- A failed `os.stat` retried with `time.sleep(0.1)` — up to 400ms per browse.
- `amyboard.mount_sd()` — **165ms**, and it was firing on every browse because one
  intermittent `EIO` stat was read as "the card is unmounted".
- Reading a WAV header with ~6 separate `read()`/`seek()` calls — 7-10ms per file.
- First read of a directory — up to ~180ms.

Practical rules: resolve the sample root once and cache it; cache directory listings
and header results; read headers only for rows on screen; and **do no card I/O while
notes are playing** — a tiny `midi.add_callback` that records the last note time is
enough to gate it. Explicit blocking actions (loading a sample) are fine; they are
expected and shown on screen.
