
from hubspot_mcp.audit import get_recent_audits, log_write


def test_log_and_retrieve_audits(tmp_path, monkeypatch):
    from pathlib import Path
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    log_write("123", "create_contact", "ObjectsAgent", {"id": "1"})
    log_write("123", "update_contact", "ObjectsAgent", {"id": "2"})
    audits = get_recent_audits("123")
    assert len(audits) == 2
    assert audits[0]["action"] == "create_contact"
    assert audits[1]["action"] == "update_contact"
    assert "timestamp" in audits[0]
    assert "informing_sources" in audits[0]
    assert audits[0]["informing_sources"] == []


def test_get_recent_audits_limit(tmp_path, monkeypatch):
    from pathlib import Path
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    for i in range(5):
        log_write("123", f"action_{i}", "Agent", {})
    audits = get_recent_audits("123", limit=3)
    assert len(audits) == 3
    assert audits[-1]["action"] == "action_4"


def test_log_with_informing_sources(tmp_path, monkeypatch):
    from pathlib import Path
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    sources = [
        {"source": "official", "trust_tier": "official", "title": "Docs", "url": "https://developers.hubspot.com/docs"},
    ]
    log_write("123", "delete_contact", "ObjectsAgent", {"id": "3"}, informing_sources=sources)
    audits = get_recent_audits("123")
    assert len(audits) == 1
    assert audits[0]["informing_sources"] == sources


def test_get_recent_audits_empty(tmp_path, monkeypatch):
    from pathlib import Path
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert get_recent_audits("123") == []
