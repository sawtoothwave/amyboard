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
only).

## Polysynth menu (`sketches/01_polysynth.py`)

Opened by turn/click/short-hold while playing. Structure:

```
POLYSYNTH
├─ MIDI Control      placeholder ("coming soon" — see next session: CC editor)
├─ Presets
│  ├─ Save State as Preset   placeholder (Stage 4)
│  └─ Load Preset            placeholder (Stage 4)
├─ Display Mode      → CC Monitor · Screensaver · Oscilloscope   (see DISPLAY_MODES.md)
├─ MIDI Channel      placeholder (deferred — see MIDI_MAPPING.md / FIRMWARE_NOTES.md)
└─ Resume Playing
```

- **Idle timeout:** after `MENU_IDLE_MS` (10 s) with no encoder input, the menu
  auto-closes back to the active display mode.
- **Audio-safe rendering:** menu draws use row-level diffing (a cursor move
  repaints only two rows) and a progressive banded framebuffer flush on full
  repaints, so navigating never holds the I2C bus long enough to drop a note.
  See the audio-safety rules in [DISPLAY_MODES.md](DISPLAY_MODES.md).
