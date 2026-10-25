"""Rules enforced in code, so they hold even if the model ignores its prompt.

These are the "guardrails you can prove" for the Responsible AI section:
each one has a unit test in tests/test_safeguards.py.
"""

import re

EMERGENCY_LINE = "In an emergency, call 911."
CRISIS_LINE = "Feeling alone or unsafe? Call or text 988, any time."
FALLBACK_LINE = (
    "Nothing in our list fits this yet. Boston's Age Strong Commission "
    "(617-635-4366), Mass 211 (dial 2-1-1), or 1-800-AGE-INFO can help you look."
)
# Said when the user has no one to ask. Same places as FALLBACK_LINE.
NO_ONE_LINE = (
    "That's okay. Your plan can use services and simple steps only. For more help "
    "finding services, Boston's Age Strong Commission (617-635-4366), which is "
    "Boston's Council on Aging, or Mass 211 (dial 2-1-1) can help."
)
# Said with any outside service a person might sign up for, like a site for sharing updates.
SEPARATE_SERVICE_LINE = ("{name} is a separate service with its own privacy settings. "
                         "Just in Case never makes an account or shares anything for you.")


def separate_service_line(name: str) -> str:
    return SEPARATE_SERVICE_LINE.format(name=name)


ESCORT_RULE_LINE = (
    "Ask your surgical team if this option meets their rule for who takes you "
    "home. Some clinics do not accept rides or paid services."
)

# Phrases that pressure a helper or the user. Kept short and easy to extend.
PRESSURE_PATTERNS = [
    r"\bmust\b",
    r"\bhave to\b",
    r"\bneed you to\b",
    r"\burgent(ly)?\b",
    r"\basap\b",
    r"\bplease (reply|respond|answer)\b",
    r"\bdon'?t forget\b",
    r"\bcounting on you\b",
    r"\bno one else\b",
    r"\byou'?re my only\b",
]

# The note must make it easy to say no. At least one of these must appear.
EASY_NO_PHRASES = [
    "no pressure",
    "no need to reply",
    "totally fine",
    "completely fine",
    "it's okay if",
    "it is okay if",
    "no worries",
    "if you can't",
    "if you cannot",
    "if it doesn't suit",
]

MAX_NOTE_GRADE = 8.0


def pressure_phrases(text: str) -> list[str]:
    lower = text.lower()
    return [p for p in PRESSURE_PATTERNS if re.search(p, lower)]


def has_easy_no(text: str) -> bool:
    lower = text.lower().replace("’", "'")
    return any(p in lower for p in EASY_NO_PHRASES)


def _syllables(word: str) -> int:
    word = word.lower().strip(".,!?;:'\"()")
    if not word:
        return 0
    groups = re.findall(r"[aeiouy]+", word)
    count = len(groups)
    if word.endswith("e") and not word.endswith(("le", "ee")) and count > 1:
        count -= 1  # silent e: "home", "time"
    return max(1, count)


def reading_grade(text: str) -> float:
    """Flesch-Kincaid grade level (standard formula, simple syllable counter).

    0.39 * (words / sentences) + 11.8 * (syllables / words) - 15.59
    """
    sentences = max(1, len(re.findall(r"[.!?]+", text)))
    words = re.findall(r"[A-Za-z']+", text)
    if not words:
        return 0.0
    syllables = sum(_syllables(w) for w in words)
    return 0.39 * (len(words) / sentences) + 11.8 * (syllables / len(words)) - 15.59


def check_helper_note(note_text: str) -> str | None:
    """Return a problem description, or None if the note is acceptable."""
    found = pressure_phrases(note_text)
    if found:
        return (
            "The note uses pressure language "
            f"({', '.join(found)}). Rewrite it so the friend feels free to say no."
        )
    if not has_easy_no(note_text):
        return (
            "The note needs one plain line that makes saying no easy, "
            "for example 'No pressure at all, and no need to reply.'"
        )
    grade = reading_grade(note_text)
    if grade > MAX_NOTE_GRADE:
        return f"The note reads at grade {grade:.1f}. Use shorter words and sentences (grade 8 or lower)."
    return None


# ---- "Only what the user said" ---------------------------------------------

def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def said_by_user(value: str, user_text: str, name: bool = False) -> bool:
    """True if a detail clearly comes from what the user typed.

    Phone numbers must match digit for digit. For words, every word of 4+
    letters must start a word the user typed (so "mornings" matches "morning",
    but "lisinopril" never appears unless she said it). Short filler words are
    ignored so the agent can tidy punctuation.
    """
    value = (value or "").strip()
    if not value:
        return True
    digits = _digits(value)
    if len(digits) >= 7:
        return digits in _digits(user_text)
    said = re.findall(r"[a-z0-9']+", user_text.lower())
    if name:  # a name must appear word for word: "Marion" is not "Mary"
        return all(w in said for w in re.findall(r"[a-z0-9']+", value.lower()) if len(w) >= 2)
    for word in re.findall(r"[a-z0-9']+", value.lower()):
        if len(word) < 4:
            continue
        stem = word[:4]
        if not any(w.startswith(stem) for w in said):
            return False
    return True


def safe_cell(value: str) -> str:
    """Spreadsheet cell that cannot run as a formula (CSV injection guard)."""
    value = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t") else value


def first_name(value) -> str:
    """Pages other people may see show first names only: "Doris Chen" -> "Doris"."""
    parts = str(value or "").replace("\r", " ").replace("\n", " ").split()
    return parts[0].rstrip(",.")[:120] if parts else ""
