# Display Modes

The AMYboard OLED (firmware-owned `amyboard.display`) is driven by a pluggable
**display mode**. Exactly one mode is active at a time and owns what the screen
shows. The active mode is held in `active_display_mode` in
`sketches/01_polysynth.py` and is switched with `set_display_mode()`.

Modes are **selected on-device** via the polysynth menu → **Display Mode** (see
[MENU.md](MENU.md)), and the choice is **persisted** across reboots (see
"Persistence" below).

## Architecture

A display mode is a subclass of `DisplayMode` (in `sketches/01_polysynth.py`)
implementing three methods:

| Method | Called from | Responsibility |
|--------|-------------|----------------|
| `on_cc(cc, val)` | MIDI callback (`midi_cb`) | Record state only. Must stay cheap and must **not** draw. |
| `render(now)` | `loop()` via `service_display()` | Draw to the panel, pushing only the rows that changed. |
| `on_activate()` | `set_display_mode()` | Progressively clear the panel and reset cached frame state so the mode redraws from scratch. |

Modes are registered in the `DISPLAY_MODES` list; the Display Mode submenu indexes
that list.

### Audio-safety rules (every mode must follow)

MicroPython runs the whole sketch on one thread, so a long OLED blit blocks audio
and MIDI (a stalled note-off causes a stuck/over-long note). The shared
infrastructure enforces:

1. The MIDI callback only records state; it never draws.
2. Drawing is throttled to `DISPLAY_REFRESH_MS` (~10 fps) and only happens when
   content changed.
3. Drawing pushes **only** the framebuffer rows that changed. The SSD1327 has no
   partial-refresh in firmware, so a full `display.show()` blits the entire 8KB
   framebuffer over the 400 kHz I2C bus (~150–180 ms of blocking). `_push_rows()`
   windows it to just the changed rows, and `DISPLAY_MAX_ROWS_PER_REFRESH` caps
   rows-per-refresh.
4. **Full-screen clears/repaints are flushed progressively.** Zeroing then
   blitting the whole panel at once (e.g. entering a mode, or a menu's full
   repaint) is split into ~24-row bands across successive `loop()` calls
   (`_begin_clear()` / `_begin_flush()` / `_service_flush()`), so the longest
   single blit stays a few tens of ms instead of ~150 ms.

## Modes

### CC Monitor (`'CC Monitor'`) — default

A live monitor of incoming MIDI Control Changes on the active channel. Each
touched CC appears on its own row: `<cc>  <short label>  <raw 0-127>` (e.g.
`74  CUTOFF  92`). Labels come from `CC_LABELS` in `sketches/01_polysynth.py`
(mirrors [CC_MAPPING.md](CC_MAPPING.md)); unmapped CCs show `CC`.

- Newest at the bottom; survivors shift up as rows expire.
- A single knob sweep updates that CC's row in place (repaints one row).
- Up to `DISPLAY_MAX_LINES` (6) rows; oldest drops off the top.
- Auto-expiry `CC_EXPIRE_MS` (6 s) after last touch, so the screen settles to
  empty when you stop playing.
- Read-only: reflects controller activity, never changes the patch.

### Screensaver (`'Screensaver'`)

A minimal screensaver: a small dot drifting around the panel, pushing only the
band of rows spanning its old+new position each step (audio-safe).

### Oscilloscope (`'Oscilloscope'`) — stub

Placeholder. A real scope needs a tap into AMY's output samples, which isn't wired
up yet; the mode is selectable for completeness but only shows a "not available
yet" notice.

## Boot & persistence

- **Boot banner.** On power-up the firmware boot banner is left visible for
  `BOOT_CLEAR_MS` (3 s), then the panel is wiped once before the active mode takes
  over.
- **Idle timeout.** While the polysynth menu is open, `MENU_IDLE_MS` (10 s) with
  no encoder input auto-closes the menu back to the active display mode.
- **Persistence.** Picking a mode writes its name to
  `/user/polysynth_settings.json`; `_restore_display_mode()` re-selects it at boot
  (matched by name, so reordering the list is safe; unknown/missing → default).

## Adding a mode

1. Subclass `DisplayMode` in `sketches/01_polysynth.py` and implement `on_cc`,
   `render`, and `on_activate`, following the audio-safety rules above (draw via
   `_push_rows()`, clear via `_begin_clear()`, keep `on_cc` cheap).
2. Add an instance to the `DISPLAY_MODES` list — it appears in the Display Mode
   submenu automatically.
