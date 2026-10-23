"""Locally verified personal-memory edits; no LLM chooses a destructive target."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from xblago.core.memory import SemanticMemoryRecord, is_sensitive_memory
from xblago.core.memory_policy import memory_veto

ALIASES = {
    "city": {"city", "город", "место_жительства", "residence"},
    "name": {"name", "имя"},
    "timezone": {"timezone", "часовой_пояс"},
    "work": {"work", "работа", "профессия"},
}
_PREFERENCE_KEYS = {"likes", "preferences", "предпочтения", "любимое", "response_style", "answer_length", "формат_ответов", "стиль_ответов", "любимый_напиток", "предпочитаемый_напиток", "drink_preference"}
_DRINK_KEYS = {"preference:drink", "preference:drinks", "preference:beverage", "preference:beverages", "preference:tea", "preference:coffee"}
_STYLE_KEYS = {"preference:response_style", "preference:examples", "preference:short_answers", "preference:detailed_answers", "preference:answer_length"}
_AMBIGUOUS = re.compile(r"[?«»\"“”]|\b(?:если|возможно|наверное|кажется|якобы|цитата|сказал\w*|if|maybe)\b", re.I)


@dataclass(frozen=True)
class MemoryEdit:
    key: str
    value: str
    category: str
    old_value: str | None = None
    replacement: bool = False

    def record(self) -> SemanticMemoryRecord:
        return SemanticMemoryRecord("", self.key, self.value, self.category, "conversation_edit", "confirmed", 1.0, "", "")


@dataclass(frozen=True)
class Resolution:
    keys: tuple[str, ...] = ()
    choices: tuple[tuple[str, str], ...] = ()
    question: str | None = None


def preference_key(value: str) -> str | None:
    drink = bool(re.search(r"\b(?:ча[йяюе]|кофе|tea|coffee)\b", value, re.I))
    style = bool(re.search(r"коротк\w*|кратк\w*|подробн\w*|объяснен\w*|ответ\w*|\b(?:short|detailed|concise)\b", value, re.I))
    if drink and not style:
        return "preference:drink"
    if style and not drink:
        return "preference:response_style"
    return None


def is_preference(key: str) -> bool:
    return key in _PREFERENCE_KEYS or bool(re.match(r"^(?:preferences?|pref|likes?)[_:]", key))


def family(key: str, value: Any = "") -> str:
    for canonical, aliases in ALIASES.items():
        if key in aliases:
            return canonical
    if key in _DRINK_KEYS:
        return "preference:drink"
    if key in _STYLE_KEYS:
        return "preference:response_style"
    if is_preference(key):
        return preference_key(str(value)) or key
    return key


def _clean(value: str) -> str:
    return " ".join(value.strip(" .!,:;").split())


def parse_edit(text: str) -> MemoryEdit | None:
    """Whole-message patterns keep a negative clause separate from its replacement."""
    if memory_veto(text) or is_sensitive_memory("", text) or _AMBIGUOUS.search(text):
        return None
    text = text.strip()
    patterns = (
        (r"я (?:больше )?не живу в (?P<old>[^,;.]+)[,;.]\s*(?:а )?теперь (?:я )?живу в (?P<new>[^,;.?!]+)[.!]?", "city", "fact"),
        (r"(?:исправь[:,]?\s*)?(?:я )?теперь (?:я )?живу в (?P<new>[^,;.]+),\s*(?:а )?не в (?P<old>[^,;.?!]+)[.!]?", "city", "fact"),
        (r"(?:раньше я (?:любил[а]?|предпочитал[а]?)|я (?:больше )?не (?:люблю|предпочитаю)) (?P<old>[^,;.]+)[,;.]\s*(?:а )?(?:теперь )?(?:я )?предпочитаю (?P<new>[^,;.?!]+)[.!]?", "preference:choice", "preference"),
        (r"замени\s+(?P<old>[^,;.]+?)\s+на\s+(?P<new>[^,;.?!]+)[.!]?", "preference:choice", "preference"),
    )
    for pattern, key, category in patterns:
        match = re.fullmatch(pattern, text, re.I)
        if match:
            value, old = _clean(match["new"]), _clean(match["old"])
            if 1 <= len(value) <= 240 and old and not re.search(r"\b(?:не|нет|not|never)\b", value, re.I):
                if category == "preference":
                    key = preference_key(value) or key
                return MemoryEdit(key, value, category, old, True)
    match = re.fullmatch(r"(?P<now>теперь )?(?:я предпочитаю|мне удобнее)\s+(?P<value>[^,;.?!]+)[.!]?", text, re.I)
    if not match:
        match = re.fullmatch(r"я люблю\s+(?P<value>[^,;.?!]+)[.!]?", text, re.I)
    if match:
        value = _clean(match["value"])
        if not 1 <= len(value) <= 240 or re.search(r"\b(?:не|нет|not|never|расскажи|помоги|объясни|скажи)\b", value, re.I):
            return None
        key = preference_key(value)
        replacement = "now" in match.re.groupindex and bool(match["now"])
        if key or replacement:
            return MemoryEdit(key or "preference:choice", value, "preference", replacement=replacement)
    return None


def _words(value: str) -> tuple[str, ...]:
    value = re.sub(r"^(?:теперь\s+)?(?:я\s+(?:предпочитаю|люблю)|мне\s+(?:удобнее|нравится))\s+", "", value, flags=re.I)
    words = re.findall(r"[\w]+", value.casefold().replace("ё", "е"))
    result = []
    for word in words:
        if word in {"я", "мой", "моя", "мое", "мои", "что", "в", "о", "об"}:
            continue
        if word in {"чай", "чая", "чаю", "чае"}:
            word = "чай"
        elif len(word) >= 5:
            word = re.sub(r"(?:ами|ями|ого|ему|ом|ем|ой|ей|ах|ях|а|я|у|ю|е|ы|и|ь)$", "", word)
        result.append(word)
    return tuple(result)


def value_matches(query: str, value: Any) -> bool:
    q, v = _words(query), _words(str(value))
    return bool(q) and set(q).issubset(v)


def resolve_edit(edit: MemoryEdit, facts: dict[str, Any]) -> Resolution:
    same = {k: str(v) for k, v in facts.items() if family(k, v) == edit.key}
    if edit.category == "fact":
        return Resolution(tuple(same))
    preferences = {k: str(v) for k, v in facts.items() if is_preference(k) or (edit.old_value is not None and k.startswith("interest:"))}
    old = {k: v for k, v in preferences.items() if edit.old_value and value_matches(edit.old_value, v)}
    # Several *values* matching an explicit retraction require user disambiguation.
    if old and len({_words(v) for v in old.values()}) > 1:
        return Resolution(choices=tuple(old.items()))
    if old:
        return Resolution(tuple(old) if edit.key == "preference:choice" else tuple(dict.fromkeys((*same, *old))))
    if same and edit.key not in {"preference:drink", "preference:response_style"} and any(v != edit.value for v in same.values()):
        return Resolution(choices=tuple(same.items()))
    if same:
        return Resolution(tuple(same))
    if edit.replacement and preferences:
        return Resolution(choices=tuple(preferences.items()))
    if edit.key == "preference:choice" and edit.old_value is not None:
        return Resolution(question="Не нашёл подходящий факт для замены. Что именно нужно изменить?")
    return Resolution()


def natural_forget_query(text: str) -> str | None:
    patterns = (
        r"забудь[:,]?\s+(.+)",
        r"не помни[:,]?\s+(.+)",
        r"удали\s+(?:информацию|сведения|данные)\s+(?:о|об)\s+(.+)",
    )
    for pattern in patterns:
        match = re.fullmatch(pattern, text.strip().rstrip(".!"), re.I)
        if match:
            query = match[1]
            preference = re.fullmatch(r"(?:что\s+)?(?:я\s+)?(?:предпочитаю|люблю)\s+(.+)", query, re.I)
            if preference:
                return "@preference:" + preference[1]
            query = re.sub(r"^(?:мой|моя|мою|моё|мое|мои|моём|моем)\s+", "", query, flags=re.I).strip()
            typed = re.fullmatch(r"(навык|интерес|цель|потребность)\s+(.+)", query, re.I)
            if typed:
                category = {"навык":"skill", "интерес":"interest", "цель":"goal", "потребность":"need"}[typed[1].lower()]
                return "@" + category + ":" + typed[2]
            return query
    return None


def resolve_forget(query: str, entries: tuple[tuple[str, Any], ...]) -> Resolution:
    topic = None
    category = None
    if query.startswith("@"):
        category, query = query[1:].split(":", 1)
    if re.fullmatch(r"(?:город\w*|где я живу|место жительства)", query, re.I):
        topic = "city"
    elif re.fullmatch(r"(?:имя|как меня зовут)", query, re.I):
        topic = "name"
    elif re.fullmatch(r"(?:работ\w*|професси\w*)", query, re.I):
        topic = "work"
    elif re.fullmatch(r"часовой пояс", query, re.I):
        topic = "timezone"
    elif re.fullmatch(r"(?:предпочтени\w*|что я предпочитаю)", query, re.I):
        topic = "preferences"
    matches: dict[str, str] = {}
    for key, value in entries:
        if category == "preference" and not (is_preference(key) or key.startswith("interest:")):
            continue
        if category and category != "preference" and not key.startswith(category + ":"):
            continue
        match = (family(key, value) == topic if topic and topic != "preferences" else
                 is_preference(key) if topic == "preferences" else
                 key == query or value_matches(query.replace("_", " "), value))
        if match:
            matches[key] = str(value)
    if len(matches) > 1:
        return Resolution(choices=tuple(matches.items()))
    return Resolution(tuple(matches))


def clarification(choices: tuple[tuple[str, str], ...], *, replacement: str | None = None) -> str:
    values = tuple(dict.fromkeys(v for _, v in choices))
    options = "; ".join(f"«{v}»" for v in values)
    if replacement is not None:
        return f"Какое предпочтение заменить на «{replacement}»: {options}? Напиши, например: «замени {values[0]} на {replacement}»."
    if len(values) == 1:
        # Equal values can still be separate facts (e.g. an interest and a preference).
        options = "; ".join(f"«{v}» ({k})" for k, v in choices)
        return f"Нашёл несколько записей: {options}. Какую удалить? Можно указать её ключ командой /forget."
    return f"Что именно забыть: {options}? Напиши «забудь» и нужный вариант."
