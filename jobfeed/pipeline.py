"""Пайплайн: джерела → нормалізація → фільтр → дедуп → sinks.

Ядро не знає ні про DOU, ні про HTML, ні про месенджери — тільки про
інтерфейси `Source` і `Sink`. Додати сайт або канал доставки можна,
не торкаючись цього файлу.
"""

from __future__ import annotations

import dataclasses
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import budget as budget_module
from . import english
from .directions import DirectionClassifier
from . import health as health_module
from .filters import VacancyFilter
from .health import SourceHealth
from .models import Entry, Vacancy, match_key
from .paths import resolve
from .reservation import ReservationDetector
from .sinks import build as build_sinks
from .sources import KINDS as SOURCE_KINDS
from .sources import PROVIDES as SOURCE_PROVIDES
from .sources import build as build_sources
from .sources.base import AdapterContractError
from .store import Store, now_stamp

DEFAULT_DB = "%LOCALAPPDATA%/find_new/state.db"


@dataclass
class RunReport:
    fetched: int = 0
    kept: int = 0
    fresh: int = 0
    window: int = 0
    seeded: bool = False
    backfilled: int = 0
    cross_source_pairs: int = 0
    likely_closed: int = 0
    coverage: dict[str, tuple[int, int, list[str]]] = field(default_factory=dict)
    absence_skips: list[str] = field(default_factory=list)
    reservations: Counter = field(default_factory=Counter)
    outputs: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # Помилки, через які прогін НЕ можна вважати успішним, — окремо від
    # решти. Мережевий збій джерела минає сам, зламаний рендер — ні.
    critical_errors: list[str] = field(default_factory=list)
    digest_written: datetime | None = None
    # Чи записано сторінку САМЕ ЦИМ прогоном, а не попереднім.
    digest_fresh: bool = False
    dropped: list[tuple[Vacancy, str]] = field(default_factory=list)
    new_vacancies: list[Vacancy] = field(default_factory=list)
    # Стан кожного налаштованого джерела — і живого, і впалого, і вимкненого.
    source_health: dict = field(default_factory=dict)
    # (скільки вакансій із рівнем англійської, скільки всього на сторінці).
    #
    # Знаменник тут — УСІ вакансії, а не «ті, чиї джерела дають поле», як у
    # решті рядків заповненості. Причина: опис є в усіх, тож відсутність
    # рівня — факт про вакансію, а не про можливості джерела.
    english: tuple[int, int] = (0, 0)
    # Скільки бюджетів дістали з ОПИСУ понад те, що дало поле джерела.
    budgets_from_text: int = 0


def _hours_until_due(source, previous: dict) -> float:
    """Скільки годин лишилось до наступного забору цього джерела.

    0 означає «пора». Джерело без `every_hours` забирається щоразу — саме
    тому перевірка тут, а не в адаптері: мовчазний пропуск був би невидимий.
    """
    every = float(getattr(source, "every_hours", 0) or 0)
    if every <= 0:
        return 0.0
    was = (previous or {}).get(source.name)
    stamp = getattr(was, "last_try", "") if was else ""
    if not stamp:
        return 0.0
    try:
        last = datetime.fromisoformat(stamp)
    except (ValueError, TypeError):
        return 0.0
    passed = (datetime.now(timezone.utc) - last).total_seconds() / 3600
    return max(0.0, round(every - passed, 1))


def collect(
    config: dict,
    cursors: dict[str, str] | None = None,
    previous_health: dict | None = None,
) -> tuple[list[Vacancy], list[str], list, dict[str, SourceHealth]]:
    """Тягне всі увімкнені джерела. Впале джерело не валить прогін.

    Повертає ще й самі джерела — вони потрібні далі, щоб збагатити нові
    вакансії (див. `Enrichable`): робити це до дедупу означало б сотні
    зайвих запитів щогодини.
    """
    vacancies: list[Vacancy] = []
    errors: list[str] = []
    healths: dict[str, SourceHealth] = {}
    sources = build_sources(config.get("sources", {}))
    for source in sources:
        # Мітка минулого забору для джерел, які вміють інкрементально
        # (див. `Incremental`). Хто не вміє — просто не має цього поля.
        if cursors and hasattr(source, "cursor"):
            source.cursor = cursors.get(source.name, "")
        health = SourceHealth(
            name=source.name,
            full_scan=bool(getattr(source, "scans_full_feed", True)),
        )

        # Джерела з власною періодичністю (фіди компаній — раз на добу).
        #
        # 🔴 Пропущений забір НЕ можна плутати з порожнім. Якщо джерело цього
        # разу не питали, його вакансій немає в `vacancies` — і сигнал
        # зникнення, який рахує «чого не було у фіді», позначив би ЗАКРИТИМИ
        # всі вакансії цього джерела за один прогін. Тому позначаємо саме
        # джерело, і `track_absence` вимикається для нього явно, за назвою
        # причини, а не через випадковий збіг умов.
        wait = _hours_until_due(source, previous_health)
        if wait > 0:
            source.skipped_this_run = True
            health.state = "waiting"
            health.wait_hours = wait
            healths[source.name] = health
            continue

        try:
            fetched = list(source.fetch())
        except Exception as exc:  # мережа/розмітка джерела — не наша аварія
            errors.append(f"{source.name}: {type(exc).__name__}: {exc}")
            fetched = []
            # Причину зберігаємо ТЕКСТОМ, а не прапорцем: «не забралось»
            # не каже, що робити, а `HTTPError 503` і `SSLError` ведуть до
            # різних дій — почекати чи лізти в адаптер.
            health.state, health.error = "failed", f"{type(exc).__name__}: {exc}"
        else:
            health.state = "ok" if fetched else "empty"
        health.complete = bool(getattr(source, "fetch_complete", True))
        health.fetched = len(fetched)
        # Пофідний стан, якщо адаптер уміє його звітувати. Джерело з 21 фіда,
        # де впав один, на рівні джерела виглядає здоровим.
        health.feeds = dict(getattr(source, "feed_health", {}) or {})
        # Глибина кожного фіда — за ЦИМ забором, а не за тим, що накопичилось
        # у базі.
        #
        # 🔴 Перша версія рахувала розкид дат серед КАРТОК НА СТОРІНЦІ — і
        # видала «бачили за 28 дн» для фідів Djinni при вікні сторінки 14.
        # Картки накопичуються прогонами, тож той розкид росте сам собою й
        # каже про НАШУ історію, а не про те, як глибоко бачить джерело.
        # Підпис при цьому обіцяв друге. Та сама підміна сусідньої величини,
        # тільки цього разу я зробила її у власній новій мітці.
        by_feed: dict[str, list] = {}
        for vacancy in fetched:
            if vacancy.posted_at:
                by_feed.setdefault(vacancy.feed, []).append(vacancy.posted_at)
        for label, dates in by_feed.items():
            if label in health.feeds and len(dates) >= 3:
                health.feeds[label]["depth_days"] = (max(dates) - min(dates)).days
        healths[source.name] = health
        # Свідомо ПОЗА try: це не збій джерела, а порушення контракту
        # адаптером, і воно мусить упасти, а не осісти в переліку помилок.
        _require_feed_labels(source, fetched)
        vacancies.extend(fetched)

    # Вимкнені джерела теж отримують рядок — інакше вони зникають із меню
    # цілком, а зникнення рядка ми вже вирішили сигналом не вважати.
    for name, reason in health_module.collect_states(
        config.get("sources", {}), set(healths)
    ).items():
        healths[name] = SourceHealth(name=name, state="off", note=reason)

    return vacancies, errors, sources, healths


def _require_feed_labels(source, vacancies: list[Vacancy]) -> None:
    """Джерело, що бере участь у сигналі зникнення, мусить мітити свої записи.

    Умови 3-4 в `track_absence` рахуються ПОФІДНО. Запис без мітки зіллється
    з усіма іншими такими ж в один псевдофід, вікно порахується по обʼєднанню
    різних фідів — і сигнал працюватиме на вигаданій величині, нічого при
    цьому не зламавши.

    Раніше від цього нас беріг випадковий збіг: єдине джерело без мітки
    (HappyMonday) виявилось єдиним інкрементальним, тобто в сигналі й так не
    брало участі. Збіг ніде не був записаний і наступний адаптер міг його
    розірвати — тому перевіряємо явно.
    """
    if not getattr(source, "scans_full_feed", True):
        return
    unlabelled = sum(1 for v in vacancies if not v.feed)
    if unlabelled:
        raise AdapterContractError(
            f"{source.name}: {unlabelled} записів без мітки фіда. Джерело з "
            "scans_full_feed = True бере участь у сигналі зникнення, а той "
            "рахує вікно пофідно — записи без мітки зіллються в один "
            "псевдофід і сигнал працюватиме на вигаданій величині. "
            "Проставте `feed=` у _to_vacancy() цього адаптера."
        )


def enrich(sources: list, vacancies: list[Vacancy]) -> tuple[list[Vacancy], list[str]]:
    """Добирає бракуючі поля для НОВИХ вакансій — по одному запиту на кожну.

    Джерело без методу `enrich` (як DOU, у якого все є у фіді) проходить
    наскрізь без жодного запиту.
    """
    errors: list[str] = []
    by_source = {source.name: source for source in sources}
    result: list[Vacancy] = []

    for name, source in by_source.items():
        batch = [v for v in vacancies if v.source == name]
        if not batch:
            continue
        if not hasattr(source, "enrich"):
            result.extend(batch)
            continue
        try:
            result.extend(source.enrich(batch))
        except Exception as exc:
            errors.append(f"{name} (збагачення): {type(exc).__name__}: {exc}")
            result.extend(batch)  # без деталей, але не втрачаємо вакансії

    known = {v.source for v in result}
    result.extend(v for v in vacancies if v.source not in known)
    return result, errors


def track_absence(store, sources: list, vacancies: list[Vacancy],
                  errors: list[str], skips: list[str]) -> int:
    """«Вакансія зникла з фіда» → ймовірно закрита. П'ять умов, усі обов'язкові.

    Сигнал безкоштовний і сильніший за будь-яке поле джерела (`validThrough`
    у Djinni виявився формулою `datePosted + 30 днів`, а не фактом закриття).
    Але він ІМОВІРНІСНИЙ, і кожна умова нижче закриває окремий спосіб
    переплутати «зняли вакансію» з «ми просто її не побачили»:

    1. Джерело віддає ПОВНУ вибірку щоразу. Для інкрементального відсутність
       запису означає «не нова», а не «знята».
    2. Забір завершився без помилок, попереджень і без вичерпання стелі
       сторінок. Відсутність даних не є доказом закриття — те саме правило,
       що з `hint` і порожньою компанією: мовчання джерела не є твердженням.
    3. У фіді щонайменше два записи з датами — з одного вікна не існує.
    4. Вакансія молодша за ПОЛОВИНУ фактичного вікна фіда — перевіряється в
       момент позначення, за вікном ЦЬОГО прогону. Так сплеск публікацій сам
       вимикає сигнал для вакансій, які він же й виштовхав: більше записів за
       менший час звужує вікно, і вони виходять за межу.
    5. Відсутня два прогони поспіль (див. `Store.mark_absence`): один
       частковий збій не має перетворювати половину стрічки на «закриті».

    Умови 3-4 рахуються ПОФІДНО, а не на джерело: малий сусідній фід не має
    гасити висновки по решті.

    Окремої перевірки «чи фід на своїй стелі» тут НЕМАЄ, і це свідомо.
    Спроба вивести стелю з кількості записів провалилась на замірі: фіди
    Djinni мають однакові вікна (27.7-28.1 доби) при різних кількостях
    (31, 32, 43, 55) — тобто джерело ріже фід ЧАСОМ, а не кількістю, і
    «стеля записів» до нього просто не застосовна. Умова про половину вікна
    робить ту саму роботу для обох режимів обрізання одразу, без історії,
    без константи й без припущень про те, як саме джерело ріже.
    """
    total = 0
    for source in sources:
        # Нуль без причини виглядає однаково і коли правило працює, і коли
        # воно просто не спрацювало. Тому кожне вимкнення називається вголос.
        def skip(feed: str, why: str) -> None:
            skips.append(f"{source.name}{':' + feed if feed else ''} — {why}")

        if getattr(source, "skipped_this_run", False):
            # Не «нічого не знайшли», а «не дивились». Без цієї гілки
            # відсутність у фіді, якого не питали, зарахувалась би за доказ
            # зняття — і джерело з добовою періодичністю позначало б УСІ свої
            # вакансії закритими на кожному прогоні, де його не забирали.
            skip("", "забір цього прогону не робився (власна періодичність)")
            continue
        if not getattr(source, "scans_full_feed", True):
            skip("", "забір інкрементальний, відсутність ≠ зняття")
            continue
        if not getattr(source, "fetch_complete", True):
            # Саме ПОВНОТА забору, а не будь-яке попередження: вичерпаний
            # бюджет збагачення деталей повноти не стосується — список
            # прочитано цілком, добираються лише поля. Інакше сигнал
            # вимикався б там, де фід насправді прочитано повністю.
            #
            # Брудний прогін РОЗРИВАЄ серію, а не просто пропускається:
            # інакше ми зарахували б за доказ відсутність у прогоні, де
            # свідомо не дивились. Помилка в бік «не позначили» коштує зайвої
            # картки, у бік «позначили» — ховає живу вакансію.
            broken = store.reset_absence(source.name)
            skip("", f"забір неповний — обірвано серій: {broken}")
            continue
        if any(e.startswith(f"{source.name}:") for e in errors):
            broken = store.reset_absence(source.name)
            skip("", f"помилка забору — обірвано серій: {broken}")
            continue

        batch = [v for v in vacancies if v.source == source.name]
        sizes = getattr(source, "feed_sizes", None) or {"": len(batch)}
        # Присутність перевіряється по ВСІХ фідах джерела, а не по своєму:
        # роботодавець міняє категорію, вакансія переїжджає в сусідній фід і
        # зі свого зникає — жива, але для нас «зникла».
        present_anywhere = {v.id for v in batch}

        # Далі — пофідно. Сусідній фід того самого джерела може стояти на
        # стелі або бути надто малим, і це не привід не робити висновків тут:
        # у Djinni фід `UX Research` стабільно віддає 1 запис, і на рівні
        # джерела він сам-один вимкнув би сигнал для всіх п'яти фідів.
        for feed in sizes:
            items = [v for v in batch if v.feed == feed]
            dated = sorted(v.posted_at for v in items if v.posted_at)
            if len(dated) < 2:
                skip(feed, f"записів з датами {len(dated)} — вікна не існує")
                continue

            window = dated[-1] - dated[0]
            if window.total_seconds() <= 0:
                skip(feed, "нульове вікно")
                continue

            # Половина вікна, а не все. Запас тут не косметичний: у DOU
            # вікно 4.1 доби при темпі 6.1 вакансії на добу, тож щоб
            # виштовхати запис віком 2 доби, треба 12.5 на добу — удвічі
            # більше за фактичний темп.
            #
            # Перевіряється в момент ПОЗНАЧЕННЯ, за вікном цього прогону, а
            # не в момент першого промаху: сплеск публікацій може виштовхати
            # живу вакансію з фіда, але він же звужує вікно — і вона сама
            # виходить за межу, а серія скидається (див. `mark_absence`).
            cutoff = dated[-1] - window / 2

            total += store.mark_absence(
                source.name, feed, present_anywhere, cutoff.isoformat()
            )

    return total


def _parse_stamp(raw: str) -> datetime | None:
    try:
        return datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None


def _direction_defaults(config: dict) -> list[str]:
    """Які напрями обрані на чистому браузері. Порожньо = всі.

    Читається з `default = true/false` у секції кожного напряму — тобто
    правиться там само, де ключі групи, а не в коді. Якщо не проставлено
    жодного `default`, поведінка лишається старою: обрані всі.
    """
    sections = config.get("directions", {}) or {}
    marked = [name for name, item in sections.items()
              if isinstance(item, dict) and "default" in item]
    if not marked:
        return []
    return [name for name in marked if sections[name].get("default")]


def _source_warnings(sources: list) -> list[str]:
    return [f"{s.name}: {w}" for s in sources for w in getattr(s, "warnings", [])]


def link_across_sources(entries: list[Entry]) -> tuple[list[Entry], int]:
    """Помічає однакові вакансії на РІЗНИХ джерелах. Нічого не ховає.

    Рішення власника проєкту, підперте заміром: злиття за ключем
    (компанія + посада) помилково обʼєднало б 4 законні пари заради одного
    справжнього дубля. Наслідки несиметричні — зайва картка це секунда шуму,
    а помилково схована вакансія людині просто не покажеться, і вона про це
    навіть не дізнається.

    🔴 ПРАВИЛО, СПОРІДНЕНЕ З ПРАВИЛОМ ПРО hint: дедуп не має права ховати
    запис, у тотожності якого ми не впевнені. Сумнів вирішується на користь
    показу.

    Тому тут лише мʼяке перехресне посилання, і лише МІЖ джерелами: збіги
    всередині одного джерела не чіпаємо взагалі — заміряно, що це законні
    різні вакансії.
    """
    groups: dict[str, list[Entry]] = {}
    for entry in entries:
        if entry.vacancy.company:  # без компанії ключ надто широкий
            groups.setdefault(match_key(entry.vacancy), []).append(entry)

    linked: dict[int, tuple[tuple[str, str], ...]] = {}
    pairs = 0
    for group in groups.values():
        if len({e.vacancy.source for e in group}) < 2:
            continue
        pairs += 1
        for entry in group:
            others = tuple(
                (e.vacancy.source, e.vacancy.url)
                for e in group
                if e.vacancy.source != entry.vacancy.source
            )
            linked[id(entry)] = others

    result = [
        dataclasses.replace(e, also_on=linked[id(e)]) if id(e) in linked else e
        for e in entries
    ]
    return result, pairs


def merge_linked(entries: list[Entry]) -> tuple[list[Entry], int]:
    """Зливає картки, повʼязані перехресним посиланням, в одну.

    🔴 Зливаємо ЛИШЕ МІЖ джерелами, і це не деталь.

    Ризик, через який ми довго відмовлялись від злиття, живе **всередині**
    одного джерела: заміряно 02.08.2026 — вісім груп однакових за ключем
    вакансій усередині Djinni, і всі вісім це РІЗНІ вакансії (`Luxoft`
    чотири різні банківські позиції з однаковою назвою). Між джерелами
    хибних збігів немає жодного: 14 пар, усі справжні дублі.

    Тобто попередній висновок «злиття помилково обʼєднає 4 законні пари»
    був правильний про збіги в одному джерелі й хибний про злиття між ними.

    І головне: ми **вже стверджуємо**, що це одна вакансія — малюємо на
    обох картках однакові бирки. Дві картки суперечили нашому ж твердженню.
    """
    groups: dict[str, list[Entry]] = {}
    for entry in entries:
        if entry.vacancy.company:
            groups.setdefault(match_key(entry.vacancy), []).append(entry)

    merged_of: dict[int, Entry] = {}
    drop: set[int] = set()
    pairs = 0
    for group in groups.values():
        if len({e.vacancy.source for e in group}) < 2:
            continue
        pairs += 1
        head = _merge_entries(group)
        merged_of[id(group[0])] = head
        drop.update(id(e) for e in group[1:])

    result = [merged_of.get(id(e), e) for e in entries if id(e) not in drop]
    return result, pairs


def _merge_entries(group: list[Entry]) -> Entry:
    """Одна картка з кількох. Порожнє НІКОЛИ не витісняє заповнене.

    Те саме правило, що в `store.update()`: збагачення може лише
    заповнювати. На скриншоті власника одна картка мала досвід, друга ні —
    і злита мусить мати обидва поля, а не втратити досвід через те, що
    інша картка прийшла першою.
    """
    def first(getter):
        for entry in group:
            value = getter(entry)
            if value not in ("", None, (), [], {}):
                return value
        return getter(group[0])

    base = group[0]
    vacancy = dataclasses.replace(
        base.vacancy,
        company=first(lambda e: e.vacancy.company),
        location=first(lambda e: e.vacancy.location),
        salary=first(lambda e: e.vacancy.salary),
        description=first(lambda e: e.vacancy.description),
        experience_months=first(lambda e: e.vacancy.experience_months),
        employment_type=first(lambda e: e.vacancy.employment_type),
        # Найраніша дата публікації: саме тоді вакансія зʼявилась на світ.
        # Пізніша — це коли її передрукувало друге джерело, а не коли її
        # відкрили.
        posted_at=min((e.vacancy.posted_at for e in group
                       if e.vacancy.posted_at), default=base.vacancy.posted_at),
    )
    seen = [e.first_seen for e in group if e.first_seen]
    return dataclasses.replace(
        base,
        vacancy=vacancy,
        ids=tuple(dict.fromkeys(e.vacancy.id for e in group)),
        # Нова, якщо нова хоч в одному джерелі: інакше вакансія, яку ми
        # щойно побачили вперше, загубилась би серед старих.
        is_new=any(e.is_new for e in group),
        first_seen=min(seen, default=base.first_seen),
        reservation=first(lambda e: e.reservation),
        directions=first(lambda e: e.directions),
        direction_evidence=first(lambda e: e.direction_evidence),
        english=first(lambda e: e.english),
        english_evidence=first(lambda e: e.english_evidence),
        budget=first(lambda e: e.budget),
        budget_evidence=first(lambda e: e.budget_evidence),
        # Закритою вважаємо лише коли ЖОДНЕ джерело її вже не показує.
        likely_closed=all(e.likely_closed for e in group),
    )


def screen(config: dict, vacancies: list[Vacancy]):
    """Ключові слова + (за потреби) вимога бронювання.

    Вимога броні свідомо застосовується ТУТ, разом з рештою фільтра, а не
    на етапі показу: інакше «нових» рахувалось би одне, а на сторінці було б
    інше. Вимкнена за замовчуванням — див. `jobfeed/reservation.py`.
    """
    # Джерела, які самі відбирають вакансії точною категорією. Для них
    # позитивний список слів вимикається — див. `VacancyFilter.decide`.
    trusted = {
        name
        for name, source_cfg in config.get("sources", {}).items()
        if isinstance(source_cfg, dict) and source_cfg.get("trust_category")
    }
    kept, dropped = VacancyFilter.from_config(config.get("filter", {})).apply(
        vacancies, trusted
    )

    reservation_cfg = config.get("reservation", {})
    if not reservation_cfg.get("required", False):
        return kept, dropped

    detector = ReservationDetector(reservation_cfg)
    with_reservation, without = [], []
    for vacancy in kept:
        if detector.detect(vacancy.description, vacancy.reservation_hint).is_yes:
            with_reservation.append(vacancy)
        else:
            without.append((vacancy, "бронювання не згадане в тексті"))
    return with_reservation, dropped + without


def run(config: dict, dry_run: bool = False) -> RunReport:
    run_cfg = config.get("run", {})
    report = RunReport()
    started_at = datetime.now(timezone.utc)

    db_path = resolve(run_cfg.get("db", DEFAULT_DB))
    with Store(db_path) as store:
        cursors = {
            name: store.get_meta(f"cursor:{name}")
            for name in config.get("sources", {})
        }
        stored_health = health_module.load(store)
        vacancies, report.errors, sources, healths = collect(
            config, cursors, stored_health
        )
        report.fetched = len(vacancies)

        kept, report.dropped = screen(config, vacancies)
        report.kept = len(kept)

        # Холодний старт. Джерела без дат публікації існують (Andersen не має
        # жодного поля з датою), тому «нове» — це різниця множин ID з попереднім
        # запуском, а не «свіже за датою». Отже на порожній БД чесної відповіді
        # про новизну не існує: перший прогін ЗАСІВАЄ базу і не позначає нічого
        # новим, інакше людина отримає сотні «нових» вакансій на рівному місці.
        # Те саме спрацює після втрати БД — і це правильно.
        report.seeded = store.count() == 0

        fresh = store.new_only(kept)

        if dry_run:
            report.fresh = 0 if report.seeded else len(fresh)
            report.new_vacancies = [] if report.seeded else fresh
            report.warnings.extend(_source_warnings(sources))
            return report

        # Збагачуємо ТІЛЬКИ нові — на це й розрахована схема 1+N. Робити це
        # до дедупу означало б сотні запитів щогодини замість одиниць.
        if fresh:
            fresh, enrich_errors = enrich(sources, fresh)
            report.errors.extend(enrich_errors)

        report.fresh = 0 if report.seeded else len(fresh)
        report.new_vacancies = [] if report.seeded else fresh

        # Хто зник із фіда. Робиться ПІСЛЯ запису нових, щоб щойно додані
        # вакансії одразу рахувались побаченими, а не набирали промах.
        report.likely_closed = track_absence(
            store, sources, vacancies, report.errors, report.absence_skips
        )

        # Спершу запамʼятовуємо, потім віддаємо. Дайджест — це стан, який
        # перемальовується з БД, тому впалий sink нічого не «губить»:
        # наступний прогін перемалює сторінку з тими самими даними.
        stamp = store.remember(fresh) if fresh else None
        window_days = int(run_cfg.get("window_days", 14))

        # Добір: те, що не влізло в бюджет запитів раніше (передусім на
        # холодному старті), дозбирується наступними прогонами. Без цього
        # такі вакансії лишились би без компанії назавжди — новими вони
        # більше не рахуються.
        backfill = store.incomplete(
            [s.name for s in sources if hasattr(s, "enrich")],
            days=window_days,
            limit=int(run_cfg.get("backfill_limit", 40)),
        )
        if backfill:
            filled, backfill_errors = enrich(sources, backfill)
            report.errors.extend(backfill_errors)
            report.backfilled = store.update([v for v in filled if v.company])

        stored = store.recent(window_days)

        # Правка конфіга має діяти й на вже збережене: якщо власник додав
        # стоп-слово, стара вакансія має зникнути зі сторінки, а не висіти
        # там вічно тільки тому, що потрапила в базу до правки.
        passing = {v.id for v in screen(config, [v for v, _ in stored])[0]}

        visible = [(v, seen) for v, seen in stored if v.id in passing]

        detector = ReservationDetector(config.get("reservation", {}))
        directions = DirectionClassifier.from_config(config.get("directions", {}))
        entries = [
            Entry(
                vacancy=vacancy,
                is_new=not report.seeded and stamp is not None and seen_at == stamp,
                reservation=detector.detect(vacancy.description, vacancy.reservation_hint),
                likely_closed=vacancy.missing_streak >= 2,
                first_seen=_parse_stamp(seen_at),
                directions=directions.classify(vacancy),
                direction_evidence=directions.evidence(vacancy),
                **dict(zip(("english", "english_evidence"),
                           english.detect(vacancy.description))),
                **dict(zip(("budget", "budget_evidence"),
                           budget_module.detect(vacancy.description, vacancy.salary))),
            )
            for vacancy, seen_at in visible
        ]
        entries = [dataclasses.replace(e, ids=(e.vacancy.id,)) for e in entries]
        entries, report.cross_source_pairs = link_across_sources(entries)
        entries, _ = merge_linked(entries)
        report.window = len(entries)
        report.reservations = Counter(entry.reservation.state for entry in entries)

        # Заповненість полів у зведенні — не попередження, а число, яке видно.
        # Найпростіший спосіб зламати нам поле — не помилка, а ВІДСУТНІСТЬ:
        # джерело перестає віддавати ключ, нічого не падає, жоден лічильник
        # помилок не ворухнеться, і чипи просто тихо зникають. Число в
        # зведенні це видно оком, без арифметики.
        #
        # Показуємо лише те, чия відсутність міняє рішення людини. Службові
        # поля сюди не додавати — вони перетворять зведення на шум.
        #
        # Знаменник — НЕ вся видача, а лише вакансії з тих джерел, які це поле
        # взагалі дають. Інакше «досвід 161 з 207» читалось би як «46 втрачено»,
        # хоча 44 з них — DOU і HappyMonday, у яких такого поля немає в
        # принципі. Перелік джерел не зашитий: він виводиться з того, чи
        # трапилось значення хоч раз цього прогону.
        fields = {
            "компанія": ("company", lambda v: bool(v.company)),
            "локація": ("location", lambda v: bool(v.location)),
            "досвід": ("experience_months", lambda v: v.experience_months is not None),
            "зарплата": ("salary", lambda v: bool(v.salary)),
        }
        shown = {e.vacancy.source for e in entries}
        report.coverage = {}
        for name, (column, has_value) in fields.items():
            # Знаменник — з ОГОЛОШЕННЯ адаптера, не з побачених значень і не
            # з БД. Виведений знаменник зникав би разом із полем: джерело
            # перестало віддавати досвід — і рядок став би «0 з 0» замість
            # «0 з 163 djinni», тобто показник сліпнув би рівно в момент
            # поломки. Оголошення ж не залежить ні від історії, ні від того,
            # коли власник видалив базу.
            # Перетинаємо з тим, що є на сторінці, щоб вимкнене джерело не
            # тягнулось у зведенні вічно.
            giving = {s for s in shown if column in SOURCE_PROVIDES.get(s, frozenset())}
            applicable = [e for e in entries if e.vacancy.source in giving]
            report.coverage[name] = (
                sum(1 for e in applicable if has_value(e.vacancy)),
                len(applicable),
                sorted(giving),
            )

        report.english = (sum(1 for e in entries if e.english), len(entries))
        report.budgets_from_text = sum(1 for e in entries if e.budget)

        # Мітку зсуваємо лише після повного прогону — див. `Incremental`.
        if not dry_run:
            for source in sources:
                if getattr(source, "next_cursor", ""):
                    store.set_meta(f"cursor:{source.name}", source.next_cursor)

        # Стан кожного джерела: цей забір поверх памʼяті про попередні.
        #
        # Перелік навмисно доповнюється з `SOURCE_KINDS`, а не будується з
        # того, що прийшло: адаптер, який є в коді, але випав з конфіга,
        # мусить мати рядок «жодного разу не забиралось», а не бути
        # відсутнім. Відсутність рядка ми вже двічі ловили як тиху
        # поломку — втретє не наступаємо.
        for name in SOURCE_KINDS:
            healths.setdefault(name, SourceHealth(name=name))
        healths = {
            name: health_module.merge(stored_health, h)
            for name, h in healths.items()
        }
        if not dry_run:
            health_module.save(store, healths)
        report.source_health = healths

    report.warnings.extend(_source_warnings(sources))

    # Мертвий ключ невідрізнимий від відсутнього напряму — тому перелік
    # мусить сказати про себе, що в ньому не спрацьовує жодного разу.
    dead = directions.dead_keys([e.vacancy for e in entries])
    if dead:
        report.warnings.append(
            f"ключів напрямів, які нічого не ловлять: {len(dead)} — "
            + ", ".join(dead[:6]) + ("…" if len(dead) > 6 else "")
        )

    # Адреса сервера друкується в саму сторінку: файлова версія має знати,
    # куди віддавати позначки, якщо сервер запущений поруч.
    serve_url = (f"http://127.0.0.1:{int(run_cfg.get('serve_port', 8765))}")
    for sink in build_sinks(run_cfg, [s.name for s in sources], SOURCE_KINDS,
                            directions.names, healths, serve_url,
                            report.dropped, _direction_defaults(config),
                            list(config.get('sources', {}).get('dou_companies', {})
                                 .get('slugs', [])),
                            list(config.get('companies', {}).get('banking', [])),
                            dict(config.get('companies', {}).get('aliases', {}))):
        try:
            report.outputs.append(sink.emit(entries))
        except Exception as exc:
            message = f"sink {sink.name}: {type(exc).__name__}: {exc}"
            if getattr(sink, "critical", False):
                report.critical_errors.append(message)
            else:
                report.errors.append(message)

    # Коли сторінку востаннє записано. Показуємо ЧИСЛОМ, а не відсутністю
    # рядка: застарілий дайджест мусить мати присутній сигнал, інакше його
    # видно лише тому, чого немає.
    #
    # ⚠️ Порівнюємо з часом ПОЧАТКУ прогону, а не просто беремо вік файла.
    # Інакше одразу після впалого рендеру мітка показувала б «оновлена
    # щойно» — бо файл записав ПОПЕРЕДНІЙ, успішний прогін. Тобто сигнал
    # про застарілу сторінку сам би брехав саме в момент поломки.
    digest_path = resolve(run_cfg.get("digest", "digest.html"))
    if digest_path.exists():
        report.digest_written = datetime.fromtimestamp(
            digest_path.stat().st_mtime, tz=timezone.utc
        )
        report.digest_fresh = report.digest_written >= started_at

    return report
