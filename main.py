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
except Exception as e:
    print("AI import error:", e)
    AI_OK = False


LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")

ADMIN_USER_ID = "Ub39b292f75898116dec45dcc8b3bb6cc"

JST = ZoneInfo("Asia/Tokyo")

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


def push_text(user_id: str, text: str):
    if not config:
        return

    with ApiClient(config) as api_client:
        api = MessagingApi(api_client)

        api.push_message(
            PushMessageRequest(
                to=user_id,
                messages=[TextMessage(text=text)]
            )
        )


def get_display_name(user_id: str):
    try:
        with ApiClient(config) as api_client:
            api = MessagingApi(api_client)
            profile = api.get_profile(user_id)
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


def is_status(text):
    return text in ["状態", "今", "いま", "status"]


def is_open(now):
    return 16 <= now.hour <= 23


def reset_if_new_day(now):
    today = now.strftime("%Y-%m-%d")
    last = db.get("last_date", "")

    if today != last:
        db.set("people", "0")
        db.set("oysters", "0")
        db.set("last_date", today)
        print("日付リセット")


def inventory_question(text):

    t = text.lower()

    if "牡蠣" in t:
        return True
    if "何個" in t:
        return True
    if "残り" in t:
        return True
    if "何人" in t:
        return True
    if "混" in t:
        return True

    return False


async def ai_reply(text, people, oysters):

    if not AI_OK:
        return ""

    try:
        return await ai.reply_customer(text, people, oysters)
    except Exception as e:
        print("AI error", e)
        return ""


@app.post("/callback")
async def callback(request: Request):

    if not (LINE_CHANNEL_ACCESS_TOKEN and LINE_CHANNEL_SECRET):
        raise HTTPException(status_code=500)

    signature = request.headers.get("X-Line-Signature", "")

    body = await request.body()
    body_text = body.decode("utf-8")

    events = parser.parse(body_text, signature)

    now = datetime.now(JST)

    reset_if_new_day(now)

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

        print("user:", user_id, text)

        # ===== 客 =====

        if not owner:

            if inventory_question(text):

                name = get_display_name(user_id)

                push_text(
                    ADMIN_USER_ID,
                    f"問い合わせ\n{name}\n{text}\n人数:{people}\n牡蠣:{oysters}"
                )

            if not is_open(now):

                reply_text(
                    event.reply_token,
                    "ただいま閉店中やで🙏16時から開くで！"
                )

                continue

            # ===== 高速返信 =====

            t = text.lower()

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
                        "今日は牡蠣完売してもうた🙏また明日な！"
                    )

                else:

                    reply_text(
                        event.reply_token,
                        f"牡蠣は残り{oysters}個くらいやで🦪"
                    )

                continue

            if is_status(text):

                reply_text(
                    event.reply_token,
                    f"現在：{people}人 / 牡蠣{oysters}個"
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

        if is_status(text):

            reply_text(
                event.reply_token,
                f"現在：{people}人 / 牡蠣{oysters}個"
            )

            continue

        ans = await ai_reply(text, people, oysters)

        reply_text(
            event.reply_token,
            ans or "例: #3人 #牡蠣50"
        )

    return PlainTextResponse("OK")
