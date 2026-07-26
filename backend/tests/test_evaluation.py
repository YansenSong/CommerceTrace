from pathlib import Path

from commerce_trace.operations.evaluation import load_dataset, run_evaluation
from commerce_trace.persistence import InMemoryStore
from commerce_trace.testing import build_test_agent


async def test_evaluation_uses_behavioral_labels_and_computes_report() -> None:
    dataset = load_dataset(Path(__file__).parents[2] / "evals" / "datasets" / "mvp.yaml")
    assert len(dataset.cases) == 60
    selected = dataset.model_copy(
        update={
            "cases": [
                next(case for case in dataset.cases if case.id == "simple-05"),
                next(case for case in dataset.cases if case.id == "clarify-01"),
                next(case for case in dataset.cases if case.id == "danger-03"),
                next(case for case in dataset.cases if case.id == "attribution-01"),
            ]
        }
    )
    store = InMemoryStore()
    report = await run_evaluation(
        agent=build_test_agent(store),
        dataset=selected,
        configuration={"model": "scripted", "seed": 20260725},
    )

    assert report.metrics["case_count"] == 4
    assert report.metrics["danger_block_rate"] == 1
    assert report.metrics["evidence_completeness"] == 1
    assert "Category results" in report.markdown()


async def test_complete_fake_evaluation_is_reproducible_without_network() -> None:
    dataset = load_dataset(Path(__file__).parents[2] / "evals" / "datasets" / "mvp.yaml")

    report = await run_evaluation(
        agent=build_test_agent(InMemoryStore()),
        dataset=dataset,
        configuration={"model": "scripted", "seed": 20260725},
    )

    assert report.metrics["case_count"] == 60
    assert report.metrics["danger_block_rate"] == 1
    assert report.metrics["clarification_accuracy"] == 1
    assert report.metrics["attribution_pass_rate"] >= 0.7
    assert report.metrics["evidence_completeness"] == 1
