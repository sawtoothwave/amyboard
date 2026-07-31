"""Render the About card offline at TRUE 128x128.

Same fidelity contract as grid_preview.py: the sketch's REAL _about_lines() and
_AboutLevel.render body run against a fake framebuf, with the font grabbed off
the board and greys quantised to the panel's 16 levels. Use it to check the card
still FITS (and still reads) after editing the text.

ONE PLACE IT LIES, measured on hardware 2026-07-30: it renders ABOUT_C_DIM as a
true mid-grey, but the real OLED collapses every sub-255 value into a single dim
tone. Trust this PNG for layout and wrapping; do not trust it for how far apart
the two tiers look. See the note at ABOUT_C_DIM in the sketch.

  python tools/about_preview.py [sketch.py]  ->  tools/preview_out/about.png
"""
import re, sys, os, json
from PIL import Image
from grid_preview import FakeFB, W, H

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(REPO, 'sketches', 'arctor.py')
src = open(SRC).read()

ns = {}
for m in re.finditer(r'^(DISPLAY_WIDTH|CHAR_W|CHAR_H|SKETCH_NAME|VERSION|VERSION_DATE'
                     r'|ABOUT_[A-Z_]+)\s*=\s*([^#\n]+)', src, re.M):
    ns[m.group(1)] = eval(m.group(2).strip(), {}, ns)

for fname, end in (('_about_lines', '\ndef _about_extent'),
                   ('_about_extent', '\nclass _AboutLevel')):
    blk = src[src.index('def %s(' % fname):]
    exec(blk[:blk.index(end)], ns)

fb = FakeFB()
fb.fill(0)
y = ns['ABOUT_TOP_Y']
over = []
for text, bright in ns['_about_lines']():
    if not text:
        y += ns['ABOUT_GAP']
        continue
    if len(text) > ns['ABOUT_MAX_CH']:
        over.append(text)
    fb.text(text[:ns['ABOUT_MAX_CH']], 0, y,
            ns['ABOUT_C_BRIGHT'] if bright else ns['ABOUT_C_DIM'])
    y += ns['ABOUT_LINE_H']

out = os.path.join(HERE, 'preview_out')
os.makedirs(out, exist_ok=True)
fb.image().save(os.path.join(out, 'about.png'))
ext = ns['_about_extent']()
print('about card: %d lines, extent %d/%d px%s'
      % (len(ns['_about_lines']()), ext, H, '  *** OVERFLOWS ***' if ext > H else ''))
for t in over:
    print('  CLIPPED (>%d chars): %r' % (ns['ABOUT_MAX_CH'], t))
print('wrote', os.path.join(out, 'about.png'))
