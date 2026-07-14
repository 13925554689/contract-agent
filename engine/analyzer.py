"""风险分析引擎 - 规则库 + LLM混合判断"""
import re
import json
import urllib.request
import urllib.error
from engine.reg_db import search_regulations
from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL

_RISK_RULES = [
    (r'违约金.*?(?:超过|高于|超出).*?(?:[总總].*?[金金额]|[合約约].*?[价價].*?款).*?(\d+)\s*%', '高风险', '违约金比例过高',
     '违约金超过合同总金额的合理比例，根据《民法典》第585条，法院可能酌情减少'),
    (r'赔偿.*?(?:一切|所有|全部|任何).*?损失', '高风险', '赔偿责任无限制',
     '赔偿"一切/全部损失"的条款过于宽泛，建议设定合理的责任上限'),
    (r'无.*?违约.*?责任|免[除责].*?一切.*?责任', '高风险', '责任免除过宽',
     '免除一切责任的条款可能因显失公平而被认定无效'),
    (r'付[款金].*?[不未].*?超[过過].*?[9九][0零]|[123]6[0零]', '中风险', '付款周期过长',
     '根据《保障中小企业款项支付条例》，付款期限不得超过60日'),
    (r'保[密秘].*?永[久远].*?[不解].*?终[止结]', '中风险', '保密期限无限制',
     '永久保密义务可能不合理，建议设定合理期限'),
    (r'管[辖豁].*?(?:对方|[买受承乙卖]).*?所在', '中风险', '管辖地不利于我方',
     '争议管辖地在对方所在地，增加我方维权成本'),
    (r'解[除约].*?[有仅].*?[权權利].*?[随任].*?时', '高风险', '单方任意解除权',
     '对方保留任意解除权对我方极为不利，建议改为约定解除条件'),
    (r'知识产权.*?归.*?属.*?[对买甲发].*?方', '中风险', '知识产权归属不利',
     '成果知识产权归对方，建议明确共有或归我方'),
    (r'[赔補].*?上[限不].*?合[同約约].*?[总總].*?[金金额]', '中风险', '赔偿责任上限低',
     '赔偿责任上限过低可能导致实际损失无法完全获赔'),
    (r'试用.*?[6六].*?月', '中风险', '试用期过长',
     '试用期超过6个月违反《劳动合同法》第19条'),
    (r'竞业.*?限[制止].*?[2二].*?年', '提示', '竞业限制期限',
     '竞业限制期限2年为法定上限，需支付补偿'),
    (r'[未不].*?书[面].*?[合协议].*?[可即].*?生[效力]', '提示', '口头协议风险',
     '未明确书面形式可能导致证据不足'),
    (r'[争异].*?[仅只].*?仲[裁断]', '中风险', '仅约定仲裁',
     '仅约定仲裁排除司法救济，需确认仲裁机构明确且公正'),
]


def analyze_risks(parsed: dict, industry: str = "通用") -> dict:
    text = parsed["raw_text"]
    clauses = parsed["clauses"]
    metadata = parsed["metadata"]

    rule_risks = _match_rules(text, clauses)
    struct_risks = _check_structure(clauses)
    related_regs = _find_relevant_regs(text, industry)

    llm_risks = []
    if LLM_API_KEY:
        try:
            llm_risks = _llm_analyze(text, industry, metadata)
        except Exception:
            pass

    all_risks = rule_risks + struct_risks + llm_risks
    all_risks = _deduplicate(all_risks)

    high = sum(1 for r in all_risks if r["level"] == "高风险")
    mid = sum(1 for r in all_risks if r["level"] == "中风险")
    low = sum(1 for r in all_risks if r["level"] == "低风险")
    tips = sum(1 for r in all_risks if r["level"] == "提示")

    suggestions = _generate_suggestions(all_risks)

    return {
        "contract_info": metadata,
        "industry": industry,
        "risk_summary": {"total": len(all_risks), "high": high, "mid": mid, "low": low, "tip": tips},
        "risks": all_risks,
        "suggestions": suggestions,
        "related_regulations": related_regs,
        "overall_risk": "高风险" if high > 2 else ("中风险" if high > 0 or mid > 3 else "低风险"),
        "analyzed_by": "LLM+规则引擎" if llm_risks else "规则引擎"
    }


def _match_rules(text: str, clauses: list) -> list:
    risks = []
    for pattern, level, name, desc in _RISK_RULES:
        matches = re.findall(pattern, text)
        if matches:
            clause_match = ""
            for c in clauses:
                for m in matches:
                    if isinstance(m, str) and m in c["content"]:
                        clause_match = c["content"][:200]
                        break
                if clause_match:
                    break

            risks.append({
                "level": level, "name": name, "description": desc,
                "source": "规则匹配", "clause_context": clause_match or text[:200],
                "suggestion": _get_remedy(name)
            })
    return risks


def _check_structure(clauses: list) -> list:
    risks = []
    clause_types = [c["type"] for c in clauses]

    required = {
        "付款条款": "合同缺少明确的付款条款，无法确定支付条件和金额",
        "违约责任": "合同缺少违约责任条款，发生违约时无法有效追责",
        "争议解决": "合同缺少争议解决条款，建议补充管辖约定或仲裁条款",
        "交付条款": "合同缺少交付/验收条款，无法明确交付标准和验收条件",
    }
    for ctype, msg in required.items():
        if ctype not in clause_types:
            risks.append({
                "level": "中风险", "name": f"缺少{ctype}", "description": msg,
                "source": "结构分析", "clause_context": "", "suggestion": f"建议补充{ctype}"
            })

    if "付款条款" in clause_types and "交付条款" not in clause_types:
        risks.append({
            "level": "中风险", "name": "付款与验收脱钩",
            "description": "有付款条款但无验收/交付条款，付款条件可能与交付成果不匹配",
            "source": "结构分析", "clause_context": "",
            "suggestion": "建议将付款节点与验收里程碑绑定"
        })

    return risks


def _find_relevant_regs(text: str, industry: str) -> list:
    keywords = []
    for kw in [r'(?:民法典|合同编)', r'(?:保密|NDA)', r'(?:违约责任|赔偿)', r'(?:知识产权|专利)',
               r'(?:争议|仲裁|诉讼)', r'(?:付款|支付)']:
        if re.search(kw, text):
            keywords.append(kw.replace('(?:', '').split('|')[0].replace(')', ''))

    regs = search_regulations(' '.join(keywords) if keywords else industry or '合同', limit=5)
    return regs


def _llm_analyze(text: str, industry: str, metadata: dict) -> list:
    prompt = f"""你是资深合同审核专家。请分析以下{industry}行业合同，识别风险并给出修改建议。

合同信息：
- 甲乙方：{metadata.get('party_a','未知')} / {metadata.get('party_b','未知')}
- 金额：{metadata.get('contract_amount','未知')}

合同内容（前3000字）：
{text[:3000]}

请以JSON数组输出风险点（最多8条），格式：
[{{"level":"高风险/中风险/低风险/提示","name":"风险名称","description":"风险说明","suggestion":"修改建议","reason":"法律依据"}}]

只输出JSON，不要其他文字。如果没有风险返回[]。"""

    data = json.dumps({
        "model": LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3, "max_tokens": 2000
    }).encode()

    req = urllib.request.Request(
        f"{LLM_BASE_URL.rstrip('/')}/chat/completions",
        data=data,
        headers={"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"}
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read())
            content = result["choices"][0]["message"]["content"]
    except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError, KeyError, TimeoutError):
        return []

    content = content.strip()
    if content.startswith("```"):
        lines = content.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        content = "\n".join(lines)

    try:
        llm_risks = json.loads(content)
    except json.JSONDecodeError:
        json_match = re.search(r'\[.*\]', content, re.DOTALL)
        if json_match:
            try:
                llm_risks = json.loads(json_match.group())
            except json.JSONDecodeError:
                return []
        else:
            return []

    if not isinstance(llm_risks, list):
        return []

    for r in llm_risks:
        if isinstance(r, dict):
            r["source"] = "LLM分析"
    return [r for r in llm_risks if isinstance(r, dict) and "level" in r and "name" in r]


def _deduplicate(risks: list) -> list:
    seen = set()
    unique = []
    for r in risks:
        key = r["name"]
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique


def _generate_suggestions(risks: list) -> list:
    high = [r for r in risks if r["level"] == "高风险"]
    mid = [r for r in risks if r["level"] == "中风险"]

    suggestions = []
    if high:
        suggestions.append(f"【优先修改】{len(high)}个高风险条款必须在签署前修改")
        for r in high:
            suggestions.append(f"  - {r['name']}: {r['suggestion']}")
    if mid:
        suggestions.append(f"【建议修改】{len(mid)}个中风险条款建议谈判修改")
    if not high and not mid:
        suggestions.append("合同整体风险可控，建议关注提示项")

    return suggestions


def _get_remedy(risk_name: str) -> str:
    remedies = {
        "违约金比例过高": "建议将违约金比例降至合同总金额的20%以下，或与对方协商合理的违约金上限",
        "责任免除过宽": "建议限定免责范围，明确排除故意违约、重大过失等情形",
        "付款周期过长": "建议缩短付款周期至60日内，或增加逾期付款的利息条款",
        "保密期限无限制": "建议设定保密期限（如合同终止后3年），到期自动解除保密义务",
        "管辖地不利于我方": "建议争取在我方所在地法院管辖，或选择中立的仲裁机构",
        "单方任意解除权": "建议改为双方协商一致解除，或设定具体的解除条件",
        "知识产权归属不利": "建议明确知识产权共有，或约定合理的使用许可和收益分配",
        "赔偿责任上限低": "建议赔偿责任上限不低于合同总金额，或区分不同违约情形设定上限",
        "试用期过长": "建议试用期不超过6个月，且试用期工资不低于转正工资的80%",
        "竞业限制期限": "竞业限制需按月支付经济补偿（不低于离职前12个月平均工资的30%），否则无效",
        "口头协议风险": "建议明确书面形式为合同生效和变更的唯一有效方式",
        "仅约定仲裁": "建议增加诉讼选项，确保争议解决途径的充分性",
    }
    return remedies.get(risk_name, "建议与法务团队进一步讨论修改方案")
