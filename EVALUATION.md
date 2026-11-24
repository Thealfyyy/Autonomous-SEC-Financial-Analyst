# FinanceBench Evaluation & Failure Analysis

## 1. Controlled Ablation Harness
We evaluate the system using `src/sec_analyst/eval/financebench_runner.py` across three progressive configurations on overlapping `PatronusAI/financebench` `10-K` questions:

1. **`naive_rag`**: Dense vector retrieval only (no XBRL SQL, no BM25/reranker, no Auditor).
2. **`hybrid_rerank`**: Metadata-filtered `pgvector` + FTS/RRF + `bge-reranker-v2-m3` parent expansion (no XBRL SQL).
3. **`full_xbrl_auditor`**: Full architecture (XBRL SQL + Python `Decimal` math + Hybrid RAG + Reranker + Deterministic Auditor).

### Ablation Results *(Measured live on RTX 3090 + `Qwen3-8B-Q4_K_M` + `BAAI/bge-m3` + `BAAI/bge-reranker-v2-m3` + PostgreSQL 16)*

Live corpus at evaluation time: **9 companies** (`NVDA`, `AAPL`, `MSFT`, `GOOGL`, `META`, `AMD`, `TSLA`, `AMZN`, `INTC`), **18 `10-K` filings (`FY2023–FY2024`)**, **755 XBRL facts**, **4,335 parent paragraphs**, and **14,707 child chunks** *(initial ablation ran at 745 facts / 3,004 parents / 10,249 chunks prior to `AMZN`/`INTC` narrative extraction fix)*.
Evaluation set: 8 queries (7 quantitative/ratio/YoY cases across `NVDA`, `AAPL`, `MSFT`, `GOOGL`, `AMD`, `TSLA` + 1 out-of-corpus abstention case `UBER`).

| Configuration | Cases | Numerical Accuracy | Citation Grounding | Abstention Rate | p50 (ms) | p95 (ms) |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| `naive_rag` | 8 | 42.9% | 100.0% | 0.0% | 517.3 | 11764.4 |
| `hybrid_rerank` | 8 | 85.7% | 87.5% | 12.5% | 5665.0 | 13404.4 |
| `full_xbrl_auditor` | 8 | **100.0%** | 87.5% *(100% of non-abstained)* | 12.5% | 2753.8 | 3162.4 |

- `naive_rag` failed 4/7 numeric cases (`nvda_rev_24`, `aapl_rd_pct_24`, `msft_rd_pct_24`, `tsla_rev_24`) and failed to abstain on `UBER` (`0.0%` abstention).
- `hybrid_rerank` improved to `85.7%` numerical accuracy via metadata filtering + Cross-Encoder reranking, but still failed `msft_rd_pct_24` where division was required without deterministic Python math.
- `full_xbrl_auditor` achieved **`100.0%` numerical accuracy** (`0` failed cases), cleanly abstained on `UBER` (`12.5%`), and achieved **p50 = 2.75s / p95 = 3.16s**.

*(Offline unit verification of the evaluation harness passes in `tests/test_eval_runner.py`.)*

---

## 2. Engineering Failure Analysis (SEC EDGAR Pitfalls Solved)

1. **Cross-Company US-GAAP XBRL Tag Drift**
   - *What broke:* Different companies report revenue under different tags (`Revenues` vs. `RevenueFromContractWithCustomerExcludingAssessedTax` vs. `SalesRevenueNet`).
   - *How we solved it:* Built `CANONICAL_CONCEPT_MAP` in `src/sec_analyst/ingestion/xbrl_parser.py` to normalize 15 core financial concepts into `canonical_concept` while preserving `raw_tag` for auditability.

2. **Restatements & Quarterly Noise Inside 10-K `companyfacts.json`**
   - *What broke:* `companyfacts.json` mixes quarterly (`Q4`) entries with full-year (`FY`) facts and includes restated comparative years across multiple filings.
   - *How we solved it:* `_is_full_year_or_instant()` filters out partial-year entries, and `v_latest_xbrl_facts` uses `DISTINCT ON ... ORDER BY filing_date DESC, is_amendment DESC` so restatements (`10-K/A`) automatically supersede older numbers.

3. **Table-of-Contents False Matches, Oversized Single-Newline Blocks & Destroyed HTML Tables**
   - *What broke:* Naive regex section splitting matched `"Item 1A. Risk Factors"` in the Table of Contents instead of the filing body, `soup.get_text()` collapsed financial tables into unreadable single-line strings, and SEC HTML blocks separated by single `\n` produced 100k+ char parent chunks that overflowed the 16k LLM context window.
   - *How we solved it:* `extract_10k_sections()` selects the longest body span per `Item` (ignoring TOC stubs), `_table_to_markdown()` preserves `<table>` rows as Markdown pipe tables, and `_split_oversized_paragraph()` subdivides large blocks on newline/sentence boundaries so every parent stays `<= 1,400` chars.
   - *Known limitation resolved in follow-up:* Table-wrapped `Item` headings (`AMZN`) and integrated annual-report headings (`INTC`) were resolved in `section_chunker.py` (`+1,331` parents / `+4,458` chunks), and `submissions.filings.files` archive pagination was added in `edgar_client.py` (verified live on `JPM`).

---

## 3. Full 250-Prompt Live Stress Test (`docs/TEST_PROMPTS.md`)

Executed all **250 prompts** (`docs/TEST_EVAL_RUN_3_FULL250.json`) live against `POST /analyze` (`Qwen3-8B-Q4_K_M` + `BAAI/bge-m3` + `BAAI/bge-reranker-v2-m3` + PostgreSQL 16):

| Metric | Result |
| :--- | :--- |
| **Overall Pass Rate (raw)** | **`236 / 250` (`94.4%`)** |
| **Overall Pass Rate (with `#248`/`#249` `AMZN`/`INTC` narratives now indexed)** | **`238 / 250` (`95.2%`)** |
| **True Abstention Accuracy (`48` out-of-scope / unindexed / undisclosed cases)** | **`48 / 48` (`100.0%`)** |
| **Unverified Numeric Leaks** | **`0`** *(all 12 answerable misses failed closed via Auditor)* |
