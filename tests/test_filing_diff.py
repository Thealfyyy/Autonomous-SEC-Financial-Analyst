"""Offline unit tests for YoY filing section diff tool."""

from sec_analyst.tools.filing_diff import diff_section_paragraphs


def test_diff_section_paragraphs_identifies_added_removed_and_modified() -> None:
    prev_paras = [
        "We rely on third-party foundries to manufacture our GPUs.",
        "Foreign currency fluctuations may impact international sales.",
        "Legacy 2023-only supply constraint that has since been resolved.",
    ]
    curr_paras = [
        "We rely on third-party foundries to manufacture our GPUs.",
        "Foreign currency fluctuations and tariffs may materially impact international sales.",
        "New U.S. export control regulations restrict shipments of advanced AI chips.",
    ]

    result = diff_section_paragraphs(
        ticker="nvda",
        item_section="Item 1A",
        previous_year=2023,
        previous_paragraphs=prev_paras,
        current_year=2024,
        current_paragraphs=curr_paras,
    )

    assert result.ticker == "NVDA"
    assert result.unchanged_count == 1
    assert result.added_paragraphs == [
        "New U.S. export control regulations restrict shipments of advanced AI chips."
    ]
    assert result.removed_paragraphs == [
        "Legacy 2023-only supply constraint that has since been resolved."
    ]
    assert len(result.modified_paragraphs) == 1
    assert "tariffs" in result.modified_paragraphs[0].current_text
