"""NLP integration layer for pattern detection."""

from .spacy_singleton import (
    get_nlp,
    lemmatize,
    lemma_set,
    content_word_set,
    contains_any_lemma,
    text_contains_substring,
)

__all__ = [
    "get_nlp",
    "lemmatize",
    "lemma_set",
    "content_word_set",
    "contains_any_lemma",
    "text_contains_substring",
]
