"""Jobico — українська IT-платформа, яка САМА пропонує машинний доступ.

Чому можна — і чому це не схоже ні на що інше в цьому проєкті
--------------------------------------------------------------
`robots.txt` (заміряно 26.08.2026) ПОЧИНАЄТЬСЯ зі звернення до агентів:

    # AI agents: no need to scrape HTML pages - machine-readable access is available.
    # MCP server (tools: search_jobs, get_job): POST https://www.jobico.io/api/mcp
    # XML job feed: https://www.jobico.io/api/feeds/jobs.xml
    # Guide for AI: https://www.jobico.io/llms.txt

    User-agent: *
    Allow: /
    Allow: /api/mcp
    Allow: /api/feeds/
    Disallow: /api/

Тобто `/api/` закритий, а `/api/feeds/` відкритий явним `Allow`. Розбирати
HTML сторінок вакансій нам прямо не рекомендують — не як заборону, а як
«є кращий шлях». Ми йдемо кращим.

⚠️ УМОВА ДЖЕРЕЛА, не наше побажання. `llms.txt`:

    Attribution: when showing or republishing a job posting, always link to
    its original page on Jobico (the link field in the feed).

Тому `url` береться з поля `link` ЯК Є, разом із `utm_source=job-feed`.
Зрізати мітку означало б порушити єдину умову, під якою нас пустили.

Заміряно на живому фіді (190 вакансій, 26.08.2026)
--------------------------------------------------
1. **`city` і `country` — окремі поля, і вони НЕ обов'язкові**: 41 і 58 зі
   190. `locationtype` при цьому заповнений завжди. Тому локація
   складається, а не береться з одного поля: інакше 149 вакансій лишились
   би без жодної згадки місця, хоча формат роботи відомий для всіх.

2. **`salary` — рядок, не число**: `2000-2500 USD`, `30000-35000 UAH`,
   але й `60-120 USD` (майже напевно погодинна). Віддаємо рядком як є.
   Розпізнати «за годину чи за місяць» із самого рядка неможливо, а
   вгадати означало б показати власнику НАШУ здогадку як дані джерела.

3. **`date` — ISO з `Z`**: `2026-08-25T15:11:02.964Z`. Без розбору дати вся
   видача стала б «щойно», і сортування за свіжістю зламалось би тихо.

   ⚠️ Глибина фіда — **96 днів** (найстаріша 2026-05-21), а не сім тижнів,
   як стояло тут спершу. Сім тижнів — це вік найстарішої ДИЗАЙНЕРСЬКОЇ
   вакансії (07.07), і воно перекочувало сюди як характеристика всього
   фіда. Та сама підміна, що й завжди: поміряли один зріз, сказали про
   сусідній. Число важливе не саме по собі — на глибині фіда тримається
   сигнал «вакансія зникла, отже ймовірно закрита».

4. **Ознаки «вакансія закрита» у фіді НЕМАЄ.** Ні `status`, ні `close_at` —
   на відміну від Recruitee. Отже зняття ловиться тільки нашим сигналом
   зникнення, і `fetch_complete` тут важить більше, ніж деінде.
"""

from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from typing import Iterable
from xml.etree import ElementTree as ET

import httpx

from ..models import Vacancy, clean

FEED_URL = "https://www.jobico.io/api/feeds/jobs.xml"

USER_AGENT = "Mozilla/5.0 (compatible; find_new-jobfeed/0.1; personal job alerts)"

_TAG = re.compile(r"<[^>]+>")


def parse_stamp(raw: str | None) -> datetime | None:
    """`2026-08-25T15:11:02.964Z` → tz-aware datetime.

    Зона названа явно (`Z`), тому нічого не виводимо з правил переходу на
    літній час, як довелось для work.ua.
    """
    text = (raw or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def plain(text: str | None) -> str:
    return clean(html.unescape(_TAG.sub(" ", text or "")))


def location_of(job: dict) -> str:
    """Місто, країна й формат — у тому ж вигляді, що в інших джерел.

    `locationtype` є завжди, місто — ні. Порядок «місце, потім формат»
    збігається з рештою джерел, щоб чипи на картці читались однаково.
    """
    parts: list[str] = []
    city = clean(job.get("city") or "")
    country = clean(job.get("country") or "")
    if city:
        parts.append(city)
    if country and country.lower() not in ("ukraine", "україна") and country != city:
        parts.append(country)
    # ⚠️ `office` тут теж потрібен, і це не косметика. Заміряно на живому
    # фіді: офісних вакансій 32, і в ДЕСЯТИ з них міста немає — тобто без
    # цієї гілки локація виходила порожньою, хоча джерело формат назвало.
    # Порожнє поле читається як «джерело не сказало», а воно сказало.
    kind = (job.get("locationtype") or "").strip().lower()
    if kind == "remote":
        parts.append("віддалено")
    elif kind == "hybrid":
        parts.append("гібрид")
    elif kind == "office":
        parts.append("офіс")
    elif kind:
        # Невідомий формат віддаємо як є: краще чуже слово, ніж порожнеча,
        # і воно ж підкаже, що в джерела з'явилось нове значення.
        parts.append(kind)
    return ", ".join(parts)


class JobicoSource:
    """Читає публічний XML-фід Jobico. Одна дошка — один фід."""

    name = "jobico"
    kind = "board"
    provides = frozenset({"company", "location", "salary"})
    # Фід віддає ВСЕ, не лише дизайн, тож відбір за фахом — на нашому боці.
    trust_category = False

    def __init__(self, timeout: float = 30.0) -> None:
        self.timeout = timeout
        self.warnings: list[str] = []
        self.fetch_complete = True
        self.feed_sizes: dict[str, int] = {}
        self.feed_health: dict[str, dict] = {}

    def fetch(self) -> Iterable[Vacancy]:
        try:
            with httpx.Client(
                timeout=self.timeout,
                headers={"User-Agent": USER_AGENT},
                follow_redirects=True,
            ) as client:
                response = client.get(FEED_URL)
        except Exception as exc:
            yield from self._fail(f"{type(exc).__name__}: {exc}")
            return
        if response.status_code != 200:
            yield from self._fail(f"HTTP {response.status_code}")
            return
        try:
            root = ET.fromstring(response.content)
        except ET.ParseError as exc:
            yield from self._fail(f"фід не є XML: {exc}")
            return

        jobs = root.findall("job")
        # 🔴 200 OK із нулем вакансій — це поломка фіда, а не порожній день:
        # 190 вакансій не зникають разом. Ми вже тричі приймали відсутність
        # за факт, тому цей випадок мусить бути гучним, а не тихим нулем.
        if not jobs:
            yield from self._fail(
                "фід віддав 200 OK і ЖОДНОЇ вакансії — це зміна формату "
                "або збій на їхньому боці, а не порожній день")
            return

        self.feed_sizes[self.name] = len(jobs)
        self.feed_health[self.name] = {
            "state": "ok", "count": len(jobs), "error": "", "title": "Jobico",
        }

        seen: set[str] = set()
        for node in jobs:
            job = {child.tag: (child.text or "") for child in node}
            uid = (job.get("id") or "").strip()
            link = (job.get("link") or "").strip()
            # Без ідентифікатора або посилання картку не зібрати: перше
            # ламає дедуплікацію, друге — умову атрибуції.
            if not uid or not link or uid in seen:
                continue
            seen.add(uid)
            yield Vacancy(
                source=self.name,
                title=clean(job.get("title") or ""),
                company=clean(job.get("company") or ""),
                location=location_of(job),
                # Як є, разом із utm — це умова джерела, див. модуль-докстрінг.
                url=link,
                tags=(),
                feed=self.name,
                posted_at=parse_stamp(job.get("date")),
                source_uid=uid,
                salary=clean(job.get("salary") or ""),
                description=plain(job.get("description")),
                employment_type=clean(job.get("employmenttype") or ""),
            )

    def _fail(self, reason: str) -> Iterable[Vacancy]:
        self.feed_sizes[self.name] = 0
        self.feed_health[self.name] = {
            "state": "failed", "count": 0, "error": reason, "title": "Jobico",
        }
        # ⚠️ Без префікса з назвою джерела. У dou_companies префікс — це слаг
        # компанії, і він додає інформацію; тут же рядок стану вже підписаний
        # джерелом, і виходило «не забралось: jobico: HTTP 503» — назва двічі.
        self.warnings.append(reason)
        # Неповний забір робить зникнення вакансій недоказовим: без цього
        # прапорця мовчазний збій зарахувався б за зняття всіх вакансій.
        self.fetch_complete = False
        return ()
