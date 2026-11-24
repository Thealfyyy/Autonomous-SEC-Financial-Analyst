# System Weak Points & Failure Analysis Report

- **Source Dataset:** [`docs/TEST_PROMPTS.md`](TEST_PROMPTS.md) (`250` prompts)
- **Live Run:** `93` stratified prompts executed against `http://127.0.0.1:8000/analyze` (`Qwen3-8B-Q4_K_M` + `BAAI/bge-m3` + `BAAI/bge-reranker-v2-m3` + PostgreSQL 16)
- **Raw Log:** [`docs/TEST_EVAL_RUN_1.json`](TEST_EVAL_RUN_1.json)
- **Result Summary:** **`75 / 93` passed (`80.6%`)**, **`18 / 93` failed (`19.4%`)**

---

## 1. Failure Breakdown by Root Cause

| # | Weak Point | Failing Cases (`TEST_EVAL_RUN_1.json`) | Count | Root Cause in Codebase |
| :-: | :--- | :--- | :-: | :--- |
| **1** | **Missing Denominator on Single-Metric Margin Queries** | `#049`, `#055`, `#064`, `#073` *(plus UI screenshot: `AAPL gross margin %`)* | **4** | `Qwen3-8B` Planner queries only the numerator (`gross_profit`, `operating_income`, or `net_income`) and omits `'revenue'`. `_auto_compute_metrics()` in `src/sec_analyst/agents/graph.py` then has no `revenue` row, `Math (0)` stays empty, the Synthesizer guesses a margin %, and the Auditor rejects it. |
| **2** | **Auditor False Positives on `COVID-19`, `"10"`, and Verbatim 10-K Text Numbers** | `#121`, `#160`, `#166`, `#190` | **4** | `_IGNORE_ITEM_NUM_RE` in `src/sec_analyst/agents/auditor.py` does not ignore `COVID-19` (`19` in `#121`, `#166`) or form/product tokens like `10` (`#160`). In `#190` (`GOOGL` Item 7 MD&A change), the Synthesizer quoted dollar figures verbatim from `retrieved_contexts` (`$84,293 million`, etc.), but `verify_answer()` only collects numbers from `sql_results` and `computed_metrics`. |
| **3** | **Synthesizer Drops `citations` on Qualitative RAG Answers** | `#127`, `#136`, `#142`, `#148`, `#163` | **5** | All 5 retrieved `ctx=5` relevant 10-K excerpts, yet `Qwen3-8B` returned `"citations": []`, causing `auditor.py` (`if retrieved_contexts and not citations`) to reject valid grounded answers. |
| **4** | **LLM Planner Misses Boundary Triggers (`AVGO`, `FY2025`, `10-Q`, Undisclosed Data)** | `#211`, `#230`, `#233`, `#250` | **4** | • `#211`: Planner dropped `AVGO` and kept only `NVDA`, bypassing `missing_tickers`.<br>• `#230`: Planner dropped `2025` and kept `[2023, 2024]`, bypassing `missing_years`.<br>• `#233`: Planner ignored `"Q2"` / `"10-Q"` and returned full-year FY2024 revenue (`$60.922B`) as Q2 revenue.<br>• `#250`: Synthesizer answered *"does not include information..."*, but `status` remained `"ok"` instead of `"insufficient_evidence"`. |
| **5** | **Incomplete US-GAAP XBRL Tag Mapping (`INTC` `total_liabilities`)** | `#045` | **1** | `CANONICAL_CONCEPT_MAP["total_liabilities"]` in `src/sec_analyst/ingestion/xbrl_parser.py` only maps `("Liabilities",)`. `INTC` reports liabilities under different US-GAAP tags (e.g. `LiabilitiesCurrent` / `LiabilitiesNoncurrent` or `LiabilitiesAndStockholdersEquity`), yielding `0` SQL rows. |
| **6** | **`filing_diff` Tool Never Triggered by LLM Planner** | `#182`, `#184`, `#186`, `#188`, `#190`, `#192` *(all had `diff=0`)* | **0*** | `Qwen3-8B` consistently left `needs_diff=false` even on explicit YoY diff prompts (`"How did ... change from FY2023 to FY2024?"`), falling back to standard RAG instead of `diff_section_paragraphs()`. |
| **7** | **Pure-Quantitative Queries Return `10-K (0)` Excerpts in UI** | User observation (`NVDA R&D % of revenue`) | **UX** | Pure quantitative questions generate `qualitative_queries: []`, leaving the UI `10-K` drawer tab at `0` even when users expect supporting Item 7/8 filing context alongside XBRL numbers. |

---

## 2. Detailed Evidence from Failed Cases

- **`#045` (`What was INTC total liabilities in FY2024?`)**: `sql=1` (0 rows) -> `Insufficient evidence: No matching records found in indexed SEC filings.`
- **`#049` (`What was NVDA operating margin % in FY2024?`)**: `sql=1, math=0` -> `Auditor verification failed after retry: Unverified numeric values found in draft: 21.5`
- **`#055` (`What was AAPL net margin % in FY2024?`)**: `sql=1, math=0` -> `Auditor verification failed after retry: Unverified numeric values found in draft: 12.35`
- **`#064` (`What was GOOGL operating margin % in FY2024?`)**: `sql=1, math=0` -> `Auditor verification failed after retry: Unverified numeric values found in draft: 11.24`
- **`#073` (`What was AMD gross margin % in FY2024?`)**: `sql=1, math=0` -> `Auditor verification failed after retry: Unverified numeric values found in draft: 45.5`
- **`#121` (`NVDA supply chain risks FY2024 Item 1A`)**: `ctx=5` -> `Unverified numeric values found in draft: 19` (`COVID-19`)
- **`#127` (`AAPL Services segment FY2024 Item 7`)**: `ctx=5` -> `Qualitative evidence was retrieved, but no citations were attached.`
- **`#136` (`GOOGL AI competition risks FY2024 Item 1A`)**: `ctx=5` -> `Qualitative evidence was retrieved, but no citations were attached.`
- **`#142` (`META Family of Apps FY2024 Item 7`)**: `ctx=5` -> `Qualitative evidence was retrieved, but no citations were attached.`
- **`#148` (`AMD Xilinx integration risks FY2024 Item 1A`)**: `ctx=5` -> `Qualitative evidence was retrieved, but no citations were attached.`
- **`#160` (`Compare AAPL and MSFT cybersecurity FY2024 Item 1C`)**: `ctx=5` -> `Unverified numeric values found in draft: 10`
- **`#163` (`TSLA Gigafactory capacity FY2024 Item 1`)**: `ctx=5` -> `Qualitative evidence was retrieved, but no citations were attached.`
- **`#166` (`NVDA FY2024 revenue growth + supply chain risks`)**: `sql=1, math=1, ctx=5` -> `Unverified numeric values found in draft: 19` (`COVID-19`)
- **`#190` (`GOOGL Item 7 MD&A change FY2023->FY2024`)**: `ctx=5` -> `Unverified numeric values found in draft: $84,293 million, $112,390 million, ...`
- **`#211` (`Compare NVDA and AVGO R&D % in FY2024`)**: `act=ok` (answered only `NVDA` `14.24%`, ignored `AVGO`)
- **`#230` (`NVDA Item 1A change FY2024->FY2025`)**: `act=ok` (hallucinated comparison using `FY2023` & `FY2024` despite `FY2025` not indexed)
- **`#233` (`NVDA Q2 FY2024 quarterly revenue from 10-Q`)**: `act=ok` (returned annual `$60,922,000,000` labeled as Q2 quarterly revenue)
- **`#250` (`NVDA private internal employee headcount breakdown`)**: `act=ok` (stated *"does not include information..."* without setting `status: insufficient_evidence`)

---

## 3. Fix Plan (Next Step)
1. **Planner Guardrails (`graph.py`):** Deterministic regex extraction for ticker symbols, 4-digit years, `10-Q`/`8-K`/`Q1–Q4` quarterly scope rejection, automatic `needs_diff=True` on YoY section-change prompts, and deterministic `revenue` inclusion on margin/`% of revenue` SQL queries.
2. **Auditor & Synthesizer Grounding (`auditor.py`, `graph.py`):** Ignore `COVID-19` / `ASC \d+` / `Section \d+` tokens, verify numbers appearing verbatim in `retrieved_contexts` for qualitative text, and auto-attach grounded `Citation` objects from retrieved contexts/SQL rows when the LLM omits `citations`.
3. **XBRL Tag Coverage (`xbrl_parser.py`):** Inspect `INTC` `companyfacts.json` for `total_liabilities` tag variants and update `CANONICAL_CONCEPT_MAP`.
4. **UI / Excerpt UX:** Optionally attach top `Item 7`/`Item 8` context or filing metadata card for pure-quantitative queries so `10-K` provenance is always inspectable.

---

## 4. Post-Fix Verification

All 7 weak points above were implemented across `xbrl_parser.py`, `auditor.py`, and `graph.py`, and verified offline (`ruff check src tests`: `All checks passed!`; `pytest -v`: `38 passed in 2.47s`) and live against `http://127.0.0.1:8000/analyze`:

1. **94-Case Stratified Live Re-Evaluation (`docs/TEST_EVAL_RUN_2.json`):** **`88 / 94` passed (`93.6%`)** (up from `75 / 93`, `80.6%`), including `#045` (`INTC` total liabilities) and `#053` (`AAPL` gross margin % from the UI screenshot).
2. **Targeted Live Re-Verification (`#121`, `#188`, `#230`, `#250`):** **4 / 4 passed** after fixing:
   - `auditor.py`: stripped markdown/inline numbered-list markers (`1.`, `### 1.`, `(1) `) and bracketed citation indices (`[1]`), plus `COVID`/`ASC`/`Section`/`Windows`/`Context` tokens.
   - `graph.py`: fixed `FY2025` year extraction (`(?<!\d)(20\d{2})(?!\d)`), added `route_after_synthesizer` + honest-abstention detection for `#250`, and ensured YoY `diff` context attachment passes cleanly (`#188`).
3. **Remaining Observed Limitations (Honest Disclosure):**
   - **`#119` & `#170` (`TEST_EVAL_RUN_2.json`):** `Qwen3-8B` occasionally emits a hallucinated or 1000x-mis-scaled number (`$40,870,000,000` in `#119`; `$245,122 billion` in `#170`). The Deterministic Auditor **caught and blocked both** rather than returning unverified numbers.
   - **Corpus Ingestion Edge Cases:** `AMZN` and `INTC` `10-K` HTML filings split `Item` headings across nested tags (`0` narrative sections extracted, though XBRL facts are ingested cleanly), and `JPM` `10-K` filings sit in `submissions.filings.files` archive pages past `recent`.

---

## 5. Full 250-Prompt Live Evaluation (`TEST_EVAL_RUN_3_FULL250.json`)

After implementing table-wrapped/integrated `10-K` heading extraction (`AMZN`: `489` parents / `1,645` chunks; `INTC`: `842` parents / `2,813` chunks), EDGAR archive pagination (`JPM`), Synthesizer `formatted_value` pre-scaling, and `10-Q` schema/routing support, all **250 prompts** in [`docs/TEST_PROMPTS.md`](TEST_PROMPTS.md) were executed live against `http://127.0.0.1:8000/analyze` (`Qwen3-8B-Q4_K_M`, `BAAI/bge-m3`, `BAAI/bge-reranker-v2-m3`, PostgreSQL 16):

- **Live Corpus:** `9` companies, `18` `10-K` filings (`FY2023–FY2024`), `755` XBRL facts, `4,335` parent paragraphs, `14,707` child chunks (all 9 companies with full narrative coverage).
- **Raw Score (`TEST_EVAL_RUN_3_FULL250.json`):** **`236 / 250` (`94.4%`)** (`188 / 200` = `94.0%` answerable; `48 / 50` = `96.0%` abstain).
- **Adjusted Score (reclassifying `#248` & `#249`):** **`238 / 250` (`95.2%`)** — `#248` (`AMZN Item 1A`) and `#249` (`INTC Item 1A`) were originally written as abstain cases only because `AMZN`/`INTC` had `0` narrative chunks; after the section-chunker fix, both answered accurately, yielding **`48 / 48` (`100.0%`)** on all true abstain cases and **`190 / 202` (`94.1%`)** on answerable cases.
- **Zero-Hallucination Safety:** Every one of the `12` remaining answerable misses (`#082`, `#083`, `#087`, `#090`, `#097`, `#100`, `#106`, `#119`, `#158`, `#177`, `#187`, `#192`) failed closed via the Deterministic Auditor (mental A−B deltas or numbers quoted from `diff_results`) rather than leaking unverified numbers.
- **End-to-End Latency:** `p50 = 8.33s`, `p95 = 16.36s` *(CPU embedding/reranker + local RTX 3090 `Qwen3-8B`)*.
