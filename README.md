# SNGParser — Скиллы для лидогенерации в Алматы и Бишкеке

Два связанных скилла для [Claude Code](https://claude.ai/code), которые превращают одну фразу в чате (например, *«Найди 200 клиентов в Алматах в маникюре»*) в готовый Google Sheets с лидами + персонализированными WhatsApp/Instagram сообщениями к ним.

Заточено под цикл продаж веб-агентства, которое делает простые сайты с онлайн-записью для малого бизнеса в Казахстане и Кыргызстане ($300-400 за сайт, 5-7 дней на запуск).

## Что внутри

```
.claude/skills/
├── 2gis-lead-gen/      ← собирает лидов с 2GIS, фильтрует "без сайта",
│                          ищет владельца через Serper + Apify Instagram
│                          → пишет в Google Sheets
│
├── gen-giper-msg/      ← читает лидов из Sheets, генерирует через Sonnet 4.6
│                          гиперперсонализированное сообщение по структуре
│                          senior closer'а → пишет обратно в новые колонки
│                          как drafts для проверки менеджером
│
└── _shared/            ← минимальные общие модули (Google Sheets, конфиг,
                          Telegram-репорты)
```

## Полный цикл

```
[Юзер] «Найди 30 клиентов в Алматах в маникюре в таблицу X»
   ↓
[2gis-lead-gen] парсит 2GIS → фильтрует "имеет сайт" (3-way проверка)
                → классифицирует телефоны как mobile/landline (KZ/KG
                префиксы) → если телефона нет, ищет имя владельца через
                Serper Google search → пишет 30 строк в Sheet
   ↓
[Юзер] «Сгенерируй сообщения для лидов в этой таблице»
   ↓
[gen-giper-msg] для каждого лида собирает контекст (имя, район, рейтинг,
                IG-handle, ниша) → вызывает `claude --print` с
                senior-closer prompt → парсит JSON-ответ из 5 частей
                (hook, observation, problem, value, ask) → валидирует
                длину + stop-words → пишет в колонку `Initial Message`
                со статусом `draft`
   ↓
[Менеджер] открывает Sheet → читает каждое сообщение → правит → ставит
           `Message Status = approved` + своё имя в `Reviewed By`
   ↓
[demo-sender / ig-outreach] (отдельные внешние скиллы) забирают только
           `approved` строки и шлют через WhatsApp / Instagram DM
```

## Стоимость одного запуска (живые цифры с тестов)

| Шаг | На 200 лидов |
|---|---|
| Apify-скрапинг 2GIS (актор `m_mamaev/2gis-places-scraper`) | ~$3.80 (Apify free credit покрывает ~$5/мес) |
| Serper Google поиск владельца (~30-40% лидов) | бесплатно (2500 бесплатных запросов/мес) |
| Apify Instagram batch (профили компаний для website-проверки) | в той же цене Apify |
| Sonnet 4.6 генерация сообщений | ~$0 (через Claude Max-подписку) |
| **Итого 200 лидов с готовыми сообщениями** | **~$3.80** |

Скорость: 30 лидов = ~4 минуты с workers=10. На 200 лидов — около получаса.

## Установка на новой машине

### Требования
- macOS / Linux
- Python 3.11+ (`brew install python@3.11` на маке)
- [Claude Code](https://claude.ai/code) с активной Max-подпиской
- Google аккаунт с доступом к Sheets API
- Аккаунт на [Apify](https://apify.com) (бесплатный) и [Serper.dev](https://serper.dev)

### Шаги

```bash
# 1. Клонировать репозиторий
git clone https://github.com/Devrank7/SNGParser.git
cd SNGParser

# 2. Установить Python зависимости
python3.11 -m pip install --user google-api-python-client google-auth certifi pytest

# 3. Скопировать .env.example в .env.local и заполнить
cp .env.example .env.local
# отредактировать .env.local — добавить APIFY_API_KEY, SERPER_API_KEY,
# (опционально) Telegram токены

# 4. Положить service_account.json в корень
# Файл получается в Google Cloud Console → IAM → Service Accounts →
# Keys → Add Key → JSON. Затем не забыть поделиться нужной Google
# Sheets с email из этого JSON (поле client_email)

# 5. Прогнать тесты — убедиться что окружение в порядке
cd .claude/skills/2gis-lead-gen && python3.11 -m pytest tests/ -v
# должно быть "122 passed in 0.07s"

# 6. Запустить Claude Code в корне проекта
cd /path/to/SNGParser
claude
```

После этого скиллы автоматически подхватятся при старте сессии — Claude Code сканирует `.claude/skills/` в текущей рабочей директории.

## Использование

Просто напиши агенту обычным языком — он сам подберёт скилл по описанию.

### Сбор лидов

```
«Найди 200 клиентов в Алматах в маникюре в таблицу
 https://docs.google.com/spreadsheets/d/.../»

«Дай 50 лидов фитнес в Бишкеке»

«Спарсь турагентства Алматы, сохрани в [ссылка на таблицу]»
```

Поддерживаемые из коробки города: **Алматы**, **Бишкек**.
Поддерживаемые ниши: **парикмахерские/салоны красоты**, **маникюр/косметология**, **фитнес/тренажёрные залы**, **турагентства**.

Для произвольных ниш — пиши свободным текстом, агент передаст через `--niche-query`.

### Генерация сообщений

```
«Сгенерируй сообщения для всех лидов в Sheet X»

«Сделай WhatsApp сообщения для 30 лидов в этой таблице»
```

## Структура колонок в Sheets

После работы обоих скиллов в твоей Google Sheets будет:

| # | Колонка | Кто заполняет |
|---|---|---|
| 1 | Discovered At | `2gis-lead-gen` |
| 2 | City | `2gis-lead-gen` |
| 3 | Niche | `2gis-lead-gen` |
| 4 | Business Name | `2gis-lead-gen` |
| 5 | Address | `2gis-lead-gen` |
| 6 | Contact Method | `2gis-lead-gen` (`phone` / `owner_ig` / `company_ig`) |
| 7 | Phone | `2gis-lead-gen` (только mobile, KZ/KG префиксы) |
| 8 | Phone Type | `2gis-lead-gen` |
| 9 | Owner Name | `2gis-lead-gen` (из Serper, иногда пусто) |
| 10 | Owner Instagram | `2gis-lead-gen` (Serper-угадан, нужна ручная проверка) |
| 11 | Owner IG Source | `2gis-lead-gen` (`serper-auto` = автомат, проверь) |
| 12 | Company Instagram | `2gis-lead-gen` |
| 13 | 2GIS URL | `2gis-lead-gen` |
| 14 | Data Source | `2gis-lead-gen` |
| 15 | Initial Message | `gen-giper-msg` (draft на проверку) |
| 16 | Channel | `gen-giper-msg` (`WhatsApp` / `Instagram DM`) |
| 17 | Message Status | `gen-giper-msg` (`draft` → менеджер ставит `approved`) |
| 18 | Reviewed By | менеджер вручную |

## Тесты

Полный набор unit-тестов (без сетевых вызовов) — мок-данные для Apify/Serper/DNS:

```bash
cd .claude/skills/2gis-lead-gen
python3.11 -m pytest tests/ -v
```

Покрытие: `phone_classify` (KZ/KG mobile префиксы с regression-тестами на VoIP/satellite блоки), `website_check` (3-way логика), `find_owner` (Serper мок), `data_sources._normalize` (формы Apify-output), `dedup_db` (SQLite миграция, idempotency).

## Что НЕ делает

- Не отправляет сообщения. Только генерирует drafts для проверки менеджером.
- Не валидирует что SIM-карта активна (HLR-lookup — отдельная опциональная интеграция, не встроена).
- Не различает корпоративный mobile от личного (это не решается по номеру).
- Не скрапит Instagram-посты для гиперперсонализации (только bio для проверки наличия сайта).

Все эти ограничения с конкретными решениями описаны в `.claude/skills/2gis-lead-gen/REFERENCE.md`.

## Лицензия

Внутренний инструмент. Используется для лидогенерации веб-агентства.
