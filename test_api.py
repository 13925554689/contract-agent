# -*- coding: utf-8 -*-
"""E2E API测试 — 覆盖全部端点 + 前后端匹配"""
import json, io, urllib.request, urllib.error

BASE = "http://127.0.0.1:5198"
results = []

def call(method, path, data=None, is_json=True):
    url = BASE + urllib.request.quote(path, safe="/?=&%")
    headers = {}
    body = None
    if data is not None:
        if is_json:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        else:
            body = data
            headers["Content-Type"] = "multipart/form-data; boundary=BOUNDARY"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            raw = r.read().decode("utf-8", "replace")
            try:
                return r.status, json.loads(raw)
            except Exception:
                return r.status, {"raw": raw[:100]}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw[:200]}

def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), name, detail)

# 1. 健康检查
s, d = call("GET", "/api/health")
check("health", s == 200 and d.get("status") == "ok", f"llm={d.get('llm_configured')} regs={d.get('reg_count')}")

# 2. 首页
s, d = call("GET", "/", is_json=False)
check("index page", s == 200)

# 3. 法规搜索(中文query)
s, d = call("GET", "/api/regulations/search?q=违约")
check("reg search", s == 200 and d.get("count", 0) > 0, f"count={d.get('count')}")

# 4. 法规列表
s, d = call("GET", "/api/regulations/list")
check("reg list", s == 200 and d.get("count", 0) >= 10, f"count={d.get('count')}")

# 5. 生成合同(LLM)
s, d = call("POST", "/api/generate", {"type": "服务合同", "requirements": "软件开发服务，金额50万元，工期6个月", "buyer": "甲方科技", "seller": "乙方软件"})
check("generate", s == 200 and d.get("success") and len(d.get("generated_text", "")) > 200, f"len={len(d.get('generated_text',''))}")

# 6. 闭环生成+审核
s, d = call("POST", "/api/generate-and-review", {"type": "采购合同", "requirements": "采购服务器20台，金额100万元，要求验收合格后付款", "export": True, "max_iterations": 2})
check("generate-and-review", s == 200 and d.get("success") and d.get("iterations", 0) >= 1, f"iter={d.get('iterations')} docx={d.get('docx_path')}")

# 7. 下载docx
docx_path = d.get("docx_path") or ""
if docx_path:
    fn = docx_path.replace("\\", "/").split("/")[-1]
    s2, d2 = call("GET", f"/api/download/{fn}")
    check("download docx", s2 == 200, f"fn={fn}")
else:
    check("download docx", False, "no docx_path")

# 8. 上传审核(txt)
body = ("--BOUNDARY\r\nContent-Disposition: form-data; name=\"industry\"\r\n\r\n通用\r\n"
        "--BOUNDARY\r\nContent-Disposition: form-data; name=\"file\"; filename=\"test_contract.txt\"\r\n"
        "Content-Type: text/plain\r\n\r\n").encode("utf-8")
with io.open("test_contract.txt", "rb") as f:
    body += f.read() + b"\r\n--BOUNDARY--\r\n"
s, d = call("POST", "/api/review", body, is_json=False)
check("review upload", s == 200 and d.get("success") and "report" in d, f"overall={d.get('report',{}).get('overall_risk')}")

# 9. 上传非法扩展名 → 应400
body2 = ("--BOUNDARY\r\nContent-Disposition: form-data; name=\"file\"; filename=\"evil.exe\"\r\n"
         "Content-Type: application/octet-stream\r\n\r\nMZ\r\n--BOUNDARY--\r\n").encode()
s, d = call("POST", "/api/review", body2, is_json=False)
check("reject exe upload", s == 400)

# 10. 路径穿越 → 应400/404
s, d = call("GET", "/api/download/..%2f..%2fconfig.py")
check("block path traversal", s in (400, 404), f"status={s}")

# 11. 缺参数 → 400
s, d = call("POST", "/api/generate", {"type": ""})
check("generate missing type ->400", s == 400)

# 12. 清理
s, d = call("POST", "/api/cleanup")
check("cleanup", s == 200 and d.get("success"))

fails = [r for r in results if not r[1]]
print(f"\n===== {len(results) - len(fails)}/{len(results)} PASSED =====")
for name, ok, det in fails:
    print("FAIL:", name, det)
