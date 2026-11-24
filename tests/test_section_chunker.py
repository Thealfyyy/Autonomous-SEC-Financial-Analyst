"""Offline unit tests for 10-K HTML section extraction and parent-child chunking."""

from sec_analyst.ingestion.section_chunker import chunk_sections, extract_10k_sections

SAMPLE_10K_HTML = """
<html>
  <head><script>console.log("ignore");</script></head>
  <body>
    <div>Table of Contents</div>
    <p>Item 1A. Risk Factors ... 12</p>
    <p>Item 7. Management's Discussion ... 30</p>

    <h2>ITEM 1A. RISK FACTORS</h2>
    <p>Our business depends heavily on third-party semiconductor foundries and advanced packaging capacity.
    Any disruption in wafer supply, substrate availability, or export licensing could materially impact revenue.</p>
    <p>We also face intense competition across accelerated computing and networking markets globally.</p>

    <h2>ITEM 1C. CYBERSECURITY</h2>
    <p>We maintain a risk-based cybersecurity program overseen by our Audit Committee and Chief Information Security Officer,
    including regular third-party penetration testing and incident response tabletop exercises.</p>

    <h2>ITEM 7. MANAGEMENT'S DISCUSSION AND ANALYSIS OF FINANCIAL CONDITION</h2>
    <p>Data Center revenue increased significantly year over year due to strong demand for accelerated computing platforms.</p>
    <table>
      <tr><th>Segment</th><th>FY2024 ($M)</th><th>FY2023 ($M)</th></tr>
      <tr><td>Data Center</td><td>47,525</td><td>15,005</td></tr>
      <tr><td>Gaming</td><td>10,447</td><td>9,067</td></tr>
    </table>
  </body>
</html>
"""


def test_extract_10k_sections_skips_toc_and_preserves_tables() -> None:
    sections = extract_10k_sections(SAMPLE_10K_HTML)
    assert set(sections.keys()) == {"Item 1A", "Item 1C", "Item 7"}

    title_1a, body_1a = sections["Item 1A"]
    assert "RISK FACTORS" in title_1a.upper()
    assert "third-party semiconductor foundries" in body_1a

    _, body_7 = sections["Item 7"]
    assert "| Segment | FY2024 ($M) | FY2023 ($M) |" in body_7
    assert "| Data Center | 47,525 | 15,005 |" in body_7


def test_chunk_sections_creates_linked_parent_and_child_chunks() -> None:
    sections = extract_10k_sections(SAMPLE_10K_HTML)
    parents, children = chunk_sections(
        sections,
        parent_max_chars=220,
        child_max_chars=120,
        child_overlap_chars=20,
    )

    assert len(parents) >= 3
    assert len(children) >= len(parents)

    parent_keys = {(p.item_section, p.parent_index) for p in parents}
    for child in children:
        assert (child.item_section, child.parent_index) in parent_keys
        assert len(child.content) <= 120


def test_chunk_sections_subdivides_oversized_single_newline_blocks() -> None:
    long_block = "\n".join([f"Sentence {i} discusses semiconductor supply chain capacity." for i in range(60)])
    parents, children = chunk_sections(
        {"Item 1A": ("Risk Factors", long_block)},
        parent_max_chars=500,
        child_max_chars=200,
        child_overlap_chars=30,
    )
    assert len(parents) > 1
    assert all(len(p.content) <= 500 for p in parents)
    assert len(children) >= len(parents)


def test_extract_10k_sections_handles_table_wrapped_and_integrated_headings() -> None:
    table_heading_html = """
    <html><body>
      <table><tr><td>Item 1A.</td><td>Risk Factors</td><td>6</td></tr></table>
      <table><tr><td>Item 1A.</td><td>Risk Factors</td></tr></table>
      <p>Amazon faces intense competition across retail, cloud computing, and digital media services worldwide.</p>
      <table><tr><td>Item 7.</td><td>Management's Discussion and Analysis</td></tr></table>
      <p>Net sales increased across North America, International, and AWS segments during fiscal year 2024.</p>
    </body></html>
    """
    table_secs = extract_10k_sections(table_heading_html)
    assert {"Item 1A", "Item 7"} <= set(table_secs.keys())
    assert "cloud computing" in table_secs["Item 1A"][1]

    integrated_html = f"""
    <html><body>
      <p>Risk Factors</p>
      <p>{'Semiconductor manufacturing and foundry execution risk. ' * 20}</p>
      <p>Cybersecurity</p>
      <p>{'Board-level oversight of enterprise cybersecurity controls. ' * 20}</p>
    </body></html>
    """
    integrated_secs = extract_10k_sections(integrated_html)
    assert {"Item 1A", "Item 1C"} <= set(integrated_secs.keys())
    assert "foundry execution risk" in integrated_secs["Item 1A"][1]
