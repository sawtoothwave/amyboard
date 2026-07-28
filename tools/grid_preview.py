"""Render the polysynth knob grid offline at TRUE 128x128.

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
SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(REPO, 'sketches', 'polysynth.py')
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
    """Panel is 4-bit: keep the top nibble, expand back to 0..255 for viewing."""
    return (int(c) >> 4) * 17


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


def render(group, cursor=0, editing_idx=None, page=0):
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
                              p.default / 127.0, p.bipolar, st)
    ns['_draw_grid_pages'](fb, page, npages)
    return fb.image(), npages


if __name__ == '__main__':
    out = os.path.join(HERE, 'preview_out')
    os.makedirs(out, exist_ok=True)
    for f in os.listdir(out):
        if f.endswith('.png'):
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
    sheet = Image.new('L', (len(imgs) * (W + 6) + 6, H + 12), 30)
    for i, (_, im) in enumerate(imgs):
        sheet.paste(im, (6 + i * (W + 6), 6))
    sheet.save(os.path.join(out, 'sheet.png'))
    print('wrote', out)
