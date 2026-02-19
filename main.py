import os
import requests
from fastapi import FastAPI, Request
from openai import OpenAI

app = FastAPI()

# 環境変数
LINE_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
OPENAI_KEY = os.environ.get("OPENAI_API_KEY")

# OpenAI クライアント（キーが無いときは None にする）
client = OpenAI(api_key=OPENAI_KEY) if OPENAI_KEY else None


@app.get("/")
def root():
    return {"ok": True}


@app.post("/webhook")
async def webhook(request: Request):
    body = await request.json()
    print("LINEきた")
    print(body)

    if not LINE_TOKEN:
        print("ENV missing: LINE_CHANNEL_ACCESS_TOKEN")
        return {"ok": False, "error": "LINE_CHANNEL_ACCESS_TOKEN is missing"}

    events = body.get("events", [])
    for ev in events:
        reply_token = ev.get("replyToken")
        msg = ev.get("message", {}) or {}
        text = msg.get("text")

        # textメッセージ以外（スタンプ等）は無視
        if not reply_token or text is None:
            continue

        # ===== AI生成 =====
        ai_text = "ごめん、今ちょい詰まったわ💦 もう一回送って！"

        if not OPENAI_KEY or client is None:
            ai_text = "OpenAIのキー入ってへんっぽい！一回確認して〜"
        else:
            try:
                completion = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {
                            "role": "system",
                            "content": "あなたは大阪の立ち飲み牡蠣屋の店主の相棒AI。関西弁で短めに返事して。",
                        },
                        {"role": "user", "content": text},
                    ],
                )
                ai_text = (completion.choices[0].message.content or "").strip() or ai_text
            except Exception as e:
                print("OpenAI error:", repr(e))
                ai_text = "ごめん、AI側が一瞬コケたわ💦 もっかい送って〜"

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
            print("LINE reply error:", repr(e))

    return {"ok": True}
