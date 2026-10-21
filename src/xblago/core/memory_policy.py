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
    return bool(_SELF.search(text)) and not _UNSAFE.search(text) and not is_sensitive_memory("", text)


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
