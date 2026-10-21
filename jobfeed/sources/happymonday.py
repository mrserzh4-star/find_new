"""HappyMonday (happymonday.ua) — WordPress REST API.

За спекою `specs/SOURCE__happymonday.md`. Цінність цього джерела — не обсяг
(~22 дизайн-вакансії, як у DOU), а РОБОТОДАВЦІ: перетин з DOU — 2 з 19.
Решта 17 на IT-бордах не зустрічаються взагалі — креативні агенції, медіа
й громадський сектор. Саме той прошарок, який DOU і Djinni не бачать.

Чотири пастки, усі перевірені на живих даних:

1. **`happymonday.ua/feed` — це БЛОГ, а не вакансії.** Виглядає як очевидний
   RSS-кандидат, віддає редакційні статті. Тут не використовується.
2. **`?job_category=dyzajn` мовчки ігнорується** — таксономії не виставлені
   в REST, і запит віддає всі 41 808 записів. Тому фільтруємо КЛІЄНТСЬКИ,
   по `class_list`, а сам параметр заборонений білим списком.
3. **Поля компанії немає** — тільки з заголовка за схемою `{посада} до
   {компанія}` / `{посада} at {компанія}`. Формат не гарантований: виділяється
   в 19 з 22. Не виділилась — лишаємо порожньою, не вигадуємо.
4. **Локація рідка** — країна є лише в 7 з 22. Порожня локація тут нормальний
   стан, а не привід відкидати запис.

Забір інкрементальний: `?after=<мітка минулого прогону>` — на щогодинний
цикл це один запит на одиниці записів. Без мітки (перший прогін) береться
вікно дайджесту, і вмикається пагінація — від СТАРІШИХ до новіших.

Порядок саме такий не з естетики. Стеля сторінок захищає від того, щоб
перший прогін не тягнув усю історію, але вона ж означає, що частину
діапазону ми не прочитали. При заборі від новіших відрізаним лишався б
найстаріший хвіст, а мітка все одно поїхала б уперед — і той хвіст не
повернувся б НІКОЛИ, причому в логах усе виглядало б здорово. Тому: забір
від старіших, а при вичерпанні стелі мітка запам'ятовує ту саму дату й
номер сторінки, з якої продовжити (`<час>|page:<n>`).
"""

from __future__ import annotations

import html
import re
from datetime import datetime, timedelta, timezone
from typing import Iterable

import httpx

from ..models import Vacancy, clean
from .feed_params import FeedSpec, validate

API_URL = "https://happymonday.ua/wp-json/wp/v2/job"
USER_AGENT = "Mozilla/5.0 (compatible; find_new-jobfeed/0.1; personal job alerts)"

PER_PAGE = 100          # стеля WP: 101 чесно віддає HTTP 400
MAX_PAGES = 10          # запобіжник, щоб перший прогін не тягнув усю історію
OVERLAP = timedelta(days=1)  # перекриття, щоб невдалий прогін нічого не загубив

SPEC = FeedSpec(
    source="happymonday",
    # `job_category` свідомо ЗАБОРОНЕНИЙ: сервер його ігнорує й віддає всі
    # 41 808 записів. Категорія задається окремим полем `categories` нижче
    # і застосовується клієнтськи.
    allowed=frozenset({"categories", "global_categories"}),
    narrowing=frozenset({"categories"}),
    hint="Категорія тут фільтрується на нашому боці, по class_list.",
)

_COMPANY = re.compile(r"\s+(?:до|at)\s+", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")


def split_title(raw_title: str) -> tuple[str, str]:
    """`{посада} до {компанія}` → (посада, компанія).

    Ділимо по ОСТАННЬОМУ роздільнику: «до» трапляється і всередині посади.
    Роздільника немає — вся строка це посада, компанія лишається порожньою.
    Так буває (`Product Designer`, `VFX Artist – Freelance`), і це не помилка.
    """
    title = clean(html.unescape(html.unescape(raw_title)))
    matches = list(_COMPANY.finditer(title))
    if not matches:
        return title, ""
    last = matches[-1]
    return title[: last.start()].strip(), title[last.end():].strip()


def _from_class_list(class_list: list[str], prefix: str) -> list[str]:
    """`class_list` — єдине місце, де лежать категорія й локація."""
    return [
        item[len(prefix):].replace("-", " ").strip()
        for item in class_list
        if isinstance(item, str) and item.startswith(prefix)
    ]


def _posted_at(record: dict) -> datetime | None:
    # `date` локальний і без зони, `date_gmt` — UTC. Беремо другий.
    raw = record.get("date_gmt") or record.get("date") or ""
    if not raw:
        return None
    try:
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


class HappyMondaySource:
    name = "happymonday"
    kind = "board"  # джоб-борд: публікуються всі компанії
    # Компанія з заголовка, локація з class_list. Зарплати в WP REST немає
    # взагалі — тому її тут НЕ оголошуємо, інакше знаменник рахував би
    # 19 вакансій, які ніколи не могли її мати.
    provides = frozenset({"company", "location"})

    # Забір інкрементальний: щоразу віддається лише те, що з'явилось після
    # минулої мітки. Тому відсутність запису тут НЕ означає, що вакансію
    # зняли — вона просто не нова. Сигнал «зникла з фіда» до цього джерела
    # не застосовується.
    scans_full_feed = False

    # Мітка останнього успішного забору — ядро підставляє її перед `fetch()`
    # і забирає `next_cursor` після (див. `Incremental` у base.py).
    cursor: str = ""
    next_cursor: str = ""

    def __init__(
        self,
        feeds: list[dict],
        timeout: float = 30.0,
        window_days: int = 14,
    ) -> None:
        specs = validate(feeds, SPEC)
        self.categories = {
            c.strip().lower()
            for spec in specs
            for c in spec.get("categories", "").split(",")
            if c.strip()
        }
        self.global_categories = {
            c.strip().lower()
            for spec in specs
            for c in spec.get("global_categories", "").split(",")
            if c.strip()
        }
        self.timeout = timeout
        self.window_days = window_days
        self.warnings: list[str] = []
        # Фід тут один, але рядок про нього все одно мусить бути:
        # джерело без розгортання читалось би як «фідів немає».
        self.feed_health: dict[str, dict] = {}
        # Забрали не все (вичерпалась стеля сторінок) — мітку рухати НЕ можна.
        self._truncated = False
        self.fetch_complete = True

    def _resume(self) -> tuple[datetime, int]:
        """Звідки тягнути: (від якої дати, з якої сторінки).

        Мітка буває двох видів, і різниця між ними принципова:

        `<час>`             прогін завершився повністю. Точного місця зупинки
                            немає, тому відступаємо на добу: якщо прогін
                            насправді впав, перекриття закриє дірку.
        `<час>|page:<n>`    прогін уперся в стелю сторінок. Продовжуємо ТУ САМУ
                            вибірку з наступної сторінки.

        Чому продовження саме за номером сторінки, а не за датою останнього
        прочитаного запису: записів з однаковою міткою часу буває більше, ніж
        уміщується на сторінці (сайт публікує пачками). Тоді дата «останнього»
        дорівнює даті, з якої ми почали, мітка не рухається — і забір топчеться
        на місці, нескінченно перечитуючи ту саму сторінку. Номер сторінки
        рухається завжди. Це безпечно саме тому, що забір іде від старіших до
        новіших: нові записи дописуються в кінець, а вже прочитані сторінки
        не зсуваються.
        """
        floor = datetime.now(timezone.utc) - timedelta(days=self.window_days)
        if not self.cursor:
            return floor, 1

        raw, _, tail = self.cursor.partition("|")
        try:
            mark = datetime.fromisoformat(raw)
        except ValueError:
            return floor, 1
        if mark.tzinfo is None:
            mark = mark.replace(tzinfo=timezone.utc)

        if tail.startswith("page:"):
            try:
                return mark, int(tail[5:]) + 1
            except ValueError:
                return mark, 1
        return max(floor, mark - OVERLAP), 1

    def fetch(self) -> Iterable[Vacancy]:
        since, first_page = self._resume()
        started = datetime.now(timezone.utc)
        seen: set[str] = set()
        self._last_page = first_page - 1

        with httpx.Client(
            timeout=self.timeout,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        ) as client:
            taken = 0
            for record in self._records(client, since, first_page):
                if not self._wanted(record):
                    continue
                vacancy = self._to_vacancy(record)
                if vacancy and vacancy.source_uid not in seen:
                    seen.add(vacancy.source_uid)
                    taken += 1
                    yield vacancy
        # `quiet`, а не `empty`: забір інкрементальний, і нуль тут означає
        # «нічого нового з минулого разу», а не «фід порожній».
        self.feed_health[self._label()] = {
            "state": "ok" if taken else "quiet", "count": taken, "error": "",
        }

        # Куди ставити мітку. Три випадки, і кожен мусить лишити стрічку
        # прочитаною без дірок:
        #
        # • виняток посеред пагінації → сюди виконання просто не дійде
        #   (`fetch` — генератор), мітка лишиться старою, діапазон перечитається;
        # • повний прохід → мітка на час старту, все прочитано;
        # • вичерпана стеля сторінок → це ШТАТНЕ завершення, виняток його не
        #   ловить. Запам'ятовуємо ту саму дату і номер сторінки, на якій
        #   спинились: наступний прогін продовжить рівно з наступної. Без цього
        #   хвіст або не забрався б ніколи (мітка застрягла), або був би
        #   перескочений (мітка поїхала вперед повз непрочитане).
        if not self._truncated:
            self.next_cursor = started.isoformat(timespec="seconds")
        else:
            self.next_cursor = f"{since.isoformat(timespec='seconds')}|page:{self._last_page}"

    def _records(self, client: httpx.Client, since: datetime, first_page: int = 1) -> Iterable[dict]:
        for page in range(first_page, first_page + MAX_PAGES):
            self._last_page = page
            response = client.get(
                API_URL,
                params={
                    "per_page": PER_PAGE,
                    "page": page,
                    "after": since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
                    "orderby": "date",
                    # Від СТАРІШИХ до новіших — навмисно. При вичерпанні стелі
                    # сторінок відрізаним лишається найновіший хвіст, і мітка
                    # може стати рівно на місце розрізу. З `desc` відрізало б
                    # найстаріше, а мітка все одно поїхала б уперед — і той
                    # хвіст не повернувся б ніколи.
                    "order": "asc",
                },
            )
            if response.status_code == 400 and page > 1:
                return  # WP так каже «сторінок більше немає» — дочитали
            response.raise_for_status()

            batch = response.json()
            if not isinstance(batch, list) or not batch:
                return
            yield from batch
            if len(batch) < PER_PAGE:
                return

        self._truncated = True
        self.fetch_complete = False
        self.warnings.append(
            f"вичерпано стелю {MAX_PAGES} сторінок по {PER_PAGE}, найстаріші записи "
            f"з цього діапазону не забрані. Мітку НЕ зсуваю — наступний прогін "
            f"перечитає той самий діапазон. Щоб це не повторювалось, зменш "
            f"window_days у конфізі: у вікно {MAX_PAGES * PER_PAGE} записів не влазить."
        )

    def _wanted(self, record: dict) -> bool:
        """Клієнтський фільтр категорії. На сервері він мовчки ігнорується."""
        class_list = record.get("class_list") or []
        categories = {c.lower() for c in _from_class_list(class_list, "job_category-")}
        if self.categories and not (categories & self.categories):
            return False
        if self.global_categories:
            globals_ = {c.lower() for c in _from_class_list(class_list, "job_global_category-")}
            if not (globals_ & self.global_categories):
                return False
        return True

    def _label(self) -> str:
        """Мітка запиту, що породив запис. Тут запит один — набір категорій."""
        return ",".join(sorted(self.categories)) or "all"

    def _to_vacancy(self, record: dict) -> Vacancy | None:
        uid = str(record.get("id") or "").strip()
        link = str(record.get("link") or "").strip()
        if not uid or not link:
            return None

        class_list = record.get("class_list") or []
        title, company = split_title((record.get("title") or {}).get("rendered", ""))
        # Місто попереду країни — так само, як на картках інших джерел.
        location = ", ".join(
            part.title()
            for part in _from_class_list(class_list, "city-") + _from_class_list(class_list, "country-")
        )
        content = (record.get("content") or {}).get("rendered", "")

        return Vacancy(
            source=self.name,
            title=title,
            company=company,
            location=location,
            url=link,
            tags=tuple(_from_class_list(class_list, "job_category-")),
            feed=self._label(),
            posted_at=_posted_at(record),
            source_uid=uid,
            description=clean(html.unescape(_TAG.sub(" ", content))),
        )
