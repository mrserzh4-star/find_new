"""Парсер schema.org JobPosting з JSON-LD — СПІЛЬНИЙ для всіх джерел.

Живе окремо від будь-якого адаптера навмисно. `JobPosting` — не саморобна
розмітка конкретного сайту, а стандарт schema.org, який джоб-борди тримають
заради Google for Jobs. Ламати його їм невигідно, і підтримує його безліч
сайтів. Тому спільний парсер + ~30 рядків «як дістати список посилань» =
нове джерело. Це головна економія на всіх майбутніх адаптерах — дорожча за
будь-яке окреме джерело.

Модуль нічого не знає ні про Djinni, ні про мережу: на вхід рядок HTML,
на вихід нормалізовані поля. Так його можна тестувати збереженою сторінкою.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator

_LD_BLOCK = re.compile(
    r'<script[^>]*type\s*=\s*["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
_TAG = re.compile(r"<[^>]+>")


@dataclass
class JobPostingFields:
    """Те, що вдалось прочитати. Порожнє поле = у розмітці його не було."""

    title: str = ""
    company: str = ""
    location: str = ""
    salary: str = ""
    description: str = ""
    url: str = ""
    identifier: str = ""
    employment_type: str = ""
    experience: str = ""
    # Досвід у місяцях, як його дає schema.org. Ціле число, а не рядок:
    # форматувати «36 місяців» у «3+ роки» — справа показу, не парсера.
    experience_months: int | None = None
    posted_at: datetime | None = None
    tags: tuple[str, ...] = field(default_factory=tuple)
    # Поле досвіду було, але число з нього не читається. Не те саме, що
    # «джерела не дало»: тут структура на місці, змінився лише формат — і
    # саме так ознака зникає тихо, без жодної помилки.
    experience_unparsed: bool = False
    # Скільки дат довелось відкинути через відсутній часовий пояс.
    # Потрібне, щоб «дати немає» і «дату відкинуто» не виглядали однаково:
    # це різні стани, і мовчазне порожнє поле сховало б другий.
    naive_dates_dropped: int = 0


def _walk(node: Any) -> Iterator[dict]:
    """Обходить будь-яку форму документа: обʼєкт, масив, `@graph`."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            if isinstance(value, (dict, list)):
                yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _is_job_posting(node: dict) -> bool:
    types = node.get("@type")
    if isinstance(types, str):
        return types == "JobPosting"
    return isinstance(types, list) and "JobPosting" in types


def find(html: str) -> dict | None:
    """Перший блок JSON-LD з `@type: JobPosting`, або None.

    Блоків на сторінці буває кілька (хлібні крихти, організація), і зіпсований
    JSON в одному з них не має ламати решту — тому кожен розбирається окремо.
    """
    for raw in _LD_BLOCK.findall(html):
        try:
            document = json.loads(raw.strip())
        except (json.JSONDecodeError, ValueError):
            continue
        for node in _walk(document):
            if _is_job_posting(node):
                return node
    return None


def _text(value: Any) -> str:
    """schema.org дозволяє і рядок, і список, і обʼєкт з `name`."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return ", ".join(t for t in (_text(v) for v in value) if t)
    if isinstance(value, dict):
        return _text(value.get("name") or value.get("value") or "")
    return ""


def _when(value: Any) -> tuple[datetime | None, bool]:
    """ISO-8601 → (tz-aware datetime або None, чи відкинуто наївну дату).

    🔴 Наївну дату НЕ інтерпретуємо, а відкидаємо. Раніше тут стояло
    «вважаємо UTC», і це була вигадка того самого роду, що компанія
    «Freelance», виведена з тире, — тільки гірша, бо виглядала точною до
    мікросекунди.

    Живий приклад, на якому це видно. Djinni віддає ту саму публікацію двічі:

        RSS pubDate       Mon, 27 Jul 2026 13:02:06 +0300   ← з поясом
        JSON-LD datePosted 2026-07-27T13:02:06.539953       ← той самий
                                                              настінний час,
                                                              але без поясу

    Тобто наївне значення тут київське, а «вважаємо UTC» зсувало б його на
    три години. Не падало б: дата лишалась валідною, сортування працювало б,
    просто «щойно» означало б «три години тому».

    Правило, ширше за цей випадок: СПІЛЬНИЙ парсер повертає значення лише
    тоді, коли воно однозначне. Неоднозначне він не інтерпретує — він його
    не повертає. Знання про пояс конкретного сайту, якщо колись знадобиться,
    живе в адаптері того сайту, а не тут.
    """
    raw = _text(value)
    if not raw:
        return None, False
    try:
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None, False
    return (moment, False) if moment.tzinfo else (None, True)


def _addresses(node: dict, key: str) -> list[str]:
    places = node.get(key)
    if isinstance(places, dict):
        places = [places]
    if not isinstance(places, list):
        return []

    parts: list[str] = []
    for place in places:
        if not isinstance(place, dict):
            parts.append(_text(place))
            continue
        address = place.get("address")
        if not isinstance(address, dict):
            parts.append(_text(address) or _text(place))
            continue
        city = _text(address.get("addressLocality"))
        region = _text(address.get("addressRegion"))
        country = _text(address.get("addressCountry"))
        parts.extend(p for p in (city, region, country) if p)

    return parts


# Слово те саме, що вживає DOU у своїх локаціях, — щоб картка виглядала
# однаково незалежно від джерела, і щоб чип формату роботи впізнавався.
REMOTE_LABEL = "віддалено"


def _location(node: dict) -> str:
    """`jobLocation` (+ ознака віддаленої роботи) → людський рядок.

    `jobLocationType: "TELECOMMUTE"` — стандартний спосіб schema.org сказати
    «віддалено», і саме він стоїть у вакансій без адреси. Без нього такі
    картки лишались би зовсім без локації, хоча дані є: у Djinni це кожна
    друга вакансія. `applicantLocationRequirements` при цьому каже, з якого
    регіону можна працювати, — теж реальні дані, не здогадка.
    """
    parts = _addresses(node, "jobLocation")
    remote = _text(node.get("jobLocationType")).upper() == "TELECOMMUTE"
    if remote:
        parts.append(REMOTE_LABEL)
        parts.extend(_addresses(node, "applicantLocationRequirements"))

    # Порядок зберігаємо, дублі прибираємо: країна часто повторюється.
    seen, ordered = set(), []
    for part in parts:
        for piece in part.split(", "):
            if piece and piece.lower() not in seen:
                seen.add(piece.lower())
                ordered.append(piece)
    return ", ".join(ordered)


_PERIOD = {
    "HOUR": "год", "DAY": "день", "WEEK": "тиждень",
    "MONTH": "міс", "YEAR": "рік",
}


def _salary(node: dict) -> str:
    """`baseSalary` → «$2000–2500 / міс». Нічого не рахуємо й не переводимо."""
    base = node.get("baseSalary")
    if not isinstance(base, dict):
        return _text(base)

    currency = _text(base.get("currency") or base.get("currencyCode"))
    amount = base.get("value")
    if not isinstance(amount, dict):
        value = _text(amount)
        return f"{value} {currency}".strip() if value else ""

    low, high = amount.get("minValue"), amount.get("maxValue")
    exact = amount.get("value")
    if low is not None and high is not None and low != high:
        figure = f"{_num(low)}–{_num(high)}"
    else:
        figure = _num(exact if exact is not None else (low if low is not None else high))
    if not figure:
        return ""

    symbol = {"USD": "$", "EUR": "€", "UAH": "₴"}.get(currency.upper(), currency)
    period = _PERIOD.get(_text(amount.get("unitText")).upper(), "")
    joined = f"{symbol}{figure}" if symbol in ("$", "€", "₴") else f"{figure} {symbol}".strip()
    return f"{joined} / {period}" if period else joined


def _num(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value)


def _experience_months(node: dict) -> tuple[int | None, bool]:
    """`experienceRequirements` → (місяці, чи не вдалось розпізнати число).

    Значення лежить у ВКЛАДЕНОМУ полі об'єкта `OccupationalExperienceRequirements`,
    а не в `name`/`value`, тому загальний `_text()` його не бачить і мовчки
    віддає порожній рядок. Саме так це поле й губилось: воно ніби «парсилось»,
    але результат був порожній на всіх без винятку вакансіях.

    Число приймаємо і рядком: `"24"` — не екзотика, а найімовірніший
    сценарій регресії. JSON-LD генерує шаблонізатор, і після будь-якої зміни
    на боці сайту `36.0` цілком може стати `"36"`. Розмітка лишиться
    валідною по schema.org, нічого не впаде — просто всі чипи досвіду тихо
    зникнуть. `"3+ years"` натомість НЕ розбираємо: це вже вгадування.
    """
    node_value = node.get("experienceRequirements")
    if not isinstance(node_value, dict) or "monthsOfExperience" not in node_value:
        return None, False

    months = node_value["monthsOfExperience"]
    # Явний `null` — це джерело каже «значення немає», а не зміна формату.
    # Позначати його як нерозпізнане означало б засвітити попередження там,
    # де все чесно, і привчити до нього око.
    if months is None:
        return None, False
    if isinstance(months, bool):  # bool — підклас int, але не кількість місяців
        return None, True
    if isinstance(months, (int, float)):
        return int(months), False
    if isinstance(months, str):
        try:
            return int(float(months.strip())), False
        except (TypeError, ValueError):
            return None, True
    # Поле є, але прочитати його ми не змогли — це не «джерело не дало».
    return None, True


def parse(node: dict) -> JobPostingFields:
    """Вузол JobPosting → наші поля. Чого в розмітці не було — лишається порожнім."""
    tags = tuple(
        t for t in (
            _text(node.get("category")),
            _text(node.get("industry")),
            _text(node.get("occupationalCategory")),
        ) if t
    )
    description = _text(node.get("description"))
    months, months_unparsed = _experience_months(node)
    posted_at, posted_naive = _when(node.get("datePosted"))
    return JobPostingFields(
        title=_text(node.get("title")),
        company=_text(node.get("hiringOrganization")),
        location=_location(node),
        salary=_salary(node),
        description=" ".join(_TAG.sub(" ", description).split()),
        url=_text(node.get("url")),
        identifier=_text(node.get("identifier")),
        employment_type=_text(node.get("employmentType")),
        experience=_text(node.get("experienceRequirements")),
        experience_months=months,
        experience_unparsed=months_unparsed,
        posted_at=posted_at,
        tags=tags,
        naive_dates_dropped=int(posted_naive),
    )


def from_html(html: str) -> JobPostingFields | None:
    node = find(html)
    return parse(node) if node is not None else None
