import re
import difflib


def _normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _soundex(word: str) -> str:
    word = word.upper()
    if not word:
        return "0000"
    codes = {
        "BFPV": "1", "CGJKQSXYZ": "2", "DT": "3",
        "L": "4", "MN": "5", "R": "6"
    }
    result = word[0]
    prev = ""
    for ch in word[1:]:
        code = ""
        for chars, c in codes.items():
            if ch in chars:
                code = c
                break
        if code and code != prev:
            result += code
        prev = code
    return (result + "000")[:4]


def _title_soundex(title: str) -> str:
    words = _normalize(title).split()
    skip = {"the", "a", "an"}
    for w in words:
        if w not in skip:
            return _soundex(w)
    return _soundex(words[0]) if words else "0000"


def find_best_match(query: str, candidates: list, cutoff: float = 0.55) -> str | None:
    if not candidates or not query:
        return None

    norm_q = _normalize(query)
    sdx_q = _title_soundex(query)
    best_title = None
    best_score = 0.0

    for title in candidates:
        norm_t = _normalize(title)

        if norm_q == norm_t:
            return title

        ratio = difflib.SequenceMatcher(None, norm_q, norm_t).ratio()

        if _title_soundex(title) == sdx_q:
            ratio += 0.10

        q_words = set(norm_q.split())
        t_words = set(norm_t.split())
        if q_words and t_words:
            overlap = len(q_words & t_words) / max(len(q_words), len(t_words))
            ratio += overlap * 0.15

        if ratio > best_score:
            best_score = ratio
            best_title = title

    return best_title if best_score >= cutoff else None
