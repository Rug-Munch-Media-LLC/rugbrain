"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC

Prompt pack loader for the three named editions.

Prompt text lives in ``prompts/*.md`` so a voice change is a reviewable diff,
not a Python change. Variables are ``string.Template`` placeholders; the loader
uses ``safe_substitute`` so a stray dollar figure in the prose cannot crash a
render. No prompt here restates a rule the style config already owns: the
caller passes the banned-phrase list, the budgets and the source floor in.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from string import Template

PROMPTS_DIR_ENV = "RUGBRAIN_PROMPTS_DIR"


def prompts_dir() -> Path:
    """Directory holding the edition prompt files."""
    override = os.getenv(PROMPTS_DIR_ENV, "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parent / "prompts"


@lru_cache(maxsize=32)
def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8").strip()


def prompt(name: str, **values: object) -> str:
    """Render ``prompts/<name>.md`` with ``values`` substituted."""
    target = prompts_dir() / f"{name}.md"
    if not target.is_file():
        raise FileNotFoundError(f"no prompt {name!r} in {prompts_dir()}")
    rendered = {key: "" if value is None else value for key, value in values.items()}
    return Template(_read(str(target))).safe_substitute(rendered)


def pack_names() -> tuple[str, ...]:
    """Every prompt file, sorted — the pack's manifest (docs excluded)."""
    return tuple(sorted(path.stem for path in prompts_dir().glob("*.md") if path.stem != "SOURCES"))
