"""Ознака «бронювання від мобілізації» — витягується з тексту вакансії.

Три стани, а не булеве значення. Різниця принципова:

    yes      у тексті прямо написано про бронювання
    no       текст ми прочитали, згадки нема
    unknown  тексту не було взагалі (джерело не дало опису)

`no` НЕ означає «броні немає». Багато компаній її мають і просто не пишуть
про це в оголошенні. Тому на картці цей стан формулюється як «не згадано»,
і жорсткий фільтр за замовчуванням вимкнений — інакше він приховає реальні
можливості, про які просто не написали.

Хибні збіги: українською `брон-` — це не лише бронювання, а й броньова
техніка (`бронетехніка`, `бронеавтомобіль`, `бронежилет`), яка в оборонних
вакансіях трапляється часто. Тому є окремий список «це не бронювання»:
він потрібен не для нашого дефолтного набору форм (вони точні), а на випадок,
коли власник допише широку основу на кшталт `брон`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

YES, NO, UNKNOWN = "yes", "no", "unknown"

# Дефолти на випадок, якщо секції в конфізі нема взагалі.
# Склад звірений з 34 живими формулюваннями (specs/FEATURE__reservation.md).
# `резервуванн` свідомо ВИКИНУТО: у фіді знайшлось «коректну роботу механізмів
# резервування» — це про надлишковість мережі, а не про мобілізацію.
DEFAULT_MENTIONS = [
    "бронюванн", "бронюють", "бронюємо", "заброньован", "відстрочк від мобіліз",
]
DEFAULT_NOT_RESERVATION = [
    "бронетехн", "бронеавто", "бронежил", "бронемаш", "бронеплит", "бронескл",
]

_SENTENCE = re.compile(r"(?<=[.!?…])\s+|\n+")


@dataclass(frozen=True)
class Reservation:
    state: str = UNKNOWN
    evidence: str = ""  # речення, де це сказано — щоб не відкривати вакансію заради перевірки

    @property
    def is_yes(self) -> bool:
        return self.state == YES


def _stems(words: list[str]) -> re.Pattern[str] | None:
    """Основи слів → одна регулярка з межею на початку слова.

    Основа, а не повне слово: `бронюванн` покриває «бронювання»,
    «бронюванням», «бронювання» в будь-якому відмінку — власнику не треба
    перелічувати форми. Запис із кількох слів («відстрочк від мобіліз»)
    стає послідовністю основ: так `відстрочка` зараховується лише поруч
    з `мобілізацією`, бо сама по собі це ще й відстрочка платежу.
    """
    parts = []
    for word in words:
        tokens = [re.escape(t) for t in word.split() if t]
        if tokens:
            parts.append(r"\s+".join(f"{t}\\w*" for t in tokens))
    if not parts:
        return None
    return re.compile(rf"(?<!\w)(?:{'|'.join(parts)})", re.IGNORECASE | re.UNICODE)


class ReservationDetector:
    def __init__(self, config: dict | None = None) -> None:
        config = config or {}
        self.mentions = _stems(list(config.get("mentions", DEFAULT_MENTIONS)))
        self.not_reservation = _stems(
            list(config.get("not_reservation", DEFAULT_NOT_RESERVATION))
        )
        self.evidence_chars = int(config.get("evidence_chars", 220))

    def _is_real(self, matched_word: str) -> bool:
        """Відсіює броньову техніку від бронювання."""
        if not self.not_reservation:
            return True
        hit = self.not_reservation.match(matched_word)
        return not (hit and hit.end() == len(matched_word))

    def detect(self, description: str, hint: str = "") -> Reservation:
        """`hint` — структурована ознака від джерела; вона завжди головніша.

        Джерело, яке має власне поле бронювання (у Djinni це фільтр
        `editorial=reservation`), знає точніше за будь-яку регулярку по
        тексту. Регулярка лишається фолбеком для джерел без структури — як DOU,
        у якого в `<item>` рівно 5 полів і місця для такої ознаки просто немає.
        """
        hint = (hint or "").strip().lower()
        if hint == YES:
            return Reservation(YES, "джерело позначило вакансію як таку, де є бронювання")
        if hint == NO:
            return Reservation(NO)

        if not description or not description.strip():
            return Reservation(UNKNOWN)
        if not self.mentions:
            return Reservation(NO)

        for sentence in _SENTENCE.split(description):
            for match in self.mentions.finditer(sentence):
                if self._is_real(match.group(0)):
                    return Reservation(YES, _evidence(sentence, match, self.evidence_chars))
        return Reservation(NO)


def _evidence(sentence: str, match: re.Match[str], limit: int) -> str:
    """Цитата навколо ЗБІГУ, а не початок речення.

    Описи на DOU часто йдуть суцільним списком без крапок, тому «речення»
    буває на кілька тисяч символів. Обрізати його з початку — означає
    показати людині текст, у якому потрібного слова не видно взагалі.
    """
    text = sentence.strip()
    if len(text) <= limit:
        return " ".join(text.split())

    # Лишаємо трохи контексту перед збігом, решту вікна — після нього.
    start = max(0, match.start() - limit // 3)
    end = min(len(text), start + limit)
    chunk = text[start:end]

    if start > 0:  # не рвемо слово на лівій межі
        chunk = chunk.split(" ", 1)[-1]
    if end < len(text):
        chunk = chunk.rsplit(" ", 1)[0]

    chunk = " ".join(chunk.split())
    return f"{'…' if start > 0 else ''}{chunk}{'…' if end < len(text) else ''}"
