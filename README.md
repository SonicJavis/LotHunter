# LotHunter UK

LOTHUNTER-MVP-001 is a local listing registry and refurbishment valuation calculator for UK IT job lots. It uses Python 3.12 and SQLite with no third-party packages or external services. It supports manual review and produces bid ceilings for review by the operator.

## Quick start

From `~/Projects/LotHunter`, run:

```bash
python3 -m lothunter
```

Open [LotHunter UK](http://127.0.0.1:8765/) in a browser on the same computer. Use **Add listing** or **Import CSV**. Stop the foreground application with Ctrl+C. Use `--port 8766` if the default port is occupied, and substitute the port in the browser URL.

For a separate example database, run:

```bash
python3 -m lothunter --db data/examples.sqlite3 --load-examples
```

Open each sample listing and select **Load illustrative scenarios**. Edit the assumptions and select **Calculate scenarios** to explore sensitivity. The unknown purchase price is blank. Enter a hypothetical price to see the corresponding landed cost, profit and ROI. Calculations are session form results and are not saved to the registry.

## Registry and import

Download the header-only template from **Import CSV** or use [fixtures/lots.csv](fixtures/lots.csv) as a populated example. Use UTF-8, comma delimiters and CSV quoting for embedded commas, quotes and newlines. Headers must match the template exactly. The only required columns are `source`, `listing_id` and `title`. Leave numbers and specifications blank when unknown. Use nonnegative GBP amounts and percentage points, such as `20` for 20%. Include a timezone in auction end dates, for example `2026-11-21T15:00:00+00:00`.

The canonical identity is the normalised source name and trimmed, case-sensitive original listing ID. Use the same source across imports, and include the auction or catalogue identifier in the listing ID where the platform reuses lot numbers. Common source aliases such as eBay UK and ebay.co.uk are recognised. Cross-platform matching is outside this milestone. Existing records retain their reviewed values. Imports advance last-seen and append changed source observations. Manual edits preserve the identity and original observations. Exact repeats of the same submission method and payload do not create another observation.

A bad row rejects the entire file. The browser caps uploads at 2 MiB and the CSV parser accepts up to 1,000 data rows. Reported evidence confidence accepts `unknown`, `low`, `medium` and `high`. The displayed confidence caps medium or high at low when the evidence or relevant specifications are missing. Review status accepts `unreviewed`, `researching`, `shortlisted` and `rejected`.

## Financial model

- Each scenario assigns the lot quantity once across whole resale, refurbishment and resale, parts recovery, and failures. All components from a donor are valued as one aggregate outcome. If parts go into a refurbished unit within the lot, assign that donor no independent resale proceeds for the consumed components and account for the work once.
- Revenue includes estimated regular sales and any clearance proceeds. Processing and labour apply to allocated units. Selling fees and the returns reserve apply to estimated revenue. Outbound postage applies to expected shipments. Failed-unit costs, lot overhead and risk contingency reduce the resulting cash estimate.
- Acquisition cost includes hammer, percentage premium, fixed buyer fee, inbound shipping or collection and their separately configured VAT charges. The premium and fixed buyer fee share the buyer-fee VAT treatment. Enter costs net of VAT when a VAT rate is supplied for the same line. Invoice lines round half-up to pennies. Model VAT as a cash outlay and verify it against the source terms.
- Profit equals revenue less acquisition and operating costs. ROI divides profit by total outlay, including labour and risk reserves. Required profit is a percentage of estimated gross revenue. The maximum bid is the highest `first_bid + n × increment` whose invoice fits the remaining budget after operating costs and required profit. The result shows no viable bid when even the first allowed bid fails.
- The calculator supports a single fixed increment and percentage premium per calculation. For an auction with different fee bands or increment bands, evaluate and verify the applicable band manually before using a bid ceiling. Tax on sales, reclaimable VAT and VAT margin-scheme accounting are outside the model. Resale prices and yields require human evidence. Scenario estimates are pre-tax planning figures.

### Illustrative scenarios

The listings capture the brief’s descriptions as reported claims. The scenario files supply example inputs, including yields, prices, VAT and fees. They contain no observed purchase price, verified resale price, exact laptop or SSD model, or individual SSD health reading.

| Lot | Scenario | Whole / refurbish / parts / failed | Estimated revenue | Maximum hammer |
| --- | --- | ---: | ---: | ---: |
| 111 laptops | Conservative | 3 / 48 / 25 / 35 | £3,016.78 | No viable bid |
| 111 laptops | Expected | 8 / 65 / 24 / 14 | £5,625.90 | £55 |
| 111 laptops | Optimistic | 12 / 78 / 18 / 3 | £8,350.50 | £1,265 |
| 38 SATA SSDs | Conservative | 23 / 0 / 0 / 15 | £215.05 | No viable bid |
| 38 SATA SSDs | Expected | 31 / 0 / 0 / 7 | £427.80 | £50 |
| 38 SATA SSDs | Optimistic | 35 / 0 / 0 / 3 | £598.50 | £145 |

Change one assumption at a time in the expected case to reproduce these examples.

- **Laptops**. Increasing refurbishment processing from £23 to £33 removes the viable bid. Raising refurbishment labour from £16 to £21 and the returns reserve from 7% to 10% also removes it. Moving five units from refurbishment into failures reduces the ceiling from £55 to £25.
- **SSDs**. Increasing whole-unit processing from £2 to £3 cuts the ceiling from £50 to £25. Raising labour from £2 to £3 and the returns reserve from 5% to 8% cuts it to £15. Moving four whole units into failures cuts it to £35.

## Architecture and storage

```text
lothunter/
  __main__.py        Loopback WSGI server, HTTP routes and CLI
  dashboard.py       HTML rendering and calculator form handling
  registry.py        Listing validation, CSV parsing and SQLite persistence
  valuation.py       Decimal-based financial calculations
  style.css         Responsive dashboard layout
fixtures/
  lots.csv          Two example listing rows
  scenarios.json    Illustrative financial inputs
tests/
  test_valuation.py Hand-calculated financial cases
  test_registry.py  Validation, import and persistence cases
  test_dashboard.py WSGI route, import and sample checks
```

SQLite uses schema version 1. The `listings` table stores a local primary key, unique `(source_key, original_id)`, UTC `first_seen` and `last_seen` and the current validated listing fields in a JSON payload. The `observations` table stores a primary key, listing foreign key, UTC observation time, submission method and the original field values as JSON. Observations are indexed by listing and appended on distinct manual or CSV submissions. CSV import is transactional.

The default database lives in the Git-ignored `data/` directory. To back it up, stop the server and copy the SQLite file to a private location. Use only a LotHunter database or a new path for `--db`.

## Security and limitations

The single-user server binds to IPv4 loopback and validates the local Host header. Forms use a per-process token and check the Origin header when supplied. The dashboard escapes listing text, uses a restrictive content policy and keeps new files private to the running user on Linux. It downloads no listing content and logs event metadata without recording form contents. Local users and processes with access to this OS account are trusted. Keep the application on loopback and run it only while reviewing listings.

The app stores structured evidence supplied by the operator. It does not independently verify seller claims, preserve external pages, or track sold outcomes automatically. Evidence coverage and confidence need human review. Native saved-search alerts, manual auction imports, approved read-only marketplace feeds where available and a paired manual-search baseline belong to LOTHUNTER-MVP-002.

## Verification and preflight

```bash
python3 -m unittest discover -v
git status --short --branch
```

Preflight observed Python 3.12.3, SQLite 3.45.1 and Git 2.43.0. FastAPI, Uvicorn and pytest were absent and are unnecessary for setup. The requested path did not exist at preflight and was initialised as a new Git repository with branch `main` and no commits. All 18 automated tests passed, including independent hand-calculated values for VAT, incremental bids, profit and ROI, plus validation, database persistence and dashboard form tests.
