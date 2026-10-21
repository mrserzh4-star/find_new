#!/usr/bin/env python3
"""find_new — персональний агрегатор вакансій.

    python run.py run                # прогін: зібрати, відфільтрувати, оновити дайджест
    python run.py run --dry-run      # те саме, але нічого не пишемо й не зберігаємо
    python run.py preview            # що пройшло б фільтр і що відсіялось (і чому)
    python run.py preview --limit 30
    python run.py cards              # варіанти текстової картки на живих даних
    python run.py stats              # скільки вакансій уже в памʼяті
"""

from __future__ import annotations

import argparse
import sys

from jobfeed import config as config_module
from jobfeed import pipeline, render
from jobfeed.paths import resolve
from jobfeed.store import Store


def _utf8_stdout() -> None:
    """Windows-консоль за замовчуванням cp1251 — кирилиця в ній падає з UnicodeEncodeError."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def cmd_run(args) -> int:
    cfg = config_module.load(args.config)
    report = pipeline.run(cfg, dry_run=args.dry_run)
    r = report.reservations
    print(
        f"\nзібрано {report.fetched} · пройшло фільтр {report.kept} · "
        f"нових {report.fresh} · на сторінці {report.window}"
        + (f" · дозбирано деталей {report.backfilled}" if report.backfilled else "")
    )
    for line in report.absence_skips:
        print(f"  · сигнал зникнення вимкнено: {line}")
    if report.likely_closed:
        print(f"ймовірно закриті (зникли з фіда двічі поспіль): {report.likely_closed}")
    if report.cross_source_pairs:
        print(f"та сама вакансія на кількох джерелах: {report.cross_source_pairs} — злито в одну картку")
    if report.coverage:
        print("заповненість (знаменник — лише джерела, які поле дають):")
        for name, (filled, applicable, giving) in report.coverage.items():
            source = ", ".join(giving) if len(giving) < 3 else "усі джерела"
            print(f"  {name:<10} {filled:>3} з {applicable:<4} {source}")
    if report.budgets_from_text:
        print(f"бюджет із опису: {report.budgets_from_text} понад ті, де джерело "
              f"дало поле зарплати")
    if report.english[1]:
        filled, total = report.english
        print(f"англійська: рівень указано в {filled} з {total} "
              f"(решта або не згадує рівня, або не згадує мови)")
    if report.window:
        print(
            f"бронювання: згадано {r.get('yes', 0)} · "
            f"не згадано {r.get('no', 0)} · опису не було {r.get('unknown', 0)}"
        )
    elif cfg.get("reservation", {}).get("required", False):
        # Порожня сторінка при увімкненій вимозі — не поломка, а наслідок
        # реального стану ринку. Пояснюємо, щоб не шукали баг.
        print(
            "\nСторінка порожня, бо ввімкнено reservation.required = true, а серед\n"
            "вакансій, що пройшли фільтр, про бронювання не написав ніхто.\n"
            "Це не помилка: бронювання зазвичай згадують оборонні й виробничі\n"
            "компанії, а не продуктові дизайн-команди. Постав required = false,\n"
            "щоб бачити всі вакансії з позначкою там, де бронь таки згадана."
        )
    for output in report.outputs:
        print(f"  → {output}")
    # Вік сторінки — ПРИСУТНІЙ сигнал. Раніше про те, що дайджест не
    # оновився, свідчила лише ВІДСУТНІСТЬ рядка «дайджест:», а відсутнього
    # рядка око не ловить: ми перевіряємо те, що написано.
    if report.digest_fresh:
        print("сторінка: оновлена цим прогоном")
    elif report.digest_written:
        from datetime import datetime, timezone
        age = int((datetime.now(timezone.utc) - report.digest_written).total_seconds())
        older = (f"{age} с" if age < 60
                 else f"{age // 60} хв" if age < 3600 else f"{age // 3600} год")
        print(f"🔴 сторінка НЕ ОНОВЛЕНА: останній запис {older} тому")
    else:
        print("🔴 сторінки ще немає")

    # Стан джерел — рядком на кожне, включно зі здоровими. Друкувати лише
    # проблемні означало б знову зробити сигналом ВІДСУТНІСТЬ рядка.
    if report.source_health:
        print("джерела:")
        for name, health in sorted(report.source_health.items()):
            mark = {"ok": "  ", "quiet": "  ", "waiting": "  ",
                    "off": "· ", "failed": "🔴", "empty": "! "}
            state = {
                "ok": f"забрано {health.fetched}",
                "quiet": "нічого нового з минулого разу",
                "waiting": (
                    f"власна періодичність · наступний забір через "
                    f"{health.wait_hours:.0f} год"
                    + (f" · востаннє забрано {health.last_ok_count}"
                       if health.last_ok else "")
                ),
                "empty": "фід порожній, помилки не було",
                "failed": f"НЕ ЗАБРАЛОСЬ — {health.error}",
                "off": f"вимкнене — {health.note}" if health.note else "вимкнене",
            }.get(health.state, "стан ще не записувався")
            print(f"  {mark.get(health.state, '  ')} {name:<12} {state}")

    for warning in report.warnings:
        print(f"  ! {warning}")
    for error in report.errors:
        print(f"  ПОМИЛКА {error}", file=sys.stderr)

    # Критичне — наприкінці й окремо. Мережевий збій джерела минає сам,
    # зламаний рендер сторінки не полагодиться, тож він і не мусить
    # губитись у переліку поруч із тимчасовими 500-ми.
    if report.critical_errors:
        print("\n" + "━" * 62, file=sys.stderr)
        print("ПРОГІН НЕВДАЛИЙ — сторінку не оновлено:", file=sys.stderr)
        for error in report.critical_errors:
            print(f"  {error}", file=sys.stderr)
        print("Дані зібрані й збережені, але власник побачить стару стрічку.",
              file=sys.stderr)
        print("━" * 62, file=sys.stderr)
        return 2

    return 1 if report.errors else 0


def cmd_preview(args) -> int:
    cfg = config_module.load(args.config)
    vacancies, errors, _, _ = pipeline.collect(cfg)
    kept, dropped = pipeline.screen(cfg, vacancies)

    print(f"зібрано {len(vacancies)} · пройшло {len(kept)} · відсіяно {len(dropped)}\n")
    print("── ПРОЙШЛИ ФІЛЬТР " + "─" * 44)
    for vacancy in kept[: args.limit]:
        meta = " · ".join(p for p in (vacancy.location, vacancy.salary) if p)
        print(f"  {vacancy.title}")
        print(f"      {vacancy.company}{'  ·  ' + meta if meta else ''}")
    print("\n── ВІДСІЯНО (причина) " + "─" * 40)
    for vacancy, reason in dropped[: args.limit]:
        print(f"  {vacancy.title[:54]:<54} ← {reason}")
    for error in errors:
        print(f"  ПОМИЛКА {error}", file=sys.stderr)
    return 0


def cmd_cards(args) -> int:
    """Варіанти ТЕКСТОВОЇ картки — знадобляться, коли зʼявиться доставка в месенджер."""
    cfg = config_module.load(args.config)
    vacancies, _, _, _ = pipeline.collect(cfg)
    kept, _ = pipeline.screen(cfg, vacancies)
    sample = kept[: args.limit]
    if not sample:
        print("нема чого показувати: фільтр не пропустив жодної вакансії")
        return 1
    for style in render.CARDS:
        print("\n" + "═" * 62)
        print(f"ВАРІАНТ: {style}")
        print("═" * 62)
        for vacancy in sample:
            print("\n" + render.render(vacancy, style))
    return 0


def cmd_keys(args) -> int:
    """Скільки заголовків ловить КОЖЕН ключ у кожному переліку.

    Нуль сам по собі не помилка — помилка це нуль, якого не очікували.
    Мовний варіант («моушн»), запас на майбутнє («vfx») і одруківка
    («ux/ux») у конфізі виглядають однаково, і читанням не розрізняються.
    Розрізняє лише запуск — тому ця команда й існує: після кожної правки
    переліку прогнати й глянути, чи нуль там, де ти його чекав.
    """
    from jobfeed.filters import _pattern
    from jobfeed.reservation import _stems

    cfg = config_module.load(args.config)
    vacancies, errors, _, _ = pipeline.collect(cfg)
    titles = [v.title for v in vacancies]
    descriptions = [v.description for v in vacancies]

    def report(label: str, keys: list[str], corpus: list[str], make) -> None:
        print(f"\n── {label} " + "─" * max(0, 52 - len(label)))
        for key in keys:
            pattern = make(key)
            hits = sum(1 for text in corpus if pattern and pattern.search(text))
            mark = "  ← нічого не ловить" if hits == 0 else ""
            print(f"  {hits:>4}  {key}{mark}")

    report("filter.include_any", cfg["filter"]["include_any"], titles, _pattern)
    report("filter.exclude_any", cfg["filter"]["exclude_any"], titles, _pattern)
    for name, section in cfg.get("directions", {}).items():
        report(f"directions [{name}]", section.get("keys", []), titles, _pattern)
    reservation = cfg.get("reservation", {})
    report("reservation.mentions", reservation.get("mentions", []),
           descriptions, lambda k: _stems([k]))
    report("reservation.not_reservation", reservation.get("not_reservation", []),
           descriptions, lambda k: _stems([k]))

    print(f"\nвибірка: {len(vacancies)} заголовків з живих джерел")
    for error in errors:
        print(f"  ПОМИЛКА {error}", file=sys.stderr)
    return 0


def cmd_serve(args) -> int:
    """Локальний сервер: та сама сторінка, але з памʼяттю позначок і кнопками."""
    from jobfeed import server
    return server.serve(args.config, host=args.host, port=args.port)


def cmd_stats(args) -> int:
    cfg = config_module.load(args.config)
    db = resolve(cfg.get("run", {}).get("db", pipeline.DEFAULT_DB))
    with Store(db) as store:
        print(f"база: {db}")
        print(f"вакансій у памʼяті: {store.count()}")
    return 0


def main() -> int:
    _utf8_stdout()
    parser = argparse.ArgumentParser(prog="run.py", description=__doc__)
    parser.add_argument("--config", default=str(resolve("config/filter.toml")))
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="прогін")
    p_run.add_argument("--dry-run", action="store_true", help="нічого не зберігати й не писати")
    p_run.set_defaults(func=cmd_run)

    p_prev = sub.add_parser("preview", help="що пройшло б фільтр")
    p_prev.add_argument("--limit", type=int, default=15)
    p_prev.set_defaults(func=cmd_preview)

    p_cards = sub.add_parser("cards", help="варіанти текстової картки")
    p_cards.add_argument("--limit", type=int, default=3)
    p_cards.set_defaults(func=cmd_cards)

    p_keys = sub.add_parser("keys", help="скільки ловить кожен ключ у кожному переліку")
    p_keys.set_defaults(func=cmd_keys)

    p_serve = sub.add_parser("serve", help="локальний сервер (позначки в БД, кнопки)")
    p_serve.add_argument("--host", default=None,
                         help="за замовчуванням 127.0.0.1 — тільки цей компʼютер")
    p_serve.add_argument("--port", type=int, default=None)
    p_serve.set_defaults(func=cmd_serve)

    p_stats = sub.add_parser("stats", help="стан памʼяті")
    p_stats.set_defaults(func=cmd_stats)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
