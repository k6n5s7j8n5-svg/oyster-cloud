import os
import requests
from fastapi import FastAPI, Request
from openai import OpenAI

app = FastAPI()

# 環境変数から読む（Railway Variablesに入れてる前提）
LINE_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

# OpenAIクライアント（1回だけ生成）
client = OpenAI(api_key=OPENAI_API_KEY)

@app.get("/")
def root():
    return {"ok": True}

@app.post("/webhook")
async def webhook(request: Request):
    body = await request.json()
    print("LINEきた")
    print(body)

    if not LINE_TOKEN:
        return {"ok": False, "error": "LINE_CHANNEL_ACCESS_TOKEN is missing"}
    if not OPENAI_API_KEY:
        return {"ok": False, "error": "OPENAI_API_KEY is missing"}

    events = body.get("events", [])
    for ev in events:
        reply_token = ev.get("replyToken")
        msg = ev.get("message", {})
        text = msg.get("text")

        # テキスト以外は無視
        if not reply_token or text is None:
            continue

        # ===== AI生成 =====
        try:
            resp = client.responses.create(
                model="gpt-4o-mini",
                input=(
                    "あなたは大阪の立ち飲み牡蠣屋の店主の相棒AI。"
                    "関西弁で、短めに、フレンドリーに返事して。\n"
                    f"ユーザー: {text}\nAI:"
                ),
            )
            ai_text = (resp.output_text or "").strip()
            if not ai_text:
                ai_text = "ごめん、今ちょい詰まったわ💦もう一回送って！"
        except Exception as e:
            print("OpenAI error:", e)
            ai_text = "ごめん、今ちょい詰まったわ💦もう一回送って！"

        # ===== LINEへ返信 =====
        try:
            res = requests.post(
                "https://api.line.me/v2/bot/message/reply",
                headers={
                    "Authorization": f"Bearer {LINE_TOKEN}",
                    "Content-Type": "application/json",
                },
                json={
                    "replyToken": reply_token,
                    "messages": [{"type": "text", "text": ai_text}],
                },
                timeout=10,
            )
            print("reply status:", res.status_code, res.text)
        except Exception as e:
            print("LINE reply error:", e)

    return {"ok": True}

