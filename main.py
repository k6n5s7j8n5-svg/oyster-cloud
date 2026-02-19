import os
import requests
from fastapi import FastAPI, Request
from openai import OpenAI

app = FastAPI()

LINE_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY)

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

        if not reply_token or text is None:
            continue

        # ===== AI返信 =====
        try:
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "あなたは大阪の立ち飲み牡蠣屋の店主の相棒AI。関西弁で短めに返事して。"},
                    {"role": "user", "content": text},
                ],
            )
            ai_text = response.choices[0].message.content.strip()
        except Exception as e:
            print("OpenAI error:", e)
            ai_text = "ごめん、AI側が一瞬コケたわ💦 もう一回送って〜"

        # ===== LINEへ返信 =====
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

    return {"ok": True}
