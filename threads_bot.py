import os
import re
import base64
from playwright.sync_api import sync_playwright

STATE_FILE = "threads_state.json"

def restore_storage():
    b64 = os.getenv("THREADS_STATE_B64", "")
    if not b64:
        raise RuntimeError("THREADS_STATE_B64 not set")
    data = base64.b64decode(b64)
    with open(STATE_FILE, "wb") as f:
        f.write(data)

def post_to_threads(text: str):
    restore_storage()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context(storage_state=STATE_FILE)
        page = context.new_page()

        page.goto("https://www.threads.net/", wait_until="domcontentloaded")
        page.wait_for_timeout(2000)

        # ※ここはThreads側のUI変更で変わる。前に動いてたセレクタの版に合わせる
        # ひとまず「投稿」ボタン検索のアプローチ（has-text）は残す
        page.get_by_role("button", name="新規スレッド").click(timeout=15000)
        editor = page.locator("div[contenteditable='true']").first
        editor.click()
        editor.fill(text)

        page.get_by_role("button", name=re.compile("投稿|Post")).click(timeout=15000)

        context.close()
        browser.close()
