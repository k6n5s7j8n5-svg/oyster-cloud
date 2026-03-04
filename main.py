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

# ai.py が無くても起動できるようにする（重要）
try:
    import ai
    AI_OK = True
except Exception as e:
    print("AI import failed:", e)
    ai = None
    AI_OK = False


LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")

# 店主 user_id（環境変数でも上書き可に）
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
    """LINEに返信"""
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


async def ai_reply(user_text: str, cur_people: int, cur_oysters: int) -> str:
    """AI返信（失敗しても例外を投げない）"""
    if not (AI_OK and ai is not None):
        return ""

    try:
        return await ai.reply_customer(user_text, cur_people, cur_oysters)
    except Exception as e:
        print("AI exception:", e)
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

    for event in events:
        if not (isinstance(event, MessageEvent) and isinstance(event.message, TextMessageContent)):
            continue

        user_id = getattr(getattr(event, "source", None), "user_id", None)
        print("DEBUG user_id:", user_id)

        text = event.message.text.strip()

        # 現在値（AIにも使う）
        cur_people = int(db.get("people", "0"))
        cur_oysters = int(db.get("oysters", "0"))

        # =========================
        # 店主以外：AI返信（＋状態は全員OK）
        # =========================
        if user_id != ADMIN_USER_ID:
            if is_status(text):
                reply_text(event.reply_token, f"現在：{cur_people}人 / 牡蠣：{cur_oysters}個")
                continue

            ans = await ai_reply(text, cur_people, cur_oysters)
            if ans:
                reply_text(event.reply_token, ans)
            else:
                reply_text(event.reply_token, "ごめん、今AIの返事がうまく出えへん🙏 ちょい後でもっかい送ってな！")
            continue

        # =========================
        # 店主：管理コマンド
        # =========================

        # user_id確認
        if text.lower() in ["id", "userid", "whoami"]:
            reply_text(event.reply_token, f"user_id: {user_id}")
            continue

        # Threads手動投稿
        if text.startswith("投稿 "):
            post_text = text.replace("投稿 ", "", 1).strip()
            try:
                threads_bot.post_to_threads(post_text)
                reply_text(event.reply_token, "Threads投稿OKやで")
            except Exception as e:
                print("Threads error:", e)
                reply_text(event.reply_token, "Threads投稿失敗したわ🙏（ログ見てな）")
            continue

        # 人数/牡蠣の更新
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

        # =========================
        # 店主：コマンド以外はAI返信（テスト用）
        # =========================
        ans = await ai_reply(text, cur_people, cur_oysters)
        if ans:
            reply_text(event.reply_token, ans)
        else:
            reply_text(event.reply_token, "例：『#3人』『#牡蠣10個』『状態』『投稿 文章』")

    return PlainTextResponse("OK")
