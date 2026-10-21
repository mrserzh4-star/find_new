"""Білий список параметрів фіда — СПІЛЬНИЙ для всіх джерел.

Не особливість окремого сайту, а закономірність джоб-бордів: невідомий
параметр вони не відхиляють, а мовчки ігнорують — і віддають HTTP 200 з
вибіркою, яку ти не просив. Заміряно на трьох джерелах із чотирьох:

    DOU          ?company=fuib          → 200 і 50 нерелевантних записів
    Djinni       невалідний editorial   → фільтр тихо знімається
    HappyMonday  ?job_category=dyzajn   → повертає всі 41 808 записів

Наслідок однаковий і найгірший з можливих: одруківка не падає, а тихо
підмінює точну вибірку на «все підряд», і помітити це можна лише за тим,
що у стрічці раптом зʼявився бекенд і бухгалтерія.

Тому кожен адаптер лише ОГОЛОШУЄ свій `FeedSpec`, а перевіряє цей модуль.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class FeedConfigError(ValueError):
    """Помилка в описі фіда. Падаємо на старті, а не тихо тягнемо не те."""


@dataclass(frozen=True)
class FeedSpec:
    source: str
    # Параметри, які джерело справді обробляє. Решта — заборонена.
    allowed: frozenset[str]
    # Хоч один із них обовʼязковий: без звуження фід покриває ~годину
    # і при щогодинному запуску вакансії губляться безповоротно.
    narrowing: frozenset[str]
    # Параметри, де перевіряємо ще й ЗНАЧЕННЯ: невалідне значення теж
    # мовчки знімає фільтр, і білий список ключів цього не ловить.
    values: dict[str, frozenset[str]] = field(default_factory=dict)
    # Параметри, які ламаються на не-ASCII (у DOU кирилиця в category → 400).
    ascii_only: frozenset[str] = frozenset()
    hint: str = ""


def validate(feeds: list[dict], spec: FeedSpec) -> list[dict[str, str]]:
    if not feeds:
        raise FeedConfigError(f"для {spec.source} не задано жодного фіда")
    return [_one(feed, index, spec) for index, feed in enumerate(feeds)]


def _one(feed: dict, index: int, spec: FeedSpec) -> dict[str, str]:
    where = f"{spec.source}, фід #{index + 1}"

    unknown = set(feed) - spec.allowed
    if unknown:
        raise FeedConfigError(
            f"{where}: джерело не знає параметрів {sorted(unknown)} і мовчки їх "
            f"проігнорує, віддавши ВСІ вакансії підряд. "
            f"Дозволені: {sorted(spec.allowed)}"
        )

    params = {k: str(v).strip() for k, v in feed.items() if str(v).strip()}

    if not (set(params) & spec.narrowing):
        raise FeedConfigError(
            f"{where}: потрібен хоча б один звужувальний параметр "
            f"({sorted(spec.narrowing)}). Незвужений фід покриває близько години, "
            f"тож при щогодинному запуску вакансії губитимуться. {spec.hint}".strip()
        )

    for key, allowed_values in spec.values.items():
        value = params.get(key, "")
        if value and value not in allowed_values:
            raise FeedConfigError(
                f"{where}: {key}={value!r} — невалідне значення тихо ЗНІМАЄ фільтр, "
                f"а не падає. Дозволені: {sorted(allowed_values)}"
            )

    for key in spec.ascii_only:
        value = params.get(key, "")
        if value and not value.isascii():
            raise FeedConfigError(
                f"{where}: {key}={value!r} — не-ASCII тут ламає запит. "
                "Пиши значення так, як воно записане на сайті латиницею."
            )

    return params
