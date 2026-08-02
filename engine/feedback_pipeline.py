"""审核反馈管道 — 提取审核结果→构建生成增强prompt
ponytail: ~60行，无新依赖，无新DB。纯函数式管道。
"""
import json


def extract_feedback(review_report: dict) -> dict:
    """从审核报告中提取可反哺到生成侧的结构化反馈

    返回: {issues: [{clause, problem, fix}], high_count, mid_count, overall}
    """
    issues = []
    for risk in review_report.get("risks", []):
        issues.append({
            "clause": risk.get("name", "未知条款"),
            "problem": risk.get("description", ""),
            "fix": risk.get("suggestion", risk.get("name", "")),
            "level": risk.get("level", "提示"),
        })

    summary = review_report.get("risk_summary", {})
    return {
        "issues": issues,
        "high_count": summary.get("high", 0),
        "mid_count": summary.get("mid", 0),
        "total_issues": summary.get("total", 0),
        "overall": review_report.get("overall_risk", "未知"),
        "suggestions": review_report.get("suggestions", []),
    }


def build_feedback_prompt(feedback: dict) -> str:
    """将审核反馈构建为重新生成时的prompt注入块

    只在有实际风险时返回有效prompt，无风险返回空字符串调用方跳过。
    """
    issues = feedback.get("issues", [])
    if not issues:
        return ""

    high_items = [i for i in issues if i["level"] == "高风险"]
    mid_items = [i for i in issues if i["level"] == "中风险"]
    other_items = [i for i in issues if i["level"] not in ("高风险", "中风险")]

    parts = ["\n\n【预算审核反馈 — 请在下一次生成时修正以下问题】\n"]

    if high_items:
        parts.append("🔴 高风险项（必须修正）：")
        for i, item in enumerate(high_items, 1):
            parts.append(f"{i}. 条款「{item['clause']}」存在严重问题：{item['problem']}")
            parts.append(f"   修正要求：{item['fix']}")
        parts.append("")

    if mid_items:
        parts.append("🟡 中风险项（建议修正）：")
        for i, item in enumerate(mid_items, 1):
            parts.append(f"{i}. 条款「{item['clause']}」：{item['problem']}")
            parts.append(f"   建议：{item['fix']}")
        parts.append("")

    if other_items:
        parts.append("⚪ 其他提示：")
        for i, item in enumerate(other_items, 1):
            parts.append(f"{i}. {item['clause']}：{item['fix']}")

    parts.append("\n请综合以上反馈，重新生成一份规避了所有上述问题的合同。")
    return "\n".join(parts)


def build_avoidance_checklist(feedback: dict) -> str:
    """从多次反馈积累的避坑检查单（纯文本，可追加到生成prompt）

    用作跨会话持久化：每次审核完把高频问题追加到 checklist 文件。
    ponytail: 最简单持久化——写入 data/checklist.txt，生成时读入注入。
    """
    issues = feedback.get("issues", [])
    if not issues:
        return ""
    lines = []
    for i in issues:
        lines.append(f"- 避免：{i['clause']}（{i['problem'][:80]}）→ {i['fix'][:80]}")
    return "\n".join(lines)
