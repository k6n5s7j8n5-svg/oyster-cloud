import os
import requests
from fastapi import FastAPI, Request

app = FastAPI()

LINE_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")

@app.post("/webhook")
async def webhook(request: Request):
    body = await request.json()
    print("LINEきた")
    print(body)

    # 念のためトークン未設定チェック
    if not LINE_TOKEN:
        return {"ok": False, "error": "LINE_CHANNEL_ACCESS_TOKEN is missing"}

    events = body.get("events", [])
    for ev in events:
        reply_token = ev.get("replyToken")
        msg = ev.get("message", {})
        text = msg.get("text")

        # テキスト以外は無視
        if not reply_token or text is None:
            continue

        # 返信内容（とりあえずオウム返し）
        reply_text = f"受け取ったで: {text}"

        requests.post(
            "https://api.line.me/v2/bot/message/reply",
            headers={
                "Authorization": f"Bearer {LINE_TOKEN}",
                "Content-Type": "application/json",
            },
            json={
                "replyToken": reply_token,
                "messages": [{"type": "text", "text": reply_text}],
            },
            timeout=10,
        )

    return {"ok": True}
