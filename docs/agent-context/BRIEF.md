---
project: Autonomous SEC Financial Analyst
owner: Project Owner
status: planning — initial scoping & brief analysis
last_updated: 2026-11-13
---

# Project Brief: Autonomous SEC Financial Analyst

> **Guidelines for this file:**
> - **Purpose:** `BRIEF.md` is the stable, living single source of truth for *present truth*—what the project is, why it is shaped this way, and what "done" means.
> - **What belongs here:** Locked architecture decisions, hard constraints, stack choices, explicit scope boundaries, and codebase structure.
> - **What does NOT belong here:** Routine session progress, bug-fix diaries, or inline `UPDATE:` history notes (those belong in `PROGRESS_LOG.md`). When a decision changes, rewrite the relevant section cleanly so the brief always reflects the current state.

Read this file in full before doing any work. Read `PROGRESS_LOG.md` next (most recent entries at the bottom), then `TASKS.md`, then `REQUESTS.md`.

Source brief: [`docs/SEC_FINANCIAL_ANALYST_BRIEF.md`](../SEC_FINANCIAL_ANALYST_BRIEF.md).

---

## 1. What this project is

An enterprise-grade, multi-agent financial research assistant that answers complex quantitative, qualitative, and cross-company comparative questions strictly grounded in official **SEC EDGAR filings** (`10-K`, `10-Q`, `8-K`).

Unlike generic "Chat with PDF" wrappers that fail on multi-page financial tables and hallucinate numbers, the system enforces strict architectural separation:
- **Quantitative metrics & math** are routed to structured **XBRL SQL tables + deterministic Python calculation** (zero LLM mental math).
- **Qualitative analysis** (Risk Factors, MD&A, Cybersecurity disclosures) is routed to **metadata-filtered Hybrid RAG + Cross-Encoder Reranking** with parent-child chunking.
- **Verification** is enforced by an **Auditor node** before any answer reaches the user.

---

## 2. Context, deadlines, and hard constraints

- **Timeline / Deadlines:** *TBD — not specified in initial brief.*
- **Hard Constraints:**
  - **Zero synthetic or AI-generated corpus documents:** Every ingested record must come directly from official public-domain SEC EDGAR sources (`data.sec.gov` JSON APIs / `edgartools` / XBRL archives).
  - **SEC Fair-Access Compliance:** Mandatory descriptive `User-Agent` header and <= 10 requests/second rate limiting, plus local raw-filing caching.
  - **Zero LLM Math:** All financial ratios, deltas, and totals must be computed by deterministic Python/SQL tools.
  - **Read-Only SQL:** The SQL agent runs with a read-only database role and AST/regex query validation (no `DROP`, `INSERT`, `UPDATE`, `ALTER`, or multi-statement injection).
  - **Strict Abstention:** Missing filings, unindexed tickers, or unsupported claims must return an explicit `insufficient_evidence` status rather than extrapolating from pretraining memory.
  - **Reproducible Offline CI:** Unit and contract tests must run in GitHub Actions without requiring a live GPU or SEC network calls.

---

## 3. Locked-in decisions

| Area | Decision | Rationale |
| :--- | :--- | :--- |
| **Orchestration** | LangGraph 4-node stateful graph (`Planner -> Quant/Qual Workers -> Synthesizer -> Auditor`) with bounded retry (max 1) | Deterministic control flow, parallel worker execution, and explicit verification loop |
| **Quant/Qual Split** | XBRL SQL + Python sandbox for numbers; Hybrid RAG + Cross-Encoder for narrative | Eliminates LLM arithmetic errors and table-parsing hallucinations |
| **Corpus Scope** | 15–20 major public companies overlapping with FinanceBench, FY2022–FY2025 (`10-K`, optionally `10-Q`) | Manageable local corpus (~60–80 filings) with objective external benchmark coverage |
| **Benchmarking** | `PatronusAI/financebench` (open-source) | Independent, reproducible numerical accuracy and grounding evaluation |
| **Reranker** | Cross-Encoder (`bge-reranker-v2-m3`) over top-K child chunks, then parent paragraph expansion | High precision retrieval with full surrounding context |

*Locked decisions (updated 2026-11-13):*
- **Database:** Single **PostgreSQL + `pgvector`** instance (handles structured XBRL SQL tables, vector embeddings, and BM25/FTS).
- **Phase 1 Filing Scope:** **`10-K` annual reports only** in v1 (`10-Q` deferred to post-v1).
- **LLM Runtime:** **Local** inference on RTX 3090 (24 GB VRAM):
  - **Phases 1–4:** `Qwen3-8B-Q4_K_M.gguf` (via `llama.cpp` OpenAI-compatible server) + `BAAI/bge-m3` embeddings (~1024-dim) + `BAAI/bge-reranker-v2-m3` (Phase 2).
  - **Phase 5 Benchmarking:** Compare `Qwen3-8B-Q4_K_M` vs. `Qwen3-14B-Q4_K_M` (and optionally `Qwen3-30B-A3B`) on FinanceBench.
- **Frontend UI:** Clean **HTML/JS** (designed/generated via Stitch MCP), served cleanly with FastAPI SSE streaming.

*v1 Differentiating Enhancements (all 5 locked in):*
1. **Controlled Ablation Study (`EVALUATION.md`):** Benchmark Naive RAG vs. Hybrid RAG + Reranker vs. Full XBRL + Multi-Agent + Auditor on FinanceBench.
2. **YoY Risk & MD&A Diff Tool:** Compare `Item 1A` / `Item 1C` across `FY(t-1)` and `FY(t)` to surface newly added, removed, or escalated risks.
3. **Click-to-Verify Provenance + Audit Memo Export:** Direct `sec.gov` filing URLs (from `cik` + `accession_number`), exact XBRL tag/period/SQL/formula inspection, and Markdown/JSON memo export.
4. **Automated Adversarial & Guardrail Tests (`tests/test_guardrails.py`):** Offline CI coverage for SQL injection, `insufficient_evidence` abstention traps, unit-mismatch prevention, and Auditor catch verification.
5. **On-Demand Live SEC Ingestion (`POST /ingest`):** Fetch and index any valid SEC ticker's `10-K` filings on demand while respecting SEC rate limits.

---

## 4. Tech stack & tools

- **Language & Runtime:** Python 3.12
- **API & Streaming:** FastAPI, Pydantic v2, Server-Sent Events (SSE)
- **Orchestration:** LangGraph
- **Data & Storage:** PostgreSQL + `pgvector` (or DuckDB / Qdrant — TBD) + BM25
- **Retrieval & Rerank:** Dense embeddings (TBD) + BM25 + `bge-reranker-v2-m3`
- **LLM Runtime:** Local `llama.cpp` / Ollama (RTX 3090) or API (configurable backend)
- **Observability:** Langfuse (or OpenTelemetry spans)
- **Frontend UI:** Clean HTML/JS or Next.js/React (TBD)
- **Testing & Infra:** Pytest, Docker Compose, GitHub Actions

---

## 5. Current scope

**In scope right now (Phase 1 & overall v1 plan):**
- Phase 1: Compliant SEC EDGAR downloader (with local caching), dual-store schema (XBRL metrics + parent-child narrative chunks for `Item 1`, `Item 1A`, `Item 1C`, `Item 7`, `Item 8`), and ingestion integrity tests.
- Phase 2: Metadata-filtered Hybrid Search (Dense + BM25) + Cross-Encoder Reranker, read-only SQL tool, and deterministic Python calculator.
- Phase 3: 4-node LangGraph workflow + FastAPI `POST /analyze` (SSE) and `GET /health`.
- Phase 4: Analyst UI with live execution timeline + "Verified Data" drawer, plus Langfuse tracing.
- Phase 5: FinanceBench evaluation runner + `README.md` / `EVALUATION.md` with failure analysis.
- Phase 6: `docker-compose.yml` + `.github/workflows/ci.yml` offline CI.

**Explicitly out of scope for now (and why):**
- Synthetic or AI-generated corpus documents — *Deferred/Prohibited because: strict data-integrity policy requires 100% official SEC EDGAR public-domain sources.*
- Unindexed tickers or arbitrary web-search fallback — *Deferred/Prohibited because: strict abstention (`insufficient_evidence`) is required to prevent hallucination outside the verified corpus.*

---

## 6. How this project is organized

```text
Autonomous SEC Financial Analyst/
├── docs/
│   ├── SEC_FINANCIAL_ANALYST_BRIEF.md   # Original project brief
│   └── agent-context/                   # Continuity files (BRIEF, TASKS, REQUESTS, PROGRESS_LOG)
├── src/                                 # (Planned) Core application logic
├── tests/                               # (Planned) Automated test suite
└── docker-compose.yml                   # (Planned) Local stack orchestration
```

---

## 7. Working norms & verification standards

- **Definition of Done:** All unit/contract tests pass (`pytest`) offline without GPU/network dependencies; code adheres to non-negotiable guardrails (zero LLM math, read-only SQL, strict abstention).
- **Review / Gate Rules:** Confirm open architecture choices (DB, embedding model, LLM backend, `10-Q`/`8-K` scope) before starting Phase 1 implementation.
