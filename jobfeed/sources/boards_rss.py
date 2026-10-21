"""Дошки з простим RSS — один адаптер на будь-яку їх кількість.

Новий такий майданчик = три рядки в конфізі, а не новий модуль. Саме те,
чого свого часу не зробили для `dou`/`dou_companies`.

    [[sources.boards_rss.feeds]]
    name  = "weworkremotely"
    url   = "https://weworkremotely.com/categories/remote-design-jobs.rss"
    style = "company_colon"

🔴 Заміри 09.09.2026, і два з них розходяться із завданням
----------------------------------------------------------
**1. `robotparser` бреше на цих сайтах — і бреше в бік «заборонено».**

    rp.set_url(host + "/robots.txt"); rp.read()
    rp.can_fetch(наш_UA, фід)  →  weworkremotely False, dribbble False

Виглядає як пряма заборона. Насправді `read()` ходить по `urllib`, а
`urllib` під своїм UA дістає з обох сайтів **HTTP 403** — і `RobotFileParser`
на 403 мовчки ставить `disallow_all = True`. Тобто «не можна» тут означає
«не зміг прочитати правила», і ці два стани парсер не розрізняє.

Якщо забрати `robots.txt` своїм UA і віддати текст у `parse()`, обидва сайти
кажуть **дозволено**: у weworkremotely `Allow: /` плюс список кабінетних
шляхів, у dribbble наш фід не підпадає під жоден `Disallow`.

⚠️ Правило для всього проєкту: **ніколи не судити про дозвіл за
`RobotFileParser.read()`.** Забирай `robots.txt` тим самим клієнтом і тим
самим UA, яким ходитимеш по даних, і лише тоді розбирай. Інакше сайт, який
не любить `urllib`, виглядатиме як такий, що заборонив нам поіменно.

**2. `awwwards` має `Disallow: /feed`, а наш шлях — `/jobs/feed/`.**
За буквою це різні шляхи (правило порівнюється з початку), тому формально
дозволено. Але намір рядка може бути «фіди не чіпайте», і в нас є правило
тлумачити двозначність на користь власника сайту. Тому це рішення винесене
в конфіг окремим рядком із поясненням — щоб його ухвалила людина, а не
замовчав адаптер.

Що дає кожен фід — заміряно, а не припущено
-------------------------------------------
```
                записів  компанія        локація              дата закриття
weworkremotely     16    у заголовку     region (придатність)  expires_at (16/16)
dribbble           56    поле author     у заголовку «… in X»  —
awwwards           15    НЕМАЄ           НЕМАЄ                 —
```

* **weworkremotely**: `Компанія: Посада`, ділиться по ПЕРШІЙ двокрапці —
  посада сама буває з двокрапкою (`Course Director: UX, UI`).
  `region` у всіх 16 записів = `Anywhere in the World` — це поле
  ПРИДАТНОСТІ, воно ж уміє казати `USA Only` чи `Europe Only`.
  ⚠️ Сусідні `country`/`state` — адреса роботодавця, а НЕ обмеження:
  у IxDF стоїть `state: Texas`, а опис кличе людей із часових поясів від
  UTC-6 до UTC+8. Я спершу переплутала їх місцями; докладно — у
  `region_of()`. ⚠️ Значення `USA Only` у сьогоднішній видачі немає взагалі,
  тобто перевірити його на живих даних поки нічим.
* **dribbble**: компанія є ОКРЕМИМ полем `author` — розбирати її з
  заголовка не треба взагалі. Шаблон `… is hiring for a position of … in …`
  не спрацював на 1 записі з 56 (`…Graphic Designer anywhere`), не на трьох.
* **awwwards**: ні компанії, ні локації немає ніде в структурі. У описі
  перший `<strong>` іноді схожий на назву студії, але вже в першому записі
  там `LE STUDIO`, а в тексті нижче — `FLOT NOIR recrute`. Тобто здогадка
  дала б неправильну компанію, а не порожню. Лишаємо порожнім.

  ⚠️ У порожньої компанії є ціна нижче за течією, і вона не очевидна на
  цьому місці. `match_key` складається як `f"{company} {title}"`, тому без
  компанії ключ вироджується в **саму лише посаду**: `'digital designer'`.
  Заміряно 09.09.2026 — у видачі awwwards уже є дві РІЗНІ вакансії з таким
  ключем (`/jobs/digital-designer-london-2` і `/jobs/digital-designer-13`).
  Сьогодні це нешкідливо: ключ використовується лише для звʼязку МІЖ
  джерелами, а обидві картки з одного. Але якщо колись зʼявиться
  успадкування позначок або злиття з базою — там мусить бути умова
  «обидві картки мають непорожню компанію», інакше одна позначка накриє
  чужу вакансію.

  Рішення лишити поле порожнім від цього не змінюється: вигадана компанія
  зіпсувала б і картку, і ключ, а порожня псує лише ключ.

Через це `provides` оголошує `company` і `location`, хоча awwwards не дає
жодного: зведення заповненості показуватиме на них стабільну нестачу. Це
не поломка, а видима ціна того, що три різні дошки живуть під одним іменем
джерела.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Iterable

import feedparser
import httpx

from ..models import Vacancy, clean

USER_AGENT = "Mozilla/5.0 (compatible; find_new-jobfeed/0.1; personal job alerts)"

# `<Компанія> is hiring for a position of <Посада> in <Місце>`
_HIRING = re.compile(
    r"^(?P<company>.+?)\s+is hiring for a position of\s+(?P<title>.+?)"
    r"(?:\s+in\s+(?P<place>.+?))?\s*$",
    re.IGNORECASE,
)

_TAG = re.compile(r"<[^>]+>")

# Коли джерело мовчить про регіон, ми кажемо це вголос. Порожнє поле
# читається як «скрізь можна», а це вже здогадка.
NO_REGION = "регіон не вказано"


class FeedConfigError(ValueError):
    """Фід описано неправильно — помилка конфігурації, не мережі."""


def check_feed(item: dict) -> dict:
    """Перевіряє опис фіда з конфіга. Помилка тут гучна й до запиту."""
    name = clean(str(item.get("name") or ""))
    url = clean(str(item.get("url") or ""))
    style = clean(str(item.get("style") or "plain")).lower()
    if not name or not url:
        raise FeedConfigError(
            f"фід {item!r}: потрібні обидва поля — `name` і `url`")
    if not url.startswith("https://"):
        raise FeedConfigError(f"фід {name}: адреса має починатись з https://")
    if style not in ("plain", "company_colon", "is_hiring"):
        raise FeedConfigError(
            f"фід {name}: невідомий style={style!r}. "
            "Дозволені: plain, company_colon, is_hiring")
    return {"name": name, "url": url, "style": style}


def split_title(raw: str, style: str, author: str = "") -> tuple[str, str, str]:
    """Заголовок → (посада, компанія, місце). Порожнє там, де не впевнені.

    🔴 Чистити заголовок обовʼязково: без цього у стрічці стоятиме
    «X is hiring for a position of Y in Z» замість посади, і власник читатиме
    рекламний рядок дошки замість того, за чим прийшов.
    """
    title = clean(raw)
    if style == "company_colon":
        # Ділимо по ПЕРШІЙ двокрапці: посада сама буває з двокрапкою
        # (`IxDF: Course Director: UX, UI`), і `rsplit` зіпсував би її.
        company, _, position = title.partition(":")
        if position.strip():
            return clean(position), clean(company), ""
        return title, "", ""
    if style == "is_hiring":
        match = _HIRING.match(title)
        if match:
            # Компанію все одно беремо з поля `author`, коли воно є: поле
            # надійніше за розбір рядка. Заголовок лишається запасним шляхом.
            return (clean(match.group("title")),
                    clean(author) or clean(match.group("company")),
                    clean(match.group("place") or ""))
        # Шаблон не спрацював (1 запис із 56). Гілка потрібна й справді
        # використовується — тому вона не «на всяк випадок».
        return title, clean(author), ""
    return title, clean(author), ""


def region_of(entry, place: str) -> str:
    """Локація й регіон — головне поле цих дощок, а не дрібниця.

    Для власника з України `USA Only` означає «подаватись не можна», і
    показати таку картку без позначки — змарнувати його час так само, як
    показати закриту вакансію. Тому коли джерело про регіон мовчить, ми
    пишемо це словами, а не лишаємо порожнечу.

    🔴 Беремо ТІЛЬКИ `region`. `country` і `state` — це адреса роботодавця,
    а не право подаватись, і я це спершу переплутала.

    Хід міркування був такий: у weworkremotely `region` дорівнює «Anywhere in
    the World» у всіх 16 записів, а поруч лежать `country` і `state` із
    конкретикою — виглядало як «шапка й справжнє обмеження під нею». Я
    зробила так, щоб конкретика витісняла шапку.

    Замір це спростував. У трьох вакансіях IxDF стоїть `state: Texas`, а в
    описі написано: «You're located in a time zone between UTC-6 (Austin,
    Chicago) and UTC+8 (Singapore, Perth)» — тобто робота відкрита для пів
    світу, а Техас це просто де сидить роботодавець. З моєю «правкою» власник
    прочитав би «Texas» і пропустив вакансію, на яку має повне право.

    Помилка в цей бік дорожча за протилежну: зайва картка коштує секунди, а
    прихована можливість не коштує нічого видимого — про неї просто не
    дізнаються.

    `region` — це і є поле придатності самого weworkremotely («Anywhere in
    the World», «USA Only», «Europe Only»), тому воно й показується. Коли
    його немає, кажемо про це словами: порожнеча читалась би як «скрізь можна».
    """
    place = clean(place)
    region = clean(getattr(entry, "region", "") or "")
    parts = list(dict.fromkeys(x for x in (place, region) if x))
    return ", ".join(parts) if parts else NO_REGION


def _stamp(entry, field: str) -> datetime | None:
    parsed = getattr(entry, field, None)
    if parsed:
        return datetime(*parsed[:6], tzinfo=timezone.utc)
    return None


class BoardsRssSource:
    """Кілька RSS-дощок під одним іменем джерела."""

    name = "boards_rss"
    kind = "board"
    provides = frozenset({"company", "location"})

    def __init__(self, feeds: list[dict], timeout: float = 40.0) -> None:
        self.feeds = [check_feed(f) for f in feeds]
        self.timeout = timeout
        self.warnings: list[str] = []
        self.fetch_complete = True
        self.feed_sizes: dict[str, int] = {}
        self.feed_health: dict[str, dict] = {}

    def fetch(self) -> Iterable[Vacancy]:
        seen: set[str] = set()
        with httpx.Client(
            timeout=self.timeout,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        ) as client:
            for feed in self.feeds:
                yield from self._fetch_one(client, feed, seen)

    def _fetch_one(self, client, feed: dict, seen: set[str]) -> Iterable[Vacancy]:
        name = feed["name"]
        try:
            response = client.get(feed["url"])
        except Exception as exc:
            self._fail(name, f"{type(exc).__name__}: {exc}")
            return
        if response.status_code != 200:
            self._fail(name, f"HTTP {response.status_code}")
            return

        parsed = feedparser.parse(response.content)
        entries = parsed.entries
        # 🔴 200 OK і жодного запису — поломка фіда, а не порожній день.
        # Дошка з десятками вакансій не порожніє за годину. Правило вп'яте
        # те саме: відсутність не є сигналом, поки її не відрізнено від збою.
        if not entries:
            self._fail(name, "фід відповів 200 OK і не дав жодного запису — "
                             "це зміна формату або збій, а не порожній день")
            return

        self.feed_sizes[name] = len(entries)
        self.feed_health[name] = {
            "state": "ok", "count": len(entries), "error": "", "title": name,
        }

        now = datetime.now(timezone.utc)
        for entry in entries:
            vacancy = self._to_vacancy(entry, feed, now)
            if vacancy and vacancy.source_uid not in seen:
                seen.add(vacancy.source_uid)
                yield vacancy

    def _to_vacancy(self, entry, feed: dict, now: datetime) -> Vacancy | None:
        link = clean(getattr(entry, "link", "") or "")
        if not link:
            return None
        # `expires_at` є лише у weworkremotely, зате в усіх 16 записів. Це
        # пряма дата від роботодавця — сильніша за наш здогадний сигнал
        # зникнення, тому протермінованих не показуємо взагалі.
        expires = _stamp(entry, "expires_at_parsed")
        if expires and expires <= now:
            return None

        position, company, place = split_title(
            getattr(entry, "title", ""), feed["style"],
            clean(getattr(entry, "author", "") or ""))
        return Vacancy(
            source=self.name,
            title=position,
            company=company,
            location=region_of(entry, place),
            url=link,
            tags=(feed["name"],),
            # Мітка фіда обовʼязкова: джерело віддає повну вибірку, отже
            # бере участь у сигналі зникнення, а той рахує вікно пофідно.
            feed=feed["name"],
            posted_at=_stamp(entry, "published_parsed"),
            source_uid=clean(getattr(entry, "id", "") or link),
            description=clean(_TAG.sub(" ", getattr(entry, "summary", "") or "")),
        )

    def _fail(self, name: str, reason: str) -> None:
        self.feed_health[name] = {"state": "failed", "count": 0,
                                  "error": reason, "title": name}
        self.feed_sizes[name] = 0
        self.warnings.append(f"{name}: {reason}")
        # Неповний забір гасить сигнал зникнення: інакше збій одного фіда
        # зарахувався б за зняття всіх його вакансій.
        self.fetch_complete = False
