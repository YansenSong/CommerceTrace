import pytest

from commerce_trace.contracts import ToolFailure, ToolSuccess
from commerce_trace.tools import ToolExecutionContext, VisualizeDataTool


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
    context = ToolExecutionContext(
        user_id="user",
        conversation_id="conversation",
        request_id="request",
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
        VisualizeDataTool.args_model(
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
    context = ToolExecutionContext(
        user_id="user",
        conversation_id="conversation",
        request_id="request",
    )
    missing = await tool.execute(
        context,
        tool.args_model(
            evidence_id="another-request",
            chart_type="bar",
            title="无权图表",
            x="region",
            y="revenue",
        ),
    )

    assert isinstance(missing, ToolFailure)
    assert missing.safe_error_code == "evidence_not_found"
