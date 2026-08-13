import os
import sys
import time
import requests
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

# 1. 严格从环境变量读取配置，直接使用完整地址
TARGET_URL = os.environ.get("BASE_URL", "").strip()
COOKIE_SID = os.environ.get("COOKIE_SID", "").strip()
COOKIE_NAME = os.environ.get("COOKIE_NAME", "").strip()

TG_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

# Telegram 消息推送辅助函数
def send_tg_message(text):
    print(f"[LOG] {text}")
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TG_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"[警告] TG 文本消息发送失败: {e}")

# Telegram 截图推送辅助函数
def send_tg_photo(photo_path, caption=""):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    if not os.path.exists(photo_path):
        return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendPhoto"
    data = {"chat_id": TG_CHAT_ID, "caption": caption}
    try:
        with open(photo_path, "rb") as photo:
            files = {"photo": photo}
            requests.post(url, data=data, files=files, timeout=15)
    except Exception as e:
        print(f"[警告] TG 截图发送失败: {e}")

def run():
    # 严格校验环境变量
    if not TARGET_URL:
        msg = "❌ [程序终止] 缺失 BASE_URL 环境变量！"
        send_tg_message(msg)
        sys.exit(1)

    if not COOKIE_SID or not COOKIE_NAME:
        msg = "❌ [程序终止] 缺失 COOKIE_SID 或 COOKIE_NAME 环境变量！"
        send_tg_message(msg)
        sys.exit(1)

    # 自动提炼域名给 Cookie 校验使用
    domain = urlparse(TARGET_URL).hostname

    send_tg_message(f"🚀 <b>[自动化流程]</b> GitHub Actions 已启动\n🎯 目标网址: <code>{TARGET_URL}</code>")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled"
            ]
        )
        
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        
        # 注入 Cookie
        context.add_cookies([{
            "name": COOKIE_NAME,
            "value": COOKIE_SID,
            "domain": domain,
            "path": "/"
        }])

        page = context.new_page()
        page.set_default_timeout(15000)

        try:
            # 步骤 1：直接打开完整的目标页面
            print(f"[Playwright] 正在访问地址: {TARGET_URL}")
            send_tg_message(f"🌐 <b>步骤 1/3</b>：正在打开页面 <code>{TARGET_URL}</code>...")
            page.goto(TARGET_URL, wait_until="networkidle")
            time.sleep(2)

            # 保存并发送“已加载完成”的截图
            step1_shot = "step1_loaded.png"
            page.screenshot(path=step1_shot)
            send_tg_photo(step1_shot, f"📸 [页面截图] 打开 {TARGET_URL} 加载完毕")

            # 步骤 2：寻找按钮并双击
            send_tg_message("🔍 <b>步骤 2/3</b>：寻找触发按钮，准备双击...")
            button_selector = "button:has-text('兑换'), button:has-text('Redeem'), .btn-primary, #redeem-btn"

            if page.is_visible(button_selector):
                button = page.locator(button_selector).first
                button.dblclick()
            else:
                page.dblclick("text=/兑换|Redeem/i")

            send_tg_message("⚡ <b>步骤 3/3</b>：已完成双击，等待页面响应...")
            time.sleep(5)

            # 保存并发送“双击后”的最终状态截图
            step2_shot = "step2_result.png"
            page.screenshot(path=step2_shot)
            send_tg_photo(step2_shot, "📸 [页面截图] 双击操作完毕后的页面状态")

            send_tg_message("✅ <b>[自动化流程]</b> 任务正常执行完毕！")

        except Exception as e:
            err_msg = f"❌ <b>[自动化流程] 运行出错/超时</b>:\n<code>{str(e)}</code>"
            send_tg_message(err_msg)

            # 截图保存异常现场
            error_shot = "error_field.png"
            try:
                page.screenshot(path=error_shot)
                send_tg_photo(error_shot, "🚨 [异常截图] 出错时的现场画面")
            except Exception:
                pass
            
            sys.exit(1)

        finally:
            browser.close()

if __name__ == "__main__":
    run()
