import os
import re
import json
import random
import requests
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
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

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


def status_reply() -> str:
    return f"現在は{get_people()}人 / 牡蠣は{get_oysters()}個です。"


def fallback_ai_reply(kind: str) -> str:
    if kind == "oyster":
        return oyster_reply()
    if kind == "crowd":
        return crowd_reply()
    if kind == "closed":
        return closed_reply()
    if kind == "status":
        return status_reply()
    if kind == "review":
        return f"Google口コミはこちらです🙏\n{REVIEW_URL}"
    return default_reply()


def ai_shop_reply(kind: str, user_text: str = "") -> str:
    if not OPENAI_API_KEY:
        return fallback_ai_reply(kind)

    people = get_people()
    oysters = get_oysters()
    open_now = is_open_now()

    facts = {
        "shop_name": SHOP_NAME,
        "shop_area": SHOP_AREA,
        "open_hour": OPEN_HOUR,
        "is_open_now": open_now,
        "people_count": people,
        "oyster_count": oysters,
        "review_url": REVIEW_URL,
        "kind": kind,
        "user_text": user_text,
    }

    system_prompt = f"""
あなたは{SHOP_AREA}にある小さな立ち飲み牡蠣屋「{SHOP_NAME}」の店主です。
お客さんへのLINE返信を作ってください。

ルール:
- 軽い関西弁
- 店っぽく自然に
- 短めで読みやすく
- 数字や営業時間は与えられた事実をそのまま使う
- 嘘を書かない
- 長すぎる宣伝はしない
- 必要なら🦪や😊を少しだけ使う
- 返答文だけ返す
- Markdownの ** は使わない
""".strip()

    user_prompt = f"""
以下の事実を使って、お客さんへの返信を1本作ってください。

事実:
{json.dumps(facts, ensure_ascii=False)}

kind の意味:
- oyster: 牡蠣の在庫についての問い合わせ
- crowd: 混雑・人数についての問い合わせ
- closed: 営業時間外の問い合わせ
- status: 店の現在状態の確認
- review: 口コミURLの案内
- default: 通常の案内
""".strip()

    payload = {
        "model": "gpt-4.1-mini",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.8,
    }

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()
        content = data["choices"][0]["message"]["content"].strip()
        return content if content else fallback_ai_reply(kind)
    except Exception as e:
        print("ai_shop_reply error:", e)
        return fallback_ai_reply(kind)


# =========================
# Threads投稿文生成
# =========================

def ensure_keywords(text: str) -> str:
    text = text.strip()
    if "大阪福島" not in text:
        text = f"大阪福島で\n{text}"
    if "牡蠣" not in text:
        text += "\n牡蠣、今夜どうです？🦪"
    return text.strip()


def call_openai_for_posts() -> dict:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY が未設定です")

    system_prompt = f"""
あなたは{SHOP_AREA}にある小さな立ち飲み牡蠣屋「{SHOP_NAME}」のSNS担当です。
Threads投稿を3本作ってください。

ルール:
- 軽い関西弁
- めちゃくちゃ店っぽい口調
- 宣伝くさすぎない
- 牡蠣が食べたくなる
- 短めで読みやすい
- 「大阪福島」を自然に入れる
- 絵文字は🦪をたまに使う
- 1本ごとに少しトーンを変える
- 昼(12:00)、夕方(18:00)、夜(22:30)向けに作る
- ハッシュタグは不要
- JSONだけ返す
- キーは post1, post2, post3

出力形式:
{{
  "post1": "本文",
  "post2": "本文",
  "post3": "本文"
}}
""".strip()

    user_prompt = """
今日のThreads投稿を3本ください。
店の雰囲気は「小さな立ち飲み・ふらっと寄れる・牡蠣と一杯がうまい」です。
""".strip()

    payload = {
        "model": "gpt-4.1-mini",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.9,
    }

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    r = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers=headers,
        json=payload,
        timeout=60,
    )
    r.raise_for_status()
    data = r.json()

    content = data["choices"][0]["message"]["content"].strip()

    if content.startswith("```"):
        content = content.replace("```json", "").replace("```", "").strip()

    posts = json.loads(content)

    return {
        "post1": ensure_keywords(posts["post1"]),
        "post2": ensure_keywords(posts["post2"]),
        "post3": ensure_keywords(posts["post3"]),
    }


def fallback_posts() -> dict:
    return {
        "post1": ensure_keywords("今日なんか牡蠣いっときたない？🦪\n海の旨味ぎゅっと詰まったやつ、ええ感じで入ってます。"),
        "post2": ensure_keywords("仕事終わりに牡蠣つまんで一杯どうです？🦪\n大阪福島でゆるっと待ってます。"),
        "post3": ensure_keywords("ちょっと牡蠣の口なってる夜ちゃいます？🦪\n今夜の一杯と一緒にどうぞ。"),
    }


def save_daily_posts():
    try:
        posts = call_openai_for_posts()
    except Exception as e:
        print("AI投稿生成失敗:", e)
        posts = fallback_posts()

    db.set("post1", posts["post1"])
    db.set("post2", posts["post2"])
    db.set("post3", posts["post3"])
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
                reply_text(event.reply_token, ai_shop_reply("status", text))
            elif (p is not None) or (o is not None):
                reply_text(event.reply_token, f"更新OK：{cur_people}人 / 牡蠣：{cur_oysters}個")
            elif not is_open_now():
                reply_text(event.reply_token, ai_shop_reply("closed", text))
            elif asks_review(text):
                reply_text(event.reply_token, ai_shop_reply("review", text))
            elif asks_oysters(text):
                reply_text(event.reply_token, ai_shop_reply("oyster", text))
            elif asks_crowd(text):
                reply_text(event.reply_token, ai_shop_reply("crowd", text))
            else:
                reply_text(event.reply_token, ai_shop_reply("default", text))

    return PlainTextResponse("OK")
