# AMYboard Sketch
# DESCRIPTION: Load a patch and play it via MIDI; rotary encoder scrolls through all patches, button press loads selected patch.

import amy, amyboard
from patches import patches as PRESETS

NUM_PRESETS = len(PRESETS)
SEESAW = 0x36
BTN_PIN = 24

# Boot synth with default patch
amy.send(synth=1, patch=0, num_voices=6)

# Encoder state
enc_offset = amyboard.read_encoder(seesaw_dev=SEESAW)
current_index = 0
loaded_patch = 0
prev_btn = False

# Display setup
amyboard.init_buttons(pins=(BTN_PIN,), seesaw_dev=SEESAW)
try:
    amyboard.init_display()
    HAS_DISPLAY = True
except Exception:
    HAS_DISPLAY = False

def draw():
    if not HAS_DISPLAY:
        return
    d = amyboard.display
    d.fill(0)
    d.text("PATCH SELECT", 0, 0, 255)
    d.text("%d / %d" % (current_index, NUM_PRESETS - 1), 0, 14, 255)
    name = PRESETS[current_index].strip()
    if len(name) <= 16:
        d.text(name, 0, 30, 255)
    else:
        d.text(name[:16], 0, 30, 255)
        d.text(name[16:32], 0, 42, 255)
    if current_index == loaded_patch:
        d.text("[LOADED]", 0, 56, 255)
    amyboard.display_refresh()

def load_patch(idx):
    global loaded_patch
    amy.send(synth=1, patch=idx, num_voices=6)
    loaded_patch = idx
    draw()

draw()

def loop():
    global current_index, prev_btn

    # Read encoder: seesaw counts down clockwise, so subtract from offset
    raw = enc_offset - amyboard.read_encoder(seesaw_dev=SEESAW)
    idx = raw % NUM_PRESETS

    btn = amyboard.read_buttons(pins=(BTN_PIN,), seesaw_dev=SEESAW)[0]

    if idx != current_index:
        current_index = idx
        draw()

    # Load patch on button release
    if prev_btn and not btn:
        load_patch(current_index)

    prev_btn = btn