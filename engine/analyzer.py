"""风险分析引擎 - 规则库 + LLM混合判断"""
import re
import json
import hashlib
import urllib.request
import urllib.error
from engine.reg_db import search_regulations, search_cases
from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL

_CACHE = {}  # ponytail: 进程内报告缓存(同文本+同参数免重复LLM计费)，重启清空；满了直接清

_POSITION_GUIDE = {
    "甲方": "我方为甲方（付款/采购/发包方）：重点防范付款后无法获得合格交付、验收标准缺失、质保与售后不足；对付款节奏、验收条件、质保条款从严把关，争取对我方有利的管辖与争议解决安排。",
    "乙方": "我方为乙方（交付/销售/承包方）：重点防范回款拖延、验收标准模糊导致无限返工、赔偿责任无上限；对付款节点、验收期限、责任上限从严把关。",
    "中立": "中立立场：不偏向任何一方，识别显失公平、权责不对等的条款。",
}

_RISK_RULES = [
    (r'违约金.*?(?<!不)(?<!得)(?:超过|高于|超出).*?(?:[总總].*?[金金额]|[合約约].*?[价價].*?款).*?(\d+)\s*%', '高风险', '违约金比例过高',
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


def analyze_risks(parsed: dict, industry: str = "通用", position: str = "中立") -> dict:
    text = parsed["raw_text"]
    clauses = parsed["clauses"]
    metadata = parsed["metadata"]

    position = position if position in _POSITION_GUIDE else "中立"

    # ponytail: 报告缓存 key=内容哈希+参数，同合同重复审核秒回且省LLM费
    # (key必须在position归一化之后算，否则非法position会生成与中立内容相同但键不同的冗余条目)
    cache_key = hashlib.sha256(
        (text + "|" + industry + "|" + position).encode("utf-8")
    ).hexdigest()
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    rule_risks = _match_rules(text, clauses)
    struct_risks = _check_structure(clauses)
    related_regs = _find_relevant_regs(text, industry)

    llm_risks = []
    if LLM_API_KEY:
        try:
            llm_risks = _llm_analyze(text, industry, metadata, related_regs, position)
        except Exception as e:
            # ponytail: LLM失败降级规则引擎，但必须留痕可诊断
            print(f"[analyzer] LLM分析失败,降级规则引擎: {e}")

    all_risks = rule_risks + struct_risks + llm_risks
    all_risks = _deduplicate(all_risks)

    high = sum(1 for r in all_risks if r["level"] == "高风险")
    mid = sum(1 for r in all_risks if r["level"] == "中风险")
    low = sum(1 for r in all_risks if r["level"] == "低风险")
    tips = sum(1 for r in all_risks if r["level"] == "提示")

    suggestions = _generate_suggestions(all_risks)

    # 判例要点：按命中风险名关联（不足时补全量前3条）
    case_hits = search_cases([r["name"] for r in all_risks], limit=5)
    if not case_hits:
        case_hits = search_cases(None, limit=3)
    report = {
        "contract_info": metadata,
        "industry": industry,
        "position": position,
        "risk_summary": {"total": len(all_risks), "high": high, "mid": mid, "low": low, "tip": tips},
        "risks": all_risks,
        "suggestions": suggestions,
        "negotiation_points": _generate_negotiation_points(all_risks),
        "related_regulations": related_regs,
        "related_cases": case_hits,
        "overall_risk": "高风险" if high > 2 else ("中风险" if high > 0 or mid > 3 else "低风险"),
        "analyzed_by": "LLM+规则引擎" if llm_risks else "规则引擎"
    }
    if len(_CACHE) >= 100:
        _CACHE.clear()
    _CACHE[cache_key] = report
    return report


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


def _llm_analyze(text: str, industry: str, metadata: dict, related_regs: list, position: str = "中立") -> list:
    reg_context = "\n".join(
        f"- {r.get('title', '')}：{str(r.get('content', ''))[:200]}"
        for r in (related_regs or [])
    ) or "（法规库无匹配）"

    prompt = f"""你是资深合同审核专家，仅适用中华人民共和国法律（不得引入美国法概念如完整协议/可分割性条款）。请分析以下{industry}行业合同，识别风险并给出修改建议。

{_POSITION_GUIDE.get(position, _POSITION_GUIDE['中立'])}

合同信息：
- 甲乙方：{metadata.get('party_a','未知')} / {metadata.get('party_b','未知')}
- 金额：{metadata.get('contract_amount','未知')}

可引用的法规库（reason字段只能引用以下法规，不得虚构法条；库中无依据的须在reason末尾标注[待核实]）：
{reg_context}

合同内容（前6000字）：
{text[:6000]}

请以JSON对象输出风险点（最多8条），格式：
{{"risks": [{{"level":"高风险/中风险/低风险/提示","name":"风险名称","description":"风险说明","suggestion":"修改建议","reason":"法律依据"}}]}}

只输出JSON。如果没有风险返回 {{"risks": []}}。"""

    data = json.dumps({
        "model": LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3, "max_tokens": 2000,
        "response_format": {"type": "json_object"}
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
    except urllib.error.HTTPError as e:
        # ponytail: 部分OpenAI兼容端点不支持response_format→去掉重试一次
        if e.code == 400:
            try:
                data2 = json.loads(data)
                data2.pop("response_format", None)
                req2 = urllib.request.Request(
                    f"{LLM_BASE_URL.rstrip('/')}/chat/completions",
                    data=json.dumps(data2).encode(),
                    headers={"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"}
                )
                with urllib.request.urlopen(req2, timeout=60) as resp:
                    content = json.loads(resp.read())["choices"][0]["message"]["content"]
                print("[analyzer] 端点不支持json模式,已降级为普通模式")
            except Exception as e2:
                print(f"[analyzer] LLM调用失败: {e2}")
                return []
        else:
            print(f"[analyzer] LLM HTTP错误: {e.code}")
            return []
    except (urllib.error.URLError, json.JSONDecodeError, KeyError, TimeoutError) as e:
        print(f"[analyzer] LLM调用失败: {e}")
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
        parsed = json.loads(content)
    except json.JSONDecodeError:
        parsed = None
        json_match = re.search(r'\{.*\}|\[.*\]', content, re.DOTALL)
        if json_match:
            try:
                parsed = json.loads(json_match.group())
            except json.JSONDecodeError:
                return []

    # 兼容两种形态: {"risks": [...]} (json模式) / [...] (降级正则提取)
    if isinstance(parsed, dict):
        parsed = parsed.get("risks", [])
    llm_risks = parsed
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


def _generate_negotiation_points(risks: list) -> list:
    """每个风险生成一句可直接使用的谈判话术（对标 lumos 合同审查skill）"""
    templates = {
        "违约金比例过高": "关于违约金条款：我方理解需要违约约束，但当前上限明显超出实际损失预期，建议调整为合同总金额的20%以内，或按实际损失据实计算。",
        "赔偿责任无限制": "关于赔偿责任范围：'一切损失'的表述双方都无法预估风险敞口，建议改为'以本合同总金额为限，且不包括间接损失和预期利益损失'。",
        "责任免除过宽": "关于免责条款：完全免除一方责任显失公平，也不会得到法院支持，建议双方各让一步，仅免除一般过失情形。",
        "付款周期过长": "关于付款周期：当前账期超出《保障中小企业款项支付条例》60日上限，建议改为验收合格后30日内支付。",
        "保密期限无限制": "关于保密期限：无限期保密义务对双方都是负担，建议改为合同终止后3年内有效，商业秘密除外。",
        "管辖地不利于我方": "关于争议管辖：在对方所在地诉讼我方维权成本过高，建议改为我方所在地法院，或折中约定合同签订地/仲裁。",
        "单方任意解除权": "关于解除权：当前仅一方可随时解除而另一方被动，建议改为双方均可提前30日书面通知解除，且需补偿对方已投入成本。",
        "知识产权归属不利": "关于知识产权：我方投入了主要开发/创作成本，建议改为双方共有，或我方保留背景知识产权并授权对方使用。",
        "赔偿责任上限低": "关于赔偿上限：当前上限无法覆盖我方实际投入，建议至少提高到合同总金额，重大过失除外。",
        "试用期过长": "关于试用期：6个月上限需以3年以上固定期限合同为前提，且试用期工资不得低于转正的80%，建议调整期限并同步调薪。",
        "竞业限制期限": "关于竞业限制：2年为法定上限，且期间须按月支付补偿金，请确认补偿标准已写入条款。",
        "口头协议风险": "关于合同形式：建议补充'本合同的变更与补充均须以书面形式作出，口头约定无效'条款。",
        "仅约定仲裁": "关于争议解决：仅约定仲裁排除了诉讼救济，请确认仲裁机构名称准确唯一，或改为'或裁或审'由我方选择。",
        "付款与验收脱钩": "关于付款与验收：建议将付款节点与验收里程碑绑定，验收合格作为付款前提，保护双方权益。",
    }
    points = []
    for r in risks:
        if r["level"] == "提示":
            continue
        tpl = templates.get(r["name"])
        if tpl:
            points.append(tpl)
    return points


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
