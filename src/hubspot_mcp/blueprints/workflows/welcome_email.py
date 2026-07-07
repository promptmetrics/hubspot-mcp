from __future__ import annotations

from typing import Any

from hubspot_mcp.blueprints.workflows import WorkflowBlueprint, register_blueprint


def _build(params: dict[str, Any]) -> dict[str, Any]:
    delay_hours = params.get("delay_hours", 0)
    subject = params.get("subject", "Welcome!")
    body = params.get("body", "Thanks for joining us.")
    actions: list[dict[str, Any]] = []
    if delay_hours:
        actions.append(
            {
                "step": 1,
                "ui_action": "Delay",
                "fields": {"Delay for": f"{delay_hours} hours"},
            }
        )
    actions.append(
        {
            "step": 2 if delay_hours else 1,
            "ui_action": "Send internal email notification",
            "fields": {"Subject": subject, "Body": body},
        }
    )
    return {
        "ui_path": "Settings > Automation > Workflows > Create workflow",
        "object_type": "Contact-based",
        "enrollment": {
            "type": "EVENT_BASED",
            "trigger": "Contact is created",
        },
        "actions": actions,
        "prerequisites": [],
        "validation": ["Create a test contact", "Verify email is sent"],
    }


register_blueprint(
    WorkflowBlueprint(
        name="welcome_email",
        description="Send a welcome email to new contacts after an optional delay.",
        tags=["email", "onboarding", "contact"],
        parameter_schema={
            "name": {"type": "string", "default": "Welcome Email", "description": "Workflow name"},
            "delay_hours": {"type": "integer", "default": 0, "description": "Delay before sending email"},
            "subject": {"type": "string", "default": "Welcome!", "description": "Email subject line"},
            "body": {"type": "string", "default": "Thanks for joining us.", "description": "Email body"},
        },
        build=_build,
    )
)
