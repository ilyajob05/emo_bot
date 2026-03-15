"""
Load pattern detector configuration from TOML file.

Supports custom keyword databases and detection thresholds.
Falls back to built-in defaults if config file is not found.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Config file path: override via PATTERNS_CONFIG env var
_CONFIG_PATH = os.environ.get(
    "PATTERNS_CONFIG",
    str(Path(__file__).resolve().parent.parent / "config" / "patterns.toml"),
)

_config: dict[str, Any] | None = None


def _load_config() -> dict[str, Any]:
    """Load TOML config, return empty dict on failure."""
    path = Path(_CONFIG_PATH)
    if not path.exists():
        logger.info("Pattern config not found at %s, using built-in defaults", path)
        return {}

    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore[no-redef]

    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
        logger.info("Loaded pattern config from %s", path)
        return data
    except Exception as e:
        logger.warning("Failed to load pattern config from %s: %s", path, e)
        return {}


def get_config() -> dict[str, Any]:
    """Return cached config (loaded once)."""
    global _config
    if _config is None:
        _config = _load_config()
    return _config


def reload_config() -> dict[str, Any]:
    """Force reload config from file."""
    global _config
    _config = _load_config()
    return _config


# ─── Typed accessors ─────────────────────────────────────────────────────────

def get_keywords(category: str) -> tuple[set[str], list[str], set[str], list[str]]:
    """Get keyword sets for a pattern category.

    Returns (lemmas_ru, substrings_ru, lemmas_en, substrings_en).
    """
    cfg = get_config().get(category, {})
    return (
        set(cfg.get("lemmas_ru", [])),
        list(cfg.get("substrings_ru", [])),
        set(cfg.get("lemmas_en", [])),
        list(cfg.get("substrings_en", [])),
    )


def get_threshold(name: str, default: float) -> float:
    """Get a detection threshold value."""
    return float(get_config().get("thresholds", {}).get(name, default))


def get_question_pattern(default: str) -> str:
    """Get the regex pattern for question detection."""
    return str(get_config().get("thresholds", {}).get("question_pattern", default))


def get_regex_patterns(category: str) -> tuple[list[str], list[str]]:
    """Get regex patterns for a category.

    Returns (regex_ru, regex_en).
    """
    cfg = get_config().get(category, {})
    return (
        list(cfg.get("regex_ru", [])),
        list(cfg.get("regex_en", [])),
    )


def get_empathy_phrases() -> tuple[list[str], list[str]]:
    """Get empathy phrases for anti-pattern tracking.

    Returns (phrases_ru, phrases_en).
    """
    cfg = get_config().get("empathy_phrases", {})
    return (
        list(cfg.get("ru", [])),
        list(cfg.get("en", [])),
    )


def get_deflection_phrases() -> tuple[list[str], list[str]]:
    """Get deflection/avoidance phrases for bot anti-pattern tracking.

    Returns (phrases_ru, phrases_en).
    """
    cfg = get_config().get("deflection_phrases", {})
    return (
        list(cfg.get("ru", [])),
        list(cfg.get("en", [])),
    )
