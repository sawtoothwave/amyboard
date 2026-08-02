# E16 Configuration Setup

The OXI e16 scene for **Arctor**, the AMYboard polysynth (`sketches/arctor.py`).

The scene mirrors Arctor's own Param Control menu: **one e16 page per param group**,
laid out in the same row-major order as the on-device knob grid, so a knob sits where
its cell sits on the OLED. All 50 editable parameters are on the controller.

| e16 page | Group | LED colour | Knobs |
|---|---|---|---|
| 1 | OSC | 49 (red) | 11 |
| 2 | VCF | 60 (red-orange) | 11 |
| 3 | LFO | 37 (amber/brown) | 7 |
| 4 | VCA | 93 (green) | 7 |
| 5 | FX | 5 (blue) | 14 |

Pages 6-12 are empty.

The five indices were picked **on hardware**, off a colour chart loaded onto the
controller — the palette's index→hue mapping isn't documented anywhere, and the
names in the OXI docs don't survive contact with the LEDs. The e16 has no clean
orange or yellow: the warm end of the palette runs red → pink, so VCF and LFO use
the most separable warm tones rather than true orange/yellow. If you need to re-pick
one, regenerate a chart page — 16 encoders, `"color"` set to its own index and
`"lower": 100` so every ring stays lit — on one of the empty pages.

## Page Layout

`*` marks a bipolar knob (centre-detent, reads -63..+64); the number is the CC.
Blank cells are deliberate — they reproduce the section breaks of Arctor's own grid
(see the `section` column of `PARAMS` in `sketches/arctor.py`).

```
PAGE 1  OSC                              PAGE 2  VCF
  ATUN* 20 | AWAV  21 | ADTY 22 | ALVL 23   CUT  74 | RES  71 | ENV* 30 | TYPE 31
  BTUN* 24 | BWAV  25 | BDTY 26 | BLVL 27   ATK  40 | DEC  41 | SUS  42 | REL  43
  DRFT  28 | DRHZ  29 | PORT 34 |    .      SHAP 45 | KBD  32 | VEL  33 |   .

PAGE 3  LFO                              PAGE 4  VCA
  RATE  76 | SHAP  78 |   .    |   .        ATK  73 | DEC  75 | SUS  79 | REL  72
  VIB   77 | PWM   83 | FLT 80 |   .        SHAP 46 | VEL  44 | VOL  84 |   .
  TRMA  81 | TRMB  82 |   .    |   .

PAGE 5  FX
  EQLO* 85 | EQMD* 86 | EQHI* 87 |   .
  CHLV  90 | CHDP  91 | CHHZ  92 |   .
  ECLV  95 | ECTM  96 | ECFB  97 | ECTN 98
  RVLV 100 | RVDC 101 | RVDP 102 | RVXO 103
```

What each CC does is in [CC_MAPPING.md](CC_MAPPING.md). The knob abbreviations are
4 characters (the e16's limit) and, unlike Arctor's on-device grid, carry their own
context — the e16 has no section headers above a row, so `ATK`/`SHAP`/`VEL` are
disambiguated only by the page they sit on, and the osc controls take an A/B prefix.

## Scene Behaviour

- **MIDI channel 12**, set once per page; every action inherits it (`channel: 0` =
  "use page channel").
- **All outputs.** Page `output: 0` = ALL (TRS1, TRS2, USB, BLE); every action
  inherits it (`output: 12` = "use page output").
- **Push = reset to INIT.** Every knob's push action is `SetToDefault` (type 4) and
  its `defaultValue` is copied from that parameter's `default` in Arctor's `PARAMS`
  table — so a push sends exactly the value a fresh INIT patch has (Cutoff → 127,
  Osc A Level → 127, Osc B Level → 0, Master Level → 84 ≈ +12 dB, effects → off).
- **Bipolar knobs** (`bipolar: true`, default 64): Osc A/B Tune, Filter Env Amount,
  and the three EQ bands. These are the six parameters flagged `bipolar=True` in
  `PARAMS`; everything else is unipolar, including Drift Amount and Vel→Filter,
  which are 0-at-the-bottom depths rather than centre-zero.
- **Encoder modes.** Most knobs are `mode 3` (1 CC per detent). Two exceptions, both
  mirroring the `stepped` flag in `PARAMS`:
  - `mode 9` (3 CC/detent) on the parameters Arctor reads as a few wide discrete
    buckets — Osc A/B Wave and LFO Wave (21 CCs per wave), Filter Type and the two
    envelope Shapes (32 CCs per bucket). Without it, changing one filter type takes
    32 detents; with it, 11.
  - `mode 5` (Acc2) on Osc A/B Tune, whose map is wide octave/fifth bands around
    narrow ±35-cent detune wings: a slow turn still resolves single cents, a fast
    one crosses an octave.

### What `mode` actually does — MEASURED

Both published sources are wrong about this field, so it was probed on the hardware
(a scratch page carrying the same knob at all 11 modes, one per encoder):

| `mode` | Measured behaviour |
|---|---|
| 0, 1, 2 | Slower than 1 CC/detent (division) |
| 3-6 | 1 CC/detent, with progressively more acceleration on fast turns |
| 7 | Above 1 CC/detent even on a slow turn |
| 8 | **2 CC/detent** |
| 9 | **3 CC/detent — the largest step the e16 will send** |
| 10 | Ignored; falls back to 1 CC/detent |

The OXI skill doc's `LSp2 / LSp4 / LSp6 = 2x / 4x / 6x` for modes 8/9/10 is not what
the firmware does — 9 steps by 3, and 10 is out of range. The published JSON Schema
is closer (it caps `mode` at 7) but wrong the other way, since 8 and 9 do work; the
copy in `e16-config/scene-schema.json` is widened to 0-10 to match.

**Why this can't match the on-device editor.** Param Control steps in *bucket index*
space — `_bucket_advance` moves one entry in a param's `steps` table, so one detent
is always exactly one wave. The e16 sends absolute CC values and knows nothing about
where the boundaries are, so the best it can do is 3 CCs per detent: ~7 detents per
wave, ~11 per filter type. The step is well under the narrowest 21-CC bucket, so no
bucket can ever be stepped over from any starting value — it is slower than the
board's own encoder, never wrong. Closing that gap would take relative-CC
("nudge") parameters in the sketch, which is deliberately not built.

## Files

| File | Role |
|---|---|
| `e16-config/arctor.json` | Source definition — edit this |
| `e16 templates/arctor.oxie16` | Compiled scene — send this to the device |
| `e16-config/generate-scene.js` | Compiler (JSON → `.oxie16`), from [brentvatne/oxi-e16-config](https://github.com/brentvatne/oxi-e16-config) |
| `e16-config/scene-schema.json` | JSON Schema for the `.oxie16` format (field/value reference) |

## Workflow

```bash
node e16-config/generate-scene.js e16-config/arctor.json "e16 templates/arctor.oxie16"
```

Then transfer `e16 templates/arctor.oxie16` to the e16 with the OXI app. Finished
scenes live in `e16 templates/` alongside the other ones; `e16-config/` holds the
source and the toolchain.

The generator needs no dependencies (plain Node, no `npm install`).

## Editing

Change a knob in `arctor.json` and regenerate. Encoder fields:

```json
{"abbr": "CUT", "name": "Cutoff", "cc": 74, "default": 127, "color": 46}
{"abbr": "ENV", "name": "Flt Env", "cc": 30, "default": 64, "bipolar": true, "color": 46}
{"abbr": "TYPE", "name": "Flt Type", "cc": 31, "default": 48, "mode": 10, "color": 46}
```

`abbr` ≤ 4 chars, `name` ≤ 8 chars, `null` for an empty slot (16 per page, 12 pages).

**When Arctor's `PARAMS` table changes, update `arctor.json` to match** — the CC,
the default and the bipolar flag all come from there, and nothing enforces it.

## Notes

- Knob positions live in the e16 scene, not on the AMYboard. Arctor keeps its own
  copy of every value (presets, `amyboard_state.json`), so after a power cycle the
  two can disagree until a knob is touched.
- The synth also accepts these CCs from anything else on channel 12 — the e16 is not
  privileged.
- Arctor deploys with `python deploy_auto.py --sketch sketches/arctor.py`
  (see `DEPLOYMENT_COMMAND.txt`).

## Troubleshooting

**Pages show, but knobs do nothing.** Check the e16 is on channel 12 and that the
scene's pages are the Arctor ones (the OXI app shows the page channel). Check the
MIDI cable/USB link, then Arctor's CC monitor display mode — it lists incoming CCs,
so if a knob's CC never appears the message is not arriving.

**A knob moves the wrong parameter.** The scene and the sketch disagree; regenerate
from `arctor.json` and compare against `PARAMS` in `sketches/arctor.py`.

**Scene fails to load silently in the OXI app.** Almost always a malformed action
object — every action needs all 11/12 fields, and encoder keys must be in the order
`name, abbr, color, push_action, turn_actions, bipolar`. The generator handles both;
hand-editing the `.oxie16` does not.

One version wrinkle, if the app does reject this scene: encoders end with
`"bipolar": false` here (what the schema requires and what current scenes use), but
the older export in `e16 templates/MFT replace.oxie16` ends with `"color2": 0`
instead. If your app is on the older form, rename that one key in
`generate-scene.js` (`emptyEncoder` and `buildEncoder`) and regenerate — at which
point the bipolar knobs lose their centre display, since that firmware has no
bipolar flag.

**Validating a scene before sending it:**

```bash
python -c "import json,jsonschema; \
  jsonschema.validate(json.load(open('e16 templates/arctor.oxie16')), \
                      json.load(open('e16-config/scene-schema.json')))"
```
