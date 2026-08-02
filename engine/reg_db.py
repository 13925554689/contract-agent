"""法规库引擎 - SQLite + 搜索 + 定期更新"""
import sqlite3
import json
import os
import re
from config import DB_PATH

_FTS_SPECIAL = re.compile(r'[AND|OR|NOT*^"(){}]')


def _escape_fts_query(query: str) -> str:
    cleaned = _FTS_SPECIAL.sub(' ', query)
    tokens = [t for t in cleaned.split() if len(t) > 0]
    return ' '.join(f'"{t}"' for t in tokens)


def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_db()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS regulations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT '通用',
                industry TEXT NOT NULL DEFAULT '通用',
                content TEXT NOT NULL,
                source TEXT DEFAULT '',
                publish_date TEXT DEFAULT '',
                effective_date TEXT DEFAULT '',
                risk_tags TEXT DEFAULT '[]',
                search_text TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS reg_fts USING fts5(
                title, content, industry, category, search_text,
                content='regulations', content_rowid='id'
            );
            CREATE TRIGGER IF NOT EXISTS reg_ai AFTER INSERT ON regulations BEGIN
                INSERT INTO reg_fts(rowid, title, content, industry, category, search_text)
                VALUES (new.id, new.title, new.content, new.industry, new.category, new.search_text);
            END;
            CREATE TRIGGER IF NOT EXISTS reg_ad AFTER DELETE ON regulations BEGIN
                INSERT INTO reg_fts(reg_fts, rowid, title, content, industry, category, search_text)
                VALUES ('delete', old.id, old.title, old.content, old.industry, old.category, old.search_text);
            END;
            CREATE TRIGGER IF NOT EXISTS reg_au AFTER UPDATE ON regulations BEGIN
                INSERT INTO reg_fts(reg_fts, rowid, title, content, industry, category, search_text)
                VALUES ('delete', old.id, old.title, old.content, old.industry, old.category, old.search_text);
                INSERT INTO reg_fts(rowid, title, content, industry, category, search_text)
                VALUES (new.id, new.title, new.content, new.industry, new.category, new.search_text);
            END;
        """)
        conn.commit()
    finally:
        conn.close()


def seed_regulations():
    conn = get_db()
    try:
        existing = conn.execute("SELECT COUNT(*) FROM regulations").fetchone()[0]
        if existing > 0:
            return

        seeds = [
            ("《民法典》合同编", "合同法规", "通用",
             "《中华人民共和国民法典》合同编是规范合同关系的基本法律。主要内容包括：合同的订立、效力、履行、变更、转让、终止以及违约责任。"
             "合同当事人应遵循公平原则确定各方权利义务。格式条款提供方应以合理方式提请对方注意免除或限制其责任的条款。"
             "违约责任包括继续履行、采取补救措施或赔偿损失。违约金过高或过低可请求调整。不可抗力可部分或全部免除责任。",
             "2021-01-01", ['合同效力', '违约责任', '格式条款', '不可抗力']),

            ("《劳动合同法》", "合同法规", "劳务/人力资源",
             "劳动合同应具备：合同期限、工作内容和地点、工作时间和休息休假、劳动报酬、社会保险、劳动保护和职业危害防护。"
             "试用期不得超过6个月。用人单位自用工之日起超1个月不满1年未签书面合同应支付双倍工资。"
             "解除合同需提前30日书面通知或支付代通知金。违法解除应支付赔偿金（经济补偿2倍）。",
             "2008-01-01", ['试用期', '合同期限', '解除条件', '赔偿金']),

            ("《招标投标法》", "合同法规", "建筑工程",
             "大型基础设施、公用事业等关系社会公共利益的项目必须招标。招标分公开招标和邀请招标。"
             "投标人不得相互串通投标报价。中标通知书发出后30日内签订书面合同。"
             "合同主要条款应与招标文件和中标人投标文件一致，不得另行订立背离合同实质性内容的其他协议。",
             "2017-12-28", ['招标条件', '投标规则', '合同签订', '实质性条款']),

            ("《网络安全法》", "行业法规", "SaaS/信息技术",
             "网络运营者收集使用个人信息应遵循合法正当必要原则，明示收集使用信息的目的方式和范围。"
             "不得泄露篡改毁损收集的个人信息，应采取技术措施确保信息安全。"
             "关键信息基础设施运营者在中国境内运营中收集和产生的个人信息和重要数据应在境内存储。"
             "SaaS服务合同中必须明确数据安全责任、数据存储位置、数据删除机制和安全事件通知义务。",
             "2017-06-01", ['个人信息', '数据安全', 'SaaS合规', '跨境数据']),

            ("《个人信息保护法》", "行业法规", "通用",
             "处理个人信息应当具有明确、合理的目的，并应当与处理目的直接相关，采取对个人权益影响最小的方式。"
             "收集个人信息，应当限于实现处理目的的最小范围，不得过度收集个人信息。"
             "敏感个人信息包括生物识别、宗教信仰、特定身份、医疗健康、金融账户、行踪轨迹等。"
             "向境外提供个人信息需通过安全评估、标准合同或认证。违反规定罚款最高5000万元或上年营业额5%。",
             "2021-11-01", ['个人信息', '最小必要', '敏感信息', '跨境传输']),

            ("《电子签名法》", "行业法规", "通用",
             "可靠的电子签名与手写签名或盖章具有同等法律效力。可靠的电子签名需满足：电子签名制作数据属于签名人专有；"
             "签署时电子签名制作数据仅由签名人控制；签署后对电子签名的任何改动能够被发现；签署后对数据电文内容和形式的任何改动能够被发现。"
             "合同各方约定使用电子签名的文书不得仅因其采用电子签名形式而否定其法律效力。",
             "2019-04-23", ['电子签名', '合同效力', '数据电文']),

            ("《保障中小企业款项支付条例》", "行业法规", "通用",
             "机关事业单位和大型企业不得强制中小企业接受不合理的付款期限方式条件和违约责任等交易条件。"
             "付款期限最长不得超过60日。逾期支付应支付逾期利息，利率不低于合同订立时1年期贷款市场报价利率。"
             "不得以法定代表人变更、未完成内部审批流程、等待竣工验收批复决算审计等为由拒绝或延迟支付。"
             "合同中付款条款应明确付款时间、支付方式、逾期利率。",
             "2020-09-01", ['支付期限', '中小企业', '逾期利率', '付款条款']),

            ("《建筑工程施工合同司法解释》", "行业法规", "建筑工程",
             "建设工程施工合同无效的情形：承包人未取得建筑业企业资质；借用资质；工程必须招标而未招标或中标无效；"
             "转包、违法分包。合同无效但工程验收合格，可参照合同约定折价补偿。"
             "工程变更签证须经发包人确认；未经确认但承包人能证明发包人同意的，应予认定。"
             "发包人逾期支付工程款的，承包人可以主张逾期付款利息（按同期贷款利率或LPR）。",
             "2021-01-01", ['合同无效', '工程款', '变更签证', '转包分包']),
        ]

        for title, category, industry, content, pub_date, tags in seeds:
            conn.execute("""
                INSERT INTO regulations (title, category, industry, content, source, publish_date, risk_tags, search_text)
                VALUES (?, ?, ?, ?, '中国法律', ?, ?, ?)
            """, (title, category, industry, content, pub_date, json.dumps(tags),
                  f"{title} {content[:200]} {category} {industry}"))

        conn.commit()
    finally:
        conn.close()


def search_regulations(query: str, industry: str = "", limit: int = 10) -> list:
    conn = get_db()
    results = []
    try:
        safe_query = _escape_fts_query(query)
        rows = conn.execute("""
            SELECT id, title, category, industry, content, risk_tags, publish_date,
                   snippet(reg_fts, 2, '<mark>', '</mark>', '...', 40) as snippet
            FROM reg_fts WHERE reg_fts MATCH ? ORDER BY rank LIMIT ?
        """, (safe_query, limit)).fetchall()

        for r in rows:
            results.append({
                "id": r["id"], "title": r["title"], "category": r["category"],
                "industry": r["industry"], "content": r["content"][:500],
                "tags": json.loads(r["risk_tags"]), "snippet": r["snippet"],
                "publish_date": r["publish_date"]
            })
    except sqlite3.OperationalError:
        esc = query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        like_q = '%' + esc + '%'
        rows = conn.execute("""
            SELECT id, title, category, industry, content, risk_tags, publish_date
            FROM regulations WHERE industry LIKE ? ESCAPE '\\' OR title LIKE ? ESCAPE '\\' OR content LIKE ? ESCAPE '\\'
            ORDER BY id DESC LIMIT ?
        """, (like_q, like_q, like_q, limit)).fetchall()
        for r in rows:
            results.append({
                "id": r["id"], "title": r["title"], "category": r["category"],
                "industry": r["industry"], "content": r["content"][:500],
                "tags": json.loads(r["risk_tags"]), "snippet": r["content"][:200],
                "publish_date": r["publish_date"]
            })
    finally:
        conn.close()
    return results


def list_regulations(industry: str = "") -> list:
    conn = get_db()
    try:
        if industry:
            rows = conn.execute("SELECT * FROM regulations WHERE industry=? ORDER BY id DESC", (industry,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM regulations ORDER BY id DESC").fetchall()
        results = []
        for r in rows:
            row = dict(r)
            try:
                row["tags"] = json.loads(row.get("risk_tags") or "[]")
            except (json.JSONDecodeError, TypeError):
                row["tags"] = []
            results.append(row)
        return results
    finally:
        conn.close()


def add_regulations(entries: list[dict]) -> int:
    conn = get_db()
    count = 0
    try:
        for e in entries:
            conn.execute("""
                INSERT OR IGNORE INTO regulations (title, category, industry, content, source, publish_date, risk_tags, search_text)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (e["title"], e.get("category", "行业法规"), e.get("industry", "通用"),
                  e["content"], e.get("source", ""), e.get("publish_date", ""),
                  json.dumps(e.get("tags", [])),
                  f"{e['title']} {e.get('category','')} {e.get('industry','')}"))
            count += 1
        conn.commit()
    finally:
        conn.close()
    return count
