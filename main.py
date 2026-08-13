#!/usr/bin/env python3
import os
import sys
import json
import time
import re
import base64
import urllib.parse
from datetime import datetime, timezone, timedelta
import requests
from playwright.sync_api import sync_playwright

# ============================================================
# 🎯 配置区 (完全由 GitHub Secrets / 环境变量控制)
# ============================================================
TARGET_URL = os.environ.get("BASE_URL", "").strip()
COOKIE_SID = os.environ.get("COOKIE_SID", "").strip()
COOKIE_NAME = os.environ.get("COOKIE_NAME", "").strip()

TG_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

NAV_TIMEOUT = 30000
SHA_TZ = timezone(timedelta(hours=8))

# ============================================================
# 📢 工具函数
# ============================================================
def log(msg):
    ts = datetime.now(SHA_TZ).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

def send_tg_message(text):
    # 为 Telegram 消息统一加上 PGSS 前缀
    formatted_text = f"PGSS {text}"
    log(f"[TG Log] {formatted_text}")
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TG_CHAT_ID, "text": formatted_text, "parse_mode": "HTML"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"[警告] TG 消息发送失败: {e}")

def send_tg_photo(photo_path, caption=""):
    formatted_caption = f"PGSS {caption}" if caption else "PGSS"
    if not TG_BOT_TOKEN or not TG_CHAT_ID or not os.path.exists(photo_path):
        return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendPhoto"
    data = {"chat_id": TG_CHAT_ID, "caption": formatted_caption}
    try:
        with open(photo_path, "rb") as photo:
            requests.post(url, data=data, files={"photo": photo}, timeout=15)
    except Exception as e:
        print(f"[警告] TG 截图发送失败: {e}")

def decode_from_linkvertise(url):
    m = re.search(r'[?&]r=([^&]+)', url)
    if not m:
        return None
    try:
        return base64.b64decode(urllib.parse.unquote(m.group(1))).decode()
    except Exception:
        return None

# ============================================================
# 🚀 纯粹兑换动作执行
# ============================================================
def do_exchange():
    if not TARGET_URL:
        send_tg_message("❌ [程序终止] 缺失 BASE_URL 环境变量！")
        sys.exit(1)
    if not COOKIE_SID:
        send_tg_message("❌ [程序终止] 缺失 COOKIE_SID 环境变量！")
        sys.exit(1)
    if not COOKIE_NAME:
        send_tg_message("❌ [程序终止] 缺失 COOKIE_NAME 环境变量！")
        sys.exit(1)

    domain = urllib.parse.urlparse(TARGET_URL).hostname
    send_tg_message("🚀 <b>[Actions 启动]</b> 准备执行兑换流程...")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"]
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

        # 动态读取并注入 Cookie
        context.add_cookies([{
            "name": COOKIE_NAME,
            "value": COOKIE_SID,
            "domain": f".{domain}" if not domain.startswith(".") else domain,
            "path": "/",
            "httpOnly": True,
            "secure": True
        }])

        page = context.new_page()

        try:
            # 1. 打开目标页面
            log(f"正在前往兑换页面: {TARGET_URL}")
            page.goto(TARGET_URL, timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
            time.sleep(3)

            # 2. 定位按钮 #genBtn
            gen_btn = page.locator("#genBtn")
            if not gen_btn.is_visible(timeout=5000):
                send_tg_message("⚠️ 页面未找到 <code>#genBtn</code> 兑换按钮！")
                shot = "no_genbtn.png"
                page.screenshot(path=shot)
                send_tg_photo(shot, "🚨 未找到按钮时的截图")
                sys.exit(1)

            captured_lv_url = []

            # 3. 监听抓包请求
            def handle_request(request):
                if "chargebee.com/api/internal/kvl" in request.url and request.method == "POST":
                    try:
                        post_data = request.post_data
                        if post_data:
                            data_json = json.loads(post_data)
                            target_url = data_json.get("data", {}).get("site_meta_window_url")
                            if target_url and "linkvertise.com" in target_url:
                                log(f"🎯 [精准捕获] Linkvertise 链接: {target_url}")
                                captured_lv_url.append(target_url)
                    except Exception as e:
                        log(f"⚠️ 抓包解析异常: {e}")

            page.on("request", handle_request)

            existing_pages = len(context.pages)
            gen_btn.click()
            send_tg_message("🖱️ 已点击 <code>#genBtn</code>，等待抓包/页面响应...")

            # 4. 等待捕获链接
            lv_url = None
            for _ in range(10):
                if captured_lv_url:
                    lv_url = captured_lv_url[0]
                    break
                time.sleep(1)

            page.remove_listener("request", handle_request)

            # 兜底捕获：如果抓包没捕获到，检测弹出的新窗口
            if not lv_url:
                try:
                    np = context.wait_for_event('page', timeout=3000)
                    np.wait_for_load_state("domcontentloaded", timeout=3000)
                    if "linkvertise.com" in np.url:
                        lv_url = np.url
                    np.close()
                except Exception:
                    pass

            if not lv_url:
                for _ in range(5):
                    time.sleep(1)
                    pages = context.pages
                    if len(pages) > existing_pages:
                        np = pages[-1]
                        if "linkvertise.com" in np.url:
                            lv_url = np.url
                        np.close()
                        break
                    if "linkvertise.com" in page.url:
                        lv_url = page.url
                        break

            if not lv_url:
                send_tg_message("❌ 点击后未能抓取到目标链接！")
                shot = "no_lv_url.png"
                page.screenshot(path=shot)
                send_tg_photo(shot, "🚨 抓包失败时的截图")
                sys.exit(1)

            # 5. Base64 解码并访问最终页面
            dest = decode_from_linkvertise(lv_url)
            if not dest:
                send_tg_message("❌ 捕获到目标链接，但 Base64 解码失败！")
                sys.exit(1)

            send_tg_message("🌐 成功解密目标链接，正在访问...")
            try:
                page.goto(dest, timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
            except Exception:
                pass
            time.sleep(3)

            # 发送成功截图与结束提示
            shot = "success.png"
            page.screenshot(path=shot)
            send_tg_photo(shot, "📸 兑换完成后的页面截图")
            send_tg_message("🎉 <b>[兑换流程结束]</b> 任务成功完成！")

        except Exception as e:
            send_tg_message(f"❌ <b>[运行异常]</b>:\n<code>{str(e)}</code>")
            sys.exit(1)
        finally:
            browser.close()

if __name__ == "__main__":
    do_exchange()
