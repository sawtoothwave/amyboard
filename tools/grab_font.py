"""Read the board's REAL framebuf 8x8 font by rendering each glyph into an in-RAM
framebuf and reading the pixels back. No guessing at letterforms."""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from board_serial import BoardSerialSession, detect_port

SCRIPT = '''
import framebuf
buf = bytearray(64)
fb = framebuf.FrameBuffer(buf, 8, 8, framebuf.GS8)
out = []
for c in range(32, 127):
    for i in range(64):
        buf[i] = 0
    fb.text(chr(c), 0, 0, 255)
    bits = 0
    for y in range(8):
        for x in range(8):
            if buf[y*8+x]:
                bits |= 1 << (y*8+x)
    out.append('%016x' % bits)
print('__FONT__' + ','.join(out) + '__END__')
'''

port = detect_port()
print('port:', port)
with BoardSerialSession(port) as s:
    out = s.run_paste_script(SCRIPT, timeout=40)
if out.count('__FONT__') < 2 or '__END__' not in out:
    print('FAILED, raw output:'); print(out[-2000:]); sys.exit(1)
data = out.rsplit('__FONT__', 1)[-1].split('__END__')[0].strip()
glyphs = data.split(',')
print('glyphs:', len(glyphs))
font = {}
for i, hexs in enumerate(glyphs):
    bits = int(hexs, 16)
    rows = []
    for y in range(8):
        rows.append([(bits >> (y*8+x)) & 1 for x in range(8)])
    font[chr(32+i)] = rows
json.dump(font, open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  'font8x8.json'), 'w'))
# sanity: show 'A' and 'P'
for ch in 'AP':
    print(ch + ':')
    for row in font[ch]:
        print('  ' + ''.join('#' if v else '.' for v in row))
