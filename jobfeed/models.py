"""Єдина схема вакансії — до неї зводяться ВСІ джерела.

Контракт: жодне поле не вигадується. Якщо джерело не дає значення —
поле лишається порожнім (`""` / `None` / `()`), а не заповнюється здогадкою.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime

_WS = re.compile(r"\s+")


def clean(text: str | None) -> str:
    """Нормалізує пробіли (в т.ч. NBSP/narrow-NBSP з DOU) і обрізає краї."""
    if not text:
        return ""
    return _WS.sub(" ", text.replace(" ", " ").replace(" ", " ")).strip()


@dataclass(frozen=True)
class Vacancy:
    """Нормалізована вакансія.

    Ядро схеми (як домовлено): source, title, company, location, url, tags, posted_at.
    Решта — службове: `source_uid` потрібен для стабільного id, `salary`/`description`
    заповнюються лише коли джерело їх реально віддає.
    """

    source: str                       # ідентифікатор джерела, напр. "dou"
    title: str                        # посада
    company: str                      # компанія ("" якщо джерело не дало)
    location: str                     # місто/віддалено, як його подає джерело
    # Посилання на вакансію в джерелі.
    #
    # ⚠️ «Канонічне, без utm» — правило для НАШИХ міток, а не універсальне.
    # У DOU ми будуємо чистий URL самі, бо мітки там наші й зайві. Але
    # jobico.io ставить умовою доступу атрибуцію саме своїм посиланням із
    # `utm_source=job-feed`, і зрізати її означало б порушити умову, під
    # якою нас пустили. Тобто: свої мітки прибираємо, ЧУЖІ не чіпаємо.
    url: str
    tags: tuple[str, ...] = ()        # мітки джерела (категорія тощо)
    posted_at: datetime | None = None  # час публікації з джерела, tz-aware

    source_uid: str = ""              # стабільний id ВСЕРЕДИНІ джерела
    salary: str = ""                  # тільки якщо джерело віддає явно
    description: str = ""             # plain text опису, може бути ""

    # Структурована ознака бронювання ВІД ДЖЕРЕЛА: "yes" / "no" / "" (не дає).
    #
    # 🔴 ПРАВИЛО ДЛЯ ВСІХ МАЙБУТНІХ ДЖЕРЕЛ: структурована ознака може лише
    # ДОДАВАТИ впевненості, ніколи не віднімати.
    #
    # `"no"` ставиться ТІЛЬКИ якщо джерело явно й однозначно каже «броні
    # немає». Якщо джерело просто мовчить — лишай ПОРОЖНЄ, далі спрацює
    # текстова детекція. Приклад, чому це не формальність: у Djinni галочку
    # `editorial=reservation` ставить роботодавець вручну, тож її відсутність
    # означає «не поставив галочку», а не «броні немає». Перетворивши
    # мовчання на `"no"`, ти заглушиш коректну детекцію по опису — джерело
    # почне стирати те, що ми і так бачимо в тексті.
    reservation_hint: str = ""

    # Скільки місяців досвіду хоче роботодавець, як каже джерело.
    # Число, не грейд: «Middle» було б НАШОЮ інтерпретацією, а не даними.
    experience_months: int | None = None
    # Тип зайнятості від джерела ("FULL_TIME", "PART_TIME", …), як є.
    employment_type: str = ""

    # З якого саме фіда джерела прийшла вакансія. Потрібно, щоб судити про
    # зникнення пофідно: один обрізаний або стабільний фід не має вимикати
    # висновки по решті фідів того самого джерела.
    feed: str = ""

    # Скільки повних заборів поспіль джерело не показувало цю вакансію.
    # Це НАШ висновок, а не дані джерела, тому й поводимось обережніше:
    # два промахи = «ймовірно закрита», але запис лишається досяжним.
    missing_streak: int = 0

    _id: str = field(default="", repr=False, compare=False)

    @property
    def id(self) -> str:
        """Детермінований id для дедупу.

        Залежить ТІЛЬКИ від (source, source_uid) — не від часу запуску, не від
        випадковості, не від полів, які джерело може переписати (заголовок,
        зарплата, guid з таймстемпом). Якщо джерело не дало uid — беремо
        канонічний URL, він теж стабільний.
        """
        basis = self.source_uid or self.url
        return hashlib.sha1(f"{self.source}:{basis}".encode()).hexdigest()[:16]

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "source": self.source,
            "source_uid": self.source_uid,
            "title": self.title,
            "company": self.company,
            "location": self.location,
            "url": self.url,
            "tags": list(self.tags),
            "posted_at": self.posted_at.isoformat() if self.posted_at else None,
            "salary": self.salary,
            "experience_months": self.experience_months,
            "employment_type": self.employment_type,
        }


@dataclass(frozen=True)
class Entry:
    """Одиниця видачі: вакансія плюс усе, що ми про неї вивели самі.

    Похідні ознаки живуть тут, а не всередині `Vacancy`, щоб не змішувати
    дані джерела з нашими висновками. Рахуються при кожному прогоні з
    того, що лежить у базі, — тому правка конфіга діє й на старі записи.
    """

    vacancy: "Vacancy"
    is_new: bool = False
    reservation: object = None  # jobfeed.reservation.Reservation
    # Та сама вакансія, помічена на ІНШИХ джерелах: ((джерело, url), ...).
    # Саме «помічена», а не «та сама напевно» — картки лишаються обидві.
    also_on: tuple[tuple[str, str], ...] = ()
    # 🔴 Усі id, що злились у цю картку — включно з канонічним.
    #
    # Позначки привʼязані до id, а id у кожного джерела свій. Заміряно
    # 02.08.2026: із 14 пар, що зливаються, ВІСІМ мають позначку, і в шести
    # з них вона стоїть лише на ОДНІЙ із двох карток. Тобто наївний вибір
    # канонічного id сховав би шість позначок власника — те єдине, чого не
    # відновити з джерел.
    #
    # Тому картка знає всі свої id: позначена, якщо позначений будь-який,
    # а нова позначка пишеться в усі одразу.
    ids: tuple[str, ...] = ()
    # Напрями дизайну, до яких належить вакансія. Кілька — норма:
    # «Graphic/Motion Designer» це і графіка, і motion.
    directions: tuple[str, ...] = ()
    # Джерело перестало показувати цю вакансію у своєму фіді (двічі поспіль).
    # Це ВИСНОВОК, не факт, тому запис не ховається — лише переїжджає в
    # окрему згорнуту секцію і лишається досяжним.
    likely_closed: bool = False
    # Коли МИ вперше побачили вакансію. Це не свіжість, а історія наших
    # прогонів, і показувати його можна лише іншим формулюванням —
    # «знайдено», а не «опубліковано». Потрібне для джерел без дати.
    first_seen: datetime | None = None
    # Рівень англійської, як його написав роботодавець, і цитата-доказ.
    # Порожньо означає «не знаємо», а НЕ «англійська не потрібна»: 102
    # вакансії згадують мову, рівень поруч лише в 64. Те саме правило, що
    # з бронню — не згадано ≠ немає.
    english: str = ""
    english_evidence: str = ""
    # Бюджет, знайдений у ТІЛІ опису, коли джерело не дало поля `salary`.
    # Порожньо означає «не знайшли», а не «не платять».
    budget: str = ""
    budget_evidence: str = ""
    # Цитата з опису для напрямів, знайдених у ТІЛІ вакансії (наразі
    # Webflow-розробка). Для напрямів із заголовка порожньо — там доказ
    # видно в самій назві посади.
    direction_evidence: dict = None

    def as_dict(self) -> dict:
        data = self.vacancy.as_dict()
        data["is_new"] = self.is_new
        if self.reservation is not None:
            data["reservation"] = self.reservation.state
            data["reservation_evidence"] = self.reservation.evidence
        if self.directions:
            data["directions"] = list(self.directions)
        if self.likely_closed:
            data["likely_closed"] = True
        if self.direction_evidence:
            data["direction_evidence"] = dict(self.direction_evidence)
        if self.budget:
            data["budget"] = self.budget
            data["budget_evidence"] = self.budget_evidence
        if self.english:
            data["english"] = self.english
            data["english_evidence"] = self.english_evidence
        if len(self.ids) > 1:
            data["merged_ids"] = list(self.ids)
        if self.also_on:
            data["also_on"] = [{"source": s, "url": u} for s, u in self.also_on]
        return data


_PUNCT = re.compile(r"[^\w\s]+", re.UNICODE)


def match_key(vacancy: "Vacancy") -> str:
    """Ключ для ПОМІТКИ однакових вакансій на різних джерелах.

    Точний збіг нормалізованих рядків, без жодного fuzzy-матчингу. Свідомо
    грубий і свідомо НЕ використовується для приховування: заміряно, що той
    самий ключ збігається і в законно різних вакансій одного роботодавця
    (кілька схожих позицій, перевиставлені оголошення).
    """
    text = f"{vacancy.company} {vacancy.title}".lower()
    return " ".join(_PUNCT.sub(" ", text).split())
