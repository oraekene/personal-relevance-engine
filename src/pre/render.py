"""Shared HTML rendering: Jinja2 templates with autoescape always on."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

TEMPLATES_DIR = Path(__file__).parent / "templates"
_JINJA = Environment(loader=FileSystemLoader(TEMPLATES_DIR), autoescape=True)


def render_template(name: str, **context: Any) -> str:
    return _JINJA.get_template(name).render(**context)


__all__ = ["TEMPLATES_DIR", "render_template"]
