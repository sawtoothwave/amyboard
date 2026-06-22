# Display Modes

The AMYboard OLED (firmware-owned `amyboard.display`) is driven by a pluggable
**display mode**. Exactly one mode is active at a time and owns what the screen
shows. The active mode is held in `active_display_mode` in `sketch.py` and can
be swapped with `set_display_mode()` (intended for a future push-encoder menu).

## Architecture

A display mode is a subclass of `DisplayMode` (in `sketch.py`) implementing three
methods:

| Method | Called from | Responsibility |
|--------|-------------|----------------|
| `on_cc(cc, val)` | MIDI callback (`midi_cb`) | Record state only. Must stay cheap and must **not** draw. |
| `render(now)` | `loop()` via `service_display()` | Draw to the panel, pushing only the rows that changed. |
| `on_activate()` | `set_display_mode()` | Clear the panel and reset cached frame state so the mode redraws from scratch. |

Modes are registered in the `DISPLAY_MODES` list. A future push-encoder menu will
index that list to let the user pick which mode drives the OLED.

### Audio-safety rules (every mode must follow)

MicroPython runs the whole sketch on one thread, so a long OLED blit blocks audio
and MIDI (a stalled note-off causes a stuck/over-long note). The shared
infrastructure (`service_display()`, `_push_rows()`, `_boot_wipe()`) enforces:

1. The MIDI callback only records state; it never draws.
2. Drawing is throttled to `DISPLAY_REFRESH_MS` (~10 fps) and only happens when
   content changed.
3. Drawing pushes **only** the framebuffer rows that changed. The SSD1327 has no
   partial-refresh in firmware, so a full `display.show()` blits the entire 8KB
   framebuffer over the 400 kHz I2C bus (~150-180 ms of blocking time).
   `_push_rows()` windows it to just the changed rows (~1KB / ~5-20 ms), and
   `DISPLAY_MAX_ROWS_PER_REFRESH` caps how many rows are pushed per refresh so a
   busy screen can never hold the bus long enough to delay a note-off.

## CC Monitor

`name = 'CC Monitor'` — the default (and currently only) mode.

A live monitor of incoming MIDI Control Changes on channel 12. As you turn knobs
on the control surface, each touched CC appears on its own row showing:

```
<cc number>  <short label>  <raw 0-127 value>
```

For example, turning the filter cutoff knob shows `74  CUTOFF  92`. Short labels
come from the `CC_LABELS` map in `sketch.py` (which mirrors the frozen CC map in
[CC_MAPPING.md](CC_MAPPING.md)); an unmapped CC falls back to the label `CC`.

Behavior:

- **Newest at the bottom.** New CCs are appended at the bottom; as rows above them
  expire, survivors shift up.
- **Update in place.** Sweeping a single knob updates that CC's row in place (it
  does not reshuffle the list), so a sweep only repaints one row.
- **Up to `DISPLAY_MAX_LINES` rows** (6) are shown at once; when more CCs are
  active than fit, the oldest drops off the top.
- **Auto-expiry.** A CC is removed from the list `CC_EXPIRE_MS` (6 s) after it was
  last touched, so the screen settles back to empty when you stop playing.
- **Boot banner.** On power-up the firmware boot banner is left visible for
  `BOOT_CLEAR_MS` (3 s), then the panel is wiped once before the monitor takes
  over.

This mode is read-only: it reflects controller activity and never changes the
patch.

## Adding a mode

1. Subclass `DisplayMode` in `sketch.py` and implement `on_cc`, `render`, and
   `on_activate`, following the audio-safety rules above (draw via `_push_rows()`,
   keep `on_cc` cheap).
2. Add an instance to the `DISPLAY_MODES` list.
3. Switch to it at runtime with `set_display_mode(<instance>)` (a push-encoder
   menu will eventually call this).
