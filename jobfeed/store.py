"""Дедуп і памʼять сервісу на SQLite (stdlib).

БД — джерело правди для дайджесту: показуємо не лише те, що зʼявилось
за останню годину, а рухоме вікно за N днів, де свіже підсвічене.
Тому в таблиці лежить уся картка, а не самі лише id.

`first_seen_at` однаковий для всієї партії одного прогону — цього досить,
щоб відновити «що прийшло минулого разу» без окремої таблиці прогонів.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import Vacancy

SCHEMA = """
CREATE TABLE IF NOT EXISTS vacancies (
    id            TEXT PRIMARY KEY,
    source        TEXT NOT NULL,
    source_uid    TEXT NOT NULL,
    title         TEXT NOT NULL,
    company       TEXT NOT NULL,
    location      TEXT NOT NULL,
    salary        TEXT NOT NULL,
    url           TEXT NOT NULL,
    tags          TEXT NOT NULL,
    posted_at     TEXT,
    first_seen_at TEXT NOT NULL,
    -- Опис зберігаємо, бо з нього рахуються похідні ознаки (бронювання).
    -- Інакше правка їх налаштувань діяла б лише на нові записи, а стара
    -- база лишалась би без ознаки назавжди.
    description   TEXT NOT NULL DEFAULT '',
    -- Структурована ознака бронювання від джерела ("yes"/"no"/""), якщо воно її дає.
    reservation_hint TEXT NOT NULL DEFAULT '',
    -- Скільки повних заборів поспіль вакансії не було у фіді джерела.
    -- Один промах — підозра, два — сигнал. Повернулась у фід → знову 0.
    missing_streak INTEGER NOT NULL DEFAULT 0,
    -- Фід джерела, з якого прийшла вакансія (див. `feed` у models.py).
    feed TEXT NOT NULL DEFAULT '',
    -- Досвід у місяцях і тип зайнятості — як їх дає джерело, без інтерпретації.
    experience_months INTEGER,
    employment_type TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS vacancies_seen_idx ON vacancies(first_seen_at);
CREATE INDEX IF NOT EXISTS vacancies_source_idx ON vacancies(source, source_uid);

-- Службові позначки джерел: мітка останнього успішного забору для тих,
-- хто вміє тягнути інкрементально (див. `Incremental` у sources/base.py).
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Особисті позначки власника: «відгукнувся» / «не цікавить».
--
-- 🔴 ЄДИНЕ в цьому проєкті, що створює людина, а не наш конвеєр. Усе інше
-- відновлюється наступним прогоном; це — ні. Тому позначки живуть у БД, а
-- localStorage лишається кешем на випадок, коли сервер не запущено.
--
-- Ключ — той самий детермінований id вакансії, sha1(source:source_uid): він
-- не залежить від часу прогону, тож позначка лягає назад на ту саму вакансію
-- після щогодинної перегенерації сторінки. З випадковим id усе це не працює.
--
-- Рядок НЕ привʼязаний зовнішнім ключем до `vacancies` навмисно: вакансія
-- може випасти з вікна, а позначка мусить пережити її повернення.
CREATE TABLE IF NOT EXISTS marks (
    id       TEXT PRIMARY KEY,
    applied  INTEGER NOT NULL DEFAULT 0,
    skipped  INTEGER NOT NULL DEFAULT 0,
    changed_at TEXT NOT NULL DEFAULT ''
);
"""


def now_stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _to_vacancy(row: sqlite3.Row) -> Vacancy:
    return Vacancy(
        source=row["source"],
        title=row["title"],
        company=row["company"],
        location=row["location"],
        url=row["url"],
        tags=tuple(json.loads(row["tags"])),
        posted_at=datetime.fromisoformat(row["posted_at"]) if row["posted_at"] else None,
        source_uid=row["source_uid"],
        salary=row["salary"],
        description=row["description"],
        reservation_hint=row["reservation_hint"],
        missing_streak=row["missing_streak"],
        feed=row["feed"],
        experience_months=row["experience_months"],
        employment_type=row["employment_type"],
    )


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """Догортає схему на базах, створених попередніми версіями."""
        have = {row["name"] for row in self.conn.execute("PRAGMA table_info(vacancies)")}

        # `valid_through` прибрано разом із фільтром протухлих: у Djinni це
        # виявилась формула `datePosted + 30 днів`, а не факт закриття, і
        # сигнал зникнення робить ту саму роботу за фактичним станом джерела.
        # Колонку саме ВИДАЛЯЄМО, а не лишаємо порожньою: дані, які пишуться
        # й не читаються, наступний читач прийме за робочі.
        if "valid_through" in have:
            self.conn.execute("ALTER TABLE vacancies DROP COLUMN valid_through")
            have.discard("valid_through")
        for column, ddl in (
            ("description", "TEXT NOT NULL DEFAULT ''"),
            ("reservation_hint", "TEXT NOT NULL DEFAULT ''"),
            ("missing_streak", "INTEGER NOT NULL DEFAULT 0"),
            ("feed", "TEXT NOT NULL DEFAULT ''"),
            ("experience_months", "INTEGER"),
            ("employment_type", "TEXT NOT NULL DEFAULT ''"),
        ):
            if column not in have:
                self.conn.execute(f"ALTER TABLE vacancies ADD COLUMN {column} {ddl}")

    def known_ids(self, ids: list[str]) -> set[str]:
        if not ids:
            return set()
        placeholders = ",".join("?" * len(ids))
        rows = self.conn.execute(
            f"SELECT id FROM vacancies WHERE id IN ({placeholders})", ids
        ).fetchall()
        return {row["id"] for row in rows}

    def new_only(self, vacancies: list[Vacancy]) -> list[Vacancy]:
        """Відсіює те, що вже бачили в попередніх прогонах."""
        known = self.known_ids([v.id for v in vacancies])
        return [v for v in vacancies if v.id not in known]

    def remember(self, vacancies: list[Vacancy], stamp: str | None = None) -> str:
        """Запамʼятовує партію. Ідемпотентно: повторний запис не створює дублів."""
        stamp = stamp or now_stamp()
        rows = [
            (
                v.id, v.source, v.source_uid, v.title, v.company, v.location,
                v.salary, v.url, json.dumps(list(v.tags), ensure_ascii=False),
                v.posted_at.isoformat() if v.posted_at else None, stamp, v.description,
                v.reservation_hint,
                v.feed, v.experience_months, v.employment_type,
            )
            for v in vacancies
        ]
        with self.conn:
            self.conn.executemany(
                "INSERT OR IGNORE INTO vacancies "
                "(id, source, source_uid, title, company, location, salary, url, "
                " tags, posted_at, first_seen_at, description, reservation_hint, "
                " feed, experience_months, employment_type) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        return stamp

    def recent(self, days: int = 14) -> list[tuple[Vacancy, str]]:
        """Вакансії за останні N днів, найсвіжіші першими. Разом з міткою партії."""
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
        rows = self.conn.execute(
            # Найсвіжіші вгорі — за датою ПУБЛІКАЦІЇ, не за часом нашого
            # забору: це різні речі. Записи без дати публікації йдуть у
            # кінець: відсутність дати не робить вакансію свіжою.
            "SELECT * FROM vacancies WHERE first_seen_at >= ? "
            "ORDER BY (posted_at IS NULL), posted_at DESC, first_seen_at DESC",
            (since,),
        ).fetchall()
        return [(_to_vacancy(row), row["first_seen_at"]) for row in rows]

    def incomplete(self, sources: list[str], days: int, limit: int) -> list[Vacancy]:
        """Збережені вакансії без компанії — кандидати на добір.

        Потрібно тому, що збагачення обмежене бюджетом запитів на прогін:
        на холодному старті сотня вакансій лишається без компанії й локації,
        і без цього добору вони б лишились такими назавжди — новими вони
        вже не рахуються, отже під `enrich` більше ніколи не потраплять.
        """
        if not sources:
            return []
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
        placeholders = ",".join("?" * len(sources))
        rows = self.conn.execute(
            f"SELECT * FROM vacancies WHERE company = '' AND first_seen_at >= ? "
            f"AND source IN ({placeholders}) "
            "ORDER BY COALESCE(posted_at, first_seen_at) DESC LIMIT ?",
            [since, *sources, limit],
        ).fetchall()
        return [_to_vacancy(row) for row in rows]

    # Поля, які добирає збагачення. Перелік один на всі — саме тому правило
    # «не стирати» діє на них разом, а не окремим винятком для кожного.
    ENRICHED = (
        "title", "company", "location", "salary",
        "tags", "description", "experience_months", "employment_type",
    )

    def update(self, vacancies: list[Vacancy]) -> int:
        """Збагачення може лише ЗАПОВНЮВАТИ й уточнювати, ніколи не стирати.

        🔴 Правило: порожнє значення в пізнішому заборі НЕ Є ТВЕРДЖЕННЯМ, що
        значення зникло. Джерело могло не віддати поле цього разу з десятка
        причин, і жодна з них не означає «даних більше немає».

        Це той самий принцип, за яким уже вирішені `reservation_hint` (тільки
        `yes`, ніколи `no`), наївна дата (`None`, а не «вважаємо UTC») і
        відмова від `validThrough`. Тут він просто застосований до ЗАПИСУ в
        базу, а не до читання.

        Без цього правила збагачення, яке цього разу не віддало досвід,
        затирало б реальні 36 на NULL — тихо, без помилки, за кілька прогонів.
        Раніше від цього беріг лише фільтр `company = ''` в `incomplete()`,
        написаний зовсім для іншого: на повторний забір потрапляли тільки
        рядки без компанії. Тобто нас беріг побічний ефект, а не правило —
        і найприродніше наступне покращення (добирати ще й рядки без досвіду)
        зняло б цей захист, не торкнувшись жодного рядка тут.
        """
        def keep(value):
            """Порожнє → None, щоб `COALESCE` лишив те, що вже в базі.

            Порівняння явне, а не через `if not value`: нуль місяців досвіду
            («без досвіду») — реальне значення, і воно мусить записуватись.
            """
            return None if value in ("", (), [], None) else value

        rows = [
            tuple(
                keep(v)
                for v in (
                    vacancy.title, vacancy.company, vacancy.location, vacancy.salary,
                    json.dumps(list(vacancy.tags), ensure_ascii=False) if vacancy.tags else None,
                    vacancy.description, vacancy.experience_months, vacancy.employment_type,
                )
            ) + (vacancy.id,)
            for vacancy in vacancies
        ]
        assignments = ", ".join(f"{col} = COALESCE(?, {col})" for col in self.ENRICHED)
        with self.conn:
            self.conn.executemany(
                f"UPDATE vacancies SET {assignments} WHERE id = ?", rows
            )
        return len(rows)

    def mark_absence(
        self, source: str, feed: str, present_anywhere: set[str], since: str
    ) -> int:
        """Промах для записів цього фіда, яких немає НІДЕ в джерелі.

        Рахується в межах ОДНОГО фіда: сусідній фід того самого джерела
        може бути обрізаний або стабільний, і це не привід не робити
        висновків тут.

        `present_anywhere` — id усіх вакансій, що прийшли цього прогону з
        БУДЬ-ЯКОГО чистого фіда джерела, а не лише з `feed`. Це принципово:
        роботодавець може змінити категорію вакансії, і тоді вона зникає зі
        свого фіда назавжди, лишаючись живою в сусідньому. Перевіряючи
        присутність тільки в межах одного фіда, ми позначили б її закритою.

        Обнулення теж по джерелу, а не по фіду: у БД `feed` заморожений на
        тому, у якому вакансія трапилась уперше (`INSERT OR IGNORE` рядок
        більше не оновлює), тож у переїхалої запис лишається зі старим фідом.

        `since` — межа покриття ЦЬОГО фіда: усе старіше могло випасти за
        край його вікна, а не бути знятим.
        """
        ids = list(present_anywhere)
        placeholders = ",".join("?" * len(ids)) if ids else "''"
        with self.conn:
            if ids:
                self.conn.execute(
                    f"UPDATE vacancies SET missing_streak = 0 "
                    f"WHERE source = ? AND id IN ({placeholders})",
                    [source, *ids],
                )
            # Інкремент теж виключає всю присутність, а не лише свій фід —
            # інакше переїхалий запис щоразу отримував би +1 і одразу −1 і
            # застряг би на 1 замість 0. На спрацювання це не впливає, але
            # число в БД брехало б, а ми на нього дивимось у лічильнику.
            self.conn.execute(
                f"UPDATE vacancies SET missing_streak = missing_streak + 1 "
                f"WHERE source = ? AND feed = ? AND id NOT IN ({placeholders}) "
                f"AND posted_at IS NOT NULL AND posted_at >= ?",
                [source, feed, *ids, since],
            )
            # Вийшла за межу покриття, поки набирала промахи, — серія
            # скидається. Інакше вакансію, яку виштовхав сплеск публікацій,
            # ми позначили б закритою на підставі промахів, зарахованих тоді,
            # коли вікно було ширшим.
            self.conn.execute(
                f"UPDATE vacancies SET missing_streak = 0 "
                f"WHERE source = ? AND feed = ? AND id NOT IN ({placeholders}) "
                f"AND (posted_at IS NULL OR posted_at < ?)",
                [source, feed, *ids, since],
            )
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM vacancies "
            "WHERE source = ? AND feed = ? AND missing_streak >= 2",
            (source, feed),
        ).fetchone()
        return row["n"]

    def reset_absence(self, source: str) -> int:
        """Брудний прогін розриває серію промахів для всього джерела.

        Не «пропускається»: відсутність у прогоні, де ми свідомо не дивились,
        не є доказом зняття вакансії. Повертає, скільки серій обірвано —
        якщо на джерелі це стається постійно, сигнал там мертвий, і це має
        бути видно числом, а не лишатись здогадкою.
        """
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM vacancies WHERE source = ? AND missing_streak > 0",
            (source,),
        ).fetchone()
        with self.conn:
            self.conn.execute(
                "UPDATE vacancies SET missing_streak = 0 WHERE source = ?", (source,)
            )
        return row["n"]

    def marks(self) -> dict[str, dict]:
        """Усі позначки. Порожні (обидва прапорці зняті) не зберігаються."""
        return {
            row["id"]: {"applied": bool(row["applied"]), "skipped": bool(row["skipped"])}
            for row in self.conn.execute(
                "SELECT id, applied, skipped FROM marks "
                "WHERE applied = 1 OR skipped = 1"
            )
        }

    def set_mark(self, vacancy_id: str, applied: bool, skipped: bool) -> None:
        if not applied and not skipped:
            self.conn.execute("DELETE FROM marks WHERE id = ?", (vacancy_id,))
        else:
            self.conn.execute(
                "INSERT INTO marks (id, applied, skipped, changed_at) VALUES (?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET applied = excluded.applied, "
                "skipped = excluded.skipped, changed_at = excluded.changed_at",
                (vacancy_id, int(applied), int(skipped), now_stamp()),
            )
        self.conn.commit()

    def import_marks(self, incoming: dict) -> tuple[int, int]:
        """Переносить позначки з браузера в базу. Повертає (додано, вже було).

        🔴 ОБʼЄДНАННЯ, а не заміна, і в один бік. Позначка, якої немає в базі,
        додається; та, що вже є, НЕ чіпається. Причина: імпорт приходить із
        конкретного браузера, а браузерів може бути кілька, і жоден із них не
        має права стерти те, чого просто не бачив. Порожній localStorage
        («я щойно почистив кеш») не сміє вичистити базу — а саме так виглядав
        би «синхронізувати як у клієнта».

        Це той самий вибір, що в `update()` для збагачення: заповнювати —
        можна, стирати — ні.
        """
        existing = set(self.marks())
        added = same = 0
        for vacancy_id, mark in (incoming or {}).items():
            if not isinstance(mark, dict):
                continue
            applied = bool(mark.get("applied"))
            skipped = bool(mark.get("skipped"))
            if not applied and not skipped:
                continue
            if vacancy_id in existing:
                same += 1
                continue
            self.conn.execute(
                "INSERT OR IGNORE INTO marks (id, applied, skipped, changed_at) "
                "VALUES (?,?,?,?)",
                (vacancy_id, int(applied), int(skipped), now_stamp()),
            )
            added += 1
        self.conn.commit()
        return added, same

    def get_meta(self, key: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) AS n FROM vacancies").fetchone()["n"]

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
