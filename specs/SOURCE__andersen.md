# Спека адаптера: Andersen

> Розвідка: researcher `2cb8da5a`, 27.07.2026. Перевірено живими запитами.
> Вердикт: 🟢 **легко** (перекваліфіковано з 🟡) · ціна ~1 год · **але цінність низька, див. §7**.

## 0. Що змінилося з 15.07

Дві правки до попереднього зрізу:

1. **Домен переїхав:** `people.andersenlab.com` → **`people-andersenlab.com`** (дефіс замість крапки). Старий 301-редиректить на новий, тож поки не зламано, але канонічним брати новий.
2. **Внутрішній API знайдено.** Попередній вердикт «🟡, API не знайдено» **скасовано**. Playwright не потрібен.

## 1. Спосіб доступу

**Публічний JSON API. Без ключа, без токена, без cookie.**

```
GET https://asite-api.andersenlab.com/api/integration/recruitment/vacancies
```

| | |
|---|---|
| Метод | `GET` |
| Заголовки | `Accept-Language: en` (опц.), `X-Country-Code: UA` (опц.) — обидва **не обов'язкові**, без них теж 200 |
| Відповідь | `application/json`, HTTP 200, **2.4 МБ** |
| Тіло | **плоский JSON-масив** (не об'єкт-обгортка), 212 елементів |

Одна вакансія: `GET .../api/integration/recruitment/vacancies/{vacancy_id}` → 200, ~10 КБ. Для нашої задачі не потрібен — список уже містить усе.

### Як знайдено (щоб можна було перевірити при поломці)

Сайт — Next.js, `pageProps` порожній, дані тягне клієнт. У бандлі `/_next/static/chunks/pages/_app-*.js` лежить API-клієнт:

```js
let {getApiUrl:o} = n(75428), i = `${o()}/api/`;
...
getVacancies(e,t){
  let n={"Accept-Language":e,"X-Country-Code":t?.toUpperCase()??""};
  return this.get("integration/recruitment/vacancies", n)
}
```

а `getApiUrl` — у модулі 75428: `return "https://asite-api.andersenlab.com"`.

⚠️ База зашита в бандл (не в env). При редеплої з іншим хостом адаптер відвалиться — перевіряти тим самим шляхом.

### Реальний фрагмент відповіді (обрізаний, бойовий запит 27.07.2026)

```json
[
  {
    "vacancy_id": 2508965,
    "matrix": {"specialization": "Design", "level": "M2"},
    "specializations": ["Design"],
    "isHot": false,
    "technologies": [],
    "name": "Product Designer in the EU",
    "level": "Middle",
    "isRemote": false,
    "isWorldWide": false,
    "duration": null,
    "hiringTag": null,
    "lookingLocations": [
      {"countryCode":"NO","countryName":"Norway","region":"EU","cities":[]},
      {"countryCode":"DE","countryName":"Germany","region":"EU","cities":[]}
    ],
    "notLookingLocations": [
      {"countryCode":"RU","countryName":"R7","region":"OTHER","cities":[]}
    ],
    "salary": {"minSalary":3050,"maxSalary":4550,"currencyCode":"EUR",
               "countryCode2":"EU","countryCode3":"EUR"},
    "summary":        [{"TYPE":"text","content":"Andersen is hiring a ..."}],
    "requirements":   [{"TYPE":"list","content":["Experience ... 5+ years. ", "..."]}],
    "responsibilities":[{"TYPE":"list","content":["Designing and developing ...","..."]}],
    "niceToHave":     [{"TYPE":"list","content":["..."]}],
    "joinUsReason":   [{"TYPE":"list","content":["..."]}]
  }
]
```

**Рівно 19 полів, присутні у 212/212 записів** (перевірено підрахунком): `vacancy_id, matrix, specializations, isHot, technologies, name, niceToHave, requirements, responsibilities, summary, joinUsReason, duration, hiringTag, isRemote, level, isWorldWide, lookingLocations, notLookingLocations, salary`.

## 2. Мапа полів → наша схема

| Наше поле | Звідки | Як |
|---|---|---|
| `source` | — | константа `"andersen"` |
| `title` | `name` | напряму, чистий рядок — **парсинг не потрібен** (на відміну від DOU) |
| `company` | — | константа `"Andersen"` (у джерелі поля немає, але воно і не потрібне: це кар'єрка однієї компанії) |
| `url` | `vacancy_id` | **конструюється:** `https://people-andersenlab.com/vacancy/{vacancy_id}` — перевірено, HTTP 200 |
| `location` | `lookingLocations[].countryName` + `isRemote` + `isWorldWide` | склеїти країни; якщо `isWorldWide` → `"worldwide"` |
| `tags` | `specializations` + `technologies` + `level` | справжні структуровані теги, на відміну від DOU |
| `posted_at` | 🔴 **НЕМАЄ В ДЖЕРЕЛІ** | див. нижче |

### 🔴 Чого в джерелі НЕМА (не додумувати)

- **Жодного поля з датою.** Я перевірив усі ключі всіх 212 записів на `date/time/created/publish/updated` — **збігів нуль**. Немає ні дати публікації, ні оновлення, ні сортування за часом.
  - **Наслідок для архітектури:** `posted_at` для Andersen чесно `None`. «Свіжість» визначити неможливо в принципі. Єдиний спосіб зрозуміти, що вакансія нова — **порівняти множину `vacancy_id` з попереднім запуском** (тобто дедуп у SQLite тут не оптимізація, а єдине джерело поняття «нове»).
  - Це також означає: **перший запуск адаптера дасть 212 «нових» вакансій**. Потрібен «холодний старт» — засіяти базу без відправки в Telegram.
- **Немає URL** — тільки конструюється з ID.
- `duration` і `hiringTag` присутні як ключі, але `null` у переважній більшості записів.

## 3. Фільтри на боці джерела

**Немає.** Ендпоінт не приймає параметрів фільтрації — віддає весь масив 212 вакансій завжди.

Фільтрація тільки клієнтська, зате поля для неї структуровані й чисті:

| Що фільтрувати | Поле | Значення |
|---|---|---|
| Напрям | `specializations[]` | `"Design"` — точний збіг |
| Рівень | `level` | `Junior` / `Middle` / `Senior` (+ `matrix.level` = `S1`/`M2`…) |
| Віддаленість | `isRemote`, `isWorldWide` | boolean |
| Локація | `lookingLocations[].countryCode` | ISO-2 |

### Словник `specializations` (18 значень, з живих даних)

`Other (39), QA (22), Data Platform (19), DevOps (18), BA & SA (15), Administration (15), .NET (14), GO (13), Java (11), Architect (10), Project management (9), Java Script (7), System administrators (5), Design (5), Python (3), Android (3), iOS (2), PHP (2)`

Плюс: `specializations` тут **чистий** — на відміну від DOU, у `Design` не падають інженери-конструктори. Стоп-лист не потрібен.

## 4. Пагінація

**Не потрібна.** Один запит = весь набір (212 записів, 2.4 МБ). Ні `page`, ні `limit`, ні курсора немає — і не треба.

Ціна: 2.4 МБ трафіку на кожен цикл. При щогодинному опитуванні — ~58 МБ/добу. Прийнятно, але якщо захочеться економити, є `If-None-Match`/`ETag` — **я його не перевіряв**, це відкрите питання для `visual`.

## 5. Стабільний ID

`vacancy_id` — ціле число (напр. `2508965`). Це первинний ключ у їхній системі, він же в URL сторінки. Кращого кандидата немає й не треба.

Ключ дедупу: `("andersen", vacancy_id)`.

## 6. Ризики

| Ризик | Оцінка |
|---|---|
| Rate-limit | не спостерігав; робив ~6 запитів поспіль, жодного 429. Але 2.4 МБ на запит — не молоти без потреби. |
| Анти-бот / Cloudflare | **немає.** Голий curl проходить. |
| Playwright | **не потрібен.** |
| Крихкість | **низька для даних, середня для адреси.** Сам JSON-контракт стабільний і версіонований (`/api/integration/...`). Але base-URL зашитий у JS-бандл — редеплой фронта може його змінити. Редизайн сайту при цьому API НЕ ламає (це його головна перевага над скрейпінгом). |
| Відсутність дат | **головний ризик.** Уся логіка «що нове» тримається виключно на нашій БД. Втрата/скидання БД = 212 дублів у канал. Потрібен захист: при старті з порожньою БД — засіяти, не відправляти. |
| Домен | щойно переїхав (`people.` → `people-`). Компанія, схоже, у процесі міграції — може переїхати ще. |

## 7. Вердикт

🟢 **Технічно легко** (~1 год, `httpx` + `json`, ніяких залежностей понад те).

**АЛЕ цінність для нашої цілі низька — і це важливіше за складність.**

З 212 вакансій до профілю Product / UX-UI підходять **5**:

| id | назва | рівень | remote |
|---|---|---|---|
| 2507104 | UX/UI Designer (India) | Middle | ні |
| 2507753 | Senior UX/UI Designer in India | Senior | ні |
| 2509526 | UX/UI Designer (Abu Dhabi, UAE) | Middle | ні |
| 2508965 | Product Designer in the EU | Middle | ні |
| 2510088 | CX & Service Designer | Middle | ні |

**Усі п'ять — не віддалені, і жодної в Україні** (Індія ×2, ОАЕ, ЄС). Тобто для власника, який шукає Product/UX-UI, Andersen дає майже нуль придатних позицій, і ті — з релокацією.

**Рекомендація:** підключати, але **не в першу чергу**. Адаптер дешевий і не ламкий — хай буде, — проте пріоритет нижчий за будь-який джоб-борд. Ставити в чергу після DOU і Djinni.
