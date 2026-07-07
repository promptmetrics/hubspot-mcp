from __future__ import annotations

from typing import Any

from hubspot_mcp.blueprints.workflows import WorkflowBlueprint, register_blueprint


def _build(params: dict[str, Any]) -> dict[str, Any]:
    stage = params.get("stage", "qualifiedtobuy")
    task_title = params.get("task_title", "Follow up on deal stage change")
    due_days = params.get("due_days", 1)
    return {
        "ui_path": "Settings > Automation > Workflows > Create workflow",
        "object_type": "Deal-based",
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
                                "property": "dealstage",
                                "operation": {
                                    "operationType": "ENUMERATION",
                                    "operator": "IS_ANY_OF",
                                    "values": [stage],
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
                "ui_action": "Create task",
                "fields": {
                    "Title": task_title,
                    "Due date": f"{{timestamp + {due_days}d}}",
                    "Assigned to": "{{deal.hubspot_owner_id}}",
                    "Priority": "Medium",
                    "Notes": f"Deal moved to stage: {stage}",
                },
            }
        ],
        "prerequisites": [],
        "validation": ["Move a deal to the target stage", "Verify task is created"],
    }


register_blueprint(
    WorkflowBlueprint(
        name="deal_stage_task",
        description="Create a follow-up task when a deal moves to a specific stage.",
        tags=["deal", "task", "pipeline"],
        parameter_schema={
            "name": {"type": "string", "default": "Deal Stage Task", "description": "Workflow name"},
            "stage": {"type": "string", "default": "qualifiedtobuy", "description": "Deal stage internal ID to trigger on"},
            "task_title": {"type": "string", "default": "Follow up on deal stage change", "description": "Task title"},
            "due_days": {"type": "integer", "default": 1, "description": "Days until task is due"},
        },
        build=_build,
    )
)
