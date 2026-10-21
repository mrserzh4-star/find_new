"""Djinni (djinni.co) — RSS для виявлення + JSON-LD для збагачення.

Побудовано за `specs/SOURCE__djinni.md` (заміри researcher-а на живих даних).
Головне джерело проєкту: окремі категорії `UI UX`, `Product Design`,
`Graphic Design` замість однієї звалки `Design` у DOU — тому інженерного
сміття тут нуль і стоп-лист не потрібен.

**Схема 1 + N.** RSS дає список без `company` і `location` (їх у фіді
немає взагалі), сторінка вакансії дає їх у schema.org JSON-LD. Тому
збагачуємо ТІЛЬКИ нові вакансії — ті, яких ще немає в базі. Інакше це
100+ запитів щогодини, тобто поведінка, за яку банять. За звичайний цикл
виходить 4-5 запитів на фіди плюс 0-3 на сторінки.

**Той самий footgun, що й у DOU, тільки гірший:** невалідне значення
`primary_keyword` не падає, а тихо віддає ПОВНИЙ нефільтрований фід зі 100
вакансій усіх спеціальностей. Тому тут дві страховки: білий список
параметрів і перевірка інваріанта — кожен запис у відповіді мусить мати
`<category>`, що дорівнює запитаній.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Iterable
from xml.etree import ElementTree

import httpx

from ..models import Vacancy, clean
from . import jsonld_jobposting
from .feed_params import FeedConfigError, FeedSpec, validate

RSS_URL = "https://djinni.co/jobs/rss/"
USER_AGENT = "Mozilla/5.0 (compatible; find_new-jobfeed/0.1; personal job alerts)"

SPEC = FeedSpec(
    source="djinni",
    allowed=frozenset({"primary_keyword", "editorial", "exp_level", "employment"}),
    narrowing=frozenset({"primary_keyword"}),
    # Невалідне значення `editorial` тихо знімає фільтр — перевіряємо і його.
    values={"editorial": frozenset({"reservation"})},
    hint="Нефільтрований фід Djinni покриває лише ~69 хвилин.",
)

_JOB_ID = re.compile(r"/jobs/(\d+)-")
_TAG = re.compile(r"<[^>]+>")


def _posted_at(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        moment = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    return moment.astimezone(timezone.utc) if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


class DjinniSource:
    name = "djinni"
    kind = "board"  # джоб-борд: публікуються всі компанії
    # Які поля це джерело взагалі дає. Оголошення, а не спостереження:
    # виведене з даних відповідає на питання «що ми бачили», а потрібне —
    # «що джерело дає», і різниця між ними видна рівно в момент поломки.
    # Тут майже все: компанія, локація, зарплата й досвід беруться зі
    # сторінки вакансії (JSON-LD), решта з RSS.
    provides = frozenset(
        {"company", "location", "salary", "experience_months", "employment_type"}
    )

    def __init__(
        self,
        feeds: list[dict],
        timeout: float = 30.0,
        enrich_pause: float = 1.0,
        max_enrich: int = 40,
    ) -> None:
        self.feeds = validate(feeds, SPEC)
        self.timeout = timeout
        self.enrich_pause = enrich_pause
        # Бюджет запитів на ВЕСЬ прогін, спільний для нових вакансій і для
        # добору старих. Обʼєкт джерела живе рівно один прогін, тому лічильник
        # тут же й скидається.
        self.max_enrich = max_enrich
        self._budget = max_enrich
        self.warnings: list[str] = []
        # Повнота саме ЗАБОРУ. Бюджет збагачення деталей її не стосується:
        # список вакансій прочитано цілком, добираються лише поля.
        self.fetch_complete = True
        # Скільки записів віддав КОЖЕН фід цього прогону. Ядро порівнює з
        # минулим разом, щоб зрозуміти, чи фід не стоїть на своїй стелі
        # (див. `track_absence`). Саме по фідах, а не сумарно: один
        # обрізаний фід із п'яти в сумі загубився б.
        self.feed_sizes: dict[str, int] = {}
        # Стан кожного фіда окремо: джерело з пʼяти фідів, де впав один,
        # на рівні джерела виглядає здоровим.
        self.feed_health: dict[str, dict] = {}
        # Дати, відкинуті через відсутній пояс. Зараз має бути нуль: дату
        # беремо з RSS, де пояс є. Ненульове значення означає, що збагачення
        # почало спиратись на JSON-LD — і це треба помітити числом, а не
        # порожнім полем без пояснення.
        self._naive_dates = 0
        # Скільки разів поле досвіду було, але число з нього не прочиталось.
        # Нуль у нормі; ненульове означає, що джерело змінило формат — і без
        # цього числа ми побачили б лише те, що чипи досвіду зникли.
        self._experience_unparsed = 0

    def _client(self) -> httpx.Client:
        return httpx.Client(
            timeout=self.timeout,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        )

    # ── крок 1: виявлення ────────────────────────────────────────────────
    def fetch(self) -> Iterable[Vacancy]:
        seen: set[str] = set()
        with self._client() as client:
            for params in self.feeds:
                label = params.get("primary_keyword", "?")
                try:
                    response = client.get(RSS_URL, params=params)
                    response.raise_for_status()
                except Exception as exc:
                    # Один мертвий фід не має валити решту чотирьох. Але
                    # забір після цього НЕПОВНИЙ, і сигнал зникнення мусить
                    # вимкнутись: відсутність вакансії у фіді, який не
                    # відповів, не є доказом того, що її зняли.
                    self.fetch_complete = False
                    self.feed_health[label] = {"state": "failed", "count": 0,
                                               "error": f"{type(exc).__name__}: {exc}"}
                    self.feed_sizes[label] = 0
                    self.warnings.append(f"фід {label}: {type(exc).__name__}: {exc}")
                    continue
                items = self._parse_rss(response.content, params)
                self.feed_sizes[label] = len(items)
                self.feed_health.setdefault(
                    label, {"state": "ok" if items else "empty",
                            "count": len(items), "error": ""}
                )
                for vacancy in items:
                    if vacancy.source_uid not in seen:
                        seen.add(vacancy.source_uid)
                        yield vacancy

    def _parse_rss(self, payload: bytes, params: dict[str, str]) -> list[Vacancy]:
        label = params.get("primary_keyword", "") or "?"
        try:
            root = ElementTree.fromstring(payload)
        except ElementTree.ParseError as exc:
            self.fetch_complete = False
            self.feed_health[label] = {"state": "failed", "count": 0,
                                       "error": f"не розібрався RSS: {exc}"}
            self.warnings.append(f"фід {params}: не розібрався RSS ({exc})")
            return []

        wanted = params.get("primary_keyword", "")
        vacancies, categories = [], []
        for item in root.iterfind(".//item"):
            vacancy = self._to_vacancy(item, params)
            if vacancy:
                vacancies.append(vacancy)
                categories.append(_text(item, "category"))

        if not vacancies:
            self.fetch_complete = False
            self.feed_health[label] = {
                "state": "failed", "count": 0,
                "error": "0 записів — найімовірніше неправильне значення параметра",
            }
            self.warnings.append(
                f"фід {params} віддав 0 записів — найімовірніше неправильне значення параметра"
            )
            return []

        # Інваріант: фільтр справді спрацював. Якщо параметр тихо зняли,
        # у відповіді будуть категорії всіх спеціальностей підряд — і ми
        # мовчки залили б у стрічку бекенд, QA і рекрутинг.
        if wanted:
            stray = {c for c in categories if c and c.strip().lower() != wanted.strip().lower()}
            if stray:
                self.fetch_complete = False
                self.feed_health[label] = {
                    "state": "failed", "count": 0,
                    "error": "фільтр не спрацював — у відповіді сторонні категорії "
                             f"{sorted(stray)[:3]}, вакансії пропущено",
                }
                self.warnings.append(
                    f"фід {params}: у відповіді сторонні категорії {sorted(stray)[:5]} — "
                    "фільтр не спрацював, вакансії з цього фіда пропущено"
                )
                return []
        return vacancies

    def _to_vacancy(self, item: ElementTree.Element, params: dict[str, str]) -> Vacancy | None:
        link = _text(item, "link") or _text(item, "guid")
        match = _JOB_ID.search(link)
        if not match:
            return None

        category = _text(item, "category")
        return Vacancy(
            source=self.name,
            title=clean(_text(item, "title")),
            company="",   # у фіді немає — заповнюється при збагаченні
            location="",  # так само
            url=link.split("?")[0],
            tags=(category,) if category else (),
            feed=params.get("primary_keyword", ""),
            posted_at=_posted_at(_text(item, "pubDate")),
            source_uid=match.group(1),
            description=clean(_TAG.sub(" ", _text(item, "description"))),
            # Фільтр `editorial=reservation` ставить сам роботодавець, тому
            # його НАЯВНІСТЬ — надійне «так». Відсутність же означає «галочку
            # не поставили», а не «броні немає», і 'no' тут ставити НЕ можна:
            # це заглушило б коректну детекцію по тексту опису.
            reservation_hint="yes" if params.get("editorial") == "reservation" else "",
        )

    # ── крок 2: збагачення (тільки нові) ─────────────────────────────────
    def enrich(self, vacancies: list[Vacancy]) -> list[Vacancy]:
        """Добирає company/location/salary зі сторінки вакансії.

        Викликається ядром ТІЛЬКИ для нових вакансій — саме це робить
        схему 1+N дешевою. Сторінка, що не відкрилась, не валить прогін:
        вакансія просто лишається без компанії та локації.
        """
        if not vacancies or self._budget <= 0:
            return list(vacancies)

        if len(vacancies) > self._budget:
            self.warnings.append(
                f"вакансій на збагачення {len(vacancies)}, бюджет запитів на прогін "
                f"{self.max_enrich} — решту доберемо наступними прогонами"
            )

        enriched: list[Vacancy] = []
        with self._client() as client:
            for index, vacancy in enumerate(vacancies):
                if self._budget <= 0:
                    enriched.append(vacancy)
                    continue
                if index:
                    time.sleep(self.enrich_pause)  # 1+N вимагає ввічливості
                self._budget -= 1
                enriched.append(self._enrich_one(client, vacancy))

        if self._experience_unparsed:
            self.warnings.append(
                f"досвід не прочитався у {self._experience_unparsed} вакансій: поле "
                "monthsOfExperience є, але число з нього не розбирається. "
                "Найімовірніше джерело змінило формат значення"
            )
        if self._naive_dates:
            self.warnings.append(
                f"вакансій лишилось без дати публікації: {self._naive_dates}. "
                "У фіді дати не було, а на сторінці вона без часового поясу — "
                "таке значення однозначно не читається, тож ми його не вгадуємо"
            )
        return enriched

    def _enrich_one(self, client: httpx.Client, vacancy: Vacancy) -> Vacancy:
        try:
            response = client.get(vacancy.url)
            response.raise_for_status()
            fields = jsonld_jobposting.from_html(response.text)
        except Exception as exc:
            self.warnings.append(f"{vacancy.url}: {type(exc).__name__}: {exc}")
            return vacancy

        if fields is None:
            self.warnings.append(f"{vacancy.url}: на сторінці немає JSON-LD JobPosting")
            return vacancy

        # Рахуємо лише ВТРАТУ, а не кожне відкидання. JSON-LD віддає наївні
        # дати завжди, і дві на вакансію — тобто лічильник «скільки відкинуто»
        # був би ненульовий щопрогону. Попередження, яке горить постійно,
        # привчає око себе не читати — той самий аргумент, чому ми не пишемо
        # «Full-time» на 93% карток.
        # Втрата ж настає лише тоді, коли дати з RSS не було й замінити нічим.
        if fields.naive_dates_dropped and vacancy.posted_at is None:
            self._naive_dates += 1
        if fields.experience_unparsed:
            self._experience_unparsed += 1

        # Замінюємо лише те, що сторінка справді дала: збагачення не має
        # права стерти вже відоме з фіда порожнім значенням.
        return replace_filled(
            vacancy,
            company=fields.company,
            location=fields.location,
            salary=fields.salary,
            title=fields.title,
            description=fields.description,
            # posted_at зі сторінки НЕ беремо: у JSON-LD дата без часового
            # поясу, а `pubDate` з RSS уже прийшов із ним. Точніше — те,
            # де зона є, а не те, що виглядає детальнішим.
            posted_at=vacancy.posted_at or fields.posted_at,
            tags=tuple(dict.fromkeys(vacancy.tags + fields.tags)),
            experience_months=fields.experience_months,
            employment_type=fields.employment_type,
        )


def replace_filled(vacancy: Vacancy, **updates) -> Vacancy:
    """`dataclasses.replace`, який ігнорує порожні значення."""
    from dataclasses import replace

    meaningful = {k: v for k, v in updates.items() if v not in ("", None, ())}
    return replace(vacancy, **meaningful) if meaningful else vacancy


def _text(element: ElementTree.Element, tag: str) -> str:
    found = element.find(tag)
    return (found.text or "").strip() if found is not None else ""
