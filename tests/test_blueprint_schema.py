import pytest

from hubspot_mcp.blueprints.workflows.schema import (
    BlueprintFile,
    is_raw_action,
    render_spec,
    substitute_params,
    validate_blueprint,
)


def _minimal(**overrides):
    base = {
        "name": "demo",
        "description": "a demo blueprint",
        "tags": ["x"],
        "parameters": {
            "subject": {"type": "string", "default": "Hi", "description": "subj"},
            "delay_hours": {"type": "integer", "default": 0, "description": "delay"},
        },
        "spec": {
            "ui_path": "Settings > Automation > Workflows",
            "object_type": "Contact-based",
            "enrollment": {"type": "EVENT_BASED", "trigger": "Contact is created"},
            "actions": [
                {
                    "ui_action": "Delay",
                    "fields": {"Delay for": "{{param:delay_hours}} hours"},
                    "include_if": "delay_hours",
                },
                {
                    "ui_action": "Send internal email notification",
                    "fields": {"Subject": "{{param:subject}}", "Body": "{{contact.firstname}}"},
                },
            ],
            "prerequisites": [],
            "validation": ["Create a test contact"],
        },
    }
    base.update(overrides)
    return base


# --- substitution ---------------------------------------------------------


class TestSubstituteParams:
    def test_param_replaced(self):
        out = substitute_params({"a": "{{param:subject}}"}, {"subject": "Hello!"})
        assert out == {"a": "Hello!"}

    def test_hubspot_tokens_untouched(self):
        out = substitute_params({"a": "{{contact.firstname}}"}, {"subject": "Hi"})
        assert out == {"a": "{{contact.firstname}}"}

    def test_timestamp_token_untouched(self):
        out = substitute_params({"a": "{{timestamp + 5d}}"}, {})
        assert out == {"a": "{{timestamp + 5d}}"}

    def test_embedded_interpolation(self):
        out = substitute_params({"a": "{{param:delay_hours}} hours"}, {"delay_hours": 2})
        assert out == {"a": "2 hours"}

    def test_exact_match_stringifies_int(self):
        # exact-match int -> str() (legacy _build parity); include_if still sees raw int
        out = substitute_params({"a": "{{param:increment}}"}, {"increment": 5})
        assert out["a"] == "5"
        assert isinstance(out["a"], str)

    def test_exact_match_preserves_list(self):
        out = substitute_params({"a": "{{param:values}}"}, {"values": ["x", "y"]})
        assert out == {"a": ["x", "y"]}

    def test_exact_match_preserves_bool(self):
        out = substitute_params({"a": "{{param:flag}}"}, {"flag": True})
        assert out["a"] is True

    def test_missing_param_raises_with_path(self):
        with pytest.raises(ValueError, match="Missing parameter 'subject'"):
            substitute_params({"a": "{{param:subject}}"}, {})

    def test_deep_walk_nested(self):
        node = {"enrollment": {"filters": [{"value": "{{param:stage}}"}]}}
        out = substitute_params(node, {"stage": "closedwon"})
        assert out["enrollment"]["filters"][0]["value"] == "closedwon"


# --- include_if + renumbering --------------------------------------------


class TestIncludeIf:
    def test_falsy_drops_action_and_renumbers(self):
        bf = validate_blueprint(_minimal())
        out = render_spec(bf, {"delay_hours": 0})
        assert [a["ui_action"] for a in out["actions"]] == ["Send internal email notification"]
        assert out["actions"][0]["step"] == 1

    def test_truthy_keeps_action_and_renumbers(self):
        bf = validate_blueprint(_minimal())
        out = render_spec(bf, {"delay_hours": 2})
        assert [a["ui_action"] for a in out["actions"]] == ["Delay", "Send internal email notification"]
        assert out["actions"][0]["step"] == 1
        assert out["actions"][1]["step"] == 2

    def test_defaults_apply_when_param_omitted(self):
        bf = validate_blueprint(_minimal())
        out = render_spec(bf, {})
        assert out["actions"][0]["fields"]["Subject"] == "Hi"


class TestRenderSpecShape:
    def test_keys_match_converter_contract(self):
        bf = validate_blueprint(_minimal())
        out = render_spec(bf, {})
        assert set(out.keys()) == {
            "ui_path",
            "object_type",
            "enrollment",
            "actions",
            "prerequisites",
            "validation",
        }

    def test_name_not_emitted(self):
        # the calling tool injects name; render_spec must not (legacy behavior)
        bf = validate_blueprint(_minimal())
        assert "name" not in render_spec(bf, {})

    def test_nested_true_branch_has_no_step(self):
        data = _minimal()
        data["spec"]["actions"].append(
            {
                "ui_action": "If/then branch",
                "fields": {"Property": "x", "Value": "{{param:subject}}"},
                "true_branch": [
                    {"ui_action": "Set property value", "fields": {"Property": "p", "Value": "v"}},
                ],
            }
        )
        bf = validate_blueprint(data)
        out = render_spec(bf, {})
        branch = out["actions"][-1]
        assert "step" in branch
        assert "step" not in branch["true_branch"][0]


# --- validation -----------------------------------------------------------


class TestValidateBlueprint:
    def test_valid_returns_file(self):
        bf = validate_blueprint(_minimal())
        assert isinstance(bf, BlueprintFile)
        assert bf.name == "demo"

    def test_missing_required_field_lists_path(self):
        data = _minimal()
        del data["name"]
        with pytest.raises(ValueError, match="name"):
            validate_blueprint(data)

    def test_ui_action_missing_ui_action_raises(self):
        data = _minimal()
        data["spec"]["actions"].append({"fields": {"x": "y"}})  # no ui_action
        with pytest.raises(ValueError, match="missing 'ui_action'"):
            validate_blueprint(data)

    def test_raw_action_without_action_type_id_raises(self):
        data = _minimal()
        data["spec"]["actions"].append({"raw": True, "node": {}})
        with pytest.raises(ValueError, match="action_type_id"):
            validate_blueprint(data)

    def test_raw_action_valid(self):
        data = _minimal()
        data["spec"]["actions"].append(
            {"raw": True, "action_type_id": "0-63809083", "node": {"customField": 1}, "note": "third-party"}
        )
        bf = validate_blueprint(data)
        assert is_raw_action(bf.spec.actions[-1])

    def test_raw_action_passes_through_render(self):
        data = _minimal()
        data["spec"]["actions"].append(
            {"raw": True, "action_type_id": "0-63809083", "node": {"customField": 1}, "note": "x"}
        )
        bf = validate_blueprint(data)
        out = render_spec(bf, {})
        raw = out["actions"][-1]
        assert raw["raw"] is True
        assert raw["action_type_id"] == "0-63809083"
        assert raw["node"] == {"customField": 1}
        assert "step" in raw  # raw actions get renumbered into the sequence


# --- integration with the existing converter ------------------------------


class TestConverterIntegration:
    def test_render_feeds_converter(self):
        from hubspot_mcp.blueprints.workflows.converter import blueprint_to_v4_payload

        bf = validate_blueprint(_minimal())
        spec = render_spec(bf, {"delay_hours": 1})
        spec["name"] = "Demo"  # tool injects name
        payload = blueprint_to_v4_payload(spec)
        assert payload["flowType"] == "WORKFLOW"
        assert payload["objectTypeId"] == "0-1"
        assert payload["isEnabled"] is False