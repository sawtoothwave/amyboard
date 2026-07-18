# AMYboard Sketch
# DESCRIPTION: Gamma9001 drum machine with MIDI General MIDI volume control (CC 7 = master volume, 0-127 mapped to 0.0-2.0). Also controllable via rotary encoder. OLED shows kit name, beat, and volume bar.
# Top-level code runs once at boot. loop() is called every 32nd note.
import amy, amyboard, sequencer, midi
import random

# --- Drum synths: one channel per kit ---
KITS = [(384, "TR-808"), (385, "TR-909"), (386, "Linn 9000"),
        (387, "MR-12"), (388, "Tokyo Syn"), (389, "Power")]
kit_idx = 0

amy.send(synth=10, patch=KITS[kit_idx][0], num_voices=6, synth_flags=3)
amy.send(synth=11, patch=385, num_voices=4, synth_flags=3)
amy.send(synth=12, patch=390, num_voices=4, synth_flags=3)

amy.send(reverb="0.52,0.80,0.83")

# --- Master volume state ---
master_volume = 1.0
amy.send(volume=master_volume)

def set_volume(v):
    global master_volume
    master_volume = max(0.0, min(2.0, v))
    amy.send(volume=master_volume)

# --- MIDI CC callback: CC7 = Channel Volume, CC11 = Expression ---
def midi_cb(m):
    if not m or len(m) < 3:
        return
    status = m[0] & 0xF0
    if status == 0xB0:
        cc = m[1]
        val = m[2]
        if cc == 7 or cc == 11:
            set_volume((val / 127.0) * 2.0)
            draw_display()

midi.add_callback(midi_cb)

# --- Rotary encoder ---
enc = amyboard.encoder()
_enc_last = [enc.read(i) for i in range(enc.encoders)]

# --- OLED display ---
d = amyboard.display

def draw_display():
    d.fill(0)
    d.text("DRUM MACHINE", 0, 0, 255)
    d.text(KITS[kit_idx][1], 0, 16, 255)
    d.text("VOL", 0, 40, 180)
    bar_w = int((master_volume / 2.0) * 100)
    d.fill_rect(30, 40, bar_w, 8, 200)
    d.fill_rect(30 + bar_w, 40, 100 - bar_w, 8, 40)
    pct = int(master_volume * 50)
    d.text("%d%%" % pct, 0, 52, 255)
    d.show()

draw_display()

# GM drum note numbers
KICK = 36
CLAP = 39
CHAT = 42
OHAT = 46
TABLA_LO = 47
SHAKER = 42

step = 0
_display_dirty = False

def loop():
    global step, kit_idx, _display_dirty

    for i in range(enc.encoders):
        pos = enc.read(i)
        delta = pos - _enc_last[i]
        _enc_last[i] = pos
        if delta and i == 0:
            set_volume(master_volume + delta * 0.05)
            _display_dirty = True
        if i < enc.leds:
            bright = int((master_volume / 2.0) * 200)
            enc.led(i, 0, bright, int(bright * 0.5))

    step += 1
    bar_step = step % 32

    if bar_step % 8 == 0:
        amy.send(synth=10, note=KICK, vel=1.0)
    if bar_step == 8 or bar_step == 24:
        amy.send(synth=10, note=CLAP, vel=0.8)
    if bar_step % 4 == 0:
        amy.send(synth=11, note=CHAT, vel=0.5)
    if bar_step % 8 == 4:
        amy.send(synth=11, note=OHAT, vel=0.4)
    if bar_step in (2, 10, 18, 26):
        amy.send(synth=12, note=TABLA_LO, vel=0.6)
    if bar_step in (6, 22):
        amy.send(synth=12, note=SHAKER, vel=0.4)

    if step % 128 == 0:
        kit_idx = (kit_idx + 1) % len(KITS)
        amy.send(synth=10, patch=KITS[kit_idx][0])
        _display_dirty = True

    if bar_step == 0 or _display_dirty:
        draw_display()
        _display_dirty = False

# Do not edit. Set automatically by the knobs on AMYboard Online.
_auto_generated_knobs = """
i1ic51,1,0,1,0.1,h%vZ
i1ic52,0,0,1,0,h,%vZ
i1ic54,0,0,1,0,h,,%vZ
h0.549Z
h,0.797Z
h,,0.827Z
"""