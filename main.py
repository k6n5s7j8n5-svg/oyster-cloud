import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse

from linebot.v3.webhook import WebhookParser
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi,
    ReplyMessageRequest, PushMessageRequest, TextMessage
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent

import db
import threads_bot

try:
    import ai
    AI_OK = True
except:
    AI_OK = False


LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")

ADMIN_USER_ID = "Ub39b292f75898116dec45dcc8b3bb6cc"

JST = ZoneInfo("Asia/Tokyo")

app = FastAPI()
db.init_db()

parser = WebhookParser(LINE_CHANNEL_SECRET)
config = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)


def reply_text(reply_token, text):

    with ApiClient(config) as api_client:

        MessagingApi(api_client).reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=text)]
            )
        )


def push_text(user_id, text):

    with ApiClient(config) as api_client:

        MessagingApi(api_client).push_message(
            PushMessageRequest(
                to=user_id,
                messages=[TextMessage(text=text)]
            )
        )


def get_name(user_id):

    try:

        with ApiClient(config) as api_client:

            profile = MessagingApi(api_client).get_profile(user_id)

            return profile.display_name

    except:

        return "名前不明"


def parse_people(text):

    text = text.replace("#", "")

    m = re.search(r"(\d+)人", text)

    return int(m.group(1)) if m else None


def parse_oysters(text):

    text = text.replace("#", "")

    m = re.search(r"(牡蠣|残り)(\d+)", text)

    return int(m.group(2)) if m else None


def is_open(now):

    if now.weekday() == 1:
        return False

    return 16 <= now.hour <= 23


def reset_day(now):

    today = now.strftime("%Y-%m-%d")

    if db.get("last_date") != today:

        db.set("people", "0")
        db.set("oysters", "0")
        db.set("last_date", today)


async def ai_reply(text, people, oysters):

    if not AI_OK:
        return ""

    try:
        return await ai.reply_customer(text, people, oysters)
    except:
        return ""


@app.post("/callback")
async def callback(request: Request):

    signature = request.headers.get("X-Line-Signature")

    body = await request.body()

    events = parser.parse(body.decode(), signature)

    now = datetime.now(JST)

    reset_day(now)

    for event in events:

        if not isinstance(event, MessageEvent):
            continue

        if not isinstance(event.message, TextMessageContent):
            continue

        user_id = event.source.user_id

        text = event.message.text.strip()

        people = int(db.get("people", "0"))

        oysters = int(db.get("oysters", "0"))

        owner = user_id == ADMIN_USER_ID

        # ===== 客 =====

        if not owner:

            t = text.lower()

            # 通知

            if "牡蠣" in t or "何人" in t or "混" in t:

                name = get_name(user_id)

                push_text(
                    ADMIN_USER_ID,
                    f"問い合わせ\n{name}\n{text}"
                )

            # 定休日

            if now.weekday() == 1:

                reply_text(
                    event.reply_token,
                    "今日は定休日（火曜日）やで🙏"
                )

                continue

            # 営業時間

            if not is_open(now):

                reply_text(
                    event.reply_token,
                    "営業時間は16:00〜24:00やで！"
                )

                continue

            # ===== 高速返信 =====

            if "何人" in t or "混" in t:

                reply_text(
                    event.reply_token,
                    f"今は店内{people}人くらいやで！"
                )

                continue

            if "牡蠣" in t or "何個" in t or "残り" in t:

                if oysters == 0:

                    reply_text(
                        event.reply_token,
                        "今日は牡蠣完売してもうた🙏"
                    )

                else:

                    reply_text(
                        event.reply_token,
                        f"牡蠣は残り{oysters}個くらいやで🦪"
                    )

                continue

            if "営業時間" in t or "何時" in t:

                reply_text(
                    event.reply_token,
                    "営業時間は16:00〜24:00やで！"
                )

                continue

            if "定休日" in t or "休み" in t:

                reply_text(
                    event.reply_token,
                    "火曜日が定休日やで！"
                )

                continue

            if "場所" in t or "どこ" in t or "住所" in t:

                reply_text(
                    event.reply_token,
                    "大阪市福島区福島5丁目12-17\nサンフラット南側1F\n黄色い提灯が目印やで！"
                )

                continue

            # ===== AI =====

            ans = await ai_reply(text, people, oysters)

            if ans:

                reply_text(event.reply_token, ans)

            else:

                reply_text(
                    event.reply_token,
                    "ちょっと今AI調子悪い🙏"
                )

            continue

        # ===== 店主 =====

        if text.startswith("投稿 "):

            msg = text.replace("投稿 ", "")

            threads_bot.post_to_threads(msg)

            reply_text(
                event.reply_token,
                "Threads投稿OK"
            )

            continue

        p = parse_people(text)

        o = parse_oysters(text)

        if p is not None:

            db.set("people", str(p))
            people = p

        if o is not None:

            db.set("oysters", str(o))
            oysters = o

        if p or o:

            reply_text(
                event.reply_token,
                f"更新OK\n人数:{people}\n牡蠣:{oysters}"
            )

            continue

        ans = await ai_reply(text, people, oysters)

        reply_text(
            event.reply_token,
            ans or "例：#3人 #牡蠣50"
        )

    return PlainTextResponse("OK")
