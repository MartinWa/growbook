"""Unicode-aware case folding for diary title and note search.

SQLite's built-in NOCASE collation only folds ASCII, so Swedish ``Å``/``å``
would not match. Both the stored search text and the query are case-folded with
Python's Unicode-aware ``casefold``; diacritics are deliberately preserved.
"""

import unicodedata


def normalize_search(text):
    return unicodedata.normalize("NFC", text).casefold()


def entry_search_text(title, body):
    return normalize_search("\n".join(part for part in (title, body) if part))
