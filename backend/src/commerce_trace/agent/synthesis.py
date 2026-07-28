from __future__ import annotations

import re

from ..models import Evidence

EVIDENCE_REFERENCE_RE = re.compile(r"\[(ev_[A-Za-z0-9_-]+)\]")
CHART_MARKDOWN_REFERENCE_RE = re.compile(r"!\[[^\]\n]*\]\(\s*chart_[A-Za-z0-9_-]+\s*\)")
INCOMPLETE_REASON_MESSAGES = {
    "insufficient_evidence": (
        "当前没有获得足够的可执行查询证据，暂时无法形成可靠结论。"
        "请补充时间范围或明确需要分析的指标。"
    ),
    "run_sql_limit": (
        "已达到本轮查询次数上限，当前结果可能不完整。如需继续，请缩小分析范围后重试。"
    ),
    "tool_iteration_limit": ("已达到本轮工具调用上限，当前结果可能不完整。请缩小问题范围后重试。"),
    "retry_limit": (
        "查询在多次修正后仍未成功，暂时无法补充更多证据。请调整问题或检查数据字段。"
    ),
}


def incomplete_reason_message(incomplete_reason: str | None) -> str:
    """将内部未完成原因转换为适合向用户展示的中文提示。"""

    if incomplete_reason is None:
        return ""
    return INCOMPLETE_REASON_MESSAGES.get(
        incomplete_reason,
        "本轮分析未能完整完成，请调整问题后重试。",
    )


def synthesize(
    evidence: list[Evidence],
    llm_content: str,
    incomplete_reason: str | None,
) -> str:
    """清理模型回答、校验证据引用并补齐口径与未完成说明。"""

    model_answer = CHART_MARKDOWN_REFERENCE_RE.sub("", llm_content).strip()
    model_answer = re.sub(r"\n{3,}", "\n\n", model_answer)
    if not evidence:
        friendly_reason = incomplete_reason_message(incomplete_reason)
        base = model_answer or friendly_reason or "当前没有足够的查询证据形成定量结论。"
        if model_answer and friendly_reason:
            base += f"\n\n{friendly_reason}"
        return base

    if model_answer and model_answer != "已根据工具结果完成分析。":
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
    missing = [item for item in evidence if f"[{item.evidence_id}]" not in answer]
    if missing:
        heading = "补充证据：" if "证据：" in answer else "证据："
        sections.append(
            heading + "\n" + "\n".join(f"- {item.claim} [{item.evidence_id}]" for item in missing)
        )
    if "口径说明：" not in answer:
        sections.append("口径说明：以上结论仅基于当前数据库覆盖范围和本次已执行查询。")
    if incomplete_reason:
        sections.append(incomplete_reason_message(incomplete_reason))
    return "\n\n".join(sections)
