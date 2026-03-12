import os
import re
import random
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse

from linebot.v3.webhook import WebhookParser
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    PushMessageRequest,
    TextMessage,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent

import db
import threads_bot


LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")
OWNER_USER_ID = os.getenv("OWNER_USER_ID", "")
REVIEW_URL = os.getenv("REVIEW_URL", "https://g.page/r/CXCoWU0ghRcQEBM/review")
SHOP_NAME = os.getenv("SHOP_NAME", "キヨリト大阪福島店")
SHOP_AREA = os.getenv("SHOP_AREA", "大阪福島")
OPEN_HOUR = int(os.getenv("OPEN_HOUR", "16"))

JST = timezone(timedelta(hours=9))

app = FastAPI()
db.init_db()

parser = None
config = None
if LINE_CHANNEL_ACCESS_TOKEN and LINE_CHANNEL_SECRET:
    parser = WebhookParser(LINE_CHANNEL_SECRET)
    config = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)


# =========================
# 基本
# =========================

def now_jst() -> datetime:
    return datetime.now(JST)


def today_str() -> str:
    return now_jst().strftime("%Y-%m-%d")


def is_open_now() -> bool:
    return now_jst().hour >= OPEN_HOUR


def get_people() -> int:
    return int(db.get("people", "0"))


def get_oysters() -> int:
    return int(db.get("oysters", "0"))


def set_people(n: int):
    db.set("people", str(max(0, n)))


def set_oysters(n: int):
    db.set("oysters", str(max(0, n)))


def daily_reset_if_needed():
    last_reset = db.get("last_reset_date", "")
    today = today_str()
    if last_reset != today:
        db.set("people", "0")
        db.set("oysters", "0")
        db.set("last_reset_date", today)
        db.set("post1_done", "0")
        db.set("post2_done", "0")
        db.set("post3_done", "0")


@app.get("/")
def root():
    return {"ok": True}


@app.get("/healthz")
def healthz():
    daily_reset_if_needed()
    return {
        "status": "healthy",
        "date": today_str(),
        "people": get_people(),
        "oysters": get_oysters(),
    }


# =========================
# LINE送信
# =========================

def reply_text(reply_token: str, text: str):
    if not config:
        return
    try:
        with ApiClient(config) as api_client:
            api = MessagingApi(api_client)
            api.reply_message(
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[TextMessage(text=text)]
                )
            )
    except Exception as e:
        print("reply_text error:", e)


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
        print("push_text error:", e)


# =========================
# 解析
# =========================

def parse_people(text: str):
    m = re.search(r"(\d+)\s*人", text)
    return int(m.group(1)) if m else None


def parse_oysters(text: str):
    m = re.search(r"(牡蠣|残り)\s*(\d+)\s*(個)?", text)
    return int(m.group(2)) if m else None


def asks_status(text: str) -> bool:
    return text in ["状態", "いま", "今", "status"]


def asks_oysters(text: str) -> bool:
    patterns = [
        r"牡蠣.*(ある|あります|残り|在庫)",
        r"(ある|あります|残り|在庫).*(牡蠣|かき|カキ)",
        r"今日.*牡蠣",
        r"牡蠣ある",
        r"牡蠣ありますか",
    ]
    return any(re.search(p, text) for p in patterns)


def asks_crowd(text: str) -> bool:
    patterns = [
        r"混ん",
        r"空いて",
        r"すいて",
        r"何人",
        r"入れそう",
        r"席",
    ]
    return any(re.search(p, text) for p in patterns)


def asks_review(text: str) -> bool:
    patterns = [r"口コミ", r"レビュー", r"google", r"グーグル"]
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


# =========================
# 返信文
# =========================

def oyster_reply() -> str:
    oysters = get_oysters()
    if oysters <= 0:
        return "お問い合わせありがとうございます🦪\n本日の牡蠣数は確認中です。気になる場合は再度ご連絡ください。"
    return f"お問い合わせありがとうございます🦪\n現在の牡蠣は残り{oysters}個です！\n{SHOP_AREA}で牡蠣気分の方、お待ちしてます。"


def crowd_reply() -> str:
    people = get_people()
    if people <= 0:
        return "お問い合わせありがとうございます😊\n今のところかなり落ち着いてます。ふらっと入りやすいです。"
    if people <= 3:
        return f"お問い合わせありがとうございます😊\n現在は{people}人です。比較的ゆったりしてます。"
    if people <= 6:
        return f"お問い合わせありがとうございます😊\n現在は{people}人です。少しにぎわってます。"
    return f"お問い合わせありがとうございます😊\n現在は{people}人です。やや混み合ってます。"


def closed_reply() -> str:
    return f"お問い合わせありがとうございます🦪\n現在は営業時間外です。\n営業時間は毎日 {OPEN_HOUR}:00〜23:59 です。"


def default_reply() -> str:
    return f"お問い合わせありがとうございます🦪\n{SHOP_NAME}です。順番にご案内します。"


# =========================
# Threads投稿文生成
# =========================

OPENING_WORDS = [
    "今日はなんだか",
    "仕事終わりに",
    "夜のごはんで",
    "ふらっと一杯の気分なら",
    "週末のご褒美に",
    "今夜の一軒目に",
]

OYSTER_WORDS = [
    "ぷりっとした牡蠣",
    "ミルキーな牡蠣",
    "レモンが合う牡蠣",
    "海の旨味が詰まった牡蠣",
    "焼きたての牡蠣",
]

EXTRA_PHRASES = [
    "口いっぱいに海の旨味が広がります。",
    "香りだけで一杯いけそうです。",
    "ひと口食べたら気分が変わります。",
    "今夜の正解ってこれかもしれません。",
]

QUESTION_PATTERNS = [
    "生牡蠣派？焼き牡蠣派？",
    "レモン派？そのまま派？",
    "1個で止まる派？止まらん派？",
]


def ensure_keywords(text: str) -> str:
    if "大阪福島" not in text:
        text = f"大阪福島で\n{text}"
    if "牡蠣" not in text:
        text += "\n牡蠣、今夜どうですか。"
    return text.strip()


def generate_post1() -> str:
    text = (
        f"{random.choice(OPENING_WORDS)}\n"
        f"{random.choice(OYSTER_WORDS)}って、反則ですよね。🦪\n"
        f"{random.choice(EXTRA_PHRASES)}\n\n"
        f"大阪福島で牡蠣食べるなら、今夜どうですか。"
    )
    return ensure_keywords(text)


def generate_post2() -> str:
    text = (
        "大阪福島で\n"
        "仕事終わりに食べたくなる牡蠣。🦪\n\n"
        f"{random.choice(EXTRA_PHRASES)}\n"
        f"{random.choice(QUESTION_PATTERNS)}"
    )
    return ensure_keywords(text)


def generate_post3() -> str:
    text = (
        "大阪福島で\n"
        "ちょっと牡蠣食べたい夜に。🦪\n\n"
        f"{random.choice(OYSTER_WORDS)}。\n"
        "今夜の一杯と一緒にどうぞ。"
    )
    return ensure_keywords(text)


def save_daily_posts():
    db.set("post1", generate_post1())
    db.set("post2", generate_post2())
    db.set("post3", generate_post3())
    db.set("post1_done", "0")
    db.set("post2_done", "0")
    db.set("post3_done", "0")
    db.set("posts_date", today_str())


def get_posts_text() -> str:
    p1 = db.get("post1", "")
    p2 = db.get("post2", "")
    p3 = db.get("post3", "")
    return (
        f"【本日のThreads投稿案】\n\n"
        f"① 12:00\n{p1}\n\n"
        f"② 18:00\n{p2}\n\n"
        f"③ 22:30\n{p3}\n\n"
        f"修正は\n"
        f"#1 文章\n#2 文章\n#3 文章\n"
        f"で送ってください。"
    )


# =========================
# cron
# =========================

@app.get("/cron/generate-daily-posts")
def cron_generate_daily_posts():
    daily_reset_if_needed()
    save_daily_posts()
    if OWNER_USER_ID:
        push_text(OWNER_USER_ID, get_posts_text())
    return {"status": "generate posts ok"}


@app.get("/cron/post/1")
def cron_post1():
    daily_reset_if_needed()
    text = db.get("post1", "")
    if not text:
        save_daily_posts()
        text = db.get("post1", "")
    threads_bot.post_to_threads(text)
    db.set("post1_done", "1")
    return {"status": "post1 ok"}


@app.get("/cron/post/2")
def cron_post2():
    daily_reset_if_needed()
    text = db.get("post2", "")
    if not text:
        save_daily_posts()
        text = db.get("post2", "")
    threads_bot.post_to_threads(text)
    db.set("post2_done", "1")
    return {"status": "post2 ok"}


@app.get("/cron/post/3")
def cron_post3():
    daily_reset_if_needed()
    text = db.get("post3", "")
    if not text:
        save_daily_posts()
        text = db.get("post3", "")
    threads_bot.post_to_threads(text)
    db.set("post3_done", "1")
    return {"status": "post3 ok"}


@app.get("/zzz-test")
def zzz_test():
    return {"status": "zzz ok"}


# =========================
# LINE webhook
# =========================

@app.post("/callback")
async def callback(request: Request):
    daily_reset_if_needed()

    body = await request.body()
    body_text = body.decode("utf-8")
    signature = request.headers.get("X-Line-Signature", "")

    if not signature or not body_text.strip():
        return PlainTextResponse("OK")

    if not parser or not config:
        raise HTTPException(status_code=500, detail="LINE env not set")

    try:
        events = parser.parse(body_text, signature)
    except Exception as e:
        print("parse error:", e)
        raise HTTPException(status_code=400, detail="Invalid signature")

    for event in events:
        if isinstance(event, MessageEvent) and isinstance(event.message, TextMessageContent):
            text = event.message.text.strip()

            # 手動投稿
            if text.startswith("投稿 "):
                post_text = text.replace("投稿 ", "", 1).strip()
                try:
                    threads_bot.post_to_threads(post_text)
                    reply_text(event.reply_token, "Threads投稿OK")
                except Exception as e:
                    reply_text(event.reply_token, f"Threads投稿失敗: {e}")
                continue

            # 管理コマンド
            if text.startswith("#1 "):
                new_text = ensure_keywords(text[3:].strip())
                db.set("post1", new_text)
                reply_text(event.reply_token, f"1本目を更新しました。\n\n{new_text}")
                continue

            if text.startswith("#2 "):
                new_text = ensure_keywords(text[3:].strip())
                db.set("post2", new_text)
                reply_text(event.reply_token, f"2本目を更新しました。\n\n{new_text}")
                continue

            if text.startswith("#3 "):
                new_text = ensure_keywords(text[3:].strip())
                db.set("post3", new_text)
                reply_text(event.reply_token, f"3本目を更新しました。\n\n{new_text}")
                continue

            if text == "#投稿確認":
                reply_text(event.reply_token, get_posts_text())
                continue

            if text == "#今日の投稿作成":
                save_daily_posts()
                reply_text(event.reply_token, get_posts_text())
                continue

            if text == "#今すぐ1投稿":
                try:
                    threads_bot.post_to_threads(db.get("post1", ""))
                    db.set("post1_done", "1")
                    reply_text(event.reply_token, "1本目をThreads投稿しました。")
                except Exception as e:
                    reply_text(event.reply_token, f"投稿失敗: {e}")
                continue

            if text == "#今すぐ2投稿":
                try:
                    threads_bot.post_to_threads(db.get("post2", ""))
                    db.set("post2_done", "1")
                    reply_text(event.reply_token, "2本目をThreads投稿しました。")
                except Exception as e:
                    reply_text(event.reply_token, f"投稿失敗: {e}")
                continue

            if text == "#今すぐ3投稿":
                try:
                    threads_bot.post_to_threads(db.get("post3", ""))
                    db.set("post3_done", "1")
                    reply_text(event.reply_token, "3本目をThreads投稿しました。")
                except Exception as e:
                    reply_text(event.reply_token, f"投稿失敗: {e}")
                continue

            cur_people = get_people()
            cur_oysters = get_oysters()

            p = parse_people(text)
            o = parse_oysters(text)

            if p is not None:
                set_people(p)
                cur_people = p

            if o is not None:
                set_oysters(o)
                cur_oysters = o

            if asks_status(text):
                reply_text(event.reply_token, f"現在：{cur_people}人 / 牡蠣：{cur_oysters}個")
            elif (p is not None) or (o is not None):
                reply_text(event.reply_token, f"更新OK：{cur_people}人 / 牡蠣：{cur_oysters}個")
            elif not is_open_now():
                reply_text(event.reply_token, closed_reply())
            elif asks_review(text):
                reply_text(event.reply_token, f"Google口コミはこちらです🙏\n{REVIEW_URL}")
            elif asks_oysters(text):
                reply_text(event.reply_token, oyster_reply())
            elif asks_crowd(text):
                reply_text(event.reply_token, crowd_reply())
            else:
                reply_text(
                    event.reply_token,
                    "例：『今3人』『牡蠣20個』『状態』\n"
                    "投稿作成は『#今日の投稿作成』です。"
                )

    return PlainTextResponse("OK")
