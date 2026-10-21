"""Локальний сервер: та сама сторінка, але з памʼяттю й кнопками.

Що він додає до статичного `digest.html`
----------------------------------------
1. **Позначки живуть у БД.** У файлі вони лежать у `localStorage`, тобто
   привʼязані до одного браузера й одного профілю. Чистка даних сайту —
   і єдине, що власник створив руками, зникає без сліду й без попередження.
2. **Додати джерело з меню** — з перевіркою ДО запису.
3. **«Оновити зараз»** — прогін без походу в термінал.

Чого він НЕ робить, свідомо
---------------------------
* **Не заміняє файл.** `digest.html` пишеться щогодинним прогоном як раніше
  й лишається повністю робочим із вимкненим сервером. Сервер — другий спосіб
  дивитись, а не єдиний. Тому вся серверна логіка на сторінці ввімкнена лише
  тоді, коли `/api/ping` відповів: файл, відкритий з диска, поводиться точно
  як досі.
* **Не веде розкладу.** Щогодинний прогін лишається задачею планувальника —
  двох джерел правди про розклад не буде. Кнопка «оновити зараз» це ручний
  запуск, а не заміна розкладу.
* **Ні автентифікації, ні HTTPS.** Це localhost.
* **Не переносить фільтри в інтерфейс.** `filter.toml` лишається місцем правди;
  форма додавання джерела саме в нього й пише.

Чому слухаємо тільки 127.0.0.1
------------------------------
`0.0.0.0` віддав би сторінку всій локальній мережі — разом із кнопкою
«оновити зараз» і формою, що пише в конфіг, без жодного пароля. У кавʼярні
це означає, що будь-хто в тій самій Wi-Fi мережі може дописати нам джерело.
Прапорець у конфізі існує, але вмикається лише руками й з поясненням.
"""

from __future__ import annotations

import json
import threading
import traceback
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from . import config as config_module
from . import pipeline
from .paths import resolve
from .sources.dou_companies import SlugError, check_slug
from .store import Store

DEFAULT_PORT = 8765
DEFAULT_HOST = "127.0.0.1"


class RunState:
    """Стан ручного прогону — щоб кнопка показувала, що відбувається.

    Прогін іде в окремому потоці: він триває десятки секунд, і сторінка не
    має на цей час завмирати. Тому потрібен саме стан, а не просто відповідь.
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.running = False
        self.started_at = ""
        self.finished_at = ""
        self.summary = ""
        self.error = ""

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "running": self.running,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "summary": self.summary,
                "error": self.error,
            }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def check_source(kind: str, value: str, timeout: float = 20.0) -> dict:
    """Перевіряє джерело ДО запису в конфіг. Нічого не зберігає.

    🔴 Записати непрацюючий слаг мовчки — це створити рядок, який вічно
    показуватиме помилку, і зробити це руками власника. Тому перевірка
    відповідає на три різні питання окремо: чи слаг узагалі валідний, чи
    відповідає URL, і чи це справді фід із записами.

    Порожній фід — НЕ привід відмовити: у шести компаній із двадцяти однієї
    зараз немає відкритих вакансій, і це нормальний стан. Але сказати про це
    треба ДО збереження, інакше власник додасть компанію, побачить нуль і
    вирішить, що щось зламано.
    """
    import feedparser
    import httpx

    from .sources.dou import USER_AGENT
    from .sources.dou_companies import FEED_URL as COMPANY_URL

    if kind == "dou_company":
        try:
            slug = check_slug(value)
        except SlugError as exc:
            return {"ok": False, "reason": str(exc)}
        url = COMPANY_URL.format(slug=slug)
        label = slug
    elif kind == "dou_category":
        category = (value or "").strip()
        if not category:
            return {"ok": False, "reason": "порожня назва категорії"}
        if not category.isascii():
            # Кирилиця в category дає HTTP 400 — назва категорії на сайті
            # латиницею. Ловимо тут, щоб не списати це на «сайт не відповів».
            return {"ok": False,
                    "reason": "назва категорії на DOU латиницею, кирилиця дає HTTP 400"}
        url = f"https://jobs.dou.ua/vacancies/feeds/?category={category}"
        label = category
    else:
        return {"ok": False, "reason": f"невідомий вид джерела: {kind!r}"}

    try:
        response = httpx.get(url, headers={"User-Agent": USER_AGENT},
                             follow_redirects=True, timeout=timeout)
    except Exception as exc:
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}", "url": url}

    if response.status_code != 200:
        reason = f"HTTP {response.status_code}"
        if response.status_code == 404:
            reason += " — такої сторінки на DOU немає, перевірте слаг"
        return {"ok": False, "reason": reason, "url": url}

    entries = feedparser.parse(response.content).entries
    if kind == "dou_category" and not entries:
        # DOU віддає 200 і порожній фід на неіснуючу категорію — помилки не
        # буде. Для категорії це майже напевно одруківка у ЗНАЧЕННІ, тому
        # відмовляємо; для компанії нуль означає «немає вакансій» і це нормально.
        return {"ok": False, "url": url,
                "reason": "фід порожній — на DOU такої категорії, найімовірніше, "
                          "немає (сайт не вважає це помилкою)"}

    return {
        "ok": True,
        "url": url,
        "label": label,
        "count": len(entries),
        "note": ("зараз відкритих вакансій немає — це нормально, рядок буде "
                 "з нулем" if not entries else ""),
        "sample": [getattr(e, "title", "")[:80] for e in entries[:3]],
    }


def add_source(config_path: Path, kind: str, value: str) -> dict:
    """Дописує перевірене джерело у filter.toml. Перевіряє ще раз перед записом.

    Перевірка повторюється навмисно: між натисканням «перевірити» і
    «зберегти» проходить час, а записаний зламаний слаг коштує дорожче за
    один зайвий запит.
    """
    result = check_source(kind, value)
    if not result.get("ok"):
        return result

    text = config_path.read_text(encoding="utf-8")
    label = result["label"]

    if kind == "dou_company":
        if f'"{label}"' in text:
            return {"ok": False, "reason": f"слаг {label} уже є в конфізі"}
        marker = "slugs = ["
        if marker not in text:
            return {"ok": False,
                    "reason": "у filter.toml немає секції [sources.dou_companies]"}
        at = text.index(marker) + len(marker)
        stamp = datetime.now().strftime("%d.%m.%Y")
        text = (text[:at] + f'\n  "{label}",  # додано зі сторінки {stamp}, '
                f'у фіді було {result["count"]}' + text[at:])
    else:
        section = "\n[[sources.dou.feeds]]\n"
        if f'category = "{label}"' in text:
            return {"ok": False, "reason": f"категорія {label} уже є в конфізі"}
        stamp = datetime.now().strftime("%d.%m.%Y")
        text += (f"{section}# додано зі сторінки {stamp}, у фіді було "
                 f"{result['count']}\ncategory = \"{label}\"\n")

    config_path.write_text(text, encoding="utf-8")
    result["written_to"] = str(config_path)
    return result


class Handler(BaseHTTPRequestHandler):
    server_version = "find_new"

    # ── службове ────────────────────────────────────────────────────────
    def log_message(self, fmt, *args):  # без шуму в консолі
        pass

    def _allow_origin(self) -> str:
        """Кому дозволяємо звертатись із чужого origin — і чому саме їм.

        🔴 Це існує заради ОДНОГО випадку: `digest.html`, відкритий із диска,
        має origin `null` і не бачить `localStorage` сторінки на localhost —
        це різні origin. Тобто позначки, наставлені у файловій версії, при
        першому запуску сервера не мігрували б НІЯК: автоматичне перенесення
        шукало б їх там, де їх немає, знаходило порожньо й рапортувало успіх.

        Дозволяємо тільки `null` (файл із диска) і сам localhost. `*` тут
        означав би, що будь-який сайт у відкритій вкладці може писати нам
        позначки й запускати прогони.
        """
        origin = self.headers.get("Origin", "")
        if origin == "null" or origin.startswith(("http://127.0.0.1", "http://localhost")):
            return origin
        return ""

    def do_OPTIONS(self) -> None:
        origin = self._allow_origin()
        self.send_response(204 if origin else 403)
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Methods", "POST, GET")
        self.end_headers()

    def _send(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        origin = self._allow_origin()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, text: str) -> None:
        body = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # Сторінку перемальовує прогін; кеш браузера показував би вчорашню.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}

    # ── маршрути ────────────────────────────────────────────────────────
    def do_GET(self) -> None:
        path = urlparse(self.path).path
        try:
            if path in ("/", "/index.html", "/digest.html"):
                return self._send_html(self.server.app.page())
            if path == "/api/ping":
                return self._send({"ok": True, "server": "find_new"})
            if path == "/api/marks":
                return self._send({"marks": self.server.app.marks()})
            if path == "/api/status":
                return self._send(self.server.app.state.snapshot())
        except Exception as exc:
            traceback.print_exc()
            return self._send({"ok": False, "reason": f"{type(exc).__name__}: {exc}"}, 500)
        self._send({"ok": False, "reason": "невідомий маршрут"}, 404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path

        # 🔴 Заголовок CORS ховає ВІДПОВІДЬ, але не спиняє ЗАПИТ.
        #
        # Знайдено на власних даних: після тесту з `Origin: https://evil...`
        # у базі лежала записана ним позначка. Заголовка `Access-Control-
        # Allow-Origin` у відповіді не було — і виглядало, ніби відмовлено.
        # Насправді сервер спокійно виконав дію, а браузер лише не показав би
        # результат. Для `/api/sources/add` це запис у конфіг, для
        # `/api/refresh` — запуск прогону: обидва не потребують читання
        # відповіді, щоб спрацювати.
        #
        # Тому перевіряємо origin на самій ДІЇ. Відсутній origin — це не
        # браузер (curl, скрипт на цій же машині), і це дозволено: браузер на
        # міжсайтовому POST заголовок ставить завжди.
        origin = self.headers.get("Origin")
        if origin is not None and not self._allow_origin():
            return self._send(
                {"ok": False, "reason": "запит із чужого origin відхилено"}, 403
            )
        # Відсутній `Origin` дозволено ТІЛЬКИ поки ми слухаємо петлю.
        #
        # Обґрунтування «браузер на міжсайтовому POST ставить Origin завжди,
        # тож його відсутність = локальний не-браузерний клієнт» вірне рівно
        # доти, доки дістатись до порту може лише цей компʼютер. Прапорець
        # `serve_host` існує — і в той день, коли його ввімкнуть, ця гілка з
        # допустимої стане дірою, а згадає про неї ніхто.
        #
        # Тому вона привʼязана до режиму явно, а не лишається наслідком.
        if origin is None and not self.server.loopback_only:
            return self._send(
                {"ok": False,
                 "reason": "сервер слухає не лише 127.0.0.1 — запити без "
                           "заголовка Origin у цьому режимі не приймаються"}, 403
            )

        data = self._body()
        app = self.server.app
        try:
            if path == "/api/marks":
                return self._send(*app.apply_mark(data))
            if path == "/api/marks/import":
                added, same = app.import_marks(data.get("marks") or {})
                return self._send({"ok": True, "added": added, "already": same,
                                   "marks": app.marks()})
            if path == "/api/sources/check":
                return self._send(check_source(str(data.get("kind", "")),
                                               str(data.get("value", ""))))
            if path == "/api/sources/add":
                return self._send(add_source(app.config_path,
                                             str(data.get("kind", "")),
                                             str(data.get("value", ""))))
            if path == "/api/refresh":
                return self._send(app.start_run())
        except Exception as exc:
            traceback.print_exc()
            return self._send({"ok": False, "reason": f"{type(exc).__name__}: {exc}"}, 500)
        self._send({"ok": False, "reason": "невідомий маршрут"}, 404)


class App:
    def __init__(self, config_path: str) -> None:
        self.config_path = Path(config_path)
        self.state = RunState()
        # Одне зʼєднання з БД на потік: sqlite не любить, коли один обʼєкт
        # ходить між потоками, а прогін іде саме в окремому.
        self._local = threading.local()

    @property
    def config(self) -> dict:
        return config_module.load(str(self.config_path))

    @property
    def db_path(self):
        return resolve(self.config.get("run", {}).get("db", pipeline.DEFAULT_DB))

    def _store(self) -> Store:
        store = getattr(self._local, "store", None)
        if store is None:
            store = Store(self.db_path)
            self._local.store = store
        return store

    def page(self) -> str:
        """Той самий файл, що й на диску.

        Свідомо читаємо ФАЙЛ, а не рендеримо наново: інакше сервер і
        планувальник малювали б сторінку двома різними шляхами, і розбіжність
        між ними вилізла б у найгіршу мить — коли щось із них зламається.
        """
        path = resolve(self.config.get("run", {}).get("digest", "digest.html"))
        if not path.exists():
            return ("<h1>Сторінки ще немає</h1><p>Запустіть прогін: "
                    "<code>python run.py run</code> або кнопкою «оновити зараз» "
                    "після першого запуску.</p>")
        return path.read_text(encoding="utf-8")

    def marks(self) -> dict:
        return self._store().marks()

    # Поля стану позначки. Перелічені явно, бо саме за цим переліком
    # відрізняється «зніми» від «не чіпай».
    MARK_FIELDS = ("applied", "skipped")

    def apply_mark(self, data: dict) -> tuple[dict, int]:
        """Зміна позначки. ВІДСУТНЄ поле означає «не чіпай», а не «зніми».

        🔴 Перша версія робила `bool(data.get("applied"))`, і цього досить,
        щоб втратити дані назавжди:

            {"id": "x", "aplied": true}   ← одруківка в назві поля
            → applied=False, skipped=False → позначку ВИДАЛЕНО
            → у відповіді {"ok": true}

        Тобто нерозпізнаний запит тихо робив ПРОТИЛЕЖНЕ до задуманого й
        звітував успіх. Зловмисник для цього не потрібен: досить одруківки у
        фронті, перейменованого поля або старого клієнта після оновлення API.
        А позначки — єдине в проєкті, чого не можна відновити з джерел.

        Три наміри більше не зводяться до одного:
          `{"applied": true}`  — постав
          `{"applied": false}` — зніми
          поля немає           — не чіпай саме це поле

        Запит зовсім без відомих полів відхиляється з причиною. Те саме
        правило, що з `hint`, наївною датою й `update()`: **мовчання не є
        твердженням.** Тут воно вперше про запит, а не про джерело.
        """
        vacancy_id = str(data.get("id", "")).strip()
        if not vacancy_id:
            return {"ok": False, "reason": "не вказано id вакансії"}, 400

        given = [name for name in self.MARK_FIELDS if name in data]
        if not given:
            return {
                "ok": False,
                "reason": "не вказано жодного відомого поля стану "
                          f"({', '.join(self.MARK_FIELDS)}) — запит не виконано, "
                          "щоб не стерти позначку через одруківку",
                "known_fields": list(self.MARK_FIELDS),
            }, 400

        store = self._store()
        current = store.marks().get(vacancy_id, {"applied": False, "skipped": False})
        state = {
            name: bool(data[name]) if name in data else bool(current.get(name))
            for name in self.MARK_FIELDS
        }
        store.set_mark(vacancy_id, state["applied"], state["skipped"])
        return {"ok": True, "id": vacancy_id, "changed": given, **state}, 200

    def set_mark(self, vacancy_id: str, applied: bool, skipped: bool) -> None:
        if vacancy_id:
            self._store().set_mark(vacancy_id, applied, skipped)

    def import_marks(self, incoming: dict) -> tuple[int, int]:
        return self._store().import_marks(incoming)

    def start_run(self) -> dict:
        with self.state.lock:
            if self.state.running:
                return {"ok": False, "reason": "прогін уже йде", "running": True}
            self.state.running = True
            self.state.started_at = _now()
            self.state.finished_at = ""
            self.state.error = ""
            self.state.summary = ""
        threading.Thread(target=self._run, daemon=True).start()
        return {"ok": True, "running": True}

    def _run(self) -> None:
        summary, error = "", ""
        try:
            report = pipeline.run(self.config)
            # ⚠️ «карток усього», а не «на сторінці». Число правдиве в обох
            # формулюваннях — це справді всі картки сторінки, 365 = 343 живі
            # + 22 ймовірно закриті (заміряно в DOM). Але тепер зведення
            # висить поруч із рядком «показано 142 з 343», і два «на
            # сторінці» з різними числами читаються як суперечність, хоча
            # рахують різне. Правдиве й зрозуміле — не одне й те саме.
            summary = (f"зібрано {report.fetched} · пройшло фільтр {report.kept} · "
                       f"нових {report.fresh} · карток усього {report.window}")
            if report.critical_errors:
                # Критичний збій не має розчинитись у зведенні: на сторінці
                # він мусить читатись як невдача, а не як число серед чисел.
                error = "; ".join(report.critical_errors)
        except Exception as exc:
            traceback.print_exc()
            error = f"{type(exc).__name__}: {exc}"
        with self.state.lock:
            self.state.running = False
            self.state.finished_at = _now()
            self.state.summary = summary
            self.state.error = error


def serve(config_path: str, host: str | None = None, port: int | None = None) -> int:
    app = App(config_path)
    run_cfg = app.config.get("run", {})
    host = host or str(run_cfg.get("serve_host", DEFAULT_HOST))
    port = int(port or run_cfg.get("serve_port", DEFAULT_PORT))

    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.app = app
    # Від цього залежить, чи можна довіряти запиту без `Origin`.
    httpd.loopback_only = host in ("127.0.0.1", "::1", "localhost")

    print(f"find_new · http://{'localhost' if host == DEFAULT_HOST else host}:{port}")
    if host != DEFAULT_HOST:
        # Не ховаємо це в лог-рядок серед інших: наслідок відрізняється не
        # зручністю, а тим, хто ще має доступ до кнопок.
        print("⚠️  СЛУХАЄМО НЕ ТІЛЬКИ 127.0.0.1 — сторінка з кнопкою «оновити»\n"
              "    і формою запису в конфіг доступна всій локальній мережі,\n"
              "    без пароля. Приберіть serve_host з конфіга, щоб вимкнути.")
    print("зупинити: Ctrl+C")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nсервер зупинено")
    finally:
        httpd.server_close()
    return 0
