"""Status-mapping rules for the #14 backfill (pure function — no DB)."""

from recipe_pipeline_server.backfill import map_status
from recipe_pipeline_shared.jobs import JobStatus


def test_uploaded_marker_wins():
    st, err = map_status("x", {"status": "ok"}, uploaded=True, rejected=False)
    assert st is JobStatus.UPLOADED and err is None


def test_rejected_marker():
    st, _ = map_status("x", {"status": "needs_review"}, uploaded=False, rejected=True)
    assert st is JobStatus.REJECTED


def test_ok_still_needs_human_glance():
    st, _ = map_status("x", {"status": "ok"}, uploaded=False, rejected=False)
    assert st is JobStatus.NEEDS_REVIEW


def test_failed_collects_fail_flag_details():
    review = {
        "status": "failed",
        "flags": [
            {"code": "empty_ocr", "severity": "fail", "detail": "no text"},
            {"code": "faint", "severity": "warn", "detail": "pale"},
        ],
    }
    st, err = map_status("x", review, uploaded=False, rejected=False)
    assert st is JobStatus.FAILED and err == "no text"


def test_harvard_beets_explicitly_failed():
    st, err = map_status("06072026-105", {"status": "ok"}, uploaded=False, rejected=False)
    assert st is JobStatus.FAILED and "re-scan" in err
