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
    ai = None


LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID", "Ub39b292f75898116dec45dcc8b3bb6cc")

JST = ZoneInfo("Asia/Tokyo")

REVIEW_URL = "https://g.page/r/CXCoWU0ghRcQEBM/review"

SHOP_HOURS_TEXT = "営業時間は16:00〜24:00やで！\n火曜日は定休日やで！"
SHOP_ADDRESS_TEXT = "大阪市福島区福島5丁目12-17\nサンフラット南側1F\n黄色い提灯が目印やで！"
SHOP_CLOSED_TEXT = "ただいま閉店中やで🙏 16時から開くで！"
SHOP_HOLIDAY_TEXT = "今日は定休日（火曜日）やで🙏"
SHOP_SOLD_OUT_TEXT = "今日は牡蠣完売してもうた🙏 また明日待ってるで！"

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
    if not config or not user_id:
        return
    try:
        with ApiClient(config) as api_client:
            api = MessagingApi(api_client)
            api.push_message(
                PushMessageRequest(
                    to=user_id,
                    messages=[TextMessage(text=text)]
                )
            )
    except Exception as e:
        print("push_message error:", e)


def get_display_name(user_id: str) -> str:
    if not config or not user_id:
        return "名前不明"
    try:
        cached = db.get(f"name:{user_id}", "")
        if cached:
            return cached

        with ApiClient(config) as api_client:
            api = MessagingApi(api_client)
            profile = api.get_profile(user_id)
            name = getattr(profile, "display_name", "") or "名前不明"
            db.set(f"name:{user_id}", name)
            return name
    except Exception as e:
        print("get_profile error:", e)
        return "名前不明"


def is_first_user(user_id: str) -> bool:
    key = f"visited:{user_id}"
    visited = db.get(key, "")
    if visited:
        return False
    db.set(key, "1")
    return True


def send_review_if_first(user_id: str):
    if is_first_user(user_id):
        push_text(
            user_id,
            "はじめまして！キヨリト大阪福島店です🦪\n\n"
            "もしよかったらGoogle口コミもお願いできます🙏\n"
            f"{REVIEW_URL}"
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
    return text in ["状態", "今", "いま", "status"]


def is_open(now: datetime) -> bool:
    if now.weekday() == 1:
        return False
    return 16 <= now.hour <= 23


def is_holiday(now: datetime) -> bool:
    return now.weekday() == 1


def reset_if_new_day(now: datetime):
    today = now.strftime("%Y-%m-%d")
    last_date = db.get("last_date", "")
    if last_date != today:
        db.set("people", "0")
        db.set("oysters", "0")
        db.set("last_date", today)
        print("日付変わったのでリセット:", today)


def needs_notify_inventory_or_people(text: str) -> bool:
    t = text.lower().replace("？", "").replace("?", "")
    if "牡蠣" in t or "かき" in t or "何個" in t or "なんこ" in t or "残り" in t:
        return True
    if "何人" in t or "なんにん" in t or "混" in t or "空い" in t or "店内" in t:
        return True
    return False


def is_address_question(text: str) -> bool:
    t = text.lower().replace("？", "").replace("?", "")
    return ("住所" in t) or ("場所" in t) or ("どこ" in t) or ("行き方" in t)


def is_hours_question(text: str) -> bool:
    t = text.lower().replace("？", "").replace("?", "")
    return (
        ("営業時間" in t)
        or ("何時" in t)
        or ("なんじ" in t)
        or ("オープン" in t)
        or ("開店" in t)
        or ("閉店" in t)
        or ("何時から" in t)
        or ("何時まで" in t)
    )


def is_holiday_question(text: str) -> bool:
    t = text.lower().replace("？", "").replace("?", "")
    return ("定休日" in t) or ("休み" in t) or ("休みの日" in t)


def is_people_question(text: str) -> bool:
    t = text.lower().replace("？", "").replace("?", "")
    return ("何人" in t) or ("なんにん" in t) or ("混" in t) or ("空い" in t) or ("店内" in t)


def is_oyster_question(text: str) -> bool:
    t = text.lower().replace("？", "").replace("?", "")
    return ("牡蠣" in t) or ("かき" in t) or ("何個" in t) or ("なんこ" in t) or ("残り" in t)


async def safe_ai_reply(text: str, people: int, oysters: int) -> str:
    if not AI_OK or ai is None:
        return ""
    try:
        return await ai.reply_customer(text, people, oysters)
    except Exception as e:
        print("AI error:", e)
        return ""


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

    now = datetime.now(JST)
    reset_if_new_day(now)

    for event in events:
        if not isinstance(event, MessageEvent):
            continue
        if not isinstance(event.message, TextMessageContent):
            continue

        user_id = getattr(getattr(event, "source", None), "user_id", None)
        text = event.message.text.strip()

        people = int(db.get("people", "0"))
        oysters = int(db.get("oysters", "0"))
        owner = user_id == ADMIN_USER_ID

        print("DEBUG user:", user_id)
        print("DEBUG text:", text)

        # 客
        if not owner:
            # 新規ユーザーには口コミURLを送る
            send_review_if_first(user_id)

            # 牡蠣 / 人数問い合わせは店主に通知
            if needs_notify_inventory_or_people(text):
                name = get_display_name(user_id)
                push_text(
                    ADMIN_USER_ID,
                    f"【問い合わせ】\n{name}\nuser_id: {user_id}\n内容: {text}\n現在: {people}人 / 牡蠣: {oysters}個"
                )

            # 住所
            if is_address_question(text):
                reply_text(event.reply_token, SHOP_ADDRESS_TEXT)
                continue

            # 営業時間
            if is_hours_question(text):
                if is_holiday(now):
                    reply_text(
                        event.reply_token,
                        f"{SHOP_HOURS_TEXT}\n今日は定休日（火曜日）やで！"
                    )
                elif is_open(now):
                    reply_text(
                        event.reply_token,
                        f"今は営業中やで！\n{SHOP_HOURS_TEXT}"
                    )
                else:
                    reply_text(
                        event.reply_token,
                        f"今は閉店中やで🙏\n{SHOP_HOURS_TEXT}"
                    )
                continue

            # 定休日
            if is_holiday_question(text):
                reply_text(event.reply_token, "定休日は火曜日やで！")
                continue

            # 定休日そのもの
            if is_holiday(now):
                reply_text(event.reply_token, SHOP_HOLIDAY_TEXT)
                continue

            # 営業時間外
            if not is_open(now):
                reply_text(event.reply_token, SHOP_CLOSED_TEXT)
                continue

            # 状態
            if is_status(text):
                reply_text(event.reply_token, f"現在：{people}人 / 牡蠣：{oysters}個")
                continue

            # 高速返信：人数
            if is_people_question(text):
                reply_text(event.reply_token, f"今は店内{people}人くらいやで！")
                continue

            # 高速返信：牡蠣
            if is_oyster_question(text):
                if oysters == 0:
                    reply_text(event.reply_token, SHOP_SOLD_OUT_TEXT)
                else:
                    reply_text(event.reply_token, f"牡蠣は残り{oysters}個くらいやで🦪")
                continue

            # AI接客
            ans = await safe_ai_reply(text, people, oysters)
            if ans:
                reply_text(event.reply_token, ans)
            else:
                reply_text(event.reply_token, "ちょっと今AI調子悪い🙏")
            continue

        # 店主
        if text.lower() in ["id", "userid", "whoami"]:
            reply_text(event.reply_token, f"user_id: {user_id}")
            continue

        if text.startswith("投稿 "):
            msg = text.replace("投稿 ", "", 1).strip()
            try:
                threads_bot.post_to_threads(msg)
                reply_text(event.reply_token, "Threads投稿OK")
            except Exception as e:
                print("Threads error:", e)
                reply_text(event.reply_token, "Threads投稿失敗したわ🙏")
            continue

        p = parse_people(text)
        o = parse_oysters(text)

        updated = False

        if p is not None:
            db.set("people", str(p))
            people = p
            updated = True

        if o is not None:
            db.set("oysters", str(o))
            oysters = o
            updated = True

        if updated:
            reply_text(event.reply_token, f"更新OK\n人数:{people}\n牡蠣:{oysters}")
            continue

        if is_status(text):
            reply_text(event.reply_token, f"現在：{people}人 / 牡蠣：{oysters}個")
            continue

        if is_address_question(text):
            reply_text(event.reply_token, SHOP_ADDRESS_TEXT)
            continue

        if is_hours_question(text):
            reply_text(event.reply_token, SHOP_HOURS_TEXT)
            continue

        if is_holiday_question(text):
            reply_text(event.reply_token, "定休日は火曜日やで！")
            continue

        ans = await safe_ai_reply(text, people, oysters)
        reply_text(event.reply_token, ans or "例：#3人 #牡蠣50")

    return PlainTextResponse("OK")
