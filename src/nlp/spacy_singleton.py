"""
spaCy singleton loader and text processing utilities.

Provides lemmatization, POS-based content word extraction,
and lemma-aware keyword matching for Russian and English.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import spacy

if TYPE_CHECKING:
    from spacy.language import Language
    from spacy.tokens import Doc

from .config import SPACY_MODEL


# ─── Singleton ────────────────────────────────────────────────────────────────

_nlp: Language | None = None


def get_nlp() -> Language:
    """Load and return the spaCy model (singleton, loaded once)."""
    global _nlp
    if _nlp is None:
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
