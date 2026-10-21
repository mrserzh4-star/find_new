"""Фіди окремих компаній на DOU — `/vacancies/<slug>/feeds/`.

Навіщо, якщо DOU вже підключений
--------------------------------
Це не інше джерело, а **глибше в часі**. Категорійний фід `?category=Design`
тримає 25 записів ≈ 3-4 доби. Фіди компаній тримають 14-43 доби. Тому
вакансія, старша за чотири доби, у категорійному фіді не просто відсутня —
її там не може бути в принципі.

Заміряно на живих даних: із 5 дизайнерських вакансій, знайдених у фідах
компаній (SQUAD, Genesis, Sigma), **у нашій БД не було жодної**, і всі п'ять
старші за вікно категорії в 3-9 разів. Серед них `Senior Product Designer`
і `Senior Graphic Designer` — точно в профілі власника.

🔴 Ми вже закривали цей напрям на замірі «0 з 21 компанії не входять у топ-50
категорійного фіда». Замір був правильний, висновок — ні: він дивився на
чотиридобове вікно й нічого не міг сказати про вакансії, старші за нього.
Наскрізна форма помилки цієї сесії: **поміряли один зріз, ствердили про сусідній.**

Чому раз на добу, а не щогодини
-------------------------------
Медіанний темп — 0.88 нової вакансії на добу на компанію, частка дизайну 3%.
Щогодинно 21 фід це 504 запити на добу заради ~0.2 дизайнерської вакансії.
І головне: **цінність цих фідів у глибині, а не у свіжості.** Нова вакансія
Genesis потрапляє в категорійний фід DOU і прилітає до нас протягом години
звичайним шляхом. Ці фіди добирають те, що висить довше за вікно категорії,
тому добова частота нічого не затримує.

Пастки, підтверджені замірами
-----------------------------
1. **Фільтрувати за РОЗІБРАНОЮ посадою, не за сирим заголовком.** Заголовок
   містить назву компанії: `Luxoft` містить `ux`, `Recruitment` і `Suits`
   містять `ui`. Без розбору всі 8 вакансій Luxoft стали б UX/UI.
   Тут це безкоштовно: `split_title` уже кладе в `title` саму посаду.
2. **Неіснуючий слаг дає 404**, а не тихі 50 чужих записів, як `?company=`.
   Одруківка тут гучна — і саме тому слаг приймається лише як слаг, а не як
   довільний параметр.
3. **Збіг слага на ATS не є доказом тотожності компанії.** До цього модуля
   не стосується (ATS ми не підключаємо), але правило належить сюди ж:
   HTTP 200 доводить, що слаг існує, а не що він ваш.
"""

from __future__ import annotations

import re
from typing import Iterable

import feedparser
import httpx

from ..models import Vacancy
from .dou import USER_AGENT, _plain, _posted_at, _VACANCY_URL, split_title

FEED_URL = "https://jobs.dou.ua/vacancies/{slug}/feeds/"

# Назва компанії в заголовку фіда: «Вакансії «X» на DOU.ua».
#
# Потрібна саме тому, що вона є й у ПОРОЖНЬОГО фіда: компанію, за якою ми
# стежимо, треба показати з нулем — а слаг (`at-oschadbank`) назвою не є.
_FEED_TITLE = re.compile(r"Вакансії\s+«(.+?)»\s+на\s+DOU", re.IGNORECASE)

_SLUG_ALLOWED = set("abcdefghijklmnopqrstuvwxyz0123456789-_.")


class SlugError(ValueError):
    """Неприйнятний слаг — помилка конфігурації, а не збій мережі."""


def check_slug(slug: str) -> str:
    """Слаг має бути слагом.

    Не косметика: сюди підуть значення, які власник додаватиме через форму,
    і рядок на кшталт `../../` або цілий URL мусить упасти тут, а не
    перетворитись на запит кудись у бік.
    """
    value = (slug or "").strip().lower()
    if not value:
        raise SlugError("порожній слаг компанії")
    bad = sorted(set(value) - _SLUG_ALLOWED)
    if bad:
        raise SlugError(
            f"слаг {slug!r} містить неприпустимі символи: {''.join(bad)}. "
            "Потрібен саме слаг зі сторінки компанії на DOU "
            "(jobs.dou.ua/companies/<слаг>/), а не URL."
        )
    return value


class DouCompaniesSource:
    """Читає фіди перелічених компаній DOU. Один слаг — один фід."""

    name = "dou_companies"
    # Сайт компанії, не джоб-борд: у фіді лише вакансії цього роботодавця.
    kind = "company"
    provides = frozenset({"company", "location", "salary"})

    def __init__(self, slugs: list[str], timeout: float = 30.0,
                 every_hours: float = 24.0) -> None:
        self.slugs = [check_slug(s) for s in slugs]
        self.timeout = timeout
        # Як часто це джерело взагалі має забиратись. Читає `pipeline.collect`.
        self.every_hours = float(every_hours)
        self.warnings: list[str] = []
        self.fetch_complete = True
        self.feed_sizes: dict[str, int] = {}
        # Стан КОЖНОГО слага окремо. Джерело з 21 фіда, де впав один, на рівні
        # джерела виглядає здоровим — рівно та поломка, яку ми щойно ловили
        # рівнем вище, тільки всередині одного адаптера.
        self.feed_health: dict[str, dict] = {}

    def fetch(self) -> Iterable[Vacancy]:
        seen: set[str] = set()
        with httpx.Client(
            timeout=self.timeout,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        ) as client:
            for slug in self.slugs:
                yield from self._fetch_one(client, slug, seen)

    def _fetch_one(self, client, slug: str, seen: set[str]) -> Iterable[Vacancy]:
        # Один мертвий слаг не має валити решту двадцяти: він стає рядком із
        # причиною, а не винятком на весь прогін.
        try:
            response = client.get(FEED_URL.format(slug=slug))
        except Exception as exc:
            self.feed_health[slug] = {"state": "failed", "count": 0,
                                      "error": f"{type(exc).__name__}: {exc}"}
            self.warnings.append(f"{slug}: {type(exc).__name__}: {exc}")
            self.feed_sizes[slug] = 0
            return

        if response.status_code != 200:
            reason = (f"HTTP {response.status_code}"
                      + (" — такої компанії на DOU немає"
                         if response.status_code == 404 else ""))
            self.feed_health[slug] = {"state": "failed", "count": 0, "error": reason}
            self.warnings.append(f"{slug}: {reason}")
            self.feed_sizes[slug] = 0
            return

        parsed = feedparser.parse(response.content)
        entries = parsed.entries
        self.feed_sizes[slug] = len(entries)
        title = _FEED_TITLE.search(parsed.feed.get("title", "") or "")
        # Нуль тут — НЕ помилка й не привід ховати компанію. У шести з
        # двадцяти одної компанії просто немає відкритих вакансій, і власник
        # тричі підтвердив: нуль сьогодні не привід зникати з переліку.
        self.feed_health[slug] = {
            "state": "ok" if entries else "empty",
            "count": len(entries),
            "error": "",
            "title": title.group(1).strip() if title else "",
        }
        for entry in entries:
            vacancy = self._to_vacancy(entry, slug)
            if vacancy and vacancy.source_uid not in seen:
                seen.add(vacancy.source_uid)
                yield vacancy

    def _to_vacancy(self, entry, slug: str) -> Vacancy | None:
        match = _VACANCY_URL.search(getattr(entry, "link", "") or "")
        if not match:
            return None
        # `split_title` віддає саме ПОСАДУ окремо від компанії — і фільтр далі
        # працює по ній. Без цього назва компанії потрапляла б під ключі:
        # `Luxoft` містить `ux`, `DigitalSuits` і `NDA Recruitment` — `ui`.
        position, company, location, salary = split_title(getattr(entry, "title", ""))
        return Vacancy(
            source=self.name,
            title=position,
            company=company,
            location=location,
            url=f"https://jobs.dou.ua{match.group(0)}",
            tags=(slug,),
            feed=slug,
            posted_at=_posted_at(entry),
            source_uid=match.group("uid"),
            salary=salary,
            description=_plain(getattr(entry, "description", "")),
        )
