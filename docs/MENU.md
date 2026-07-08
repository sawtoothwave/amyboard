# On-device menus & encoder navigation

The AMYboard is navigated with a single **rotary encoder + push button** (Adafruit
Seesaw, front-panel I2C). There are two menu layers:

- the **global launcher menu** (owned by `wrapper_sketch.py`), and
- each sketch's **own menu** (e.g. the polysynth's, in `sketches/01_polysynth.py`).

This doc describes the implemented behavior. (`docs/MENU_SKETCHING.md` is the
original hand-drawn design sketch and may differ.)

## Universal gesture model

One rule everywhere: **click goes IN, hold backs OUT, turn scrolls.**

| Gesture | Meaning |
|---|---|
| **Turn** | scroll within a menu |
| **Click** (short press) | enter / select / drill in |
| **Hold** | back out one level. A hold **auto-repeats**: each ~600 ms of continuous hold backs out one more level. |

From **playing** (no menu open) the three obvious gestures — **turn, click, or a
short hold** — all open the sketch's own menu. Because hold auto-repeats,
*continuing* to hold from playing escalates outward to the global menu.

The back-out ladder, deepest → shallowest:

```
name-entry → sub-menu → sketch (root) menu → GLOBAL menu
```

A hold out of a sketch's **root** menu goes straight to the global menu — it does
not stop at "playing," so it never flashes the display mode on the way out.
"Playing" is the closed-menu base state, re-entered by a **Resume Playing** menu
item (a click) or the menu's **idle timeout**.

## Global launcher menu (`wrapper_sketch.py`)

The launcher is the permanent boot program (deployed to `/user/current/sketch.py`)
and never changes; it runs a chosen sketch and drives its `loop()`.

- **Overlay, not reboot.** Opening the global menu over a running sketch is an
  in-memory overlay: the sketch stays resident and its MIDI callback + audio keep
  running, so **Resume returns instantly with no audio dropout**. The launcher
  stops calling the sketch's `loop()` while the overlay is up (display/CV pause;
  audio does not).
- **Items:** `Resume` (only shown when a sketch is resident) and `Load Sketch`
  (lists `.py` files in `/user/sketches` and boots into one).
- **Only `Load Sketch` resets the board** (`machine.reset()`), which is required
  to tear down the previous sketch's `midi.add_callback` — otherwise two synths
  would answer MIDI.

### Launcher ↔ sketch contract

To make hold-to-back-out span both the sketch's own menu levels and the final hop
out to global without two encoder readers fighting, the **launcher is the sole
encoder reader**. It injects a `launcher` object into each sketch's namespace and
feeds abstract input through it:

| Field | Direction | Meaning |
|---|---|---|
| `launcher.delta` | launcher → sketch | encoder detents this tick |
| `launcher.click` | launcher → sketch | short click this tick |
| `launcher.back` | launcher → sketch | hold: pop one of the sketch's own menu levels |
| `launcher.menu_depth` | sketch → launcher | how deep the sketch's menu is (0 = playing) |
| `launcher.repaint` | launcher → sketch | force a full redraw (overlay clobbered the screen) |
| `launcher.resumed` | launcher → sketch | on Resume, close the sketch's own menu (Resume → playing) |

A hold becomes `back` while the sketch is ≥2 levels deep; at the sketch's root (or
at playing) it opens the global overlay instead. A sketch that ignores all of this
still works — it just jumps straight to the global menu on a hold. Sketches should
consume input via these fields, **not read the encoder directly** (one reader
only) — *except* as a self-contained fallback when the wrapper is absent (see
below); the two never read the encoder at the same time.

## Polysynth menu (`sketches/01_polysynth.py`)

Opened by turn/click/short-hold while playing. Structure:

```
POLYSYNTH
├─ Param Control     → numbered list of all 28 synth params → per-param editor
├─ Presets           → Save State as Preset · Load Preset · Delete Preset
├─ Display Mode      → CC Monitor · Screensaver · Oscilloscope   (see DISPLAY_MODES.md)
└─ Resume Playing
```

- **List scrolling** is 1:1 with encoder detents and **clamps at the ends** (no
  wrap-around).
- **Idle timeout:** after `MENU_IDLE_MS` (10 s) with no encoder input the menu
  **suspends** — the active display mode takes back the screen but the menu stack
  is kept (which level you're on and the cursor position, or an editor's value),
  and the next input (turn, click, or hold) resumes you exactly where you were.
  A hold only escapes to the global menu from an *active* (non-suspended) root.
  Closing to playing happens only on explicit action (Resume Playing, or backing
  out past the root).
- **Audio-safe rendering:** menu draws use row-level diffing (a cursor move
  repaints only two rows) and a progressive banded framebuffer flush on full
  repaints, so navigating never holds the I2C bus long enough to drop a note.
  See the audio-safety rules in [DISPLAY_MODES.md](DISPLAY_MODES.md).

### Param Control (parameter editor)

**Param Control** lists all 28 editable synth parameters, numbered (`1. Osc A
Pitch` … `28. Lfo>Amp B`). Selecting one opens a **0-127 slider editor** for that
parameter, which drives the parameter's real MIDI CC through the same
`handle_cc()` path a hardware knob uses — so on-device edits and the E16 knobs
stay in sync and share all value→sound mapping. Exposing another parameter is a
one-line addition to the `PARAMS` list.

Editor gestures:

| Gesture | Action |
|---|---|
| **Turn** | Adjust the value **live** (you hear it). Encoder **acceleration**: a fast spin covers ground, a single detent stays 1:1 for fine control. |
| **Single click** | Keep the current value and exit to the list. (Deferred `EDIT_DBLCLICK_MS` ≈ 400 ms to detect a double-click.) |
| **Double click** | Reset the parameter to its patch default, staying in the editor. |
| **Hold** | Revert to the value on entry and exit to the list. |

- **Display:** the raw 0-127 value and (for discrete params) a friendly label
  render at **2× scale** over a cursor track; the title and end labels stay 1×.
  (framebuf has only an 8×8 font and no scaling API, so `_text2x()` renders glyphs
  into a temp buffer and blits each pixel as a 2×2 block.)
- **Discrete labels:** wave shapes show `Sine / Pulse / Saw Dn / Saw Up /
  Triangle / Noise`; filter type shows `LP 24 / LP / BP / HP`; osc pitch shows the
  interval / cents (`-1 oct`, `+5th`, `Unison`, `+12c`, …).
- **Live MIDI:** while the editor is open, an incoming CC for that parameter moves
  the cursor/value in real time, and the encoder continues from wherever MIDI left
  it — the E16 and the encoder cooperate on one value.
- **Rendering** pushes only the changed bands per turn (snappy and audio-safe),
  throttled by `EDIT_REFRESH_MS` so a stream of incoming CCs can't hog the I2C bus.

### Standalone mode (no wrapper)

The polysynth is a **self-contained single file**: dropped on a board as its own
boot sketch (no `wrapper_sketch.py`), it drives its own menu. When the wrapper
doesn't inject a `launcher`, the sketch builds an internal `_StandaloneLauncher`
that reads the Seesaw encoder directly and fills the *same* `launcher.*` fields,
so all the menu code is identical in both modes. It replicates the hold-ladder
**minus the global-escape rung**: a hold at the polysynth root menu does nothing
(there's no wrapper to escape to) — leave the root via **Resume Playing** or the
idle timeout. The reader is only instantiated when the wrapper is absent, so the
two never contend for the encoder, and wrapped behavior is unchanged.
