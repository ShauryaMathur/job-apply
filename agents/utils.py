"""Shared utility functions for the agent pipeline."""

from __future__ import annotations

import re


def strip_latex_to_text(tex: str) -> str:
    """Strip LaTeX commands and extract readable plain text."""
    # Remove comments
    tex = re.sub(r"%.*$", "", tex, flags=re.MULTILINE)
    # Remove \command[...]{...} -> keep content
    tex = re.sub(r"\\[a-zA-Z]+\*?\[.*?\]\{([^}]*)\}", r"\1", tex)
    # Remove \command{...} -> keep content
    tex = re.sub(r"\\[a-zA-Z]+\*?\{([^}]*)\}", r"\1", tex)
    # Remove remaining \command
    tex = re.sub(r"\\[a-zA-Z]+\*?", " ", tex)
    # Remove special chars
    tex = re.sub(r"[{}\\$&#^_~]", " ", tex)
    # Normalize whitespace
    tex = re.sub(r"\s+", " ", tex)
    return tex.strip()


def escape_latex(text: str) -> str:
    """Escape LaTeX special characters in plain prose."""
    # Replace backslash first to avoid double-escaping
    text = text.replace("\\", r"\textbackslash{}")
    text = text.replace("&", r"\&")
    text = text.replace("%", r"\%")
    text = text.replace("$", r"\$")
    text = text.replace("#", r"\#")
    text = text.replace("^", r"\textasciicircum{}")
    text = text.replace("~", r"\textasciitilde{}")
    text = text.replace("{", r"\{")
    text = text.replace("}", r"\}")
    text = text.replace("_", r"\_")
    return text
