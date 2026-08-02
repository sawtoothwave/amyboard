# E16 Configuration

OXI e16 scene definitions for controlling AMYboard.

## Files

- **`arctor.json`** — source definition for the Arctor scene (**edit this**)
- **`../e16 templates/arctor.oxie16`** — compiled scene, sent to the device (finished scenes live in `e16 templates/`)
- **`generate-scene.js`** — compiler, JSON → `.oxie16` (from [brentvatne/oxi-e16-config](https://github.com/brentvatne/oxi-e16-config), plus a one-line patch so per-encoder `mode` reaches the turn action)
- **`scene-schema.json`** — JSON Schema for the `.oxie16` format; the reference for what every field and value means

The Arctor scene replaced an earlier `amyboard.json` (deleted: its CC map predated
Drift, so it still put Cutoff/Reso on CC 28/29) and `merge_scenes.py` (deleted: it
merged AMYboard pages into an existing scene to preserve MFT pages 1-3 — the Arctor
scene is a scene of its own and needs no merge). Both are in git history.

## Build

```bash
node generate-scene.js arctor.json "../e16 templates/arctor.oxie16"   # or: npm run generate
```

No dependencies — plain Node.

## Layout

One page per Arctor param group, in the same row-major order as the on-device knob
grid: **1 OSC** (49) · **2 VCF** (60) · **3 LFO** (37) · **4 VCA** (93) · **5 FX**
(5). Channel 12, all outputs, push-to-reset on every knob.

Full layout, encoder-mode rationale and the editing rules are in
[../docs/E16_SETUP.md](../docs/E16_SETUP.md); the CC map is in
[../docs/CC_MAPPING.md](../docs/CC_MAPPING.md).

The source of truth for CCs, defaults and bipolarity is the `PARAMS` table in
`sketches/arctor.py`. `arctor.json` copies from it; nothing enforces that, so when
`PARAMS` changes, update `arctor.json` too.
