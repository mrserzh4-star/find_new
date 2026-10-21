"""Регресійні тести на розбір і фільтрацію.

Навіщо саме ці: за один день у проєкті знайшлось ТРИ тихі втрати —
випадки, коли вакансія зникала зі стрічки, а прогін показував здорові
цифри й нуль помилок:

  1. `arts` через межі слова не ловив `Art Director` — зникло 7 арт-директорів;
  2. `\\w` включає підкреслення, тому фемінітив `Дизайнер_ка` переставав
     бути дизайнером;
  3. цитата про бронювання бралась із початку речення, а описи на DOU йдуть
     суцільним списком — у цитату потрапляв текст без самої згадки.

Жодну з них не спіймав би тест «сервіс запустився». Тому тут перевіряються
не «працює/не працює», а КОНКРЕТНІ зниклі записи — і кожен випадок нижче
колись був справжньою помилкою або справжньою пасткою джерела.

Запуск:  python -m pytest tests -q     (або: python tests/test_parsing.py)
Мережа не потрібна: усі дані — зафіксовані зразки з живих джерел.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Збережені сторінки живих джерел — мережа тестам не потрібна.
SAMPLES = Path(__file__).resolve().parent / "samples"

import dataclasses
from datetime import datetime, timezone

from jobfeed.filters import VacancyFilter
from jobfeed.models import Vacancy, match_key
from jobfeed.reservation import NO, UNKNOWN, YES, ReservationDetector
from jobfeed.sources import happymonday, jsonld_jobposting
from jobfeed.sources.dou import split_title as dou_split
from jobfeed.sources.feed_params import FeedConfigError

failures: list[str] = []


def check(name: str, got, want) -> None:
    if got != want:
        failures.append(f"{name}\n    очікували: {want!r}\n    отримали:  {got!r}")


# ── DOU: розбір заголовка ────────────────────────────────────────────────
# Формат `{посада} в {компанія}[, {зарплата}][, {локація}]`. Пастка в тому,
# що зарплата буває ВСЕРЕДИНІ, а не в кінці, тож «останній сегмент = локація»
# неправильно (4 випадки з 75 на живих даних).
def test_dou_titles() -> None:
    cases = [
        ("Motion Designer (Mental Health Unit) в Applyft, віддалено",
         ("Motion Designer (Mental Health Unit)", "Applyft", "віддалено", "")),
        # зарплата в кінці, локації немає
        ("UI/UX дизайнер в Рекрутинговий центр Сил, від&amp;nbsp;$1200",
         ("UI/UX дизайнер", "Рекрутинговий центр Сил", "", "від $1200")),
        # зарплата ВСЕРЕДИНІ переліку
        ("Graphic Designer (Email) в IMPRO Email Agency, $750–1100, віддалено",
         ("Graphic Designer (Email)", "IMPRO Email Agency", "віддалено", "$750–1100")),
        # кілька локацій, дужки з комою всередині немає
        ("Product Designer в Zeely AI, Київ, Варшава (Польща), віддалено",
         ("Product Designer", "Zeely AI", "Київ, Варшава (Польща), віддалено", "")),
        # " в " всередині посади — роздільником має бути ОСТАННІЙ
        ("Дизайнер в команду росту в Acme, Київ",
         ("Дизайнер в команду росту", "Acme", "Київ", "")),
        # роздільника немає взагалі
        ("Product Designer", ("Product Designer", "", "", "")),
    ]
    for raw, want in cases:
        check(f"DOU split_title: {raw[:40]}", dou_split(raw), want)


def test_dou_double_unescape() -> None:
    """Один прохід unescape лишає `&nbsp;` у видимому тексті (5 з 75)."""
    from jobfeed.sources.dou import unescape
    check("подвійний unescape", unescape("від&amp;nbsp;$1200 &amp;amp; бонус"),
          "від $1200 & бонус")


# ── HappyMonday: компанія із заголовка ───────────────────────────────────
def test_happymonday_titles() -> None:
    cases = [
        ("Brand &amp; UI/UX Designer до Quests Inc", ("Brand & UI/UX Designer", "Quests Inc")),
        ("Graphic Designer at MEGOGO", ("Graphic Designer", "MEGOGO")),
        # роздільник трапляється двічі — беремо останній
        ("Дизайнер до відділу маркетингу до OMGAgency",
         ("Дизайнер до відділу маркетингу", "OMGAgency")),
        # компанії немає — це нормальний стан, не помилка
        ("Product Designer", ("Product Designer", "")),
        ("VFX Artist – Freelance", ("VFX Artist – Freelance", "")),
    ]
    for raw, want in cases:
        check(f"HM split_title: {raw[:40]}", happymonday.split_title(raw), want)


# ── Фільтр: межі слова ───────────────────────────────────────────────────
def test_word_boundaries() -> None:
    f = VacancyFilter(
        include_any=["дизайн*", "ux", "ui", "art", "креатив"],
        exclude_any=["інженер-конструктор"],
        match_fields=["title"],
        locations_any=[],
    )

    def passes(title: str) -> bool:
        return f.decide(Vacancy(source="t", title=title, company="", location="", url=""))[0]

    # РЕГРЕСІЯ: підкреслення не має розривати слово (фемінітиви).
    check("Дизайнер_ка проходить", passes("Дизайнер_ка"), True)
    # РЕГРЕСІЯ: фемінітив без підкреслення теж має проходити — через основу.
    check("Дизайнерка проходить", passes("Дизайнерка"), True)
    check("Дизайнерів проходить", passes("Шукаємо дизайнерів"), True)
    # РЕГРЕСІЯ: "arts" не ловив "Art Director" — тепер ловить "art".
    check("Art Director проходить", passes("Art Director"), True)
    check("Артдиректор без збігу", passes("Артдиректор"), False)  # у конфізі є окремим словом
    # Межі мусили лишитись: коротке "ui" не ловить довші слова.
    check("build не проходить", passes("build tools engineer"), False)
    check("guideline не проходить", passes("guideline writer"), False)
    # Закінчення теж лишається межею.
    check("креативів не проходить", passes("Таргетолог - тестувач креативів"), False)
    # Стоп-слово сильніше за збіг.
    check("стоп-слово б'є include", passes("Інженер-конструктор дизайн"), False)


def test_stem_leaks() -> None:
    """Зірочка знімає праву межу — і саме тому може протікати.

    Українською безпечна ("дизайн" запозичений корінь), англійською ні:
    "design*" ловить "Designated". Тому англійські форми розписані точніше.
    Обидві сторони пастки тут закріплені: що НЕ має проходити і що має.
    """
    f = VacancyFilter(
        include_any=["дизайн*", "design", "designer*", "designs", "designing"],
        exclude_any=[], match_fields=["title"], locations_any=[],
    )

    def passes(title: str) -> bool:
        return f.decide(Vacancy(source="t", title=title, company="", location="", url=""))[0]

    # Протікання, яке зірочка створює.
    check("Designated не проходить", passes("Designated Broker Manager"), False)
    check("Designation не проходить", passes("Designation Specialist"), False)
    # Але жодна справжня форма не втрачена.
    for title in ("Design Lead", "Designer", "Senior Designers",
                  "UX Designer", "Designing Manager", "Designs Coordinator"):
        check(f"{title} проходить", passes(title), True)
    # Українська сторона: зірочка тут і потрібна, і безпечна.
    for title in ("Дизайнерка", "Веб-дизайнерка", "Дизайнування процесів"):
        check(f"{title} проходить", passes(title), True)
    # Ліва межа тримається на обох мовах.
    check("Redesign не ловиться на design", passes("Redesign Specialist"), False)


def test_trust_category() -> None:
    """Джерело, що саме відбирає, не проходить через позитивний список.

    Але стоп-слова діють ЗАВЖДИ — це запобіжник, він не має відключатись.
    """
    f = VacancyFilter(
        include_any=["ux"], exclude_any=["electronics"],
        match_fields=["title"], locations_any=[],
    )
    art = Vacancy(source="djinni", title="Art Director", company="", location="", url="")
    check("довірене джерело: без ключових слів проходить", f.decide(art, True)[0], True)
    check("недовірене джерело: без ключових слів ні", f.decide(art, False)[0], False)

    junk = Vacancy(source="djinni", title="Electronics Design", company="", location="", url="")
    check("стоп-слово діє й на довіреному", f.decide(junk, True)[0], False)


# ── Бронювання ───────────────────────────────────────────────────────────
def test_reservation() -> None:
    d = ReservationDetector()
    check("бронювання", d.detect("Можливість бронювання працівників.").state, YES)
    check("заброньовані", d.detect("Усі співробітники заброньовані.").state, YES)
    check("відстрочка+мобілізація", d.detect("Надаємо відстрочку від мобілізації.").state, YES)
    # Пастки, всі з живих описів.
    check("бронетехніка не бронь", d.detect("Розробка бронетехніки.").state, NO)
    check("бронежилет не бронь", d.detect("Виробництво бронежилетів.").state, NO)
    check("резервування мережі не бронь",
          d.detect("Коректна робота механізмів резервування.").state, NO)
    check("відстрочка платежу не бронь",
          d.detect("Працюємо з відстрочкою платежу 30 днів.").state, NO)
    # Три стани: без опису сказати нічого не можна.
    check("порожній опис = unknown", d.detect("").state, UNKNOWN)

    # Структурована ознака сильніша за текст, але 'no' від джерела —
    # це справді 'джерело каже ні', а не 'джерело мовчить'.
    check("hint=yes перебиває текст", d.detect("Звичайний опис.", "yes").state, YES)
    check("hint=no перебиває текст", d.detect("Можливість бронювання.", "no").state, NO)
    check("порожній hint не заважає", d.detect("Можливість бронювання.", "").state, YES)


def test_reservation_evidence() -> None:
    """РЕГРЕСІЯ: цитата має показувати ЗБІГ, а не початок речення.

    Описи на DOU йдуть суцільним списком без крапок, тож «речення» буває
    на кілька тисяч символів — обрізане з початку, воно не містить самого
    слова «бронювання», заради якого й показується.
    """
    long_text = ("Офіційне працевлаштування " * 30) + "Бронювання та повний супровід " + ("Оплачувані лікарняні " * 30)
    evidence = ReservationDetector().detect(long_text).evidence
    check("цитата містить збіг", "ронювання" in evidence, True)
    check("цитата обрізана з обох боків", evidence.startswith("…") and evidence.endswith("…"), True)


# ── JSON-LD ──────────────────────────────────────────────────────────────
SAMPLE_PAGE = """
<html><head>
<script type="application/ld+json">{"@type":"BreadcrumbList","itemListElement":[]}</script>
<script type="application/ld+json">{ це не JSON </script>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"JobPosting","title":"UI/UX Designer",
 "datePosted":"2026-07-27T12:06:55.358512",
 "hiringOrganization":{"@type":"Organization","name":"NDA Recruitment"},
 "jobLocation":{"@type":"Place","address":{"@type":"PostalAddress",
   "addressCountry":"Ukraine","addressLocality":["Zaporizhzhia"]}},
 "baseSalary":{"@type":"MonetaryAmount","currency":"USD",
   "value":{"maxValue":2500,"minValue":2000,"unitText":"MONTH"}},
 "category":"UI UX"}
</script>
</head></html>
"""

REMOTE_PAGE = """
<script type="application/ld+json">
{"@type":"JobPosting","title":"Senior UI/UX designer","jobLocation":null,
 "jobLocationType":"TELECOMMUTE",
 "applicantLocationRequirements":{"@type":"AdministrativeArea",
   "address":{"@type":"PostalAddress","addressRegion":"Europe"}}}
</script>
"""


def test_jsonld() -> None:
    fields = jsonld_jobposting.from_html(SAMPLE_PAGE)
    check("JSON-LD знайдено попри зіпсований сусідній блок", fields is not None, True)
    check("компанія", fields.company, "NDA Recruitment")
    check("локація: місто попереду країни", fields.location, "Zaporizhzhia, Ukraine")
    check("зарплата", fields.salary, "$2000–2500 / міс")
    check("категорія в tags", "UI UX" in fields.tags, True)
    # РЕГРЕСІЯ: наївну дату НЕ інтерпретуємо, а відкидаємо. Djinni віддає
    # той самий момент двічі — у RSS з поясом (+0300) і в JSON-LD без нього.
    # «Вважати UTC» зсунуло б 81% стрічки на три години, і не впало б:
    # дати лишились би валідними, просто «щойно» означало б «три години тому».
    check("наївну дату відкинуто, не вгадано", fields.posted_at, None)
    check("відкидання пораховано", fields.naive_dates_dropped >= 1, True)


def test_jsonld_aware_date_kept() -> None:
    """Дата З поясом лишається як є — відкидаємо лише неоднозначну."""
    page = ('<script type="application/ld+json">'
            '{"@type":"JobPosting","title":"X",'
            '"datePosted":"2026-07-27T13:02:06+03:00"}</script>')
    fields = jsonld_jobposting.from_html(page)
    check("tz-aware дата збережена", fields.posted_at.isoformat(), "2026-07-27T13:02:06+03:00")
    check("нічого не відкинуто", fields.naive_dates_dropped, 0)


def test_experience_months() -> None:
    """Досвід лежить у ВКЛАДЕНОМУ полі — загальний `_text()` його не бачив.

    Саме через це поле здавалось реалізованим, а було порожнім на всіх
    вакансіях без винятку.
    """
    page = ('<script type="application/ld+json">'
            '{"@type":"JobPosting","title":"X","employmentType":"PART_TIME",'
            '"experienceRequirements":{"@type":"OccupationalExperienceRequirements",'
            '"monthsOfExperience":36.0}}</script>')
    fields = jsonld_jobposting.from_html(page)
    check("місяці досвіду прочитані", fields.experience_months, 36)
    check("тип зайнятості прочитаний", fields.employment_type, "PART_TIME")

    # Число рядком — не екзотика, а найімовірніша регресія: JSON-LD генерує
    # шаблонізатор, і `36.0` після зміни на боці сайту стає `"36"`. Розмітка
    # лишається валідною, нічого не падає — просто всі чипи досвіду зникають.
    from jobfeed.sources.jsonld_jobposting import _experience_months as months_of
    check("число рядком читається", months_of({"experienceRequirements":
          {"monthsOfExperience": "24"}}), (24, False))
    check("текстовий досвід не вгадуємо", months_of({"experienceRequirements":
          {"monthsOfExperience": "3+ years"}}), (None, True))
    # Явний null — джерело каже «немає», це не зміна формату й не привід
    # світити попередження.
    check("null не є регресією", months_of({"experienceRequirements":
          {"monthsOfExperience": None}}), (None, False))
    check("структури немає — тиша", months_of({}), (None, False))
    check("рядок замість обʼєкта — тиша", months_of({"experienceRequirements": "3+ years"}),
          (None, False))

    from jobfeed.digest import employment_label, experience_label
    check("круглі — у роки", experience_label(36), "від 3 років")
    check("родовий відмінок для одного", experience_label(12), "від 1 року")
    # Некруглі лишаються місяцями: «від 1 року» для 18 міс губить половину.
    check("некруглі — у місяцях", experience_label(18), "від 18 міс")
    check("немає даних — немає підпису", experience_label(None), "")
    # Full-time на 93% карток — шум, який привчає око пропускати те місце.
    check("full-time не показуємо", employment_label("FULL_TIME"), "")
    check("відхилення показуємо", employment_label("PART_TIME"), "часткова зайнятість")


def test_jsonld_remote() -> None:
    """РЕГРЕСІЯ: без TELECOMMUTE половина карток Djinni лишалась без локації."""
    fields = jsonld_jobposting.from_html(REMOTE_PAGE)
    check("віддалена робота з jobLocationType", fields.location, "віддалено, Europe")


def test_jsonld_absent() -> None:
    check("сторінка без JobPosting", jsonld_jobposting.from_html("<html></html>"), None)


# ── Білий список параметрів ──────────────────────────────────────────────
def test_feed_params() -> None:
    """Три джерела з чотирьох мовчки ігнорують невідомий параметр."""
    from jobfeed.sources.djinni import DjinniSource
    from jobfeed.sources.dou import DouSource
    from jobfeed.sources.happymonday import HappyMondaySource

    bad = [
        (DouSource, {"company": "fuib"}, "невідомий ключ"),
        (DouSource, {"category": "Дизайн"}, "кирилиця в category"),
        (DouSource, {"exp": "1-3"}, "фід не звужений"),
        (DjinniSource, {"primary_keyword": "UI UX", "editorial": "ZZZ"}, "невалідне значення"),
        (HappyMondaySource, {"job_category": "dyzajn"}, "параметр, який сервер ігнорує"),
    ]
    for cls, feed, label in bad:
        try:
            cls(feeds=[feed])
            failures.append(f"білий список пропустив {label}: {feed}")
        except FeedConfigError:
            pass

    for cls, feed in (
        (DouSource, {"category": "Design"}),
        (DjinniSource, {"primary_keyword": "UI UX", "editorial": "reservation"}),
        (HappyMondaySource, {"categories": "dyzajn"}),
    ):
        try:
            cls(feeds=[feed])
        except FeedConfigError as exc:
            failures.append(f"білий список відкинув валідний фід {feed}: {exc}")


# ── Дедуп і перехресні збіги ─────────────────────────────────────────────
def test_ids_and_keys() -> None:
    a = Vacancy(source="dou", title="X", company="", location="", url="u", source_uid="123")
    b = Vacancy(source="djinni", title="X", company="", location="", url="u", source_uid="123")
    check("id детермінований", a.id, a.id)
    check("однаковий uid у різних джерелах не колізує", a.id != b.id, True)

    # Ключ перехресного збігу нечутливий до регістру й пунктуації, але
    # НЕ використовується для приховування — лише для позначки.
    x = Vacancy(source="dou", title="UX/UI Designer", company="SKELAR", location="", url="")
    y = Vacancy(source="djinni", title="ux ui designer", company="skelar", location="", url="")
    check("ключ збігається попри регістр і слеш", match_key(x), match_key(y))


def test_absence_tracking() -> None:
    """«Зникла з фіда» → ймовірно закрита. Перевіряються всі запобіжники."""
    import tempfile
    from datetime import timedelta

    from jobfeed.store import Store

    now = datetime.now(timezone.utc)
    def make(uid: str, days_ago: int) -> Vacancy:
        return Vacancy(source="djinni", title=f"V{uid}", company="C", location="",
                       url=f"u{uid}", source_uid=uid, feed="UI UX",
                       posted_at=now - timedelta(days=days_ago))

    # ignore_cleanup_errors: на Windows файл БД лишається зайнятим, якщо
    # перевірка впала до close() — падати має тест, а не прибирання.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = Store(Path(tmp) / "t.db")
        try:
            recent, gone, ancient = make("1", 1), make("2", 2), make("3", 40)
            store.remember([recent, gone, ancient])
            edge = (now - timedelta(days=3)).isoformat()
            present = {recent.id, ancient.id}

            # Прохід 1: `gone` немає у фіді — це лише ПІДОЗРА, ще не сигнал.
            check("один промах ще не сигнал",
                  store.mark_absence("djinni", "UI UX", present, edge), 0)

            # Прохід 2: другий промах поспіль — тепер сигнал.
            check("два промахи поспіль — сигнал",
                  store.mark_absence("djinni", "UI UX", present, edge), 1)

            by_id = {v.id: v for v, _ in store.recent(days=90)}
            check("зникла має streak 2", by_id[gone.id].missing_streak, 2)
            # Запобіжник: старіша за покриття фіда — не чіпаємо взагалі.
            check("старіша за край вікна не рахується", by_id[ancient.id].missing_streak, 0)
            check("присутня у фіді має 0", by_id[recent.id].missing_streak, 0)

            # Сусідній фід того самого джерела не чіпає чужих записів.
            store.mark_absence("djinni", "Graphic Design", set(), edge)
            by_id = {v.id: v for v, _ in store.recent(days=90)}
            check("інший фід не чіпає чужі записи", by_id[recent.id].missing_streak, 0)

            # Повернулась у фід — підозра знімається повністю, а не зменшується.
            store.mark_absence("djinni", "UI UX", present | {gone.id}, edge)
            by_id = {v.id: v for v, _ in store.recent(days=90)}
            check("повернення обнуляє лічильник", by_id[gone.id].missing_streak, 0)
        finally:
            store.close()


def test_absence_recategorized() -> None:
    """РЕГРЕСІЯ: вакансія переїхала в інший фід — вона ЖИВА, не закрита.

    Роботодавець міняє категорію, вакансія зникає зі свого фіда назавжди й
    зʼявляється в сусідньому. У БД `feed` заморожений на першому (рядок
    пишеться раз), тому перевірка присутності лише в межах свого фіда
    позначила б живу вакансію закритою вже за два прогони.
    """
    import tempfile
    from datetime import timedelta

    from jobfeed.store import Store

    now = datetime.now(timezone.utc)
    moved = Vacancy(source="djinni", title="Moved", company="C", location="", url="u1",
                    source_uid="1", feed="Design", posted_at=now - timedelta(days=1))
    stays = Vacancy(source="djinni", title="Stays", company="C", location="", url="u2",
                    source_uid="2", feed="Design", posted_at=now - timedelta(days=1))
    edge = (now - timedelta(days=3)).isoformat()

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = Store(Path(tmp) / "t.db")
        try:
            store.remember([moved, stays])
            # Три прогони: `moved` є у фіді Graphic Design, свого фіда вона
            # покинула. Присутність рахується по ВСІХ фідах джерела.
            for _ in range(3):
                everywhere = {moved.id, stays.id}
                store.mark_absence("djinni", "Design", everywhere, edge)
                store.mark_absence("djinni", "Graphic Design", everywhere, edge)
            by_id = {v.id: v for v, _ in store.recent(days=90)}
            check("переїхала в інший фід — лишається живою", by_id[moved.id].missing_streak, 0)

            # А ось справжнє зникнення з джерела цілком — сигнал спрацьовує.
            store.mark_absence("djinni", "Design", {stays.id}, edge)
            store.mark_absence("djinni", "Design", {stays.id}, edge)
            by_id = {v.id: v for v, _ in store.recent(days=90)}
            check("зникла з усіх фідів — позначена", by_id[moved.id].missing_streak, 2)
        finally:
            store.close()


def test_feed_label_contract() -> None:
    """Джерело в сигналі зникнення мусить мітити записи — інакше падаємо.

    Без мітки записи зіллються в один псевдофід, вікно порахується по
    обʼєднанню різних фідів, і сигнал працюватиме на вигаданій величині,
    нічого при цьому не зламавши. Раніше від цього беріг випадковий збіг:
    єдине джерело без мітки виявилось єдиним інкрементальним.
    """
    from jobfeed.pipeline import _require_feed_labels
    from jobfeed.sources.base import AdapterContractError

    class Src:
        name = "test"
        scans_full_feed = True

    class Incremental:
        name = "test"
        scans_full_feed = False

    unlabelled = [Vacancy(source="test", title="A", company="", location="", url="")]
    labelled = [Vacancy(source="test", title="A", company="", location="", url="",
                        feed="UI UX")]

    try:
        _require_feed_labels(Src(), unlabelled)
        failures.append("запобіжник пропустив запис без мітки фіда")
    except AdapterContractError:
        pass

    # Мітка є — проходить.
    _require_feed_labels(Src(), labelled)
    # Інкрементальне джерело в сигналі не бере участі, з нього мітки не вимагаємо.
    _require_feed_labels(Incremental(), unlabelled)


def test_provides_declaration() -> None:
    """Знаменник заповненості спирається на ОГОЛОШЕННЯ, не на спостереження.

    Виведений із побачених значень знаменник зникає разом із полем: джерело
    перестало віддавати досвід — і рядок став би «0 з 0» замість
    «0 з 163 djinni». Тобто показник сліпне рівно тоді, коли потрібен.
    """
    from jobfeed.sources import PROVIDES

    check("djinni оголошує досвід", "experience_months" in PROVIDES["djinni"], True)
    # DOU досвіду не дає — і не мусить потрапляти у знаменник цього поля.
    check("dou досвіду не оголошує", "experience_months" in PROVIDES["dou"], False)
    # У HappyMonday зарплати немає взагалі: інакше знаменник рахував би
    # вакансії, які ніколи не могли її мати.
    check("happymonday зарплати не оголошує", "salary" in PROVIDES["happymonday"], False)
    check("компанію оголошують усі",
          all("company" in fields for fields in PROVIDES.values()), True)

    # Оголошені назви мусять існувати в моделі — інакше знаменник мовчки
    # порахує нуль джерел і рядок зникне.
    fields = {f.name for f in dataclasses.fields(Vacancy)}
    for source, declared in PROVIDES.items():
        unknown = declared - fields
        if unknown:
            failures.append(f"{source} оголошує неіснуючі поля: {sorted(unknown)}")


def test_enrichment_never_erases() -> None:
    """Збагачення заповнює й уточнює, але НІКОЛИ не стирає.

    Порожнє значення в пізнішому заборі не є твердженням, що значення
    зникло. Без цього правила збагачення, яке цього разу не віддало досвід,
    затирало б реальні 36 на NULL — тихо, за кілька прогонів, без помилки.
    """
    import tempfile

    from jobfeed.store import Store

    full = Vacancy(source="djinni", title="X", company="Acme", location="Kyiv",
                   url="u", source_uid="1", feed="UI UX", salary="$100",
                   experience_months=36, employment_type="FULL_TIME")
    nothing = Vacancy(source="djinni", title="X", company="", location="", url="u",
                      source_uid="1", feed="UI UX")

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = Store(Path(tmp) / "t.db")
        try:
            store.remember([full])
            store.update([nothing])
            kept = [v for v, _ in store.recent(days=90)][0]
            check("компанію не затерто", kept.company, "Acme")
            check("локацію не затерто", kept.location, "Kyiv")
            check("зарплату не затерто", kept.salary, "$100")
            check("досвід не затерто", kept.experience_months, 36)
            check("зайнятість не затерто", kept.employment_type, "FULL_TIME")

            # Але нове значення записується.
            store.update([Vacancy(source="djinni", title="X", company="Acme Inc",
                                  location="", url="u", source_uid="1", feed="UI UX",
                                  experience_months=24)])
            kept = [v for v, _ in store.recent(days=90)][0]
            check("нове значення записалось", kept.company, "Acme Inc")
            check("порожнє поруч не зашкодило", kept.location, "Kyiv")

            # Нуль місяців — це «без досвіду», реальне значення, не порожнеча.
            store.update([Vacancy(source="djinni", title="X", company="", location="",
                                  url="u", source_uid="1", feed="UI UX",
                                  experience_months=0)])
            kept = [v for v, _ in store.recent(days=90)][0]
            check("нуль місяців записується", kept.experience_months, 0)
        finally:
            store.close()

    # Правило мусить діяти на ВСІ поля збагачення разом, а не бути переліком
    # винятків: інакше наступне поле додадуть повз нього.
    check("перелік полів збагачення один", "experience_months" in Store.ENRICHED, True)


def test_direction_from_description() -> None:
    """Напрям з ОПИСУ — свідомий виняток, і він мусить мати стоп-контекст.

    Правило «напрям за назвою посади» лишається чинним для дисциплін: слова
    «дизайн», «бренд», «продукт» є в описі майже кожної вакансії. Але
    інструмент — не дисципліна: `Webflow` у заголовку 0 разів із 270, в
    описі 14.

    Стоп-контекст існує, бо дві з тих 14 — це агенція, що описує СЕБЕ:
    «a Webflow Experts partner, holding a 5.0 rating». Слово присутнє, але
    не про вакансію — той самий клас, що `Advanced Figma` в рівні мови.
    """
    from jobfeed.directions import DirectionClassifier
    from jobfeed.models import Vacancy

    config = {
        "UX/UI і веб": {"keys": ["ux"]},
        "Webflow-розробка": {
            "keys": ["webflow"],
            "match": "description",
            "not_near": ["partner", "certified", "rating"],
        },
    }
    classifier = DirectionClassifier.from_config(config)

    def dirs(title, description=""):
        return classifier.classify(Vacancy(source="t", title=title, company="c",
                                           location="", url=title + description,
                                           description=description))

    check("вимога в описі — потрапляє",
          "Webflow-розробка" in dirs("Graphic designer", "вмієш Webflow — базові навички"),
          True)
    check("«буде плюсом» теж потрапляє",
          "Webflow-розробка" in dirs("Designer", "experience with a CMS like Webflow is a plus"),
          True)

    # 🔴 Самоопис роботодавця — НЕ вимога до кандидата.
    check("агенція описує себе — не потрапляє",
          "Webflow-розробка" in dirs(
              "Product Designer",
              "Nielsen Norman Group UX certified, and a Webflow Experts partner, "
              "holding a 5.0 rating on Clutch"),
          False)

    # Заголовок для цієї групи не дивимось узагалі — інакше правило
    # «з опису» було б наполовину правилом «із заголовка».
    check("слово в заголовку без опису не рахується",
          "Webflow-розробка" in dirs("Webflow Developer", ""), False)

    # Решта груп працюють як раніше — по заголовку.
    check("звичайна група лишилась по заголовку",
          "UX/UI і веб" in dirs("UX Designer", ""), True)

    # Цитата-доказ є саме для групи з опису.
    vacancy = Vacancy(source="t", title="Designer", company="c", location="", url="u",
                      description="Hands-on experience with Webflow or Framer")
    check("цитата поруч із висновком",
          "Webflow" in classifier.evidence(vacancy).get("Webflow-розробка", ""), True)


def test_company_groups() -> None:
    """У переліку компанії ДВОХ природ, і різниця мусить бути видима.

    * **під наглядом** — читаємо їхній фід окремо, побачимо нову вакансію
      того ж дня. Показуються НАВІТЬ із нулем: «нуль сьогодні не привід не
      стежити» — правило власника, повторене пʼять разів;
    * **трапилась** — роботодавець потрапив у стрічку сам, через
      категорійний фід, і завтра може зникнути непоміченим.

    Плюс псевдоніми: той самий банк зветься `ПриватБанк` у фіді DOU й
    `PrivatBank` у вакансії з Djinni. Без них у переліку два рядки на одну
    компанію.
    """
    from jobfeed.digest import company_groups, watched_companies
    from jobfeed.health import SourceHealth
    from jobfeed.models import Entry, Vacancy

    def entry(company, source):
        return Entry(vacancy=Vacancy(source=source, title="Designer",
                                     company=company, location="", url=company + source))

    health = {"dou_companies": SourceHealth(name="dou_companies", feeds={
        "privatbank": {"title": "ПриватБанк", "count": 21},
        "kredobank": {"title": "АТ «КРЕДОБАНК»", "count": 0},
        "squad": {"title": "SQUAD", "count": 22},
    })}
    watched = watched_companies(health, {"privatbank": "PrivatBank"})

    check("назва з фіда, а не слаг", "АТ «КРЕДОБАНК»" in watched, True)
    # 🔴 Псевдонім переважає назву з фіда — інакше буде два рядки на банк.
    check("псевдонім переміг", "PrivatBank" in watched, True)
    check("сирої назви немає", "ПриватБанк" in watched, False)

    entries = [
        entry("PrivatBank", "djinni"),      # під наглядом І у стрічці
        entry("Seranova", "djinni"),        # трапилась
        entry("Ukraїner", "happymonday"),   # трапилась, поза IT-бордами
    ]
    groups = dict((key, names) for key, _, names in
                  company_groups(entries, watched, ["privatbank", "kredobank"]))

    banks = dict(groups["bank"])
    check("банк без вакансій усе одно в групі", "АТ «КРЕДОБАНК»" in banks, True)
    check("він позначений як під наглядом", banks["АТ «КРЕДОБАНК»"], True)
    check("банків у групі", len(banks), 2)

    on_it = dict(groups["onit"])
    check("випадкова компанія теж у переліку", "Seranova" in on_it, True)
    check("і НЕ позначена наглядом", on_it["Seranova"], False)
    check("компанія під наглядом без вакансій — у IT-бордах",
          on_it.get("АТ «КРЕДОБАНК»"), True)

    check("поза IT-бордами лише HappyMonday",
          [n for n, _ in groups["offit"]], ["Ukraїner"])
    # Топ-50 — слаги під наглядом, яких немає в переліку банків.
    check("Топ-50 без банків", [n for n, _ in groups["top50"]], ["SQUAD"])


def test_budget_from_text() -> None:
    """Бюджет з опису — лише коли поруч сказано, що це винагорода.

    Заміряно на 243 живих вакансіях: гроші в описі має 14, справжня зарплата
    серед них — 6. Тобто наївний пошук грошей помиляється більш ніж у
    половині випадків, і кожен рядок нижче взято з реального опису.
    """
    from jobfeed.budget import detect

    def money(text, salary=""):
        return detect(text, salary)[0]

    # Справжні — контекст поруч.
    check("compensation", money("Location: Remote Compensation: $1,800-2,500 About"),
          "$1,800-2,500")
    check("українською", money("Ми пропонуємо: Заробітна плата: 40 000 грн — 45 000 грн"),
          "40 000 грн — 45 000 грн")

    # 🔴 Діапазон не можна обрізати. Перша версія віддавала верхню межу —
    # для зарплати це завищення пропозиції, гірше за обрізання рівня мови.
    check("діапазон цілим",
          money("time zone Salary: 1,500-2,000 USD per month Payment"),
          "1,500-2,000 USD per month")

    # Періодичність — частина твердження: $1,500 і $1,500/month різні речі.
    check("періодичність збережена",
          money("What We Offer Salary: $1,500/month — open to discussing"),
          "$1,500/month")

    # Хибні — сума є, але вона не про зарплату.
    for text, why in [
        ("clients have raised $500M+ in funding", "інвестиції"),
        ("Referral bonus — $200 for a hire", "бонус"),
        ("Великі (>$1,000,000 monthly) маркетинг бюджети", "рекламний бюджет"),
        ("within the $1.19 trillion e-commerce industry", "розмір ринку"),
        ("scaled from $2M to $50M in ARR", "виторг"),
        ("courses/certifications (200 EUR per year)", "пільга"),
    ]:
        check(f"не зарплата: {why}", money(text), "")

    # Сума без слова-контексту — теж ні: сама сума не є доказом.
    check("сума без контексту", money("We are a team of 40 people in $2,000 offices"), "")

    # Поле джерела надійніше за наш розбір — не дублюємо.
    check("є поле salary — не лізем у текст",
          money("Compensation: $1,800-2,500", salary="$2,000"), "")


def test_english_level() -> None:
    """Рівень англійської — лише коли він справді про англійську.

    Заміряно на 224 живих описах: 102 згадують мову, рівень поруч у 64.
    Пошук по всьому опису дав би 79 — тобто 15 вакансій отримали б рівень
    із чужого контексту: `Advanced Figma`, `Proficiency in Figma`,
    `AI-native SaaS`. Кожен випадок нижче — з реальних описів.
    """
    from jobfeed.english import detect

    def level(text):
        return detect(text)[0]

    # Далеко один від одного — не зараховуємо.
    check("далекий рівень не чіпляється",
          level("Advanced Figma expertise. " + "x" * 120 + " English is a must."), "")
    # Межа пункту списку всередині вікна.
    check("межа пункту розриває звʼязок",
          level("Strong English skills are mandatory ○ Proficiency in Figma"), "")
    # Слово зі шкали витісняє загальне.
    check("шкала важливіша за загальне слово",
          level("English proficiency at B1 level or higher"), "B1+")
    check("те саме зі словесною шкалою",
          level("English proficiency at Intermediate level or higher"), "Intermediate+")
    # Загальне слово впритул — законний рівень.
    check("proficient in English зараховуємо",
          level("highly proficient in English (written and spoken)"), "Proficient")

    # 🔴 Близькість — НЕ прикріплення. Знайдено researcher-ом на живих даних:
    # мова стоїть за чотири символи, і будь-яка перевірка на відстань це
    # зараховує, але слово належить наступному іменнику.
    check("загальне слово при чужому іменнику не рахується",
          level("Good spoken and written English for fluent communication."), "Good")
    check("службові слова між ними не заважають",
          level("Fluent spoken English is a must"), "Fluent")
    check("порядок «слово — мова» теж працює",
          level("highly proficient in English"), "Proficient")

    # Діапазон лишається діапазоном: звести до нижньої межі означає відкинути
    # вакансію, яка підходить.
    check("діапазон не зводимо до точки",
          level("Володіння англійською на рівні B1-B2"), "B1–B2")
    check("«or higher» стає видимим плюсом",
          level("English: Upper-Intermediate (B2) or higher"), "Upper-Intermediate+")
    # Українською «мінімум» стоїть ПЕРЕД рівнем.
    check("мінімум перед рівнем теж ловиться",
          level("Рівень англійської мови — мінімум розмовний B1"), "B1+")

    # Дві шкали не зводимо в одну: показуємо як написано.
    check("CEFR лишається CEFR", level("English: B2 level"), "B2")

    # Немає рівня — немає чипа. Це НЕ означає «англійська не потрібна».
    check("згадка без рівня нічого не дає", level("English is a must"), "")
    check("порожній опис", level(""), "")


def test_mark_field_absence() -> None:
    """Відсутнє поле означає «не чіпай», а не «зніми».

    `bool(data.get("applied"))` перетворює одруківку в назві поля на команду
    видалити позначку — і повертає `ok: true`. Тобто нерозпізнаний запит
    робить ПРОТИЛЕЖНЕ до задуманого й звітує успіх. Зловмисник не потрібен:
    досить перейменованого поля або старого клієнта після оновлення API.

    Позначки — єдине в проєкті, чого не можна відновити з джерел.
    """
    import pathlib
    import tempfile
    from jobfeed.server import App

    with tempfile.TemporaryDirectory() as folder:
        app = App.__new__(App)          # без читання конфіга — потрібна лише БД
        import threading
        app._local = threading.local()
        from jobfeed.store import Store
        app._local.store = Store(pathlib.Path(folder) / "t.db")

        app.apply_mark({"id": "x", "applied": True, "skipped": False})
        check("позначку поставлено", app.marks()["x"]["applied"], True)

        # Одруківка в назві поля не сміє нічого стерти.
        body, status = app.apply_mark({"id": "x", "aplied": True})
        check("одруківка відхилена", status, 400)
        check("позначка на місці", app.marks()["x"]["applied"], True)

        # Часткове оновлення чіпає тільки назване поле.
        app.apply_mark({"id": "x", "skipped": True})
        check("applied не зачеплено", app.marks()["x"]["applied"], True)
        check("skipped виставлено", app.marks()["x"]["skipped"], True)

        # Явне зняття все ще працює — інакше ми полагодили б ціною функції.
        app.apply_mark({"id": "x", "applied": False, "skipped": False})
        check("явне зняття працює", "x" in app.marks(), False)

        body, status = app.apply_mark({"applied": True})
        check("без id відхилено", status, 400)
        app._local.store.close()


def test_marks_merge() -> None:
    """Перенесення позначок ДОДАЄ і ніколи не стирає.

    Позначки — єдине в проєкті, що створює людина; все інше відновлюється
    наступним прогоном. Тому напрямок обʼєднання тут не деталь реалізації:
    «синхронізувати як у клієнта» означало б, що почищений кеш браузера
    вичищає базу, а браузер, який ніколи не бачив позначки, її видаляє.

    Те саме правило, що в `update()` для збагачення: заповнювати можна,
    стирати — ні.
    """
    import pathlib
    import tempfile
    from jobfeed.store import Store

    with tempfile.TemporaryDirectory() as folder:
        with Store(pathlib.Path(folder) / "t.db") as store:
            store.set_mark("a", applied=True, skipped=False)
            store.set_mark("b", applied=False, skipped=True)

            # Порожній клієнт («я почистив дані сайту») не сміє нічого забрати.
            added, same = store.import_marks({})
            check("порожній імпорт нічого не стер", len(store.marks()), 2)
            check("нічого не додано", added, 0)

            # Інший браузер приносить своє — додається, чуже не чіпається.
            added, same = store.import_marks({"c": {"applied": True},
                                              "a": {"applied": False, "skipped": True}})
            check("нове додано", added, 1)
            check("наявне не перезаписано", store.marks()["a"],
                  {"applied": True, "skipped": False})
            check("разом", len(store.marks()), 3)

            # Знята позначка зникає, а не лишається порожнім рядком.
            store.set_mark("a", applied=False, skipped=False)
            check("зняте прибрано", "a" in store.marks(), False)


def test_company_slugs() -> None:
    """Слаг мусить бути слагом — сюди підуть значення з форми додавання."""
    from jobfeed.sources.dou_companies import SlugError, check_slug

    check("нормальний слаг", check_slug("Genesis-Technology"), "genesis-technology")
    for bad in ("", "../../etc", "https://jobs.dou.ua/vacancies/squad/feeds/",
                "squad?x=1", "squad/../dou"):
        try:
            check_slug(bad)
            check(f"мусив упасти: {bad!r}", True, False)
        except SlugError:
            check(f"відкинуто: {bad!r}", True, True)


def test_filter_before_classification() -> None:
    """Класифікація — ТІЛЬКИ після основного фільтра. Порядок, не деталь.

    Ключ `developer` створювався для гібридів дизайн+код і в основній стрічці
    поводиться правильно, бо туди й так потрапляє лише дизайн. На сирому фіді
    компанії він перетворює «Розробка і код» на «всі розробники»: заміряно
    200 → 20 «профільних» без `include_any`, з них 17 Java/PHP/BI/Front-end.
    З фільтром — 6.

    Власник побачив би 17 Java-розробників із чипом «Розробка і код», і це
    виглядало б як робота таксономії, а не як помилка. Тест фіксує саме
    порядок, бо переставити його можна непомітно й нічого не зламати.
    """
    from jobfeed.directions import DirectionClassifier
    from jobfeed.pipeline import screen

    config = {
        "filter": {"include_any": ["designer"], "exclude_any": []},
        "directions": {"Розробка і код": {"keys": ["developer"]}},
    }
    raw = [
        Vacancy(source="t", title="Java Developer", company="", location="", url="a"),
        Vacancy(source="t", title="PHP Developer", company="", location="", url="b"),
        Vacancy(source="t", title="Product Designer", company="", location="", url="c"),
    ]
    kept, _ = screen(config, raw)
    check("фільтр лишає лише дизайн", [v.title for v in kept], ["Product Designer"])

    classifier = DirectionClassifier.from_config(config["directions"])
    # Так виглядав би результат, якби класифікували ДО фільтра.
    before = [v.title for v in raw if "Розробка і код" in classifier.classify(v)]
    after = [v.title for v in kept if "Розробка і код" in classifier.classify(v)]
    check("до фільтра розробники потрапляють у групу", len(before), 2)
    check("після фільтра — жодного", after, [])


def test_source_health() -> None:
    """Стан джерела мусить пережити невдалий забір, не збрехавши про історію.

    Ловить три різні способи збрехати здоровим виглядом:
    1. невдала спроба перезаписує «востаннє вдало» — і мертве джерело
       виглядає щойно оновленим;
    2. нуль в інкрементального джерела читається як порожній фід — і
       HappyMonday світиться попередженням у кожному тихому прогоні;
    3. «немає запису в памʼяті» друкується як «джерело не працює» — твердження
       про нашу історію видається за твердження про джерело.
    """
    from jobfeed.health import SourceHealth, merge
    from jobfeed.digest import _health_line

    was = {"djinni": SourceHealth(name="djinni", state="ok", last_ok="2026-07-27T10:00:00+00:00",
                                  last_ok_count=167)}
    failed = merge(was, SourceHealth(name="djinni", state="failed", error="ConnectError"))
    check("невдача не стирає історію", failed.last_ok, "2026-07-27T10:00:00+00:00")
    check("скільки було тоді", failed.last_ok_count, 167)
    check("стан — поточний", failed.state, "failed")

    quiet = merge({}, SourceHealth(name="happymonday", state="empty", full_scan=False))
    check("нуль в інкрементального — не збій", quiet.state, "quiet")
    check("нуль у повного фіда — окремий стан",
          merge({}, SourceHealth(name="dou", state="empty")).state, "empty")

    # Рядок про порожню памʼять не має містити тверджень про джерело.
    css, line = _health_line(SourceHealth(name="new"))
    check("не приписуємо джерелу того, чого не міряли",
          "не забиралось" in line or "не працює" in line, False)

    # Причина мусить бути В ТЕКСТІ, а не лише в кольорі: колір не каже,
    # чекати чи лізти в конфіг.
    css, line = _health_line(failed)
    check("причина написана словами", "ConnectError" in line, True)

    # Пропущений забір ≠ порожній результат. Сьогодні вакансії такого джерела
    # береже ще й умова «записів з датами 0», тобто наслідок був би той самий
    # і без цієї гілки. Але вона називає ПРИЧИНУ: «не дивились» замість
    # «дивились і не знайшли». Ми вже двічі обпікались на діагностиці, що
    # називає результат замість причини, і на захисті, що тримався на збігу.
    waiting = merge({"x": SourceHealth(name="x", state="ok", last_ok="2026-07-27T10:00:00+00:00",
                                       last_ok_count=200)},
                    SourceHealth(name="x", state="waiting", wait_hours=17))
    check("очікування не стирає історію", waiting.last_ok_count, 200)
    css, line = _health_line(waiting)
    check("не читається як збій", css, "ok")
    check("видно, коли наступний забір", "наступний забір" in line, True)


def test_sink_criticality() -> None:
    """Дайджест критичний, службові виходи — ні.

    Прогін із мертвим рендером друкує повне здорове зведення й відрізняється
    від успішного лише ВІДСУТНІСТЮ рядка «дайджест:». Відсутнього рядка око
    не ловить — тому критичний збій мусить мати ПРИСУТНІЙ сигнал: окремий
    блок і ненульовий код виходу.
    """
    from jobfeed.sinks import ConsoleSink, HtmlDigestSink, JsonDumpSink

    check("дайджест критичний", HtmlDigestSink.critical, True)
    # Дамп і консоль власник не читає — їхнє падіння лишається рядком.
    check("дамп не критичний", JsonDumpSink.critical, False)
    check("консоль не критична", ConsoleSink.critical, False)


# ── work.ua: розбір списку ───────────────────────────────────────────────
# Кожна перевірка нижче — справжня помилка, спіймана під час написання
# адаптера, а не гіпотетичний випадок.
def test_workua_list() -> None:
    from jobfeed.sources import work_ua

    page = (SAMPLES / "workua_list.html").read_text(encoding="utf-8")
    rows = work_ua.parse_list(page)
    check("work.ua: карток", len(rows), 14)

    # 🔴 Ключ саме за uid, а не за назвою: назва «UI/UX-дизайнер» на цій
    # сторінці зустрічається чотири рази, і словник за назвою мовчки лишив би
    # останню картку — тобто тест перевіряв би не те, що написано.
    by_uid = {r["uid"]: r for r in rows}
    by_title = {r["title"]: r for r in rows}

    # 🔴 Компанія і зарплата лежать в ОДНАКОВИХ `<span class="strong-600">`,
    # і на картках із зарплатою вона стоїть першою. Перша версія розбору
    # брала «перший strong-600» — і власник побачив би «27 000 – 127 000 грн»
    # як назву роботодавця.
    money = by_uid["7664551"]
    check("work.ua: зарплата не в компанії", "грн" in money["company"], False)
    check("work.ua: зарплата на місці", money["salary"], "27 000 – 127 000 грн")

    # Місто — ОСТАННІЙ безкласовий span, і не сама компанія: на частині
    # карток назву фірми загорнуто в такий самий span, і «перший збіг»
    # повертав `Improvs` як місто.
    improvs = by_title["Senior UI/UX Design Lead"]
    check("work.ua: місто не є компанією", improvs["location"], "Дистанційно")

    # Блок компанії різався фіксованими 900 символами — і на картці зі
    # значками «перевірена» та «VIP» (1042 символи) місто мовчки зникало.
    check("work.ua: місто за довгим блоком значків",
          by_title["Провідний фахівець, фахівчиня з UI/UX та вебдизайну"]["location"],
          "Київ")

    check("work.ua: без компанії", sum(1 for r in rows if not r["company"]), 0)
    check("work.ua: без міста", sum(1 for r in rows if not r["location"]), 0)
    check("work.ua: без дати", sum(1 for r in rows if not r["posted_at"]), 0)
    check("work.ua: є наступна сторінка", work_ua.has_next_page(page, 1), True)
    check("work.ua: після останньої немає", work_ua.has_next_page(page, 9), False)


def test_workua_no_match_is_not_empty() -> None:
    """Запит, який нічого не знайшов, віддає 200 OK і ЧУЖІ вакансії.

    🔴 Головна пастка цього джерела. Перевірка «200 OK і нуль карток =
    поломка» тут не спрацювала б ніколи: сторінка-нісенітниця приносить
    доцентів і паркувальника. Одруківка в конфізі тихо налила б їх у
    стрічку дизайнера.
    """
    from jobfeed.sources import work_ua

    page = (SAMPLES / "workua_no_match.html").read_text(encoding="utf-8")
    # Картки на ній справді є — саме тому нуль карток не був би сигналом.
    check("work.ua: на порожній сторінці є картки",
          len(work_ua._CARD.findall(page)) > 0, True)
    try:
        work_ua.parse_list(page)
        failures.append("work.ua: сторінка «нічого не знайдено» розібралась як звичайна")
    except work_ua.NoMatchPage:
        pass

    # ⚠️ Маркер мусить бути саме сайтовим класом. Перша версія брала фразу
    # «Вибачте, але ні.», зчитану з порожньої сторінки, — а вона стоїть і на
    # нормальній (постійний банер про хвилину мовчання).
    good = (SAMPLES / "workua_list.html").read_text(encoding="utf-8")
    check("work.ua: маркер не спрацьовує на нормальній сторінці",
          bool(work_ua._NO_MATCH.search(good)), False)


def test_workua_dates_and_queries() -> None:
    from jobfeed.sources import work_ua

    # Дата береться з машинного `<time datetime>` і має київську зону:
    # влітку +3, взимку +2. Без зони її довелось би відкинути зовсім.
    summer = work_ua.parse_posted_at("2026-08-12 14:05:02")
    winter = work_ua.parse_posted_at("2026-01-15 14:05:02")
    check("work.ua: літня дата в UTC", summer.isoformat(), "2026-08-12T11:05:02+00:00")
    check("work.ua: зимова дата в UTC", winter.isoformat(), "2026-01-15T12:05:02+00:00")
    check("work.ua: сміття замість дати", work_ua.parse_posted_at("вчора"), None)

    # Запит — шматок шляху, не URL: значення приходить із конфіга, і
    # `../../` мусить упасти тут, а не піти запитом кудись убік.
    check("work.ua: запит нормалізується",
          work_ua.check_query("/jobs-ux+ui+designer/"), "ux+ui+designer")
    for bad in ("https://www.work.ua/jobs-x/", "../../etc", "дизайнер"):
        try:
            work_ua.check_query(bad)
            failures.append(f"work.ua: запит {bad!r} прийнято, а мав упасти")
        except work_ua.QueryError:
            pass


def test_workua_silent_breakage_is_loud() -> None:
    """200 OK і жодної розібраної картки = ПОМИЛКА джерела, не «сьогодні порожньо».

    Фіда в work.ua немає, тому це саморобний розбір розмітки — і ламається
    він тихо: сайт відповідає 200, сторінка виглядає цілою, а карток нуль.
    Ми вже двічі приймали відсутність за факт, тому тут вона мусить бути
    гучною: запит стає `failed`, а `fetch_complete` падає — інакше сигнал
    «вакансія зникла» зарахував би нашу поломку за зняття всіх вакансій.
    """
    from jobfeed.sources import work_ua

    class FakeResponse:
        status_code = 200
        # H1 є (тобто це не сторінка «нічого не знайдено»), карток немає.
        text = "<html><body><h1>Ux ui designer</h1></body></html>"

    class FakeClient:
        def get(self, url):
            return FakeResponse()

    source = work_ua.WorkUaSource(queries=["ux+ui+designer"], delay=0.0)
    got = list(source._fetch_query(FakeClient(), "ux+ui+designer", set()))
    check("work.ua: нічого не віддано", got, [])
    check("work.ua: запит позначено помилкою",
          source.feed_health["ux+ui+designer"]["state"], "failed")
    check("work.ua: забір неповний", source.fetch_complete, False)
    check("work.ua: причина названа словами",
          "розмітк" in source.feed_health["ux+ui+designer"]["error"], True)

    # А сторінка «за запитом нічого» — це НЕ помилка й не нуль-поломка.
    class EmptyResponse:
        status_code = 200
        text = (SAMPLES / "workua_no_match.html").read_text(encoding="utf-8")

    class EmptyClient:
        def get(self, url):
            return EmptyResponse()

    quiet = work_ua.WorkUaSource(queries=["ux+ui+designer"], delay=0.0)
    check("work.ua: порожній запит нічого не віддає",
          list(quiet._fetch_query(EmptyClient(), "ux+ui+designer", set())), [])
    check("work.ua: порожній запит — не помилка",
          quiet.feed_health["ux+ui+designer"]["state"], "empty")
    check("work.ua: порожній запит не рве повноту", quiet.fetch_complete, True)


def test_workua_mixed_alphabet_direction() -> None:
    """`Web-дизайнер` — латиниця плюс кирилиця в одному слові.

    Не ловився ЖОДНИМ ключем: `вебдизайн*` вимагає кириличного «веб»,
    `web design*` — латинського «design». Форма провалювалась між ними в
    «Інше», тобто ховалась із першого екрана.
    """
    import tomllib

    from jobfeed.directions import DirectionClassifier

    config = tomllib.loads(
        (Path(__file__).resolve().parent.parent / "config" / "filter.toml")
        .read_text(encoding="utf-8"))
    classifier = DirectionClassifier.from_config(config.get("directions", {}))
    for title in ("Web-дизайнер", "Web-дизайнер (B2B-, SaaS-лендинг)",
                  "Вебдизайнер", "Веб-дизайнер"):
        vacancy = Vacancy(source="work_ua", title=title, company="",
                          location="", url="")
        check(f"напрям для {title!r}", "UX/UI і веб" in classifier.classify(vacancy), True)


# ── Recruitee: ATS-API ───────────────────────────────────────────────────
def test_recruitee_published_not_created() -> None:
    """Дата картки — `published_at`, а НЕ `created_at`.

    🔴 У завданні було сказано «created_at — це дата публікації». У даних є
    обидва поля, і вони розходяться: у MacPaw «Head of PR» — на 12 днів, а
    в тестової вакансії — на ТРИ МІСЯЦІ. `created_at` — це коли чернетку
    створили в ATS; для світу вакансії тоді ще не існувало.
    """
    from jobfeed.sources import recruitee

    offer = {
        "id": 2702754,
        "title": "Senior 3D/Motion Designer",
        "company_name": "MacPaw",
        "created_at": "2026-01-13 12:35:27 UTC",
        "published_at": "2026-04-21 11:49:23 UTC",
        "city": "Kyiv", "country": "Ukraine", "remote": True,
        "status": "published", "close_at": None,
        "salary": {"min": None, "max": None, "currency": None, "period": None},
    }
    stamp = recruitee.parse_stamp(offer["published_at"])
    check("recruitee: дата публікації", stamp.isoformat(), "2026-04-21T11:49:23+00:00")
    check("recruitee: сміття замість дати", recruitee.parse_stamp("вчора"), None)
    check("recruitee: порожня дата", recruitee.parse_stamp(None), None)

    # Місто НЕ губиться через `location: "Remote job"` — воно окремим полем.
    check("recruitee: локація", recruitee.location_of(offer), "Kyiv, віддалено")
    # Порожня зарплата лишається порожньою, а не «0».
    check("recruitee: порожня зарплата", recruitee.salary_of(offer), "")
    check("recruitee: зарплата з числами",
          recruitee.salary_of({"salary": {"min": 2000, "max": 3000,
                                          "currency": "USD", "period": "month"}}),
          "2000–3000 USD month")


def test_recruitee_internal_tags_stay_inside() -> None:
    """`tags` з API — внутрішні мітки рекрутера, а не публічні ярлики.

    У MacPaw там `['@vlada', 'L - 3 months']`: імʼя відповідальної людини й
    строк найму. Показати це власникові означало б винести назовні чужу
    робочу кухню, тому в картку йде слаг компанії, а не вони.
    """
    from jobfeed.sources.recruitee import RecruiteeSource

    payload = {"offers": [{
        "id": 1, "title": "Designer", "company_name": "MacPaw",
        "published_at": "2026-08-06 14:54:59 UTC", "status": "published",
        "careers_url": "https://demo.recruitee.com/o/designer",
        "city": "Kyiv", "country": "Ukraine", "remote": True,
        "tags": ["@vlada", "L - 3 months"], "close_at": None,
    }]}

    class FakeResponse:
        status_code = 200
        url = "https://demo.recruitee.com/o/designer"

        def json(self):
            return payload

    class FakeClient:
        def get(self, url):
            return FakeResponse()

    source = RecruiteeSource(companies=["demo"])
    got = list(source._fetch_one(FakeClient(), "demo", set()))
    check("recruitee: одна вакансія", len(got), 1)
    check("recruitee: мітки — слаг, не внутрішні", got[0].tags, ("demo",))
    check("recruitee: компанія з API", got[0].company, "MacPaw")
    # Досвід лишається порожнім: `experience_code` — грейд, а не місяці.
    check("recruitee: досвід не вигаданий", got[0].experience_months, None)


def test_recruitee_all_zero_is_breakage() -> None:
    """Нуль в однієї компанії — норма. Нуль в УСІХ — поломка API.

    Різниця важлива: одна компанія просто не наймає, а всі одразу — це
    майже напевно зміна відповіді. Ми вже тричі приймали відсутність за факт.
    """
    import unittest.mock as mock

    from jobfeed.sources.recruitee import RecruiteeSource

    class EmptyResponse:
        status_code = 200
        url = ""

        def json(self):
            return {"offers": []}

    class EmptyClient:
        def get(self, url):
            return EmptyResponse()

    class FakeCtx:
        def __enter__(self):
            return EmptyClient()

        def __exit__(self, *exc):
            return False

    source = RecruiteeSource(companies=["a", "b"])
    with mock.patch("httpx.Client", return_value=FakeCtx()):
        got = list(source.fetch())
    check("recruitee: усі нулі — нічого не віддано", got, [])
    check("recruitee: усі нулі — забір неповний", source.fetch_complete, False)
    check("recruitee: усі нулі — сказано словами",
          any("жодна з" in w for w in source.warnings), True)
    # Але самі компанії лишаються видимими з нулем, а не зникають.
    check("recruitee: компанії з нулем лишились у переліку",
          sorted(source.feed_health), ["a", "b"])
    check("recruitee: нуль — не помилка компанії",
          source.feed_health["a"]["state"], "empty")


def test_recruitee_dead_links_are_skipped() -> None:
    """Компанія з мертвими посиланнями пропускається ЦІЛКОМ.

    Тенант може лишити API живим, вимкнувши хостинг карʼєрного сайту: тоді
    `careers_url` віддає чесні 200 — але на рекламній сторінці самої ATS.
    Заміряно на MacPaw. Картка, яку не відкрити, гірша за її відсутність.
    """
    from jobfeed.sources.recruitee import RecruiteeSource

    class Redirected:
        status_code = 200
        # httpx після переходів лишає тут КІНЦЕВУ адресу — уже не наш тенант.
        url = "https://recruitee.com/"

        def json(self):
            return {"offers": [{
                "id": 1, "title": "Senior 3D/Motion Designer",
                "company_name": "MacPaw", "status": "published",
                "published_at": "2026-08-06 14:54:59 UTC",
                "careers_url": "https://macpaw.recruitee.com/o/senior-3dmotion-designer",
                "close_at": None,
            }]}

    class RedirectingClient:
        def get(self, url):
            return Redirected()

    source = RecruiteeSource(companies=["macpaw"])
    got = list(source._fetch_one(RedirectingClient(), "macpaw", set()))
    check("recruitee: мертві посилання — нічого не віддано", got, [])
    check("recruitee: компанію позначено збоєм",
          source.feed_health["macpaw"]["state"], "failed")
    check("recruitee: причина названа словами",
          "не хоститься" in source.feed_health["macpaw"]["error"], True)


def test_recruitee_closed_offer_dropped() -> None:
    """`close_at` у минулому = вакансія закрита самим роботодавцем."""
    from jobfeed.sources.recruitee import RecruiteeSource

    source = RecruiteeSource(companies=["demo"])
    check("recruitee: без close_at — відкрита",
          source._is_open({"status": "published", "close_at": None}), True)
    check("recruitee: close_at у минулому — закрита",
          source._is_open({"status": "published",
                           "close_at": "2020-01-01 00:00:00 UTC"}), False)
    check("recruitee: close_at у майбутньому — відкрита",
          source._is_open({"status": "published",
                           "close_at": "2099-01-01 00:00:00 UTC"}), True)
    check("recruitee: чернетка не показується",
          source._is_open({"status": "draft", "close_at": None}), False)


def test_recruitee_slug_guard() -> None:
    from jobfeed.sources import recruitee

    check("recruitee: слаг нормалізується", recruitee.check_slug(" Clario "), "clario")
    for bad in ("https://clario.recruitee.com/", "../../etc", "кларіо", ""):
        try:
            recruitee.check_slug(bad)
            failures.append(f"recruitee: слаг {bad!r} прийнято, а мав упасти")
        except recruitee.SlugError:
            pass


# ── jobico.io: XML-фід ───────────────────────────────────────────────────
def test_jobico_feed() -> None:
    """Розбір трьох справжніх карток зі збереженого фіда."""
    from xml.etree import ElementTree as ET

    from jobfeed.sources.jobico import JobicoSource, location_of

    root = ET.parse(SAMPLES / "jobico_feed.xml").getroot()
    jobs = [{child.tag: (child.text or "") for child in node}
            for node in root.findall("job")]
    check("jobico: карток у зразку", len(jobs), 3)
    by_title = {j["title"]: j for j in jobs}

    # ⚠️ `office` мусить давати формат. Заміряно на живому фіді: офісних 32,
    # і в ДЕСЯТИ міста немає — без цієї гілки локація виходила порожньою,
    # хоча джерело формат назвало. Порожнеча читалась би як «не сказали».
    check("jobico: офіс із містом",
          location_of(by_title["Монтажник РЕА / Радіотехнік"]), "Київ, офіс")
    check("jobico: віддалено без міста",
          location_of(by_title["Middle/Senior UX/UI Designer"]), "віддалено")
    check("jobico: невідомий формат не губиться",
          location_of({"locationtype": "underwater"}), "underwater")
    check("jobico: нічого не сказали — порожньо", location_of({}), "")

    source = JobicoSource()
    designer = by_title["Middle/Senior UX/UI Designer"]
    stamp = __import__("jobfeed.sources.jobico", fromlist=["parse_stamp"]).parse_stamp(
        designer["date"])
    check("jobico: дата з фіда", stamp.isoformat(), "2026-07-07T16:03:45.218000+00:00")


def test_jobico_link_keeps_utm() -> None:
    """🔴 Посилання — ДОСЛІВНО з фіда, разом із utm.

    Атрибуція саме цим посиланням є умовою доступу (`llms.txt`), а мітка
    `utm_source=job-feed` належить джерелу: нею воно рахує віддачу від
    агрегаторів. У `models.Vacancy` поле `url` описане як «без utm» — це
    правило про НАШІ мітки, не про чужі, і саме тут воно могло б збити з
    пантелику наступного.
    """
    from xml.etree import ElementTree as ET

    from jobfeed.sources.jobico import JobicoSource

    root = ET.parse(SAMPLES / "jobico_feed.xml").getroot()
    node = root.findall("job")[1]
    link = node.findtext("link")
    check("jobico: у зразку є utm", "utm_source=job-feed" in link, True)

    # Проганяємо повний шлях збірки картки через підставну відповідь.
    class FakeResponse:
        status_code = 200
        content = (SAMPLES / "jobico_feed.xml").read_bytes()

    class FakeCtx:
        def __enter__(self):
            return type("C", (), {"get": lambda self, url: FakeResponse()})()

        def __exit__(self, *exc):
            return False

    import unittest.mock as mock

    source = JobicoSource()
    with mock.patch("httpx.Client", return_value=FakeCtx()):
        rows = list(source.fetch())
    check("jobico: зібрано карток", len(rows), 3)
    urls = {v.url for v in rows}
    check("jobico: жодне посилання не обрізане",
          all("utm_source=job-feed" in u for u in urls), True)
    check("jobico: фід здоровий", source.fetch_complete, True)


def test_jobico_empty_feed_is_breakage() -> None:
    """200 OK і жодного `<job>` — поломка, а не порожній день.

    190 вакансій не зникають разом. Правило вчетверте те саме: відсутність
    не є сигналом, поки її не відрізнено від поломки. Тут це особливо
    важить, бо ознаки «вакансія закрита» у фіді немає взагалі — зняття
    ловиться ЛИШЕ нашим сигналом зникнення, а той вимикається саме
    прапорцем `fetch_complete`.
    """
    import unittest.mock as mock

    from jobfeed.sources.jobico import JobicoSource

    for payload in (b"<?xml version='1.0'?><source><publisher>Jobico</publisher></source>",
                    b"not xml at all"):
        class FakeResponse:
            status_code = 200
            content = payload

        class FakeCtx:
            def __enter__(self):
                return type("C", (), {"get": lambda self, url: FakeResponse()})()

            def __exit__(self, *exc):
                return False

        source = JobicoSource()
        with mock.patch("httpx.Client", return_value=FakeCtx()):
            rows = list(source.fetch())
        check("jobico: нічого не віддано", rows, [])
        check("jobico: забір неповний", source.fetch_complete, False)
        check("jobico: джерело позначене збоєм",
              source.feed_health["jobico"]["state"], "failed")


def test_jobico_dates() -> None:
    from jobfeed.sources.jobico import parse_stamp

    check("jobico: ISO із Z", parse_stamp("2026-08-25T15:11:02.964Z").isoformat(),
          "2026-08-25T15:11:02.964000+00:00")
    check("jobico: сміття замість дати", parse_stamp("вчора"), None)
    check("jobico: порожня дата", parse_stamp(""), None)
    check("jobico: дата без зони стає UTC",
          parse_stamp("2026-08-25T15:11:02").isoformat(), "2026-08-25T15:11:02+00:00")


# ── boards_rss: три дошки під одним адаптером ────────────────────────────
def test_boards_titles() -> None:
    """Заголовок мусить стати посадою, а не лишитись рекламним рядком дошки."""
    from jobfeed.sources.boards_rss import split_title

    # weworkremotely: «Компанія: Посада», але посада САМА буває з двокрапкою.
    check("boards: подвійна двокрапка",
          split_title("IxDF - Interaction Design Foundation: Course Director: UX, UI",
                      "company_colon"),
          ("Course Director: UX, UI", "IxDF - Interaction Design Foundation", ""))
    check("boards: без двокрапки — уся назва посада",
          split_title("Head of Design", "company_colon"), ("Head of Design", "", ""))

    # dribbble: без чистки у стрічці стояло б «X is hiring for a position of…»
    check("boards: is hiring розібрано",
          split_title("AirOps is hiring for a position of Product Design Lead in New York",
                      "is_hiring", author="AirOps"),
          ("Product Design Lead", "AirOps", "New York"))
    # 🔴 Компанія береться з поля `author`, а не з заголовка: поле надійніше.
    check("boards: компанія з author, не із заголовка",
          split_title("Old Name is hiring for a position of Designer in Kyiv",
                      "is_hiring", author="Нова назва")[1], "Нова назва")
    # Єдиний запис із 56, де шаблону немає: закінчується «anywhere», не «in X».
    check("boards: шаблон не спрацював — назва лишається цілою",
          split_title("KAP STRATEGIES is hiring for a position of Graphic Designer anywhere",
                      "is_hiring", author="KAP STRATEGIES"),
          ("Graphic Designer anywhere", "KAP STRATEGIES", ""))

    check("boards: plain лишає заголовок як є",
          split_title("Assistant Directeur Artistique UI", "plain"),
          ("Assistant Directeur Artistique UI", "", ""))


def test_boards_region_is_eligibility_not_address() -> None:
    """🔴 `state`/`country` — адреса роботодавця, а НЕ право подаватись.

    Я спершу зробила навпаки: вирішила, що `region: Anywhere in the World` —
    це шапка, а конкретика в `state` — справжнє обмеження, і дала конкретиці
    витісняти шапку. Замір спростував: у вакансіях IxDF стоїть `state: Texas`,
    а опис кличе людей із часових поясів від UTC-6 до UTC+8. Тобто з тією
    «правкою» власник прочитав би «Texas» і пропустив вакансію, на яку має
    право. Помилка в цей бік дорожча: зайва картка коштує секунди, а
    прихована можливість не коштує нічого видимого.
    """
    import feedparser

    from jobfeed.sources.boards_rss import NO_REGION, region_of

    parsed = feedparser.parse((SAMPLES / "rss_weworkremotely.xml").read_bytes())
    by_title = {e.title: e for e in parsed.entries}
    ixdf = next(e for t, e in by_title.items() if "Course Director" in t)
    check("boards: state не витісняє регіон", getattr(ixdf, "state", ""), "Texas")
    check("boards: показуємо придатність, а не адресу",
          region_of(ixdf, ""), "Anywhere in the World")

    # Порожнеча читалась би як «скрізь можна» — тому кажемо словами.
    class Bare:
        pass

    check("boards: джерело мовчить — кажемо це", region_of(Bare(), ""), NO_REGION)
    check("boards: місце із заголовка попереду регіону",
          region_of(Bare(), "New York City"), "New York City")


def test_boards_empty_feed_is_breakage() -> None:
    """200 OK і жодного запису — поломка, а не порожній день."""
    from jobfeed.sources.boards_rss import BoardsRssSource

    class Empty:
        status_code = 200
        content = (b'<?xml version="1.0"?><rss version="2.0"><channel>'
                   b"<title>x</title></channel></rss>")

    class Client:
        def get(self, url):
            return Empty()

    source = BoardsRssSource(feeds=[{"name": "x", "url": "https://x.test/f.rss"}])
    got = list(source._fetch_one(Client(), source.feeds[0], set()))
    check("boards: нічого не віддано", got, [])
    check("boards: фід позначено збоєм", source.feed_health["x"]["state"], "failed")
    check("boards: забір неповний", source.fetch_complete, False)


def test_boards_feed_config() -> None:
    """Помилка в конфізі мусить упасти до першого запиту, а не в мережі."""
    from jobfeed.sources.boards_rss import FeedConfigError, check_feed

    check("boards: нормальний фід",
          check_feed({"name": "a", "url": "https://a.test/f.rss", "style": "plain"}),
          {"name": "a", "url": "https://a.test/f.rss", "style": "plain"})
    check("boards: style за замовчуванням",
          check_feed({"name": "a", "url": "https://a.test/f.rss"})["style"], "plain")
    for bad in ({"name": "a"}, {"url": "https://a.test/f.rss"},
                {"name": "a", "url": "http://a.test/f.rss"},
                {"name": "a", "url": "https://a.test/f.rss", "style": "вигадка"}):
        try:
            check_feed(bad)
            failures.append(f"boards: конфіг {bad!r} прийнято, а мав упасти")
        except FeedConfigError:
            pass


def main() -> int:
    # Windows-консоль за замовчуванням cp1251 — кирилиця в ній падає.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
    if failures:
        print(f"ПРОВАЛЕНО {len(failures)}:\n")
        for f in failures:
            print(f"  ✗ {f}\n")
        return 1
    print(f"OK — {len(tests)} груп перевірок пройшли")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
