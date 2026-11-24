# Project Brief: Autonomous SEC Financial Analyst (Hybrid XBRL + Multi-Agent RAG)

## 1. Project Vision & Objective
Build an enterprise-grade, multi-agent financial research assistant that answers complex quantitative, qualitative, and cross-company comparative questions strictly grounded in official **SEC EDGAR filings** (`10-K`, `10-Q`, `8-K`).

Unlike generic "Chat with PDF" wrappers that fail on multi-page financial tables and hallucinate numbers, this system enforces a strict architectural separation:
- **Quantitative metrics & math** are routed to structured **XBRL SQL tables + deterministic Python calculation** (zero LLM mental math).
- **Qualitative analysis** (Risk Factors, MD&A, Cybersecurity disclosures) is routed to **metadata-filtered Hybrid RAG + Cross-Encoder Reranking** with parent-child chunking.
- **Verification** is enforced by an **Auditor node** before any answer reaches the user.

---

## 2. Data Integrity & Openly Licensed Source of Truth
- **Strict Policy:** **Zero synthetic or AI-generated corpus documents.** Every ingested record must come directly from an official public-domain source.
- **Primary Source:** **SEC EDGAR** (`data.sec.gov` JSON APIs / `edgartools` / XBRL archives) — **100% U.S. Federal Public Domain**.
  - Must respect SEC fair-access policy: mandatory descriptive `User-Agent` header and <= 10 requests/second rate limiting.
- **Target Corpus Scope:**
  - **15–20 major public companies** overlapping with the open-source **FinanceBench** dataset (e.g., `AAPL`, `MSFT`, `NVDA`, `AMZN`, `GOOGL`, `META`, `AMD`, `INTC`, `TSLA`, `JPM`, `PFE`, `JNJ`, `WMT`, `KO`, `NFLX`).
  - **3–4 Fiscal Years (`FY2022–FY2025`)** of `10-K` (and optionally `10-Q`) filings (~60–80 full filings, thousands of pages, and structured XBRL facts).
- **External Evaluation Dataset:** Open-source **FinanceBench** (`PatronusAI/financebench` on Hugging Face / GitHub) for objective, independent benchmarking.

---

## 3. Target Architecture & Tech Stack

| Layer | Technology | Purpose |
| :--- | :--- | :--- |
| **API & Streaming** | Python 3.12, **FastAPI**, Pydantic v2, **SSE** | Exposes `/analyze` with real-time Server-Sent Events streaming agent steps |
| **Orchestration** | **LangGraph** | Stateful 4-node directed graph with parallel execution and bounded auditor retry |
| **Quantitative Store** | **PostgreSQL** (or DuckDB) | Normalized XBRL facts (`ticker`, `fiscal_year`, `period`, `concept`, `value`, `unit`) |
| **Qualitative Store** | **`pgvector`** (or Qdrant) + BM25 | Parent-child chunks of `Item 1`, `Item 1A`, `Item 1C`, `Item 7`, `Item 8` |
| **Retrieval & Rerank** | Dense Embeddings + BM25 + **Cross-Encoder** | Metadata-filtered hybrid search + Cross-Encoder reranking (`bge-reranker-v2-m3`) |
| **LLM Runtime** | Local `llama.cpp` / Ollama (RTX 3090) or API | Structured JSON / tool-calling inference with configurable backend |
| **Observability** | **Langfuse** (or OpenTelemetry spans) | Traces per-node latency, SQL queries, chunk scores, and token cost |
| **Frontend UI** | Clean HTML/JS or Next.js/React | Displays live agent reasoning trace, executed SQL/metrics, and filing citations |
| **Infra & CI/CD** | **Docker Compose**, **Pytest**, **GitHub Actions** | One-command startup (`docker compose up`) + automated CI test & build checks |

---

## 4. Multi-Agent Pipeline Specification (`LangGraph`)

1. **Planner / Router Node:**
   - Parses the user prompt into structured sub-tasks: `{quantitative_queries[], qualitative_queries[], tickers[], fiscal_years[]}`.
   - Enforces negative boundaries early: if a requested ticker or fiscal year is outside the indexed corpus, abstains cleanly rather than guessing.
2. **Quantitative Worker Node (Text-to-SQL + Python Sandbox):**
   - Generates **read-only parameterized SQL** against the XBRL table (strictly blocked against `DROP`, `INSERT`, `UPDATE`, `ALTER`, or multi-statement injection).
   - Passes raw retrieved numbers into a safe deterministic Python calculator (for YoY %, margins, CAGR, ratios) so the LLM never performs arithmetic.
3. **Qualitative Worker Node (Hybrid RAG + Reranker):**
   - Executes metadata-filtered (`ticker`, `fiscal_year`, `item_section`) Hybrid Search (Dense + BM25).
   - Reranks top-K child chunks using a Cross-Encoder, then expands to full parent paragraphs for context completeness.
4. **Synthesizer & Auditor Loop:**
   - Synthesizes a cited financial brief.
   - **Auditor Node** deterministically verifies that every numerical figure in the answer matches the SQL/Python output and every narrative claim cites a valid indexed filing (`NVDA 10-K FY2025, Item 1A`).
   - Allows at most 1 self-correction retry if an unverified number or missing citation is detected.

---

## 5. Phased Implementation Plan

### Phase 1: SEC EDGAR Ingestion & Dual-Store Schema
- Build compliant SEC EDGAR downloader (caching raw filings locally so re-runs are offline/fast).
- Design PostgreSQL schema for structured XBRL metrics + `pgvector` schema for parent-child narrative chunks (`Item 1A`, `Item 1C`, `Item 7`).
- Verify ingestion integrity with automated unit tests (row counts, zero duplicate accession numbers, section coverage).

### Phase 2: Hybrid Retrieval, Reranker & Quantitative Tools
- Implement metadata-filtered Hybrid Search (Dense + BM25) + Cross-Encoder Reranker.
- Implement read-only SQL query tool + deterministic financial math calculator with strict safety validation.

### Phase 3: LangGraph Orchestrator & FastAPI SSE Streaming
- Build the 4-node LangGraph workflow (Planner → Quant/Qual Workers → Synthesizer → Auditor).
- Expose `POST /analyze` (streaming SSE events for live node transitions) and `GET /health`.

### Phase 4: Analyst UI & Langfuse Observability
- Build a clean frontend displaying:
  - Live agent execution timeline (*Planner → SQL → RAG → Auditor*).
  - Expandable "Verified Data" drawer showing the exact SQL queries, computed metrics, and cited SEC filing excerpts.
- Wire **Langfuse** tracing for token cost, span latency, and retrieval score telemetry.

### Phase 5: FinanceBench Evaluation & Documentation
- Curate an evaluation runner executing open-source **FinanceBench** questions matching our indexed companies.
- Record and publish exact metrics in `README.md` and `EVALUATION.md`:
  - Numerical accuracy vs. ground truth
  - Citation grounding rate
  - $p50 / p95$ end-to-end latency & cost per query
  - Honest **"Failure Analysis"** section detailing what broke (e.g., XBRL tag taxonomy differences across companies) and how it was solved.

### Phase 6: Containerization & CI/CD
- Package the full stack (`api`, `postgres/pgvector`, optional `model-server`) in `docker-compose.yml`.
- Add `.github/workflows/ci.yml` running `pytest` (using mocked LLM/DB fixtures for fast offline CI) + `docker build` + `docker compose config`.

---

## 6. Non-Negotiable Engineering Guardrails
1. **Zero LLM Math:** All financial ratios, deltas, and totals must be computed by deterministic Python/SQL tools.
2. **Read-Only SQL:** The SQL agent runs with a read-only database role and AST/regex query validation.
3. **Strict Abstention:** Missing filings, unindexed tickers, or unsupported claims must return an explicit `insufficient_evidence` status rather than extrapolating from pretraining memory.
4. **Reproducible Offline CI:** Unit and contract tests must run in GitHub Actions without requiring a live GPU or SEC network calls.
