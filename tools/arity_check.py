"""Catch call-site/signature mismatches that py_compile cannot see.

Why this exists: renaming _draw_grid_header(d, disp) -> (d, group, disp) without
updating its two call sites produced a TypeError on every grid render, which
_render_grid's `except Exception` swallowed whole. The screen just went blank.
py_compile passes (it's valid syntax) and the offline preview passes (it supplies its
own call sites), so nothing caught it but the hardware.

MicroPython raises TypeError at CALL time, so a mismatch can sit in code that imports
and runs fine until the moment it fires -- and the sketch deliberately swallows render
exceptions to keep audio alive, so it surfaces as a blank screen with no traceback.
That is the whole case for checking before the code ever reaches the board.

Used two ways:
  * `python3 tools/arity_check.py [path]` -- exits non-zero on a mismatch.
  * `deploy_auto.py` imports check_file() and refuses to upload a failing sketch.
    That is the gate that matters; the CLI is for running it by hand.

Scope: module-level functions called by bare name. Methods, attribute calls, and
anything reached through a variable are NOT checked -- resolving those needs real type
inference. It handles defaults, *args, **kwargs, and keyword arguments, and skips call
sites that splat (*a / **kw) since their arity isn't knowable statically.
"""
import ast
import sys


def signatures(tree):
    """name -> signature facts, for module-level `def`s only."""
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
    return sigs


def check_tree(tree):
    """Return [(call_line, name, why, def_line)], empty if everything matches."""
    sigs = signatures(tree)
    bad = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        s = sigs.get(node.func.id)
        if s is None or any(isinstance(x, ast.Starred) for x in node.args):
            continue
        if any(k.arg is None for k in node.keywords):      # **kwargs at the call site
            continue
        npos = len(node.args)
        named = {k.arg for k in node.keywords}
        if not s['kwarg']:
            unknown = named - s['names']
            if unknown:
                bad.append((node.lineno, node.func.id,
                            'unknown kwarg(s) %s' % sorted(unknown), s['line']))
                continue
        bound = npos + len(named & s['names'])
        if npos > s['pos'] and not s['vararg']:
            bad.append((node.lineno, node.func.id,
                        '%d positional args, takes at most %d' % (npos, s['pos']),
                        s['line']))
        elif bound < s['min']:
            bad.append((node.lineno, node.func.id,
                        'only %d of %d required args supplied' % (bound, s['min']),
                        s['line']))
    return sorted(bad)


def check_file(path):
    """Return [(call_line, name, why, def_line)] for one file, empty if clean."""
    with open(path) as f:
        return check_tree(ast.parse(f.read(), path))


def main(argv):
    path = argv[1] if len(argv) > 1 else 'sketches/01_polysynth.py'
    with open(path) as f:
        tree = ast.parse(f.read(), path)
    sigs = signatures(tree)
    bad = check_tree(tree)
    for line, name, why, dline in bad:
        print('MISMATCH %s:%d  %s(...) -- %s  [def at :%d]' % (path, line, name, why, dline))
    print('%s: %d module-level funcs, %d mismatch(es)' % (path, len(sigs), len(bad)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
