import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

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

# 店主 user_id（環境変数でも上書き可）
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID", "Ub39b292f75898116dec45dcc8b3bb6cc")


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


def is_status(text: str) -> bool:
    return text in ["状態", "いま", "今", "status"]


def business_is_open(jst: datetime) -> bool:
    """16:00-23:59 open, 00:00-15:59 closed"""
    return 16 <= jst.hour <= 23


def daily_reset_if_needed(jst: datetime):
    """日付が変わったら people/oysters を0に戻す"""
    today = jst.strftime("%Y-%m-%d")
    last_date = db.get("last_date", "")
    if last_date != today:
        db.set("people", "0")
        db.set("oysters", "0")
        db.set("last_date", today)
        print("Daily reset:", today)


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

    # JST now（pytz不要）
    jst_now = datetime.now(ZoneInfo("Asia/Tokyo"))
    daily_reset_if_needed(jst_now)
    is_open_now = business_is_open(jst_now)

    for event in events:
        if not (isinstance(event, MessageEvent) and isinstance(event.message, TextMessageContent)):
            continue

        user_id = getattr(getattr(event, "source", None), "user_id", None)
        print("DEBUG user_id:", user_id)

        text = event.message.text.strip()

        cur_people = int(db.get("people", "0"))
        cur_oysters = int(db.get("oysters", "0"))

        # =====================
        # 客（店主以外）
        # =====================
        if user_id != ADMIN_USER_ID:
            # 閉店時間は固定文で返す（AI呼ばない）
            if not is_open_now:
                reply_text(event.reply_token, "今日はまだ閉店中やで🙏 16時から開くで！")
                continue

            if is_status(text):
                reply_text(event.reply_token, f"現在：{cur_people}人 / 牡蠣：{cur_oysters}個")
                continue

            try:
                ai_text = await ai.reply_customer(text, cur_people, cur_oysters)
                if ai_text:
                    reply_text(event.reply_token, ai_text)
                else:
                    reply_text(event.reply_token, "今ちょいAIの返事が出えへん🙏")
            except Exception as e:
                print("AI error:", e)
                reply_text(event.reply_token, "ごめん、今AIの返事がうまく出えへん🙏")
            continue

        # =====================
        # 店主（管理コマンド）
        # ※店主は閉店時間でも更新できる
        # =====================
        if text.lower() in ["id", "userid", "whoami"]:
            reply_text(event.reply_token, f"user_id: {user_id}")
            continue

        if text.startswith("投稿 "):
            post_text = text.replace("投稿 ", "", 1).strip()
            try:
                threads_bot.post_to_threads(post_text)
                reply_text(event.reply_token, "Threads投稿OKやで")
            except Exception as e:
                print("Threads error:", e)
                reply_text(event.reply_token, "Threads投稿失敗したわ🙏（ログ見てな）")
            continue

        p = parse_people(text)
        o = parse_oysters(text)

        updated = False
        if p is not None:
            db.set("people", str(p))
            cur_people = p
            updated = True

        if o is not None:
            db.set("oysters", str(o))
            cur_oysters = o
            updated = True

        if is_status(text):
            reply_text(event.reply_token, f"現在：{cur_people}人 / 牡蠣：{cur_oysters}個")
            continue

        if updated:
            reply_text(event.reply_token, f"更新OK：{cur_people}人 / 牡蠣：{cur_oysters}個")
            continue

        # 店主もコマンド以外はAIで返す（テスト用）
        try:
            ai_text = await ai.reply_customer(text, cur_people, cur_oysters)
            reply_text(event.reply_token, ai_text or "例：『#3人』『#牡蠣10個』『状態』『投稿 文章』")
        except Exception as e:
            print("AI error:", e)
            reply_text(event.reply_token, "例：『#3人』『#牡蠣10個』『状態』『投稿 文章』")

    return PlainTextResponse("OK")
