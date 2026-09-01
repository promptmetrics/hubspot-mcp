from __future__ import annotations

from collections.abc import Callable

from hubspot_mcp.agents._base import AgentPrompt, build_agent_prompt
from hubspot_mcp.agents.account_info import get_account_info_agent_prompt
from hubspot_mcp.agents.analytics import get_analytics_agent_prompt
from hubspot_mcp.agents.appointments import get_appointments_agent_prompt
from hubspot_mcp.agents.associations import get_associations_agent_prompt
from hubspot_mcp.agents.audit_logs import get_audit_logs_agent_prompt
from hubspot_mcp.agents.carts import get_carts_agent_prompt
from hubspot_mcp.agents.commerce import get_commerce_agent_prompt
from hubspot_mcp.agents.communications import get_communications_agent_prompt
from hubspot_mcp.agents.courses import get_courses_agent_prompt
from hubspot_mcp.agents.custom_objects import get_custom_objects_agent_prompt
from hubspot_mcp.agents.data import get_data_agent_prompt
from hubspot_mcp.agents.deal_splits import get_deal_splits_agent_prompt
from hubspot_mcp.agents.discounts import get_discounts_agent_prompt
from hubspot_mcp.agents.email_events import get_email_events_agent_prompt
from hubspot_mcp.agents.engagements import get_engagements_agent_prompt
from hubspot_mcp.agents.fees import get_fees_agent_prompt
from hubspot_mcp.agents.forecasts import get_forecasts_agent_prompt
from hubspot_mcp.agents.forms import get_forms_agent_prompt
from hubspot_mcp.agents.goals import get_goals_agent_prompt
from hubspot_mcp.agents.hygiene import get_hygiene_agent_prompt
from hubspot_mcp.agents.invoices import get_invoices_agent_prompt
from hubspot_mcp.agents.leads import get_leads_agent_prompt
from hubspot_mcp.agents.listings import get_listings_agent_prompt
from hubspot_mcp.agents.lists import get_lists_agent_prompt
from hubspot_mcp.agents.object_library import get_object_library_agent_prompt
from hubspot_mcp.agents.objects import get_objects_agent_prompt
from hubspot_mcp.agents.orders import get_orders_agent_prompt
from hubspot_mcp.agents.pipelines import get_pipelines_agent_prompt
from hubspot_mcp.agents.projects import get_projects_agent_prompt
from hubspot_mcp.agents.properties import get_properties_agent_prompt
from hubspot_mcp.agents.quotes import get_quotes_agent_prompt
from hubspot_mcp.agents.raw_api import get_raw_api_agent_prompt
from hubspot_mcp.agents.scheduler import get_scheduler_agent_prompt
from hubspot_mcp.agents.security_history import get_security_history_agent_prompt
from hubspot_mcp.agents.sequences import get_sequences_agent_prompt
from hubspot_mcp.agents.service import get_service_agent_prompt
from hubspot_mcp.agents.services import get_services_agent_prompt
from hubspot_mcp.agents.subscriptions import get_subscriptions_agent_prompt
from hubspot_mcp.agents.taxes import get_taxes_agent_prompt
from hubspot_mcp.agents.timeline_events import get_timeline_events_agent_prompt
from hubspot_mcp.agents.triage import get_triage_agent_prompt
from hubspot_mcp.agents.users import get_users_agent_prompt
from hubspot_mcp.agents.verify import get_verify_agent_prompt
from hubspot_mcp.agents.workflows import get_workflows_agent_prompt

_AGENT_REGISTRY: dict[str, Callable[..., AgentPrompt]] = {
    "objects": get_objects_agent_prompt,
    "properties": get_properties_agent_prompt,
    "workflows": get_workflows_agent_prompt,
    "lists": get_lists_agent_prompt,
    "pipelines": get_pipelines_agent_prompt,
    "users": get_users_agent_prompt,
    "hygiene": get_hygiene_agent_prompt,
    "analytics": get_analytics_agent_prompt,
    "associations": get_associations_agent_prompt,
    "engagements": get_engagements_agent_prompt,
    "custom_objects": get_custom_objects_agent_prompt,
    "service": get_service_agent_prompt,
    "raw_api": get_raw_api_agent_prompt,
    "forms": get_forms_agent_prompt,
    "data": get_data_agent_prompt,
    "commerce": get_commerce_agent_prompt,
    "carts": get_carts_agent_prompt,
    "orders": get_orders_agent_prompt,
    "quotes": get_quotes_agent_prompt,
    "subscriptions": get_subscriptions_agent_prompt,
    "invoices": get_invoices_agent_prompt,
    "deal_splits": get_deal_splits_agent_prompt,
    "discounts": get_discounts_agent_prompt,
    "fees": get_fees_agent_prompt,
    "taxes": get_taxes_agent_prompt,
    "goals": get_goals_agent_prompt,
    "appointments": get_appointments_agent_prompt,
    "courses": get_courses_agent_prompt,
    "listings": get_listings_agent_prompt,
    "services": get_services_agent_prompt,
    "communications": get_communications_agent_prompt,
    "leads": get_leads_agent_prompt,
    "projects": get_projects_agent_prompt,
    "object_library": get_object_library_agent_prompt,
    "sequences": get_sequences_agent_prompt,
    "scheduler": get_scheduler_agent_prompt,
    "account_info": get_account_info_agent_prompt,
    "audit_logs": get_audit_logs_agent_prompt,
    "security_history": get_security_history_agent_prompt,
    "email_events": get_email_events_agent_prompt,
    "forecasts": get_forecasts_agent_prompt,
    "timeline_events": get_timeline_events_agent_prompt,
    "triage": get_triage_agent_prompt,
    "verify": get_verify_agent_prompt,
}

# Category taxonomy for visual grouping and CLI organization
_AGENT_CATEGORIES: dict[str, tuple[str, str]] = {
    # Core CRM
    "objects": ("Core CRM", "🧩"),
    "properties": ("Core CRM", "🧩"),
    "custom_objects": ("Core CRM", "🧩"),
    "associations": ("Core CRM", "🧩"),
    "lists": ("Core CRM", "📋"),
    # Automation
    "workflows": ("Automation", "⚙️"),
    "pipelines": ("Automation", "⚙️"),
    "sequences": ("Automation", "⚙️"),
    "scheduler": ("Automation", "⚙️"),
    # Engagement
    "engagements": ("Engagement", "💬"),
    "communications": ("Engagement", "💬"),
    "leads": ("Engagement", "💬"),
    # Users
    "users": ("Users & Access", "👤"),
    # Analytics & Data
    "analytics": ("Analytics & Data", "📊"),
    "hygiene": ("Analytics & Data", "📊"),
    "forecasts": ("Analytics & Data", "📊"),
    "data": ("Analytics & Data", "📊"),
    # Service Hub
    "service": ("Service Hub", "🛎️"),
    "forms": ("Service Hub", "🛎️"),
    # Commerce
    "commerce": ("Commerce", "🛒"),
    "carts": ("Commerce", "🛒"),
    "orders": ("Commerce", "🛒"),
    "quotes": ("Commerce", "🛒"),
    "subscriptions": ("Commerce", "🛒"),
    "invoices": ("Commerce", "🛒"),
    "deal_splits": ("Commerce", "🛒"),
    "discounts": ("Commerce", "🛒"),
    "fees": ("Commerce", "🛒"),
    "taxes": ("Commerce", "🛒"),
    "services": ("Commerce", "🛒"),
    # Content & Projects
    "projects": ("Content & Projects", "📝"),
    "object_library": ("Content & Projects", "📝"),
    "courses": ("Content & Projects", "📝"),
    "listings": ("Content & Projects", "📝"),
    "appointments": ("Content & Projects", "📝"),
    "goals": ("Content & Projects", "📝"),
    # System & Audit
    "raw_api": ("System & Audit", "🔒"),
    "account_info": ("System & Audit", "🔒"),
    "audit_logs": ("System & Audit", "🔒"),
    "security_history": ("System & Audit", "🔒"),
    "email_events": ("System & Audit", "🔒"),
    "timeline_events": ("System & Audit", "🔒"),
    "triage": ("Loop", "🔁"),
    "verify": ("Loop", "✅"),
}


# Routing terms for the keyword fast-path router (`orchestrator.route_request`).
# One entry per registry key. Terms are DOMAIN NOUNS / synonyms a user says to
# mean this agent — never generic CRUD verbs (find/search/get/create/update/
# delete/merge/list/show/...), which are intent signals scored separately in
# `route_request`. `triage`/`verify` are orchestration-internal and intentionally
# unroutable (empty terms). Keeping this centralized (Option A) mirrors
# `_AGENT_CATEGORIES` and makes cross-agent term collisions visible in one place.
# A registry-coverage test asserts every key here matches `_AGENT_REGISTRY`.
_AGENT_ROUTE_TERMS: dict[str, list[str]] = {
    "objects": ["contact", "contacts", "company", "companies", "deal", "deals", "ticket", "tickets", "record", "records"],
    "properties": ["property", "properties", "custom property", "field", "field type", "property schema", "property group"],
    "workflows": ["workflow", "workflows", "automation", "blueprint", "blueprints"],
    "lists": ["list", "lists", "static list", "dynamic list", "active list", "membership"],
    "pipelines": ["pipeline", "pipelines", "stage", "stages", "deal stage", "ticket pipeline"],
    "users": ["user", "users", "role", "roles", "team", "teams", "permission", "permissions"],
    "hygiene": ["duplicate", "duplicates", "deduplicate", "clean up", "stale", "data quality"],
    "analytics": ["analytics", "report", "reports", "dashboard", "dashboards", "metric", "metrics", "funnel", "conversion"],
    "associations": ["association", "associations", "association schema", "relationship", "relationships"],
    "engagements": ["engagement", "engagements", "note", "notes", "task", "tasks", "email", "emails", "meeting", "meetings", "call", "calls", "activity"],
    "custom_objects": ["custom object", "custom objects", "custom record", "custom records", "custom schema"],
    "service": ["knowledge base", "kb article", "feedback survey", "service automation", "customer service"],
    "raw_api": ["raw api", "endpoint", "http request", "api call", "direct api", "curl", "hubspot api"],
    "forms": ["form", "forms", "form field", "form submission", "form builder"],
    "data": ["sync", "data import", "data export", "import status", "data sync"],
    "commerce": ["payment", "payments", "refund", "refunds", "transaction", "checkout"],
    "carts": ["cart", "carts", "basket", "shopping cart"],
    "orders": ["order", "orders", "purchase order"],
    "quotes": ["quote", "quotes", "quote template"],
    "subscriptions": ["subscription", "subscriptions", "recurring billing", "billing cycle"],
    "invoices": ["invoice", "invoices", "accounts receivable"],
    "deal_splits": ["deal split", "deal splits", "revenue split", "revenue splits"],
    "discounts": ["discount", "discounts", "discount code"],
    "fees": ["fee", "fees"],
    "taxes": ["tax", "taxes", "tax rate"],
    "goals": ["goal", "goals", "goal target", "goal targets", "quota"],
    "appointments": ["appointment", "appointments"],
    "courses": ["course", "courses", "training course"],
    "listings": ["listing", "listings"],
    "services": ["service product", "productized service", "service line item"],
    "communications": ["communication", "communications", "whatsapp", "linkedin", "sms"],
    "leads": ["lead", "leads", "lead scoring"],
    "projects": ["project", "projects"],
    "object_library": ["object library", "object schema", "object catalog", "available object"],
    "sequences": ["sequence", "sequences", "enrollment"],
    "scheduler": ["meeting link", "meeting links", "availability", "booking page", "scheduler"],
    "account_info": ["account info", "account information", "portal info", "api usage", "account metadata"],
    "audit_logs": ["audit log", "audit logs", "audit event", "audit trail"],
    "security_history": ["security history", "security event", "security events", "login history", "password change"],
    "email_events": ["email event", "email events", "campaign", "campaigns", "email open", "email click"],
    "forecasts": ["forecast", "forecasts", "projection", "projections", "pipeline forecast"],
    "timeline_events": ["timeline event", "timeline events", "timeline event template", "timeline event token"],
    "triage": [],
    "verify": [],
}


def get_agent_category(agent_name: str) -> str:
    return _AGENT_CATEGORIES.get(agent_name, ("Unknown", "❓"))[0]


def get_agent_emoji(agent_name: str) -> str:
    return _AGENT_CATEGORIES.get(agent_name, ("Unknown", "❓"))[1]


def group_agents_by_category(agent_names: list[str]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for name in agent_names:
        category = get_agent_category(name)
        groups.setdefault(category, []).append(name)
    return dict(sorted(groups.items()))


def get_agent_prompt(agent_name: str, portal_config=None) -> AgentPrompt | None:
    builder = _AGENT_REGISTRY.get(agent_name)
    if builder is None:
        return None
    return builder(portal_config)


def list_agent_names() -> list[str]:
    return list(_AGENT_REGISTRY.keys())


__all__ = [
    "AgentPrompt",
    "build_agent_prompt",
    "get_agent_prompt",
    "list_agent_names",
    "_AGENT_REGISTRY",
    "get_agent_category",
    "get_agent_emoji",
    "group_agents_by_category",
    "_AGENT_CATEGORIES",
    "_AGENT_ROUTE_TERMS",
]
