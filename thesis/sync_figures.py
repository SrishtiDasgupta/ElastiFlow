#!/usr/bin/env python3
"""
Figure provenance: check and regenerate the dissertation's data figures.

`thesis/figures.yaml` lists every figure of the dissertation that comes from
data: the submitted image, the committed file it is a copy of, the one script
that writes that file, what the script reads, and the state that was recorded
when the manifest was made.

    python thesis/sync_figures.py check [--thesis-images DIR] [--strict]
    python thesis/sync_figures.py regenerate [--thesis-images DIR] [--only NAME ...]
    python thesis/sync_figures.py writers

check       compares each committed file with the submitted image (byte for byte,
            then by the text drawn in the PDF, whitespace aside) when DIR is
            given, verifies the committed file and the generator exist, counts
            the scripts that write the figure's name, and reports where the
            state differs from the recorded one. --strict exits 1 on any
            difference. Without DIR only the static checks run.
regenerate  runs each generator in a throw-away git worktree of HEAD (the
            working tree is never written to), then compares the regenerated
            file with the committed one and, with DIR, with the submitted image.
writers     lists the scripts that write each figure's name (the rule: one).

The submitted image tree is frozen; this script only reports. Copying a
regenerated file into the thesis is a decision, not something this script does.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
MANIFEST = Path(__file__).resolve().parent / 'figures.yaml'
STATES = ('identical', 'identical tokens', 'identical pixels', 'differs')


def load():
    return yaml.safe_load(MANIFEST.read_text())


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def pdf_tokens(p: Path):
    if p.suffix.lower() != '.pdf' or shutil.which('gs') is None:
        return None
    cp = subprocess.run(['gs', '-q', '-dNOPAUSE', '-dBATCH', '-sDEVICE=txtwrite', '-sOutputFile=-', str(p)],
                        capture_output=True, text=True)
    return cp.stdout.split()


def compare(a: Path, b: Path) -> str:
    """'identical' (bytes), 'identical tokens' (a PDF's drawn text), 'identical pixels'
    (a PNG's raster), 'differs', or 'missing'."""
    if not a.exists() or not b.exists():
        return 'missing'
    if sha(a) == sha(b):
        return 'identical'
    ta, tb = pdf_tokens(a), pdf_tokens(b)
    if ta is not None and ta == tb:
        return 'identical tokens'
    if a.suffix.lower() == '.png' and b.suffix.lower() == '.png':
        from PIL import Image
        if Image.open(a).convert('RGBA').tobytes() == Image.open(b).convert('RGBA').tobytes():
            return 'identical pixels'
    return 'differs'


def _code_literals(py: Path):
    """String literals in a script's code (docstrings excluded), f-string parts included."""
    tree = ast.parse(py.read_text(errors='ignore'))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                docstrings.add(id(first.value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            yield node.value


def writers(fig: dict) -> list[str]:
    """Scripts in the generator's directory that save a figure and name this one
    in their code (a string literal containing the file's stem as a whole word)."""
    stem = Path(fig['committed']).stem
    word = re.compile(r'(?<![\w])' + re.escape(stem) + r'(?![\w])')
    gen_dir = (REPO / fig['generator']).parent
    out = []
    for py in sorted(gen_dir.glob('*.py')):
        text = py.read_text(errors='ignore')
        if 'savefig' in text and any(word.search(lit) for lit in _code_literals(py)):
            out.append(str(py.relative_to(REPO)))
    return out


def check(thesis_images: str | None, strict: bool) -> int:
    m = load()
    root = Path(thesis_images).expanduser() if thesis_images else None
    problems = 0
    print(f"{'figure':52s} {'writers':>7s}  {'recorded':17s} {'now':17s}")
    for fig in m['figures']:
        committed, generator = REPO / fig['committed'], REPO / fig['generator']
        w = writers(fig)
        now = compare(committed, root / fig['thesis']) if root else '-'
        flags = []
        if not committed.exists():
            flags.append('committed file missing')
        if not generator.exists():
            flags.append('generator missing')
        if len(w) != 1:
            flags.append(f'{len(w)} writers: {w}')
        if root and now != fig['status']:
            flags.append('state changed')
        problems += bool(flags)
        print(f"{fig['thesis']:52s} {len(w):>7d}  {fig['status']:17s} {now:17s} {'; '.join(flags)}")
    print(f"\n{len(m['figures'])} figures, {problems} with a problem" + ("" if root else " (static checks only: pass --thesis-images DIR to compare with the submitted images)"))
    return 1 if strict and problems else 0


def regenerate(thesis_images: str | None, only: list[str]) -> int:
    m = load()
    figs = [f for f in m['figures'] if not only or any(o in f['thesis'] or o in f['committed'] for o in only)]
    root = Path(thesis_images).expanduser() if thesis_images else None
    tmp = Path(tempfile.mkdtemp(prefix='elastiflow-figs-'))
    wt = tmp / 'tree'
    subprocess.run(['git', 'worktree', 'add', '-q', str(wt), 'HEAD'], cwd=REPO, check=True)
    problems = 0
    try:
        by_gen = {}
        for f in figs:
            by_gen.setdefault(f['generator'], []).append(f)
        for gen, group in by_gen.items():
            script = wt / gen
            cp = subprocess.run([sys.executable, script.name], cwd=script.parent,
                                env=dict(os.environ, PYTHONPATH=str(wt), MPLBACKEND='Agg'),
                                capture_output=True, text=True, timeout=1800)
            if cp.returncode != 0:
                problems += len(group)
                print(f"{gen}: exit {cp.returncode}\n  " + "\n  ".join((cp.stdout + cp.stderr).splitlines()[-6:]))
                continue
            for f in group:
                new = wt / f['committed']
                vs_committed = compare(new, REPO / f['committed'])
                vs_thesis = compare(new, root / f['thesis']) if root else '-'
                bad = vs_committed == 'differs' or vs_thesis == 'differs' or 'missing' in (vs_committed, vs_thesis)
                problems += bad
                print(f"{f['thesis']:52s} regenerated vs committed: {vs_committed:17s} vs submitted: {vs_thesis}")
    finally:
        subprocess.run(['git', 'worktree', 'remove', '--force', str(wt)], cwd=REPO)
        subprocess.run(['git', 'worktree', 'prune'], cwd=REPO)
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{len(figs)} figures regenerated, {problems} differ from what is committed or submitted")
    return 1 if problems else 0


def list_writers() -> int:
    for fig in load()['figures']:
        w = writers(fig)
        print(f"{fig['thesis']:52s} {len(w)}  {', '.join(w)}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    c = sub.add_parser('check'); c.add_argument('--thesis-images'); c.add_argument('--strict', action='store_true')
    r = sub.add_parser('regenerate'); r.add_argument('--thesis-images'); r.add_argument('--only', nargs='*', default=[])
    sub.add_parser('writers')
    a = ap.parse_args(argv)
    if a.cmd == 'check':
        return check(a.thesis_images, a.strict)
    if a.cmd == 'regenerate':
        return regenerate(a.thesis_images, a.only)
    return list_writers()


if __name__ == '__main__':
    sys.exit(main())
