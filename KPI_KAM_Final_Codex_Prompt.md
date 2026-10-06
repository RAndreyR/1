# FINAL PROMPT FOR CODEX
## Project: KPI KAM Desktop (Windows)

You are building a production-quality Windows desktop MVP called **KPI KAM** for calculating quarterly KPI bonuses for Key Account Managers / coordinators from Excel shipment data and price thresholds.

The application replaces an existing Excel/VBA workflow. Accuracy, auditability, and reproducibility are more important than visual complexity.

---

# 1. Target platform and stack

Build a local Windows desktop application with:

- Python 3.12+
- PySide6 for GUI
- pandas + openpyxl for Excel import/export
- sqlite3 for local persistent storage
- decimal.Decimal for all monetary and price calculations
- pytest for automated tests
- PyInstaller for packaging
- no internet dependency
- no external server
- no Excel installation required

Preferred executable packaging for MVP:
- PyInstaller `onedir`
- output application name: `KPI_KAM.exe`

User data and logs:
- `%APPDATA%\KPI_KAM\`
- SQLite database: `%APPDATA%\KPI_KAM\kpi_kam.db`
- logs: `%APPDATA%\KPI_KAM\logs\app.log`

---

# 2. Core principle

Every KPI number must be explainable down to the original shipment row.

The user must be able to click any aggregate value and see:
- which source rows were included;
- which source rows were excluded;
- exact exclusion reason;
- actual shipment price;
- threshold price;
- difference between them;
- matched canonical product;
- product category.

The system must never silently guess a product mapping if exact matching fails.

---

# 3. Input files

The user uploads an Excel `.xlsx` or `.xlsm`.

Expected sheets:
- `отгрузки`
- `прайс`
- `KPI` may exist but is not used as a source of plan values

The program must not execute macros from the input file.

Field detection must use header names, not hardcoded column letters only.

## 3.1 Sheet `отгрузки`

Expected logical fields:

- `Период`
- `Клиент`
- `Тип клиента`
- `Номенклатура`
- `Количество`
- `Выручка`
- `цена за 1 кг`
- `ЮЛ`
- `Задача в Битрикс`
- `Комментарии`

Normalize:
- leading/trailing spaces;
- non-breaking spaces;
- repeated spaces;
- case differences;
- `дистрибьютер` / `дистрибьютор`;
- visually identical strings with extra whitespace.

## 3.2 Sheet `прайс`

Expected logical fields:

- product name
- product category / type
- green-zone threshold for LPU
- green-zone threshold for distributors

Expected categories:
- `СБКС`
- `ВМК`
- `ЭП`
- `Latema`
- `Novionta`

Critical rule:
**Product category must always come from the current price list. Never hardcode category by row number or by product name in code.**

If the price list says a product is СБКС, it is СБКС.

---

# 4. Product matching

## 4.1 Exact normalized match

Normalize both shipment product names and price-list product names by:
- trim;
- replace non-breaking spaces;
- collapse multiple spaces;
- case-insensitive comparison.

If exact normalized match succeeds, use it.

## 4.2 Alias mapping

If exact match fails:
- do not auto-assign;
- put the row/product on the `Product Mapping` screen;
- user selects the canonical product from the price list;
- optionally check `Remember this mapping`;
- save alias mapping in SQLite.

Saved mapping format:
`raw normalized alias -> canonical product id`

On future imports apply saved alias automatically.

Never infer category directly from alias text. Category comes from the selected canonical product in the current price list.

---

# 5. Quarter normalization

Recognize:
- `1 кв.`, `1 кв`, `1кв.`, `1кв`
- same for quarters 2, 3, 4

Map to integers 1..4.

If a row cannot be mapped:
- exclude from green-zone KPI;
- include in audit log with reason `Некорректный период`.

---

# 6. Client type normalization

Recognize:
- `дистрибьютер`
- `дистрибьютор`
- case and whitespace variations
as normalized client type `дистрибьютер`.

Recognize:
- `ЛПУ`
- case/whitespace variations
as normalized client type `ЛПУ`.

Unknown client type:
- exclude from green-zone KPI;
- audit reason `Неизвестный тип клиента`.

---

# 7. Shipment price

Primary source:
`цена за 1 кг`

If missing or zero:
`price = revenue / quantity`

If quantity is zero and price is missing:
- exclude from green-zone KPI;
- audit reason `Невозможно определить цену`.

Use Decimal, not float.

---

# 8. Green-zone threshold

After product matching:

For `ЛПУ` use the LPU threshold from `прайс`.

For `дистрибьютер` use the distributor threshold from `прайс`.

If threshold is blank or zero:
- exclude from green-zone KPI;
- audit reason `Не задана зеленая зона`.

---

# 9. Critical price comparison rule

This is a business rule and must be implemented exactly.

Before comparison:
- round actual shipment price to **1 decimal place**
- round threshold price to **1 decimal place**
- use `Decimal.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)`

Then:
`eligible = actual_price_1dp >= threshold_price_1dp`

Equality must pass.

Example:
- actual 154.04 -> 154.0
- threshold 154.04 -> 154.0
- eligible = True

Example:
- actual 154.04 -> 154.0
- threshold 154.06 -> 154.1
- eligible = False

Do not use Python built-in `round()` for financial logic.

Display in audit:
- raw actual price
- rounded actual price
- raw threshold
- rounded threshold
- delta after rounding

---

# 10. Green-zone aggregation

If a row is eligible, sum **revenue**.

Dimensions:
- employee/calculation
- year
- quarter
- client type
- product category

Product categories:
1. СБКС
2. ВМК
3. Latema
4. Novionta
5. ЭП

Client types:
- distributor
- LPU

---

# 11. KPI premium blocks

Rates must be editable in settings and stored in the calculation snapshot.

Default rates:

## Block 1
Distributor shipments, categories:
- СБКС
- ВМК

Formula:
`(eligible SBKS distributor revenue + eligible VMK distributor revenue) * 5%`

Default rate: **5.0%**

## Block 2
LPU shipments, categories:
- СБКС
- ВМК

Formula:
`(eligible SBKS LPU revenue + eligible VMK LPU revenue) * 5%`

Default rate: **5.0%**

## Block 3
Distributor shipments, categories:
- ЭП
- Latema
- Novionta

Formula:
`(eligible EP + Latema + Novionta distributor revenue) * 1.5%`

Default rate: **1.5%**

## Block 4
LPU shipments, categories:
- ЭП
- Latema
- Novionta

Formula:
`(eligible EP + Latema + Novionta LPU revenue) * 2%`

Default rate: **2.0%**

All rates editable.

---

# 12. Mandatory quarterly plan input

Plan values are NOT imported from Excel.

At the beginning of every calculation the user must manually enter all four quarterly plan values:

- Q1 plan
- Q2 plan
- Q3 plan
- Q4 plan

These are mandatory fields.

The `Calculate KPI` button remains disabled until all four fields contain valid non-negative numeric values.

Display:
- Q1
- Q2
- Q3
- Q4
- annual plan = Q1 + Q2 + Q3 + Q4

Store the exact plan values as part of the calculation snapshot/history.

Do not overwrite historical plan values if settings later change.

---

# 13. Plan gate

Default minimum plan achievement:
**90%**

Editable in Settings.

## Q1-Q3

For each quarter:

`quarter_pass = quarter_total_actual >= quarter_plan * 90%`

If false:
- calculate theoretical premium anyway;
- show it as `Расчетная премия`;
- `К выплате = 0`.

If true:
- `К выплате = Расчетная премия`.

## Q4 and annual catch-up

Annual plan:
`Q1 plan + Q2 plan + Q3 plan + Q4 plan`

Annual actual:
sum of actual shipments Q1..Q4.

Scenario A:
If
`annual_actual >= annual_plan * 90%`

then Q4 payable:
`full calculated premium for the entire year - premiums actually paid in Q1-Q3`

This is annual catch-up / true-up.

Scenario B:
If annual threshold is not met, but:
`Q4 actual >= Q4 plan * 90%`

then Q4 payable:
ordinary calculated Q4 premium.

Otherwise:
Q4 payable = 0.

Never allow negative Q4 payable. If subtraction produces a negative result, payable = 0.

---

# 14. Actual sales for plan achievement

Quarter actual for plan achievement is the sum of **all shipment revenue in that quarter**, regardless of green-zone eligibility.

Important:
- a product may be absent from the price list;
- the shipment still counts toward total actual sales;
- but it does not count toward green-zone premium until mapped to a price-list product and a threshold exists.

Unknown products must be visible in audit.

---

# 15. Money precision

Use `Decimal` for:
- revenue
- prices
- thresholds
- premiums
- plans

Keep at least 2 decimal places internally for money.

Do not discard kopecks in intermediate calculations.

Final money display:
`1 234 567,89 ₽`

Use Russian-style grouping and decimal comma in UI.

---

# 16. GUI design

Use a modern business desktop layout.

Preferred:
- left navigation or top navigation;
- dashboard cards;
- editable tables;
- drill-down views;
- no spreadsheet-like overcrowding.

## Screen A: New Calculation / Dashboard

Fields:
- employee name
- year
- source Excel file
- mandatory Q1 plan
- mandatory Q2 plan
- mandatory Q3 plan
- mandatory Q4 plan
- auto-calculated annual plan
- plan gate %
- button `Проверить файл`
- button `Рассчитать KPI`

After calculation show for each quarter:
- actual
- plan
- achievement %
- gate status
- calculated premium
- payable premium

Also show the four premium blocks.

Clicking any block opens audit rows that formed the amount.

## Screen B: Import Validation

Show:
- detected worksheets
- row count
- product count
- missing columns
- unmatched products
- invalid quarters
- unknown client types
- rows with missing price data

Buttons:
- `Перейти к сопоставлению`
- `Рассчитать`

Do not allow final calculation while required product mappings remain unresolved, unless user explicitly marks them as `Оставить несопоставленным`.

## Screen C: Product Mapping

Columns:
- raw product name from shipments
- suggested/current canonical product
- canonical category
- status

Actions:
- select canonical product
- remember mapping
- leave unresolved

## Screen D: Audit / Drill-down

Filters:
- quarter
- client type
- category
- included/excluded
- product
- client

Columns:
- source Excel row number
- quarter
- client
- client type
- raw product name
- canonical product
- category
- quantity
- revenue
- raw actual price
- rounded actual price
- raw threshold
- rounded threshold
- delta
- status
- exclusion reason

Footer:
- included revenue
- excluded revenue
- row count

## Screen E: Premium Detail

For selected quarter show:
- actual
- plan
- threshold 90%
- achievement %
- gate status

Then table:
- Block 1 base, rate, premium
- Block 2 base, rate, premium
- Block 3 base, rate, premium
- Block 4 base, rate, premium
- calculated premium
- payable premium

For Q4 additionally show:
- annual plan
- annual actual
- annual achievement %
- annual catch-up amount
- premiums already paid Q1-Q3

## Screen F: Settings

Editable:
- plan gate %
- Block 1 rate
- Block 2 rate
- Block 3 rate
- Block 4 rate
- price comparison precision fixed to 1 decimal for this version, but show as read-only setting
- default export folder

## Screen G: History

Columns:
- calculation date
- employee
- year
- source file
- Q1 payable
- Q2 payable
- Q3 payable
- Q4 payable
- annual payable

Double click opens full saved calculation snapshot.

---

# 17. SQLite schema

Implement at least:

## employees
- id
- name
- active

## products
- id
- canonical_name
- category
- price_lpu
- price_distributor
- active
- import_id

## product_aliases
- id
- alias_normalized
- product_id
- created_at

## kpi_profiles
- id
- name
- plan_gate
- rate_block_1
- rate_block_2
- rate_block_3
- rate_block_4
- price_precision

## imports
- id
- employee_id
- year
- source_file
- source_hash
- imported_at

## calculation_plans
- calculation_id
- q1_plan
- q2_plan
- q3_plan
- q4_plan
- annual_plan

## shipments
- id
- import_id
- source_row
- quarter
- client
- client_type
- product_raw
- product_id nullable
- quantity
- revenue
- unit_price_raw
- unit_price_rounded
- threshold_raw
- threshold_rounded
- price_delta
- legal_entity
- comment
- eligible
- exclusion_reason

## calculations
- id
- import_id
- profile_snapshot_json
- plan_snapshot_json
- calculated_at
- q1_actual
- q2_actual
- q3_actual
- q4_actual
- annual_actual
- q1_calculated_premium
- q2_calculated_premium
- q3_calculated_premium
- q4_calculated_premium
- q1_payable
- q2_payable
- q3_payable
- q4_payable

Store enough data to reopen a historical calculation exactly as originally calculated.

---

# 18. Calculation engine architecture

Critical requirement:
**The calculation engine must not depend on PySide6.**

Public API example:

```python
result = calculate_kpi(
    shipments=shipments,
    products=products,
    plans=plans,
    rules=rules,
    paid_history=paid_history,
)
```

Return a structured result object containing:
- quarter actuals
- annual actual
- green-zone totals by quarter/client/category
- four premium block bases
- four premium block amounts
- plan achievement
- calculated premium
- payable premium
- annual catch-up values
- included rows
- excluded rows
- warnings

All calculations must be unit-testable without GUI.

---

# 19. Excel export

Export a new `.xlsx` with:

1. `Итог KPI`
2. `Зеленый коридор`
3. `Включенные строки`
4. `Исключенные строки`
5. `Сопоставления`
6. `Параметры расчета`

Do not overwrite source workbook unless explicitly added later.

---

# 20. Logging

Log:
- import started/completed
- file structure errors
- row counts
- matched/unmatched product counts
- calculation rules snapshot
- result totals
- application exceptions

Avoid unnecessary client personal data in system log.

---

# 21. Project structure

Use:

```text
kpi_kam/
├── main.py
├── requirements.txt
├── README.md
├── build_windows.bat
├── app/
│   ├── ui/
│   │   ├── main_window.py
│   │   ├── dashboard.py
│   │   ├── import_page.py
│   │   ├── mapping_page.py
│   │   ├── audit_page.py
│   │   ├── premium_page.py
│   │   ├── settings_page.py
│   │   └── history_page.py
│   ├── services/
│   │   ├── excel_importer.py
│   │   ├── product_matcher.py
│   │   ├── calculation_engine.py
│   │   ├── validation_service.py
│   │   └── export_service.py
│   ├── models/
│   ├── repositories/
│   └── utils/
├── tests/
│   ├── test_normalization.py
│   ├── test_matching.py
│   ├── test_green_corridor.py
│   ├── test_plan_gate.py
│   ├── test_q4_trueup.py
│   └── fixtures/
└── resources/
```

---

# 22. Required automated tests

Before implementing GUI, write tests for:

## Price equality
If actual rounded price equals rounded threshold:
eligible = True.

## One-decimal rule
Actual 154.04 and threshold 154.04 -> both 154.0 -> eligible.
Actual 154.04 and threshold 154.06 -> 154.0 vs 154.1 -> not eligible.

## Category source
Category must be loaded from price list, never hardcoded.

## Product alias
Alias resolves to canonical product, then category comes from canonical product.

## Plan gate Q1-Q3
Below 90% -> calculated premium visible, payable 0.
At 90% exactly -> payable allowed.

## Q4 annual catch-up
Annual >= 90% -> annual calculated premium minus already paid Q1-Q3.
Result cannot be negative.

## Unknown product
Counts in total actual.
Does not count in green-zone KPI.
Appears in exclusions.

## Missing threshold
Counts in total actual.
Excluded from green-zone KPI.

---

# 23. Reference workbook

Use the latest sample workbook supplied by the user as a regression fixture if available:
`KPI Трофимов Дмитрий 2026.xlsm`

Do not execute its VBA.

Use it only as input data for:
- shipments
- price list
- regression comparison

Plans must still be entered manually in the application and must not be imported from the KPI sheet.

---

# 24. Development sequence

Implement in this exact order:

## Phase 1
Pure calculation engine + models + Decimal rules + unit tests.

No GUI yet.

## Phase 2
Excel importer + validation + product matching + alias storage.

## Phase 3
PySide6 GUI:
- New Calculation
- Validation
- Product Mapping
- Dashboard
- Audit
- Premium Detail

## Phase 4
SQLite history + settings.

## Phase 5
Excel export.

## Phase 6
Windows packaging:
- PyInstaller onedir
- `build_windows.bat`
- README build instructions

Do not proceed to UI polishing until all calculation tests pass.

---

# 25. Acceptance criteria

The MVP is complete only if:

1. Loads `.xlsx` and `.xlsm` without running macros.
2. Works without Microsoft Excel installed.
3. Requires Q1-Q4 plan input before calculation.
4. Compares prices at 1 decimal using Decimal + ROUND_HALF_UP.
5. Exact equality passes green zone.
6. Product category comes from current price list.
7. Unmatched products never get silently guessed.
8. Alias mappings are stored and reused.
9. Unknown products still count in total actual.
10. Each aggregate has drill-down to source rows.
11. Every excluded row has a reason.
12. Q1-Q3 plan gate works at 90%.
13. Q4 annual catch-up works.
14. History reopens exact saved calculation state.
15. Excel export works.
16. PyInstaller build succeeds on Windows.
17. No financial calculation uses float.
18. Automated tests pass before release build.

---

# 26. Coding quality requirements

- type hints
- dataclasses or pydantic models
- clear service boundaries
- no business logic in GUI widgets
- no hardcoded worksheet row numbers for category logic
- meaningful exceptions
- testable functions
- Russian UI text
- English identifiers in source code are acceptable/preferred
- comments only where useful
- README with:
  - setup
  - test
  - run
  - build
  - directory structure
  - known limitations

---

# 27. Codex execution instruction

Start by creating the repository skeleton and calculation tests.

Then implement the calculation engine.

Run tests and fix all failures.

Only after the business logic passes tests, implement Excel import and GUI.

At each phase:
- keep the application runnable;
- do not remove tests;
- do not replace Decimal with float;
- do not infer missing product mappings automatically.

Final deliverables:
- complete source project
- requirements.txt
- pytest suite
- build_windows.bat
- PyInstaller spec if needed
- README
- sample local database initialization
- Windows executable build instructions
