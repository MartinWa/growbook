"""Unicode-aware case folding for diary note search.

SQLite's built-in NOCASE collation only folds ASCII, so Swedish ``Å``/``å``
would not match. Both the stored search text and the query are case-folded with
Python's Unicode-aware ``casefold``; diacritics are deliberately preserved.
"""

import unicodedata


def normalize_search(text):
    return unicodedata.normalize("NFC", text).casefold()
