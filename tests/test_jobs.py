from ngx_research import jobs


def test_daily_sync_mode_uses_lightweight_equity_feeds():
    assert jobs._sync_step_labels_for_mode("daily") == (
        "stocks",
        "fundamentals",
        "disclosures",
        "market-news",
    )


def test_full_sync_mode_includes_manual_heavy_feeds():
    assert jobs._sync_step_labels_for_mode("full") == (
        "stocks",
        "fundamentals",
        "disclosures",
        "indices",
        "etfs",
        "bonds",
        "bond-auctions",
        "nasd-otc-stocks",
        "market-news",
    )


def test_daily_sync_mode_skips_dividends_by_default(monkeypatch):
    monkeypatch.setattr(jobs.settings, "automation_daily_dividend_sync_enabled", False)
    monkeypatch.setattr(jobs.settings, "automation_dividend_sync_enabled", True)

    assert jobs._include_dividends_for_mode("daily", None) is False
    assert jobs._include_dividends_for_mode("full", None) is True
    assert jobs._include_dividends_for_mode("daily", True) is True
