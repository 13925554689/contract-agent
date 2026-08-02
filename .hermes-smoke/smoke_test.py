# -*- coding: utf-8 -*-
"""合同生成审核智能体 - 后端全接口冒烟测试"""
import json, os, urllib.request, urllib.error

BASE = "http://127.0.0.1:5198"
PASS, FAIL = 0, 0
results = []

def req(method, path, data=None, headers=None, raw=None):
    url = BASE + path
    body = None
    hdrs = dict(headers or {})
    if data is not None:
        body = json.dumps(data).encode()
        hdrs["Content-Type"] = "application/json"
    if raw is not None:
        body = raw
    r = urllib.request.Request(url, data=body, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)

def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1; results.append(f"PASS  {name}")
    else:
        FAIL += 1; results.append(f"FAIL  {name}  {detail}")

def jload(body):
    try: return json.loads(body)
    except Exception: return {}

# 1. 首页
s, body, _ = req("GET", "/")
text = body.decode("utf-8", "ignore")
check("GET / → 200", s == 200 and "合同审核" in text and "合同生成" in text and "法规库" in text, f"status={s}")

# 2. 健康检查
s, body, _ = req("GET", "/api/health")
d = jload(body)
check("GET /api/health → ok", s == 200 and d.get("status") == "ok" and d.get("reg_count") == 8, f"{d}")

# 3. 法规列表
s, body, _ = req("GET", "/api/regulations/list")
d = jload(body)
check("GET /api/regulations/list → 8条", s == 200 and d.get("count") == 8, f"count={d.get('count')}")
keys_ = list(d["results"][0].keys()) if d.get("results") else ["none"]
check("法规字段完整", bool(d.get("results")) and all(k in d["results"][0] for k in ("title","category","content","tags")), f"{keys_}")

# 4. 法规搜索
s, body, _ = req("GET", "/api/regulations/search?q=" + urllib.parse.quote("违约金"))
d = jload(body)
check("GET /api/regulations/search?q=违约金", s == 200 and d.get("count", 0) > 0, f"count={d.get('count')}")

# 5. 空搜索 → 400
s, body, _ = req("GET", "/api/regulations/search?q=")
check("空搜索 → 400", s == 400, f"status={s}")

# 6. FTS特殊字符不崩溃
for q in ["AND OR NOT", "付款*", '(")']:
    s, body, _ = req("GET", "/api/regulations/search?q=" + urllib.parse.quote(q))
    check(f"FTS特殊字符 '{q}' 不崩溃", s in (200, 400), f"status={s}")

# 7. 法规刷新
s, body, _ = req("POST", "/api/regulations/refresh")
d = jload(body)
check("POST /api/regulations/refresh", s == 200 and d.get("success") and d.get("reg_count") == 8, f"{d}")

# 8. 合同审核 (txt)
with open("test_contract.txt", "rb") as f:
    raw = f.read()
boundary = "----testboundary7MA4YWxk"
parts = []
parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"test_contract.txt\"\r\nContent-Type: text/plain\r\n\r\n".encode() + raw + b"\r\n")
parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"industry\"\r\n\r\n通用\r\n".encode())
parts.append(f"--{boundary}--\r\n".encode())
body_bytes = b"".join(parts)
s, body, _ = req("POST", "/api/review", raw=body_bytes, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
d = jload(body)
check("POST /api/review (txt) → success", s == 200 and d.get("success"), f"status={s} {d.get('error','')}")
if d.get("success"):
    rep = d["report"]
    check("报告结构完整", all(k in rep for k in ("contract_info","risk_summary","risks","suggestions","related_regulations","overall_risk")), f"keys={list(rep.keys())}")
    check("风险汇总四维", all(k in rep["risk_summary"] for k in ("high","mid","low","tip")), f"{rep['risk_summary']}")
    check("风险项有level/name/description", all(all(k in r for k in ("level","name","description")) for r in rep["risks"]), f"risks={len(rep['risks'])}")

# 9. 审核无文件 → 400
s, body, _ = req("POST", "/api/review", raw=b"", headers={"Content-Type": "multipart/form-data; boundary=x"})
check("审核无文件 → 400", s == 400, f"status={s}")

# 10. 审核非法扩展名 → 400
parts = [f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"evil.exe\"\r\nContent-Type: application/octet-stream\r\n\r\n".encode() + b"MZ\x90\x00" + b"\r\n", f"--{boundary}--\r\n".encode()]
s, body, _ = req("POST", "/api/review", raw=b"".join(parts), headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
d = jload(body)
check("审核非法扩展名 → 400", s == 400, f"status={s} {d.get('error','')}")

# 11. 合同生成 (模板降级)
s, body, _ = req("POST", "/api/generate", data={"type": "采购合同", "requirements": "采购100台服务器，3年维保，预付30%+验收60%+质保10%，交货30天"})
d = jload(body)
check("POST /api/generate → success", s == 200 and d.get("success") and d.get("generated_text"), f"status={s} {d.get('error','')}")
if d.get("success"):
    check("生成文本含条款", "违约责任" in d["generated_text"] and "争议解决" in d["generated_text"], f"word_count={d.get('word_count')}")
    check("llm_available=false 标记", d.get("llm_available") is False, f"{d.get('llm_available')}")

# 12. 生成缺type → 400
s, body, _ = req("POST", "/api/generate", data={"requirements": "x"})
check("生成缺type → 400", s == 400, f"status={s}")

# 13. 生成非法type → 500
s, body, _ = req("POST", "/api/generate", data={"type": "不存在合同", "requirements": "x"})
d = jload(body)
check("生成非法type → 500带supported", s == 500 and "supported" in d, f"status={s} {d.get('error','')}")

# 14. 生成+导出DOCX
s, body, _ = req("POST", "/api/generate", data={"type": "服务合同", "requirements": "提供SaaS服务1年，7x24支持", "buyer": "甲方科技", "seller": "乙方信息", "export": True})
d = jload(body)
docx_path = d.get("docx_path", "")
check("生成+导出 → docx_path", s == 200 and docx_path, f"{d.get('error','')}")
if docx_path:
    fname = os.path.basename(docx_path)
    s2, body2, h2 = req("GET", f"/api/download/{urllib.parse.quote(fname)}")
    check("下载DOCX → 200", s2 == 200 and b"PK" in body2[:4], f"status={s2} len={len(body2)}")

# 15. 路径穿越下载 → 400或404(路由层拦截也算安全)
s, body, _ = req("GET", "/api/download/..%2F..%2Fconfig.py")
check("路径穿越下载 → 被拦截", s in (400, 404), f"status={s}")

# 16. 闭环管道 generate-and-review
s, body, _ = req("POST", "/api/generate-and-review", data={"type": "采购合同", "requirements": "采购100台服务器", "export": False})
d = jload(body)
check("POST /api/generate-and-review → success", s == 200 and d.get("success"), f"status={s} {d.get('error','')}")
if d.get("success"):
    check("闭环history≥1轮", d.get("iterations", 0) >= 1 and len(d.get("history", [])) >= 1, f"iterations={d.get('iterations')}")
    check("闭环risk_summary在history", "risk_summary" in d["history"][0] and "issues" in d["history"][0], f"{list(d['history'][0].keys())}")

# 17. cleanup
s, body, _ = req("POST", "/api/cleanup")
d = jload(body)
check("POST /api/cleanup → success", s == 200 and d.get("success") and "removed" in d, f"{d}")

# 18. 404
s, body, _ = req("GET", "/api/nonexistent")
check("未知API → 404", s == 404, f"status={s}")

print(f"\n===== 后端冒烟结果: PASS={PASS} FAIL={FAIL} =====")
for r in results: print(r)
print(f"\n===== 失败详情 =====")
for r in results:
    if r.startswith("FAIL"): print(" -", r)
