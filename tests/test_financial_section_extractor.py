from ngx_research.services.financial_section_extractor import (
    parse_report_pages,
    select_financial_section,
)


def test_parse_report_pages_splits_pdf_text_markers():
    text = "--- Page 1 ---\nCover\n\n--- Page 2 ---\nFinancial statements\n"

    pages = parse_report_pages(text)

    assert [page.number for page in pages] == [1, 2]
    assert pages[0].text == "Cover"
    assert pages[1].text == "Financial statements"


def test_select_financial_section_prefers_later_financial_statement_pages():
    pages = []
    for page_number in range(1, 80):
        body = "Chairman overview and corporate governance narrative."
        if page_number == 52:
            body = "Financial statements table of contents"
        if page_number == 55:
            body = """
            Consolidated statement of profit or loss
            Revenue 300,000
            Profit for the year 50,000
            Earnings per share 2.14
            """
        if page_number == 56:
            body = """
            Statement of financial position
            Total assets 900,000
            Total liabilities 400,000
            Total equity 500,000
            """
        if page_number == 57:
            body = """
            Statement of cash flows
            Net cash generated from operating activities 80,000
            """
        pages.append(f"--- Page {page_number} ---\n{body}")
    report_text = "\n".join(pages)

    selected, warnings = select_financial_section(report_text, max_chars=12000)

    assert "--- Page 1 ---" not in selected
    assert "Consolidated statement of profit or loss" in selected
    assert "Statement of financial position" in selected
    assert "Statement of cash flows" in selected
    assert any("Selected pages:" in warning for warning in warnings)


def test_select_financial_section_keeps_primary_statements_before_large_notes():
    pages = [
        "--- Page 1 ---\nChairman overview",
        "--- Page 20 ---\nNotes to the financial statements\n" + ("Revenue note 1,000\n" * 300),
        "--- Page 30 ---\nEarnings per share\n" + ("EPS details\n" * 300),
        "--- Page 40 ---\nConsolidated statement of comprehensive income\nRevenue 300,000\nLoss after tax 45,000",
        "--- Page 41 ---\nStatement of financial position\nTotal assets 900,000\nTotal equity 500,000",
        "--- Page 42 ---\nStatement of cash flows\nNet cash from operating activities 80,000",
    ]

    selected, warnings = select_financial_section("\n".join(pages), max_chars=1200)

    assert "Consolidated statement of comprehensive income" in selected
    assert "Statement of financial position" in selected
    assert "Statement of cash flows" in selected
    assert "--- Page 1 ---" not in selected
    assert "Revenue note 1,000" not in selected
    assert any("trimmed to priority pages" in warning for warning in warnings)


def test_select_financial_section_uses_local_currency_table_of_contents():
    toc = """
    Financial statements 112
    Expressed in Nigerian Naira
    Independent auditor's report 113
    Statement of profit or loss and other comprehensive income 116
    Statement of financial position 117
    Statement of changes in equity 118
    Statement of cash flows 120
    Notes to the financial statements 121
    Expressed in US Dollars
    Statement of profit or loss and other comprehensive income 183
    Statement of financial position 184
    Statement of cash flows 187
    """
    pages = ["--- Page 2 ---\n" + toc]
    for page_number in range(112, 190):
        body = "Notes to the financial statements"
        if page_number == 116:
            body = "Statement of profit or loss and other comprehensive income\nRevenue 63,384"
        if page_number == 117:
            body = "Statement of financial position\nTotal assets 520,000"
        if page_number == 120:
            body = "Statement of cash flows\nNet cash from operating activities 70,000"
        if page_number == 183:
            body = "Statement of profit or loss and other comprehensive income\nUS dollar statement"
        if page_number == 188:
            body = "Notes to the financial statements\nUSD revenue note"
        pages.append(f"--- Page {page_number} ---\n{body}")

    selected, warnings = select_financial_section("\n".join(pages), max_chars=12000)

    assert "Revenue 63,384" in selected
    assert "Total assets 520,000" in selected
    assert "Net cash from operating activities 70,000" in selected
    assert "US dollar statement" not in selected
    assert "USD revenue note" not in selected
    assert any("Table-of-contents statement pages found" in warning for warning in warnings)


def test_toc_pages_are_kept_when_section_is_trimmed():
    toc = """
    Financial statements
    Statement of profit or loss and other comprehensive income 87
    Statement of financial position 88
    Statement of changes in equity 89
    Statement of cash flows 91
    """
    pages = ["--- Page 1 ---\n" + toc]
    for page_number in range(20, 95):
        body = "Revenue note " + ("1,000 " * 300)
        if page_number == 87:
            body = "Statement of profit or loss and other comprehensive income\nRevenue 452"
        if page_number == 88:
            body = "Statement of financial position\nTotal assets 1,200"
        if page_number == 91:
            body = "Statement of cash flows\nCash generated from operations 447"
        pages.append(f"--- Page {page_number} ---\n{body}")

    selected, warnings = select_financial_section("\n".join(pages), max_chars=4000)

    assert "Revenue 452" in selected
    assert "Total assets 1,200" in selected
    assert "Cash generated from operations 447" in selected
    assert any("trimmed to priority pages" in warning for warning in warnings)


def test_toc_prefers_later_statement_cluster_when_no_currency_split():
    toc = """
    Statement of profit or loss and other comprehensive income 30
    Statement of financial position 31
    Statement of cash flows 34
    Independent auditor's report 86
    Statement of profit or loss and other comprehensive income 87
    Statement of financial position 88
    Statement of changes in equity 89
    Statement of cash flows 91
    """
    pages = ["--- Page 1 ---\n" + toc]
    for page_number in range(28, 94):
        body = "Narrative"
        if page_number == 30:
            body = "Statement of profit or loss and other comprehensive income\nEarly summary"
        if page_number == 87:
            body = "Statement of profit or loss and other comprehensive income\nRevenue 452"
        if page_number == 88:
            body = "Statement of financial position\nTotal assets 1,200"
        if page_number == 91:
            body = "Statement of cash flows\nCash generated from operations 447"
        pages.append(f"--- Page {page_number} ---\n{body}")

    selected, warnings = select_financial_section("\n".join(pages), max_chars=12000)

    assert "Early summary" not in selected
    assert "Revenue 452" in selected
    assert "Total assets 1,200" in selected
    assert "Cash generated from operations 447" in selected
    assert any("Table-of-contents statement pages found" in warning for warning in warnings)


def test_toc_prefers_consolidated_statements_over_separate_wrapped_lines():
    toc = """
    Financial statements 124
    Independent auditors' report 126
    Consolidated statement of profit
    or loss and other comprehensive income 130
    Consolidated statement of financial position 131
    Consolidated statement of changes in equity 132
    Consolidated statement of cash flows 133
    Notes to the consolidated
    financial statements 134
    Separate statement of profit or loss
    and other comprehensive income 187
    Separate statement of financial position 188
    Separate statement of cash flows 190
    """
    pages = ["--- Page 1 ---\n" + toc]
    for page_number in range(124, 192):
        body = "Narrative"
        if page_number == 130:
            body = "Consolidated statement of profit or loss and other comprehensive income\nRevenue 452"
        if page_number == 131:
            body = "Consolidated statement of financial position\nTotal assets 1,200"
        if page_number == 133:
            body = "Consolidated statement of cash flows\nCash generated from operations 447"
        if page_number == 187:
            body = "Separate statement of profit or loss and other comprehensive income\nSeparate revenue"
        pages.append(f"--- Page {page_number} ---\n{body}")

    selected, warnings = select_financial_section("\n".join(pages), max_chars=12000)

    assert "Revenue 452" in selected
    assert "Total assets 1,200" in selected
    assert "Cash generated from operations 447" in selected
    assert "Separate revenue" not in selected
    assert any("Table-of-contents statement pages found" in warning for warning in warnings)


def test_toc_numbers_are_rejected_when_target_pages_are_not_statements():
    toc = """
    Consolidated statement of profit or loss and other comprehensive income 30
    Consolidated statement of financial position 31
    Consolidated statement of cash flows 33
    """
    pages = ["--- Page 1 ---\n" + toc]
    for page_number in range(28, 136):
        body = "Financial review narrative"
        if page_number == 30:
            body = "A spotlight on Nigeria"
        if page_number == 31:
            body = "Additional performance metrics"
        if page_number == 130:
            body = "Consolidated statement of profit or loss and other comprehensive income\nRevenue 452"
        if page_number == 131:
            body = "Consolidated statement of financial position\nTotal assets 1,200"
        if page_number == 133:
            body = "Consolidated statement of cash flows\nCash generated from operations 447"
        pages.append(f"--- Page {page_number} ---\n{body}")

    selected, warnings = select_financial_section("\n".join(pages), max_chars=12000)

    assert "A spotlight on Nigeria" not in selected
    assert "Revenue 452" in selected
    assert "Total assets 1,200" in selected
    assert "Cash generated from operations 447" in selected
    assert not any("Table-of-contents statement pages found" in warning for warning in warnings)


def test_toc_printed_page_numbers_resolve_to_nearby_pdf_pages():
    toc = """
    Financial statements 124
    Independent auditors' report 126
    Consolidated statement of profit
    or loss and other comprehensive income 130
    Consolidated statement of financial position 131
    Consolidated statement of cash flows 133
    """
    pages = ["--- Page 1 ---\n" + toc]
    for page_number in range(120, 136):
        body = "Narrative"
        if page_number == 127:
            body = "Consolidated statement of profit or loss and other comprehensive income\nRevenue 452"
        if page_number == 128:
            body = "Consolidated statement of financial position\nTotal assets 1,200"
        if page_number == 130:
            body = "Consolidated statement of cash flows\nCash generated from operations 447"
        pages.append(f"--- Page {page_number} ---\n{body}")

    selected, warnings = select_financial_section("\n".join(pages), max_chars=12000)

    assert "Revenue 452" in selected
    assert "Total assets 1,200" in selected
    assert "Cash generated from operations 447" in selected
    assert any("Table-of-contents statement pages found" in warning for warning in warnings)


def test_toc_ignores_strategic_statement_entries_before_financial_statements_marker():
    toc = """
    Strategic report 01
    Chairman's statement 10
    Chief Executive Officer's statement 26
    A spotlight on Nigeria 30
    Financial review 50
    Financial statements 124
    Independent auditors' report 126
    Consolidated statement of profit
    or loss and other comprehensive income 130
    Consolidated statement of financial position 131
    Consolidated statement of cash flows 133
    """
    pages = ["--- Page 1 ---\n" + toc]
    for page_number in range(20, 136):
        body = "Narrative"
        if page_number == 29:
            body = "Chief Executive Officer's statement\nNarrative revenue discussion"
        if page_number == 127:
            body = "Consolidated statement of profit or loss and other comprehensive income\nRevenue 452"
        if page_number == 128:
            body = "Consolidated statement of financial position\nTotal assets 1,200"
        if page_number == 130:
            body = "Consolidated statement of cash flows\nCash generated from operations 447"
        pages.append(f"--- Page {page_number} ---\n{body}")

    selected, warnings = select_financial_section("\n".join(pages), max_chars=12000)

    assert "Chief Executive Officer's statement" not in selected
    assert "Revenue 452" in selected
    assert "Total assets 1,200" in selected
    assert "Cash generated from operations 447" in selected
    assert any("Table-of-contents statement pages found" in warning for warning in warnings)


def test_select_financial_section_falls_back_when_no_page_markers():
    selected, warnings = select_financial_section("Revenue 1 Profit 2", max_chars=8)

    assert selected == "Revenue "
    assert warnings == ["Report text had no page markers; used leading text."]
