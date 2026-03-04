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
import ai


LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")

# 店主 user_id
ADMIN_USER_ID = "Ub39b292f75898116dec45dcc8b3bb6cc"


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
    text = text.replace("#", "")
    m = re.search(r"(\d+)\s*人", text)
    return int(m.group(1)) if m else None


def parse_oysters(text: str):
    text = text.replace("#", "")
    m = re.search(r"(牡蠣|残り)\s*(\d+)\s*(個)?", text)
    return int(m.group(2)) if m else None


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

        if not (isinstance(event, MessageEvent) and isinstance(event.message, TextMessageContent)):
            continue

        user_id = None
        if hasattr(event, "source") and hasattr(event.source, "user_id"):
            user_id = event.source.user_id

        print("DEBUG user_id:", user_id)

        text = event.message.text.strip()

        cur_people = int(db.get("people", "0"))
        cur_oysters = int(db.get("oysters", "0"))

        # =========================
        # 店主以外：AI返信
        # =========================
        if user_id != ADMIN_USER_ID:

            if text in ["状態", "いま", "今", "status"]:
                reply_text(event.reply_token, f"現在：{cur_people}人 / 牡蠣：{cur_oysters}個")
                continue

            try:
                ai_text = await ai.reply_customer(text, cur_people, cur_oysters)

                if ai_text:
                    reply_text(event.reply_token, ai_text)
                else:
                    reply_text(event.reply_token, "今ちょいAIの返事が出えへん🙏")

            except Exception as e:
                reply_text(event.reply_token, f"AIエラー: {e}")

            continue

        # =========================
        # 店主コマンド
        # =========================

        if text.lower() in ["id", "userid", "whoami"]:
            reply_text(event.reply_token, f"user_id: {user_id}")
            continue


        # Threads投稿
        if text.startswith("投稿 "):
            post_text = text.replace("投稿 ", "", 1).strip()

            try:
                threads_bot.post_to_threads(post_text)
                reply_text(event.reply_token, "Threads投稿OKやで")
            except Exception as e:
                reply_text(event.reply_token, f"Threads投稿失敗: {e}")

            continue


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
            # ★ 店主でもAI返信できるようにする
            try:
    　　　　　　　ai_text = await ai.reply_customer(text, cur_people, cur_oysters)

    　　　　　　　if ai_text:
        　　　　　　　reply_text(event.reply_token, ai_text)
    　　　　　　　else:
       　　　　　　　 reply_text(event.reply_token, "今ちょいAIの返事が出えへん🙏")

　　　　　　　except Exception as e:
    　　　　　　　print("AI exception:", e)
    　　　　　　　reply_text(event.reply_token, "ごめん、今AIの返事がうまく出えへん🙏 ちょい後でもっかい送ってな！")

    return PlainTextResponse("OK")
