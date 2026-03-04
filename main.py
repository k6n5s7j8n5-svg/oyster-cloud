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
import ai  # ← 追加


LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")

# 店主 user_id
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID", "Ub39b292f75898116dec45dcc8b3bb6cc")

# 機能ON/OFF（Railwayの環境変数で切替）
AI_AUTO_REPLY = os.getenv("AI_AUTO_REPLY", "1") == "1"
THREADS_AUTO_POST_ON_UPDATE = os.getenv("THREADS_AUTO_POST_ON_UPDATE", "1") == "1"
THREADS_AUTO_POST_USE_AI = os.getenv("THREADS_AUTO_POST_USE_AI", "1") == "1"


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


def is_status_command(text: str) -> bool:
    return text in ["状態", "いま", "今", "status"]


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

        # Railwayログ
        print("DEBUG user_id:", user_id)

        text = event.message.text.strip()

        # DB現状（共通で使う）
        cur_people = int(db.get("people", "0"))
        cur_oysters = int(db.get("oysters", "0"))

        # =========================
        # 1) 非店主：AI自動返信エリア
        # =========================
        if user_id != ADMIN_USER_ID:
            # 状態確認だけは誰でも見れるように（好みで外してOK）
            if is_status_command(text):
                reply_text(event.reply_token, f"現在：{cur_people}人 / 牡蠣：{cur_oysters}個")
                continue

            if AI_AUTO_REPLY:
                try:
                    ai_reply = await ai.generate_customer_reply(
                        user_text=text,
                        cur_people=cur_people,
                        cur_oysters=cur_oysters
                    )
                    if ai_reply:
                        reply_text(event.reply_token, ai_reply)
                    else:
                        reply_text(event.reply_token, "今ちょいAI返事止まってるわ🙏 店主に聞いてな！")
                except Exception as e:
                    reply_text(event.reply_token, f"ごめん、今返事うまく出えへんかった🙏（{e}）")
            else:
                reply_text(event.reply_token, "更新できるのは店主だけやで。")
            continue

        # =========================
        # 2) 店主：管理コマンドエリア
        # =========================

        # user_id確認
        if text.lower() in ["id", "userid", "whoami"]:
            reply_text(event.reply_token, f"user_id: {user_id}")
            continue

        # AI自動返信 ON/OFF（店主が切替できる）
        if text.lower() in ["ai on", "aiオン", "ai 1"]:
            db.set("ai_auto_reply", "1")
            reply_text(event.reply_token, "AI自動返信：ONにしたで")
            continue
        if text.lower() in ["ai off", "aiオフ", "ai 0"]:
            db.set("ai_auto_reply", "0")
            reply_text(event.reply_token, "AI自動返信：OFFにしたで")
            continue

        # Threads 手動投稿
        if text.startswith("投稿 "):
            post_text = text.replace("投稿 ", "", 1).strip()
            try:
                threads_bot.post_to_threads(post_text)
                reply_text(event.reply_token, "Threads投稿OKやで")
            except Exception as e:
                reply_text(event.reply_token, f"Threads投稿失敗: {e}")
            continue

        # 状態表示（店主も使える）
        if is_status_command(text):
            reply_text(event.reply_token, f"現在：{cur_people}人 / 牡蠣：{cur_oysters}個")
            continue

        # =========================
        # 3) 人数/牡蠣 更新（＋Threads自動投稿）
        # =========================
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

        if updated:
            reply_text(event.reply_token, f"更新OK：{cur_people}人 / 牡蠣：{cur_oysters}個")

            # Threads 自動投稿（更新時）
            if THREADS_AUTO_POST_ON_UPDATE:
                try:
                    if THREADS_AUTO_POST_USE_AI and os.getenv("OPENAI_API_KEY", ""):
                        post_text = await ai.generate_threads_post(
                            cur_people=cur_people,
                            cur_oysters=cur_oysters,
                            hint=None
                        )
                        # AIが空なら保険で固定文
                        if not post_text:
                            post_text = f"今の店内：{cur_people}人 / 牡蠣残り：{cur_oysters}個🦪"
                    else:
                        post_text = f"今の店内：{cur_people}人 / 牡蠣残り：{cur_oysters}個🦪"

                    threads_bot.post_to_threads(post_text)
                except Exception as e:
                    # 自動投稿は“失敗してもLINEは止めない”
                    print("Threads auto post failed:", e)

            continue

        # それ以外
        reply_text(event.reply_token, "例：『#3人』『#牡蠣10個』『状態』『投稿 文章』")

    return PlainTextResponse("OK")
