import os
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
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--single-process",
                "--no-zygote",
            ],
        )
        context = browser.new_context(storage_state=STATE_FILE)
        page = context.new_page()

        page.goto("https://www.threads.net/", wait_until="networkidle")
        page.wait_for_timeout(3000)

        page.goto("https://www.threads.net/intent/post?text=" + text, wait_until="networkidle")
        page.wait_for_timeout(5000)

        browser.close()
