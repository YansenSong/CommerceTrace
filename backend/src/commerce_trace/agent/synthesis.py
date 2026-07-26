from __future__ import annotations

import re
from datetime import date, timedelta

from ..contracts import Evidence

EVIDENCE_REFERENCE_RE = re.compile(r"\[(ev_[A-Za-z0-9_-]+)\]")
CHART_MARKDOWN_REFERENCE_RE = re.compile(
    r"!\[[^\]\n]*\]\(\s*chart_[A-Za-z0-9_-]+\s*\)"
)
MONTH_RE = re.compile(r"(\d{4})-(\d{2})")
INCOMPLETE_REASON_MESSAGES = {
    "insufficient_evidence": (
        "当前没有获得足够的可执行查询证据，暂时无法形成可靠结论。"
        "请补充时间范围或明确需要分析的指标。"
    ),
    "business_sql_limit": (
        "已达到本轮查询次数上限，当前结果可能不完整。"
        "如需继续，请缩小分析范围后重试。"
    ),
    "tool_iteration_limit": (
        "已达到本轮工具调用上限，当前结果可能不完整。"
        "请缩小问题范围后重试。"
    ),
    "sql_retry_limit": (
        "查询在多次修正后仍未成功，暂时无法补充更多证据。"
        "请调整问题或检查数据字段。"
    ),
}


def incomplete_reason_message(incomplete_reason: str | None) -> str:
    if incomplete_reason is None:
        return ""
    return INCOMPLETE_REASON_MESSAGES.get(
        incomplete_reason,
        "本轮分析未能完整完成，请调整问题后重试。",
    )


def synthesize(
    question: str,
    evidence: list[Evidence],
    llm_content: str,
    incomplete_reason: str | None,
) -> str:
    model_answer = CHART_MARKDOWN_REFERENCE_RE.sub("", llm_content).strip()
    model_answer = re.sub(r"\n{3,}", "\n\n", model_answer)
    if not evidence:
        friendly_reason = incomplete_reason_message(incomplete_reason)
        base = model_answer or friendly_reason or "当前没有足够的查询证据形成定量结论。"
        if model_answer and friendly_reason:
            base += f"\n\n{friendly_reason}"
        return base

    coverage_gap = temporal_coverage_gap_conclusion(question, evidence)
    if coverage_gap is not None:
        answer = coverage_gap
    elif model_answer and model_answer != "已根据工具结果完成分析。":
        allowed_ids = {item.evidence_id for item in evidence}
        answer = EVIDENCE_REFERENCE_RE.sub(
            lambda match: match.group(0) if match.group(1) in allowed_ids else "",
            model_answer,
        ).strip()
        if not answer.startswith("结论"):
            answer = f"结论：{answer}"
    else:
        answer = f"结论：{evidence[0].claim}。"

    sections = [answer]
    if coverage_gap is not None:
        cited = [item for item in evidence if f"[{item.evidence_id}]" in answer]
        if cited:
            sections.append(
                "证据："
                + "\n"
                + "\n".join(f"- {item.claim} [{item.evidence_id}]" for item in cited)
            )
    else:
        missing = [item for item in evidence if f"[{item.evidence_id}]" not in answer]
        if missing:
            heading = "补充证据：" if "证据：" in answer else "证据："
            sections.append(
                heading
                + "\n"
                + "\n".join(f"- {item.claim} [{item.evidence_id}]" for item in missing)
            )
    if "口径说明：" not in answer:
        sections.append("口径说明：以上结论仅基于当前数据库覆盖范围和本次已执行查询。")
    if incomplete_reason and incomplete_reason != "data_coverage_gap":
        sections.append(incomplete_reason_message(incomplete_reason))
    return "\n\n".join(sections)


def temporal_coverage_gap_conclusion(
    question: str,
    evidence: list[Evidence],
) -> str | None:
    if "上个月" not in question:
        return None

    target_month: str | None = None
    target_evidence_id: str | None = None
    minimum: str | None = None
    maximum: str | None = None
    coverage_evidence_id: str | None = None
    zero_result = False

    for item in evidence:
        for row in item.preview:
            raw_target = row.get("last_month")
            if isinstance(raw_target, str) and MONTH_RE.fullmatch(raw_target):
                target_month = raw_target
                target_evidence_id = item.evidence_id
            raw_minimum = row.get("min_date")
            raw_maximum = row.get("max_date")
            if (
                isinstance(raw_minimum, str)
                and isinstance(raw_maximum, str)
                and MONTH_RE.match(raw_minimum)
                and MONTH_RE.match(raw_maximum)
            ):
                minimum = raw_minimum
                maximum = raw_maximum
                coverage_evidence_id = item.evidence_id
            raw_count = row.get("order_count")
            if isinstance(raw_count, (int, float)) and raw_count == 0:
                zero_result = True

    if target_month is None:
        first_of_this_month = date.today().replace(day=1)
        target_month = (first_of_this_month - timedelta(days=1)).strftime("%Y-%m")
    if minimum is None or maximum is None or coverage_evidence_id is None:
        return None

    minimum_month_match = MONTH_RE.match(minimum)
    maximum_month_match = MONTH_RE.match(maximum)
    assert minimum_month_match is not None
    assert maximum_month_match is not None
    minimum_month = minimum_month_match.group(0)
    maximum_month = maximum_month_match.group(0)
    if minimum_month <= target_month <= maximum_month:
        return None

    year, month = target_month.split("-")
    target_label = f"{year}年{int(month)}月"
    minimum_label = minimum.split("T", 1)[0]
    maximum_label = maximum.split("T", 1)[0]
    metric = "订单总量" if "订单" in question else "目标指标"
    citations = [f"[{coverage_evidence_id}]"]
    if target_evidence_id and target_evidence_id != coverage_evidence_id:
        citations.append(f"[{target_evidence_id}]")
    conclusion = (
        f"结论：当前数据无法回答上个月（{target_label}）的{metric}，"
        f"因为数据库只覆盖 {minimum_label} 至 {maximum_label}。"
    )
    if zero_result:
        conclusion += (
            f"查询得到 0 仅表示数据集中没有 {target_label} 的记录，"
            f"不能说明实际{metric}为 0。"
        )
    return conclusion + " " + " ".join(citations)
