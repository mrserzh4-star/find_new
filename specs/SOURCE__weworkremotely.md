# Спека адаптера: We Work Remotely (weworkremotely.com)

> Розвідка: researcher `2cb8da5a`, 27.07.2026. Перевірено живими запитами.
> Вердикт: 🟡 **технічно тривіальний, за цінністю — найслабший з усіх перевірених.**
> Ціна ~1 год. Рекомендація: брати **останнім** або не брати взагалі — аргументи в §8.

## 1. Спосіб доступу

**Готовий RSS по категоріях. Без ключа, без авторизації.**

```
GET https://weworkremotely.com/categories/remote-design-jobs.rss
```

| | |
|---|---|
| Метод | `GET`, без заголовків |
| Відповідь | `application/rss+xml; charset=utf-8`, HTTP 200, ~400 КБ |
| Записів | **38** у дизайн-фіді |
| `ttl` | 60 (сам сайт рекомендує опитувати раз на годину — точно наш розклад) |

`robots.txt` дозволяє: заборонені лише `/admin/`, `/account/`, `/job-seekers/account/`, `/job-seekers/profile/`, `/manage-company/`. Категорії й `.rss` відкриті.

### Доступні фіди (перевірено)

| фід | записів |
|---|---|
| `remote-design-jobs.rss` | 38 |
| `remote-product-jobs.rss` | 56 |
| `all-other-remote-jobs.rss` | 139 |
| `remote-marketing-jobs.rss` | **301 редірект** — категорії з такою назвою немає |

### Реальний запис (бойовий запит 27.07.2026)

```xml
<item>
  <title>Trust Based Mobility: Video Editing Specialist (Direct-Response / AI Video Editor)</title>
  <region>Anywhere in the World</region>
  <country></country>
  <state>Wyoming</state>
  <skills></skills>
  <category>Design</category>
  <type>Full-Time</type>
  <description>…</description>
  <pubDate>Thu, 23 Jul 2026 20:03:19 +0000</pubDate>
  <expires_at>Sat, 22 Aug 2026 20:03:19 +0000</expires_at>
  <guid>https://weworkremotely.com/remote-jobs/trust-based-mobility-video-editing-specialist…</guid>
  <link>https://weworkremotely.com/remote-jobs/trust-based-mobility-video-editing-specialist…</link>
</item>
```

**Поля (усі присутні в 38/38):** `title`, `region`, `country`, `state`, `skills`, `category`, `type`, `description`, `pubDate`, `expires_at`, `guid`, `link`. Плюс `media:content` у 13 з 38.

Це найбагатший набір полів серед усіх RSS у проєкті — багатший за DOU і Djinni.

## 2. Мапа полів → наша схема

| Наше поле | Звідки | Як |
|---|---|---|
| `source` | — | константа `"wwr"` |
| `title` | `<title>` | частина **після** `": "` |
| `company` | `<title>` | частина **до** `": "` — формат `Компанія: Посада` витриманий у **38 з 38** |
| `location` | `<region>` | + `<state>`/`<country>` за наявності |
| `url` | `<link>` = `<guid>` | напряму, канонічний, без query |
| `tags` | `<category>`, `<skills>`, `<type>` | |
| `posted_at` | `<pubDate>` | RFC-2822 |

Бонус: `<expires_at>` — дата протухання, можна прибирати мертві вакансії. Жодне інше джерело в проєкті цього не дає.

### Чого немає / що ненадійне

- **`<skills>` заповнено в 13 з 38**, `<country>` — **в 1 з 38**. Тобто теги за навичками і країна практично порожні; покладатися на них не можна.
- `<state>` іноді суперечить `<region>`: перший запис має `region = Anywhere in the World` і водночас `state = Wyoming`. Пріоритет — `<region>`.

## 3. Фільтри на боці джерела

Тільки вибір категорії через URL фіда. Query-параметрів немає.

## 4. Пагінація

Немає, та й не потрібна: 38 записів покривають **55 діб** (28.05–23.07). Запас для щогодинного опитування ~×1300 — найбільший у проєкті.

## 5. Стабільний ID

`<guid>` = `<link>` = канонічний URL зі слагом:
`https://weworkremotely.com/remote-jobs/<company>-<position-slug>`

**Числового ID немає** — на відміну від DOU, Djinni й HappyMonday. Ключ дедупу — сам URL.

⚠️ Ризик: слаг складається з назви компанії й посади. Якщо роботодавець відредагує заголовок, URL зміниться → та сама вакансія прилетить як нова. Захисту від цього тут немає, бо стабільнішого ідентифікатора джерело не віддає.

⚠️ **У фіді вже є дублікати.** Знайшов: 1 пара з однаковим `guid` і 2 пари з однаковим заголовком (`Twilio: Principal Presales Engineer`, `Dropbox: Principal Product Designer`). Тобто дедуп потрібен **усередині одного забору**, а не лише між заборами.

## 6. 🔴 Якість: 50% сміття в категорії, і це не суміжні ролі

Категорія `Design` проставлена в 38 з 38 записів. Але за заголовками **19 з 38 не мають до дизайну жодного стосунку**:

```
✗ Stripe: User Risk Strategist, Ecosystem Risk Strategy
✗ Stripe: Risk Strategist, Card Network Compliance
✗ Stripe: Program Manager, Risk Ops Enablement
✗ Twilio: Principal Presales Engineer  (×2)
✗ Dropbox: Senior Infrastructure Software Engineer
✗ Dropbox: Infrastructure Software Engineer
✗ Coinbase: Derivative Sales Analyst
✗ TE Connectivity: PRODUCT MANAGER IV / III / AI Business Acceleration Manager
✗ TE Connectivity: Sr R&D/Product Dvl Engineer
✗ TE Connectivity: Staff Mechanical Product Development Engineer
✗ JLL: Strategic Sourcing Specialist
✗ JLL: Occupancy Planner EMEA
✗ Innovapptive: Solution Consulting Lead
✗ Viking Pump Hygienic: Commodity Manager
✗ Food Service Specialties: Back Office support
✗ Stone & Tile: Administrative Assistant
```

Для порівняння: у DOU сміття 14% і воно **суміжне** (інженери-конструктори — все ж дизайн, тільки не той). Тут — `Back Office support` і `Administrative Assistant` у категорії Design. Це не спірна класифікація, це поламана.

### І це ще не все — серед решти 19 теж не все дизайн

Простий keyword-фільтр по слову `design` пропускає ще один шар:

```
✗ Analog Devices: Senior Design Verification Engineer     — верифікація мікросхем
✗ Astreya: AI Infrastructure DC Design Engineer II        — інфраструктура ЦОД
✗ Diversified: Design Engineer 2 - Specialist             — інженерія
✗ Anthropic: Safeguards Enforcement Analyst, Age-Appropriate Design — policy
✗ Design Develop Now: Digital Project Manager             — «Design» у НАЗВІ КОМПАНІЇ, роль PM
✗ Education Sub Saharan Africa: Consultancy: Design and Implementation — програмний дизайн
```

**Реальних продуктових/візуальних дизайн-вакансій — приблизно 12–13 з 38 (~33%).**

Ось вони:
```
★ Stripe: Senior Staff Product Designer, Risk
★ Twilio: Lead Product Designer
★ Dropbox: Principal Product Designer   (×2 — дубль)
★ Gusto: Senior Staff Product Designer, Finance
★ Hospitable: Staff UI/UX Product Designer (USA/EMEA)
★ Jobgether: Senior Mobile UX Designer
★ garden3d: Head of Design
★ Iron Mountain: EMEA Design Manager
★ LooseGrip: Junior Designer (Part-Time Contract)
★ Studyflix: Praktikum Grafik, Design & Medienkommunikation (Berlin)
★ Trust Based Mobility: Video Editing Specialist
★ 10 Ancestry.com Operations: Principal Design Engineer, Storytelling
```

⚠️ Цифра «12–13» — **моя оцінка за заголовками**, не заміряний факт: межа між `Design Engineer (Storytelling)` і `Design Verification Engineer` суб'єктивна. Точніше можна було б сказати, лише прочитавши описи, чого я не робив.

## 7. Темп: найнижчий у проєкті

~12 цільових за 55 діб = **~0.22 на добу**.

| джерело | цільових на добу |
|---|---|
| DOU (`category=Design`) | ~5 |
| Djinni (4 фіди) | ~4 |
| HappyMonday | ~1.2 |
| **We Work Remotely** | **~0.22** |

Тобто приблизно **одна релевантна вакансія на 4–5 днів**.

Плюс: усі — міжнародні, переважно великий американський tech (Stripe, Dropbox, Twilio, Gusto, Coinbase). Формально проходять, бо власник фільтр за локацією зняв. Але наскільки вони досяжні для дизайнера з України — питання, на яке фід не відповідає: `region = Anywhere in the World` стоїть навіть там, де `state = Wyoming`.

## 8. Вердикт

🟡 **Технічно — найпростіше джерело в проєкті. За цінністю — найслабше.**

**За:**
- ~1 год роботи, звичайний RSS, найбагатші поля, `expires_at`, компанія витягується надійно (38/38)
- вікно 55 діб — щогодинний розклад безпечний із гігантським запасом
- закриває тему «міжнародний remote», якої немає в інших джерелах

**Проти:**
- **67% сміття** проти 14% у DOU і 0% у Djinni та HappyMonday
- **~0.22 цільових на добу** — у 20 разів менше за DOU
- сміття не суміжне, а повністю стороннє (`Administrative Assistant` у категорії Design) — тобто клієнтський фільтр доведеться робити агресивним, а агресивний фільтр ризикує різати й потрібне
- немає числового ID; дедуп на URL, який залежить від заголовка
- дублікати всередині самого фіда

**Моя рекомендація:** брати **після** того, як Djinni, DOU і HappyMonday запрацюють у коді — і лише якщо обсягу здасться замало. Година роботи тут дешева, але вона додає джерело, яке дає одну вакансію на чотири дні й вимагає найагресивнішого фільтра з усіх.

Якщо ж мета — саме міжнародний remote, то це поки єдиний перевірений кандидат, і тоді 🟢 за відсутністю альтернатив.
