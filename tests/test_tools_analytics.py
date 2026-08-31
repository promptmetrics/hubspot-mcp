import pytest

from hubspot_mcp.tools.analytics import hubspot_calculate_metrics, hubspot_pipeline_velocity


@pytest.mark.asyncio
async def test_hubspot_calculate_metrics():
    data = [
        {"dealstage": "closedwon", "amount": 1000},
        {"dealstage": "closedlost", "amount": 500},
        {"dealstage": "appointmentscheduled", "amount": 200},
    ]
    result = await hubspot_calculate_metrics(data)
    assert result["conversion_rate"] == 33.33
    assert result["average_deal_size"] == 566.67
    assert result["win_rate"] == 50.0


@pytest.mark.asyncio
async def test_hubspot_pipeline_velocity():
    deals = [
        {"stage_history": [
            {"stage_id": "a", "entered_at": "2024-01-01T00:00:00Z"},
            {"stage_id": "b", "entered_at": "2024-01-11T00:00:00Z"},
        ]}
    ]
    result = await hubspot_pipeline_velocity(deals)
    assert result["velocity_by_stage"]["a"] == 10.0
