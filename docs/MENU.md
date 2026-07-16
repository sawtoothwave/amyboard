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
├─ Param Control     → Osc · VCF · LFO · VCA · FX → each a sectioned knob grid
├─ Save As Preset    → Overwrite current · Save as New · Cancel  (see Presets)
├─ Load Preset       → INIT (built-in) + saved presets
├─ Delete Preset     → saved presets (INIT is not deletable)
├─ Display Mode      → CC Monitor · Screensaver · Oscilloscope   (see DISPLAY_MODES.md)
└─ Resume Playing
```

- **List scrolling** is 1:1 with encoder detents and **clamps at the ends** (no
  wrap-around). Lists longer than one screen are **paginated** (`MENU_VISIBLE`
  items per page) rather than continuously scrolled: the cursor moves within a
  fixed page, and the window advances a whole page only when the cursor crosses a
  page boundary. A row of small squares at the bottom-right — one per page, the
  current page filled and the rest hollow — shows where you are. This
  keeps navigation snappy — moving within a page repaints only two rows, and the
  costlier full-page repaint fires once per page instead of on every step past a
  sliding edge.
- **Idle timeout:** after `MENU_IDLE_MS` (15 s) with no encoder input the menu
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

### Param Control (the knob grid)

**Param Control** opens a list of the five parameter groups (Osc / VCF / LFO /
VCA / FX). Selecting one opens that group's **knob grid**: every param is a cell
with a short label and a value bar, edited in place. There is no separate editor
screen — a click selects the cell you are on and turning then adjusts it live.
Edits drive the param's real MIDI CC through the same `handle_cc()` path a
hardware knob uses, so on-device edits and the E16 knobs stay in sync and share
all value→sound mapping. Exposing another parameter is still a one-line addition
to the `PARAMS` list; the layout re-packs itself around it.

The grid is 4 columns wide and cut into labelled **sections** of up to 4 params
each — `OSC A`, `FILTER`, `ADSR`, `EQ`, `CHORUS`, and so on. Sections are not
decoration: they carry the group prefix a label would otherwise need, which is
what lets a cell label be 3 characters instead of 4, which is what makes 4
columns legible at all. See [CC_MAPPING.md](CC_MAPPING.md) for the full
structure, the 4-char hard limit, and the vertical budget (which has no slack).

Gestures — the cursor is the cell you are on, "selected" is the cell you are
editing (shown knocked out / reverse-video):

| Gesture | With the cursor | With a cell selected |
|---|---|---|
| **Turn** | Move cell to cell, in reading order (across a row, then on to the next section). Clamps at the ends. | Adjust the value **live** (you hear it). Encoder **acceleration**: a fast spin covers ground, a single detent stays 1:1. Bucketed params step one bucket per detent. |
| **Click** | Select this cell for editing (snapshots the value, so a hold can revert it). | Keep the value and go back to the cursor. Deferred `EDIT_DBLCLICK_MS` ≈ 400 ms so a second click can arrive. |
| **Double click** | — | Reset the param to its patch default, staying selected. |
| **Hold** | Back out to the group list. | Revert to the value on entry and go back to the cursor. |

- **Top band:** the group name on the left, the focused param's live value on the
  right. Bucketed params show their word rather than a number (`fmt`), bipolar
  params draw a center-anchored bar.
- **Discrete labels:** wave shapes show `Sine / Pulse / Saw Dn / Saw Up /
  Triangle / Noise`; filter type shows `LP 24 / LP / BP / HP`; osc pitch shows the
  interval / cents (`-1 oct`, `+5th`, `Unison`, `+12c`, …).
- **Live MIDI:** while a cell is selected, an incoming CC for that param moves its
  value in real time, and the encoder continues from wherever MIDI left it — the
  E16 and the encoder cooperate on one value.
- **Pagination:** 3 sections fit a page (so 12 params); a group with more pages,
  with a bright dash for the current page and a dim dot per other page, centred in
  a reserved band at the bottom. Only FX (14 params) pages today.
- **Rendering** redraws only the changed cell(s) plus the top band per turn, and
  pushes exactly that cell's rectangle over I2C via `_push_window()` — a few
  hundred bytes, a few ms — rather than the full panel, so dialing stays well
  within the audio-render budget. Cell rectangles come from the layout pass
  (`_grid_layout`), which resolves each group to absolute `(page, x, y)` once when
  the level opens.

> **History — there is no longer a separate slider editor.** Param Control used to
> be a numbered list of every param; picking one opened a full-screen 0-127 slider
> (`_EditLevel`). The grid replaced it, and the code was removed once it had been
> unreachable for a while. Params are edited *in place* in the grid — if you are
> looking for the slider screen, it is gone deliberately, not missing.

### Presets

Presets are named patch snapshots in internal flash (`/user/polysynth_presets.json`).
The three actions live directly on the root menu (**Save As Preset / Load Preset /
Delete Preset**).

- **What a preset stores:** the raw 0-127 value of every editable CC — a snapshot
  of `param_values`. Because both the E16 knobs (external MIDI CC) and the on-device
  editor funnel through the same `handle_cc()`, which records each value into
  `param_values`, a preset captures the current patch **regardless of which set it**
  (last-write-wins per parameter). Loading replays those values through `handle_cc`,
  the same path a knob turn takes, so held notes are never cut.
- **Save flow:** if a preset is "current" (last loaded or saved this session, and
  still present), Save As Preset opens a chooser — **Overwrite** (→ `OVERWRITE
  "name"?` confirm), **Save as New** (→ name entry), **Cancel**. With nothing
  current, or the current being INIT, it goes straight to name entry. Name entry is
  a lowercase char ring; committing shows `SAVE?`/`OVERWRITE?` confirm. Success
  flashes **PRESET SAVED!**.
- **Load flow:** applies the preset live, flashes **PRESET LOADED!**, then returns
  to playing. The loaded preset becomes the session's "current" one.
- **Built-in `INIT` preset:** a *virtual*, write-protected preset synthesized from
  the parameter defaults (not stored in flash, so it can never go stale or be
  corrupted). It is always first in the Load list — a one-click return to a clean
  init state — and cannot be deleted (absent from the Delete list) or overwritten
  (the name `INIT`/`init` is reserved).
- **Resume across reset:** the name of the last loaded/saved preset is persisted to
  settings (`current_preset`) and re-applied at boot by `_restore_current_preset()`
  (after `init_synth()`), so a reset resumes that patch rather than the bare
  defaults. Deleting the current preset clears the pointer. The chosen display mode
  persists the same way.

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
