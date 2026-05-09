"""Regex parser for ``@username`` mentions in a free-form comment body.

The parser is intentionally conservative: it matches the same
character class the auth layer uses for usernames
(``[A-Za-z0-9_-]``) and only triggers on an ``@`` that follows a
word boundary. That avoids catching things like email addresses
(``foo@bar.com``) or in-word ``@`` symbols (``some@@thing``) as
mentions, while still recognising the canonical
``Hey @alice, look at this`` pattern.

Duplicates are deduped while preserving first-seen order so the
caller (the comments route) can iterate without worrying about
double-inserting the same mentioned user.
"""

from __future__ import annotations

import re

# ``(?<![\w@])`` rejects matches preceded by another word-char or @
# (so ``foo@bar`` and ``@@alice`` don't match). The username pattern
# mirrors :class:`reckora_api.auth.schemas.UserRegister.username`'s
# allow-list of letters / digits / underscore / hyphen.
_MENTION_RE = re.compile(r"(?<![\w@])@([A-Za-z0-9_-]{3,64})\b")


def extract_mentions(body: str) -> list[str]:
    """Return de-duplicated ``@username`` candidates in first-seen order.

    The returned strings do not include the leading ``@`` and are
    case-preserved (the auth layer is case-sensitive about
    usernames). Callers are expected to look each candidate up
    against the user table — unknown candidates should be dropped
    silently rather than rejected, so a typo'd handle in the body
    does not 422 the comment.
    """
    seen: set[str] = set()
    out: list[str] = []
    for match in _MENTION_RE.finditer(body):
        name = match.group(1)
        if name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out
