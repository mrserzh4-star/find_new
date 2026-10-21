"""DOU (jobs.dou.ua) — RSS 2.0. Ядро сервісу.

Побудовано за спекою `specs/SOURCE__dou.md` (заміри researcher-а на живих даних).
Чотири речі тут виглядають надлишковими, але кожна закриває підтверджену пастку:

1. **Лише категорійні/пошукові фіди.** Нефільтрований фід — 50 записів на 1 год 14 хв,
   пагінації в RSS немає (`page=2` віддає ті самі ID). При щогодинному запуску запас
   14 хвилин: у піковий день вакансії губляться безповоротно. `?category=Design` —
   25 записів на 4 доби 18 год. Тому фід без жодного звужувального параметра —
   помилка конфігурації, а не «дефолт».
2. **Білий список параметрів.** DOU мовчки ігнорує невідомий параметр: `?company=fuib`
   дає HTTP 200 і 50 записів, з яких жоден не стосується fuib. Одруківка в назві
   параметра не падає, а тихо підміняє вибірку на «всі вакансії». Тому невідомий
   ключ = явна помилка на старті.
3. **Подвійне екранування.** Заголовки містять `&amp;nbsp;` — один прохід unescape
   лишає `&nbsp;` у видимому тексті. Зачеплено 5 заголовків із 75.
4. **Ключ дедупу — числовий ID з URL, не `guid`.** guid містить unix-час; при
   піднятті вакансії він майже напевно зміниться і дасть дубль.

Чого фід НЕ дає: окремих полів `company` і `location` (склеєні в заголовок)
і `<category>`. У `tags` кладемо тільки власну категорію запиту.
"""

from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from typing import Iterable

import feedparser
import httpx

from ..models import Vacancy, clean
from .feed_params import FeedConfigError, FeedSpec, validate

FEED_URL = "https://jobs.dou.ua/vacancies/feeds/"
USER_AGENT = "Mozilla/5.0 (compatible; find_new-jobfeed/0.1; personal job alerts)"

SPEC = FeedSpec(
    source="dou",
    allowed=frozenset({"category", "exp", "remote", "city", "search"}),
    narrowing=frozenset({"category", "search", "city"}),
    # Кирилиця в category дає HTTP 400 — назва категорії на сайті латиницею.
    ascii_only=frozenset({"category"}),
    hint="Нефільтрований фід DOU покриває лише ~1 год 14 хв.",
)

_VACANCY_URL = re.compile(r"/companies/(?P<slug>[^/]+)/vacancies/(?P<uid>\d+)/")
_TAG = re.compile(r"<[^>]+>")
_SALARY_HINT = re.compile(r"[$€₴]|\bгрн\b|\bвід\s|\bдо\s", re.IGNORECASE)


def unescape(text: str) -> str:
    """Два проходи: у фіді сидить `&amp;nbsp;`, один прохід лишає `&nbsp;`."""
    return html.unescape(html.unescape(text or ""))


def split_title(raw_title: str) -> tuple[str, str, str, str]:
    """`{посада} в {компанія}[, {зарплата}][, {локація}]` → чотири поля.

    Ділимо по ОСТАННЬОМУ ` в ` (`rsplit`): на 75 живих заголовках роздільник
    присутній 75/75, неоднозначних 0. Далі не можна брати «останній сегмент =
    локація»: у 4 з 75 усередині сидить зарплата, тому сегменти розбираємо
    за вмістом, а не за позицією.
    """
    title = clean(unescape(raw_title))
    if " в " not in title:
        return title, "", "", ""

    position, rest = title.rsplit(" в ", 1)
    parts = [p.strip() for p in rest.split(",") if p.strip()]
    company = parts[0] if parts else ""

    salary_parts, location_parts = [], []
    for part in parts[1:]:
        (salary_parts if _SALARY_HINT.search(part) else location_parts).append(part)

    return position.strip(), company, ", ".join(location_parts), ", ".join(salary_parts)


def _plain(html_text: str) -> str:
    return clean(unescape(_TAG.sub(" ", html_text or "")))


def _posted_at(entry) -> datetime | None:
    parsed = getattr(entry, "published_parsed", None)
    return datetime(*parsed[:6], tzinfo=timezone.utc) if parsed else None


class DouSource:
    """Читає список категорійних/пошукових фідів DOU."""

    name = "dou"
    kind = "board"  # джоб-борд: публікуються всі компанії
    # Компанія й локація витягуються із заголовка, зарплата — звідти ж,
    # коли роботодавець її вказав. Досвіду й типу зайнятості фід не дає.
    provides = frozenset({"company", "location", "salary"})

    def __init__(self, feeds: list[dict], timeout: float = 30.0) -> None:
        self.feeds = validate(feeds, SPEC)
        self.timeout = timeout
        self.warnings: list[str] = []
        self.fetch_complete = True
        # Розмір кожного фіда цього прогону — див. `feed_sizes` у djinni.py.
        self.feed_sizes: dict[str, int] = {}
        # Стан кожного фіда окремо — див. коментар у djinni.py.
        self.feed_health: dict[str, dict] = {}

    @staticmethod
    def _label(params: dict[str, str]) -> str:
        """Мітка фіда — вона ж єдиний вміст `tags`: власна категорія запиту."""
        return params.get("category") or params.get("search") or params.get("city", "")

    def fetch(self) -> Iterable[Vacancy]:
        seen: set[str] = set()
        with httpx.Client(
            timeout=self.timeout,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        ) as client:
            for params in self.feeds:
                label = self._label(params)
                try:
                    response = client.get(FEED_URL, params=params)
                    response.raise_for_status()
                except Exception as exc:
                    # Один мертвий фід не валить решту, але забір стає
                    # НЕПОВНИМ — інакше сигнал зникнення зарахував би за
                    # доказ відсутність у фіді, який не відповів.
                    self.fetch_complete = False
                    self.feed_health[label] = {"state": "failed", "count": 0,
                                               "error": f"{type(exc).__name__}: {exc}"}
                    self.feed_sizes[label] = 0
                    self.warnings.append(f"фід {label}: {type(exc).__name__}: {exc}")
                    continue
                entries = feedparser.parse(response.content).entries
                # Те саме значення, що йде у `feed` запису. Розбіжність тут
                # безпечна за наслідком, але брехлива за причиною: ключі не
                # зійшлись би, і у виводі стояло б «вікна не існує» замість
                # «немає мітки фіда».
                self.feed_sizes[label] = len(entries)
                self.feed_health[label] = {
                    "state": "ok" if entries else "failed",
                    "count": len(entries),
                    "error": "" if entries else
                             "0 записів — найімовірніше неправильне значення параметра",
                }
                if not entries:
                    # Неіснуюча категорія віддає HTTP 200 і порожній фід —
                    # помилки не буде, тому одруківку в ЗНАЧЕННІ параметра
                    # видно тільки так. Білий список ловить лише ключі.
                    self.fetch_complete = False
                    self.warnings.append(
                        f"фід {params} віддав 0 записів — найімовірніше "
                        "неправильне значення параметра (DOU не вважає це помилкою)"
                    )
                for entry in entries:
                    vacancy = self._to_vacancy(entry, label)
                    if vacancy and vacancy.source_uid not in seen:
                        seen.add(vacancy.source_uid)
                        yield vacancy

    def _to_vacancy(self, entry, label: str) -> Vacancy | None:
        match = _VACANCY_URL.search(getattr(entry, "link", "") or "")
        if not match:
            # Не картка вакансії (сервісний елемент фіда) — пропускаємо.
            return None

        position, company, location, salary = split_title(getattr(entry, "title", ""))
        return Vacancy(
            source=self.name,
            title=position,
            company=company,
            location=location,
            url=f"https://jobs.dou.ua{match.group(0)}",  # канонічний, без utm
            tags=(label,) if label else (),
            feed=label,
            posted_at=_posted_at(entry),
            source_uid=match.group("uid"),
            salary=salary,
            description=_plain(getattr(entry, "description", "")),
        )
