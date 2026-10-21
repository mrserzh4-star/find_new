"""Реєстр джерел.

Додати сайт = написати модуль з класом, що реалізує `Source`, і додати
його у `build()`. Ядро змінювати не треба.

Якщо новий сайт віддає schema.org JSON-LD (а це роблять майже всі джоб-борди
заради Google for Jobs) — бери готовий `jsonld_jobposting` і пиши лише те,
як дістати список посилань. Це десятки рядків, а не окремий парсер.
"""

from __future__ import annotations

from .base import Enrichable, Incremental, Source
from .andersen import AndersenSource
from .djinni import DjinniSource
from .dou import DouSource
from .dou_companies import DouCompaniesSource
from .happymonday import HappyMondaySource
from .boards_rss import BoardsRssSource
from .jobico import JobicoSource
from .recruitee import RecruiteeSource
from .work_ua import WorkUaSource

__all__ = [
    "Source", "Enrichable", "Incremental",
    "DouSource", "DouCompaniesSource", "DjinniSource", "HappyMondaySource",
    "AndersenSource", "WorkUaSource", "RecruiteeSource", "JobicoSource", "BoardsRssSource", "build",
]


# Вид кожного відомого джерела — потрібен видачі навіть тоді, коли джерело
# вимкнене: таб «Сайти компаній» має існувати й бути порожнім із поясненням,
# а не зникати разом із джерелом.
KINDS = {
    cls.name: cls.kind
    for cls in (DjinniSource, DouSource, DouCompaniesSource,
                HappyMondaySource, AndersenSource, WorkUaSource,
                RecruiteeSource, JobicoSource, BoardsRssSource)
}


# Які поля дає кожне джерело. Як і KINDS, збирається з класів адаптерів:
# факт про можливості джерела оголошує адаптер, а не виводить ядро зі
# спостережень. Виведення відповідає на «що ми бачили», а потрібне —
# «що джерело дає», і різниця видна лише тоді, коли ми чогось не побачили,
# тобто рівно в момент поломки.
PROVIDES = {
    cls.name: cls.provides
    for cls in (DjinniSource, DouSource, DouCompaniesSource,
                HappyMondaySource, AndersenSource, WorkUaSource,
                RecruiteeSource, JobicoSource, BoardsRssSource)
}


def build(config: dict) -> list[Source]:
    """Створює джерела з секції [sources] конфіга. Вимкнені — пропускає."""
    sources: list[Source] = []

    djinni = config.get("djinni", {})
    if djinni.get("enabled", True) and djinni:
        sources.append(DjinniSource(feeds=list(djinni.get("feeds", []))))

    dou = config.get("dou", {})
    if dou.get("enabled", True) and dou:
        sources.append(DouSource(feeds=list(dou.get("feeds", []))))

    happymonday = config.get("happymonday", {})
    if happymonday.get("enabled", True) and happymonday:
        sources.append(HappyMondaySource(feeds=list(happymonday.get("feeds", []))))

    companies = config.get("dou_companies", {})
    if companies.get("enabled", True) and companies:
        sources.append(DouCompaniesSource(
            slugs=list(companies.get("slugs", [])),
            every_hours=float(companies.get("every_hours", 24)),
        ))

    boards = config.get("boards_rss", {})
    if boards.get("enabled", True) and boards.get("feeds"):
        sources.append(BoardsRssSource(feeds=list(boards.get("feeds", []))))

    jobico = config.get("jobico", {})
    if jobico.get("enabled", True) and jobico:
        sources.append(JobicoSource())

    recruitee = config.get("recruitee", {})
    if recruitee.get("enabled", True) and recruitee:
        sources.append(RecruiteeSource(
            companies=list(recruitee.get("companies", [])),
            every_hours=float(recruitee.get("every_hours", 24)),
        ))

    work_ua = config.get("work_ua", {})
    if work_ua.get("enabled", True) and work_ua:
        sources.append(WorkUaSource(
            queries=list(work_ua.get("queries", [])),
            max_pages=int(work_ua.get("max_pages", 5)),
            delay=float(work_ua.get("delay", 2.0)),
        ))

    andersen = config.get("andersen", {})
    if andersen.get("enabled", True) and andersen:
        sources.append(AndersenSource(feeds=list(andersen.get("feeds", []))))

    return sources
