"""
spaCy singleton loader and text processing utilities.

Provides lemmatization, POS-based content word extraction,
and lemma-aware keyword matching for Russian and English.

The spaCy model is auto-downloaded on first use if not installed.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from typing import TYPE_CHECKING

import spacy

if TYPE_CHECKING:
    from spacy.language import Language
    from spacy.tokens import Doc

from .config import SPACY_MODEL

logger = logging.getLogger(__name__)

# ─── Singleton ────────────────────────────────────────────────────────────────

_nlp: Language | None = None

_SPACY_MODEL_URLS = {
    "ru_core_news_sm": (
        "https://github.com/explosion/spacy-models/releases/download/"
        "ru_core_news_sm-3.8.0/ru_core_news_sm-3.8.0-py3-none-any.whl"
    ),
}


def _build_install_commands() -> list[list[str]]:
    """Build list of install commands to try, in priority order."""
    url = _SPACY_MODEL_URLS.get(SPACY_MODEL)
    package = url if url else SPACY_MODEL
    commands = []

    # 1. Try uv (preferred in uv-managed environments)
    uv_path = shutil.which("uv")
    if uv_path:
        commands.append([uv_path, "pip", "install", "--python", sys.executable, package])

    # 2. Try pip as module
    commands.append([sys.executable, "-m", "pip", "install", package])

    # 3. Try spacy download as last resort
    commands.append([sys.executable, "-m", "spacy", "download", SPACY_MODEL])

    return commands


def _install_model() -> None:
    """Download and install the spaCy model.

    Tries uv pip, pip, and spacy download in order.
    """
    logger.warning("spaCy model '%s' not found, installing...", SPACY_MODEL)

    last_error = None
    for cmd in _build_install_commands():
        try:
            logger.info("Trying: %s", " ".join(cmd[:4]) + " ...")
            subprocess.run(cmd, capture_output=True, text=True, check=True)
            logger.info("spaCy model '%s' installed successfully", SPACY_MODEL)
            return
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            last_error = e
            continue

    raise OSError(
        f"Can't find model '{SPACY_MODEL}' and auto-install failed. "
        f"Install manually: python -m spacy download {SPACY_MODEL}"
    ) from last_error


def get_nlp() -> Language:
    """Load and return the spaCy model (singleton, loaded once).

    Auto-downloads the model if not installed.
    """
    global _nlp
    if _nlp is None:
        try:
            _nlp = spacy.load(SPACY_MODEL)
        except OSError:
            _install_model()
            _nlp = spacy.load(SPACY_MODEL)
    return _nlp


def _process(text: str) -> Doc:
    """Process text through spaCy pipeline."""
    nlp = get_nlp()
    # Limit text length to avoid processing huge messages
    return nlp(text[:5000])


# ─── Lemmatization Utilities ─────────────────────────────────────────────────

def lemmatize(text: str) -> list[str]:
    """Return list of lemmas, excluding punctuation and whitespace."""
    doc = _process(text)
    return [
        token.lemma_.lower()
        for token in doc
        if not token.is_punct and not token.is_space
    ]


def lemma_set(text: str) -> set[str]:
    """Return set of lemmas with len >= 2.

    Replacement for the old _word_set() — uses morphological lemmas
    instead of raw lowercased tokens. This collapses Russian inflected
    forms: "жалобу", "жалобы", "жалоба" all become {"жалоба"}.
    """
    doc = _process(text)
    return {
        token.lemma_.lower()
        for token in doc
        if not token.is_punct and not token.is_space and len(token.lemma_) >= 2
    }


def content_word_set(text: str) -> set[str]:
    """Extract content words (NOUN, PROPN, ADJ, NUM) as lemmas.

    Replacement for the old _question_object_set() — uses POS tags
    to identify what the bot is asking for, instead of a stopword list.
    This gives much better results for Russian where question verbs
    vary ("уточните", "подскажите", "напишите") but the object
    ("номер", "заказ", "телефон") stays the same.
    """
    doc = _process(text)
    content_pos = {"NOUN", "PROPN", "ADJ", "NUM"}
    result = {
        token.lemma_.lower()
        for token in doc
        if token.pos_ in content_pos and len(token.lemma_) >= 2
    }
    # Fallback: if no content words found, return all lemmas
    return result if result else lemma_set(text)


def contains_any_lemma(text: str, keyword_lemmas: set[str]) -> set[str]:
    """Check if text contains any of the keyword lemmas.

    Replacement for the old _contains_any() substring matching.
    Both the text and keywords are in lemma form, so "жалобу" in text
    matches "жалоба" in keywords.

    Returns the set of matched keyword lemmas.
    """
    text_lemmas = lemma_set(text)
    return text_lemmas & keyword_lemmas


def text_contains_substring(text: str, substrings: list[str]) -> list[str]:
    """Fallback substring matching for multi-word phrases and edge cases.

    Some patterns are better matched as substrings because spaCy may
    misparse them (e.g. "Роспотребнадзор" as a whole word,
    "подам иск" as a phrase). Used alongside lemma matching.
    """
    text_lower = text.lower()
    return [s for s in substrings if s in text_lower]
