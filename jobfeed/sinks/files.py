"""Реалізації sink-а: HTML-дайджест, машиночитний дамп, консоль."""

from __future__ import annotations

from .. import digest
from ..models import Entry
from ..paths import resolve
from ..reservation import YES


class HtmlDigestSink:
    """Локальна сторінка-дайджест. Перезаписується щопрогону — це стан, не стрічка подій."""

    name = "html"
    # Єдине, чим користується власник. Дамп і консоль йому не потрібні.
    critical = True

    def __init__(
        self,
        path: str = "digest.html",
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
    ) -> None:
        self.path = resolve(path)
        self.sources = sources or []
        self.kinds = kinds or {}
        self.direction_order = direction_order or []
        self.health = health or {}
        self.serve_url = serve_url
        self.dropped = dropped or []
        self.direction_defaults = direction_defaults or []
        self.slugs = slugs or []
        self.banking = banking or []
        self.aliases = aliases or {}

    def emit(self, entries: list[Entry]) -> str:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            digest.build(entries, sources=self.sources, kinds=self.kinds,
                         direction_order=self.direction_order, health=self.health,
                         serve_url=self.serve_url, dropped=self.dropped,
                         direction_defaults=self.direction_defaults,
                         slugs=self.slugs, banking=self.banking,
                         aliases=self.aliases),
            encoding="utf-8",
        )
        return f"дайджест: {self.path}"


class JsonDumpSink:
    """Машиночитний зріз — щоб можна було під'єднати що завгодно, не чіпаючи сервіс."""

    name = "json"
    critical = False   # службовий зріз, людина його не читає

    def __init__(self, path: str = "out/vacancies.json") -> None:
        self.path = resolve(path)

    def emit(self, entries: list[Entry]) -> str:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(digest.to_json(entries), encoding="utf-8")
        return f"дамп: {self.path}"


class ConsoleSink:
    """Друкує тільки НОВЕ — для запуску руками й для логів планувальника."""

    name = "console"
    critical = False   # дублює те, що й так видно у зведенні

    def emit(self, entries: list[Entry]) -> str:
        fresh = [e for e in entries if e.is_new]
        if not fresh:
            print("нових вакансій нема")
            return "консоль: 0"
        for entry in fresh:
            vacancy = entry.vacancy
            meta = " · ".join(p for p in (vacancy.location, vacancy.salary) if p)
            mark = " 🛡 бронювання" if entry.reservation and entry.reservation.state == YES else ""
            print(f"\n  {vacancy.title}{mark}")
            print(f"    {vacancy.company}{' — ' + meta if meta else ''}")
            print(f"    {vacancy.url}")
        return f"консоль: {len(fresh)}"


# Місце під месенджер. Реалізується як ще один клас із `emit()` — див. README.
# Ключі беруться з оточення (TELEGRAM_BOT_TOKEN тощо), у код не потрапляють.
