"""Напрям дизайну — за яким власник фільтрує стрічку.

Це фільтр ПОКАЗУ, а не відбору: він нічого не викидає з бази й не чіпає
`include_any`. Вакансія, знята галочкою, лишається в базі й повертається,
щойно галочку поставлять назад.

Три рішення, які виглядають деталями, але кожне заміряне:

* **Одна вакансія — БАГАТО груп.** `Graphic/Motion Designer`,
  `Web Developer and UI/UX Designer`, `Brand & UI/UX Designer` — у 2+ групах
  32 вакансії з 203 (16%). Однозначна класифікація приховала б їх усі.

* **Тільки заголовок, не опис.** На шести неоднозначних заголовках опис дав
  2-4 групи одночасно в чотирьох випадках — тобто множить неоднозначність
  замість розвʼязувати. Та сама причина, через яку опис відкинули для
  основного фільтра.

* **«Інше» — повноцінна група, видима й увімкнена.** Там `Design System Lead`,
  `Growth Designer`, `E-commerce Designer` — тобто рівно профіль власника.
  Нерозпізнане чесніше показати, ніж розкидати здогадом по чотирьох групах.

Ключі — у конфізі, правляться без коду. Межі слова ті самі, що у фільтрі:
зірочка знімає праву межу («графічн*» ловить усі відмінки), підкреслення не
розриває слово.
"""

from __future__ import annotations

from dataclasses import dataclass

from .filters import _pattern
from .models import Vacancy

# Група для того, що не впізналось. Не «сміття» й не прихована категорія:
# вона стоїть у списку нарівні з рештою й увімкнена за замовчуванням.
OTHER = "Інше"


# Скільки символів навколо згадки дивитись, коли напрям шукається в ОПИСІ.
# Те саме вікно, що в рівні англійської й бюджеті, і з тієї ж причини: слово
# може стояти в тексті й бути не про те.
DESCRIPTION_WINDOW = 70


@dataclass
class DirectionClassifier:
    """Заголовок → набір напрямів. Порожній набір означає «Інше»."""

    groups: dict[str, list[str]]
    # Групи, які шукаються в ОПИСІ, а не в заголовку, і стоп-слова до них.
    from_text: dict[str, list[str]] = None

    @classmethod
    def from_config(cls, config: dict) -> "DirectionClassifier":
        sections = {name: section for name, section in (config or {}).items()
                    if isinstance(section, dict)}
        groups = {name: list(s.get("keys", [])) for name, s in sections.items()}
        from_text = {name: list(s.get("not_near", []))
                     for name, s in sections.items()
                     if s.get("match") == "description"}
        return cls(groups, from_text)

    def __post_init__(self) -> None:
        self.from_text = self.from_text or {}
        self._compiled = {
            name: [(key, _pattern(key)) for key in keys]
            for name, keys in self.groups.items()
        }
        self._stop = {
            name: [_pattern(word) for word in words]
            for name, words in self.from_text.items()
        }

    @property
    def names(self) -> list[str]:
        """Назви груп у порядку конфіга, з «Іншим» у кінці."""
        return [*self._compiled, OTHER]

    def _hit(self, name: str, vacancy: Vacancy):
        """→ збіг для цієї групи, або None. Для груп з опису — з перевіркою.

        🔴 СВІДОМИЙ ВИНЯТОК із правила «напрям за назвою посади».

        Правило лишається чинним для ДИСЦИПЛІН: слова «дизайн», «бренд»,
        «продукт» є в описі майже кожної вакансії, тож напрям з опису не
        вивести — це заміряно, опис давав 2-4 групи одночасно там, де
        заголовок давав одну.

        Але інструмент — не дисципліна. `Webflow` у заголовку 0 разів із
        270, в описі 14; `Framer` 9, `WordPress` 2, `Tilda` 0. Тобто
        інструменти згадують рідко й предметно, і згадка є перевірюваним
        фактом, а не загальним словом.

        `not_near` існує, бо серед 14 згадок дві — це агенція, що описує
        СЕБЕ: «a Webflow Experts partner, holding a 5.0 rating». Той самий
        клас, що `Advanced Figma` в рівні мови й `raised $500M` у бюджеті:
        слово присутнє, але не про вакансію.
        """
        keys = self._compiled.get(name, ())
        if name not in self._stop:
            for _, pattern in keys:
                if pattern.search(vacancy.title):
                    return pattern.search(vacancy.title)
            return None

        text = vacancy.description or ""
        for _, pattern in keys:
            found = pattern.search(text)
            if not found:
                continue
            start = max(0, found.start() - DESCRIPTION_WINDOW)
            window = text[start:found.end() + DESCRIPTION_WINDOW]
            if any(stop.search(window) for stop in self._stop[name]):
                continue
            return found
        return None

    def evidence(self, vacancy: Vacancy) -> dict[str, str]:
        """Цитата з опису для груп, знайдених у тексті.

        Доказ поруч із висновком окупився вже двічі — на рівні мови й на
        бюджеті: твердження перевіряється поглядом, а не походом у базу.
        """
        quotes = {}
        text = vacancy.description or ""
        for name in self._stop:
            found = self._hit(name, vacancy)
            if found:
                start = max(0, found.start() - DESCRIPTION_WINDOW)
                quotes[name] = " ".join(
                    text[start:found.end() + DESCRIPTION_WINDOW].split())
        return quotes

    def classify(self, vacancy: Vacancy) -> tuple[str, ...]:
        matched = tuple(name for name in self._compiled
                        if self._hit(name, vacancy))
        return matched or (OTHER,)

    def dead_keys(self, vacancies: list[Vacancy]) -> list[str]:
        """Ключі, які не зловили НІЧОГО на всій вибірці.

        Мертвий ключ невідрізнимий від відсутнього напряму: він валідний на
        вигляд, нічого не падає, просто ніколи не спрацьовує. Одруківка в
        `ux/ux` виглядала б рівно так само, як свідоме рішення не ловити
        цей випадок — тому перелік ключів мусить уміти сказати про себе,
        що в ньому не працює.
        """
        return [
            f"{name}: {key}"
            for name, keys in self._compiled.items()
            for key, pattern in keys
            # Група, що шукається в описі, і перевіряється по опису —
            # інакше кожен її ключ звітував би «нічого не ловить».
            if not any(pattern.search(v.description if name in self._stop else v.title)
                       for v in vacancies)
        ]
