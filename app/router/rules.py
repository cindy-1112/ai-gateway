from __future__ import annotations

import fnmatch


def match_pattern(pattern: str, model: str) -> bool:
    return fnmatch.fnmatch(model, pattern)
