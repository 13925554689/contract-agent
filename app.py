"""合同生成与审核智能体 - Flask Web应用"""
import os
import re
import json
import time
import glob
from flask import Flask, request, jsonify, render_template, send_file
from werkzeug.utils import secure_filename

from config import UPLOAD_DIR, INDUSTRIES, CONTRACT_TYPES
from engine.reg_db import init_db, seed_regulations, search_regulations, list_regulations
from engine.parser import parse_contract
from engine.analyzer import analyze_risks
from engine.generator import generate_contract, export_docx, append_checklist
from engine.feedback_pipeline import extract_feedback, build_feedback_prompt, build_avoidance_checklist

ALLOWED_EXTENSIONS = {'.pdf', '.docx', '.doc', '.txt'}
ALLOWED_MIME_PREFIXES = {
    '.pdf': ('application/pdf',),
    '.docx': ('application/vnd.openxmlformats-officedocument.wordprocessingml.document',),
    '.doc': ('application/msword',),
    '.txt': ('text/plain',),
}

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
os.makedirs(UPLOAD_DIR, exist_ok=True)

init_db()
seed_regulations()


def _is_safe_filename(filename):
    if not filename or '..' in filename or '/' in filename or '\\' in filename:
        return False
    return bool(re.match(r'^[\w\u4e00-\u9fff.\-()（）\s]+$', filename))


def _allowed_file(filename):
    ext = os.path.splitext(filename)[1].lower()
    return ext in ALLOWED_EXTENSIONS


# ponytail: text→clauses helper for in-memory generated text (no file needed)
def _text_to_clauses(text: str) -> list:
    """将生成的合同文本拆分为条款列表，复用 parser._classify_clause"""
    from engine.parser import _classify_clause, _clean_text
    text = _clean_text(text)
    clauses = []
    # 按常见条款标识拆分
    pattern = r'(?:(?:第[一二三四五六七八九十百千0-9]+[条節节章]|(?:一|二|三|四|五|六|七|八|九|十)[、，,.])\s*|(?:^|\n)\s*\d+[\.、．]\s+)'
    parts = re.split(pattern, text)
    if len(parts) <= 2:
        paras = [p.strip() for p in text.split('\n\n') if len(p.strip()) > 10]
        for i, para in enumerate(paras):
            clauses.append({
                "index": i + 1, "type": _classify_clause(para),
                "content": para[:1000], "word_count": len(para)
            })
    else:
        clause_idx = 0
        for part in parts:
            part = part.strip()
            if len(part) < 10:
                continue
            clause_idx += 1
            clauses.append({
                "index": clause_idx, "type": _classify_clause(part),
                "content": part[:1000], "word_count": len(part)
            })
    return clauses


@app.route('/')
def index():
    return render_template('index.html', industries=INDUSTRIES, contract_types=CONTRACT_TYPES)


@app.route('/api/review', methods=['POST'])
def review_contract():
    if 'file' not in request.files:
        return jsonify({"error": "请上传合同文件"}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "文件名为空"}), 400

    if not _allowed_file(file.filename):
        return jsonify({"error": "不支持的文件格式，仅允许 PDF/DOCX/TXT"}), 400

    industry = request.form.get('industry', '通用')

    safe_name = secure_filename(file.filename)
    if not safe_name:
        safe_name = f"{int(time.time())}.txt"
    else:
        safe_name = f"{int(time.time())}_{safe_name}"
    filepath = os.path.join(UPLOAD_DIR, safe_name)

    real_upload = os.path.realpath(UPLOAD_DIR)
    real_filepath = os.path.realpath(filepath)
    if not real_filepath.startswith(real_upload + os.sep) and real_filepath != real_upload:
        return jsonify({"error": "非法文件路径"}), 400

    file.save(filepath)

    try:
        parsed = parse_contract(filepath)
        report = analyze_risks(parsed, industry)
        return jsonify({"success": True, "report": report, "filename": file.filename})

    except Exception as e:
        return jsonify({"error": str(e), "stage": "分析失败"}), 500
    finally:
        try:
            os.remove(filepath)
        except OSError:
            pass


@app.route('/api/generate', methods=['POST'])
def gen_contract():
    data = request.get_json()
    if not data:
        return jsonify({"error": "请提供请求数据"}), 400

    contract_type = data.get('type', '')
    requirements = data.get('requirements', '')
    buyer = data.get('buyer', '')
    seller = data.get('seller', '')
    export = data.get('export', False)

    if not contract_type:
        return jsonify({"error": "请选择合同类型"}), 400
    if not requirements:
        return jsonify({"error": "请输入合同要求"}), 400

    result = generate_contract(contract_type, requirements, buyer, seller)

    if "error" in result:
        return jsonify(result), 500

    docx_path = ""
    if export:
        docx_path = export_docx(result["generated_text"], contract_type)
        result["docx_path"] = docx_path

    return jsonify({"success": True, **result})


@app.route('/api/generate-and-review', methods=['POST'])
def generate_and_review():
    """闭环管道：生成→审核→反馈→重新生成（最多2轮迭代）

    ponytail: 整个闭环 ~70行，无新依赖，无新DB。复用现有 analyze_risks + generate_contract。
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "请提供请求数据"}), 400

    contract_type = data.get('type', '')
    requirements = data.get('requirements', '')
    buyer = data.get('buyer', '')
    seller = data.get('seller', '')
    export = data.get('export', False)
    max_iterations = min(data.get('max_iterations', 2), 3)

    if not contract_type or not requirements:
        return jsonify({"error": "缺少 type 或 requirements"}), 400

    history = []
    feedback_text = ""
    final_text = ""
    final_docx = ""

    for iteration in range(1, max_iterations + 1):
        result = generate_contract(
            contract_type, requirements, buyer, seller,
            review_feedback=feedback_text, load_checklist=True
        )
        if "error" in result:
            return jsonify(result), 500

        gen_text = result["generated_text"]

        # 审核：直接构造 parsed dict（在内存中，不走文件解析）
        parsed = {
            "raw_text": gen_text,
            "clauses": _text_to_clauses(gen_text),
            "word_count": len(gen_text),
            "metadata": {"title": contract_type, "party_a": buyer, "party_b": seller},
        }

        report = analyze_risks(parsed, "通用")

        # 提取反馈
        feedback = extract_feedback(report)
        total_issues = feedback.get("total_issues", 0)

        history.append({
            "iteration": iteration,
            "word_count": len(gen_text),
            "risk_summary": report.get("risk_summary", {}),
            "overall_risk": report.get("overall_risk", "未知"),
            "issues": feedback.get("issues", []),
        })

        if total_issues == 0 or iteration == max_iterations:
            # 最终轮：积累避坑清单到持久化文件
            checklist = build_avoidance_checklist(feedback)
            append_checklist(checklist)

            final_text = gen_text
            if export:
                final_docx = export_docx(final_text, contract_type)
            break

        # 下一轮注入反馈
        feedback_text = build_feedback_prompt(feedback)

    return jsonify({
        "success": True,
        "contract_type": contract_type,
        "generated_text": final_text,
        "word_count": len(final_text),
        "history": history,
        "iterations": len(history),
        "docx_path": final_docx,
        "disclaimer": "本合同时由AI审核-反馈-重新生成闭环生成，仅供参考。正式签署前请由专业律师审核。"
    })


@app.route('/api/download/<filename>')
def download_file(filename):
    # 服务端生成的文件名含中文(如 服务合同_20260802.docx)，secure_filename会剥掉中文，
    # 这里用_is_safe_filename校验：允许中文但拦截 ../ 路径穿越
    if not _is_safe_filename(filename):
        return jsonify({"error": "非法文件名"}), 400

    path = os.path.join(UPLOAD_DIR, filename)
    real_upload = os.path.realpath(UPLOAD_DIR)
    real_path = os.path.realpath(path)
    if not real_path.startswith(real_upload + os.sep):
        return jsonify({"error": "非法文件路径"}), 400

    if os.path.exists(real_path):
        return send_file(real_path, as_attachment=True, download_name=filename)
    return jsonify({"error": "文件不存在"}), 404


@app.route('/api/regulations/search')
def search_regs():
    q = request.args.get('q', '')
    industry = request.args.get('industry', '')
    if not q:
        return jsonify({"error": "请输入搜索关键词"}), 400

    results = search_regulations(q, industry)
    return jsonify({"success": True, "results": results, "count": len(results)})


@app.route('/api/regulations/list')
def list_regs():
    industry = request.args.get('industry', '')
    results = list_regulations(industry)
    return jsonify({"success": True, "results": results, "count": len(results)})


@app.route('/api/regulations/refresh', methods=['POST'])
def refresh_regs():
    init_db()
    seed_regulations()
    return jsonify({"success": True, "message": "法规库已刷新", "reg_count": len(list_regulations())})


@app.route('/api/health')
def health():
    from config import LLM_API_KEY
    return jsonify({
        "status": "ok",
        "llm_configured": bool(LLM_API_KEY),
        "reg_count": len(list_regulations())
    })


@app.route('/api/cleanup', methods=['POST'])
def cleanup_uploads():
    now = time.time()
    max_age = 3600
    removed = 0
    for f in glob.glob(os.path.join(UPLOAD_DIR, '*')):
        if os.path.isfile(f) and (now - os.path.getmtime(f)) > max_age:
            try:
                os.remove(f)
                removed += 1
            except OSError:
                pass
    return jsonify({"success": True, "removed": removed})


if __name__ == '__main__':
    print("\n📋 合同生成与审核智能体启动中...")
    print(f"🌐 http://127.0.0.1:5198")
    print(f"📊 法规库: {len(list_regulations())} 条")
    from config import LLM_API_KEY
    print(f"🤖 LLM: {'已配置' if LLM_API_KEY else '⚠ 未配置(仅规则引擎)'}")
    debug_mode = os.getenv('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(debug=debug_mode, host='127.0.0.1', port=5198)
