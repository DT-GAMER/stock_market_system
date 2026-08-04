from __future__ import annotations

import re
from dataclasses import dataclass

MAX_SECTION_CHARS = 60000
PAGE_HEADER_RE = re.compile(r"--- Page (?P<number>\d+) ---")
TOC_PAGE_NUMBER_RE = re.compile(r"(?<![,.\d])(?P<number>\d{1,3})\s*$")

HIGH_VALUE_PATTERNS = (
    re.compile(r"\bstatement\b.{0,80}\bprofit or loss\b", re.IGNORECASE),
    re.compile(r"\bstatement\b.{0,80}\bcomprehensive income\b", re.IGNORECASE),
    re.compile(r"\bstatement of financial position\b", re.IGNORECASE),
    re.compile(r"\bstatement of cash flows?\b", re.IGNORECASE),
    re.compile(r"\bstatement of changes in equity\b", re.IGNORECASE),
    re.compile(r"\bconsolidated statement\b", re.IGNORECASE),
)

MEDIUM_VALUE_PATTERNS = (
    re.compile(r"\bnotes to the financial statements\b", re.IGNORECASE),
    re.compile(r"\bfinancial statements\b", re.IGNORECASE),
    re.compile(r"\bindependent auditor", re.IGNORECASE),
    re.compile(r"\bearnings per share\b", re.IGNORECASE),
    re.compile(r"\bprofit for the year\b", re.IGNORECASE),
    re.compile(r"\brevenue\b", re.IGNORECASE),
    re.compile(r"\btotal assets\b", re.IGNORECASE),
    re.compile(r"\btotal liabilities\b", re.IGNORECASE),
    re.compile(r"\bcash generated from operations\b", re.IGNORECASE),
    re.compile(r"\boperating cash flows?\b", re.IGNORECASE),
)

PRIMARY_STATEMENT_PATTERNS = (
    re.compile(r"\bstatement\b.{0,80}\bprofit or loss\b", re.IGNORECASE),
    re.compile(r"\bstatement\b.{0,80}\bcomprehensive income\b", re.IGNORECASE),
    re.compile(r"\bincome statement\b", re.IGNORECASE),
    re.compile(r"\bstatement of financial position\b", re.IGNORECASE),
    re.compile(r"\bbalance sheet\b", re.IGNORECASE),
    re.compile(r"\bstatement of cash flows?\b", re.IGNORECASE),
    re.compile(r"\bstatement of changes in equity\b", re.IGNORECASE),
)

CONSOLIDATED_PATTERN = re.compile(r"\bconsolidated\b", re.IGNORECASE)
SEPARATE_PATTERN = re.compile(r"\bseparate\b", re.IGNORECASE)

SUPPORTING_NOTE_PATTERNS = (
    re.compile(r"\bearnings per share\b", re.IGNORECASE),
    re.compile(r"\bdividends?\b", re.IGNORECASE),
    re.compile(r"\brevenue\b", re.IGNORECASE),
    re.compile(r"\bsegment information\b", re.IGNORECASE),
    re.compile(r"\bnotes to the financial statements\b", re.IGNORECASE),
)

LOW_VALUE_PATTERNS = (
    re.compile(r"\bchairman", re.IGNORECASE),
    re.compile(r"\bchief executive", re.IGNORECASE),
    re.compile(r"\bcorporate governance\b", re.IGNORECASE),
    re.compile(r"\bdirectors'? report\b", re.IGNORECASE),
    re.compile(r"\bnotice of annual general meeting\b", re.IGNORECASE),
    re.compile(r"\bshareholder information\b", re.IGNORECASE),
    re.compile(r"\bproxy form\b", re.IGNORECASE),
)


@dataclass(frozen=True)
class ReportPage:
    number: int
    text: str


def select_financial_section(report_text: str, max_chars: int = MAX_SECTION_CHARS) -> tuple[str, list[str]]:
    pages = parse_report_pages(report_text)
    if not pages:
        return report_text[:max_chars], ["Report text had no page markers; used leading text."]

    warnings: list[str] = []
    toc_numbers = _toc_statement_page_numbers(pages)
    selected_numbers = set(toc_numbers)
    if toc_numbers:
        warnings.append(
            "Table-of-contents statement pages found: "
            + ", ".join(_compress_page_ranges(sorted(toc_numbers)))
        )
        selected_numbers.update(_nearby_supporting_note_page_numbers(pages, toc_numbers))
    else:
        selected_numbers = _primary_statement_page_numbers(pages)
        if selected_numbers:
            selected_numbers.update(_supporting_note_page_numbers(pages))

    if not selected_numbers:
        scored = [(page, _score_page(page.text)) for page in pages]
        anchors = [page for page, score in scored if score >= 8]
        best_pages = [page for page, score in sorted(scored, key=lambda item: item[1], reverse=True)[:8] if score > 0]
        anchors = best_pages or pages[:8]
        warnings.append("No strong financial statement anchor found; used best-scoring pages.")
        selected_numbers = _expand_anchors(anchors, pages)

    selected_pages = [page for page in pages if page.number in selected_numbers]
    section = _join_pages(selected_pages)

    if len(section) > max_chars:
        selected_pages = _prioritize_pages_within_limit(
            selected_pages,
            max_chars,
            mandatory_numbers=toc_numbers,
        )
        section = _join_pages(selected_pages)[:max_chars]
        warnings.append(f"Financial section exceeded {max_chars} characters; trimmed to priority pages.")

    warnings.append(
        "Selected pages: "
        + ", ".join(_compress_page_ranges([page.number for page in selected_pages])[:12])
    )
    return section, warnings


def parse_report_pages(report_text: str) -> list[ReportPage]:
    matches = list(PAGE_HEADER_RE.finditer(report_text))
    if not matches:
        return []

    pages: list[ReportPage] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(report_text)
        pages.append(ReportPage(number=int(match.group("number")), text=report_text[start:end].strip()))
    return pages


def _score_page(text: str) -> int:
    score = 0
    score += 10 * sum(1 for pattern in HIGH_VALUE_PATTERNS if pattern.search(text))
    score += 3 * sum(1 for pattern in MEDIUM_VALUE_PATTERNS if pattern.search(text))
    score -= 4 * sum(1 for pattern in LOW_VALUE_PATTERNS if pattern.search(text))

    numeric_density = len(re.findall(r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b|\b\d+\.\d+\b", text))
    if numeric_density >= 30:
        score += 8
    elif numeric_density >= 12:
        score += 4

    return score


def _expand_anchors(anchors: list[ReportPage], pages: list[ReportPage]) -> set[int]:
    page_numbers = {page.number for page in pages}
    selected: set[int] = set()
    for anchor in anchors:
        for number in range(anchor.number - 2, anchor.number + 14):
            if number in page_numbers:
                selected.add(number)

    if len(selected) < 8:
        first_anchor = min(anchor.number for anchor in anchors)
        for number in range(first_anchor, first_anchor + 24):
            if number in page_numbers:
                selected.add(number)
    return selected


def _toc_statement_page_numbers(pages: list[ReportPage]) -> set[int]:
    toc_text = "\n".join(page.text for page in pages[:20])
    if not toc_text:
        return set()

    financial_toc_section = _financial_statements_toc_section(toc_text)
    local_section = _local_currency_toc_section(financial_toc_section)
    page_numbers = {page.number for page in pages}
    entries: list[tuple[str, int]] = []
    for line in _toc_entries(local_section):
        if not any(pattern.search(line) for pattern in PRIMARY_STATEMENT_PATTERNS):
            continue
        match = TOC_PAGE_NUMBER_RE.search(line.strip())
        if not match:
            continue
        entries.append((line, int(match.group("number"))))

    entries = _preferred_toc_entries(entries)
    page_by_number = {page.number: page for page in pages}
    selected: set[int] = set()
    for line, statement_page in entries:
        resolved_page = _resolve_toc_statement_page(line, statement_page, page_by_number)
        if resolved_page is None:
            continue
        candidate_numbers = {
            number for number in range(resolved_page - 1, resolved_page + 2) if number in page_numbers
        }
        selected.update(candidate_numbers)
    return _dominant_page_cluster(selected)


def _resolve_toc_statement_page(
    toc_line: str,
    printed_page: int,
    page_by_number: dict[int, ReportPage],
) -> int | None:
    expected_patterns = _statement_patterns_for_toc_line(toc_line)
    search_numbers = [printed_page]
    for distance in range(1, 16):
        search_numbers.extend([printed_page - distance, printed_page + distance])

    for number in search_numbers:
        page = page_by_number.get(number)
        if not page:
            continue
        if any(pattern.search(page.text) for pattern in expected_patterns):
            return number
    return None


def _statement_patterns_for_toc_line(toc_line: str) -> tuple[re.Pattern[str], ...]:
    lowered = toc_line.lower()
    if "financial position" in lowered or "balance sheet" in lowered:
        return (
            re.compile(r"\bstatement of financial position\b", re.IGNORECASE),
            re.compile(r"\bbalance sheet\b", re.IGNORECASE),
        )
    if "cash flow" in lowered:
        return (re.compile(r"\bstatement of cash flows?\b", re.IGNORECASE),)
    if "changes in equity" in lowered:
        return (re.compile(r"\bstatement of changes in equity\b", re.IGNORECASE),)
    if "profit or loss" in lowered or "comprehensive income" in lowered:
        return (
            re.compile(r"\bstatement\b.{0,80}\bprofit or loss\b", re.IGNORECASE),
            re.compile(r"\bstatement\b.{0,80}\bcomprehensive income\b", re.IGNORECASE),
        )
    return PRIMARY_STATEMENT_PATTERNS


def _financial_statements_toc_section(toc_text: str) -> str:
    marker_starts = _financial_statement_marker_starts(toc_text)
    if marker_starts:
        return toc_text[marker_starts[-1] :]
    return toc_text


def _financial_statement_marker_starts(toc_text: str) -> list[int]:
    starts: list[int] = []
    offset = 0
    previous_line = ""
    for raw_line in toc_text.splitlines(keepends=True):
        line = " ".join(raw_line.strip().split())
        if re.fullmatch(r"financial statements\s+\d{1,3}", line, re.IGNORECASE) and "notes" not in previous_line.lower():
            starts.append(offset)
        if line:
            previous_line = line
        offset += len(raw_line)
    return starts


def _toc_entries(toc_text: str) -> list[str]:
    entries: list[str] = []
    current = ""
    for raw_line in toc_text.splitlines():
        line = " ".join(raw_line.strip().split())
        if not line:
            continue
        if TOC_PAGE_NUMBER_RE.search(line):
            entries.append((current + " " + line).strip())
            current = ""
        else:
            current = (current + " " + line).strip()
    return entries


def _preferred_toc_entries(entries: list[tuple[str, int]]) -> list[tuple[str, int]]:
    consolidated_entries = [entry for entry in entries if CONSOLIDATED_PATTERN.search(entry[0])]
    if consolidated_entries:
        return consolidated_entries
    non_separate_entries = [entry for entry in entries if not SEPARATE_PATTERN.search(entry[0])]
    return non_separate_entries or entries


def _local_currency_toc_section(toc_text: str) -> str:
    local_match = re.search(r"expressed in nigerian naira", toc_text, re.IGNORECASE)
    foreign_match = re.search(r"expressed in (?:us|u\.s\.) dollars?", toc_text, re.IGNORECASE)
    if local_match and foreign_match and local_match.start() < foreign_match.start():
        return toc_text[local_match.start() : foreign_match.start()]
    return toc_text


def _dominant_page_cluster(numbers: set[int]) -> set[int]:
    ranges = _page_clusters(numbers)
    if len(ranges) <= 1:
        return numbers
    substantial_ranges = [current_range for current_range in ranges if len(current_range) >= 3]
    if substantial_ranges:
        return set(substantial_ranges[-1])
    return set(max(ranges, key=len))


def _page_clusters(numbers: set[int]) -> list[list[int]]:
    ordered = sorted(numbers)
    if not ordered:
        return []
    clusters: list[list[int]] = [[ordered[0]]]
    for number in ordered[1:]:
        if number <= clusters[-1][-1] + 3:
            clusters[-1].append(number)
        else:
            clusters.append([number])
    return clusters


def _primary_statement_page_numbers(pages: list[ReportPage]) -> set[int]:
    page_numbers = {page.number for page in pages}
    selected: set[int] = set()
    for page in pages:
        if any(pattern.search(page.text) for pattern in PRIMARY_STATEMENT_PATTERNS):
            for number in range(page.number - 1, page.number + 7):
                if number in page_numbers:
                    selected.add(number)
    return selected


def _supporting_note_page_numbers(pages: list[ReportPage]) -> set[int]:
    selected: set[int] = set()
    for page in pages:
        if any(pattern.search(page.text) for pattern in SUPPORTING_NOTE_PATTERNS):
            selected.add(page.number)
    return selected


def _nearby_supporting_note_page_numbers(pages: list[ReportPage], anchors: set[int]) -> set[int]:
    if not anchors:
        return set()
    upper_bound = max(anchors) + 8
    lower_bound = min(anchors) - 2
    selected: set[int] = set()
    for page in pages:
        if page.number < lower_bound or page.number > upper_bound:
            continue
        if any(pattern.search(page.text) for pattern in SUPPORTING_NOTE_PATTERNS):
            selected.add(page.number)
    return selected


def _priority_rank(page: ReportPage) -> tuple[int, int, int]:
    text = page.text
    if any(pattern.search(text) for pattern in PRIMARY_STATEMENT_PATTERNS):
        bucket = 3
    elif any(pattern.search(text) for pattern in SUPPORTING_NOTE_PATTERNS):
        bucket = 2
    else:
        bucket = 1
    return (bucket, _score_page(text), -page.number)


def _prioritize_pages_within_limit(
    pages: list[ReportPage],
    max_chars: int,
    mandatory_numbers: set[int] | None = None,
) -> list[ReportPage]:
    mandatory_numbers = mandatory_numbers or set()
    mandatory_pages = [page for page in pages if page.number in mandatory_numbers]
    remaining_pages = [page for page in pages if page.number not in mandatory_numbers]
    ordered_pages = [
        *sorted(mandatory_pages, key=lambda page: page.number),
        *sorted(remaining_pages, key=_priority_rank, reverse=True),
    ]
    selected: list[ReportPage] = []
    used = 0
    for page in ordered_pages:
        page_text = _format_page(page)
        if used + len(page_text) > max_chars and selected:
            continue
        selected.append(page)
        used += len(page_text)
        if used >= max_chars:
            break
    return sorted(selected, key=lambda page: page.number)


def _join_pages(pages: list[ReportPage]) -> str:
    return "\n\n".join(_format_page(page) for page in pages)


def _format_page(page: ReportPage) -> str:
    return f"--- Page {page.number} ---\n{page.text}"


def _compress_page_ranges(numbers: list[int]) -> list[str]:
    if not numbers:
        return []
    ordered = sorted(set(numbers))
    ranges: list[str] = []
    start = previous = ordered[0]
    for number in ordered[1:]:
        if number == previous + 1:
            previous = number
            continue
        ranges.append(f"{start}-{previous}" if start != previous else str(start))
        start = previous = number
    ranges.append(f"{start}-{previous}" if start != previous else str(start))
    return ranges
