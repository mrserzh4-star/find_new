"""Формат картки вакансії в каналі.

Це ЄДИНА поверхня, яку бачить людина, тому рішення тут — дизайнерські,
а не технічні. Канал читають з телефона, у стрічці, рішення бінарне:
відкрити або проскролити. Отже картка мусить винести наперед рівно ті
три речі, за якими це рішення й ухвалюють — посада, компанія, локація —
і зарплату, якщо джерело її дало. Все інше конкурує з ними за увагу.

Три варіанти нижче — на вибір власника. Перемикається `card = "..."` у конфізі.
Розмітка — Telegram HTML parse_mode.
"""

from __future__ import annotations

import html
from datetime import datetime, timezone

from .models import Vacancy

TELEGRAM_LIMIT = 4096


def _e(text: str) -> str:
    return html.escape(text or "", quote=False)


def _when(posted_at: datetime | None) -> str:
    """Відносний час — «2 год тому» читається швидше за дату."""
    if not posted_at:
        return ""
    delta = datetime.now(timezone.utc) - posted_at
    hours = int(delta.total_seconds() // 3600)
    if hours < 1:
        return "щойно"
    if hours < 24:
        return f"{hours} год тому"
    days = hours // 24
    return f"{days} дн тому" if days < 7 else posted_at.strftime("%d.%m")


def _meta(vacancy: Vacancy, sep: str = " · ") -> str:
    """Метарядок: тільки те, що джерело реально дало. Порожнє не показуємо."""
    parts = [p for p in (vacancy.location, vacancy.salary, _when(vacancy.posted_at)) if p]
    return sep.join(_e(p) for p in parts)


def card_compact(vacancy: Vacancy) -> str:
    """A — «стрічка». Два рядки, посада клікабельна, нуль декору.

    Найшвидша для скролу: очей ловить болд-посаду, під нею — хто і де.
    Зарплата й свіжість підмішані в той самий метарядок, щоб не рости вгору.
    """
    head = f'<b><a href="{_e(vacancy.url)}">{_e(vacancy.title)}</a></b>'
    line = " — ".join(p for p in (_e(vacancy.company), _meta(vacancy)) if p)
    return head + (f"\n{line}" if line else "")


def card_labeled(vacancy: Vacancy) -> str:
    """B — «картка з підписами». Кожен факт на своєму рядку з іконкою.

    Повільніша для скролу (5 рядків замість 2), але не дає нічого проґавити
    і краще виглядає, коли вакансій за годину одна-дві, а не десять.
    """
    lines = [f"🎯 <b>{_e(vacancy.title)}</b>"]
    if vacancy.company:
        lines.append(f"🏢 {_e(vacancy.company)}")
    if vacancy.location:
        lines.append(f"📍 {_e(vacancy.location)}")
    if vacancy.salary:
        lines.append(f"💰 {_e(vacancy.salary)}")
    when = _when(vacancy.posted_at)
    if when:
        lines.append(f"🕑 {_e(when)}")
    lines.append(f'🔗 <a href="{_e(vacancy.url)}">Відкрити на {_e(vacancy.source.upper())}</a>')
    return "\n".join(lines)


def card_teaser(vacancy: Vacancy, teaser_chars: int = 160) -> str:
    """C — «з тизером». A плюс перше речення опису.

    Дає більше контексту, але опис на DOU часто починається з реклами
    компанії, а не з суті вакансії — тому тизер тут свідомо короткий
    і рветься по реченню, а не по символу.
    """
    base = card_compact(vacancy)
    teaser = _first_sentence(vacancy.description, teaser_chars)
    return base + (f"\n<i>{_e(teaser)}</i>" if teaser else "")


def _first_sentence(text: str, limit: int) -> str:
    if not text:
        return ""
    head = text[: limit * 2]
    for end in (". ", "! ", "? "):
        idx = head.find(end)
        if 0 < idx <= limit:
            return head[: idx + 1].strip()
    return (text[:limit].rsplit(" ", 1)[0] + "…") if len(text) > limit else text


CARDS = {
    "compact": card_compact,
    "labeled": card_labeled,
    "teaser": card_teaser,
}


def render(vacancy: Vacancy, style: str = "compact") -> str:
    if style not in CARDS:
        raise ValueError(f"невідомий стиль картки: {style!r}; є: {', '.join(CARDS)}")
    return CARDS[style](vacancy)


def pack(
    vacancies: list[Vacancy], style: str = "compact", limit: int = TELEGRAM_LIMIT
) -> list[tuple[str, list[Vacancy]]]:
    """Пакує картки в повідомлення, не перевищуючи ліміт Telegram.

    Кілька вакансій за годину — це один пост із порожнім рядком між картками,
    а не серія пінгів. Одна вакансія = один пост, як і було б.

    Повертає пари (текст повідомлення, вакансії в ньому) — пайплайну треба
    знати РІВНО, що закрив кожен меседж, щоб позначити відправленими лише
    доставлене.
    """
    messages: list[tuple[str, list[Vacancy]]] = []
    blocks: list[str] = []
    chunk: list[Vacancy] = []
    size = 0
    for vacancy in vacancies:
        block = render(vacancy, style)
        extra = len(block) + (2 if blocks else 0)
        if blocks and size + extra > limit:
            messages.append(("\n\n".join(blocks), chunk))
            blocks, chunk, size = [block], [vacancy], len(block)
        else:
            blocks.append(block)
            chunk.append(vacancy)
            size += extra
    if blocks:
        messages.append(("\n\n".join(blocks), chunk))
    return messages
