"""Prompts live as version-controlled `.md` files, one per agent turn.

Keeping them out of the Python source means a prompt change is a readable diff, and
Day 1's "safe to change later" list (plan section 21) stays honest.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parent


class PromptNotFound(FileNotFoundError):
    """A prompt file was asked for by a name that has no `.md` beside this module."""


@lru_cache(maxsize=32)
def load_prompt(name: str) -> str:
    """Return the text of `<name>.md`."""
    path = PROMPT_DIR / f"{name}.md"
    if not path.is_file():
        available = ", ".join(sorted(p.stem for p in PROMPT_DIR.glob("*.md"))) or "none"
        raise PromptNotFound(f"no prompt named {name!r} in {PROMPT_DIR} (have: {available})")
    return path.read_text(encoding="utf-8")


def render_prompt(name: str, **values: object) -> str:
    """Load `<name>.md` and substitute `{placeholder}` values."""
    return load_prompt(name).format(**values)
