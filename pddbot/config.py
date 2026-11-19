"""集中管理配置：从 .env 读取，提供全局常量。"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv


def _app_root() -> Path:
    """返回应用根目录。

    - 普通运行：项目根目录（本文件的上一级）。
    - PyInstaller 打包后：exe 所在目录（这样 .env / storage_state.json
      能放在 exe 旁边，用户可编辑）。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


ROOT = _app_root()

# 打包后（frozen）的初始化：浏览器内核路径 + 把内置数据释放到 exe 旁
if getattr(sys, "frozen", False):
    # 浏览器内核放在 exe 旁的 ms-playwright 目录（首次运行自动下载，用户无需单独安装）
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / "ms-playwright"))
    # PyInstaller 把数据打进 _internal，这里首次运行时释放到 exe 旁，方便用户查看/编辑
    import shutil

    _bundle = Path(getattr(sys, "_MEIPASS", ROOT))
    for _name in ("data/qa.yaml", "data/products.yaml", ".env.example"):
        _src, _dst = _bundle / _name, ROOT / _name
        if _src.exists() and not _dst.exists():
            _dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(_src, _dst)
            except Exception:
                pass

# 优先加载 exe/项目旁边的 .env
load_dotenv(ROOT / ".env")

# 登录态文件（Playwright storage_state）
STATE_FILE = ROOT / "storage_state.json"

# 日志目录
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

# ===== 拼多多商家后台 =====
PDD_HOME = "https://mms.pinduoduo.com/"

# ===== DeepSeek =====
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

# ===== 运行配置 =====
DRY_RUN = os.getenv("DRY_RUN", "true").lower() in ("1", "true", "yes")
POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", "2.0"))
# 轮询间隔随机范围（秒）：模拟人工节奏，避免固定频率被识别为自动化
POLL_MIN = float(os.getenv("POLL_MIN", "3"))
POLL_MAX = float(os.getenv("POLL_MAX", "8"))
# 人工最近一次回复后，再无人工回复超过这个时长（分钟），AI 才恢复接管该会话
HUMAN_PAUSE_MINUTES = float(os.getenv("HUMAN_PAUSE_MINUTES", "10"))
HEADLESS = os.getenv("HEADLESS", "false").lower() in ("1", "true", "yes")

# ===== 飞书通知 =====
FEISHU_ENABLED = os.getenv("FEISHU_ENABLED", "false").lower() in ("1", "true", "yes")
FEISHU_WEBHOOK = os.getenv("FEISHU_WEBHOOK", "")
FEISHU_SECRET = os.getenv("FEISHU_SECRET", "")
FEISHU_AT_USERS = [u.strip() for u in os.getenv("FEISHU_AT_USERS", "").split(",") if u.strip()]

# ===== 自动议价（机器人在区间内让价，人工兑现改价/发券）=====
def _bargain_from_env() -> None:
    global BARGAIN_ENABLED, BARGAIN_MIN, BARGAIN_MAX, BARGAIN_STEP, BARGAIN_DAILY_BUDGET
    global BARGAIN_UNKNOWN_PRODUCT, BARGAIN_AD_SHARE, BARGAIN_FULFILL_TEXT
    BARGAIN_ENABLED = os.getenv("BARGAIN_ENABLED", "false").lower() in ("1", "true", "yes")
    # 未在「利润测算」录入的商品用这个默认区间（元）
    BARGAIN_MIN = float(os.getenv("BARGAIN_MIN", "0.5"))
    BARGAIN_MAX = float(os.getenv("BARGAIN_MAX", "2"))
    # 让价取整单位（元）
    BARGAIN_STEP = float(os.getenv("BARGAIN_STEP", "0.5"))
    # 每天最多让出的总金额（元），超出后砍价一律转人工
    BARGAIN_DAILY_BUDGET = float(os.getenv("BARGAIN_DAILY_BUDGET", "50"))
    # 未录入利润测算的商品：default=用默认区间，human=不让价转人工
    BARGAIN_UNKNOWN_PRODUCT = os.getenv("BARGAIN_UNKNOWN_PRODUCT", "default")
    # 推广费占售价比例（%），计算商品最大可让价时预留；商品填了实际 ROI 则按 ROI 算
    BARGAIN_AD_SHARE = float(os.getenv("BARGAIN_AD_SHARE", "0"))
    # 答应让价后告诉买家怎么拿到优惠（人工随后改价/发券）
    BARGAIN_FULFILL_TEXT = os.getenv(
        "BARGAIN_FULFILL_TEXT", "您先拍下先别付款，客服马上给您改价哦～"
    )


_bargain_from_env()


# ===== 链接监控（定时抓商品数据，异常才推飞书）=====
def _monitor_from_env() -> None:
    global MONITOR_INTERVAL, MONITOR_DROP_PCT, MONITOR_MIN_UV, MONITOR_CVR_DROP_PCT
    global MONITOR_CONFIRM, MONITOR_COOLDOWN_H, MONITOR_START_HOUR, MONITOR_END_HOUR
    MONITOR_INTERVAL = float(os.getenv("MONITOR_INTERVAL", "60"))        # 检查间隔（分钟）
    MONITOR_DROP_PCT = float(os.getenv("MONITOR_DROP_PCT", "50"))        # 访客比平时低多少 % 算骤降
    MONITOR_MIN_UV = int(float(os.getenv("MONITOR_MIN_UV", "30")))       # 平时访客低于这个数的不判
    MONITOR_CVR_DROP_PCT = float(os.getenv("MONITOR_CVR_DROP_PCT", "60"))  # 转化率比平时低多少 % 算暴跌
    MONITOR_CONFIRM = int(float(os.getenv("MONITOR_CONFIRM", "2")))      # 连续几次异常才推送
    MONITOR_COOLDOWN_H = float(os.getenv("MONITOR_COOLDOWN_H", "6"))     # 同一异常几小时内不重复推
    MONITOR_START_HOUR = int(float(os.getenv("MONITOR_START_HOUR", "8")))  # 每天几点开始检查
    MONITOR_END_HOUR = int(float(os.getenv("MONITOR_END_HOUR", "23")))     # 每天几点停止检查


_monitor_from_env()


def _reload() -> None:
    """从当前环境变量刷新模块级配置。"""
    global DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL
    global DRY_RUN, POLL_INTERVAL, POLL_MIN, POLL_MAX, HUMAN_PAUSE_MINUTES, HEADLESS
    global FEISHU_ENABLED, FEISHU_WEBHOOK, FEISHU_SECRET, FEISHU_AT_USERS
    DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
    DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    DRY_RUN = os.getenv("DRY_RUN", "true").lower() in ("1", "true", "yes")
    POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", "2.0"))
    POLL_MIN = float(os.getenv("POLL_MIN", "3"))
    POLL_MAX = float(os.getenv("POLL_MAX", "8"))
    HUMAN_PAUSE_MINUTES = float(os.getenv("HUMAN_PAUSE_MINUTES", "10"))
    HEADLESS = os.getenv("HEADLESS", "false").lower() in ("1", "true", "yes")
    FEISHU_ENABLED = os.getenv("FEISHU_ENABLED", "false").lower() in ("1", "true", "yes")
    FEISHU_WEBHOOK = os.getenv("FEISHU_WEBHOOK", "")
    FEISHU_SECRET = os.getenv("FEISHU_SECRET", "")
    FEISHU_AT_USERS = [u.strip() for u in os.getenv("FEISHU_AT_USERS", "").split(",") if u.strip()]
    _bargain_from_env()
    _monitor_from_env()


def update_env(updates: dict) -> None:
    """更新 .env 里的键值（保留其他行与注释），并同步刷新当前进程配置。"""
    env_path = ROOT / ".env"
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    done = set()
    for i, line in enumerate(lines):
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            key = s.split("=", 1)[0].strip()
            if key in updates:
                lines[i] = f"{key}={updates[key]}"
                done.add(key)
    for k, v in updates.items():
        if k not in done:
            lines.append(f"{k}={v}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for k, v in updates.items():
        os.environ[k] = str(v)
    _reload()
