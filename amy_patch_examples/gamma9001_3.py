# AMYboard Sketch
# DESCRIPTION: Gamma9001 drum machine with General MIDI master volume control via MIDI CC 7 and rotary encoder. The OLED shows kit name, beat, and current volume level.
# Top-level code runs once at boot. loop() is called every 32nd note.
import amy, amyboard, sequencer, midi
import random

# --- Master volume state (0.0 - 2.0, default 1.0) ---
master_volume = 1.0
amy.send(volume=master_volume)

# --- Drum synths: one channel per kit ---
KITS = [(384, "TR-808"), (385, "TR-909"), (386, "Linn 9000"),
        (387, "MR-12"), (388, "Tokyo Syn"), (389, "Power")]
kit_idx = 0

amy.send(synth=10, patch=KITS[kit_idx][0], num_voices=6, synth_flags=3)
amy.send(synth=11, patch=385, num_voices=4, synth_flags=3)
amy.send(synth=12, patch=390, num_voices=4, synth_flags=3)

amy.send(reverb="0.52,0.80,0.83")

# --- Rotary encoder for volume control ---
enc = amyboard.encoder()
_enc_last = [enc.read(i) for i in range(enc.encoders)]

# --- OLED display ---
d = amyboard.display

def draw_display():
    d.fill(0)
    d.text("DRUM MACHINE", 0, 0, 255)
    d.text(KITS[kit_idx][1], 0, 16, 255)
    d.text("VOL:", 0, 32, 255)
    bar_w = min(int(master_volume * 80), 96)
    d.fill_rect(28, 32, bar_w, 7, 200)
    pct_str = str(int(master_volume * 100)) + "%"
    d.text(pct_str, 0, 44, 180)
    d.show()

draw_display()

# --- MIDI CC 7 = General MIDI channel volume -> master volume ---
def midi_cb(m):
    global master_volume
    if not m or len(m) < 3:
        return
    status = m[0] & 0xF0
    if status == 0xB0:
        cc = m[1]
        val = m[2]
        if cc == 7:
            master_volume = val / 127.0 * 2.0
            amy.send(volume=master_volume)
            draw_display()

midi.add_callback(midi_cb)

# GM drum note numbers
KICK, SNARE, CLAP = 36, 38, 39
CHAT, OHAT = 42, 46
SHAKER, TABLA_LO, TABLA_HI = 42, 47, 50

# --- Step sequencer ---
sequencer.tempo(120)

step = 0
display_dirty = False

def loop():
    global step, kit_idx, master_volume, display_dirty
    step += 1

    # --- Encoder 0 controls master volume ---
    for i in range(enc.encoders):
        pos = enc.read(i)
        delta = pos - _enc_last[i]
        _enc_last[i] = pos
        if delta and i == 0:
            master_volume = max(0.0, min(2.0, master_volume + delta * 0.05))
            amy.send(volume=master_volume)
            display_dirty = True
        if i < enc.leds:
            brightness = int(min(master_volume / 2.0, 1.0) * 200)
            enc.led(i, 0, brightness, brightness // 2)

    bar_step = step % 32

    # Kit rotation every 4 bars
    if bar_step == 0 and step % 128 == 0 and step > 0:
        kit_idx = (kit_idx + 1) % len(KITS)
        amy.send(synth=10, patch=KITS[kit_idx][0])
        display_dirty = True

    # Four-on-the-floor kick
    if bar_step % 8 == 0:
        amy.send(synth=10, note=KICK, vel=1.0)

    # Snare on 2 and 4
    if bar_step in (8, 24):
        amy.send(synth=10, note=SNARE, vel=0.9)

    # Clap layered on snare
    if bar_step in (8, 24):
        amy.send(synth=11, note=CLAP, vel=0.7)

    # Closed hats: 8th notes
    if bar_step % 4 == 0:
        amy.send(synth=11, note=CHAT, vel=0.5)

    # Open hat offbeat
    if bar_step % 8 == 4:
        amy.send(synth=11, note=OHAT, vel=0.4)

    # Percussion layer
    if bar_step % 6 == 0:
        amy.send(synth=12, note=SHAKER, vel=0.35)
    if bar_step in (10, 22):
        amy.send(synth=12, note=TABLA_LO, vel=0.5)
    if bar_step in (18, 28):
        amy.send(synth=12, note=TABLA_HI, vel=0.45)

    # Display refresh once per bar or when volume changed
    if bar_step == 0 or display_dirty:
        draw_display()
        display_dirty = False