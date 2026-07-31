"""Render Arctor's knob grid offline at TRUE 128x128.

Fidelity:
  - geometry: exact. We exec the sketch's real _grid_layout/_draw_grid_* against a
    fake framebuf, so every position/size is whatever the shipped code computes.
  - font: exact. font8x8.json was read OFF THE BOARD by rendering each glyph into an
    in-RAM framebuf and reading the pixels back (see grabfont.py).
  - greys: quantised to the panel's 16 levels (it is 4-bit, top nibble only), so a
    dim level here is the dim level the panel can actually show.
Remaining differences from the real thing: OLED pixel bloom/contrast, and nothing else
of substance.
"""
import re, sys, os, json
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(REPO, 'sketches', 'arctor.py')
src = open(SRC).read()

W = H = 128
FONT = {k: v for k, v in json.load(open(os.path.join(HERE, 'font8x8.json'))).items()}

ns = {}
for m in re.finditer(r'^(DISPLAY_WIDTH|CHAR_W|GRID_[A-Z_0-9]+)\s*=\s*([^#\n]+)', src, re.M):
    try:
        ns[m.group(1)] = eval(m.group(2).strip(), {}, ns)
    except Exception:
        pass
ns['clamp'] = lambda v, lo, hi: lo if v < lo else (hi if v > hi else v)

for fname, end in (('_grid_layout', '\ndef _draw_grid_section'),
                   ('_draw_grid_section', '\ndef _grid_disp'),
                   ('_draw_grid_cell', '\ndef _draw_grid_header'),
                   ('_draw_grid_header', '\ndef _draw_page_dots'),
                   ('_draw_page_dots', '\ndef _draw_grid_pages'),
                   ('_draw_grid_pages', '\nclass _GridLevel')):
    blk = src[src.index('def %s(' % fname):]
    blk = blk[:blk.index(end)]
    exec(blk, ns)


def q(c):
    """Quantise a colour the way the BOARD does, then expand for viewing.

    MicroPython's GS4_HMSB framebuf masks the colour to its LOW nibble
    (`col & 0x0f`) -- colour here is a 0..15 level, not a 0..255 intensity.
    VERIFIED on hardware 2026-07-30 by drawing text at a spread of values and
    reading the nibbles back out of `display._hw.buffer`: 255 -> 15, 110 -> 14,
    244 -> 4, 215 -> 7, 20 -> 4.

    This used to take the TOP nibble (`c >> 4`), which is what a 0..255 intensity
    would imply and is wrong. That single line is why every preview this project
    ever rendered showed tones the panel was not drawing -- notably the About
    card's "dim" 110, which previewed as a mid-grey but renders one step off white.
    Any constant above 15 is a bug at the call site; the mask hides it, so the
    assert does not fire on it -- see the note at ABOUT_C_DIM in the sketch.
    """
    return (int(c) & 0x0F) * 17


class FakeFB:
    def __init__(self):
        self.px = [[0] * W for _ in range(H)]

    def _set(self, x, y, c):
        if 0 <= x < W and 0 <= y < H:
            self.px[y][x] = c

    def fill(self, c):
        c = q(c)
        for y in range(H):
            for x in range(W):
                self.px[y][x] = c

    def fill_rect(self, x, y, w, h, c):
        c = q(c)
        for yy in range(int(y), int(y + h)):
            for xx in range(int(x), int(x + w)):
                self._set(xx, yy, c)

    def text(self, s, x, y, c):
        c = q(c)
        for i, ch in enumerate(s):
            g = FONT.get(ch)
            if not g:
                continue
            gx = int(x) + i * ns['CHAR_W']
            for ry in range(8):
                for rx in range(8):
                    if g[ry][rx]:
                        self._set(gx + rx, int(y) + ry, c)

    def image(self):
        im = Image.new('L', (W, H))
        im.putdata([v for row in self.px for v in row])
        return im


class P:
    # Mirrors the sketch's _Param closely enough for _grid_layout. If _Param grows a
    # field that _grid_layout reads, add it here too -- the parser below is regex, not
    # an import, so a new field fails loudly rather than defaulting.
    def __init__(s, label, cc, group, section, bipolar, default, newrow, grid,
                 hdr='', halfcol=False):
        s.label, s.cc, s.group, s.section = label, cc, group, section
        s.bipolar, s.default, s.newrow, s.grid = bipolar, default, newrow, grid
        s.hdr, s.halfcol = hdr, halfcol      # split header name / half-column shift


body = src[src.index('PARAMS = ['):]
body = body[:body.index('\n]')]
params = []
# Positional columns: label, cc, default, grid (then to_val/store/update, which the
# preview doesn't need -- they land in the keyword tail and are ignored).
for m in re.finditer(r"_Param\(\s*'([^']*)'\s*,\s*(CC_[A-Z0-9_]+)\s*,\s*(\d+)\s*,"
                     r"\s*'([^']*)'(.*?)\),\s*$",
                     body, re.M):
    tail = m.group(5)
    g = re.search(r"group='([^']*)'", tail)
    sec = re.search(r"section='([^']*)'", tail)
    hdr = re.search(r"hdr='([^']*)'", tail)
    params.append(P(m.group(1), m.group(2), g.group(1) if g else '',
                    sec.group(1) if sec else '', 'bipolar=True' in tail, int(m.group(3)),
                    'newrow=True' in tail, m.group(4),
                    hdr.group(1) if hdr else '', 'halfcol=True' in tail))

# A row the regex fails to match would silently VANISH from the preview (that is how
# regex parsing fails); refuse to render from a partial table instead.
n_rows = body.count('_Param(')
if len(params) != n_rows:
    sys.exit('grid_preview: parsed %d of %d _Param rows -- the PARAMS regex no '
             'longer matches the row shape; fix the parser before trusting a PNG'
             % (len(params), n_rows))
# What the top-right readout shows for each group's first (focused) param, i.e. what
# that param's fmt would produce at its default. Hand-kept: the preview parses PARAMS
# with regexes and cannot call the sketch's fmt_* functions.
FOCUS_VALUE = {'Osc': 'Unison', 'VCF': '127', 'LFO': '0.20 Hz', 'VCA': '0', 'FX': '0.0dB'}


# CC name -> number, so the hover reveal (which draws '#<cc>') can be previewed. The
# parser above keeps _Param's cc column as the SYMBOL ('CC_CUTOFF'); resolve it here
# from the module-level constants rather than duplicating the numbers.
CC_NUMS = {m.group(1): int(m.group(2))
           for m in re.finditer(r"^(CC_[A-Z0-9_]+)\s*=\s*(\d+)", src, re.M)}


def render(group, cursor=0, editing_idx=None, page=0, reveal=False):
    ps = [p for p in params if p.group == group]
    cells, heads, npages = ns['_grid_layout'](ps)
    fb = FakeFB()
    fb.fill(0)
    ns['_draw_grid_header'](fb, group.upper(), FOCUS_VALUE.get(group, '64'))
    for hp, hy, ht in heads:
        if hp == page:
            ns['_draw_grid_section'](fb, hy, ht)
    for i, p in enumerate(ps):
        cp, cx, cy = cells[i]
        if cp != page:
            continue
        st = 'none'
        if i == cursor:
            st = 'cursor'
        if editing_idx is not None and i == editing_idx:
            st = 'selected'
        ns['_draw_grid_cell'](fb, cx, cy, p.grid,
                              p.default / 127.0, p.bipolar, st,
                              ('#%d' % CC_NUMS[p.cc]) if (reveal and st == 'cursor')
                              else None)
    ns['_draw_grid_pages'](fb, page, npages)
    return fb.image(), npages


if __name__ == '__main__':
    out = os.path.join(HERE, 'preview_out')
    os.makedirs(out, exist_ok=True)
    # Clear only OUR stale PNGs -- a blanket wipe also deleted about_preview.py's
    # card, so whichever tool ran second silently owned the directory.
    for f in os.listdir(out):
        if f.endswith('.png') and not f.startswith('about'):
            os.remove(os.path.join(out, f))
    imgs = []
    for group in ('Osc', 'VCF', 'LFO', 'VCA', 'FX'):
        _, npages = render(group, 0, None, 0)
        for pg in range(npages):
            img, _ = render(group, 0 if pg == 0 else None, None, pg)
            name = group if npages == 1 else '%s_p%d' % (group, pg + 1)
            img.save(os.path.join(out, '%s.png' % name))
            imgs.append((name, img))
            print('%-10s page %d/%d -> %s.png' % (group, pg + 1, npages, name))
    img, _ = render('Osc', 5, 5, 0)
    img.save(os.path.join(out, 'Osc_selected.png'))
    imgs.append(('Osc_selected', img))
    print('Osc_selected -> Osc_selected.png')
    # Hover CC reveal, on the WORST case: the widest CC number ('#103' fills all 32px
    # of the cell), with neighbours drawn either side so the adjacency is real.
    worst = max(params, key=lambda p: CC_NUMS[p.cc])
    wps = [p for p in params if p.group == worst.group]
    wi = wps.index(worst)
    wpage = ns['_grid_layout'](wps)[0][wi][0]
    img, _ = render(worst.group, wi, None, wpage, reveal=True)
    img.save(os.path.join(out, 'hover_cc.png'))
    imgs.append(('hover_cc', img))
    print('hover CC #%d on %s/%s -> hover_cc.png'
          % (CC_NUMS[worst.cc], worst.group, worst.grid))
    sheet = Image.new('L', (len(imgs) * (W + 6) + 6, H + 12), 30)
    for i, (_, im) in enumerate(imgs):
        sheet.paste(im, (6 + i * (W + 6), 6))
    sheet.save(os.path.join(out, 'sheet.png'))
    print('wrote', out)
