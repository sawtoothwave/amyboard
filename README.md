# Overview

This project provides a few different pieces of code designed to add some cool features to an [AMYboard](https://github.com/shorepine/tulipcc/blob/main/docs/amyboard/README.md) synthesizer.

## Utilities

- **A deploy/verify utility** that sends sketches directly to your AMYboard from the command line or a code editor like VS Code and verifies that the board is running what's expected. Helpful when coding new sketches.
- **An onboard sketch loader** that runs directly on the AMYboard, letting you select and load different sketches without powering down, unracking, or re-connecting to your computer.

## Individual AMY instrument sketches

- **Arctor:** A 2-oscillator, analog-style, 6-voice polysynth with MIDI CC control over all critical functions. By default, Arctor is preconfigured to "just work" with a [screen](https://www.adafruit.com/product/5297?gad_source=1&gad_campaignid=23986111167&gbraid=0AAAAADx9JvS2lB1lX_Ft_-n0t6ivycl2x&gclid=CjwKCAjw6rfSBhAqEiwA_yocptUVmj3G4L9UGqaPX8xZzAYFERMJMN3gjPKZAO3HPVFmfF6dC9cI-RoCM88QAvD_BwE) and [click encoder](https://www.adafruit.com/product/5880) (connected via I2C) for navigation, parameter control, and saving/loading presets. If all you have is an AMYboard with none of those I2C peripheral parts, you can still use the synthesizer and control its parameters using an external MIDI controller (more details **HERE**).
- **More to come (maybe).**

### Running Arctor

`sketches/arctor.py` is **self-contained** — one file, no other part of this repo
required at runtime. It imports only firmware modules (`amy`, `amyboard`, `midi`)
and the standard library. Two ways to install it, and it detects which by itself:

| Copy it to | You get |
|---|---|
| `/user/sketches/arctor.py` | pick it from the onboard sketch loader; hold backs out to the global menu |
| `/user/current/sketch.py` | boots straight into Arctor, no launcher; it reads the encoder itself |

Both paths verified on hardware 2026-07-30. From this repo:
`python deploy_auto.py --sketch sketches/arctor.py --activate`

**Two things to know before you file a bug:**

- **Firmware:** needs the AMYboard build of **2026-07-27 or later**. Portamento
  (CC 34) uses AMY's per-oscillator `'m'` keyword, absent from older builds — on
  those, Glide silently does nothing and everything else works. Check with
  `import os; os.uname().version`.
- **MIDI channel is fixed at 12** and there is no on-device picker. AMY auto-routes
  MIDI channel N to synth N, and this instrument lives on synth 12. Changing it
  means editing two coupled constants in the sketch (`SYNTH` and the CC filter),
  which must agree.

Presets and the selected display mode persist to `/user/arctor_presets.json` and
`/user/arctor_settings.json`, both created on first save.




### Scrap content below

- **`sketches/arctor.py`** is the canonical instrument: a 2-oscillator (A/B)
  analog-style synth with 6-voice polyphony, a shared resonant filter (VCF
  envelope + key tracking), a VCA envelope, and a per-voice LFO routed to vibrato
  (global), PWM, filter cutoff and per-oscillator tremolo. Parameters update live
  — no voice reset, so held notes are never cut. CC assignments follow
  AMYboard/standard-MIDI defaults where they exist; the full map is in
  [docs/CC_MAPPING.md](docs/CC_MAPPING.md).
- MIDI is received on **channel 12 only** — AMY maps synth number N to MIDI
  channel N, so the instrument lives on synth 12. CV1 provides 1V/oct monophonic
  pitch and CV2 a gate. (Firmware specifics — the boot-time channel-1 default
  synth, `grab_midi_notes` — are covered in
  [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and
  [docs/FIRMWARE_NOTES.md](docs/FIRMWARE_NOTES.md).)
- **On-device control** via a rotary encoder + button (click in / hold out / turn
  to scroll): a slim global launcher plus each sketch's own menu — see
  [docs/MENU.md](docs/MENU.md). The Arctor menu offers **Param Control** (edit
  all 28 synth parameters as 0-127 sliders), **Presets** (save / name / load /
  delete patches to flash), and a selectable, persisted OLED **display mode**
  ([docs/DISPLAY_MODES.md](docs/DISPLAY_MODES.md)); the mod wheel drives vibrato.
  Run without the launcher, Arctor reads its own encoder too, so it works
  as a **self-contained single file**.
- Additional synth-graph references live in `amy_patch_examples/` (notably
  `sketch_5osc_analog.py`).

## Sketches & Deployment

- **Canonical sketches** live in `sketches/`. Root `sketch.py` is a scratch
  staging copy that gets overwritten freely while experimenting; promote a
  working sketch into `sketches/` once it is solid.
- **`wrapper_sketch.py`** is the global launcher: it is deployed to the firmware
  boot file (`/user/current/sketch.py`), runs a chosen sketch, and drives its
  `loop()`. Its global menu is an in-memory **overlay** over the running sketch —
  **Resume** returns instantly with no audio dropout; only **Load Sketch** resets
  the board. See [docs/MENU.md](docs/MENU.md) for the gesture model and the
  launcher↔sketch input contract.
- **Deploy to internal flash, not the SD card.** The board's FatFs can read but
  not write its large exFAT SD card, so sketches are deployed to
  `/user/sketches` (always writable over serial), which the launcher loads from
  first. The deploy tooling — `deploy_auto.py`, `verify.py`, `board_serial.py` —
  sends the file over the MicroPython REPL and verifies an exact byte-for-byte
  readback. See `DEPLOYMENT_COMMAND.txt`. Typical loop:

  ```
  python deploy_auto.py --sketch sketches/arctor.py            # deploy + verify
  python deploy_auto.py --sketch sketches/arctor.py --activate # deploy + boot into it
  ```


## Documentation

- [Development Guidelines](docs/AGENTS.md) - Agent collaboration rules and architectural guidance
- [Architecture](docs/ARCHITECTURE.md) - High-level system design
- [Menu & Navigation](docs/MENU.md) - On-device menus, gesture model, launcher↔sketch contract
- [WiFi & WebREPL](docs/WIFI.md) - Menu-toggled, auto-on-boot WiFi + WebREPL for wireless access
- [Display Modes](docs/DISPLAY_MODES.md) - Pluggable OLED display modes (CC Monitor, Screensaver, Oscilloscope)
- [CC Mapping](docs/CC_MAPPING.md) - CC → parameter map and live behavior
- [MIDI Mapping](docs/MIDI_MAPPING.md) - Control surface roles and channel assignment
- [Firmware Notes](docs/FIRMWARE_NOTES.md) - Firmware gotchas: audio test, grab_midi_notes, boot-loop recovery
- [E16 Setup](docs/E16_SETUP.md) - Oxi e16 configuration and deployment notes

## Resources

- [AMYboard Documentation](https://github.com/shorepine/tulipcc/blob/main/docs/amyboard/README.md)
- [AMY Language & Hardware](https://github.com/shorepine/amy)
