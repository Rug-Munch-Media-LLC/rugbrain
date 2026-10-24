"""Path/comment iteration helpers for the dimension-drift gate."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path


def _iter_python_files(roots: Sequence[Path]) -> Iterator[Path]:
    """Yield every .py under each root.

    A root may be a single FILE or a directory. A file root is the case that
    matters for the mutation tests — and it is the case that silently made
    every "scan just this file" probe return zero files, because
    ``Path("x.py").rglob("*.py")`` is empty. A gate that reports "clean"
    because it looked at nothing is worse than no gate, so the branch is
    explicit.
    """
    for root in roots:
        if not root.exists():
            continue
        if root.is_file():
            if root.suffix == ".py":
                yield root
            continue
        yield from sorted(root.rglob("*.py"))


def _relative(path: Path, base: Path) -> str:
    try:
        return str(path.resolve().relative_to(base))
    except ValueError:
        return str(path)


def _iter_comments(source: str) -> Iterator[tuple[int, str]]:
    """Yield ``(lineno, text)`` for every comment line, tokenise-safely.

    ``tokenize`` rather than a ``#`` split so a ``#`` inside a string is not
    mistaken for a comment and a 2560 inside a URL is not a claim.
    """
    import io
    import tokenize

    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        for token in tokens:
            if token.type == tokenize.COMMENT:
                yield token.start[0], token.string
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return
