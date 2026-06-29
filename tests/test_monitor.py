"""Tests for the regression-detection logic (pure functions)."""

from monitor import regressions, topic_metrics


def test_topic_metrics_counts_pages_faqs_files():
    data = {
        "pages": [
            {
                "url": "u",
                "blocks": [
                    {"segments": [{"faqs": {"QAs": [{"question": "q", "answer": "a"}]}}]},
                    {"segments": [{"files": "x.pdf"}]},
                ],
            }
        ]
    }
    m = topic_metrics(data)
    assert m["pages"] == 1 and m["faqs"] == 1 and m["files"] == 1


def test_no_baseline_means_no_regression():
    assert regressions(None, {"pages": 0, "faqs": 0, "files": 0, "chars": 0}) == []


def test_detects_page_and_faq_and_content_drops():
    old = {"pages": 6, "faqs": 20, "files": 5, "chars": 10000}
    new = {"pages": 4, "faqs": 5, "files": 5, "chars": 3000}
    drops = regressions(old, new)
    assert any("pages" in d for d in drops)
    assert any("FAQ" in d for d in drops)
    assert any("content" in d for d in drops)


def test_small_fluctuation_is_not_a_regression():
    old = {"pages": 6, "faqs": 20, "files": 5, "chars": 10000}
    new = {"pages": 6, "faqs": 19, "files": 5, "chars": 9500}  # minor wiggle
    assert regressions(old, new) == []
