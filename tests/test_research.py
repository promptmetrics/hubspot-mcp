"""Tests for hubspot_mcp.research module."""

from hubspot_mcp.research import RESEARCH_PROMPT_BLOCK, classify_url


def test_classify_developers_url_is_official():
    src, tier = classify_url("https://developers.hubspot.com/docs/api/crm/contacts")
    assert src == "official"
    assert tier == "official"


def test_classify_knowledge_url_is_official():
    src, tier = classify_url("https://knowledge.hubspot.com/contacts/import-contacts")
    assert src == "official"
    assert tier == "official"


def test_classify_community_url_defaults_to_unverified():
    src, tier = classify_url("https://community.hubspot.com/t5/Lists/foo/td-p/12345")
    assert src == "community"
    assert tier == "community-unverified"


def test_classify_unknown_domain_falls_back_to_community_unverified():
    src, tier = classify_url("https://random.example.com/post")
    assert src == "community"
    assert tier == "community-unverified"


def test_research_prompt_block_mentions_websearch_and_informing_sources():
    assert "WebSearch" in RESEARCH_PROMPT_BLOCK
    assert "site:developers.hubspot.com" in RESEARCH_PROMPT_BLOCK
    assert "site:community.hubspot.com" in RESEARCH_PROMPT_BLOCK
    assert "informing_sources" in RESEARCH_PROMPT_BLOCK
