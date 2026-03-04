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
import threads_bot

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")

# ★ あなたのLINE user_id を固定したい時は Railway の環境変数に入れる
# LINE_ADMIN_USER_ID="Uxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
LINE_ADMIN_USER_ID = os.getenv("LINE_ADMIN_USER_ID", "").strip()

app = FastAPI()
db.init_db()

parser = None
config = None
if LINE_CHANNEL_ACCESS_TOKEN and LINE_CHANNEL_SECRET:
    parser = WebhookParser(LINE_CHANNEL_SECRET)
    config = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)

@app.get("/")
def root():
    return {"ok": True}

@app.get("/healthz")
def healthz():
    return {"status": "healthy"}

def reply_text(reply_token: str, text: str):
    if not config:
        return
    with ApiClient(config) as api_client:
        api = MessagingApi(api_client)
        api.reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=text)]
            )
        )

def parse_people(text: str):
    m = re.search(r"(\d+)\s*人", text)
    return int(m.group(1)) if m else None

def parse_oysters(text: str):
    m = re.search(r"(牡蠣|残り)\s*(\d+)\s*(個)?", text)
    return int(m.group(2)) if m else None

def get_user_id_from_event(event) -> str | None:
    # event.source.user_id が取れる時だけ取る
    src = getattr(event, "source", None)
    return getattr(src, "user_id", None)

@app.post("/callback")
async def callback(request: Request):
    if not (LINE_CHANNEL_ACCESS_TOKEN and LINE_CHANNEL_SECRET and parser and config):
        raise HTTPException(status_code=500, detail="LINE env not set")

    signature = request.headers.get("X-Line-Signature", "")
    body = await request.body()
    body_text = body.decode("utf-8")

    try:
        events = parser.parse(body_text, signature)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid signature")

    for event in events:
        # ★ user_id を取得してログに出す
        user_id = get_user_id_from_event(event)
        print("DEBUG user_id:", user_id)

        if isinstance(event, MessageEvent) and isinstance(event.message, TextMessageContent):
            text = event.message.text.strip()

            # ★ まずは確認用：送ったら user_id を返信で返す（あなたが1回確認するため）
            if text in ["userid", "user id", "ID", "id"]:
                reply_text(event.reply_token, f"あなたのuser_idはこれやで：{user_id}")
                continue

            # ★（任意）管理者IDを設定したら、管理者以外は更新できない
            # まだ未設定なら全員OKのまま
            if LINE_ADMIN_USER_ID and user_id and user_id != LINE_ADMIN_USER_ID:
                reply_text(event.reply_token, "更新できるのは店主だけやで。")
                continue

            if text.startswith("投稿 "):
                post_text = text.replace("投稿 ", "", 1).strip()
                try:
                    threads_bot.post_to_threads(post_text)
                    reply_text(event.reply_token, "Threads投稿OK")
                except Exception as e:
                    reply_text(event.reply_token, f"Threads投稿失敗: {e}")
                continue

            cur_people = int(db.get("people", "0"))
            cur_oysters = int(db.get("oysters", "0"))

            p = parse_people(text)
            o = parse_oysters(text)

            if p is not None:
                db.set("people", str(p))
                cur_people = p
            if o is not None:
                db.set("oysters", str(o))
                cur_oysters = o

            if text in ["状態", "いま", "今", "status"]:
                reply_text(event.reply_token, f"現在：{cur_people}人 / 牡蠣：{cur_oysters}個")
            elif (p is not None) or (o is not None):
                reply_text(event.reply_token, f"更新OK：{cur_people}人 / 牡蠣：{cur_oysters}個")
            else:
                reply_text(event.reply_token, "例：『今3人』『牡蠣20個』『状態』")

    return PlainTextResponse("OK")
