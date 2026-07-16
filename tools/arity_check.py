"""Catch call-site/signature mismatches that py_compile cannot see.

Why this exists: renaming _draw_grid_header(d, disp) -> (d, group, disp) without
updating its two call sites produced a TypeError on every grid render, which
_render_grid's `except Exception: pass` swallowed whole. The screen just went blank.
py_compile passes (it's valid syntax) and the offline preview passes (it supplies its
own call sites), so nothing caught it but the hardware.
"""
import ast, sys

path = sys.argv[1] if len(sys.argv) > 1 else 'sketches/01_polysynth.py'
tree = ast.parse(open(path).read())

sigs = {}
for node in tree.body:
    if isinstance(node, ast.FunctionDef):
        a = node.args
        names = [x.arg for x in a.posonlyargs + a.args]
        sigs[node.name] = {
            'pos': len(names),
            'min': len(names) - len(a.defaults),
            'names': set(names) | {x.arg for x in a.kwonlyargs},
            'vararg': a.vararg is not None,
            'kwarg': a.kwarg is not None,
            'line': node.lineno,
        }

bad = []
for node in ast.walk(tree):
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
        continue
    s = sigs.get(node.func.id)
    if s is None or any(isinstance(x, ast.Starred) for x in node.args):
        continue
    if any(k.arg is None for k in node.keywords):        # **kwargs at the call site
        continue
    npos = len(node.args)
    named = {k.arg for k in node.keywords}
    if not s['kwarg']:
        unknown = named - s['names']
        if unknown:
            bad.append((node.lineno, node.func.id, 'unknown kwarg(s) %s' % sorted(unknown), s['line']))
            continue
    bound = npos + len(named & s['names'])
    if npos > s['pos'] and not s['vararg']:
        bad.append((node.lineno, node.func.id,
                    '%d positional args, takes at most %d' % (npos, s['pos']), s['line']))
    elif bound < s['min']:
        bad.append((node.lineno, node.func.id,
                    'only %d of %d required args supplied' % (bound, s['min']), s['line']))

for line, name, why, dline in sorted(bad):
    print('MISMATCH %s:%d  %s(...) -- %s  [def at :%d]' % (path, line, name, why, dline))
print('%s: %d module-level funcs, %d mismatch(es)' % (path, len(sigs), len(bad)))
sys.exit(1 if bad else 0)
