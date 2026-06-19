# GombaShop Bulgarian Sites Scraper

Скриптът намира български сайтове, които вероятно използват **GombaShop**, валидира ги по HTML „отпечатъци“ и генерира Excel файл с:

- URL
- Име на сайт
- Име на фирма / юридическо лице, когато е публично налично
- Категория
- E-mail
- Confidence score и сигнали за проверка
- Източник на намиране

## Важно ограничение

Няма легален и надежден начин един скрипт сам да „обходи целия български интернет“ без входна база от домейни или search index. Затова практичният подход е:

1. Да намери кандидати чрез търсачки по GombaShop fingerprints.
2. Да валидира всеки кандидат чрез директна проверка на сайта.
3. Да извади публично налични e-mail-и и фирмени имена от начална/контактна/правна страница.

## Инсталация

```bash
python -m venv .venv
.venv\Scripts\activate   # Windows
# source .venv/bin/activate  # macOS/Linux
pip install -r requirements.txt
```

## Най-лесно стартиране без API ключ

Използва DuckDuckGo. Работи, но не е толкова стабилно и може да върне по-малко резултати:

```bash
python gombashop_scraper.py --search-backend duckduckgo --out gombashop_sites.xlsx --include-rejected
```

## По-надеждно стартиране със SerpAPI

```bash
set SERPAPI_KEY=your_key_here
python gombashop_scraper.py --search-backend serpapi --out gombashop_sites.xlsx --include-rejected
```

macOS/Linux:

```bash
export SERPAPI_KEY=your_key_here
python gombashop_scraper.py --search-backend serpapi --out gombashop_sites.xlsx --include-rejected
```

## Само ръчно подадени домейни

Добави домейни в `manual_domains.txt`, после:

```bash
python gombashop_scraper.py --search-backend none --seed-file manual_domains.txt --out gombashop_sites.xlsx --include-rejected
```

## GitHub Actions

В `.github/workflows/scrape.yml` има примерен workflow. Ако използваш SerpAPI, добави `SERPAPI_KEY` като GitHub Secret.

## Как се валидира GombaShop

Скриптът търси силни и средни сигнали, например:

- `meta name="Author" content="www.gombashop.com"`
- `powered by GombaShop`
- `GombaShop™`
- `/plugins/RssFeedPlugin/feed`
- `/cart-wishlistCount.html`
- `/cart-wishlistEditAx.html`
- GombaShop CSS/JS класове и файлове като `gs-header`, `gs-main-container`, `pub.product.js`

Редовете със статус `verified` са най-надеждни. `probable` е добре да се прегледат ръчно.
