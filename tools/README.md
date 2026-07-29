# tools/

Dev tooling for the sketches (mostly polysynth; `triggerbox_sim.py` covers the
triggerbox). Nothing here ships to the board — these are host-side checks and a
display harness. (Deploy scripts live at the repo root:
`deploy_auto.py`, `board_serial.py`, `deploy_wifi.py`.)

## `arity_check.py` — catch call-site/signature mismatches

**This runs automatically.** `deploy_auto.py` imports `check_file()` and refuses to
upload a sketch that fails, before it opens the serial port — so a bad sketch never
reaches the board. `--no-check` skips the gate; don't (see `docs/AGENTS.md`).

Run it by hand any time:

```sh
python3 tools/arity_check.py                       # defaults to sketches/01_polysynth.py
python3 tools/arity_check.py wrapper_sketch.py
```

Exits non-zero on a mismatch.

It exists because of a real bug: `_draw_grid_header(d, disp)` grew a `group`
argument and its two call sites were left behind. Every grid render raised
`TypeError`, which the grid render's `except Exception` swallowed, so the screen
just went blank. Nothing else catches this class:

- `py_compile` passes — a wrong-arity call is valid *syntax*.
- `grid_preview.py` passes — it execs the real `_draw_grid_*` functions but
  supplies its **own** call sites, so it validates their insides, not their callers.
- The board itself is the only other detector, and (before `_render_fault`) it
  reported the failure as a blank screen.

Handles defaults, `*args`, `**kwargs`, and keyword arguments, and skips call sites
that splat (`*a` / `**kw`) since their arity isn't knowable statically. **Scope:
module-level functions called by bare name** — methods, attribute calls, and anything
reached through a variable are not checked; resolving those needs real type inference.
So it is a floor, not a guarantee.

## `grid_preview.py` — render the knob grid offline at 128×128

```sh
python3 -m venv venv && ./venv/bin/pip install pillow
./venv/bin/python tools/grid_preview.py            # -> tools/preview_out/*.png
./venv/bin/python tools/grid_preview.py path/to/other_sketch.py
```

Writes one true 128×128 PNG per group per page, a `hover_cc.png` showing the hover CC
reveal on its worst case (the widest CC number, in an edge cell with neighbours), plus
a `sheet.png` contact strip.
Needs Pillow; nothing else. Iterate on layout here rather than burning ~20s deploy
cycles — and, more to the point, **look at it before believing arithmetic**. It has
already caught a spacing bug that a clean-looking calculation missed.

### What it is and is not faithful about

- **Geometry: exact.** It execs the sketch's real `_grid_layout` and `_draw_grid_*`
  against a fake framebuf, so positions and sizes are whatever the shipped code
  computes — not a re-implementation that can drift.
- **Font: exact.** `font8x8.json` holds the board's actual 8×8 glyphs, read off the
  hardware by `grab_font.py`. This matters more than it sounds: an earlier version
  substituted a Mac mono font and made 4-char labels look survivable when on the
  real font they fuse into a wall (`EQLOEQMDEQHICHLV`). A lookalike font is worse
  than no preview.
- **Greys: exact.** Quantised to the panel's 16 levels (it is 4-bit, top nibble
  only), so a dim level here is one the panel can actually show.
- **NOT faithful:** OLED contrast and pixel bloom, and it errs in one direction —
  **dim greys read much brighter on the panel than they do here** (confirmed on
  hardware 2026-07-16). So anything resting on *"is this dim thing visible"* —
  `GRID_C_SECT_RUL` at level 4/16, `GRID_C_PAGE_OFF` at level 1/16 — will look
  more marginal in the mock than it is. Don't brighten a constant because the PNG
  looks faint; check the panel.
- **NOT faithful:** the caller. It reimplements `_GridLevel.render`'s drawing
  sequence, so it cannot catch bugs in `_GridLevel.render` itself. That is
  `grid_sim.py`'s / `arity_check.py`'s job.

It parses the `PARAMS` table out of the source with regexes rather than importing
the sketch (which needs `amy`/`amyboard`); the grid label is the 4th positional
column of each `_Param(...)` row. If a row grows a new shape, the parser may need
a nudge — it refuses to run (rather than silently dropping rows) when the regex
matches fewer rows than the table holds.

## `grid_sim.py` — run the real menu code on the host

```sh
python3 tools/grid_sim.py                          # defaults to sketches/01_polysynth.py
```

Exits non-zero on failure. Stubs `amy` / `amyboard` / `midi` (and adds MicroPython's
`time.ticks_*`), execs the sketch, and drives the **actual** `SketchMenu` — not a
re-implementation, so it cannot drift from the shipped code.

It exists because the CC→grid path has no other way to be tested: reflecting an
incoming CC needs a MIDI source, and the board has no REPL while the sketch runs, so
a CC cannot be injected over serial either. The alternative was shipping it on
reasoning alone.

Currently checks that an external CC marks the right cell stale, that the cell is
actually pushed, that the 19.5ms header band is skipped when the value has not
changed, that a CC flood is bounded per tick and drains rather than being dropped,
that every `PARAMS` row drives its full map→store→send pipeline through `handle_cc`
without raising (the table calls `to_val`/`update` through variables, which
`arity_check.py` cannot see — this is the arity net for those), and that the
worst-case frame stays inside the ~69ms `loop()` tick.

`hover_checks` covers the **hover CC reveal**: that a dwell under 3 s does nothing, that
the reveal then costs exactly one cell push per phase (and doesn't redraw within a
phase), that it alternates, that any input cancels it at once, that a selected cell
never reveals, and that what lands on the panel is `#<cc>`. It ages the level's hover
clock rather than sleeping — the phase is a pure function of elapsed time, which is what
makes that possible.

`preset_apply_checks` covers **preset loading**: that a param the snapshot predates
resets to its default instead of inheriting the last patch's value (the portamento
leak heard on hardware 2026-07-28), that a retired CC is still skipped rather than
fatal, and that a load applies all 50 params.

It also covers **Scan Presets** (`scan_checks`), driven through `menu.handle()` so
the root-menu wiring and dispatch are exercised too: that a turn loads without a
click, that the ends wrap, that a summed fast-spin delta applies only the preset
landed on, that a click opens Param Control *in place of* the scan (and that the grid
there shows the live CC-informed value, not the snapshot's), that a hold out of it
lands on the root rather than back in the scan, and that the current-preset pointer is written
**exactly once** — when the scan level itself goes away — with no step in between
writing settings. That last one is the point: a settings write per detent is flash
I/O in the audio path, and nothing else here would notice it.

- **Proves:** state-machine behaviour — which cells go stale, what gets pushed, on
  which tick.
- **Does NOT prove:** real I2C timing, audio, or anything visual. Frame costs are
  computed from the measured per-push figures (9.5ms/cell, 19.5ms/band), not timed.
- **Gotcha:** renders are gated by `EDIT_REFRESH_MS` against the last render's
  timestamp. On the board `loop()` arrives every ~69ms so the gate always passes;
  in the sim, calls are microseconds apart and get throttled away. Drive renders
  through `tick()`, which ages the timestamp. Skip it and the sim renders **nothing**
  while every check appears to pass.

## `triggerbox_sim.py` — the same trick, for the triggerbox

```sh
python3 tools/triggerbox_sim.py                    # defaults to sketches/triggerbox.py
```

Exits non-zero on failure. Sibling of `grid_sim.py` with the same stub-and-exec
approach, aimed at `triggerbox.py`'s slot editor. Unlike `grid_sim.py` it puts
`time.ticks_ms()` fully under the test's control, because the behaviour under test
*is* timing: a single click's commit is deferred by `EDIT_DBLCLICK_MS` so a second
click can be read as a double.

Currently checks the encoder acceleration curve (1:1 at one detent, quadratic above,
capped) and which lists opt into it; every slot-editor gesture (single click commits
after the window with exactly one flash write, double click resets to the
`PARAM_SPEC` default and keeps editing, hold reverts to the pre-edit value and writes
nothing, a turn cancels a pending click, and an in-flight commit is flushed rather
than dropped when the menu suspends or closes); and the panel flush queue — that the
row the cursor landed on is the FIRST band pushed, that a jump costs two 12px bands
rather than one spanning everything between, and that popping a level forces the
level underneath to fully repaint.

It also covers **kits** end to end, against real WAVs and a real JSON file (with
`KITS_FILE` redirected to a temp path, so the round-trip through flash actually
runs rather than being mocked): save/overwrite/delete, the pinned-first `EMPTY`
virtual kit and alphabetical ordering, the name-entry ring (build, backspace, clamp
on OK, the length cap), the root menu's item list, the `*` modified marker and its
headline budget, that loading a kit QUEUES its samples instead of loading them
inline, and that a sample which has moved leaves a labelled empty pad — refused on
the `stat` as `GONE`, with the wanted path still persisted so putting the card back
brings it home.

- **Proves:** which gesture lands which value, when the commit fires, that a commit
  is never silently lost, what order panel regions go out in, and what a kit
  actually stores and restores.
- **Does NOT prove:** audio, encoder feel, or the real per-band I2C cost (pushes are
  counted, not timed — 19ms/12-row band is the measured figure they're reasoned
  against). Nor the real SD card: the "missing sample" path is a deleted temp file,
  not a pulled card.

## `grab_font.py` — read the board's 8×8 font over serial

```sh
python3 tools/grab_font.py        # board must be connected; rewrites tools/font8x8.json
```

Renders every glyph (chr 32..126) into an in-RAM `framebuf` on the board, reads the
pixels back, and writes `font8x8.json`. **You should not need to run this** — the
font is checked in. Re-run it only if the board's MicroPython/framebuf font changes.

Note it drops the board to a REPL, so the sketch stops until you re-deploy or reset.
