from __future__ import annotations

from typing import Any

from hubspot_mcp.blueprints.workflows import WorkflowBlueprint, register_blueprint


def _build(params: dict[str, Any]) -> dict[str, Any]:
    inactive_days = params.get("inactive_days", 90)
    subject = params.get("subject", "We miss you!")
    body = params.get("body", "It's been a while — here's what's new.")
    return {
        "ui_path": "Settings > Automation > Workflows > Create workflow",
        "object_type": "Contact-based",
        "enrollment": {
            "type": "PROPERTY_BASED",
            "filter_branch": {
                "filterBranchType": "OR",
                "filterBranches": [
                    {
                        "filterBranchType": "AND",
                        "filters": [
                            {
                                "filterType": "PROPERTY",
                                "property": "last_engagement_date",
                                "operation": {
                                    "operationType": "ALL_PROPERTY",
                                    "operator": "IS_KNOWN",
                                    "includeObjectsWithNoValueSet": False,
                                },
                            }
                        ],
                    }
                ],
            },
        },
        "actions": [
            {
                "step": 1,
                "ui_action": "Delay",
                "fields": {"Delay for": f"{inactive_days} days"},
            },
            {
                "step": 2,
                "ui_action": "Send internal email notification",
                "fields": {"Subject": subject, "Body": body},
            },
        ],
        "prerequisites": [
            "Contact property 'last_engagement_date' is active",
        ],
        "validation": ["Create a test contact with last_engagement_date set", "Verify email sent after delay"],
    }


register_blueprint(
    WorkflowBlueprint(
        name="re_engagement",
        description="Send a re-engagement email to contacts who have been inactive for a specified number of days.",
        tags=["email", "contact", "retention"],
        parameter_schema={
            "name": {"type": "string", "default": "Re-engagement Campaign", "description": "Workflow name"},
            "inactive_days": {"type": "integer", "default": 90, "description": "Days of inactivity before email is sent"},
            "subject": {"type": "string", "default": "We miss you!", "description": "Email subject line"},
            "body": {"type": "string", "default": "It's been a while — here's what's new.", "description": "Email body"},
        },
        build=_build,
    )
)
