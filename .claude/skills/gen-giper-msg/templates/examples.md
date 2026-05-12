# Few-Shot Examples — High-Quality Generations

These examples are appended after the system prompt so the model has a calibrated
sense of voice. They cover the three contact tiers × main niches.

---

## Example 1 — phone tier, nail studio, Bishkek (with owner name)

**Input context:**
```
business_name: Parizat nail studio
city: Бишкек
district: Первомайский
niche: маникюр / косметология
2gis_rating: 4.8 (159 отзывов)
owner_name: Паризат
phone: +996509951195
company_instagram: @parizat.nailstudio
channel: WhatsApp
```

**Output:**
```json
{
  "hook": "Здравствуйте, Паризат 👋",
  "observation": "Зашёл сегодня на 2ГИС изучить nail-студии Бишкека — у вас лучший рейтинг 4.8 в Первомайском районе.",
  "problem": "Заметил, что записи идут через DM и комментарии в Instagram — наверное немало клиенток теряется в потоке сообщений.",
  "value": "Я делаю простые сайты с онлайн-записью для салонов Бишкека — от $300, готовы за 5 дней. Клиент сам выбирает время и бронирует.",
  "ask": "Хотите покажу пример как такой сайт мог бы выглядеть для Parizat nail studio? Минут 5, без обязательств."
}
```

---

## Example 2 — phone tier, beauty salon, Almaty (no owner name)

**Input context:**
```
business_name: Stilist studio
city: Алматы
district: Бостандыкский
niche: парикмахерская / салон красоты
2gis_rating: 4.7 (89 отзывов)
owner_name: (empty)
phone: +77017771122
company_instagram: @stilist.studio.almaty
channel: WhatsApp
```

**Output:**
```json
{
  "hook": "Здравствуйте 💇 пишу про Stilist studio",
  "observation": "На 2ГИС у вас 4.7 в Бостандыкском — один из самых сильных рейтингов среди небольших салонов района.",
  "problem": "Вижу что записи идут через звонки и Instagram — клиенты которые хотели бы записаться вечером или в выходные, скорее всего, не доходят.",
  "value": "Делаю сайты с онлайн-записью для салонов Алматы — от $300, готов за 5 дней. С календарём, услугами и ценами.",
  "ask": "Скинуть пример как мог бы выглядеть сайт под Stilist studio? Минут 5 — без обязательств."
}
```

---

## Example 3 — owner_ig tier, fitness, Almaty (Serper-found handle)

**Input context:**
```
business_name: PowerHouse Gym
city: Алматы
district: Алмалинский
niche: фитнес / тренажёрные залы
owner_name: Адильбек Жунусов
owner_instagram: @adilbek.coach
company_instagram: @powerhouse.almaty
channel: Instagram DM
note: owner_ig_source = serper-auto (handle was machine-guessed, the message is still a polite first-touch in case it's the right person)
```

**Output:**
```json
{
  "hook": "Адильбек, здравствуйте 💪",
  "observation": "Видел PowerHouse Gym на 2ГИС и ваш Insta — серьёзный зал, грамотно поставленный контент.",
  "problem": "Заметил только, что покупка абонемента и расписание у клиентов идёт через DM — это часто отжирает у тренеров пару часов в день на одни и те же вопросы.",
  "value": "Я делаю простые сайты с расписанием, онлайн-оплатой абонемента и формой записи на пробное — от $300, неделя на запуск.",
  "ask": "Хотите покажу пример страницы под PowerHouse? Минут 5, без обязательств."
}
```

---

## Example 4 — company_ig tier, travel agency, Bishkek (only company IG + name hint)

**Input context:**
```
business_name: Sky Tour KG
city: Бишкек
district: (empty)
niche: турфирмы / турагентства
2gis_rating: 4.6 (42 отзыва)
owner_name: Айгуль Турдубаева
owner_instagram: (empty)
company_instagram: @skytour.kg
channel: Instagram DM
```

**Output:**
```json
{
  "hook": "Здравствуйте ✈️ пишу про Sky Tour KG",
  "observation": "Изучаю турагентства Бишкека на 2ГИС — у вас неплохой рейтинг (4.6) и активный Instagram с турпакетами.",
  "problem": "Заметил, что заявки идут через DM и WhatsApp — клиент перед выбором тура часто смотрит конкурентов у которых есть сайт с каталогом и онлайн-формой.",
  "value": "Я делаю простые сайты с каталогом туров, ценами и формой заявки — для агентств Бишкека от $300, готовы за неделю.",
  "ask": "Если интересно — могу набросать макет под Sky Tour KG, чтобы вы увидели как это выглядит."
}
```

---

## Example 5 — phone tier, barber, Almaty (super lean — minimal data)

**Input context:**
```
business_name: Borodach barbershop
city: Алматы
district: (empty)
niche: парикмахерская / салон красоты
owner_name: (empty)
phone: +77471234567
company_instagram: (empty)
channel: WhatsApp
```

**Output:**
```json
{
  "hook": "Здравствуйте 💈 пишу про Borodach",
  "observation": "Нашёл вас на 2ГИС среди барбершопов Алматы — компактный формат, постоянная клиентская база как я вижу по отзывам.",
  "problem": "Часто у небольших барбершопов запись идёт по звонку — клиенты которые в первый раз стесняются звонить, теряются.",
  "value": "Я делаю простые сайты с онлайн-записью для барбершопов Алматы — от $300, готовы за 5 дней. Клиент видит услуги, цены, мастеров.",
  "ask": "Хотите покажу пример как такой сайт мог бы выглядеть для Borodach? Минут 5, без обязательств."
}
```

---

## Anti-examples — НЕ так

### ❌ Anti-1: corporate slop
```json
{"hook":"Уважаемый клиент!","observation":"Наша компания специализируется на разработке качественных сайтов для салонов красоты с индивидуальным подходом.","problem":"...","value":"Мы предлагаем уникальное решение под ваш бизнес...","ask":"Свяжитесь с нами!"}
```
Почему плохо: "уважаемый клиент", "наша компания", "качественных", "индивидуальным подходом",
"уникальное решение", "свяжитесь с нами" — всё в blacklist. Никакой персонализации.

### ❌ Anti-2: too long, multiple CTAs
```json
{"hook":"Привет!","observation":"...","problem":"...","value":"Мы делаем сайты, делаем рекламу, делаем SEO, делаем приложения — полный спектр услуг.","ask":"Звоните +77001234567, пишите в Telegram @x, заходите на наш сайт example.com, отвечайте на это сообщение!"}
```
Почему плохо: список услуг (мы фокусируемся на сайтах), 4 CTAs (выбери один), упоминание
собственного телефона/сайта в первом касании.

### ❌ Anti-3: invented facts
```json
{"hook":"Здравствуйте, Мария!","observation":"Видел ваш пост от 5 марта про новые услуги ламинирования.","problem":"...","value":"...","ask":"..."}
```
Почему плохо: имени "Мария" в данных нет, поста 5 марта мы не видели (мы не смотрим посты в Lite-режиме).
Это галлюцинация — она моментально палит, когда владелица отвечает "я не Мария и таких постов у нас не было".
