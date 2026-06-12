# E16 Configuration

This directory contains OXI e16 scene definitions and generation tools for controlling AMYboard.

## Files

- **`amyboard.json`** - Source definition for the AMYboard control scene
- **`amyboard.oxie16`** - Compiled scene file (generated from JSON)
- **`merge_scenes.py`** - Utility to merge scenes while preserving pages
- **`generate-scene.js`** - Node.js script to compile JSON → .oxie16 (from brentvatne/oxi-e16-config)

## Quick Start

### 1. Install Dependencies

```bash
npm install
```

### 2. Generate Scene

```bash
node generate-scene.js amyboard.json amyboard.oxie16
```

This creates `amyboard.oxie16` from the JSON source file.

### 3. Merge with MFT Pages (Optional)

If you want to preserve your existing MFT configuration:

```bash
python3 merge_scenes.py \
  ../e16\ templates/MFT\ replace.oxie16 \
  amyboard.oxie16 \
  --preserve-pages 0-2 \
  --output ../e16\ templates/MFT\ replace.oxie16
```

### 4. Transfer to Device

Use the OXI app or your preferred method to load the `.oxie16` file onto your e16.

## Editing the Configuration

Edit `amyboard.json` to change:
- Knob names and abbreviations
- MIDI CC assignments
- Default values
- Page organization

Then regenerate: `node generate-scene.js amyboard.json amyboard.oxie16`

## Page Layout

The current configuration uses:

- **Page 0**: Empty (placeholder for future use)
- **Page 1**: Empty (placeholder)
- **Page 2**: Empty (placeholder)
- **Page 3**: Oscillators + Filter (14 knobs)
- **Page 4**: Modulators - Envelopes + LFO (15 knobs)
- **Page 5-10**: Empty
- **Page 11**: Effects (12 knobs)

*Note: The OXI app displays pages as 1-indexed, so these become pages 1-12.*

## Notes on MFT Preservation

If you created your MFT scene with the OXI app directly, it won't have a JSON source file. The `merge_scenes.py` script provides a way to preserve those pages when adding AMYboard controls.

Alternatively:
1. Create a new scene with just pages 4, 8, 12 defined
2. Manually import MFT pages into your device separately
3. Copy/restore MFT pages on the e16 itself

## References

- [brentvatne/oxi-e16-config](https://github.com/brentvatne/oxi-e16-config) - E16 configuration framework
- [../docs/CC_MAPPING.md](../docs/CC_MAPPING.md) - Complete CC assignments
- [../docs/E16_SETUP.md](../docs/E16_SETUP.md) - Setup guide
