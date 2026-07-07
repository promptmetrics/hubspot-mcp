from __future__ import annotations

from typing import Any
from urllib.parse import quote

from hubspot_mcp.client import HubSpotClient
from hubspot_mcp.errors import HubSpotError, RateLimitError, ScopeError
from hubspot_mcp.tools import tool


@tool(name="hubspot_get_knowledge_base_article", description="Retrieve a HubSpot knowledge base article by ID.")
async def hubspot_get_knowledge_base_article(
    article_id: str,
    client: HubSpotClient,
    portal_id: str,
) -> dict[str, Any]:
    try:
        resp = await client.get(
            f"/knowledge-base/v3/articles/{quote(article_id, safe='')}",
            portal_id=portal_id,
            expected_scopes=["content.knowledge-base.read"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_get_knowledge_base_article"}


@tool(name="hubspot_list_kb_articles", description="List HubSpot knowledge base articles with optional filtering.")
async def hubspot_list_kb_articles(
    client: HubSpotClient,
    portal_id: str,
    category: str | None = None,
    status: str | None = None,
    limit: int = 10,
    after: str | None = None,
) -> dict[str, Any]:
    query = f"?limit={limit}"
    if category:
        query += f"&category={quote(category, safe='')}"
    if status:
        query += f"&status={quote(status, safe='')}"
    if after:
        query += f"&after={quote(after, safe='')}"
    try:
        resp = await client.get(
            f"/knowledge-base/v3/articles{query}",
            portal_id=portal_id,
            expected_scopes=["content.knowledge-base.read"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_list_kb_articles"}


@tool(name="hubspot_get_ticket_pipeline", description="Retrieve a ticket pipeline by ID with its stages.")
async def hubspot_get_ticket_pipeline(
    pipeline_id: str,
    client: HubSpotClient,
    portal_id: str,
) -> dict[str, Any]:
    try:
        resp = await client.get(
            f"/crm/v3/pipelines/tickets/{quote(pipeline_id, safe='')}",
            portal_id=portal_id,
            expected_scopes=["crm.pipelines.orders.read"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_get_ticket_pipeline"}


@tool(name="hubspot_create_ticket_pipeline", description="Create a new ticket pipeline with stages.")
async def hubspot_create_ticket_pipeline(
    label: str,
    display_order: int,
    stages: list[dict[str, Any]],
    client: HubSpotClient,
    portal_id: str,
) -> dict[str, Any]:
    try:
        resp = await client.post(
            "/crm/v3/pipelines/tickets",
            portal_id=portal_id,
            body={"label": label, "displayOrder": display_order, "stages": stages},
            expected_scopes=["crm.pipelines.orders.write"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_create_ticket_pipeline"}


@tool(name="hubspot_list_service_automation", description="List service automation rules (workflows scoped to service objects).")
async def hubspot_list_service_automation(
    client: HubSpotClient,
    portal_id: str,
    limit: int = 10,
    after: str | None = None,
) -> dict[str, Any]:
    query = f"?limit={limit}"
    if after:
        query += f"&after={quote(after, safe='')}"
    try:
        resp = await client.get(
            f"/automation/v4/workflows{query}",
            portal_id=portal_id,
            expected_scopes=["automation"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_list_service_automation"}


@tool(name="hubspot_get_feedback_survey", description="Retrieve a customer feedback survey by ID.")
async def hubspot_get_feedback_survey(
    survey_id: str,
    client: HubSpotClient,
    portal_id: str,
) -> dict[str, Any]:
    try:
        resp = await client.get(
            f"/feedback/v1/surveys/{quote(survey_id, safe='')}",
            portal_id=portal_id,
            expected_scopes=["feedback.surveys.read"],
        )
        return resp.body
    except (HubSpotError, RateLimitError, ScopeError) as exc:
        return {"error": str(exc), "tool": "hubspot_get_feedback_survey"}
