import os
import re
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse

from linebot.v3.webhook import WebhookParser
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi,
    ReplyMessageRequest, TextMessage
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent

import db

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")

if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_CHANNEL_SECRET:
    # 起動はするが、Webhook受信時に弾く
    pass

app = FastAPI()
db.init_db()

parser = WebhookParser(LINE_CHANNEL_SECRET)
config = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)

@app.get("/")
def root():
    return {"ok": True}

@app.get("/healthz")
def healthz():
    return {"status": "healthy"}

def reply_text(reply_token: str, text: str):
    with ApiClient(config) as api_client:
        api = MessagingApi(api_client)
        api.reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=text)]
            )
        )

def parse_people(text: str):
    # 例: "今3人", "3人", "現在 4 人"
    m = re.search(r"(\d+)\s*人", text)
    return int(m.group(1)) if m else None

def parse_oysters(text: str):
    # 例: "牡蠣20", "牡蠣 20個", "残り15個"
    m = re.search(r"(牡蠣|残り)\s*(\d+)\s*(個)?", text)
    return int(m.group(2)) if m else None

@app.post("/callback")
async def callback(request: Request):
    if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_CHANNEL_SECRET:
        raise HTTPException(status_code=500, detail="LINE env not set")

    signature = request.headers.get("X-Line-Signature", "")
    body = await request.body()
    body_text = body.decode("utf-8")

    try:
        events = parser.parse(body_text, signature)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid signature")

    for event in events:
        if isinstance(event, MessageEvent) and isinstance(event.message, TextMessageContent):
            text = event.message.text.strip()

            # 現在値
            cur_people = int(db.get("people", "0"))
            cur_oysters = int(db.get("oysters", "0"))

            # 更新
            p = parse_people(text)
            o = parse_oysters(text)

            if p is not None:
                db.set("people", str(p))
                cur_people = p

            if o is not None:
                db.set("oysters", str(o))
                cur_oysters = o

            # 表示コマンド
            if text in ["状態", "いま", "今", "status"]:
                reply_text(event.reply_token, f"現在：{cur_people}人 / 牡蠣：{cur_oysters}個")
            elif (p is not None) or (o is not None):
                reply_text(event.reply_token, f"更新OK：{cur_people}人 / 牡蠣：{cur_oysters}個")
            else:
                reply_text(event.reply_token, "例：『今3人』『牡蠣20個』『状態』")

    return PlainTextResponse("OK")
