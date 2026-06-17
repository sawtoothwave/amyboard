# AMYboard Sketch
# DESCRIPTION: Menu system: set oscillator count (0-3), wave per osc, VCA ADSR, Filter ADSR. Rotary encoder scrolls, button selects/edits. Play via MIDI ch 1.

import amy, amyboard

SEESAW = 0x36
BTN_PIN = 24

WAVES = [
    (amy.SINE,     "Sine"),
    (amy.PULSE,    "Pulse"),
    (amy.SAW_DOWN, "SawDown"),
    (amy.SAW_UP,   "SawUp"),
    (amy.TRIANGLE, "Triangle"),
    (amy.NOISE,    "Noise"),
]
NUM_WAVES = len(WAVES)

num_oscs  = 1
osc_waves = [0, 0, 0]

# VCA ADSR: [attack_ms, decay_ms, sustain_level, release_ms]
vca_adsr = [10, 100, 0.7, 300]

# Filter ADSR: [attack_ms, decay_ms, sustain_level, release_ms]
flt_adsr = [0, 200, 0.0, 100]


def bp_from_adsr(a, d, s, r):
    return "%d,1.0,%d,%.2f,%d,0.0" % (int(a), int(d), s, int(r))


def apply_vca():
    a, d, s, r = vca_adsr
    amy.send(synth=1, osc=0, bp0=bp_from_adsr(a, d, s, r))


def apply_filter():
    a, d, s, r = flt_adsr
    amy.send(synth=1, osc=0, bp1=bp_from_adsr(a, d, s, r))
    amy.send(synth=1, osc=0,
             filter_freq={'const': 800, 'eg1': 800},
             filter_type=amy.FILTER_LPF24,
             resonance=1.0)


def rebuild_synth():
    n = max(num_oscs, 1)
    amy.send(synth=1, num_voices=4, oscs_per_voice=n)
    for i in range(n):
        w = WAVES[osc_waves[i]][0]
        if n > 1:
            pan = 0.2 + 0.6 * i / (n - 1)
        else:
            pan = 0.5
        if i < n - 1:
            amy.send(synth=1, osc=i, wave=w, pan=pan, chained_osc=i + 1)
        else:
            amy.send(synth=1, osc=i, wave=w, pan=pan)
    if num_oscs == 0:
        amy.send(synth=1, osc=0, amp='0,0,0,0,0,0,0')
    apply_vca()
    apply_filter()


# Menu definition
# Each entry: (label, category, sub_index)
# category: "num_oscs", "wave", "vca", "flt"
# sub_index: for wave=osc index 0-2, for vca/flt=adsr index 0-3
MENU_ITEMS = [
    ("Num Oscs",    "num_oscs", 0),
    ("Osc1 Wave",   "wave",     0),
    ("Osc2 Wave",   "wave",     1),
    ("Osc3 Wave",   "wave",     2),
    ("VCA Attack",  "vca",      0),
    ("VCA Decay",   "vca",      1),
    ("VCA Sustain", "vca",      2),
    ("VCA Release", "vca",      3),
    ("Flt Attack",  "flt",      0),
    ("Flt Decay",   "flt",      1),
    ("Flt Sustain", "flt",      2),
    ("Flt Release", "flt",      3),
]
NUM_ITEMS = len(MENU_ITEMS)

MENU_STEPS = {
    "num_oscs": (0,   3,    1,    True),
    "wave":     (0,   NUM_WAVES - 1, 1, True),
    "vca_0":    (1,   2000, 10,   True),
    "vca_1":    (1,   2000, 10,   True),
    "vca_2":    (0.0, 1.0,  0.05, False),
    "vca_3":    (1,   3000, 10,   True),
    "flt_0":    (1,   2000, 10,   True),
    "flt_1":    (1,   2000, 10,   True),
    "flt_2":    (0.0, 1.0,  0.05, False),
    "flt_3":    (1,   3000, 10,   True),
}


def get_menu_key(cat, sub):
    if cat in ("vca", "flt"):
        return cat + "_" + str(sub)
    return cat


def get_val(cat, sub):
    if cat == "num_oscs":
        return float(num_oscs)
    elif cat == "wave":
        return float(osc_waves[sub])
    elif cat == "vca":
        return float(vca_adsr[sub])
    elif cat == "flt":
        return float(flt_adsr[sub])
    return 0.0


def set_val(cat, sub, v):
    global num_oscs
    if cat == "num_oscs":
        num_oscs = int(v)
        rebuild_synth()
    elif cat == "wave":
        osc_waves[sub] = int(v)
        rebuild_synth()
    elif cat == "vca":
        vca_adsr[sub] = v
        apply_vca()
    elif cat == "flt":
        flt_adsr[sub] = v
        apply_filter()


def val_display(cat, sub):
    v = get_val(cat, sub)
    if cat == "wave":
        return WAVES[int(v)][1]
    key = get_menu_key(cat, sub)
    mn, mx, step, is_int = MENU_STEPS.get(key, (0, 1, 1, False))
    if is_int:
        return str(int(v))
    return "%.2f" % v


amyboard.init_display()
amyboard.init_buttons(pins=(BTN_PIN,), seesaw_dev=SEESAW)
d = amyboard.display


def draw(sel, editing):
    d.fill(0)
    if editing:
        d.text("< EDIT >", 0, 0, 255)
    else:
        d.text("MENU  %d/%d" % (sel + 1, NUM_ITEMS), 0, 0, 255)
    label, cat, sub = MENU_ITEMS[sel]
    d.text(label[:16], 0, 14, 255)
    vstr = val_display(cat, sub)
    d.text(vstr[:16], 0, 28, 255)
    if editing:
        d.text("BTN=confirm", 0, 42, 255)
    else:
        d.text("BTN=edit", 0, 42, 255)
    amyboard.display_refresh()


rebuild_synth()

enc_offset    = amyboard.read_encoder(seesaw_dev=SEESAW)
prev_btn      = False
menu_idx      = 0
mode          = 0   # 0=menu nav, 1=edit
edit_enc_base = 0
edit_val_base = 0.0

draw(menu_idx, False)


def loop():
    global enc_offset, prev_btn, menu_idx, mode
    global edit_enc_base, edit_val_base

    raw_enc = amyboard.read_encoder(seesaw_dev=SEESAW)
    delta   = int(enc_offset - raw_enc)
    btn     = amyboard.read_buttons(pins=(BTN_PIN,), seesaw_dev=SEESAW)[0]
    btn_pressed = prev_btn and not btn

    if mode == 0:
        if delta != 0:
            menu_idx   = (menu_idx + delta) % NUM_ITEMS
            enc_offset = raw_enc
            draw(menu_idx, False)
        if btn_pressed:
            label, cat, sub = MENU_ITEMS[menu_idx]
            edit_val_base = get_val(cat, sub)
            edit_enc_base = raw_enc
            enc_offset    = raw_enc
            mode          = 1
            draw(menu_idx, True)
    else:
        label, cat, sub = MENU_ITEMS[menu_idx]
        key = get_menu_key(cat, sub)
        mn, mx, step, is_int = MENU_STEPS.get(key, (0, 1, 1, False))
        enc_delta = int(edit_enc_base - raw_enc)
        new_val   = edit_val_base + enc_delta * step
        if new_val < mn:
            new_val = mn
        if new_val > mx:
            new_val = mx
        if is_int:
            new_val = float(int(round(new_val)))
        cur = get_val(cat, sub)
        if abs(new_val - cur) >= step * 0.49:
            set_val(cat, sub, new_val)
            draw(menu_idx, True)
        if btn_pressed:
            enc_offset = raw_enc
            mode       = 0
            draw(menu_idx, False)

    prev_btn = btn