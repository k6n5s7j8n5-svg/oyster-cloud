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

# ====== LINE env ======
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")

# ★店主の user_id（今ログに出たやつ）
# もし後で環境変数にしたいなら:
# ADMIN_USER_ID = os.getenv("LINE_ADMIN_USER_ID", "").strip()
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
    """
    例:
      "今3人" / "3人" / "現在 4 人" / "#3人"
    """
    # # を許容
    text = text.replace("#", "")
    m = re.search(r"(\d+)\s*人", text)
    return int(m.group(1)) if m else None


def parse_oysters(text: str):
    """
    例:
      "牡蠣20" / "牡蠣 20個" / "残り15個" / "#牡蠣10"
    """
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
        # テキスト以外はスルー
        if not (isinstance(event, MessageEvent) and isinstance(event.message, TextMessageContent)):
            continue

        # ====== user_id 取得 ======
        user_id = None
        if hasattr(event, "source") and hasattr(event.source, "user_id"):
            user_id = event.source.user_id

        # Railwayログに出す
        print("DEBUG user_id:", user_id)

        text = (event.message.text or "").strip()

        # ====== 店主以外は拒否 ======
        if user_id != ADMIN_USER_ID:
            # 返信できるなら返す（店主以外は更新不可）
            if hasattr(event, "reply_token") and event.reply_token:
                reply_text(
                    event.reply_token,
                    f"更新できるのは店主だけやで。\n(user_id: {user_id})"
                )
            continue

        # ====== 店主だけここから先に進む ======

        # user_id確認用コマンド（必要なら）
        if text.lower() in ["id", "userid", "user_id", "whoami", "わたしだれ", "誰"]:
            reply_text(event.reply_token, f"店主やで。\n(user_id: {user_id})")
            continue

        # Threads投稿コマンド
        # 例: "投稿 今日は牡蠣が最高やで"
        if text.startswith("投稿 "):
            post_text = text.replace("投稿 ", "", 1).strip()
            try:
                threads_bot.post_to_threads(post_text)
                reply_text(event.reply_token, f"Threads投稿OKやで。\n(user_id: {user_id})")
            except Exception as e:
                reply_text(event.reply_token, f"Threads投稿失敗や…: {e}\n(user_id: {user_id})")
            continue

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
            reply_text(event.reply_token, f"現在：{cur_people}人 / 牡蠣：{cur_oysters}個\n(user_id: {user_id})")
        elif (p is not None) or (o is not None):
            reply_text(event.reply_token, f"更新OK：{cur_people}人 / 牡蠣：{cur_oysters}個\n(user_id: {user_id})")
        else:
            reply_text(
                event.reply_token,
                "例：『#3人』『#牡蠣10個』『状態』『投稿 文章』\n"
                f"(user_id: {user_id})"
            )

    return PlainTextResponse("OK")
