# Repository Guidelines

## **FOR EVERY USER REQUEST THAT REQUIRES CHANGING PRODUCTION CODE OR TESTS PERFORM THIS RITUAL**

1. Repeat back to the user what you think is in scope for this request and what's out of scope
2. Once the user has agreed, make an explicit promise to the user that if you think you need to do something out of scope to make the app better, simpler, more elegant or more robust, that you'll stop working and explain why expanding the scope accords with our organizing principle of lowering cognitive load for new devs and prioritizing simplicity over cleverness.
3. When you complete a task, after you run tests, ask the user if they want you to "perform a code review and see if any cleanup is required now that this task complete to see if there is anything messy or inelegant?" If they say yes, review uncommitted changes.
4. Update this document to keep it in parallel with requests the user makes of your behavior.
5. Whenever a change affects live CC assignments or control behavior in `sketches/arctor.py`, update `docs/CC_MAPPING.md` in the same task.
6. **Verify before you deploy, and prefer the offline tools to the board.** `deploy_auto.py` refuses to upload a sketch whose call sites don't match their signatures (it gates on `tools/arity_check.py`) — treat a failure as a real bug, never as something to get past. For Arctor menu/grid work also run `tools/grid_sim.py` (drives the real `SketchMenu` against stubbed board modules) and `tools/grid_preview.py` (renders the display at true 128×128 with the board's own font). Iterating on a picture costs seconds; a deploy costs ~20s and your attention. See `tools/README.md` for what each is and is not honest about.

## User Guidance

- The user is an experienced technologist but is not a professional developer; explain why you are making each meaningful architectural choice.
- When a direction could reasonably apply to both user-facing and internal code (for example renaming UI labels vs. domain modules), pause and confirm the intended scope before proceeding.

## Big Picture

- This repository is intended to create the instrument/control code for an AMYboard synthesizer board, described here: https://github.com/shorepine/tulipcc/blob/main/docs/amyboard/README.md
- The available documentation for the AMY language and the hardware is here: https://github.com/shorepine/amy
- The user owns an AMYboard and wants to use it within their Eurorack synthesizer system, primarily as a polyphonic synthesizer engine that they will control (keyboard, sequencing, parameter changes) via MIDI. The primary keyboard instrument will be the Arturia Keystep Pro; the primary sequencer will be the Squarp Hermod+ (although it might also be driven by MIDI from a variety of other sources, routed through the Hermod+ and into the AMYboard); the primary MIDI parameter controller will be the Oxi e16.
- The user has an Adafruit 128x128 OLED screen (https://www.adafruit.com/product/4741) and an Adafruit I2C STEMMA QT Rotary Encoder Breakout (https://www.adafruit.com/product/5880) connected to the board.


## Safety Rules (Workspace Hygiene)

- **NEVER trigger audio on the board without waiting for the user's explicit
  go-ahead.** Before anything that makes sound — tone/liveness tests, trigger
  sequences, A/B comparisons, or a "quick diagnostic beep" — describe exactly what
  will play, then STOP and wait for the user to say go in a **separate message**.
  Do not announce and fire in the same turn. There is **no exception** for "it's
  only a diagnostic" or "we're mid-debug"; an active audio-debugging session is
  when the user's speakers are most live. (Requested 2026-07-18, re-violated and
  re-affirmed 2026-07-19.)
- Treat changes you did not author as intentional collaborator work.
- Do not revert or remove others' changes without explicit user approval.
- Do not use destructive git commands.
- **NEVER EVER commit with `--no-verify`.**
- **NEVER EVER push with `--no-verify`.**
- **NEVER deploy with `--no-check`** to get around a failing arity check. MicroPython raises `TypeError` at *call* time, so a mismatched call site runs fine until the moment it fires — and the sketch's render paths swallow exceptions to keep audio alive, so it surfaces as a blank screen with no traceback. That bug has already cost a debug round on hardware. `py_compile` cannot see it; neither can `grid_preview` (it supplies its own call sites).
- Do not delete files unless explicitly instructed.
- Prefer to use `--3way` style analysis for merge conflicts
- Never commit secrets.
- When possible, consult and leverage already-working code from other sketches we've already built together (for example, knob behavior, global redraw parameters and ways of reducing screen lag or audio jittering while the user navigates the board, etc.) rather than rebuilding the wheel.