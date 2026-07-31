"""Render every unique Arctor screen at TRUE 128x128, for the docs.

Same fidelity contract as grid_preview.py, extended to the whole app: this execs
the REAL sketch (via grid_sim's stubs), swaps amyboard.display for a framebuf
that records pixels, then drives the actual SketchMenu and DisplayModes and
snapshots what they draw. Nothing here re-implements a screen -- if a layout
changes in the sketch, these PNGs change with it.

  - geometry: exact (the shipped draw code runs)
  - font: exact (font8x8.json, grabbed off the board)
  - greys: exact (quantised `col & 0x0F` the way the GS4 framebuf does)

What it does NOT capture: OLED bloom/contrast, and anything time-varying mid
animation (the screensaver dot is caught at one arbitrary step).

  python3 tools/screencaps.py        ->  docs/screens/*.png + a contact sheet
                                         and the markdown to paste into arctor.md
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from PIL import Image
import grid_sim as gs
from grid_preview import FakeFB, W, H

OUT = os.path.join(REPO, 'docs', 'screens')

# Presets seeded before rendering, so list screens show something real rather
# than an empty box. Ten of them so the Load list spills onto a second page and
# the page indicator is visible in at least one capture.
SEED = ['bell pad', 'brass stab', 'chimey', 'deep bass', 'ehry',
        'glass keys', 'noisy pwm', 'padwarb', 'soft lead', 'wobbler']


class RecordingFB(FakeFB):
    """grid_preview's framebuf plus the _hw attribute the sketch pokes at."""

    class _HW:
        width = 128
        col_addr = (0, 63)
        row_addr = (0, 127)

        def __init__(self):
            self.buffer = bytearray(128 * 128 // 2)

        def write_cmd(self, c):
            pass

        def write_data(self, b):
            pass

    def __init__(self):
        FakeFB.__init__(self)
        self._hw = RecordingFB._HW()


def _snap(fb, name, shots):
    img = fb.image()
    img.save(os.path.join(OUT, name + '.png'))
    shots.append((name, img))
    print('  %-24s -> docs/screens/%s.png' % (name, name))
    return img


def main():
    os.makedirs(OUT, exist_ok=True)
    for f in os.listdir(OUT):
        if f.endswith('.png'):
            os.remove(os.path.join(OUT, f))

    ns = gs.load()
    gs._patch_push(ns)
    import amyboard
    fb = RecordingFB()
    amyboard.display = fb
    ns['DISPLAY_OK'] = True

    # Seed presets + a "current" one so the Save chooser and Overwrite confirm
    # have a real name to show.
    ns['_presets'][:] = [{'name': n, 'cc': {}} for n in SEED]
    ns['_write_presets'] = lambda: True
    ns['_set_setting'] = lambda k, v: None
    ns.setdefault('_current_preset_name', '')
    ns['_current_preset_name'] = 'padwarb'

    SketchMenu = ns['SketchMenu']
    menu = ns['menu']
    shots = []

    def clear():
        fb.fill(0)

    def paint(level_owner=None):
        """Force a full repaint of the current level and draw it."""
        menu.dirty = True
        menu._needs_clear = True
        menu._prev = None
        cur = menu.cur
        for attr in ('full', 'dirty'):
            if hasattr(cur, attr):
                setattr(cur, attr, True)
        menu._edit_last_render = 0
        cur.render(menu)

    print('\n--- Playing (display modes)')
    # CC Monitor: feed a few CCs so it has rows to show.
    mode = ns['CC_MONITOR_MODE']
    ns['set_display_mode'](mode)
    import time as _t
    now = _t.ticks_ms()
    for cc, val in ((74, 96), (71, 40), (20, 64), (34, 12)):
        try:
            mode.on_cc(cc, val)
        except Exception as e:
            print('    (on_cc %d failed: %r)' % (cc, e))
    clear()
    # Render repeatedly WITHOUT resetting mode.prev: the monitor caps rows per
    # refresh and drains the rest over later calls, so clearing prev each time
    # makes it redraw row 0 forever and only one entry ever lands.
    for _ in range(8):
        mode.render(_t.ticks_ms())
    _snap(fb, '01-cc-monitor', shots)

    mode = ns['SCREENSAVER_MODE']
    ns['set_display_mode'](mode)
    clear()
    for _ in range(3):
        mode.render(_t.ticks_ms())
    _snap(fb, '02-screensaver', shots)

    print('\n--- Menu')
    menu.open()
    clear(); paint(); _snap(fb, '03-menu-root', shots)

    # Param Control: the group list, then the grid in each of its states.
    dict(menu.cur.items)['Param control']()
    clear(); paint(); _snap(fb, '04-param-groups', shots)

    dict(menu.cur.items)['Osc']()
    grid = menu.cur
    clear(); paint(); _snap(fb, '05-grid-cursor', shots)

    grid.idx = 5
    menu.handle(0, True, False)           # click = select the cell for editing
    clear(); paint(); _snap(fb, '06-grid-selected', shots)
    menu.handle(0, False, True)           # back out of edit, keep the grid

    # Hover CC reveal: age the level's own hover clock past the threshold.
    grid.editing = False
    grid.hover_at = _t.ticks_ms() - (ns['HOVER_REVEAL_MS'] + 100)
    grid.hover_shown = False
    clear(); paint()
    grid.hover_at = _t.ticks_ms() - (ns['HOVER_REVEAL_MS'] + 100)
    grid.hover_shown = False
    menu._edit_last_render = 0
    grid.render(menu)
    _snap(fb, '07-grid-hover-cc', shots)

    # A multi-page group, second page, so the page indicator is captured.
    menu.handle(0, False, True)            # back to the group list
    dict(menu.cur.items)['FX']()
    grid = menu.cur
    if getattr(grid, 'npages', 1) > 1:
        grid.idx = len(grid.params) - 1    # last cell => last page
    clear(); paint(); _snap(fb, '08-grid-page2', shots)
    menu.handle(0, False, True)
    menu.handle(0, False, True)            # back to root

    # Every parameter group, every page -- one image per screen you can actually
    # land on. Named rather than numbered ON PURPOSE: the numbered captures above
    # are already linked from arctor.md, and inserting these into that sequence
    # would renumber everything after them and silently break those links.
    print('\n--- Every parameter page')
    groups = ns['PARAM_GROUPS']
    for g in groups:
        menu.stack = [menu._root()]
        dict(menu.cur.items)['Param control']()
        dict(menu.cur.items)[g]()
        lvl = menu.cur
        npages = max(c[0] for c in lvl.cells) + 1
        for pg in range(npages):
            # Put the cursor on the first cell OF THIS PAGE, so each capture shows
            # the page it names rather than paging back to wherever idx last was.
            lvl.idx = next(i for i, c in enumerate(lvl.cells) if c[0] == pg)
            lvl.editing = False
            lvl.hover_shown = False
            lvl.hover_at = _t.ticks_ms()
            name = 'grid-%s' % g.lower()
            if npages > 1:
                name += '-p%d' % (pg + 1)
            clear(); paint(); _snap(fb, name, shots)
    menu.stack = [menu._root()]

    print('\n--- Presets')
    dict(menu.cur.items)['Save as preset']()
    clear(); paint(); _snap(fb, '09-save-chooser', shots)

    dict(menu.cur.items)['Save as new']()
    lvl = menu.cur
    lvl.name = 'brassy'
    lvl.sel = 4
    clear(); paint(); _snap(fb, '10-name-entry', shots)
    menu.handle(0, False, True)            # cancel name entry

    dict(menu.cur.items)['Overwrite']()
    clear(); paint(); _snap(fb, '11-overwrite-confirm', shots)
    menu.handle(0, False, True)
    menu.handle(0, False, True)            # back to root

    dict(menu.cur.items)['Load preset']()
    clear(); paint(); _snap(fb, '12-load-preset', shots)
    menu.cur.idx = 8                       # cross onto page 2
    clear(); paint(); _snap(fb, '13-list-page2', shots)
    menu.handle(0, False, True)

    # Scan opens ON the currently-loaded preset (that is the feature -- scanning
    # starts where the patch already is). Point "current" at a page-1 name first,
    # or the capture opens on page 2 and reads like a paging bug.
    ns['_current_preset_name'] = 'chimey'
    dict(menu.cur.items)['Scan presets']()
    clear(); paint(); _snap(fb, '14-scan-presets', shots)
    menu.handle(0, False, True)
    ns['_current_preset_name'] = 'padwarb'

    dict(menu.cur.items)['Delete preset']()
    clear(); paint(); _snap(fb, '15-delete-preset', shots)
    dict(menu.cur.items)['chimey']()
    clear(); paint(); _snap(fb, '16-delete-confirm', shots)
    menu.handle(0, False, True)
    menu.handle(0, False, True)

    print('\n--- Display mode + About')
    dict(menu.cur.items)['Display mode']()
    clear(); paint(); _snap(fb, '17-display-mode', shots)
    menu.handle(0, False, True)

    dict(menu.cur.items)['About']()
    clear(); paint(); _snap(fb, '18-about', shots)
    menu.handle(0, False, True)

    print('\n--- Toast')
    clear()
    menu._draw_toast('PRESET SAVED!')
    _snap(fb, '19-toast', shots)

    # Contact sheet: 5 across, labelled by index, at 1x with a gutter.
    cols = 5
    rows = (len(shots) + cols - 1) // cols
    pad = 8
    sheet = Image.new('L', (cols * (W + pad) + pad, rows * (H + pad) + pad), 40)
    for i, (_, im) in enumerate(shots):
        x = pad + (i % cols) * (W + pad)
        y = pad + (i // cols) * (H + pad)
        sheet.paste(im, (x, y))
    sheet.save(os.path.join(OUT, '_sheet.png'))

    print('\n%d screens -> %s' % (len(shots), OUT))
    return shots


if __name__ == '__main__':
    main()
