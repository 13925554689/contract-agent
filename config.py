"""合同审核智能体 - 配置"""
import os
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

LLM_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
LLM_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")

DB_PATH = os.path.join(BASE_DIR, "data", "regulations.db")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")

INDUSTRIES = ["通用", "建筑工程", "SaaS/信息技术", "劳务/人力资源", "采购/供应链", "租赁/不动产"]

CONTRACT_TYPES = [
    "采购合同", "服务合同", "劳动合同", "租赁合同",
    "保密协议(NDA)", "合作框架协议", "销售合同", "技术开发合同"
]
