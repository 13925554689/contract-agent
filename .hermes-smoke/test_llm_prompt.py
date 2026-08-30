"""Self-check: _llm_analyze 注入法规库 + 反幻觉指令 (无网络, stub urlopen)"""
import json
import urllib.request
from engine.analyzer import _llm_analyze

captured = {}

def fake_urlopen(req, timeout=None):
    captured["body"] = json.loads(req.data)
    return _FakeResp(json.dumps({
        "choices": [{"message": {"content": json.dumps([
            {"level": "高风险", "name": "测试", "description": "d",
             "suggestion": "s", "reason": "《民法典》第470条"}
        ])}}]
    }).encode())

class _FakeResp:
    def __init__(self, data): self._data = data
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self): return self._data

urllib.request.urlopen = fake_urlopen

regs = [{"title": "《民法典》合同编", "content": "合同订立、效力、违约责任。违约金过高可请求调整。"},
        {"title": "《劳动合同法》", "content": "试用期不得超过6个月。"}]

risks = _llm_analyze("直播场地合作协议，无费用条款", "通用",
                     {"party_a": "甲", "party_b": "乙", "contract_amount": "0"}, regs)

prompt = captured["body"]["messages"][0]["content"]
assert "《民法典》合同编" in prompt and "《劳动合同法》" in prompt, "法规未注入 prompt"
assert "[待核实]" in prompt, "反幻觉指令缺失"
assert "中华人民共和国法律" in prompt, "法系锚定缺失"
assert len(risks) == 1 and risks[0]["source"] == "LLM分析", "LLM 结果解析失败"
print("PASS: 法规注入 + 反幻觉指令 + 法系锚定 全部生效, prompt 长度", len(prompt))
