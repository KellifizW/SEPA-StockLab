"""
tests/test_data_pipeline_news_normalize.py
──────────────────────────────────────────
Unit tests for news normalization helpers in data_pipeline.
"""

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_normalize_news_frame_populates_source_column():
    from modules.data_pipeline import _normalize_news_frame

    raw = pd.DataFrame(
        [
            {
                "Date": "2026-03-07 08:30:00",
                "Title": "Fed comments move futures",
                "Link": "https://example.com/a",
            },
            {
                "Date": "2026-03-07 08:00:00",
                "Title": "Oil jumps as tensions rise",
                "Link": "https://example.com/b",
            },
        ]
    )

    out = _normalize_news_frame(raw, source="wsj", ticker="SPY", max_rows=10)

    assert not out.empty
    assert "source" in out.columns
    assert out["source"].isna().sum() == 0
    assert set(out["source"].unique()) == {"wsj"}
