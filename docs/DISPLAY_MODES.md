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
   repaint) is split into `FLUSH_BAND_ROWS` (~12) row bands across successive
   `loop()` calls (`_begin_clear()` / `_begin_flush()` / `_service_flush()`), so
   the longest single blit stays a few tens of ms instead of ~150 ms.

## Modes

### CC Monitor (`'CC Monitor'`) — default

A live monitor of incoming MIDI Control Changes on the active channel. Each
touched CC appears as a two-line group — the parameter's name on top, then
`CC <n>` with its value below (e.g. `Cutoff` / `CC 74            92`). The name
and value are derived from the one `PARAMS` table (via `_mon_name` and the
grid's value formatter), so every parameter — effects included — is labelled
and nothing can drift; the value is the friendly, formatted reading the editor
shows (`+5th`, `Saw Up`, `-2 dB`, `2.0 Hz`) when the parameter has one, else
the raw 0-127. An unmapped CC (e.g. the raw mod wheel, CC 1) shows its number.

- Newest at the bottom; survivors shift up as groups expire.
- A single knob sweep updates that CC's value line in place (repaints one row).
- Up to `DISPLAY_MAX_ENTRIES` (4) groups; oldest drops off the top. Each group
  is two 12px text lines (24px), and the leftover 32px is spread across the 3
  gaps between them (~10-11px each), flush to the top and bottom edges.
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
  no encoder input **suspends** it: the active display mode takes back the screen
  while the menu keeps its place, and the next encoder input resumes the menu
  where you left it (see [MENU.md](MENU.md)).
- **Persistence.** Picking a mode writes its name to
  `/user/polysynth_settings.json`; `_restore_display_mode()` re-selects it at boot
  (matched by name, so reordering the list is safe; unknown/missing → default).

## Adding a mode

1. Subclass `DisplayMode` in `sketches/01_polysynth.py` and implement `on_cc`,
   `render`, and `on_activate`, following the audio-safety rules above (draw via
   `_push_rows()`, clear via `_begin_clear()`, keep `on_cc` cheap).
2. Add an instance to the `DISPLAY_MODES` list — it appears in the Display Mode
   submenu automatically.
