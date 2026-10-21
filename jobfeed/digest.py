"""HTML-дайджест — головна поверхня, яку бачить людина.

Дизайн-рішення і чому саме так:

* **Одна колонка, не дашборд.** Це стрічка для читання, а не панель метрик.
  Ширина обмежена ~760px: довший рядок око губить, коротший ріже заголовки.
* **«Нове» вгорі, окремою секцією.** Головне питання при відкритті —
  «що зʼявилось, відколи я дивився». Все інше — довідка, тому нижче.
* **Ліва акцентна смуга замість бейджа «NEW».** Помічається периферійним зором
  при скролі, не додаючи ще один елемент у рядок.
* **Чипи тільки з реальних даних.** Немає зарплати у джерелі — немає чипа.
  Порожній плейсхолдер гірший за відсутність: він читається як «зарплата не вказана
  роботодавцем», хоча насправді ми просто не знаємо.
* **Формат роботи виводиться з локації, а не вигадується.** Локація ріжеться
  на частини як є: місто лишається містом, «віддалено» підсвічується як формат.
  Похідного чипа «офіс» немає — назва міста вже означає присутність.
  За брифом форматом не фільтруємо, але показуємо.
* **Пошук і фільтри — на клієнті.** Сторінка статична й офлайнова; тридцять рядків
  JS рятують, коли за два тижні набереться 200 карток.
* **Нуль зовнішніх запитів.** Ні шрифтів, ні CDN: файл має відкриватись з диска
  без мережі й не тікати даними назовні.
"""

from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .models import Entry
from .reservation import NO, YES

# `дистанційн` — це work.ua. Слово інше, стан той самий; без нього половина
# їхніх карток мала б місто «Дистанційно» й жодного чипа формату.
_REMOTE = re.compile(r"віддален|дистанційн|remote", re.IGNORECASE)
_ABROAD = re.compile(r"за\s+кордоном", re.IGNORECASE)


def _e(text: str) -> str:
    return html.escape(text or "", quote=True)


def location_chips(location: str) -> list[tuple[str, str]]:
    """Локація → чипи (вид, текст). Нічого не додумуємо і нічого не дублюємо.

    DOU складає локацію в один рядок через кому: «Київ, за кордоном, віддалено».
    Кожна частина — окремий чип: місто лишається містом, «віддалено» і
    «за кордоном» підсвічуються як формат роботи. Окремий похідний чип «офіс»
    не малюємо: назва міста вже означає присутність, а зайвий чип у рядку
    конкурує з рештою за увагу і робить формат менш помітним, а не більш.
    """
    chips = []
    for part in location.split(","):
        part = part.strip()
        if not part:
            continue
        kind = "fmt" if (_REMOTE.search(part) or _ABROAD.search(part)) else "loc"
        chips.append((kind, part))
    return chips


_MONTHS = ("січня", "лютого", "березня", "квітня", "травня", "червня",
           "липня", "серпня", "вересня", "жовтня", "листопада", "грудня")


def _aware(moment: datetime) -> datetime:
    # Рендер не має права падати через форму даних одного джерела: наївну
    # дату вважаємо UTC, а не кидаємо TypeError на всю сторінку.
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _relative(moment: datetime | None) -> str:
    """Скільки минуло, людською мовою. Без вигаданої точності."""
    if not moment:
        return ""
    local = _aware(moment).astimezone()
    now = datetime.now().astimezone()
    minutes = int((now - local).total_seconds() // 60)
    if minutes < 60:
        return "щойно"
    if minutes < 60 * 24:
        return f"{minutes // 60} год тому"

    # Далі рахуємо КАЛЕНДАРНИМИ днями, а не діленням годин на 24: 47 годин
    # тому — це позавчора, а не «вчора», і людина порівнює саме з календарем.
    days = (now.date() - local.date()).days
    if days == 1:
        return "вчора"
    if days < 7:
        return f"{days} дн тому"
    if days < 31:
        return f"{days // 7} тиж тому"
    return _exact(moment)


def _exact(moment: datetime | None) -> str:
    """Точна дата для підказки. Час показуємо лише коли він не опівночі —
    інакше вигадали б точність, якої джерело не дало."""
    if not moment:
        return ""
    # Рівна опівніч UTC означає, що джерело дало дату БЕЗ часу (сюди
    # значення приходить уже нормалізованим адаптером, тому перевірка саме
    # по UTC, а не по поясу джерела). Такі не переводимо в місцевий пояс:
    # перевід приписав би їм годину (00:00 → 03:00) і навіть міг би зсунути
    # день — тобто ми вигадали б точність, якої джерело не давало.
    # Київська опівніч зберігається як 21:00 UTC, під правило не потрапляє
    # й показується з часом — це правильно, час там справді був.
    if (moment.hour, moment.minute) == (0, 0):
        return f"{moment.day} {_MONTHS[moment.month - 1]} {moment.year}"
    local = _aware(moment).astimezone()
    return (f"{local.day} {_MONTHS[local.month - 1]} {local.year}, "
            f"{local.strftime('%H:%M')}")


_EMPLOYMENT = {
    "PART_TIME": "часткова зайнятість",
    "CONTRACTOR": "контракт",
    "TEMPORARY": "тимчасова",
    "INTERN": "стажування",
    "VOLUNTEER": "волонтерство",
    "PER_DIEM": "погодинна",
    "OTHER": "інший формат",
}


def experience_label(months: int | None) -> str:
    """Місяці досвіду → те, як це читає людина.

    36 місяців ніхто не тримає в голові як «36» — це «3 роки». Але в грейд
    («Middle») не перетворюємо: грейд був би НАШОЮ інтерпретацією, а джерело
    сказало лише число.

    У роки переводимо ЛИШЕ круглі значення. 18 місяців як «від 1 року» —
    формально правда, але половина вимоги при цьому зникає; «від 18 міс»
    і коротше, і точніше.
    """
    if months is None or months < 0:
        return ""
    if months == 0:
        return "без досвіду"
    if months % 12:
        return f"від {months} міс"
    years = months // 12
    # Після «від» іде родовий відмінок: «від 1 року», «від 3 років».
    return f"від {years} {'року' if years == 1 else 'років'}"


def employment_label(raw: str) -> str:
    """Тип зайнятості — ТІЛЬКИ коли він відхиляється від звичайного.

    Full-time стоїть у 93% вакансій. Підпис на дев'яти картках із десяти —
    це не інформація, а шум: око вчиться пропускати те місце й перестає
    помічати саме ті випадки, де формат інший і це важливо.
    """
    key = (raw or "").strip().upper()
    if not key or key == "FULL_TIME":
        return ""
    return _EMPLOYMENT.get(key, key.replace("_", " ").lower())


def _card(entry: Entry, kinds: dict[str, str], kind: str = "board") -> str:
    vacancy, is_new = entry.vacancy, entry.is_new
    reservation = entry.reservation

    chips = location_chips(vacancy.location)
    experience = experience_label(vacancy.experience_months)
    if experience:
        chips.append(("exp", experience,
                      f"Роботодавець указав досвід: {vacancy.experience_months} міс"))
    employment = employment_label(vacancy.employment_type)
    if employment:
        chips.append(("job", employment))
    # Рівень англійської — тільки коли він написаний поруч зі згадкою мови.
    # Показуємо ДОСЛІВНО, як у джерелі: `B2` і `Upper-Intermediate` приблизно
    # рівні, але «приблизно» в чипі стало б твердженням. `+` і діапазон
    # лишаються — це мінімум, і звести їх до точки означало б відкинути
    # вакансію, яка підходить.
    if entry.english:
        chips.append(("eng", f"англ. {entry.english}",
                      f"З опису: …{entry.english_evidence}…"))
    if vacancy.salary:
        chips.append(("pay", vacancy.salary))
    elif entry.budget:
        # Той самий чип, що й зарплата з поля джерела: для власника це одне
        # й те саме число, різниця лише в тому, звідки ми його взяли. Але в
        # підказці кажемо звідки — і даємо цитату, як у рівня мови: вона вже
        # раз окупилась, через неї знайшли єдину помилку за десять хвилин.
        chips.append(("pay", entry.budget,
                      "Взято з опису вакансії, поля зарплати джерело не дало. "
                      f"…{entry.budget_evidence}…"))
    # ⚠️ Два РІЗНІ часи, і плутати їх не можна. `posted_at` — коли роботодавець
    # опублікував вакансію, це і є свіжість. `first_seen` — коли її вперше
    # побачив НАШ сервіс, тобто історія наших прогонів. Коли джерело не дає
    # дати публікації (Andersen не має її в принципі), показуємо друге — але
    # ІНШИМ словом, бо це інше твердження.
    if vacancy.posted_at:
        age, exact, prefix = _relative(vacancy.posted_at), _exact(vacancy.posted_at), "Опубліковано"
    else:
        age, exact, prefix = _relative(entry.first_seen), _exact(entry.first_seen), "Знайдено"
        if age:
            age = f"знайдено {age}"
    if age:
        chips.append(("age", age, f"{prefix}: {exact}"))
    # Бронювання показуємо ТІЛЬКИ коли воно є: акцентний чип плюс цитата,
    # щоб людина побачила формулювання, а не наш висновок, і не відкривала
    # вакансію заради перевірки.
    #
    # Негативної позначки навмисно немає. Ознака, яка стоїть на кожній картці,
    # не розрізняє нічого — це просто шум, а цінність чипа тут саме в тому, що
    # він рідкісний і одразу впадає в око. Сенс «не згадано ≠ немає» несе
    # застереження в підвалі сторінки, і воно там обовʼязкове.
    booking = ""
    has_reservation = 0
    if reservation is not None and reservation.state == YES:
        has_reservation = 1
        chips.insert(0, ("res", "🛡 є бронювання"))
        if reservation.evidence:
            booking = f'<div class="card__quote">{_e(reservation.evidence)}</div>'

    # Бирки джерел у куті картки. Своє джерело плюс УСІ, де ця сама вакансія
    # трапилась, — кожна окремим посиланням на свою версію оголошення.
    #
    # Це те місце, де видно, що ми свідомо не зливаємо дублі: пара схожих
    # карток із однаковими бирками `djinni + dou` читається як «одна вакансія
    # на двох сайтах», а не як наша помилка. Раніше друге джерело жило
    # окремим рядком «також на», і звʼязок треба було вичитувати.
    badges = "".join(
        f'<a class="badge badge--{kinds.get(src, "board")}" href="{_e(link)}" '
        f'target="_blank" rel="noopener" '
        f'title="{"Власний сайт компанії" if kinds.get(src) == "company" else "Джоб-борд"}'
        f': відкрити там">{_e(SOURCE_LABELS.get(src, src))}</a>'
        for src, link in [(vacancy.source, vacancy.url), *entry.also_on]
    )
    sources_block = f'<div class="card__sources">{badges}</div>'

    # Напрями — окремим рядком під метаданими, а не в спільному ряду чипів:
    # це не властивість вакансії, а те, за чим вона фільтрується, і людина
    # має бачити ВІДПОВІДЬ на питання «чому воно тут» без пошуку в конфізі.
    # Дві групи на картці — норма, а не помилка класифікації.
    dirs_html = "".join(
        # Напрям, знайдений в ОПИСІ, несе цитату в підказці — як рівень мови
        # й бюджет. Напрям із заголовка її не потребує: доказ у самій назві.
        f'<span class="dir"'
        + (f' title="З опису: …{_e((entry.direction_evidence or {})[name])}…"'
           if (entry.direction_evidence or {}).get(name) else "")
        + f'>{_e(name)}</span>'
        for name in entry.directions
    )
    dirs_block = f'<div class="card__dirs">{dirs_html}</div>' if dirs_html else ""

    chip_html = "".join(
        f'<span class="chip chip--{c[0]}"'
        + (f' title="{_e(c[2])}"' if len(c) > 2 else "")
        + f">{_e(c[1])}</span>"
        for c in chips
    )

    # Особисті позначки. Живуть у localStorage браузера, тому прив'язані до
    # детермінованого `id` — саме тому вони й переживають перегенерацію
    # сторінки: `sha1(source:source_uid)` не залежить від часу прогону.
    # Дві кнопки свідомо РІЗНІ за вагою. «Відгукнувся» — досягнення, до нього
    # повертаються; «не цікавить» — прибирання, і робити його таким же
    # помітним не можна, інакше картка перетворюється на дві однакові команди.
    # Жодна з них не мусить перебивати заголовок: головне на картці — вакансія.
    #
    # Стан «натиснуто» читається НЕ лише кольором: іконка міняється (○ → ✔,
    # ✕ → ⊘) і зʼявляється рамка. Колір тут третій канал, а не єдиний.
    marks = (
        '<div class="card__marks">'
        f'<button class="mark mark--applied" data-mark="applied" data-id="{_e(vacancy.id)}"'
        ' type="button" title="Я вже відгукнувся на цю вакансію">'
        '<span class="mark__off">○</span><span class="mark__on">✔</span>'
        " відгукнувся</button>"
        f'<button class="mark mark--skip" data-mark="skipped" data-id="{_e(vacancy.id)}"'
        ' type="button" title="Прибрати з видачі — не мій профіль">'
        '<span class="mark__off">✕</span><span class="mark__on">⊘</span>'
        " не цікавить</button>"
        "</div>"
    )
    company = f'<div class="card__company">{_e(vacancy.company)}</div>' if vacancy.company else ""
    haystack = _e(" ".join([vacancy.title, vacancy.company, vacancy.location, vacancy.source]).lower())

    return (
        f'<article class="card{" card--new" if is_new else ""}" '
        f'data-id="{_e(vacancy.id)}" data-new="{int(is_new)}" data-res="{has_reservation}" '
        f'data-source="{_e(vacancy.source)}" data-kind="{_e(kind)}" '
        f'data-ids="{_e(",".join(entry.ids or (vacancy.id,)))}" '
        f'data-feed="{_e(vacancy.feed)}" '
        f'data-dirs="{_e("|".join(entry.directions))}" '
        f'data-company="{_e(vacancy.company.lower())}" data-text="{haystack}">'
        f"{sources_block}"
        f'<a class="card__title" href="{_e(vacancy.url)}" target="_blank" rel="noopener">'
        f"{_e(vacancy.title)}</a>"
        f"{company}"
        f'<div class="card__meta">{chip_html}</div>'
        f"{dirs_block}"
        f"{booking}"
        f"{marks}"
        f"</article>"
    )


CSS = """
*,*::before,*::after{box-sizing:border-box}
:root{
  --bg:#f6f7f9; --surface:#fff; --line:#e4e7ec; --ink:#14181f; --muted:#5d6673;
  --accent:#2f6df6; --accent-soft:#e8efff; --chip:#f1f3f6;
  --ok:#14804a; --ok-soft:#e3f5eb; --faint:#9aa3b0;
}
@media (prefers-color-scheme:dark){
  :root{--bg:#0f1216;--surface:#161a20;--line:#252b34;--ink:#e8ebf0;--muted:#98a2b3;
        --accent:#6d9bff;--accent-soft:#1b2434;--chip:#1e242c;
        --ok:#4ecb8d;--ok-soft:#12271d;--faint:#6b7480}
}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.5 -apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
  -webkit-font-smoothing:antialiased}
.shell{display:flex;gap:30px;max-width:1120px;margin:0 auto;padding:28px 20px 64px;
  align-items:flex-start}
.main{flex:1;min-width:0}
.side{flex:0 0 236px;width:236px;position:sticky;top:20px;max-height:calc(100vh - 40px);
  overflow-y:auto}
.side__group{margin-top:22px}
.side h3{font-size:11px;text-transform:uppercase;letter-spacing:.09em;color:var(--muted);
  margin:0 0 6px;font-weight:600;display:flex;align-items:baseline;gap:7px}
.side__note{margin:0 0 8px;font-size:11.5px;color:var(--faint);line-height:1.4}
.side input[type=search]{width:100%;padding:7px 10px;margin-bottom:6px;border:1px solid var(--line);
  border-radius:8px;background:var(--surface);color:inherit;font:inherit;font-size:12.5px}
.nav{display:flex;width:100%;align-items:center;gap:8px;appearance:none;background:none;
  border:0;border-radius:8px;padding:6px 9px;font:inherit;font-size:13px;color:var(--muted);
  cursor:pointer;text-align:left}
.nav .cnt{margin-left:auto}
.nav:hover{background:var(--chip);color:var(--ink)}
.nav.is-on{background:var(--accent-soft);color:var(--accent);font-weight:600}
.nav--reset{font-weight:600;color:var(--ink)}
.nav--off{color:var(--faint);font-size:11.5px;line-height:1.4;cursor:default;
  padding:7px 9px;display:block}
.nav--off:hover{background:none;color:var(--faint)}
/* Рядок джерела став двоповерховим: назва з числом, під нею — стан. */
.nav--src{display:block;padding:7px 9px}
.nav--src .nav__top{display:flex;align-items:center;gap:8px;width:100%}
.nav--src .nav__state{display:block;margin-top:2px;font-size:11px;line-height:1.35;
  color:var(--faint);white-space:normal}
/* Колір лише ПІДСИЛЮЄ текст, а не замінює його: причина завжди написана
   словами, бо «червоне» не каже, чекати чи лізти в конфіг. */
.nav--src.is-bad .nav__state{color:#c0392b}
.nav--src.is-warn .nav__state{color:#b7791f}
.nav--src.is-off{opacity:.72}
/* Панель сервера прихована, доки сервер не відповів: кнопка, яка нічого не
   робить, обіцяє дію. Файл із диска не показує її взагалі. */
.srv{display:none;margin:0 0 10px;padding:8px;border:1px solid var(--line);border-radius:10px}
body.is-served .srv{display:block}
.srv__go{width:100%;appearance:none;border:1px solid var(--line);background:var(--chip);
  border-radius:8px;padding:7px;font:inherit;font-size:12.5px;cursor:pointer;color:var(--ink)}
.srv__go:disabled{opacity:.6;cursor:default}
.srv__note{margin:6px 0 0;font-size:11px;color:var(--muted);line-height:1.35}
.srv__add{margin-top:8px;font-size:12px}
.srv__add summary{cursor:pointer;color:var(--accent);font-size:11.5px}
.srv__add select,.srv__add input{width:100%;margin-top:5px;padding:5px 6px;font:inherit;
  font-size:12px;border:1px solid var(--line);border-radius:7px;background:var(--bg);
  color:var(--ink)}
.srv__row{display:flex;gap:6px;margin-top:6px}
.srv__row button{flex:1;appearance:none;border:1px solid var(--line);background:none;
  border-radius:7px;padding:5px;font:inherit;font-size:12px;cursor:pointer;color:var(--ink)}
.srv__row button:disabled{opacity:.45;cursor:default}
.srv__result{margin:6px 0 0;font-size:11px;line-height:1.35;color:var(--muted)}
.srv__result.is-ok{color:#1e7e46}
.srv__result.is-bad{color:#c0392b}
.srv__hint{margin:6px 0 0;font-size:10.5px;color:var(--faint);line-height:1.35}
/* Одна й та сама сторінка каже РІЗНЕ залежно від того, чи є сервер: із
   файлу позначки справді крихкі, під сервером вони в базі. Лишити тут один
   текст означало б, що в одному з двох режимів сторінка бреше про те, де
   лежить єдине, що власник створив руками. */
.keep__served{display:none}
body.is-served .keep__local{display:none}
body.is-served .keep__served{display:inline}
.dropped{margin:18px 0 0;border-top:1px solid var(--line);padding-top:12px}
.dropped summary{cursor:pointer;font-size:12px;color:var(--muted);letter-spacing:.02em;
  text-transform:uppercase}
.dropped summary .n{color:var(--ink)}
.drop__note{margin:8px 0 10px;font-size:11.5px;color:var(--faint)}
.drop{display:grid;grid-template-columns:1fr auto;gap:2px 10px;padding:5px 0;
  border-bottom:1px solid var(--line);font-size:12px}
.drop__t{color:var(--ink)}
.drop__c{color:var(--faint);font-size:11px;text-align:right}
.drop__r{grid-column:1/-1;color:var(--faint);font-size:11px}
.drop__more{margin:8px 0 0;font-size:11.5px;color:var(--faint)}
/* Рядок = назва (фільтрує) + стрілка (розгортає). Дві дії — дві кнопки. */
.nav__row{display:grid;grid-template-columns:1fr auto;align-items:start}
.nav__toggle{appearance:none;background:none;border:0;padding:6px 7px;margin-top:1px;
  font:inherit;font-size:11px;color:var(--faint);cursor:pointer;border-radius:7px;
  transition:transform .12s ease}
.nav__toggle:hover{background:var(--chip);color:var(--ink)}
.nav__toggle--none{cursor:default;width:25px}
.grp.is-open > .nav__row > .nav__toggle{transform:rotate(90deg)}
/* Клікабельність мусить бути видна ДО кліку: власник не здогадався, що
   заголовок джерела — кнопка, і тиснув окремі компанії. */
.nav--src .nav__name{color:var(--ink);font-weight:600}
.nav--src:hover .nav__name{text-decoration:underline}
.nav__bad{margin-left:6px;font-size:10px;color:#c0392b;font-weight:600}
.nav__note{display:block;margin-top:1px;font-size:10px;color:var(--faint);
  line-height:1.3;white-space:normal}
.grp--l1{margin-left:9px;padding-left:8px;border-left:1px solid var(--line)}
.nav__feeds[hidden]{display:none}
/* Тіло розділу компаній не має лівої лінійки й відступу — це не вкладені
   фіди, а власний вміст розділу. */
.side__body{margin:0;padding:0;border:0}
.side__head{margin:0;flex:1}
/* Заголовок-кнопка мусить читатись як кнопка ДО кліку — власник двічі не
   здогадався, що заголовок клікабельний. */
.nav--head{appearance:none;background:none;border:0;padding:6px 9px;font:inherit;
  font-size:11px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;
  color:var(--muted);cursor:pointer;text-align:left;display:flex;
  align-items:center;gap:8px;border-radius:8px;width:100%}
.nav--head .cnt{margin-left:auto}
.nav--head:hover{background:var(--chip);color:var(--ink);text-decoration:underline}
.age{font-weight:600;color:var(--ink)}
/* Застаріла сторінка мусить читатись як попередження, а не як підпис. */
.age.is-old{color:#b7791f}
.age.is-stale{color:#c0392b}
.reread{appearance:none;background:none;border:1px solid var(--line);border-radius:7px;
  padding:3px 10px;flex:0 0 auto;font:inherit;font-size:11px;color:var(--muted);
  cursor:pointer}
.reread:hover{background:var(--chip);color:var(--ink)}
.sub__hint{font-size:11px;color:var(--faint);min-width:0}
/* Під сервером цей рядок став неправдою: там збір запускає кнопка в шапці,
   а не тільки run.bat. Писався він для файлової сторінки — там і лишається. */
body.is-served .sub__hint{display:none}
.tabs{display:flex;gap:2px;margin:0 0 14px;border-bottom:1px solid var(--line)}
.tab{appearance:none;background:none;border:0;border-bottom:2px solid transparent;
  padding:8px 12px;margin-bottom:-1px;font:inherit;font-size:13px;color:var(--muted);
  cursor:pointer;display:flex;align-items:center;gap:6px}
.tab:hover{color:var(--ink)}
.tab.is-on{color:var(--ink);border-bottom-color:var(--accent);font-weight:600}
.tab .cnt{font-size:11px;color:var(--faint)}
.tab.is-on .cnt{color:var(--accent)}
.dirs__live{margin-top:6px;padding-top:7px;border-top:1px solid var(--line);
  font-size:12px;color:var(--ink);font-weight:600}
.dirs__live span{font-weight:400;color:var(--faint)}
.nav__eye{margin-right:5px;font-size:9px;color:var(--accent);vertical-align:1px}
.side__active{margin:0 0 6px 9px;font-size:11px;color:var(--accent);line-height:1.35}
.side__active b{font-weight:600}
.side__group .nav__row{align-items:center;gap:4px}
.nav__feeds{margin:1px 0 4px 9px;padding-left:8px;border-left:1px solid var(--line)}
.nav--feed{display:grid;grid-template-columns:1fr auto;font-size:12px;padding:4px 7px;
  column-gap:6px}
.nav--feed .nav__sub{grid-column:1/-1;font-size:10.5px;color:var(--faint);line-height:1.3}
.nav--feed.is-bad .nav__sub{color:#c0392b}
.nav--feed.is-warn .nav__sub{color:#b7791f}
.nav--company.is-tail{display:none}
.grp.show-tail .nav--company.is-tail{display:flex}
.side__more{appearance:none;background:none;border:0;padding:5px 9px;font:inherit;
  font-size:12px;color:var(--accent);cursor:pointer;text-decoration:underline}
.side__empty{margin:4px 9px;font-size:12px;color:var(--faint)}
@media (max-width:900px){
  .shell{flex-direction:column}
  .side{position:static;width:100%;flex:none;max-height:none}
}
header{margin-bottom:24px}
h1{margin:0 0 4px;font-size:22px;letter-spacing:-.01em}
.head{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-bottom:4px}
.head h1{margin:0}
/* Кнопка головної дії: більша за все інше в шапці, окрім самого заголовка. */
.go{appearance:none;border:0;border-radius:10px;padding:9px 18px;font:inherit;
  font-size:14px;font-weight:600;cursor:pointer;white-space:nowrap}
.go--real{background:var(--accent);color:#fff}
.go--real:hover{filter:brightness(1.07)}
.go--real:disabled{opacity:.75;cursor:default}
/* Файлова кнопка навмисно НЕ виглядає як кнопка збору: вона підказка, і
   вигляд каже це раніше, ніж власник дочитає назву. */
.go--file{background:var(--chip);color:var(--ink);border:1px solid var(--line);
  font-weight:500}
.go--file:hover{background:var(--surface)}
.ran{margin:8px 0 0;font-size:12.5px;color:#1e7e46;font-weight:600}
.ran.is-bad{color:#c0392b}
.howto{margin:10px 0 0;padding:12px 14px;border:1px solid var(--line);
  border-radius:10px;background:var(--surface);font-size:13px;max-width:620px}
.howto b{display:block;margin-bottom:8px;color:var(--ink);font-size:13px}
.howto p{margin:9px 0 0;font-size:12px;color:var(--muted);line-height:1.6}
.howto__row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.howto code{background:var(--chip);padding:2px 7px;border-radius:6px;font-size:12px;
  color:var(--ink)}
.howto__row code{flex:1 1 240px;min-width:0;overflow-wrap:anywhere}
.howto__row button{appearance:none;border:1px solid var(--line);background:none;
  border-radius:7px;padding:6px 10px;font:inherit;font-size:12px;cursor:pointer;
  color:var(--ink);white-space:nowrap}
.howto__row button:hover{background:var(--chip)}
.sub{color:var(--muted);font-size:13px}
/* Метадані переносяться, кнопка живе окремим рядком — її видимість не
   залежить від того, скільки джерел перелічено вище. */
.sub__meta{display:block}
.sub__row{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin-top:6px}
.bar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:20px 0 8px;
  position:sticky;top:0;background:var(--bg);padding:12px 0;z-index:5}
.bar input[type=search]{flex:1 1 220px;min-width:0;padding:9px 12px;border:1px solid var(--line);
  border-radius:8px;background:var(--surface);color:inherit;font:inherit}
.bar input[type=search]:focus{outline:2px solid var(--accent);outline-offset:-1px;border-color:transparent}
.toggle{display:inline-flex;align-items:center;gap:7px;padding:9px 12px;border:1px solid var(--line);
  border-radius:8px;background:var(--surface);cursor:pointer;font-size:13px;user-select:none;white-space:nowrap}
.toggle input{accent-color:var(--accent);margin:0}
h2{font-size:12px;text-transform:uppercase;letter-spacing:.09em;color:var(--muted);
  margin:28px 0 10px;font-weight:600}
.card{position:relative;background:var(--surface);border:1px solid var(--line);
  border-radius:10px;padding:14px 16px;margin-bottom:8px;transition:border-color .12s,transform .12s}
.card:hover{border-color:var(--accent);transform:translateY(-1px)}
.card--new{border-left:3px solid var(--accent);background:var(--accent-soft)}
.card__title{display:block;font-size:16px;font-weight:600;color:inherit;text-decoration:none;
  letter-spacing:-.005em;padding-right:46%}
.card__title:hover{color:var(--accent)}
.card__company{color:var(--muted);font-size:13.5px;margin-top:3px}
.card__meta{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
.chip{font-size:11.5px;padding:3px 8px;border-radius:999px;background:var(--chip);color:var(--muted);
  white-space:nowrap}
.chip--pay{background:transparent;border:1px solid var(--accent);color:var(--accent);font-weight:600}
.chip--fmt{background:transparent;border:1px solid var(--line)}
.chip--exp{background:transparent;border:1px solid var(--line)}
/* Рівень мови — та сама вага, що досвід: обидва вимоги, а не переваги. */
.chip--eng{background:transparent;border:1px solid var(--line)}
.chip--job{background:var(--accent-soft);color:var(--accent);font-weight:600}
.chip--src{background:transparent;border:1px dashed var(--line);text-transform:uppercase;
  letter-spacing:.05em;font-size:10.5px}
.chip--res{background:var(--ok-soft);color:var(--ok);font-weight:600;border:1px solid var(--ok)}
.chip--nores{background:transparent;color:var(--faint);border:1px solid transparent}
.card__quote{margin-top:9px;padding-left:10px;border-left:2px solid var(--ok);
  color:var(--muted);font-size:12.5px;line-height:1.45;font-style:italic}
details.closed{margin-top:32px;border-top:1px solid var(--line);padding-top:14px}
details.closed>summary{cursor:pointer;font-size:12px;text-transform:uppercase;
  letter-spacing:.09em;color:var(--muted);font-weight:600;list-style:none;
  display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}
details.closed>summary::-webkit-details-marker{display:none}
details.closed>summary::before{content:"▸";font-size:11px}
details.closed[open]>summary::before{content:"▾"}
details.closed>summary em{text-transform:none;letter-spacing:0;font-weight:400;
  font-style:normal;font-size:12px;color:var(--faint)}
details.closed .card{opacity:.62}
details.closed .card:hover{opacity:1}
.card__sources{position:absolute;top:12px;right:14px;display:flex;gap:5px;flex-wrap:wrap;
  justify-content:flex-end;max-width:45%}
.badge{font-size:10.5px;letter-spacing:.05em;text-transform:uppercase;padding:3px 8px;
  border-radius:6px;text-decoration:none;white-space:nowrap;border:1px solid var(--line);
  color:var(--muted);background:var(--surface)}
.badge:hover{border-color:var(--accent);color:var(--accent)}
/* Власний сайт компанії важить більше за борд — це видно до читання назви. */
.badge--company{border-color:var(--ok);color:var(--ok);background:var(--ok-soft);font-weight:700}
.badge--company:hover{border-color:var(--ok);color:var(--ok);filter:brightness(.94)}
.card__dirs{display:flex;flex-wrap:wrap;gap:5px;margin-top:8px}
.dir{font-size:11px;padding:2px 8px;border-radius:999px;background:var(--accent-soft);
  color:var(--accent);white-space:nowrap}
.dirs{position:relative}
.dirs>summary{list-style:none;cursor:pointer;padding:9px 12px;border:1px solid var(--line);
  border-radius:8px;background:var(--surface);font-size:13px;white-space:nowrap;
  display:inline-flex;align-items:center;gap:7px}
.dirs>summary::-webkit-details-marker{display:none}
.dirs>summary::after{content:"▾";font-size:11px;color:var(--muted)}
.dirs[open]>summary{border-color:var(--accent)}
.dirs__menu{position:absolute;z-index:20;top:calc(100% + 4px);left:0;min-width:270px;
  background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:6px;
  box-shadow:0 8px 24px rgba(0,0,0,.10)}
.dirs__row{display:flex;align-items:center;gap:9px;padding:7px 9px;border-radius:7px;
  cursor:pointer;font-size:13px}
.dirs__row:hover{background:var(--chip)}
.dirs__row span:first-of-type{flex:1}
.dirs__row input{accent-color:var(--accent);margin:0}
.dirs__all{display:flex;gap:6px;padding:6px 9px 2px;border-top:1px solid var(--line);
  margin-top:4px}
.dirs__all button{appearance:none;background:none;border:0;padding:2px 0;font:inherit;
  font-size:12px;color:var(--accent);cursor:pointer;text-decoration:underline}
.card__marks{display:flex;gap:6px;margin-top:12px;align-items:center}
.mark{appearance:none;background:var(--surface);border:1px solid var(--line);border-radius:8px;
  padding:6px 12px;font:inherit;font-size:12.5px;color:var(--muted);cursor:pointer;
  display:inline-flex;align-items:center;gap:6px;transition:border-color .12s,color .12s}
.mark:hover{border-color:var(--muted);color:var(--ink)}
.mark__on{display:none}
.mark.is-on .mark__off{display:none}
.mark.is-on .mark__on{display:inline}
/* «Відгукнувся» — досягнення: помітне, з вагою. */
.mark--applied{color:var(--ink)}
.mark--applied.is-on{background:var(--ok-soft);border-color:var(--ok);color:var(--ok);
  font-weight:600;box-shadow:inset 0 0 0 1px var(--ok)}
/* «Не цікавить» — прибирання: навмисно тихіше, щоб не сперечалось із першою. */
.mark--skip{font-size:12px}
.mark--skip.is-on{background:var(--chip);border-color:var(--faint);color:var(--faint);
  text-decoration:line-through}
.card--applied{border-left:3px solid var(--ok)}
.card--skipped{opacity:.5}
.card--skipped:hover{opacity:1}
.keep{margin:18px 0 0;padding:12px 14px;border:1px dashed var(--line);border-radius:10px;
  color:var(--muted);font-size:12.5px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.keep b{color:var(--ink);font-weight:600}
.keep button,.keep label{appearance:none;background:var(--surface);border:1px solid var(--line);
  border-radius:8px;padding:6px 11px;font:inherit;font-size:12.5px;color:inherit;cursor:pointer}
.keep input[type=file]{display:none}
.cnt{background:var(--chip);border-radius:999px;padding:1px 6px;font-size:11px;color:var(--muted)}
.empty{color:var(--muted);padding:28px 0;text-align:center;font-size:14px}
footer{margin-top:40px;color:var(--muted);font-size:12px;border-top:1px solid var(--line);padding-top:14px}
"""

JS = """
// ── Особисті позначки ───────────────────────────────────────────────────
// Живуть у localStorage браузера. Це працює ЛИШЕ тому, що наш id
// детермінований — sha1(source:source_uid) не залежить від часу прогону,
// тож після щогодинної перегенерації сторінки позначки лягають назад на ті
// самі вакансії. З випадковим id вони б губились щогодини.
const KEY = 'find_new.marks.v1';
let marks = {};
try { marks = JSON.parse(localStorage.getItem(KEY) || '{}'); } catch (e) { marks = {}; }
function save(){ try { localStorage.setItem(KEY, JSON.stringify(marks)); } catch (e) {} }
function markOf(id){ return marks[id] || {}; }

// 🔴 Злита картка має КІЛЬКА id — по одному від кожного джерела.
//
// Позначки привʼязані до id, і заміряно: із 14 злитих пар вісім мають
// позначку, а в шести з них вона стоїть лише на ОДНІЙ із двох карток.
// Якби картка знала лише свій канонічний id, шість позначок власника
// зникли б з екрана — і це єдине в проєкті, чого не відновити з джерел.
//
// Тому: позначена, якщо позначений БУДЬ-ЯКИЙ із її id; нова позначка
// пишеться в УСІ одразу, щоб стан не залежав від того, який з них
// канонічний сьогодні.
function idsOf(card){ return (card.dataset.ids || card.dataset.id).split(','); }

function cardMark(card){
  const out = {};
  for (const id of idsOf(card)){
    const m = markOf(id);
    if (m.applied) out.applied = true;
    if (m.skipped) out.skipped = true;
  }
  return out;
}

// Сервер — НЕОБОВʼЯЗКОВИЙ. Поки `/api/ping` не відповів, сторінка поводиться
// рівно як досі: файл, відкритий із диска, лишається повністю робочим.
// Тому все серверне вмикається прапорцем, а не заміняє наявний шлях.
let SERVER = false;

function toggle(card, name){
  const state = cardMark(card);
  state[name] = !state[name];
  for (const id of idsOf(card)){
    if (!state.applied && !state.skipped) delete marks[id];
    else marks[id] = {applied: !!state.applied, skipped: !!state.skipped};
  }
  save(); paint(); apply();
  if (SERVER) for (const id of idsOf(card)){
    // Пишемо й локально, і на сервер. localStorage лишається кешем: якщо
    // сервер зупинять, позначки далі видно, а не зникають разом із ним.
    fetch('/api/marks', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({id, applied: !!state.applied, skipped: !!state.skipped})})
      .catch(() => {});
  }
}

async function connectServer(){
  let base = '';
  try {
    const ping = await fetch('/api/ping');
    if (!ping.ok) throw 0;
  } catch (e) {
    // Ми не під сервером — найімовірніше, файл із диска. Але сервер може
    // бути запущений поруч, і тоді наші позначки треба ВІДДАТИ йому, доки
    // вони не зникли разом із кешем браузера.
    //
    // Без цього кроку міграція була б фікцією: сторінка на localhost не
    // бачить localStorage файлової сторінки (різні origin), тому шукала б
    // позначки там, де їх немає, знаходила порожньо — і звітувала успіх.
    return backupToServer();
  }
  SERVER = true;

  // 🔴 МІГРАЦІЯ. Спершу віддаємо те, що вже наставлено в цьому браузері, і
  // лише потім забираємо обʼєднаний набір. Порядок саме такий: якби ми
  // спершу прочитали базу й перезаписали нею локальне, позначки, яких на
  // сервері ще немає, зникли б у той самий момент, коли власник уперше
  // запустив сервер, — тобто ми знищили б їх дією, що зветься «підключення».
  //
  // Обʼєднання одностороннє: сервер додає невідоме й не чіпає наявне.
  // Порожній localStorage не має права вичистити базу.
  let payload = {};
  try { payload = JSON.parse(localStorage.getItem(KEY) || '{}'); } catch (e) {}
  try {
    const res = await fetch('/api/marks/import', {method:'POST',
      headers:{'Content-Type':'application/json'}, body: JSON.stringify({marks: payload})});
    const data = await res.json();
    marks = data.marks || marks;
    save(); paint(); apply();
    if (data.added) note(`позначки перенесено в базу: ${data.added}`);
  } catch (e) { note('сервер не відповів на перенесення позначок — працюю локально'); }

  document.body.classList.add('is-served');
  // Справжня кнопка збору існує лише там, де збір справді можливий.
  if (srvRefresh) srvRefresh.hidden = false;
  if (goFile) goFile.hidden = true;
  if (howto) howto.hidden = true;
}

async function backupToServer(){
  let payload = {};
  try { payload = JSON.parse(localStorage.getItem(KEY) || '{}'); } catch (e) {}
  try {
    const res = await fetch(SERVE_URL + '/api/marks/import', {method:'POST',
      headers:{'Content-Type':'application/json'}, body: JSON.stringify({marks: payload})});
    const data = await res.json();
    // 🔴 Синхронізація ДВОБІЧНА. Раніше ця гілка віддавала свої позначки й
    // читала з відповіді лише `added` — обʼєднаний набір уже приходив і
    // викидався. Наслідок: позначив на localhost → відкрив файл із диска →
    // позначок немає. У зворотний бік працювало, тому асиметрія й не була
    // видима звідти, звідки на неї дивишся.
    //
    // Прийняти відповідь безпечно, і це ПОМІРЯНО, а не припущено: ендпоінт
    // спершу імпортує надіслані позначки, а тоді повертає весь вміст бази —
    // тобто обʼєднання. Локальна позначка, якої база не знала, у відповіді
    // вже є й зникнути не може. Якби він повертав лише серверні, прийняття
    // стерло б саме те, що ми весь час бережемо.
    const incoming = data.marks;
    if (incoming && typeof incoming === 'object'){
      const before = Object.keys(marks).length;
      marks = incoming;
      save(); paint(); apply();
      const gained = Object.keys(marks).length - before;
      if (gained > 0) note(`підтягнуто позначок із бази: ${gained}`);
      else if (data.added) note(`позначки збережено в базі: ${data.added}. `
        + `Повна версія — ${SERVE_URL}`);
    }
  } catch (e) {}   // сервер не запущено — це нормальний, найчастіший стан
}

function note(text){
  const box = document.getElementById('srv-note');
  if (box){ box.textContent = text; box.hidden = false; }
}

const q = document.getElementById('q');
const onlyNew = document.getElementById('only-new');
const onlyRes = document.getElementById('only-res');
// Поточна вкладка — вісь особистого стану. Не зберігається між
// відкриттями навмисно: перша вкладка і є те, що розвʼязує щоденну
// проблему власника, тож кожне відкриття має починатися з неї.
let tab = 'todo';
const tabButtons = [...document.querySelectorAll('.tab')];

function stateOf(card){
  const m = cardMark(card);
  if (m.skipped) return 'skipped';
  if (m.applied) return 'applied';
  return 'todo';
}
const side = document.querySelector('.side');
const companyQ = document.getElementById('company-q');
const companyEmpty = document.getElementById('company-empty');
const cards = [...document.querySelectorAll('.card')];
const dirBoxes = [...document.querySelectorAll('[data-dir]')];
const dirsState = document.getElementById('dirs-state');
const dirsLive = document.getElementById('dirs-live');

// Вибір напрямів памʼятається. Дефолт із конфіга застосовується ТІЛЬКИ до
// чистого браузера: якщо людина щось перемкнула, наступне відкриття не має
// скидати це «налаштуванням за замовчуванням» — інакше ми зітремо її дію
// тим, що зветься налаштуванням. Той самий принцип, що з позначками:
// збережений вибір людини важливіший за наш дефолт.
const DIRS_KEY = 'find_new.dirs.v1';
function saveDirs(){
  try {
    localStorage.setItem(DIRS_KEY, JSON.stringify(
      dirBoxes.filter(b => b.checked).map(b => b.dataset.dir)));
  } catch (e) {}
}
function restoreDirs(){
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem(DIRS_KEY) || 'null'); } catch (e) {}
  if (!Array.isArray(saved)) return;           // нічого не збережено — лишаємо дефолт
  for (const box of dirBoxes) box.checked = saved.includes(box.dataset.dir);
}
let nav = 'all';

// Семантика АБО: вакансія лишається, якщо в неї є ХОЧ ОДИН обраний напрям.
// Не «сховати, якщо є хоч один знятий» — та семантика ховала б гібриди на
// кшталт Graphic/Motion Designer, які наполовину те, що потрібно.
function inDirections(card){
  const chosen = dirBoxes.filter(b => b.checked).map(b => b.dataset.dir);
  if (chosen.length === dirBoxes.length) return true;   // усі — не фільтруємо
  const own = (card.dataset.dirs || '').split('|').filter(Boolean);
  return own.some(d => chosen.includes(d));
}

function paint(){
  for (const card of cards){
    const m = cardMark(card);
    card.classList.toggle('card--applied', !!m.applied);
    card.classList.toggle('card--skipped', !!m.skipped);
    for (const button of card.querySelectorAll('.mark')){
      button.classList.toggle('is-on', !!m[button.dataset.mark]);
    }
  }
  // Числа на самих вкладках і є тепер загальний облік позначок — окремі
  // лічильники біля перемикачів зникли разом із перемикачами.
  for (const key of ['todo', 'applied', 'skipped']){
    document.getElementById('tab-' + key).textContent =
      cards.filter(c => !c.closest('details.closed') && stateOf(c) === key).length;
  }
  recountMenu();
}

// ── Лічильники в меню ───────────────────────────────────────────────────
//
// 🔴 РЕАГУЮТЬ НА ПОЗНАЧКИ, НЕ РЕАГУЮТЬ НА ФІЛЬТРИ. Це не компроміс, а
// розрізнення двох різних речей:
//
//   * фільтр — тимчасовий ПОГЛЯД. Зняв напрям — число впало, повернув —
//     виросло. Якби числа реагували, жодне з них не означало б «скільки
//     там є», і спертись не було б на що;
//   * позначка — стала особиста ДІЯ. Він відгукнувся, і це назавжди.
//     Число, яке на це не реагує, показує роботу, якої вже немає:
//     `BetterMe 5`, коли обидві вакансії вже опрацьовані.
//
// Тобто число показує стан його РОБОТИ, а не стан його погляду.
//
// Картки з розділу «ймовірно закриті» в лічильники не входять — так само,
// як не входили при генерації сторінки.
function countsCard(card){
  if (card.closest('details.closed')) return false;
  // Лічильники меню рахують те, що в ПОТОЧНІЙ вкладці: на «До розгляду»
  // це його робочий набір, на «Відгукнувся» — куди він подався в цій
  // компанії. Вкладка — вісь позначок, тому числа на неї реагують; на
  // фільтри (джерело, напрям, пошук) не реагують, як і домовлялись.
  return stateOf(card) === tab;
}

function recountMenu(){
  const live = cards.filter(countsCard);

  // Вакансійні лічильники: рахуємо тим самим правилом, що й фільтр меню,
  // але БЕЗ поточного вибору — інакше число стало б результатом фільтра.
  const was = nav;
  for (const button of side.querySelectorAll('[data-nav]')){
    const box = button.querySelector('.cnt');
    if (!box || button.dataset.nav === 'all') continue;
    if (button.dataset.nav.startsWith('cgroup:')) continue;   // рахує компанії
    nav = button.dataset.nav;
    box.textContent = live.filter(inNav).length;
  }
  nav = was;

  // Скільки компаній лишилось — для заголовка розділу й для груп.
  const alive = new Set(live.map(c => c.dataset.company).filter(Boolean));
  for (const button of side.querySelectorAll('[data-nav^="cgroup:"]')){
    const names = COMPANY_GROUPS[button.dataset.nav.slice(7)] || [];
    const box = button.querySelector('.cnt');
    if (box) box.textContent = names.filter(n => alive.has(n)).length;
  }
  const head = document.querySelector('.side__head .cnt');
  if (head) head.textContent = alive.size;
  const reset = document.querySelector('.nav--reset .cnt');
  if (reset) reset.textContent = live.length;

  // Напрями — те саме правило.
  for (const box of dirBoxes){
    const cnt = box.parentElement.querySelector('.cnt');
    if (!cnt) continue;
    const dir = box.dataset.dir;
    cnt.textContent = live.filter(
      c => (c.dataset.dirs || '').split('|').includes(dir)).length;
  }
}

// Навігація в меню — це ОДИН вибір: усе, джерело або компанія. Напрям
// лишається окремо у випадному списку зверху: одна річ фільтрується в
// одному місці, інакше меню стає другим механізмом тих самих фільтрів.
function inNav(card){
  if (nav === 'all') return true;
  // Кілька джерел через кому: `DOU` показує і категорійний фід, і фіди
  // компаній. Обʼєднання лише в поданні — у конвеєрі це різні джерела з
  // різними розкладами.
  if (nav.startsWith('source:')) return nav.slice(7).split(',').includes(card.dataset.source);
  if (nav.startsWith('company:')) return card.dataset.company === nav.slice(8);
  // Група компаній: перелік назв приходить із Python, бо він знає і слаги
  // Топ-50, і перелік банків із конфіга. Картка своєї групи не знає —
  // група це властивість КОМПАНІЇ, а не вакансії.
  if (nav.startsWith('cgroup:')){
    const names = COMPANY_GROUPS[nav.slice(7)] || [];
    return names.includes(card.dataset.company);
  }
  if (nav.startsWith('kind:')) return card.dataset.kind === nav.slice(5);
  // Фід — це окрема сторінка, з якої ми тягнемо. Для dou_companies один фід
  // це одна компанія, тож клік по її рядку показує саме її вакансії.
  if (nav.startsWith('feed:')) return card.dataset.feed === nav.slice(5);
  return true;
}

function apply(){
  const needle = q.value.trim().toLowerCase();
  let shown = 0;
  for (const card of cards){
    const ok = inNav(card)
            && inDirections(card)
            && (!onlyNew.checked || card.dataset.new === '1')
            && (!onlyRes.checked || card.dataset.res === '1')
            && stateOf(card) === tab
            && (!needle || card.dataset.text.includes(needle));
    card.hidden = !ok;
    // «Ймовірно закриті» лежать у згорнутій секції й у лічильник не входять:
    // інакше в шапці стояло б більше, ніж людина бачить на екрані.
    if (ok && !card.closest('details.closed')) shown++;
  }
  for (const section of document.querySelectorAll('section, details.closed')){
    const visible = [...section.querySelectorAll('.card')].filter(c => !c.hidden);
    section.hidden = visible.length === 0;
    const counter = section.querySelector('.n');
    if (counter) counter.textContent = visible.length;
  }
  // Порожній таб «Сайти компаній» пояснює ПРИЧИНУ, а не мовчить: власник
  // просив цей таб, і сама пустота читалась би як поломка.
  const chosen = dirBoxes.filter(b => b.checked).length;
  dirsState.textContent = chosen === dirBoxes.length ? 'усі'
                        : (chosen === 0 ? 'жодного' : chosen + ' з ' + dirBoxes.length);
  if (dirsLive) dirsLive.innerHTML = 'показано ' + shown + ' <span>з ' + cards.length + '</span>';
  // Порожня стрічка при трьох одночасних фільтрах читається як поломка,
  // якщо не видно, скільки саме показано.
  // Порожньо — але З ПРИЧИНОЮ. Голе «нічого не знайшлось» однакове і для
  // «фільтри надто вузькі», і для «джерело вимкнене третій тиждень»: воно
  // повідомляє РЕЗУЛЬТАТ там, де людині потрібна ПРИЧИНА. Клік по вимкненому
  // Andersen давав порожній екран, з якого не можна зрозуміти, чи це стан
  // джерела, чи поломка сторінки.
  const box = document.getElementById('empty');
  box.hidden = shown > 0;
  if (shown === 0){
    const src = nav && nav.startsWith('source:') ? nav.slice(7) : '';
    const fd  = nav && nav.startsWith('feed:') ? nav.slice(5) : '';
    if (src && SOURCE_STATE[src]){
      box.textContent = SOURCE_STATE[src].label + ': ' + SOURCE_STATE[src].line
        + '. Вакансій із цього джерела на сторінці немає.';
    } else if (nav.startsWith('company:') && WATCHED[nav.slice(8)]){
      // Нуль у компанії під наглядом — не «нічого немає», а «ми дивимось,
      // і зараз там нічого нашого». Різні твердження.
      box.textContent = 'Ми стежимо за цією компанією окремим фідом. Зараз у '
        + 'неї немає вакансій, які проходять фільтр, — нову побачимо того ж дня.';
    } else if (fd && FEED_STATE[fd]){
      // Три РІЗНІ причини порожнечі, які ззовні виглядають однаково:
      // компанія не має відкритих вакансій; вакансії є, але жодна не дизайн;
      // фід не забрався. Не назвати їх — значить показати «нічого немає»
      // там, де правильна відповідь «нічого нашого немає».
      box.textContent = FEED_STATE[fd];
    } else if (+document.getElementById('tab-' + tab).textContent === 0){
      // Порожня вкладка — не порожня сторінка. Різниця в тому, що тут
      // причина відома точно, і мовчати про неї не можна.
      box.textContent = {
        todo: 'Усе опрацьовано — нерозглянутих вакансій не лишилось.',
        applied: 'Ти ще нікуди не відгукнувся. Позначка «відгукнувся» на '
               + 'картці перенесе її сюди.',
        skipped: 'Ти ще нічого не відкинув. Позначка «не цікавить» на картці '
               + 'перенесе її сюди.',
      }[tab];
    } else {
      box.textContent = 'У цій вкладці під обрані фільтри не підпадає жодна '
        + 'вакансія. Знімати їх — угорі й ліворуч.';
    }
  }
  document.getElementById('shown').textContent = shown;
}

side.addEventListener('click', event => {
  const button = event.target.closest('[data-nav]');
  if (!button) return;
  nav = button.dataset.nav;
  // Підсвічуємо ВСІ кнопки з цим фільтром, а не лише натиснуту: `all` тепер
  // мають і «Усі вакансії», і заголовок компаній, і після кліку по другому
  // перша гасла б — виглядало б як «фільтр десь стоїть», хоча він знятий.
  for (const other of side.querySelectorAll('[data-nav]'))
    other.classList.toggle('is-on', other.dataset.nav === nav);
  paintActive(button);
  apply();
});

// Пошук по компаніях: 156 назв око не переглядає, а конкретну шукають точково.
companyQ.addEventListener('input', () => {
  const needle = companyQ.value.trim().toLowerCase();
  // Введення розгортає перелік ТИМЧАСОВО, не змінюючи збережений стан:
  // людина шукає компанію, а не перебудовує меню. Очищення повертає те, що
  // було, — інакше один пошук лишив би розділ розкритим назавжди.
  const body = side.querySelector('[data-body="companies"]');
  if (body){
    const group = body.closest('.grp');
    const forced = !!needle;
    body.hidden = forced ? false : !openGroups['companies'];
    if (group) group.classList.toggle('is-open', forced || !!openGroups['companies']);
  }
  // Групи компаній — другий рівень. Під час пошуку розкриваємо ті, де є
  // збіг: інакше знайдене лежало б усередині згорнутої групи, і пошук
  // виглядав би як «нічого не знайдено» при наявному результаті. Ту саму
  // помилку ми вже ловили з хвостом переліку.
  for (const cg of side.querySelectorAll('.cgroup')){
    const key = cg.dataset.grp;
    const cgBody = cg.querySelector('[data-body="' + CSS.escape(key) + '"]');
    if (!cgBody) continue;
    const hit = needle && [...cg.querySelectorAll('.nav--company')]
      .some(r => r.textContent.toLowerCase().includes(needle));
    cgBody.hidden = needle ? !hit : !openGroups[key];
    cg.classList.toggle('is-open', needle ? !!hit : !!openGroups[key]);
  }
  let visible = 0;
  for (const row of side.querySelectorAll('.nav--company')){
    const hit = !needle || row.textContent.toLowerCase().includes(needle);
    // Під час пошуку показуємо і хвіст: інакше знайдеться лише те, що й так
    // видно, а шукають саме те, чого не видно.
    //
    // ⚠️ display мусить бути ЯВНИЙ 'flex', а не ''. Порожній рядок прибирає
    // інлайновий стиль і повертає керування правилу `.is-tail{display:none}` —
    // тобто знайдений рядок із хвоста лишався б схованим, а пошук виглядав
    // би як «нічого не знайдено» при наявному збігу.
    const show = hit && (needle || !row.classList.contains('is-tail')
                         || side.classList.contains('show-tail'));
    row.style.display = show ? 'flex' : 'none';
    if (hit) visible++;
  }
  companyEmpty.hidden = visible > 0;
});

// «ще N» — власна кнопка в кожній групі: група «З IT-бордів» має 165
// компаній, і один спільний перемикач розкривав би їх усі одразу.
side.addEventListener('click', event => {
  const more = event.target.closest('[data-more]');
  if (!more) return;
  const group = more.closest('.grp');
  const open = group.classList.toggle('show-tail');
  more.textContent = open ? 'згорнути' : more.dataset.label || more.textContent;
  if (!more.dataset.label && !open) more.textContent = 'ще';
});

document.addEventListener('click', event => {
  const button = event.target.closest('.mark');
  if (button) toggle(button.closest('article'), button.dataset.mark);
});

// ── Експорт / імпорт позначок ───────────────────────────────────────────
// Не прикраса: позначки живуть у браузері, тож очищення його даних або
// перехід на інший комп'ютер стирає працю по двохстах вакансіях.
document.getElementById('export').addEventListener('click', () => {
  const blob = new Blob([JSON.stringify(marks, null, 2)], {type: 'application/json'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'find_new-marks.json';
  a.click();
  URL.revokeObjectURL(a.href);
});
document.getElementById('import').addEventListener('change', event => {
  const file = event.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    try {
      const loaded = JSON.parse(reader.result);
      if (loaded && typeof loaded === 'object') {
        // Зливаємо, а не заміняємо: імпорт не має стирати те, що вже
        // наставили в цьому браузері.
        marks = Object.assign({}, marks, loaded);
        save(); paint(); apply();
      }
    } catch (e) { alert('Не вдалося прочитати файл позначок.'); }
    event.target.value = '';
  };
  reader.readAsText(file);
});

for (const box of dirBoxes) box.addEventListener('change', () => { saveDirs(); apply(); });
document.getElementById('dirs-all').addEventListener('click', () => {
  for (const box of dirBoxes) box.checked = true; saveDirs(); apply();
});
document.getElementById('dirs-none').addEventListener('click', () => {
  for (const box of dirBoxes) box.checked = false; saveDirs(); apply();
});

q.addEventListener('input', apply);
for (const control of [onlyNew, onlyRes]) control.addEventListener('change', apply);

for (const button of tabButtons){
  button.addEventListener('click', () => {
    tab = button.dataset.tab;
    for (const other of tabButtons) other.classList.toggle('is-on', other === button);
    recountMenu();   // меню рахує в межах вкладки — див. countsCard
    apply();
  });
}
// ── Панель сервера ──────────────────────────────────────────────────────
// Показується ЛИШЕ коли сервер відповів. Кнопки, які нічого не роблять без
// сервера, гірші за їхню відсутність: вони обіцяють дію.
const srvRefresh = document.getElementById('srv-refresh');
const srvKind    = document.getElementById('srv-kind');
const srvValue   = document.getElementById('srv-value');
const srvCheck   = document.getElementById('srv-check');
const srvAdd     = document.getElementById('srv-add');
const srvResult  = document.getElementById('srv-result');

function showResult(data, saved){
  srvResult.hidden = false;
  srvResult.className = 'srv__result ' + (data.ok ? 'is-ok' : 'is-bad');
  if (!data.ok){ srvResult.textContent = '🔴 ' + (data.reason || 'не вдалось'); return; }
  const bits = [`фід відповідає · записів ${data.count}`];
  if (data.note) bits.push(data.note);
  if (data.sample && data.sample.length) bits.push('напр.: ' + data.sample[0]);
  if (saved) bits.push('ЗБЕРЕЖЕНО у filter.toml — забереться наступним прогоном');
  srvResult.textContent = (saved ? '✓ ' : '') + bits.join(' · ');
  // Зберігати можна лише після вдалої перевірки — саме тому кнопка
  // розблоковується тут, а не стоїть активною від початку.
  srvAdd.disabled = saved;
}

async function ask(url, body){
  const res = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify(body)});
  return res.json();
}

if (srvCheck) srvCheck.addEventListener('click', async () => {
  srvAdd.disabled = true;
  srvResult.hidden = false; srvResult.className = 'srv__result';
  srvResult.textContent = 'перевіряю…';
  showResult(await ask('/api/sources/check',
    {kind: srvKind.value, value: srvValue.value.trim()}), false);
});

if (srvAdd) srvAdd.addEventListener('click', async () => {
  srvAdd.disabled = true;
  showResult(await ask('/api/sources/add',
    {kind: srvKind.value, value: srvValue.value.trim()}), true);
});

if (srvRefresh) srvRefresh.addEventListener('click', async () => {
  srvRefresh.disabled = true;
  srvRefresh.textContent = 'оновлюю…';
  await ask('/api/refresh', {});
  poll();
});

async function poll(){
  let data = {};
  try { data = await (await fetch('/api/status')).json(); } catch (e) { return; }
  if (data.running){
    // Стан пишеться НА КНОПЦІ: вона тепер у шапці, і дивляться на неї, а не
    // в боковий рядок під списком джерел.
    srvRefresh.textContent = 'оновлюю…';
    setTimeout(poll, 1500);
    return;
  }
  if (data.error){
    srvRefresh.disabled = false;
    srvRefresh.textContent = 'оновити вакансії';
    note('🔴 прогін невдалий: ' + data.error);
    return;
  }
  // Раніше тут писалось «перезавантажте сторінку» — і власник мусив зробити
  // рукою те, що сторінка вміє сама. Прогін щойно переписав digest.html, у
  // DOM ще старі картки: без перезавантаження кнопка виглядає як така, що
  // спрацювала вхолосту. Стан читання (вкладка, фільтри, прокрутка) кладемо
  // в sessionStorage тим самим keepState(), що й авто-оновлення.
  srvRefresh.textContent = (data.summary || 'готово') + ' — перезавантажую…';
  if (data.summary) note(data.summary);
  try {
    sessionStorage.setItem(RAN_KEY, JSON.stringify({at: Date.now(), text: data.summary || ''}));
  } catch (e) {}
  keepState();
  setTimeout(() => location.reload(), 1200);
}

// ── Згортання розділів меню ─────────────────────────────────────────────
// Стан запамʼятовується: інакше при кожному відкритті сторінки все
// розгорталося б заново, і скарга «надто багато всього» повернулась би.
// За замовчуванням згорнуто все — джерела видно, їхні фіди за кліком.
const OPEN_KEY = 'find_new.sidebar.v1';
let openGroups = {};
try { openGroups = JSON.parse(localStorage.getItem(OPEN_KEY) || '{}'); } catch (e) {}

// Який фільтр зараз стоїть — словами, поруч із розділом, якого він стосується.
function paintActive(button){
  const box = document.getElementById('company-active');
  if (!box) return;
  if (!nav.startsWith('company:')){
    box.hidden = true;
    return;
  }
  const name = (button && button.dataset.nav === nav)
    ? button.textContent.replace(/\\d+$/, '').trim()
    : nav.slice(8);
  box.hidden = false;
  box.innerHTML = 'показано лише <b></b> — натисніть заголовок, щоб зняти';
  box.querySelector('b').textContent = name;
}

function paintGroups(){
  for (const group of document.querySelectorAll('.grp')){
    const key = group.dataset.grp;
    const body = group.querySelector('[data-body="' + CSS.escape(key) + '"]');
    const toggle = group.querySelector('.nav__toggle[data-toggle]');
    const open = !!openGroups[key];
    group.classList.toggle('is-open', open);
    if (body) body.hidden = !open;
    if (toggle){
      toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
      toggle.title = open ? 'згорнути' : 'розгорнути';
    }
  }
}

side.addEventListener('click', event => {
  const toggle = event.target.closest('.nav__toggle[data-toggle]');
  if (!toggle) return;
  event.stopPropagation();
  const key = toggle.dataset.toggle;
  openGroups[key] = !openGroups[key];
  try { localStorage.setItem(OPEN_KEY, JSON.stringify(openGroups)); } catch (e) {}
  paintGroups();
}, true);

restoreDirs();
paintGroups();
// ── Свіжість сторінки й авто-оновлення ──────────────────────────────────
//
// Три речі, кожна з яких окремо ламає читання, якщо зробити її грубо:
//
// 1. `<meta refresh>` перезавантажив би сторінку посеред читання й скинув
//    прокрутку — тому таймер свій;
// 2. перезавантаження ГУБИТЬ стан: вкладку, фільтри, позицію. Тому перед
//    ним стан кладеться в `sessionStorage` і відновлюється після;
// 3. перезавантаження ПІД РУКАМИ гірше за застарілі дані — тому тільки
//    коли вкладка неактивна або миша не рухалась кілька хвилин.
// Як часто сторінка має перечитуватись. Прогін щогодинний, тож частіше
// сенсу немає — але й рідше не варто, щоб свіжі вакансії не чекали.
const RELOAD_EVERY = 7 * 60 * 1000;
// Скільки людина має не торкатись сторінки, щоб її можна було смикнути.
const IDLE_BEFORE  = 3 * 60 * 1000;
// Коли ця копія сторінки завантажилась. Саме від неї рахується період:
// кожне перезавантаження починає відлік заново, тож окремої змінної
// «коли востаннє перезавантажували» не потрібно.
const LOADED_AT = Date.now();
const KEEP_KEY = 'find_new.reload.v1';

const ageBox = document.getElementById('age');
const generatedAt = ageBox ? new Date(ageBox.dataset.generated) : null;
let lastActivity = Date.now();
for (const kind of ['mousemove', 'keydown', 'click', 'scroll', 'touchstart']){
  document.addEventListener(kind, () => { lastActivity = Date.now(); }, {passive: true});
}

function ageText(minutes){
  if (minutes < 1) return 'оновлено щойно';
  if (minutes < 60) return 'оновлено ' + minutes + ' хв тому';
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return 'оновлено ' + hours + ' год тому';
  const days = Math.floor(hours / 24);
  return 'оновлено ' + days + ' дн тому';
}

function tickAge(){
  if (!generatedAt || isNaN(generatedAt)) return;
  const minutes = Math.floor((Date.now() - generatedAt) / 60000);
  ageBox.textContent = ageText(minutes);
  // Дві межі, а не одна: «давно» і «дуже давно» — різні стани, і другий
  // означає, що збір, найімовірніше, взагалі не працює.
  ageBox.classList.toggle('is-old', minutes >= 90 && minutes < 60 * 6);
  ageBox.classList.toggle('is-stale', minutes >= 60 * 6);
  if (minutes >= 60 * 6) ageBox.title = 'Схоже, збір не запускався — перевірте run.bat';
}

function keepState(){
  try {
    sessionStorage.setItem(KEEP_KEY, JSON.stringify({
      at: Date.now(),
      scroll: window.scrollY,
      tab: tab,
      nav: nav,
      search: q.value,
      onlyNew: onlyNew.checked,
      onlyRes: onlyRes.checked,
    }));
  } catch (e) {}
}

function restoreState(){
  let kept = null;
  try { kept = JSON.parse(sessionStorage.getItem(KEEP_KEY) || 'null'); } catch (e) {}
  try { sessionStorage.removeItem(KEEP_KEY); } catch (e) {}
  // Тільки для щойно зробленого перезавантаження. Через півгодини це вже
  // не «те саме читання», а нове відкриття — і воно має починатись із
  // першої вкладки, як ми й вирішили.
  if (!kept || Date.now() - kept.at > 60000) return;

  if (kept.tab && kept.tab !== tab){
    const button = tabButtons.find(b => b.dataset.tab === kept.tab);
    if (button) button.click();
  }
  if (kept.nav && kept.nav !== 'all'){
    const button = side.querySelector('[data-nav="' + CSS.escape(kept.nav) + '"]');
    if (button) button.click();
  }
  if (kept.search){ q.value = kept.search; }
  onlyNew.checked = !!kept.onlyNew;
  onlyRes.checked = !!kept.onlyRes;
  apply();
  window.scrollTo(0, kept.scroll || 0);
}

function maybeReload(){
  // 🔴 Період перезавантаження й період ПЕРЕВІРКИ — різні речі.
  //
  // Перевіряти треба часто, щоб зловити момент, коли людина відійшла.
  // Перезавантажувати часто — ні: неактивна вкладка інакше смикається
  // щохвилини заради файла, що оновлюється раз на годину.
  //
  // Перша версія мала `RELOAD_EVERY = 7 хв` і не використовувала її
  // ЖОДНОГО разу — перевірка стояла на `setInterval(…, 60000)`, і
  // фактичний період був одна хвилина. Мертва константа з коментарем,
  // що пояснює намір, — гірша за її відсутність: наступний змінив би
  // число й нічого б не сталось.
  if (Date.now() - LOADED_AT < RELOAD_EVERY) return;
  const idle = Date.now() - lastActivity;
  // Читає прямо зараз — не чіпаємо. Застарілі дані дешевші за смикнуту
  // з-під рук сторінку.
  if (!document.hidden && idle < IDLE_BEFORE) return;
  keepState();
  location.reload();
}

if (ageBox){
  tickAge();
  setInterval(tickAge, 30000);
  // Раз на хвилину — це частота ПЕРЕВІРКИ, не перезавантаження.
  setInterval(maybeReload, 60000);
  setTimeout(restoreState, 0);
}

// ── Кнопка «чим оновити» на файловій сторінці ───────────────────────────
// Клік мусить дати ВИДИМУ інструкцію: кнопка, після якої нічого не
// змінюється на екрані, читається як зламана.
const goFile = document.getElementById('go-file');
const howto = document.getElementById('howto');
const howtoPath = document.getElementById('howto-path');
if (howtoPath) howtoPath.textContent = PROJECT_DIR;

if (goFile) goFile.addEventListener('click', () => {
  howto.hidden = !howto.hidden;
  if (!howto.hidden) howto.scrollIntoView({block: 'nearest', behavior: 'smooth'});
});

const howtoCopy = document.getElementById('howto-copy');
if (howtoCopy) howtoCopy.addEventListener('click', () => {
  // 🔴 Видима відповідь НЕ залежить від буфера обміну.
  //
  // Заміряно тут-таки, у Chrome: `navigator.clipboard.writeText()` уміє
  // повиснути — проміс не виконується і не відхиляється (2 с, стан
  // PENDING). Тобто гілка `catch` не спрацьовує теж, і кнопка мовчить:
  // клац — і нічого. Рівно та мовчазна невдача, яку ми ловимо на кожній
  // кнопці.
  //
  // Тому спершу ВИДІЛЯЄМО шлях — це працює завжди, видно оком і дозволяє
  // Ctrl+C, — і лише потім пробуємо буфер. Успіх буфера підвищує підпис,
  // а не створює його.
  const range = document.createRange();
  range.selectNodeContents(howtoPath);
  const selection = window.getSelection();
  selection.removeAllRanges();
  selection.addRange(range);
  howtoCopy.textContent = 'виділено — тисни Ctrl+C';

  try {
    navigator.clipboard.writeText(PROJECT_DIR).then(() => {
      howtoCopy.textContent = 'скопійовано ✓';
    }, () => {});
  } catch (e) {}
  setTimeout(() => { howtoCopy.textContent = 'скопіювати шлях'; }, 4000);
});

// Підсумок останнього збору, що пережив перезавантаження.
const RAN_KEY = 'find_new.ran.v1';
const ranBox = document.getElementById('ran');
(function showRan(){
  if (!ranBox) return;
  let kept = null;
  try { kept = JSON.parse(sessionStorage.getItem(RAN_KEY) || 'null'); } catch (e) {}
  try { sessionStorage.removeItem(RAN_KEY); } catch (e) {}
  // Тільки щойно зроблений збір. Через хвилину цей рядок уже не «результат
  // мого кліку», а спогад — і читався б як свіжий, не будучи ним.
  if (!kept || !kept.text || Date.now() - kept.at > 60000) return;
  ranBox.hidden = false;
  ranBox.textContent = '✓ збір завершено: ' + kept.text;
})();

const rereadButton = document.getElementById('reread');
if (rereadButton) rereadButton.addEventListener('click', () => {
  keepState();
  location.reload();
});

paint();
apply();
connectServer();
"""


SOURCE_LABELS = {"djinni": "Djinni", "dou": "DOU", "happymonday": "HappyMonday",
                 "work_ua": "Work.ua", "jobico": "Jobico",
                 "boards_rss": "Дошки (RSS)",
                 "andersen": "Andersen", "dou_companies": "Компанії DOU"}


# Скільки компаній показувати одразу. Решта — за кнопкою, з видимим числом:
# 127 компаній із однією вакансією зробили б меню довшим за саму стрічку,
# але ховати їх мовчки не можна — власник має бачити, скільки не показано.
COMPANIES_SHOWN = 12


def _health_line(health) -> tuple[str, str]:
    """Рядок стану під назвою джерела: (клас, текст).

    Правило те саме, за яким зроблено Andersen: **порожньо з причиною, а не
    просто порожньо**. Червоний колір повідомляє, що щось не так, і на цьому
    зупиняється — а власнику треба знати, чекати чи лізти в конфіг. Тому в
    рядку стоїть причина текстом і час останнього ВДАЛОГО забору, а не час
    останньої спроби: спроба відбувається щогодини й завжди виглядає свіжою.
    """
    state = getattr(health, "state", "unknown")
    if state == "waiting":
        # Свідомо не забирали цього прогону — це РОБОЧИЙ стан, а не збій і не
        # порожнеча. Показуємо, коли забирали востаннє й скільки тоді було:
        # без цього рядок «чекає» нічим не відрізнявся б від «мовчить третій
        # день», а це протилежні речі.
        was = (f"востаннє {_relative(_parse(health.last_ok))} ({health.last_ok_count})"
               if health.last_ok else "ще не забиралось жодного разу")
        hours = health.wait_hours
        when = (f"{int(hours * 60)} хв" if hours < 1 else f"{hours:.0f} год")
        return "ok", f"{was} · наступний забір через {when}"
    if state == "quiet":
        was = f" · востаннє нове {_relative(_parse(health.last_ok))}" if health.last_ok else ""
        return "ok", f"нічого нового з минулого разу{was}"
    if state == "ok":
        line = f"забрано {health.fetched} · {_relative(_parse(health.last_ok))}"
        # Неповний забір НЕ можна показувати зеленим: помилки не було, число
        # більше нуля — і рядок нічим не відрізняється від повного успіху,
        # хоча частини вакансій ми не бачили.
        if not getattr(health, "complete", True):
            return "warn", line + " · стрічку обрізано стелею сторінок, це не весь фід"
        return "ok", line
    if state == "off":
        return "off", f"вимкнене — {health.note}" if health.note else "вимкнене"
    if state == "failed":
        was = (f" · востаннє вдало {_relative(_parse(health.last_ok))}"
               f" ({health.last_ok_count})" if health.last_ok else "")
        return "bad", f"не забралось: {health.error}{was}"
    if state == "empty":
        # Порожній фід — НЕ помилка: вакансії справді могли закінчитись.
        # Але й не «все гаразд», інакше джерело, яке тихо перестало віддавати,
        # виглядало б як здорове з нулем.
        was = f" · востаннє вдало {_relative(_parse(health.last_ok))}" if health.last_ok else ""
        return "warn", f"фід порожній, помилки не було{was}"
    # Про САМЕ ДЖЕРЕЛО ми тут нічого не знаємо — знаємо лише, що в нашій
    # памʼяті запису немає. Формулювання каже саме це й нічого понад.
    return "warn", "стан ще не записувався"


def _parse(stamp: str) -> datetime | None:
    try:
        return datetime.fromisoformat(stamp)
    except (ValueError, TypeError):
        return None


def _dropped(dropped: list) -> str:
    """Що фільтр викинув цього прогону — згорнутим блоком, із причиною.

    Досі це було видно лише окремою командою в терміналі. Власник править
    `filter.toml` руками й не бачив, що саме втрачає: одного разу ключ
    `arts` мовчки з'їв **сім справжніх вакансій**, і знайшов їх замір, а не
    сторінка.

    Свідомо ЗГОРНУТО: розгорнутий перелік повертає на сторінку той шум, який
    ми з неї прибирали. Але число у заголовку видно завжди — саме воно й
    відповідає на питання «чи фільтр не з'їв зайвого».
    """
    if not dropped:
        return ""
    rows = "".join(
        f'<div class="drop"><span class="drop__t">{_e(vacancy.title)}</span>'
        f'<span class="drop__c">{_e(vacancy.company)}</span>'
        f'<span class="drop__r">{_e(reason)}</span></div>'
        for vacancy, reason in dropped[:60]
    )
    tail = (f'<p class="drop__more">…і ще {len(dropped) - 60}</p>'
            if len(dropped) > 60 else "")
    return (f'<details class="dropped"><summary>Відсіяно фільтром цього прогону · '
            f'<span class="n">{len(dropped)}</span></summary>'
            f'<p class="drop__note">Ключі — у config/filter.toml. Перевірити, '
            f'скільки ловить кожен: <code>python run.py keys</code></p>'
            f'{rows}{tail}</details>')


def _feed_depth(days) -> str:
    """Наскільки глибоко в час сягає фід — за тим, що ми БАЧИЛИ.

    ⚠️ Це наше СПОСТЕРЕЖЕННЯ, а не оголошення джерела, і формулювання це
    каже прямо. Різниця не словесна: на кількох записах число буде
    випадковим, а виглядатиме як властивість фіда. Саме на такій підміні
    ми сьогодні закрили був цілий напрям — заміряли вікно категорійного
    фіда й вирішили, що глибших вакансій не існує.

    Тому: менше трьох записів у заборі — числа немає взагалі (його не рахує
    `pipeline`). Порожнеча тут чесніша за число, у яке повірять.

    Навіщо взагалі: вікно сторінки 14 днів, а `category=Design` тримає ~4.
    Для таких фідів ми показуємо проміжок, ширший за той, що джерело взагалі
    памʼятає, — і порожнеча в ньому читається як «вакансій немає» замість
    «джерело так далеко не бачить».
    """
    if days is None:
        return ""
    if days < 1:
        return "фід сягає менш ніж добу"
    return f"фід сягає {days} дн"


# 🔴 Обʼєднання ЛИШЕ В ПОДАННІ. У коді `dou_companies` лишається окремим
# джерелом із власним розкладом (`every_hours = 24`), власною поведінкою в
# сигналі зникнення й власним записом здоровʼя. Зливати їх у конвеєрі
# означало б зламати добову періодичність заради вигляду меню.
#
# Навіщо: власник спитав «чому два DOU?» — і має рацію. Це той самий сайт,
# різниця лише в тому, ЯК ДАЛЕКО НАЗАД кожен спосіб бачить: категорійний фід
# тримає ~3 доби, фіди компаній — 14-43. Два рядки поруч читались як
# довільне дублювання, бо назви говорили про компанії, а різниця насправді
# в глибині погляду.
DISPLAY_UNDER = {"dou_companies": "dou"}

# Підпис вкладеної групи. ⚠️ Не «сайти компаній»: ми не скрапимо жодного
# сайту компанії, це ті самі фіди DOU, просто по одному роботодавцю.
# Власник просив саме сайти компаній і отримав фіди DOU — назва мусить не
# дати переплутати, як це вже робить рядок «роботодавці зі зібраних
# вакансій, не джерела забору».
SUB_LABELS = {"dou_companies": "окремі компанії"}
SUB_NOTES = {"dou_companies": "ті самі фіди DOU, по одному роботодавцю — "
                              "не сайти компаній"}


def _group_count(top: str, sources: dict) -> int:
    """Скільки карток у джерела разом із тими, що показані під ним."""
    return sources.get(top, 0) + sum(
        sources.get(name, 0) for name, under in DISPLAY_UNDER.items() if under == top
    )


def _broken(health) -> int:
    """Скільки фідів усередині не забралось.

    Потрібне саме для ЗГОРНУТОГО стану: ми щойно робили пофідні рядки, щоб
    падіння одного з пʼяти фідів Djinni не було невидимим. Якщо згорнути й
    на цьому спинитись — повернемо ту саму дірку, тільки за кліком. Тому
    згорнуте не означає непомітне: число проблемних виходить нагору.
    """
    feeds = dict(getattr(health, "feeds", {}) or {})
    return sum(1 for item in feeds.values() if item.get("state") == "failed")


def _feed_word(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return "фід"
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return "фіди"
    return "фідів"


def _source_group(name: str, sources: dict, health: dict, entries: list[Entry]) -> str:
    """Джерело з усім, що показується під ним: власні фіди й вкладені джерела."""
    own = health.get(name)
    css, line = _health_line(own)
    inner = _feed_rows(name, own, entries)
    broken = _broken(own)

    navs = [name] + [sub for sub, under in DISPLAY_UNDER.items() if under == name]
    for sub in navs[1:]:
        sub_health = health.get(sub)
        broken += _broken(sub_health)
        sub_css, sub_line = _health_line(sub_health)
        feeds = len(dict(getattr(sub_health, "feeds", {}) or {}))
        inner += _row(
            key="sub:" + sub,
            nav="source:" + sub,
            label=SUB_LABELS.get(sub, sub),
            count=sources.get(sub, 0),
            state=str(feeds) + " " + _feed_word(feeds) + " · " + sub_line,
            css=sub_css,
            broken=_broken(sub_health),
            inner=_feed_rows(sub, sub_health, entries),
            note=SUB_NOTES.get(sub, ""),
            level=1,
        )

    return _row(key="src:" + name, nav="source:" + ",".join(navs),
                label=SOURCE_LABELS.get(name, name),
                count=_group_count(name, sources),
                state=line, css=css, broken=broken, inner=inner, level=0)


def _row(key: str, nav: str, label: str, count: int, state: str, css: str,
         broken: int, inner: str, note: str = "", level: int = 0) -> str:
    """Рядок меню: клікабельна назва + ОКРЕМА стрілка згортання.

    Дві кнопки, а не одна: назва фільтрує стрічку, стрілка розгортає. Одна
    кнопка на дві дії робила б обидві непередбачуваними.

    Власник не здогадався, що заголовок джерела клікабельний, і тиснув
    окремі компанії. Механізм працював — не працював ВИГЛЯД. Тому назва
    має колір тексту, підкреслення при наведенні й підказку: елемент мусить
    повідомляти про себе ДО кліку, а не після.
    """
    mark = ('<span class="nav__bad" title="фідів не забралось: ' + str(broken)
            + '">\u25cf ' + str(broken) + "</span>") if broken else ""
    if inner:
        caret = ('<button class="nav__toggle" data-toggle="' + _e(key)
                 + '" type="button" aria-expanded="false" title="розгорнути">'
                 + "\u25b8</button>")
    else:
        caret = '<span class="nav__toggle nav__toggle--none"></span>'
    hint = '<span class="nav__note">' + _e(note) + "</span>" if note else ""
    return (
        '<div class="grp grp--l' + str(level) + '" data-grp="' + _e(key) + '">'
        '<div class="nav__row">'
        '<button class="nav nav--src is-' + css + '" data-nav="' + _e(nav) + '" '
        'type="button" title="показати всі вакансії: ' + _e(label) + '">'
        '<span class="nav__top"><span class="nav__name">' + _e(label) + "</span>"
        + mark + '<span class="cnt">' + str(count) + "</span></span>"
        '<span class="nav__state">' + _e(state) + "</span>" + hint + "</button>"
        + caret + "</div>"
        '<div class="nav__feeds" data-body="' + _e(key) + '" hidden>' + inner + "</div>"
        "</div>"
    )


# Борди, які за визначенням айтишні. HappyMonday — загальний, тому компанія,
# знайдена лише там, на IT-бордах відсутня.
IT_BOARDS = frozenset({"djinni", "dou", "dou_companies"})

# Скільки компаній показувати в розгорнутій групі одразу. Решта — за
# кнопкою, з видимим числом: група «З IT-бордів» має 165 компаній, і
# розгортати її повністю означало б повернути ту саму стіну, від якої ми
# щойно пішли.
GROUP_SHOWN = 12


def _slug_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def watched_companies(health: dict, aliases: dict,
                      kinds: dict[str, str] | None = None) -> dict[str, str]:
    """Компанії, за якими стежимо окремим фідом: {назва: слаг}.

    Назву беремо із заголовка фіда («Вакансії «X» на DOU.ua»), бо вона є
    навіть у ПОРОЖНЬОГО фіда — а слаг (`at-oschadbank`) назвою не є.

    🔴 `aliases` існує, бо той самий банк зветься по-різному в різних
    джерелах: `ПриватБанк` у фіді DOU й `PrivatBank` у вакансії з Djinni.
    Без цього в переліку були б два рядки на один банк. Заміряно по всіх
    41 слагу: розбіжностей рівно дві.

    🔴 Джерела беруться за ВИДОМ, а не поіменно. Тут стояло
    `health.get("dou_companies")` — і правило «нуль не привід зникати»
    мовчки не діяло на жодне наступне джерело того ж виду. Перевірено на
    recruitee: його компанії в перелік не потрапляли зовсім, тобто Clario
    без дизайнерських вакансій просто не існувала б для власника, хоча ми
    щодоби читаємо її фід. Вид оголошує сам адаптер (`kind = "company"`),
    тому наступне джерело підхопиться без правок тут.
    """
    kinds = kinds or {}
    names = {}
    for name, item in (health or {}).items():
        # Без переданих видів поводимось як раніше — інакше та сама функція
        # мовчки міняла б поведінку в тих викликах, куди види ще не дійшли.
        if kinds.get(name) != "company" if kinds else name != "dou_companies":
            continue
        for slug, feed in (getattr(item, "feeds", {}) or {}).items():
            title = (aliases or {}).get(slug) or (feed.get("title") or "").strip()
            if title:
                names[title] = slug
    return names


def company_groups(entries: list[Entry], watched: dict, banking: list[str]):
    """→ [(ключ, підпис, [(компанія, чи під наглядом)])]. Групи перетинаються.

    🔴 У переліку компанії ДВОХ РІЗНИХ ПРИРОД, і плутати їх не можна:

    * **під наглядом** — ми читаємо їхній фід окремо, тож нову вакансію
      побачимо того ж дня. Показуються навіть із нулем: «нуль сьогодні не
      привід не стежити» — правило власника, повторене пʼять разів;
    * **трапилась** — роботодавець потрапив у стрічку сам, через
      категорійний фід. Завтра може зникнути, і ми цього не помітимо.

    Різниця для власника істотна, тому вона позначена в самому рядку, а не
    сховано в підписі розділу.

    Назви груп кажуть, ДЕ ми знайшли компанію, а не якої вона галузі: поля
    галузі джерела не дають, і стверджувати класифікацію, якої ми не
    робили, не можна.
    """
    sources: dict[str, set[str]] = {}
    for entry in entries:
        name = entry.vacancy.company
        if name:
            sources.setdefault(name, set()).add(entry.vacancy.source)

    bank_slugs = {s.lower() for s in (banking or [])}
    watched_names = set(watched)

    def mark(names):
        return sorted(((n, n in watched_names) for n in names),
                      key=lambda pair: (not pair[1], pair[0].lower()))

    banks = [n for n, slug in watched.items() if slug in bank_slugs]
    # Топ-50 — це решта слагів під наглядом: перелік будувався зі статті,
    # банки додані пізніше окремим блоком.
    top = [n for n, slug in watched.items() if slug not in bank_slugs]

    on_it = [n for n, src in sources.items() if src & IT_BOARDS]
    on_it += [n for n in watched_names if n not in sources]
    off_it = [n for n, src in sources.items() if not (src & IT_BOARDS)]

    return [
        ("top50", "Топ-50 DOU", mark(top)),
        ("bank", "Банкінг", mark(banks)),
        ("onit", "З IT-бордів", mark(on_it)),
        ("offit", "Поза IT-бордами", mark(off_it)),
    ]


def _company_rows(names: list, counts: dict, key: str) -> str:
    rows = ""
    for index, (name, watched) in enumerate(names):
        # Маркер нагляду стоїть у рядку, а не в підписі розділу: різниця
        # між «побачимо того ж дня» і «трапилась випадково» стосується
        # конкретної компанії, а не всього переліку.
        eye = ('<span class="nav__eye" title="стежимо за цією компанією окремим '
               'фідом — нову вакансію побачимо того ж дня">◉</span>'
               if watched else "")
        rows += (
            f'<button class="nav nav--company{"" if index < GROUP_SHOWN else " is-tail"}" '
            f'data-nav="company:{_e(name.lower())}" type="button">'
            f'{eye}{_e(name)}<span class="cnt">{counts.get(name, 0)}</span></button>'
        )
    tail = len(names) - GROUP_SHOWN
    if tail > 0:
        rows += (f'<button class="side__more" data-more="{_e(key)}" type="button">'
                 f'ще {tail}</button>')
    return rows


def _company_group_block(entries: list[Entry], counts: dict[str, int],
                         watched: dict, banking: list[str]) -> str:
    """Групи всередині розділу «Компанії у стрічці» — другий рівень згортання."""
    blocks = ""
    for key, label, names in company_groups(entries, watched, banking):
        body = _company_rows(names, counts, key)
        # Порожня група лишається видимою з нулем: зникнення рядка ми
        # сигналом не вважаємо, і тут це те саме правило.
        blocks += (
            f'<div class="grp grp--l1 cgroup" data-grp="cg:{key}">'
            f'<div class="nav__row">'
            f'<button class="nav nav--src" data-nav="cgroup:{key}" type="button" '
            f'title="показати вакансії групи: {_e(label)}">'
            f'<span class="nav__top"><span class="nav__name">{_e(label)}</span>'
            f'<span class="cnt">{len(names)}</span></span></button>'
            f'<button class="nav__toggle" data-toggle="cg:{key}" type="button" '
            f'aria-expanded="false" title="розгорнути">\u25b8</button>'
            f'</div>'
            f'<div class="nav__feeds" data-body="cg:{key}" hidden>{body}</div>'
            f'</div>'
        )
    return blocks


def _feed_rows(source: str, health, entries: list[Entry]) -> str:
    """Окремі фіди всередині джерела — по рядку на кожен.

    Для `dou_companies` один фід = одна компанія, і власник просив саме
    «список сторінок, які ми скрапимо». Джерело з 21 фіда, де впав один,
    на рівні джерела виглядає цілком здоровим — це та сама поломка, яку ми
    щойно полагодили поверхом вище, тільки всередині адаптера.

    🔴 Нуль НЕ ховаємо. Шість компаній із двадцяти однієї не мають зараз
    відкритих вакансій, і власник тричі підтвердив: нуль сьогодні не привід
    зникати з переліку. Компанія, яку прибрали з переліку, і компанія без
    вакансій виглядають однаково — саме тому нуль мусить бути видимий.
    """
    feeds = dict(getattr(health, "feeds", {}) or {})
    if not feeds:
        return ""
    shown: dict[str, int] = {}
    for entry in entries:
        if entry.vacancy.source == source:
            key = entry.vacancy.feed
            shown[key] = shown.get(key, 0) + 1

    rows = ""
    # Спершу ті, що дали вакансії у стрічку, далі решта за розміром фіда.
    order = sorted(feeds.items(), key=lambda kv: (-shown.get(kv[0], 0),
                                                  -kv[1].get("count", 0), kv[0]))
    for slug, item in order:
        state = item.get("state", "")
        count = item.get("count", 0)
        # ⚠️ ДВІ РІЗНІ ВЕЛИЧИНИ, і «з N» їх плутало: ліворуч — картки цього
        # фіда на сторінці (вікно 14 днів, беруться з бази), праворуч — що
        # фід віддав ОСТАННІМ забором. Ліве буває більшим за праве: вакансія
        # ще у вікні, але з фіда вже випала. «55 з 54» читалось як підмножина
        # і виглядало помилкою — рівно та підміна сусідньої величини, яку ми
        # ловимо всю сесію, тільки цього разу в підписі.
        if state == "failed":
            note = item.get("error", "не забралось")
        elif state == "quiet":
            note = "нічого нового з минулого разу"
        elif not count:
            note = "відкритих вакансій немає"
        else:
            note = f"у фіді зараз {count}"
        depth = _feed_depth(item.get("depth_days"))
        if depth:
            note += f" · {depth}"
        rows += (
            f'<button class="nav nav--feed{" is-bad" if state == "failed" else ""}" '
            f'data-nav="feed:{_e(slug)}" type="button">{_e(slug)}'
            f'<span class="cnt">{shown.get(slug, 0)}</span>'
            f'<span class="nav__sub">{_e(note)}</span></button>'
        )

    # 🔴 Сума рядків мусить сходитись із числом джерела — або розбіжність
    # мусить бути НАЗВАНА.
    #
    # Знайдено на живих даних: HappyMonday показував 19 карток, а його єдиний
    # фід — нуль. Ті 19 записів збережені ДО того, як адаптер почав ставити
    # мітку фіда, тож у них `feed` порожній і в жоден рядок вони не потрапляли.
    # Числа не сходились, і ніщо про це не казало: розбивка виглядала повною,
    # просто мовчки не покривала третину джерела.
    # Рахуємо проти ВИВЕДЕНИХ рядків, а не проти всіх карток джерела. Перша
    # версія віднімала `sum(shown.values())`, куди входив і сам безмітковий
    # ключ — залишок виходив нуль, і запобіжник мовчав саме там, де мав
    # спрацювати. Той самий клас, що ловимо весь день, тільки в перевірці.
    counted = sum(shown.get(slug, 0) for slug in feeds)
    leftover = sum(1 for e in entries if e.vacancy.source == source) - counted
    if leftover > 0:
        rows += (
            f'<button class="nav nav--feed is-warn" data-nav="feed:" type="button">'
            f'без мітки фіда<span class="cnt">{leftover}</span>'
            f'<span class="nav__sub">записи з попередніх версій, коли джерело '
            f'ще не мітило фід</span></button>'
        )
    return rows


def _sidebar(entries: list[Entry], kinds: dict[str, str], health: dict,
             slugs: list[str] | None = None,
             banking: list[str] | None = None,
             aliases: dict | None = None) -> str:
    """Бокове меню: звідки тягнемо і які компанії у стрічці.

    ⚠️ Два розділи навмисно названі так, щоб їх не можна було переплутати.
    Прохання власника звучало як «сайти компаній, з яких іде скрапінг» —
    але ми не скрапимо жодного сайту компанії: три джерела це джоб-борди,
    а єдиний власний сайт (Andersen) вимкнений. Підпис «Компанії у стрічці»
    каже, що це РОБОТОДАВЦІ з уже зібраних вакансій, а не джерела забору.
    Якби тут стояло «сайти компаній», число 156 читалось би як «156 сайтів
    під скрапінгом» — і ми ввели б в оману видачею, а не словами.
    """
    sources: dict[str, int] = {}
    companies: dict[str, int] = {}
    for entry in entries:
        sources[entry.vacancy.source] = sources.get(entry.vacancy.source, 0) + 1
        if entry.vacancy.company:
            companies[entry.vacancy.company] = companies.get(entry.vacancy.company, 0) + 1

    # 🔴 Перелік будується з ПЕРЕЛІКУ ДЖЕРЕЛ, а не зі зібраних вакансій.
    #
    # Раніше рядок джерела існував лише доти, доки джерело щось віддавало:
    # Djinni падає → рядок `Djinni 169` зникає → лишаються DOU й HappyMonday
    # зі своїми здоровими числами, і сторінка виглядає бездоганно. Дві
    # третини стрічки зникають без жодного сліду в інтерфейсі.
    #
    # Тепер рядок є завжди, змінюється тільки його стан. Це те саме правило,
    # яке ми вже застосували до дайджесту: **відсутність рядка сигналом не є.**
    # 🔴 Перелік будується з ПЕРЕЛІКУ ДЖЕРЕЛ, а не зі зібраних вакансій.
    #
    # Раніше рядок джерела існував лише доти, доки джерело щось віддавало:
    # Djinni падає → рядок `Djinni 169` зникає → лишаються DOU й HappyMonday
    # зі своїми здоровими числами, і сторінка виглядає бездоганно. Дві
    # третини стрічки зникають без жодного сліду в інтерфейсі.
    #
    # Тепер рядок є завжди, змінюється тільки його стан.
    names = list(health) or list(sources)
    tops = [name for name in names if name not in DISPLAY_UNDER]

    source_rows = ""
    for name in sorted(tops, key=lambda n: (-_group_count(n, sources), n)):
        source_rows += _source_group(name, sources, health, entries)
    off_rows = ""

    # Сортування за кількістю, не за алфавітом: компанія з 13 вакансіями
    # важливіша за 127 одиничних, і око має зустріти її першою.
    watched = watched_companies(health, aliases or {}, kinds)
    company_rows = _company_group_block(entries, companies, watched, banking or [])

    return f"""<aside class="side">
  <div class="srv">
    <!-- Кнопка збору звідси ПЕРЕЇХАЛА в шапку, до заголовка. Тут вона стояла
         під списком джерел, у дрібному шрифті бокового меню, і власник її не
         бачив. Місце головної дії — біля заголовка, а не в довіднику. -->
    <p class="srv__note" id="srv-note" hidden></p>
    <details class="srv__add">
      <summary>додати джерело</summary>
      <select id="srv-kind">
        <option value="dou_company">компанія на DOU (слаг)</option>
        <option value="dou_category">категорія DOU</option>
      </select>
      <input id="srv-value" type="text" placeholder="напр. squad" autocomplete="off"
             spellcheck="false">
      <div class="srv__row">
        <button id="srv-check" type="button">перевірити</button>
        <button id="srv-add" type="button" disabled>зберегти</button>
      </div>
      <p class="srv__result" id="srv-result" hidden></p>
      <p class="srv__hint">Довільний URL не приймається: адаптера під нього
        немає, і кнопка обіцяла б неможливе. Записується у filter.toml —
        він лишається місцем правди.</p>
    </details>
  </div>

  <button class="nav nav--reset is-on" data-nav="all" type="button">Усі вакансії
    <span class="cnt">{len(entries)}</span></button>

  <div class="side__group">
    <h3>Звідки тягнемо</h3>
    <p class="side__note">числа — скільки карток на сторінці; скільки показано
      зараз після фільтрів, видно вгорі. Дрібним — стан останнього забору</p>
    {source_rows}{off_rows}
  </div>

  <div class="side__group grp" data-grp="companies">
    <div class="nav__row">
      <button class="side__head nav--head" data-nav="all" type="button"
              title="показати вакансії всіх компаній">Компанії
        <span class="cnt">{len(companies)}</span></button>
      <button class="nav__toggle" data-toggle="companies" type="button"
              aria-expanded="false" title="розгорнути">\u25b8</button>
    </div>
    <!-- Підпис переписаний 31.07.2026. Було «роботодавці зі зібраних
         вакансій» — після того, як у перелік додались компанії під наглядом
         із нулем, це стало неправдою: там є й ті, чиїх вакансій у стрічці
         немає. Підпис мусив змінитись разом із вмістом. -->
    <p class="side__note"><b>◉ — стежимо окремо</b>, нову вакансію побачимо
      того ж дня; без значка — роботодавець трапився у стрічці сам і завтра
      може зникнути. Групи кажуть, <b>де ми знайшли</b> компанію, а не якої
      вона галузі — поля галузі джерела не дають</p>
    <!-- Активний фільтр мусить бути видно ЗВІДКИ ЗАВГОДНО, включно зі
         згорнутим розділом: інакше людина бачить коротку стрічку й не має
         на екрані нічого, що пояснює чому. Це те саме правило, що з
         порожнім результатом — показувати причину, а не лише наслідок. -->
    <p class="side__active" id="company-active" hidden></p>
    <!-- Поле пошуку лишається ЗОВНІ згорнутого блоку. Інакше знайти компанію
         коштувало б два кліки замість нуля: спершу розгорни, потім шукай —
         тобто ми полагодили б довжину меню й зламали головний сценарій.
         Введення саме розгортає перелік, очищення повертає збережений стан. -->
    <input id="company-q" type="search" placeholder="знайти компанію…"
           autocomplete="off" spellcheck="false">
    <div class="nav__feeds side__body" data-body="companies" hidden>
      <div id="company-list">{company_rows}</div>
      <p class="side__empty" id="company-empty" hidden>Такої компанії у стрічці немає</p>
    </div>
  </div>
</aside>"""


def _directions_menu(entries: list[Entry], order: list[str],
                     defaults: list[str] | None = None) -> str:
    """Список напрямів із мультивибором. Усі увімкнені за замовчуванням.

    Семантика зняття галочки — АБО: вакансія лишається, якщо в неї є ХОЧ
    ОДИН обраний напрям. Це вибрано за напрямком помилки: «АБО» помиляється
    в бік зайвої картки (прогорнути — секунда), а «виключення має пріоритет»
    — у бік прихованої вакансії. `Graphic/Motion Designer` наполовину те,
    що потрібно, і ховати його через другу половину неправильно.

    «Інше» стоїть у списку нарівні з рештою й теж увімкнене: там
    `Design System Lead` і `Growth Designer`, тобто саме те, що шукають.
    """
    counts: dict[str, int] = {}
    for entry in entries:
        for name in entry.directions:
            counts[name] = counts.get(name, 0) + 1

    # Дефолт приходить із конфіга. Порожній перелік означає «усі» — так
    # поводилась сторінка досі, і мовчазна зміна цієї поведінки була б
    # гіршою за явний перелік.
    chosen = set(defaults) if defaults else set(order)
    rows = "".join(
        f'<label class="dirs__row"><input type="checkbox" data-dir="{_e(name)}"'
        f'{" checked" if name in chosen else ""}>'
        f'<span>{_e(name)}</span><span class="cnt">{counts.get(name, 0)}</span></label>'
        for name in order
    )
    return (
        '<details class="dirs"><summary>Напрям <span class="cnt" id="dirs-state">усі</span>'
        "</summary>"
        f'<div class="dirs__menu">{rows}'
        '<div class="dirs__all"><button type="button" id="dirs-all">усі</button>'
        '<button type="button" id="dirs-none">жодного</button></div>'
        # 🔴 Живе число стоїть ВСЕРЕДИНІ панелі, а не лише вгорі сторінки.
        #
        # Механізм оновлення працював і раніше — стрічка перемальовується
        # миттєво. Але результат мінявся ЗА відкритою панеллю: числа біля
        # груп статичні (це розмір групи, і ми лишаємо їх такими), заголовок
        # каже «3 з 8» — тобто скільки груп обрано, а не скільки вакансій
        # буде. У самій панелі не рухалось нічого, і клік не давав жодного
        # підтвердження, що дія сталась.
        #
        # Правило: зворотний звʼязок мусить бути в полі зору того, хто діє.
        '<div class="dirs__live" id="dirs-live"></div>'
        "</div></details>"
    )


def build(
    entries: list[Entry],
    generated_at: datetime | None = None,
    sources: list[str] | None = None,
    kinds: dict[str, str] | None = None,
    direction_order: list[str] | None = None,
    health: dict | None = None,
    serve_url: str = "",
    dropped: list | None = None,
    direction_defaults: list | None = None,
    slugs: list | None = None,
    banking: list | None = None,
    aliases: dict | None = None,
) -> str:
    generated_at = generated_at or datetime.now(timezone.utc)
    health = health or {}
    # Ті самі формулювання, що в боковому меню, — беруться з однієї функції,
    # щоб рядок стану й пояснення порожнього екрана не могли розійтись.
    # Адреса сервера друкується в сторінку з конфіга: файлова версія має
    # знати, куди віддавати позначки, а порт власник міг змінити.
    serve_url_json = json.dumps(serve_url or "http://127.0.0.1:8765")
    # Тека проєкту друкується в сторінку: підказка «запусти run.bat» без
    # шляху змушує шукати теку руками. Пакет лежить у <проєкт>/jobfeed/,
    # тому батько батька — сама тека.
    project_dir_json = json.dumps(str(Path(__file__).resolve().parent.parent))
    feed_state_json = json.dumps(
        {
            slug: (
                f"{slug}: фід не забрався — {item.get('error', '')}"
                if item.get("state") == "failed"
                else f"У {slug} зараз немає відкритих вакансій на DOU."
                if not item.get("count")
                else f"У {slug} зараз {item.get('count')} відкритих вакансій, "
                     "але жодна не проходить фільтр — дизайну серед них немає."
            )
            for item_health in health.values()
            for slug, item in (getattr(item_health, "feeds", {}) or {}).items()
        },
        ensure_ascii=False,
    )
    state_json = json.dumps(
        {
            name: {
                "label": SOURCE_LABELS.get(name, name),
                "line": _health_line(item)[1],
            }
            for name, item in health.items()
        },
        ensure_ascii=False,
    )
    # «Ймовірно закриті» їдуть в окрему згорнуту секцію, а не зникають.
    # Це наш ВИСНОВОК (джерело перестало показувати вакансію), а не факт —
    # і те саме правило, що з дублями: не ховаємо запис, у якому не впевнені.
    closed = [e for e in entries if e.likely_closed]
    live = [e for e in entries if not e.likely_closed]
    # ⚠️ Нормалізуємо ДО першого використання. Раніше рядок стояв нижче, і
    # це було нешкідливо, поки `kinds` впливали лише на бейдж картки. Тепер
    # від них залежить, які компанії вважати наглядовими, — а `None` тут
    # означав би, що бічне меню й дані для JS рахують їх по-різному.
    kinds = kinds or {}
    # Назви компаній у нижньому регістрі — так само, як `data-company` на
    # картці, інакше збіг не спрацює на жодній назві з великою літерою.
    watched_json = json.dumps(
        {name.lower(): slug for name, slug
         in watched_companies(health or {}, aliases or {}, kinds).items()},
        ensure_ascii=False,
    )
    company_groups_json = json.dumps(
        {key: [name.lower() for name in names]
         for key, names in ((k, [name for name, _ in n]) for k, _, n in
                            company_groups(live, watched_companies(health or {},
                                                                   aliases or {},
                                                                   kinds),
                                           banking or []))},
        ensure_ascii=False,
    )

    fresh = [e for e in live if e.is_new]
    older = [e for e in live if not e.is_new]
    reserved = sum(
        1 for e in entries if e.reservation is not None and e.reservation.state == YES
    )

    sections = []
    if fresh:
        sections.append(
            f'<section><h2>Нове з минулого запуску · <span class="n">{len(fresh)}</span></h2>'
            + "".join(_card(e, kinds, kinds.get(e.vacancy.source, "board")) for e in fresh)
            + "</section>"
        )
    if older:
        sections.append(
            f'<section><h2>Раніше · <span class="n">{len(older)}</span></h2>'
            + "".join(_card(e, kinds, kinds.get(e.vacancy.source, "board")) for e in older)
            + "</section>"
        )
    dropped_block = _dropped(dropped or [])
    if closed:
        sections.append(
            '<details class="closed"><summary>Ймовірно закриті · '
            f'<span class="n">{len(closed)}</span>'
            "<em>джерело перестало їх показувати — можливо, вакансію зняли</em>"
            "</summary>"
            + "".join(_card(e, kinds, kinds.get(e.vacancy.source, "board")) for e in closed)
            + "</details>"
        )

    if not sections:
        sections.append(
            '<div class="empty">Поки порожньо. Прогін ще не знайшов вакансій, '
            "що проходять фільтр.</div>"
        )

    stamp = generated_at.astimezone().strftime("%d.%m.%Y, %H:%M")
    source_note = ", ".join(sources or []) or "—"

    return f"""<!doctype html>
<html lang="uk">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>find_new</title>
<style>{CSS}</style>
<body>
<div class="shell">
{_sidebar(live, kinds, health, slugs, banking, aliases)}

<div class="main">
  <header>
    <div class="head">
      <h1>Вакансії</h1>
      <!-- 🔴 ДВІ кнопки, а не одна на два режими.
           Під сервером збір справді запускається — там кнопка робить рівно
           те, що обіцяє назвою. З файла це неможливо: браузер не запускає
           програм. Тому там стоїть кнопка з ІНШОЮ назвою, яка не вдає збір,
           а показує, чим його запустити.
           Однакове місце й розмір — щоб не здавалось, що чогось бракує;
           різні назви — щоб не здавалось, що вони роблять одне. -->
      <button id="srv-refresh" class="go go--real" type="button" hidden
              title="Запустити збір вакансій просто зараз">оновити вакансії</button>
      <button id="go-file" class="go go--file" type="button"
              title="Показати, чим запускається збір">чим оновити вакансії</button>
    </div>
    <!-- 🔴 Результат збору ПЕРЕЖИВАЄ перезавантаження.
         Заміряно: прогін триває ~2 с, а сторінка після нього
         перезавантажується — тобто підпис «зібрано 210 · нових 0» видно
         близько секунди й він зникає разом зі старим DOM. Кнопка виглядала
         б так, ніби нічого не сталося, хоча сталося все.
         Тому підсумок кладеться в sessionStorage і показується вже на
         НОВІЙ сторінці — там, де його встигнуть прочитати. -->
    <p class="ran" id="ran" hidden></p>
    <div class="howto" id="howto" hidden>
      <b>Збір запускає окрема програма — браузер її запустити не може.</b>
      <div class="howto__row">
        <code id="howto-path"></code>
        <button id="howto-copy" type="button">скопіювати шлях</button>
      </div>
      <p>Відкрий цю теку й клацни двічі:<br>
         <code>run.bat</code> — один збір просто зараз;<br>
         <code>serve.bat</code> — та сама сторінка, але з робочою кнопкою збору;<br>
         <code>schedule.bat</code> — збір щогодини сам, без кнопок.</p>
    </div>
    <div class="sub">
      <!-- Вік сторінки цокає сам, без перезавантаження: інакше «оновлено
           щойно» лишалось би написаним і через шість годин. Власник уже
           одного разу відкрив учорашню сторінку й вирішив, що вакансій
           немає — рядок мусить старіти на очах. -->
      <span id="age" class="age" data-generated="{generated_at.isoformat()}"
            title="Згенеровано {_e(stamp)}">оновлено щойно</span>
      · джерела: {_e(source_note)} ·
      показано <span id="shown">{len(live)}</span> з {len(live)}
      <!-- ⚠️ Кнопка перечитує СТОРІНКУ, і назва каже саме це. Кнопку, що
           «оновлює вакансії», ми колись прибрали саме тому, що з файла
           запустити збір неможливо, а кнопка обіцяла б це.

           🔴 Стоїть ОКРЕМИМ рядком, а не в кінці рядка метаданих. Причина не
           косметична: у потоці тексту її видимість залежала б від довжини
           того, що стоїть перед нею — при трьох джерелах влізло б, при
           пʼятьох ні. Власник надіслав скриншот, де її не було видно.
           Відтворити обрізання я не змогла на жодній ширині від 1120 до
           520 px, але сама залежність реальна, і прибрати її дешевше, ніж
           доводити, що вона не спрацює. -->
      <div class="sub__row">
        <button id="reread" type="button" class="reread"
                title="Перечитати файл digest.html з диска">перечитати сторінку</button>
        <span class="sub__hint">вакансії збирає <code>run.bat</code> — сторінка
          лише показує те, що він зібрав</span>
      </div>
    </div>
  </header>

  <div class="bar">
    <input id="q" type="search" placeholder="Пошук за посадою, компанією, містом…"
           autocomplete="off" spellcheck="false">
    <label class="toggle"><input id="only-new" type="checkbox"> тільки нові</label>
    <label class="toggle"><input id="only-res" type="checkbox"> 🛡 з бронню
      <span class="cnt">{reserved}</span></label>
    {_directions_menu(live, direction_order or [], direction_defaults)}
  </div>


  <!-- Вісь ЙОГО стану, а не ще один фільтр. Три стани взаємовиключні:
       вакансія або чекає розгляду, або він відгукнувся, або відкинув.
       Перемикачі це дозволяли б безглузді комбінації (сховати обидва — і
       що тоді?), вкладки — ні.

       Замінили саме ПЕРЕМИКАЧІ, а не додались до них: два механізми для
       одного стану створюють друге місце, де шукати причину, чому вакансії
       не видно. -->
  <div class="tabs" role="tablist">
    <button class="tab is-on" data-tab="todo" type="button">До розгляду
      <span class="cnt" id="tab-todo">0</span></button>
    <button class="tab" data-tab="applied" type="button">Відгукнувся
      <span class="cnt" id="tab-applied">0</span></button>
    <button class="tab" data-tab="skipped" type="button">Не цікавить
      <span class="cnt" id="tab-skipped">0</span></button>
  </div>

  {"".join(sections)}
  <div class="empty" id="empty" hidden>Нічого не знайшлось.</div>
  {dropped_block}

  <div class="keep">
    <span class="keep__local"><b>Твої позначки зберігаються лише в цьому
      браузері.</b> Очищення його даних або перехід на інший компʼютер їх
      зітре — тому час від часу зберігай копію.</span>
    <span class="keep__served"><b>Позначки зберігаються в базі.</b>
      Цей браузер тримає копію, щоб вони були видні й із вимкненим сервером.</span>
    <button id="export" type="button">Експортувати позначки</button>
    <label>Імпортувати<input id="import" type="file" accept="application/json"></label>
  </div>

  <footer>
    Сторінка генерується локально при кожному прогоні. Дані — з фідів джерел як є:
    чого джерело не дало, того тут немає, а не «немає взагалі».
    <br>
    Позначка про бронювання ставиться лише там, де про це прямо написано в тексті
    вакансії. <b>«Бронь не згадана» означає, що в оголошенні про неї не сказано, —
    а не що компанія її не робить.</b> Багато компаній бронюють і не пишуть про це.
  </footer>
</div>
</div>
<script>
// Стан джерел для порожнього екрана: коли фільтр по джерелу нічого не дав,
// сторінка мусить назвати ПРИЧИНУ, а не лише результат.
const COMPANY_GROUPS = {company_groups_json};
const WATCHED = {watched_json};
const SERVE_URL = {serve_url_json};
const PROJECT_DIR = {project_dir_json};
const SOURCE_STATE = {state_json};
const FEED_STATE = {feed_state_json};
</script>
<script>{JS}</script>
</body>
</html>
"""


def to_json(entries: list[Entry]) -> str:
    return json.dumps([e.as_dict() for e in entries], ensure_ascii=False, indent=2)
