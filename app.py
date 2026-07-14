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
from engine.generator import generate_contract, export_docx

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


@app.route('/api/download/<filename>')
def download_file(filename):
    safe_name = secure_filename(filename)
    if not safe_name or safe_name != filename:
        return jsonify({"error": "非法文件名"}), 400

    path = os.path.join(UPLOAD_DIR, safe_name)
    real_upload = os.path.realpath(UPLOAD_DIR)
    real_path = os.path.realpath(path)
    if not real_path.startswith(real_upload + os.sep):
        return jsonify({"error": "非法文件路径"}), 400

    if os.path.exists(real_path):
        return send_file(real_path, as_attachment=True, download_name=safe_name)
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
