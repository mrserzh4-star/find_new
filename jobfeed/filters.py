"""Фільтр вакансій. Уся поведінка — з `config/filter.toml`, у коді нічого не зашито."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Vacancy


# Межа слова = не буква й не цифра. Саме так, а не через `\w`: у `\w` входить
# підкреслення, і тоді фемінітив «Дизайнер_ка» перестає бути дизайнером —
# на живих даних це коштувало нам справжньої вакансії. Дефіс, слеш і крапка
# межею лишаються, тому "ui" все одно не ловить "build".
_EDGE_BEFORE = r"(?<![^\W_])"
_EDGE_AFTER = r"(?![^\W_])"


def _pattern(keyword: str) -> re.Pattern[str]:
    """Ключове слово → regex зі межами слова.

    Межі потрібні, бо коротке "ui" інакше ловить "build" і "guide".
    Пробіли в ключі матчать будь-який пробільний рядок, дефіс — дефіс або пробіл,
    щоб "ui/ux" і "ui / ux", "front-end" і "front end" збігались однаково.

    Зірочка в кінці ("дизайн*") знімає ПРАВУ межу — ключ стає основою слова
    й ловить будь-яке закінчення. Це потрібно для української, де форм багато
    («дизайнер», «дизайнерка», «дизайнерів»), а перелічувати їх вручну означає
    рано чи пізно якусь пропустити. Ліва межа лишається завжди, тому основа
    не спрацює всередині іншого слова.
    """
    keyword = keyword.strip()
    stem = keyword.endswith("*")
    if stem:
        keyword = keyword[:-1].strip()

    parts = [re.escape(p) for p in re.split(r"[\s\-]+", keyword) if p]
    body = r"[\s\-/]*".join(parts)
    return re.compile(
        _EDGE_BEFORE + body + ("" if stem else _EDGE_AFTER),
        re.IGNORECASE | re.UNICODE,
    )


@dataclass
class VacancyFilter:
    include_any: list[str]
    exclude_any: list[str]
    match_fields: list[str]
    locations_any: list[str]

    @classmethod
    def from_config(cls, config: dict) -> "VacancyFilter":
        return cls(
            include_any=list(config.get("include_any", [])),
            exclude_any=list(config.get("exclude_any", [])),
            match_fields=list(config.get("match_fields", ["title"])),
            locations_any=list(config.get("locations_any", [])),
        )

    def __post_init__(self) -> None:
        self._include = [(k, _pattern(k)) for k in self.include_any]
        self._exclude = [(k, _pattern(k)) for k in self.exclude_any]
        self._locations = [(k, _pattern(k)) for k in self.locations_any]

    def _haystack(self, vacancy: Vacancy) -> str:
        return " \n ".join(
            str(getattr(vacancy, field, "") or "") for field in self.match_fields
        )

    def decide(self, vacancy: Vacancy, trust_category: bool = False) -> tuple[bool, str]:
        """(підходить, причина). Причина йде в лог — видно, ЧОМУ відсіяли.

        `trust_category` — джерело вже саме відібрало вакансії точною категорією
        (як Djinni своїми `UI UX`, `Product Design`). Тоді позитивний список
        не застосовується: покращити він тут нічого не може, а втратити може —
        на живих даних саме так тихо зникли сім арт-директорів, бо `arts`
        через межі слова не ловить `Art Director`.

        Стоп-слова діють ЗАВЖДИ, на всіх джерелах: це запобіжник, і він не
        має відключатися ніколи, навіть коли зараз нічого не ловить.
        """
        text = self._haystack(vacancy)

        for keyword, pattern in self._exclude:
            if pattern.search(text):
                return False, f"стоп-слово: {keyword}"

        if trust_category:
            return True, "джерело відібрало категорією"

        if self._include:
            hits = [k for k, p in self._include if p.search(text)]
            if not hits:
                return False, "жодного ключового слова"
        else:
            hits = []

        if self._locations:
            location_text = f"{vacancy.location} {vacancy.title}"
            if not any(p.search(location_text) for _, p in self._locations):
                return False, f"локація не підходить: {vacancy.location or '—'}"

        return True, ("збіг: " + ", ".join(hits)) if hits else "фільтр порожній"

    def apply(
        self, vacancies: list[Vacancy], trusted: set[str] | None = None
    ) -> tuple[list[Vacancy], list[tuple[Vacancy, str]]]:
        """`trusted` — джерела, які самі відбирають категорією (див. `decide`)."""
        trusted = trusted or set()
        kept, dropped = [], []
        for vacancy in vacancies:
            ok, reason = self.decide(vacancy, vacancy.source in trusted)
            (kept if ok else dropped).append(vacancy if ok else (vacancy, reason))
        return kept, dropped
