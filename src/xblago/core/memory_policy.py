"""Conservative local gates: provider output is untrusted input."""
from __future__ import annotations

import re

from xblago.core.memory import is_sensitive_memory

# A mixed/ambiguous message is left in conversation, never interpreted as consent.
_UNSAFE = re.compile(
    r"не\s+(?:(?:надо|нужно|следует)\s+)?(?:запомина\w*|запом\w*|сохраня\w*|сохрани\w*)|забудь|"
    r"do\s+not\s+(?:remember|save)|don['’]t\s+(?:remember|save)|forget|"
    r"\b(?:не|нет|если|возможно|наверное|кажется|предположим|допустим|якобы|цитата|сказал\w*|"
    r"not|never|maybe|perhaps|if|said|quote|что|он|она|они|друг|подруг\w*|мама|папа)\b|[?«»\"“”]",
    re.I,
)
_SELF = re.compile(
    r"^(?:(?:исправление:|теперь|уточнение:)\s+)*"
    r"(?:я\s+(?:живу|работаю|люблю|предпочитаю|умею|изучаю|хочу|планирую|интересуюсь|"
    r"увлекаюсь|мечтаю|занимаюсь)|меня зовут|мне\s+(?:нравится|нравятся|близка|близки|"
    r"удобнее|нужна|нужен|нужно)|мо[йяеё]\s+(?:город|имя|работа|профессия|цель|часовой пояс)|"
    r"I\s+(?:live|work|like|love|prefer|want|plan|enjoy|can|need)|my\s+(?:name|city|goal|timezone))\b",
    re.I,
)


def memory_veto(text: str) -> bool:
    return bool(re.search(
        r"не\s+(?:(?:надо|нужно|следует)\s+)?(?:запомина\w*|запом\w*|сохраня\w*|сохрани\w*)|"
        r"do\s+not\s+(?:remember|save)|don['’]t\s+(?:remember|save)", text, re.I,
    ))


def may_extract(text: str) -> bool:
    return bool(assertion_text(text))


def assertion_text(text: str) -> str:
    """Only a standalone assertion or a leading assertion followed by a question.

    Negation, uncertainty, quotes, third-party wording and storage veto remain
    whole-message guards. We do not split arbitrary conjunctions into facts.
    """
    if is_sensitive_memory('', text) or memory_veto(text):
        return ''
    if _SELF.search(text) and not _UNSAFE.search(text):
        return text
    match = re.fullmatch(r'([^,;.!?\n]+),\s*((?:что|как|где|куда)\s+[^.!?\n]+\?)', text.strip(), re.I)
    if not match:
        return ''
    prefix, question = match.groups()
    # Only the question marker and its initial interrogative are exempt.
    tail = re.sub(r'^(?:что|как|где|куда)\s+', '', question[:-1], flags=re.I)
    if _SELF.search(prefix) and not _UNSAFE.search(prefix) and not _UNSAFE.search(tail):
        return prefix.strip()
    return ''


def grounded(value: str, evidence: object, text: str) -> bool:
    """Require a verbatim, locally eligible source span and a verbatim value.

    Extractors preserve the user's wording; they must not infer or translate facts.
    Check the complete message so removing a negation from evidence cannot bypass it.
    """
    return (
        isinstance(evidence, str)
        and 3 <= len(evidence) <= 1000
        and evidence in text
        and may_extract(text)
        and may_extract(evidence)
        and value.casefold() in evidence.casefold()
        and evidence in assertion_text(text)
    )


_RELATIONS = {
    "name": r"меня зовут|мо[её] имя|my name",
    "city": r"я живу в|мой город|I live|my city",
    "timezone": r"мой часовой пояс|мой timezone|my timezone",
    "work": r"я работаю|моя работа|I work",
    "interest": r"я люблю|я интересуюсь|я увлекаюсь|мне (?:нравится|нравятся|близка|близки)|I (?:like|love|enjoy)",
    "preference": r"я предпочитаю|мне удобнее|I prefer",
    "skill": r"я умею|I can",
    "goal": r"я хочу|я планирую|я мечтаю|I (?:want|plan)",
    "need": r"мне (?:нужна|нужен|нужно)|I need",
}


def supported_relation(key: str, category: str, evidence: str) -> bool:
    """Do not let the model reinterpret a mentioned city as a place of residence."""
    relation = key if category == "fact" else category
    if category != "fact" and not key.startswith(category + ":"):
        return False
    pattern = _RELATIONS.get(relation)
    return pattern is not None and bool(re.search(pattern, evidence, re.I))


def extract_personal_facts(text: str) -> list[tuple[str, str]]:
    normalized = " ".join(assertion_text(text).strip().split())
    patterns = (
        (r"^(?:меня зовут|мое имя|моё имя)\s+(.+)$", "name"),
        (r"^(?:(?:исправление:|теперь)\s+)*(?:я живу в|мой город(?:\s*—|\s*-|\s*=)?|город)\s+(.+)$", "city"),
        (r"^(?:мой часовой пояс|мой timezone|timezone)\s*(?:—|-|=|is)?\s*(.+)$", "timezone"),
        (r"^(?:я работаю в|моя работа|я работаю)\s*(?:—|-|=|в)?\s*(.+)$", "work"),
        (r"^(?:я люблю|мне нравится|мне нравятся)\s+(.+)$", "likes"),
        (r"^(?:я предпочитаю|мне удобнее)\s+(.+)$", "preferences"),
    )
    for pattern, key in patterns:
        match = re.match(pattern, normalized, re.IGNORECASE)
        if match:
            value = match.group(1).strip(" .,!;:")
            if 1 <= len(value) <= 160 and not re.search(r"[.;!\n]|,|\b(?:но|а|и)\b", value, re.I):
                return [(key, value)]
    return []
