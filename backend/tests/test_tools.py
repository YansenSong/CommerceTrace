import pytest

from commerce_trace.agent.tools import (
    ToolContext,
    ToolRegistry,
    VisualizeDataArgs,
    VisualizeDataTool,
)
from commerce_trace.contracts import ToolFailure, ToolSuccess


@pytest.mark.parametrize(
    ("chart_type", "arguments"),
    [
        ("metric_card", {"value": "revenue"}),
        ("bar", {"x": "region", "y": "revenue"}),
        ("line", {"x": "region", "y": "revenue"}),
        ("pie", {"x": "region", "value": "revenue"}),
    ],
)
async def test_visualize_data_supports_only_controlled_plotly_contracts(
    chart_type: str,
    arguments: dict[str, str],
) -> None:
    context = ToolContext(
        query_results={
            "result-1": {
                "evidence_id": "ev-1",
                "columns": ["region", "revenue"],
                "preview": [{"region": "华东", "revenue": 100}],
            }
        },
    )

    result = await VisualizeDataTool().execute(
        context,
        VisualizeDataArgs(
            evidence_id="ev-1",
            chart_type=chart_type,
            title="经营结果",
            **arguments,
        ),
    )

    assert isinstance(result, ToolSuccess)
    assert result.data["chart"]["chart_type"] == chart_type


async def test_visualize_data_rejects_missing_evidence_and_fields() -> None:
    tool = VisualizeDataTool()
    context = ToolContext()
    missing = await tool.execute(
        context,
        VisualizeDataArgs(
            evidence_id="another-request",
            chart_type="bar",
            title="无权图表",
            x="region",
            y="revenue",
        ),
    )

    assert isinstance(missing, ToolFailure)
    assert missing.safe_error_code == "evidence_not_found"


async def test_registry_owns_registration_and_argument_validation() -> None:
    registry = ToolRegistry()
    registry.register(VisualizeDataTool())

    with pytest.raises(ValueError, match="duplicate tool: visualize_data"):
        registry.register(VisualizeDataTool())

    result = await registry.execute(
        "visualize_data",
        {"chart_type": "bar"},
        ToolContext(),
    )

    assert isinstance(result, ToolFailure)
    assert result.safe_error_code == "invalid_tool_arguments"
