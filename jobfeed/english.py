"""Рівень англійської з тіла вакансії — тільки коли впевнені.

Чому не «є слово рівня + є слово english»
-----------------------------------------
Заміряно на 224 живих вакансіях:

    згадують англійську                                102
    рівень ПОРУЧ зі згадкою (±60 символів)              66
    обидва слова є, але далеко один від одного          79 - 66 = 13

Тобто наївний пошук по всьому опису дав би 79 — і **13 вакансій отримали б
рівень із чужого контексту**. `Advanced Photoshop` в одному абзаці й згадка
англійської в іншому склались би в «англійська Advanced».

Звідки саме ±60
---------------
Заміряно по вікнах, не взято з голови:

    ±20 → 47    ±40 → 63    ±60 → 66    ±80 → 66
    ±120 → 67   ±200 → 69   весь опис → 79

Приріст вигасає на 60 і тримається плоским до 80; далі знову росте — але
росте вже за рахунок далеких збігів, тобто того самого шуму. Беремо початок
плато.

Три речі, які тут легко зробити неправильно
-------------------------------------------
1. **Діапазон і «або вище» — це МІНІМУМ, а не точка.** `B1–B2`,
   `B2 level or above`, `Intermediate+` — якщо звести до однієї точки,
   `B1–B2` стане `B1`, і власник відкине вакансію, яка йому підходить.
   Тому діапазон лишається діапазоном, а «or above» стає видимим `+`.

2. **Відсутність рівня ≠ англійська не потрібна.** 102 згадують, рівень
   поруч лише в 66. Решта пишуть «English is a must» без рівня. Чип
   зʼявляється лише коли впевнені, і його відсутність нічого не стверджує —
   те саме правило, що з бронню: `не згадано` ≠ `немає`.

3. **Дві шкали не зводимо в одну.** У даних і CEFR (`B2`), і словесна
   (`Upper-Intermediate`). Вони приблизно рівні — але «приблизно» в чипі
   стає твердженням. Показуємо так, як написано в джерелі.
"""

from __future__ import annotations

import re

# Вікно — див. замір у докстрінгу модуля.
WINDOW = 60

_ENGLISH = re.compile(r"англійськ\w*|english|англ\.", re.IGNORECASE)

# Рівні як їх пишуть роботодавці. Порядок важливий: довші варіанти перші,
# інакше `upper-intermediate` розпадеться на `intermediate`.
#
# 🔴 Слова поділені на ДВА КЛАСИ, і це не стилістика.
#
# `B2`, `Intermediate` — шкала: вони майже не бувають ні про що, крім мови.
# `proficiency`, `fluent`, `advanced`, `native` — загальні слова, які
# чіпляються до будь-якої навички: `Proficiency in Figma`, `Advanced
# Photoshop`. На живих описах хибний рівень давали саме вони.
#
# Звідси два правила в `detect`:
#   1. якщо у вікні є слово ЗІ ШКАЛИ — беремо його, а не загальне. Інакше
#      `English proficiency at B1 level or higher` показувалось як
#      «Proficiency+», хоча роботодавець написав B1;
#   2. загальне слово зараховуємо, лише коли воно впритул до згадки мови.
_HARD = (r"upper[\s-]?intermediate|pre[\s-]?intermediate|"
         r"intermediate|elementary|beginner|[abc][12]")
# `good` теж сюди, а не у шкалу: воно дає ті самі 4 вакансії (заміряно), але
# як загальне слово проходить суворішу перевірку на прикріплення. `good
# communication skills` поруч зі згадкою мови не стане рівнем.
_SOFT = (r"proficiency|proficient|fluent|native|advanced|good|"
         r"добр\w*|хорош\w*")

_LEVEL = re.compile(rf"\b({_HARD}|{_SOFT})\b", re.IGNORECASE)
_IS_SOFT = re.compile(rf"^({_SOFT})$", re.IGNORECASE)

# 🔴 Загальне слово мусить НАЛЕЖАТИ згадці мови, а не просто стояти поруч.
#
# Відстані замало, і це знайдено на живих даних:
#
#     «Good spoken and written English for fluent communication»
#                                            ↑ це про communication, не про мову
#
# Тут мова стоїть ЗА ЧОТИРИ СИМВОЛИ — будь-яка перевірка на близькість це
# зараховує. Різниця не у відстані, а в тому, до чого слово прикріплене.
#
# Тому між загальним словом і згадкою мови дозволені лише службові слова:
# `fluent in English`, `Fluent spoken English`, `proficiency in English` —
# так; `English for fluent communication` — ні, бо `for` вводить новий
# іменник, і слово вже про нього.
_CONNECTOR = re.compile(
    r"^[\s,:—–-]*"
    r"(?:(?:in|of|at|on|level|spoken|written|oral|verbal|both|and|та|і|з|у|на|"
    r"мов\w*|рівн\w*|усн\w*|письмов\w*)[\s,:—–-]*)*$",
    re.IGNORECASE,
)

# «або вище» в усіх формах, які трапляються. `+` теж: `Intermediate+`.
_AT_LEAST = re.compile(
    r"(or\s+(above|higher|better)|and\s+(above|higher)|\+|"
    r"(та|і|й)\s+вище|не\s+нижче|мінімум|at\s+least|min\.?)",
    re.IGNORECASE,
)

# 🔴 Межа пункту списку. Вікна ±60 НЕ досить: заміряно на живих даних, що
# `Strong English skills are mandatory for client communication ○ Proficiency
# in Figma` дає «англійська Proficiency» — слово належить НАСТУПНОМУ пункту.
# Це рівно та хибна атрибуція, проти якої існує вікно, тільки вона пролазить
# усередині вікна.
#
# Набір роздільників звужений заміром, а не здогадом: `—`, `:` і `,` сюди НЕ
# входять, бо ними пишуть сам рівень (`English — B2`, `English: Intermediate+`,
# `Рівень англійської мови — мінімум розмовний B1`). Ширший набір відкидав
# 14 вакансій, з них законних більшість.
_BREAK = re.compile(r"[○•▪|]|\.\s")

# Роздільники діапазону: `B1-B2`, `B1–B2`, `B1 to B2`, `B1/B2`.
_RANGE_GAP = re.compile(r"^[\s]*(?:[-–—/]|to|до)[\s]*$", re.IGNORECASE)


def _tidy(raw: str) -> str:
    """Нормалізуємо лише регістр і дефіс — не значення.

    `UPPER INTERMEDIATE` і `upper-intermediate` це те саме написання одного
    рівня, а `B2` і `Upper-Intermediate` — ні, і другу пару ми не чіпаємо.
    """
    text = re.sub(r"\s+", " ", raw.strip())
    if re.fullmatch(r"[abcABC][12]", text):
        return text.upper()
    if "intermediate" in text.lower():
        text = text.lower().replace(" ", "-")
    # Складені назви пишемо як їх пишуть люди: `Upper-Intermediate`, не
    # `Upper-intermediate`.
    return "-".join(part[:1].upper() + part[1:].lower() for part in text.lower().split("-"))


def detect(description: str) -> tuple[str, str]:
    """→ (рівень як показувати, цитата-доказ). Порожньо — значить не знаємо.

    Повертаємо ще й цитату: у бронюванні це виявилось важливішим за сам
    висновок — людина бачить формулювання роботодавця, а не наш переказ.
    """
    text = description or ""
    if not text:
        return "", ""

    for mention in _ENGLISH.finditer(text):
        start = max(0, mention.start() - WINDOW)
        end = min(len(text), mention.end() + WINDOW)
        window = text[start:end]

        # Рівень зараховуємо, лише якщо між ним і згадкою англійської немає
        # межі пункту: інакше він про сусідній рядок списку, а не про мову.
        here = mention.start() - start, mention.end() - start
        found = []
        for item in _LEVEL.finditer(window):
            # Проміжок МІЖ словом і згадкою — без самої згадки. Перша версія
            # брала `[min(кінець), max(початок)]`, і в проміжок потрапляло
            # слово `English`, яке службовим не є: правило відкидало всі
            # одинадцять законних випадків замість одного хибного.
            gap = (window[item.end():here[0]] if item.end() <= here[0]
                   else window[here[1]:item.start()] if item.start() >= here[1]
                   else "")
            if _BREAK.search(gap):
                continue
            if _IS_SOFT.match(item.group(1)) and not _CONNECTOR.match(gap):
                continue
            found.append(item)
        # Слово зі шкали витісняє загальне: роботодавець, який написав і
        # `proficiency`, і `B1`, мав на увазі B1.
        hard = [item for item in found if not _IS_SOFT.match(item.group(1))]
        found = hard or found
        if not found:
            continue

        levels: list[str] = []
        for index, item in enumerate(found):
            level = _tidy(item.group(1))
            # Діапазон: два рівні поспіль через роздільник — лишаємо обидва.
            if index and _RANGE_GAP.fullmatch(window[found[index - 1].end():item.start()]):
                if levels and levels[-1] != level:
                    levels[-1] = f"{levels[-1]}–{level}"
                    continue
            if level not in levels:
                levels.append(level)

        label = levels[0] if levels else ""
        if not label:
            continue

        # «або вище» шукаємо з ОБОХ боків: англійською пишуть після
        # (`B2 or above`), українською — часто перед (`мінімум розмовний B1`).
        # Дивитись лише в один бік означало б показати мінімум як точку, а
        # це та сама помилка, що звести діапазон до нижньої межі.
        tail = window[found[-1].end():found[-1].end() + 24]
        head = window[max(0, found[0].start() - 24):found[0].start()]
        if "–" not in label and (_AT_LEAST.search(tail) or _AT_LEAST.search(head)):
            label += "+"

        quote = re.sub(r"\s+", " ", window).strip()
        return label, quote

    return "", ""
