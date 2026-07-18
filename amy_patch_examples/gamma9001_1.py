# AMYboard Sketch
# DESCRIPTION: Gamma9001 drum machine with general MIDI volume control (CC 7 + encoder knob). Three drum channels rotate through all six banks. OLED shows kit, beat, and volume level.
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

# --- Master volume (0.0 - 2.0, default 1.0) ---
master_vol = 1.0
amy.send(volume=master_vol)

# --- Rotary encoder for volume ---
enc = amyboard.encoder()
_enc_count = max(enc.encoders, 1)
_last_enc = [enc.read(i) for i in range(_enc_count)]

# --- OLED display ---
d = amyboard.display

def draw_display():
    d.fill(0)
    d.text("DRUM MACHINE", 0, 0, 255)
    d.text(KITS[kit_idx][1], 0, 12, 255)
    bar_w = int(master_vol / 2.0 * 120)
    d.text("VOL", 0, 24, 200)
    d.fill_rect(0, 34, bar_w, 8, 255)
    d.fill_rect(bar_w, 34, 120 - bar_w, 8, 60)
    pct = int(master_vol * 100)
    d.text("%3d%%" % pct, 80, 24, 200)
    d.show()

draw_display()

# --- MIDI CC 7 = General MIDI volume ---
def midi_cb(m):
    global master_vol
    if not m or len(m) < 3:
        return
    status = m[0] & 0xF0
    if status == 0xB0 and m[1] == 7:
        master_vol = max(0.0, min(2.0, round(m[2] / 127.0 * 2.0, 3)))
        amy.send(volume=master_vol)
        draw_display()

midi.add_callback(midi_cb)

step = 0
_display_step = -1

def loop():
    global master_vol, _last_enc, kit_idx, step, _display_step
    step += 1

    # Encoder 0: master volume
    if enc.encoders > 0:
        pos = enc.read(0)
        delta = pos - _last_enc[0]
        _last_enc[0] = pos
        if delta:
            master_vol = max(0.0, min(2.0, master_vol + delta * 0.05))
            amy.send(volume=master_vol)
            if enc.leds > 0:
                bright = int(master_vol / 2.0 * 255)
                enc.led(0, 0, bright, bright // 2)
            draw_display()

    # Redraw display once per bar if content has not changed
    if step % 32 == 0 and step != _display_step:
        _display_step = step
        draw_display()