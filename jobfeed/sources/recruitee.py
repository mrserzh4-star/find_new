"""Recruitee — публічний API кар'єрних сторінок компаній.

Це КЛАС джерел, а не сайт. Один адаптер відкриває будь-яку компанію, що
сидить на Recruitee: `https://<слаг>.recruitee.com/api/offers/` віддає JSON
без ключа й авторизації, з уже розібраними полями.

Чому можна
----------
`robots.txt` тенанта (заміряно 20.08.2026 на `clario`) — 82 байти:

    User-Agent: *

    Disallow: /v/

    Sitemap: https://clario.recruitee.com/sitemap.xml

⚠️ Синтаксично цей `Disallow` НЕ діє: між `User-Agent:` і `Disallow:` стоїть
порожній рядок, а він за стандартом завершує запис — тому правило не
належить жодній групі, і `robotparser` каже `/v/ → True`. Ми все одно
вважаємо `/v/` закритим: намір власника очевидний, а двозначність ми
тлумачимо на його користь. Коштує це нічого — нам потрібен `/api/offers/`.

Заміряно, що пастка не там, де здається
---------------------------------------
1. **`created_at` — НЕ дата публікації.** У даних є обидві: `created_at`
   (створення чернетки в ATS) і `published_at` (вихід у люди). Вони
   розходяться, і сильно: у MacPaw «Head of PR» — на 12 днів, а
   `[TEST] Junior Data Protection` — на **три місяці**. Беремо
   `published_at`: вакансія не існувала для світу, поки її не опублікували.

2. **`careers_url` буває мертвим.** Тенант може вимкнути хостинг кар'єрного
   сайту, лишивши API живим. Тоді `https://<слаг>.recruitee.com/o/<...>`
   віддає 301 на `recruitee.com` — рекламну сторінку самої ATS. Заміряно на
   MacPaw: вакансія справжня й публічна (вона є на macpaw.com), але за
   посиланням із API власник потрапляє на «Customizable ATS & hiring
   software». Тому посилання ПЕРЕВІРЯЄТЬСЯ один раз на компанію за прогін, і
   компанія з мертвими посиланнями не показується взагалі: картка, яку не
   відкрити, гірша за відсутність картки.

3. **`tags` — це внутрішні мітки рекрутера, не публічні ярлики.** У MacPaw
   там `['@vlada', 'L - 3 months']`: ім'я відповідальної людини й строк
   найму. Назовні вони не йдуть.

4. **`experience_code` — грейд, а не число.** `experienced`, `master_degree`
   тощо. Наше поле `experience_months` — саме місяці, тому лишається
   порожнім: перетворити грейд на число означало б видати НАШУ
   інтерпретацію за дані джерела.
"""

from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from typing import Iterable

import httpx

from ..models import Vacancy, clean

API_URL = "https://{slug}.recruitee.com/api/offers/"

USER_AGENT = "Mozilla/5.0 (compatible; find_new-jobfeed/0.1; personal job alerts)"

_TAG = re.compile(r"<[^>]+>")
_SLUG_ALLOWED = set("abcdefghijklmnopqrstuvwxyz0123456789-_")


class SlugError(ValueError):
    """Неприйнятний слаг — помилка конфігурації, а не збій мережі."""


def check_slug(slug: str) -> str:
    """Слаг має бути піддоменом, а не URL.

    Те саме правило, що для DOU: значення приходить із конфіга, і рядок
    `../../` чи цілий URL мусить упасти тут, а не піти запитом кудись убік.
    """
    value = (slug or "").strip().lower()
    if not value:
        raise SlugError("порожній слаг компанії")
    bad = sorted(set(value) - _SLUG_ALLOWED)
    if bad:
        raise SlugError(
            f"слаг {slug!r} містить неприпустимі символи: {''.join(bad)}. "
            "Потрібен піддомен зі сторінки компанії "
            "(<слаг>.recruitee.com), а не URL."
        )
    return value


def parse_stamp(raw: str | None) -> datetime | None:
    """`2026-08-06 14:54:59 UTC` → tz-aware datetime.

    Зона в рядку названа явно, тому нічого не припускаємо — на відміну від
    work.ua, де її довелось виводити з правила переходу на літній час.
    """
    text = (raw or "").strip()
    if not text:
        return None
    text = text[:-4].strip() if text.endswith(" UTC") else text
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def plain(text: str | None) -> str:
    return clean(html.unescape(_TAG.sub(" ", text or "")))


def location_of(offer: dict) -> str:
    """Місто плюс формат — у тому ж вигляді, що й в інших джерел.

    Поле `location` від Recruitee для віддаленої вакансії дорівнює «Remote
    job» і ГУБИТЬ місто, хоча воно в даних є окремо. Складаємо самі: місто
    лишається містом, віддаленість стає форматом.
    """
    parts: list[str] = []
    city = clean(offer.get("city") or "")
    country = clean(offer.get("country") or "")
    if city:
        parts.append(city)
    if country and country.lower() not in ("ukraine", "україна") and country != city:
        parts.append(country)
    if offer.get("remote"):
        parts.append("віддалено")
    elif offer.get("hybrid"):
        parts.append("гібрид")
    return ", ".join(parts)


def salary_of(offer: dict) -> str:
    """`{'min': …, 'max': …, 'currency': …, 'period': …}` → рядок або порожньо.

    Порожні поля лишаються порожнім рядком, а не «0» чи «не вказано»:
    відсутність числа не є числом.
    """
    data = offer.get("salary") or {}
    low, high = data.get("min"), data.get("max")
    if not low and not high:
        return ""
    money = f"{low}–{high}" if low and high else str(low or high)
    return " ".join(x for x in (money, data.get("currency") or "",
                                data.get("period") or "") if x).strip()


class RecruiteeSource:
    """Читає публічний API перелічених компаній. Один слаг — одна компанія."""

    name = "recruitee"
    kind = "company"
    provides = frozenset({"company", "location", "salary"})

    def __init__(self, companies: list[str], timeout: float = 30.0,
                 every_hours: float = 24.0) -> None:
        self.companies = [check_slug(s) for s in companies]
        self.timeout = timeout
        # Як часто це джерело взагалі забирається. Читає `pipeline.collect`.
        self.every_hours = float(every_hours)
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
            for slug in self.companies:
                yield from self._fetch_one(client, slug, seen)

        # 🔴 Нуль в ОДНІЙ компанії — норма: вона не наймає. Нуль в УСІХ разом
        # — майже напевно зміна API, а не збіг. Ми вже тричі приймали
        # відсутність за факт, тому цей випадок мусить бути гучним.
        healthy = [h for h in self.feed_health.values() if h["state"] != "failed"]
        if self.companies and healthy and not any(h["count"] for h in healthy):
            self.fetch_complete = False
            self.warnings.append(
                f"жодна з {len(healthy)} компаній не віддала жодної вакансії — "
                "це радше зміна API, ніж збіг; сигнал зникнення вимкнено")

    def _fetch_one(self, client, slug: str, seen: set[str]) -> Iterable[Vacancy]:
        # Одна мертва компанія не валить решту: вона стає рядком із причиною.
        try:
            response = client.get(API_URL.format(slug=slug))
        except Exception as exc:
            self._fail(slug, f"{type(exc).__name__}: {exc}")
            return
        if response.status_code != 200:
            self._fail(slug, f"HTTP {response.status_code}"
                             + (" — такої компанії на Recruitee немає"
                                if response.status_code == 404 else ""))
            return
        try:
            offers = response.json().get("offers", [])
        except Exception as exc:
            self._fail(slug, f"відповідь не є JSON: {type(exc).__name__}")
            return

        # Посилання перевіряємо ОДИН раз на компанію, а не на вакансію:
        # хостинг вимикається цілком, не поштучно.
        sample = next((o.get("careers_url") for o in offers if o.get("careers_url")), "")
        if sample and not self._link_alive(client, slug, sample):
            self.feed_sizes[slug] = 0
            self.feed_health[slug] = {
                "state": "failed", "count": 0,
                "error": ("кар'єрний сайт не хоститься на Recruitee — посилання "
                          "з API ведуть на рекламну сторінку самої ATS. Вакансії "
                          "справжні, але відкрити їх звідси неможливо, тому "
                          "компанію не показуємо."),
                "title": slug,
            }
            self.warnings.append(
                f"{slug}: посилання з API ведуть не на компанію — пропущено")
            self.fetch_complete = False
            return

        rows = [o for o in offers if self._is_open(o)]
        self.feed_sizes[slug] = len(rows)
        company = clean(next((o.get("company_name") or "" for o in offers), "")) or slug
        # Нуль — не привід зникати з переліку: компанія, за якою стежимо,
        # мусить стояти з нулем. Це правило власник підтвердив чотири рази.
        self.feed_health[slug] = {
            "state": "ok" if rows else "empty",
            "count": len(rows),
            "error": "",
            "title": company,
        }
        for offer in rows:
            uid = str(offer.get("id") or offer.get("guid") or "")
            if not uid or uid in seen:
                continue
            seen.add(uid)
            yield Vacancy(
                source=self.name,
                title=clean(offer.get("title") or ""),
                company=company,
                location=location_of(offer),
                url=offer.get("careers_url") or "",
                # Мітка — слаг компанії, а НЕ `tags` з API: там внутрішні
                # позначки рекрутера вигляду `@vlada`.
                tags=(slug,),
                feed=slug,
                posted_at=parse_stamp(offer.get("published_at")),
                source_uid=uid,
                salary=salary_of(offer),
                description=plain(offer.get("description")),
                employment_type=clean(offer.get("employment_type_code") or ""),
            )

    def _is_open(self, offer: dict) -> bool:
        """Відкинути те, що джерело саме називає закритим.

        `close_at` — пряма дата закриття від роботодавця, сильніша за наш
        здогадний сигнал зникнення. ⚠️ На живих даних вона поки жодного разу
        не була заповнена (0 з 20 вакансій), тобто гілка написана за полем,
        яке API оголошує, а не перевірена на факті.
        """
        if clean(offer.get("status") or "") not in ("", "published"):
            return False
        closes = parse_stamp(offer.get("close_at"))
        return not (closes and closes <= datetime.now(timezone.utc))

    def _link_alive(self, client, slug: str, url: str) -> bool:
        """Чи веде посилання туди, куди обіцяє.

        Перевіряємо ХОСТ, а не код відповіді: мертвий тенант віддає чесні
        200 — просто на сторінці самої Recruitee.
        """
        try:
            response = client.get(url)
        except Exception:
            return False
        return f"{slug}.recruitee.com" in str(response.url)

    def _fail(self, slug: str, reason: str) -> None:
        self.feed_health[slug] = {"state": "failed", "count": 0,
                                  "error": reason, "title": slug}
        self.feed_sizes[slug] = 0
        self.warnings.append(f"{slug}: {reason}")
        # Помилка компанії робить вибірку неповною — інакше зникнення
        # вакансій із непрочитаної компанії зарахувалось би за їх зняття.
        self.fetch_complete = False
