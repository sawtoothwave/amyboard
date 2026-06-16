# E16 Configuration Setup

This guide walks through setting up the OXI e16 to control AMYboard.

## Prerequisites

- OXI e16 controller
- AMYboard synthesizer
- Node.js (LTS recommended)
- MIDI connection between e16 and AMYboard

## Overview

The AMYboard control uses three dedicated pages on the e16:
- **Page 4**: Oscillators + Filter
- **Page 8**: Envelopes (VCF + VCA)
- **Page 12**: LFO

Pages 1-3 are reserved for your existing MFT configuration and will be preserved.

## Configuration Files

### `amyboard.json`
Source definition file describing the e16 pages, knob assignments, and MIDI CC mappings.

Generated from this file: `amyboard.oxie16` (the actual config sent to the device)

## Workflow

### Step 1: Set Up e16-config Locally

```bash
cd amyboard
npm install  # Install generate-scene.js dependencies
```

### Step 2: Generate the AMYboard Scene

```bash
node e16-config/generate-scene.js e16-config/amyboard.json e16-config/amyboard.oxie16
```

This creates `amyboard.oxie16` from the JSON source.

### Step 3: Preserve MFT Pages

Since we want to keep your existing MFT pages (1-3), you need to merge them:

```bash
# Backup the original
cp "e16 templates/MFT replace.oxie16" "e16 templates/MFT replace.oxie16.backup"

# Extract MFT pages and merge with AMYboard pages
python3 e16-config/merge_scenes.py \
  "e16 templates/MFT replace.oxie16" \
  e16-config/amyboard.oxie16 \
  --preserve-pages 0-2 \
  --output "e16 templates/MFT replace.oxie16"
```

### Step 4: Transfer to Device

Transfer `e16 templates/MFT replace.oxie16` to your e16 using the OXI app or your preferred method.

## Important Notes

- **CC Channel**: All AMYboard CCs are on MIDI channel 1
- **Push to Reset**: Pressing any knob returns it to its default value
- **Frequency Defaults**: 
  - Osc A pitch → 440 Hz (center dead zone, CC 60-68)
  - Osc B pitch → 440 Hz (center dead zone, CC 60-68)
  - Both oscillators reference 440 Hz, so they are unison at center; the stepped tuning map adds fifths/octaves away from center.
- **Persistence**: Knob positions are stored on the e16 scene, not on the AMYboard

## CC Reference

See [CC_MAPPING.md](CC_MAPPING.md) for complete MIDI CC assignments and default values.

## AMYboard Sketch

The AMYboard runs `sketch.py` which:
1. Listens to MIDI CCs on channel 1
2. Maps them to AMY synthesizer parameters live (no voice reset on change)
3. Plays channel-1 notes on a 6-voice polyphonic synth
4. Also supports CV1 (1V/oct pitch) and CV2 (gate) for monophonic CV play

To deploy: Upload `sketch.py` to `/user/current/sketch.py` on the AMYboard.

## Troubleshooting

### E16 pages show, but knobs don't control anything
1. Check MIDI channel (should be 1)
2. Verify USB connection between e16 and AMYboard
3. Check AMYboard logs (connect via `mpremote` and look for errors)

### Settings don't persist across power cycles
1. Verify `/user/` directory exists on AMYboard
2. Check for write permission issues in `sketch.py`
3. Look at `amyboard_state.json` on the device

### Wrong frequency mappings
1. Edit the frequency range in `sketch.py` function `cc_to_freq()`
2. Adjust default CC values in `e16-config/amyboard.json`
