# GombaShop Scraper v2

Скрейпър за откриване на сайтове, които използват GombaShop, и експорт в Excel.

## Какво извежда

Excel файлът съдържа:

- `url`
- `site_name`
- `company_name`
- `category`
- `emails`
- `confidence`
- `signals`
- `matched_url`
- `status`
- `source_query`
- `source_backend`

## Важно за 1000+ сайта

Без search API ключ няма надежден начин да се извадят 1000+ сайта. DuckDuckGo е безплатен, но често връща малко резултати или блокира. За реални 1000+ използвай:

- `SERPAPI_KEY` — препоръчително
- или `BING_SEARCH_API_KEY`
- плюс `--ct-gombashop`

## Инсталация

```bash
pip install -r requirements.txt
```

## Най-добра команда за 1000+ резултата

### Windows PowerShell

```powershell
$env:SERPAPI_KEY="YOUR_SERPAPI_KEY"
python gombashop_scraper.py --search-backend serpapi --target-candidates 8000 --max-queries 220 --search-limit-per-query 100 --ct-gombashop --urlscan-gombashop --workers 20 --include-rejected --out gombashop_sites.xlsx
```

### macOS / Linux

```bash
export SERPAPI_KEY="YOUR_SERPAPI_KEY"
python gombashop_scraper.py --search-backend serpapi --target-candidates 8000 --max-queries 220 --search-limit-per-query 100 --ct-gombashop --urlscan-gombashop --workers 20 --include-rejected --out gombashop_sites.xlsx
```

## Ако нямаш API ключ

```bash
python gombashop_scraper.py --search-backend duckduckgo --ct-gombashop --urlscan-gombashop --max-queries 220 --include-rejected --out gombashop_sites.xlsx
```

Този вариант може да даде много по-малко от 1000, защото DuckDuckGo ограничава резултатите.

## GitHub Actions

1. Качи файловете в GitHub repo.
2. В GitHub отвори `Settings → Secrets and variables → Actions → New repository secret`.
3. Добави secret:
   - `SERPAPI_KEY` = твоят SerpAPI ключ
4. Стартирай:
   - `Actions → GombaShop Scraper v2 → Run workflow`
5. След края свали Excel файла от `Artifacts`.

## Как валидира GombaShop

Скриптът не записва всеки резултат от търсачката. Първо проверява HTML-а за отпечатъци като:

- `meta Author = www.gombashop.com`
- `powered by GombaShop`
- `Онлайн магазин създаден с GombaShop`
- `/plugins/RssFeedPlugin/feed`
- `/plugins/FbDynamicProducts/conversion`
- `cart-wishlistEditAx.html`
- `QuickView.showInfo`
- `gs-header`, `gs-main-container`, `gs-cart`

## Какво да правиш, ако пак са малко

1. Увери се, че не го пускаш само с DuckDuckGo.
2. Използвай SerpAPI или Bing API.
3. Увеличи:

```bash
--target-candidates 12000 --max-queries 300
```

4. Остави включено:

```bash
--ct-gombashop --urlscan-gombashop
```

5. Добави собствени домейни в `manual_domains.txt`, ако имаш списък с български домейни.


## v3 notes

This version fixes long GitHub Action runs that fail at the final XLSX export because some scraped pages contain illegal Excel control characters. It also writes a rolling `gombashop_progress.csv` checkpoint during the run, so even if a run fails or is cancelled you can still download partial results from the artifact.

For big runs, start with `contact_pages=3`. Increasing it may find more emails, but it also makes the run much slower.

Recommended GitHub inputs for a large run:

- `search_backend`: `auto` if you have `SERPAPI_KEY`; otherwise `duckduckgo` will usually find far fewer sites.
- `target_candidates`: `8000`
- `max_queries`: `220`
- `include_rejected`: `true`
- `contact_pages`: `3`

Artifacts uploaded after every run:

- `gombashop_sites.xlsx` - final Excel when export succeeds
- `gombashop_progress.csv` - partial/fallback results
- `scraper.log` - full log for debugging
