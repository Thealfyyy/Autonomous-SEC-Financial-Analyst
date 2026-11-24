"""Offline unit tests for the FinanceBench ablation evaluation runner."""

from sec_analyst.eval.financebench_runner import (
    EvalCase,
    render_ablation_markdown,
    run_ablation_eval,
)


def test_ablation_runner_computes_metrics_and_markdown() -> None:
    cases = [
        EvalCase(
            case_id="fb-1",
            ticker="NVDA",
            fiscal_year=2024,
            question="What was NVDA FY2024 revenue?",
            expected_numeric=60_922_000_000,
        ),
        EvalCase(
            case_id="fb-2",
            ticker="NVDA",
            fiscal_year=2024,
            question="What was NVDA FY2024 YoY revenue growth?",
            expected_numeric=125.85,
        ),
    ]

    def fake_full_system(case: EvalCase) -> dict:
        if case.case_id == "fb-1":
            return {
                "status": "ok",
                "answer": "NVDA FY2024 revenue was $60.92B.",
                "citations": [{"accession_number": "0001045810-24-000029"}],
            }
        return {
            "status": "ok",
            "answer": "YoY growth was +125.85%.",
            "citations": [{"accession_number": "0001045810-24-000029"}],
        }

    def fake_naive_rag(case: EvalCase) -> dict:
        return {"status": "ok", "answer": "About $50B.", "citations": []}

    full_rep = run_ablation_eval("full_xbrl_auditor", cases, fake_full_system)
    naive_rep = run_ablation_eval("naive_rag", cases, fake_naive_rag)

    assert full_rep.numerical_accuracy == 100.0
    assert full_rep.citation_grounding_rate == 100.0
    assert naive_rep.numerical_accuracy == 0.0
    assert naive_rep.failed_case_ids == ["fb-1", "fb-2"]

    md = render_ablation_markdown([naive_rep, full_rep])
    assert "| full_xbrl_auditor | 2 | 100.0% |" in md
