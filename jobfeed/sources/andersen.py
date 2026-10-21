"""Andersen — внутрішній JSON-API кар'єрки однієї компанії.

За спекою `specs/SOURCE__andersen.md`. Найдешевше джерело в проєкті: один
запит, 19 структурованих полів, чистий заголовок, справжні теги. І найменш
цінне: під профіль підходять 5 вакансій із 212, жодної в Україні.

Дві особливості, які міняють поведінку ядра — обидві не обхідні:

1. **Жодного поля з датою.** Перевірено по всіх ключах усіх 212 записів:
   ні публікації, ні оновлення, ні сортування. Тому `posted_at` тут чесно
   `None`, а не вигадана дата забору. Наслідки самі собою правильні:
   • «нове» для цього джерела визначається ВИКЛЮЧНО різницею множин id з
     попереднім прогоном — тобто база не оптимізація дедупу, а єдине
     джерело поняття новизни;
   • сигнал «зникла з фіда» до Andersen не застосовується автоматично:
     він вимагає щонайменше двох записів З ДАТАМИ, щоб порахувати вікно.
     Спеціального винятку писати не треба — умова відсікає його сама.

2. **Ендпоінт не приймає параметрів.** Він завжди віддає весь масив, тому
   фільтрація тільки клієнтська — зате по структурованих полях, а не по
   заголовку. Той самий підхід, що в HappyMonday.

⚠️ Адреса API зашита в їхній фронтенд-бандл, а не в конфіг. При редеплої з
іншим хостом адаптер відвалиться — це очікуваний спосіб поломки, і він
голосний (мережева помилка), а не тихий.
"""

from __future__ import annotations

from typing import Any, Iterable

import httpx

from ..models import Vacancy, clean
from .feed_params import FeedSpec, validate

API_URL = "https://asite-api.andersenlab.com/api/integration/recruitment/vacancies"
VACANCY_URL = "https://people-andersenlab.com/vacancy/{uid}"
USER_AGENT = "Mozilla/5.0 (compatible; find_new-jobfeed/0.1; personal job alerts)"

COMPANY = "Andersen"  # кар'єрка однієї компанії, поля в джерелі немає

SPEC = FeedSpec(
    source="andersen",
    # Параметрів на боці джерела немає взагалі, тож і білому списку нема чого
    # пропускати: `specializations` — НАШ клієнтський фільтр, не запит.
    allowed=frozenset({"specializations", "levels"}),
    narrowing=frozenset({"specializations"}),
    hint="Ендпоінт віддає весь масив; звуження робиться на нашому боці.",
)


def _values(raw: str) -> set[str]:
    return {v.strip().lower() for v in raw.split(",") if v.strip()}


def _blocks_to_text(*blocks: Any) -> str:
    """`[{TYPE: text|list, content: str|[str]}]` → суцільний текст.

    Опис потрібен для детекції бронювання, тому склеюємо всі блоки, які
    джерело дає, і нічого не вигадуємо, коли їх немає.
    """
    parts: list[str] = []
    for block in blocks:
        if not isinstance(block, list):
            continue
        for item in block:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                parts.extend(str(c) for c in content)
    return clean(" ".join(parts))


def _location(record: dict) -> str:
    """Країни + формат роботи. Все з полів, нічого з здогадок."""
    parts: list[str] = []
    for place in record.get("lookingLocations") or []:
        if isinstance(place, dict):
            for city in place.get("cities") or []:
                if city:
                    parts.append(str(city))
            name = place.get("countryName")
            if name:
                parts.append(str(name))
    if record.get("isWorldWide"):
        parts.append("worldwide")
    if record.get("isRemote"):
        # Те саме слово, що вживають інші джерела, — щоб чип формату роботи
        # на картці впізнавався незалежно від того, звідки вакансія.
        parts.append("віддалено")

    seen, ordered = set(), []
    for part in parts:
        if part.lower() not in seen:
            seen.add(part.lower())
            ordered.append(part)
    return ", ".join(ordered)


def _salary(record: dict) -> str:
    money = record.get("salary")
    if not isinstance(money, dict):
        return ""
    low, high = money.get("minSalary"), money.get("maxSalary")
    currency = str(money.get("currencyCode") or "").strip()
    if low is None and high is None:
        return ""
    figure = f"{low}–{high}" if low is not None and high is not None and low != high else str(
        low if low is not None else high
    )
    symbol = {"USD": "$", "EUR": "€", "UAH": "₴"}.get(currency.upper(), currency)
    return f"{symbol}{figure}" if symbol in ("$", "€", "₴") else f"{figure} {symbol}".strip()


class AndersenSource:
    name = "andersen"
    kind = "company"  # власний сайт однієї компанії, а не борд

    # Віддає весь масив щоразу — отже повна вибірка. Але сигнал зникнення
    # усе одно не спрацює: без дат вікно не рахується (див. модульний опис).
    scans_full_feed = True
    # Компанія — константа (кар'єрка однієї компанії), локація й зарплата
    # структуровані. Дати немає в принципі, тож і поля з нею не оголошуємо.
    provides = frozenset({"company", "location", "salary"})

    def __init__(self, feeds: list[dict], timeout: float = 30.0) -> None:
        specs = validate(feeds, SPEC)
        self.specializations = {
            v for spec in specs for v in _values(spec.get("specializations", ""))
        }
        self.levels = {v for spec in specs for v in _values(spec.get("levels", ""))}
        self.timeout = timeout
        self.warnings: list[str] = []
        self.fetch_complete = True
        self.feed_sizes: dict[str, int] = {}

    def fetch(self) -> Iterable[Vacancy]:
        with httpx.Client(
            timeout=self.timeout,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        ) as client:
            response = client.get(API_URL)
            response.raise_for_status()
            records = response.json()

        if not isinstance(records, list) or not records:
            self.fetch_complete = False
            self.warnings.append("API віддав порожній або несподіваний масив")
            return

        label = ",".join(sorted(self.specializations)) or "all"
        wanted = [r for r in records if isinstance(r, dict) and self._wanted(r)]
        self.feed_sizes[label] = len(wanted)

        seen: set[str] = set()
        for record in wanted:
            vacancy = self._to_vacancy(record, label)
            if vacancy and vacancy.source_uid not in seen:
                seen.add(vacancy.source_uid)
                yield vacancy

    def _wanted(self, record: dict) -> bool:
        """Клієнтський фільтр по структурованих полях, не по заголовку."""
        if self.specializations:
            have = {str(s).lower() for s in record.get("specializations") or []}
            matrix = record.get("matrix")
            if isinstance(matrix, dict) and matrix.get("specialization"):
                have.add(str(matrix["specialization"]).lower())
            if not (have & self.specializations):
                return False
        if self.levels:
            if str(record.get("level") or "").lower() not in self.levels:
                return False
        return True

    def _to_vacancy(self, record: dict, label: str) -> Vacancy | None:
        uid = str(record.get("vacancy_id") or "").strip()
        title = clean(str(record.get("name") or ""))
        if not uid or not title:
            return None

        tags = [str(s) for s in (record.get("specializations") or []) if s]
        tags += [str(t) for t in (record.get("technologies") or []) if t]
        level = record.get("level")
        if level:
            tags.append(str(level))

        return Vacancy(
            source=self.name,
            title=title,
            company=COMPANY,
            location=_location(record),
            url=VACANCY_URL.format(uid=uid),
            tags=tuple(dict.fromkeys(tags)),
            # Дати в джерелі немає — і вигадувати її не можна: підставивши
            # час забору, ми зробили б усі вакансії «щойно опублікованими».
            posted_at=None,
            source_uid=uid,
            salary=_salary(record),
            description=_blocks_to_text(
                record.get("summary"),
                record.get("responsibilities"),
                record.get("requirements"),
                record.get("niceToHave"),
            ),
            feed=label,
        )
