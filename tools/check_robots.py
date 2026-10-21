"""Чи дозволяє нам сайт свій шлях — перевірка, яка не бреше.

Навіщо окремий інструмент замість двох рядків у консолі
-------------------------------------------------------
Бо ті два рядки ми написали неправильно й місяць виносили за ними вироки.

    rp = RobotFileParser(); rp.set_url(host + "/robots.txt"); rp.read()
    rp.can_fetch(UA, url)          # ← weworkremotely False, dribbble False

Виглядає як пряма заборона. Насправді `read()` ходить по `urllib`, обидва
сайти віддають `urllib` HTTP 403, а `RobotFileParser` на 403 мовчки ставить
`disallow_all = True`. Тобто **«заборонено» і «не зміг прочитати правила»
зливаються в одне слово** — а це різні стани з різними наслідками:

* заборонено      → джерело не беремо, переперевіряти нема чого;
* не прочитали    → ми взагалі не знаємо правил, і вирок передчасний;
* прочитали, дозволено, але сторінки віддають 403 → правила відомі й на
  нашому боці, закрито технічно.

Заміряно 09.09.2026 — саме через цю ваду weworkremotely і dribbble ледь не
були викреслені як «заборонені нам», хоча обидва дозволяють.

⚠️ НА ЩО ЦЕЙ ІНСТРУМЕНТ НЕ ВІДПОВІДАЄ
------------------------------------
На питання «чи є там що брати». Він знає лише «чи можна» і «чи відповідає».

Це не причіпка, а вже пійманий випадок: `uxjobsboard.com` і
`designremotely.co` дають у четвертій колонці чесні HTTP 200 — і обидва
мертві. Перший віддає заглушку хостера «Job board unavailable», другий
редиректить на `/closing-down/` зі словами «we are no longer accepting new
job postings». Вакансій — нуль в обох.

Тобто три сусідні величини, які легко злити в одну:

    дозволено ≠ відповідає ≠ є що брати

Останню доводиться дивитись очима, і саме її ця таблиця не показує.
Рахувати вакансії тут не будемо: для цього треба знати розмітку кожного
сайту, і інструмент перестав би бути універсальним.

Як користуватись
----------------
    python tools/check_robots.py https://example.com/jobs.rss [ще URL…]

Друкує обидва способи поруч, щоб розбіжність було видно оком, а не треба
було про неї пам'ятати.
"""

from __future__ import annotations

import sys
import urllib.robotparser
from urllib.parse import urlsplit

import httpx

# Той самий UA, яким ходять адаптери. Перевіряти під іншим — означає
# перевіряти не те: правила бувають різні для різних імен.
USER_AGENT = "Mozilla/5.0 (compatible; find_new-jobfeed/0.1; personal job alerts)"


def by_read(url: str) -> str:
    """Спосіб, яким користуватись НЕ можна. Лишений, щоб бачити розбіжність."""
    parts = urlsplit(url)
    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(f"{parts.scheme}://{parts.netloc}/robots.txt")
    try:
        parser.read()
    except Exception as exc:
        return f"впав: {type(exc).__name__}"
    return "дозволено" if parser.can_fetch(USER_AGENT, url) else "заборонено"


def by_fetch(url: str, timeout: float = 25.0) -> tuple[str, str]:
    """Правильний спосіб: забрати robots.txt СВОЇМ клієнтом, тоді розбирати.

    Повертає (стан robots.txt, вирок). Вирок «правил не знаємо» — окремий
    від «заборонено»: сайт, який не дає прочитати правила, ми не маємо права
    записати ні в дозволені, ні в заборонені.
    """
    parts = urlsplit(url)
    robots = f"{parts.scheme}://{parts.netloc}/robots.txt"
    try:
        response = httpx.get(robots, timeout=timeout,
                             headers={"User-Agent": USER_AGENT},
                             follow_redirects=True)
    except Exception as exc:
        return f"{type(exc).__name__}", "правил не знаємо"
    if response.status_code != 200:
        return f"HTTP {response.status_code}", "правил не знаємо"

    parser = urllib.robotparser.RobotFileParser()
    parser.parse(response.text.splitlines())
    verdict = "дозволено" if parser.can_fetch(USER_AGENT, url) else "заборонено"
    delay = parser.crawl_delay(USER_AGENT)
    if delay:
        verdict += f" (crawl-delay {delay})"
    return f"HTTP 200, {len(response.content)} Б", verdict


def probe(url: str, timeout: float = 25.0) -> str:
    """А чи віддає сайт саму сторінку. Дозвіл у robots і доступ — різні речі."""
    try:
        response = httpx.get(url, timeout=timeout,
                             headers={"User-Agent": USER_AGENT},
                             follow_redirects=True)
    except Exception as exc:
        return f"{type(exc).__name__}"
    return f"HTTP {response.status_code}"


def main(urls: list[str]) -> int:
    # Windows-консоль за замовчуванням cp1251 — кирилиця й рамка в ній
    # падають. Той самий рядок стоїть у tests/test_parsing.py.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    if not urls:
        print(__doc__.strip().splitlines()[-3])
        return 2
    print(f"{'сайт':<34} {'read()':<12} {'robots.txt':<22} {'вирок':<24} сторінка")
    print("─" * 108)
    for url in urls:
        host = urlsplit(url).netloc
        state, verdict = by_fetch(url)
        print(f"{host:<34} {by_read(url):<12} {state:<22} {verdict:<24} {probe(url)}")
    print("\nРозбіжність між колонками read() і «вирок» означає, що сайт не")
    print("любить urllib, а не що він нам щось заборонив.")
    # Межу друкуємо ПОРУЧ ІЗ ТАБЛИЦЕЮ, а не лише в докстрінгу: читають
    # вивід, а не файл. Дві мертві дошки віддавали тут чесні HTTP 200.
    print("\n⚠️  «сторінка HTTP 200» НЕ означає «джерело придатне»:")
    print("    закрита дошка теж відповідає 200 — заглушкою. Чи є там")
    print("    вакансії, ця таблиця не знає; це видно лише очима.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
