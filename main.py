#!/usr/bin/env python3
"""
pgss  自动兑换 - 纯兑换版
====================================================================
说明:
1. 仅保留通过接口获取精确冷却时间并在时间到后发起兑换的功能。
2. 已彻底移除所有明文敏感域名与 AFK 挂机逻辑。
3. 必须通过环境变量 BASE_URL 与 COOKIE_SID 传入配置。
"""

import json
import time
import re
import base64
import urllib.parse
import os
import sys
from datetime import datetime, timezone, timedelta
from playwright.sync_api import sync_playwright

# ============================================================
# 🎯 配置区（严格从环境变量读取）
# ============================================================
# 1. BASE_URL：基础 API 域名 (必须设置)
BASE_URL = os.environ.get("BASE_URL", "").strip().rstrip("/")

# 2. COOKIE_SID：登录凭证
COOKIE_SID = os.environ.get("COOKIE_SID", "").strip()

# 3. PROXY：代理设置 (例如: "http://127.0.0.1:7890")
PROXY = os.environ.get("PROXY", "").strip()

# 4. HEADED：是否显示浏览器界面，默认无头模式 (False)
HEADED = os.environ.get("HEADED", "False").lower() in ("true", "1", "t")

# 5. LOG_FILE：日志路径
LOG_FILE = os.environ.get("LOG_FILE", "pgss.log")

# 6. NAV_TIMEOUT：超时设置
NAV_TIMEOUT = int(os.environ.get("NAV_TIMEOUT", "30000"))

# 🔴 日志控制开关：True = 输出日志并写入文件；False = 完全静默运行
SHOW_LOG = os.environ.get("SHOW_LOG", "True").lower() in ("true", "1", "t")

# 东八区时区对象
SHA_TZ = timezone(timedelta(hours=8))

# ============================================================
def log(msg):
    if not SHOW_LOG:
        return
    ts = datetime.now(SHA_TZ).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:
        print(f"写入日志失败: {e}")

def decode_from_linkvertise(url):
    m = re.search(r'[?&]r=([^&]+)', url)
    if not m:
        return None
    try:
        return base64.b64decode(urllib.parse.unquote(m.group(1))).decode()
    except Exception:
        return None

def parse_iso_time(iso_str):
    """将 ISO 时间字符串转为 UTC 时间戳（秒）"""
    if not iso_str:
        return 0
    try:
        if iso_str.endswith("Z"):
            iso_str = iso_str[:-1] + "+00:00"
        dt = datetime.fromisoformat(iso_str)
        return dt.timestamp()
    except Exception:
        return 0

def format_beijing_time(iso_str):
    """将 ISO 时间字符串转换为北京时间的可读字符串 (YYYY-MM-DD HH:MM:SS)"""
    if not iso_str:
        return "未知时间"
    try:
        if iso_str.endswith("Z"):
            iso_str = iso_str[:-1] + "+00:00"
        dt = datetime.fromisoformat(iso_str)
        dt_bj = dt.astimezone(SHA_TZ)
        return dt_bj.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return iso_str

def safe_goto(page, url):
    """安全导航"""
    for attempt in range(3):
        try:
            page.goto(url, timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
            return
        except Exception as e:
            if "ERR_ABORTED" in str(e) and attempt < 2:
                time.sleep(1)
                continue
            raise e

# ============================================================
def check_and_exchange_via_api(exchange_page, context):
    """通过访问 /api/lv/stats 接口获取精确冷却时间，时间到后自动触发兑换。返回 True 表示兑换成功需重启"""
    try:
        # 1. 动态拼装 API 地址
        stats_url = f"{BASE_URL}/api/lv/stats"
        log("🔍 正在请求接口获取精确兑换状态...")
        response = exchange_page.goto(stats_url, timeout=NAV_TIMEOUT)
        
        if not response or response.status != 200:
            log(f"⚠️ 获取状态接口失败，状态码: {response.status if response else 'None'}，60 秒后重试")
            time.sleep(60)
            return False

        body_text = exchange_page.locator("body").inner_text()
        data = json.loads(body_text)

        enabled = data.get("enabled", True)
        remaining = data.get("remaining", 0)
        next_available_str = data.get("next_available")

        next_available_bj = format_beijing_time(next_available_str)
        log(f"📊 [兑换状态] 启用状态: {enabled} | 今日剩余次数: {remaining} | 下次可用时间(北京时间): {next_available_bj}")

        if not enabled or remaining <= 0:
            log("⚠️ 功能未启用或今日次数已用尽，进入 300 秒休眠检查...")
            time.sleep(300)
            return False

        # 2. 计算冷却时间
        now_ts = time.time()
        next_ts = parse_iso_time(next_available_str)
        
        if next_ts > now_ts:
            wait_sec = int(next_ts - now_ts) + 10  # 多加 10 秒缓冲
            hrs, rem = divmod(wait_sec, 3600)
            mins, secs = divmod(rem, 60)
            log(f"⏳ 当前处于冷却中，还需等待 {hrs}h {mins}m {secs}s (目标北京时间: {next_available_bj})")
            time.sleep(wait_sec)
            return False

        # 3. 冷却已过，跳转回网页版执行点击兑换
        log("🎯 冷却已结束，准备前往兑换页面执行点击操作...")
        redeem_page_url = f"{BASE_URL}/linkvertise"
        safe_goto(exchange_page, redeem_page_url)
        time.sleep(3)

        gen_btn = exchange_page.locator("#genBtn")
        if not gen_btn.is_visible(timeout=5000):
            log("⚠️ 未找到 #genBtn 兑换按钮，60 秒后重试")
            time.sleep(60)
            return False

        captured_lv_url = []

        # 🔍 精准监听 Chargebee 上报请求，提取目标 Linkvertise 链接
        def handle_request(request):
            if "chargebee.com/api/internal/kvl" in request.url and request.method == "POST":
                try:
                    post_data = request.post_data
                    if post_data:
                        data_json = json.loads(post_data)
                        target_url = data_json.get("data", {}).get("site_meta_window_url")
                        if target_url and "linkvertise.com" in target_url:
                            log(f"🎯 [精准捕获] 从埋点数据中成功提取 Linkvertise 链接: {target_url}")
                            captured_lv_url.append(target_url)
                except Exception as e:
                    log(f"⚠️ 解析抓包数据异常: {e}")

        exchange_page.on("request", handle_request)

        existing = len(context.pages)
        gen_btn.click()
        log("🖱️ 已点击兑换按钮")

        # 等待最多 10 秒捕获链接
        lv_url = None
        for _ in range(10):
            if captured_lv_url:
                lv_url = captured_lv_url[0]
                break
            time.sleep(1)

        exchange_page.remove_listener("request", handle_request)

        # 兜底抓取逻辑
        if not lv_url:
            try:
                np = context.wait_for_event('page', timeout=5000)
                np.wait_for_load_state("domcontentloaded", timeout=5000)
                if "linkvertise.com" in np.url:
                    lv_url = np.url
                np.close()
            except Exception:
                pass

        if not lv_url:
            for _ in range(10):
                time.sleep(1)
                pages = context.pages
                if len(pages) > existing:
                    np = pages[-1]
                    if "linkvertise.com" in np.url:
                        lv_url = np.url
                    np.close()
                    break
                if "linkvertise.com" in exchange_page.url:
                    lv_url = exchange_page.url
                    break

        if not lv_url:
            log("❌ 未能获取到 Linkvertise 链接，60 秒后重试...")
            time.sleep(60)
            return False

        dest = decode_from_linkvertise(lv_url)
        if not dest:
            log("❌ 解析兑换目标地址失败，60 秒后重试...")
            time.sleep(60)
            return False

        log("🌐 打开兑换目标链接...")
        try:
            exchange_page.goto(dest, timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
        except Exception:
            pass
        time.sleep(3)
        
        log("✅ 兑换已完成！触发重新打开浏览器机制以彻底清除缓存...")
        return True

    except Exception as e:
        log(f"⚠️ 兑换过程异常: {e}")
        time.sleep(30)
        return False

def run_bot():
    if not BASE_URL:
        log("❌ 错误：未配置环境变量 BASE_URL，程序终止")
        sys.exit(1)

    cookie_value = COOKIE_SID.strip()
    if not cookie_value:
        log("❌ 错误：未配置环境变量 COOKIE_SID，程序终止")
        sys.exit(1)

    # 根据 BASE_URL 动态计算顶级域名
    parsed_base = urllib.parse.urlparse(BASE_URL)
    host_parts = parsed_base.netloc.split(':')[0].split('.')
    if len(host_parts) >= 2:
        cookie_domain = f".{'.'.join(host_parts[-2:])}"
    else:
        cookie_domain = f".{parsed_base.netloc}"

    proxy_setting = {"server": PROXY.strip()} if PROXY and PROXY.strip() else None

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=not HEADED,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
            proxy=proxy_setting,
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

        context.add_cookies([{
            "name": "pingless.sid",
            "value": cookie_value,
            "domain": cookie_domain,
            "path": "/",
            "httpOnly": True,
            "secure": True
        }])

        exchange_page = context.new_page()
        log("🚀 开始运行 pgss 自动兑换脚本...")
        
        try:
            while True:
                log("🔍 检查接口状态并执行兑换...")
                need_restart = check_and_exchange_via_api(exchange_page, context)
                
                if need_restart:
                    log("🔄 触发浏览器重启机制，正在关闭当前浏览器实例以完全清除缓存...")
                    break

        except KeyboardInterrupt:
            log("👋 中断")
        finally:
            browser.close()
            log("浏览器已关闭")

def main():
    while True:
        try:
            run_bot()
            log("⏳ 等待 5 秒后重新启动全新的浏览器实例...")
            time.sleep(5)
        except KeyboardInterrupt:
            log("👋 程序完全退出")
            break
        except Exception as e:
            log(f"❌ 运行发生致命异常: {e}，将在 10 秒后重试...")
            time.sleep(10)

if __name__ == "__main__":
    main()
