"""work.ua — цільові списки вакансій, розбір HTML.

Чому взагалі можна
------------------
`https://www.work.ua/robots.txt` містить дві групи, і **обидві дозволяють
сторінки вакансій**. Заміряно 14.08.2026 через `urllib.robotparser`:

    UA                  /jobs-ux+ui+designer/   crawl-delay
    find_new-jobfeed    True                    None      (потрапляє під `*`)
    *                   True                    None
    ClaudeBot           True                    2

Правила Disallow у групах `*` і `ClaudeBot` **збігаються повністю** —
різниця лише в тому, що для `ClaudeBot` додано `Crawl-delay: 2` і `Sitemap`.

🔴 Тому ми ходимо ПІД СВОЇМ іменем, а не під `ClaudeBot`.

Спокуса назватись `ClaudeBot` була в завданні прямо: дозвіл виданий цьому
імені поіменно. Але `ClaudeBot` — це краулер Anthropic, а не ми. Назватись
чужим іменем, щоб успадкувати видані йому права, — це підміна, а не
ввічливість; і з нею наш трафік лічився б сайту як трафік Anthropic.
Замір знімає питання остаточно: під власним іменем нам відкрито РІВНО ті
самі шляхи, тобто чужа личина не дає нічого, крім неправди.

Затримку `2` секунди ми при цьому дотримуємо, хоча для `*` її не задано:
з двох правил беремо суворіше. Це дешево і це єдине, що сайт узагалі
попросив.

Пастки, знайдені замірами
-------------------------
1. **Порожній результат тут НЕ порожній.** Запит-нісенітниця
   `/jobs-zzqqxx+neisnuyucha+profesiya/` віддає HTTP 200 і **10 карток**:
   доценти, завідувачі кафедр, паркувальник. Тобто перевірка «200 OK і нуль
   карток = поломка» не спрацювала б ніколи, а одруківка в конфізі тихо
   налила б у стрічку дизайнера вакансії парковника.
   Ознака такої сторінки — заголовок «Вибачте, але ні.» і відсутність H1 з
   назвою запиту. Обидві перевіряємо; картки з такої сторінки викидаємо.
2. **JSON-LD у них немає взагалі** — ні в списку, ні на сторінці вакансії
   (перевірено обидві). Отже спільний `jsonld_jobposting` не застосовний, і
   це саморобний розбір розмітки, який зламається мовчки при зміні верстки.
   Тому «0 карток при 200 OK і без ознаки порожнього запиту» = помилка
   джерела, а не «сьогодні нічого».
3. **Дата є машинна.** У картці стоїть `<time datetime="2026-08-12 14:05:02">`
   — беремо її, а не «12 серпня» словами й не час забору. Дата без зони,
   тому проставляємо київську (див. `_KYIV`).
4. **Пагінація існує** («Стор. 1 з 2»), і без неї сигнал зникнення бреше:
   вакансія, яку виштовхнули на другу сторінку, виглядала б знятою.
   Тому читаємо до кінця, а стеля сторінок вмикає `fetch_complete = False`.
"""

from __future__ import annotations

import html
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Iterable

import httpx

from ..models import Vacancy, clean

BASE = "https://www.work.ua"
LIST_URL = BASE + "/jobs-{query}/"

# Своє ім'я. Не `ClaudeBot` — див. шапку модуля.
USER_AGENT = "Mozilla/5.0 (compatible; find_new-jobfeed/0.1; personal job alerts)"

# Умова, під якою нас впустили. Секунди, послідовно, між УСІМА запитами.
CRAWL_DELAY = 2.0

# Картка починається з якоря з id вакансії — він же `source_uid`.
_CARD = re.compile(r'<a name="(\d+)"></a>')
_TITLE = re.compile(
    r'<h2 class="my-0">\s*<a[^>]*href="(?P<href>/jobs/\d+/[^"]*)"[^>]*>(?P<title>.*?)</a>',
    re.DOTALL,
)
# 🔴 І компанія, і зарплата лежать у `<span class="strong-600">`, тому «перший
# strong-600» — НЕ компанія. На картках із зарплатою першим іде саме вона, і
# перша версія цього розбору підставляла власникові «27 000 – 127 000 грн» як
# назву роботодавця. Тому кожне поле прив'язане до СВОЄЇ іконки-якоря:
# `glyphicon-hryvnia-fill` — гроші, `glyphicon-company` — компанія.
_MONEY_BLOCK = re.compile(r"glyphicon-hryvnia-fill")
_COMPANY_BLOCK = re.compile(r"glyphicon-company")
_STRONG = re.compile(r'<span class="strong-600">(.*?)</span>', re.DOTALL)
_TIME = re.compile(r'<time datetime="(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})"')
_SNIPPET = re.compile(r'<p class="ellipsis[^"]*"[^>]*>(.*?)</p>', re.DOTALL)
# Місто — безкласовий span у блоці компанії, після значків «перевірена» тощо.
_CITY = re.compile(r'<span class="">(.*?)</span>', re.DOTALL)
_H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.DOTALL)
_NEXT_PAGE = re.compile(r'href="/jobs-[^"]*\?page=(\d+)"')
_TAG = re.compile(r"<[^>]+>")

# Сторінка «за таким запитом немає нічого» — але з чужими картками на ній.
#
# ⚠️ Маркер — САЙТОВИЙ клас, а не фраза. Перша спроба брала текст
# «Вибачте, але ні.», зчитаний із порожньої сторінки, — і тест одразу показав,
# що ця фраза є й на нормальній: це постійний банер про хвилину мовчання, до
# результатів пошуку стосунку не має. Рівно та сама форма помилки, яку ми
# ловимо в цьому проєкті постійно: побачив на сторінці X — вирішив, що це
# ознака X. Клас `ga-search-no-results` сайт ставить саме на цей стан
# (заміряно: 0 збігів на сторінці з результатами, 1 на порожній).
_NO_MATCH = re.compile(r"ga-search-no-results", re.IGNORECASE)

_QUERY_ALLOWED = set("abcdefghijklmnopqrstuvwxyz0123456789+-")


class QueryError(ValueError):
    """Неприйнятний запит — помилка конфігурації, а не збій мережі."""


def check_query(query: str) -> str:
    """Запит має бути шматком шляху, а не URL і не довільним текстом.

    Те саме правило, що для слагів DOU: значення приходить із конфіга (а
    колись і з форми), і рядок `../../` чи цілий URL мусить упасти тут, а не
    перетворитись на запит кудись убік.
    """
    value = (query or "").strip().lower().lstrip("/")
    if value.startswith("jobs-"):
        value = value[len("jobs-"):]
    value = value.strip("/")
    if not value:
        raise QueryError("порожній запит work.ua")
    bad = sorted(set(value) - _QUERY_ALLOWED)
    if bad:
        raise QueryError(
            f"запит {query!r} містить неприпустимі символи: {''.join(bad)}. "
            "Потрібен шматок шляху зі сторінки пошуку work.ua "
            "(work.ua/jobs-<запит>/), напр. 'ux+ui+designer'."
        )
    return value


def _kyiv_offset(naive: datetime) -> timezone:
    """Київський зсув для наївної дати work.ua.

    `zoneinfo` на цій машині бази часових поясів не має (перевірено:
    `ZoneInfoNotFoundError` на 'Europe/Kyiv'), а тягнути залежність заради
    однієї дати не варто. Правило України просте й стабільне: літній час
    з останньої неділі березня по останню неділю жовтня.

    Помилка в найгіршому разі — одна година на межі переходу. Для стрічки,
    де вік показується в днях і годинах, це нічого не змінює; альтернатива
    ж — узагалі не мати дати, бо без зони її довелось би відкинути.
    """
    def last_sunday(year: int, month: int) -> datetime:
        day = 31 if month == 3 else 31   # обидва місяці 31-денні
        d = datetime(year, month, day)
        return d - timedelta(days=(d.weekday() + 1) % 7)

    start = last_sunday(naive.year, 3).replace(hour=3)
    end = last_sunday(naive.year, 10).replace(hour=4)
    return timezone(timedelta(hours=3 if start <= naive < end else 2))


def parse_posted_at(raw: str) -> datetime | None:
    """`2026-08-12 14:05:02` → tz-aware datetime у UTC."""
    raw = (raw or "").strip().replace("T", " ")
    try:
        naive = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return naive.replace(tzinfo=_kyiv_offset(naive)).astimezone(timezone.utc)


def _text(raw: str) -> str:
    return clean(html.unescape(_TAG.sub(" ", raw or "")))


def _block_after(body: str, anchor: re.Pattern) -> str:
    """Шматок картки від іконки-якоря до кінця її рядка (`</div></div>`).

    Межа саме структурна. Перша версія різала фіксовані 900 символів — і
    мовчки губила місто на картці, де блок компанії має 1042 символи через
    значки «перевірена» й «VIP». Довільна константа тут і є пасткою: вона
    відрізає не там, де закінчився блок, а там, де скінчилось моє припущення
    про його довжину, і робить це без жодної помилки.

    Порожній рядок, якщо якоря немає — поля просто не буде, і це чесніше за
    здогадку з сусіднього блоку.
    """
    found = anchor.search(body)
    if not found:
        return ""
    tail = body[found.end():]
    end = tail.find("</div></div>")
    return tail[:end] if end != -1 else tail


def _city_of(company_block: str, company: str) -> str:
    """Місто з блоку компанії — ОСТАННІЙ безкласовий span, і не сама компанія.

    Два капкани, обидва спіймані на живій сторінці:

    * на частині карток назва компанії загорнута в такий самий `<span class="">`,
      тому «перший збіг» повертав `Improvs` як місто — назву фірми, показану
      власникові як географію;
    * на решті карток перед містом стоїть список значків, і місто йде після
      нього — тобто останнім.

    Звідси правило: беремо останній збіг і відкидаємо його, якщо він
    дослівно дорівнює назві компанії. Порожнє чесніше за назву фірми в полі
    міста: за локацією власник фільтрує.
    """
    matches = [_text(m.group(1)).strip(" ,") for m in _CITY.finditer(company_block)]
    matches = [m for m in matches if m and m != company]
    return matches[-1] if matches else ""


class NoMatchPage(Exception):
    """Сторінка сказала «за таким запитом нічого», але картки на ній є."""


def parse_list(page: str) -> list[dict]:
    """HTML сторінки списку → сирі поля карток.

    Окремо від мережі навмисно: так розбір тестується збереженою сторінкою,
    без жодного запиту.

    Кидає `NoMatchPage`, якщо сторінка є відповіддю «нічого не знайдено».
    Картки на ній справжні, але до запиту стосунку не мають — і мовчазно
    зарахувати їх було б гірше за будь-яку помилку.
    """
    if _NO_MATCH.search(page) or not _H1.search(page):
        raise NoMatchPage()

    out: list[dict] = []
    parts = _CARD.split(page)
    for uid, body in zip(parts[1::2], parts[2::2]):
        title = _TITLE.search(body)
        if not title:
            continue
        stamp = _TIME.search(body)
        snippet = _SNIPPET.search(body)
        company_block = _block_after(body, _COMPANY_BLOCK)
        money_block = _block_after(body, _MONEY_BLOCK)
        company = _STRONG.search(company_block)
        salary = _STRONG.search(money_block)
        company_name = _text(company.group(1)) if company else ""
        out.append({
            "uid": uid,
            "url": BASE + title.group("href"),
            "title": _text(title.group("title")),
            "company": company_name,
            "location": _city_of(company_block, company_name),
            "salary": _text(salary.group(1)) if salary else "",
            "posted_at": parse_posted_at(stamp.group(1)) if stamp else None,
            "description": _text(snippet.group(1)) if snippet else "",
        })
    return out


def has_next_page(page: str, current: int) -> bool:
    """Чи є сторінка після поточної."""
    return any(int(n) > current for n in _NEXT_PAGE.findall(page))


class WorkUaSource:
    """Читає перелічені в конфізі цільові списки work.ua."""

    name = "work_ua"
    kind = "board"
    provides = frozenset({"company", "location", "salary"})

    def __init__(self, queries: list[str], timeout: float = 30.0,
                 max_pages: int = 5, delay: float = CRAWL_DELAY) -> None:
        self.queries = [check_query(q) for q in queries]
        self.timeout = timeout
        self.max_pages = int(max_pages)
        self.delay = float(delay)
        self.warnings: list[str] = []
        self.fetch_complete = True
        self.feed_sizes: dict[str, int] = {}
        self.feed_health: dict[str, dict] = {}
        self._last_request = 0.0

    def _wait(self) -> None:
        """Затримка між запитами — послідовно, буквально 2 секунди."""
        gap = time.monotonic() - self._last_request
        if self._last_request and gap < self.delay:
            time.sleep(self.delay - gap)
        self._last_request = time.monotonic()

    def fetch(self) -> Iterable[Vacancy]:
        seen: set[str] = set()
        with httpx.Client(
            timeout=self.timeout,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        ) as client:
            for query in self.queries:
                yield from self._fetch_query(client, query, seen)

    def _fetch_query(self, client, query: str, seen: set[str]) -> Iterable[Vacancy]:
        # Один мертвий запит не валить решту: він стає рядком із причиною.
        rows: list[dict] = []
        page_no = 1
        while page_no <= self.max_pages:
            url = LIST_URL.format(query=query)
            if page_no > 1:
                url += f"?page={page_no}"
            self._wait()
            try:
                response = client.get(url)
            except Exception as exc:
                self._fail(query, f"{type(exc).__name__}: {exc}")
                return
            if response.status_code != 200:
                # 403 тут майже напевно не «заборонено правилами», а перевірка
                # Cloudflare («увімкніть JavaScript і cookies»). Різниця важлива
                # для того, хто читатиме рядок стану: правила нам якраз
                # дозволяють, а от захист вимагає довести, що ми браузер.
                # Проходити його ми не будемо — це і є обхід бот-захисту.
                reason = f"HTTP {response.status_code}"
                if response.status_code == 403 and "cloudflare" in (
                        response.headers.get("server", "").lower()):
                    reason += (" — перевірка Cloudflare (вимагає JavaScript і "
                               "cookies). Це захист сайту, а не правило "
                               "robots.txt: за robots нам дозволено. Обходити "
                               "перевірку ми не будемо.")
                self._fail(query, reason)
                return

            try:
                found = parse_list(response.text)
            except NoMatchPage:
                # Запит нічого не знайшов. Це НЕ помилка й НЕ привід брати
                # картки, які сайт підсунув замість відповіді.
                self.feed_sizes[query] = 0
                self.feed_health[query] = {
                    "state": "empty", "count": 0, "error": "",
                    "title": f"work.ua: за запитом «{query}» зараз нічого",
                }
                return

            if not found:
                # 200 OK, сторінка виглядає як список, а карток нуль. Розмітку
                # змінили — і мовчазний нуль тут прочитався б як «сьогодні
                # порожньо». Ми вже двічі ловили відсутність, прийняту за факт.
                self._fail(query, "сторінка відповіла 200, але жодної картки "
                                  "не розібрано — найімовірніше змінилась розмітка")
                return

            rows.extend(found)
            if not has_next_page(response.text, page_no):
                break
            page_no += 1
        else:
            # Стеля сторінок вичерпана, а наступна існує: вибірка неповна, і
            # сигнал зникнення для цього прогону мусить вимкнутись.
            self.fetch_complete = False
            self.warnings.append(
                f"{query}: стеля у {self.max_pages} сторінок вичерпана — "
                "вибірка неповна, сигнал зникнення вимкнено")

        self.feed_sizes[query] = len(rows)
        self.feed_health[query] = {
            "state": "ok", "count": len(rows), "error": "",
            "title": f"work.ua: {query.replace('+', ' ')}",
        }
        for row in rows:
            if row["uid"] in seen:
                continue
            seen.add(row["uid"])
            yield Vacancy(
                source=self.name,
                title=row["title"],
                company=row["company"],
                location=row["location"],
                url=row["url"],
                tags=(query,),
                feed=query,
                posted_at=row["posted_at"],
                source_uid=row["uid"],
                salary=row["salary"],
                description=row["description"],
            )

    def _fail(self, query: str, reason: str) -> None:
        self.feed_health[query] = {"state": "failed", "count": 0, "error": reason}
        self.feed_sizes[query] = 0
        self.warnings.append(f"{query}: {reason}")
        # Помилка запиту робить вибірку джерела неповною — інакше зникнення
        # вакансій із непрочитаного запиту зарахувалось би за їх зняття.
        self.fetch_complete = False
