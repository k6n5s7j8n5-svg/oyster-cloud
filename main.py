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

# AIは落ちてもサーバーを落とさない（重要）
try:
    import ai
    AI_OK = True
except Exception as e:
    print("AI import failed:", e)
    ai = None
    AI_OK = False


LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")

# 店主 user_id（環境変数で上書き可）
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID", "Ub39b292f75898116dec45dcc8b3bb6cc")

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


def push_text(to_user_id: str, text: str):
    """店主にプッシュ通知（reply_token不要）"""
    if not config or not to_user_id:
        return
    try:
        with ApiClient(config) as api_client:
            api = MessagingApi(api_client)
            api.push_message(
                PushMessageRequest(
                    to=to_user_id,
                    messages=[TextMessage(text=text)]
                )
            )
    except Exception as e:
        print("push_message error:", e)


def get_display_name(user_id: str) -> str:
    """LINE表示名（取れない場合は空）"""
    if not config or not user_id:
        return ""
    try:
        with ApiClient(config) as api_client:
            api = MessagingApi(api_client)
            profile = api.get_profile(user_id)
            return getattr(profile, "display_name", "") or ""
    except Exception as e:
        # グループ等で取れないことがある
        print("get_profile error:", e)
        return ""


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


def business_is_open(jst_now: datetime) -> bool:
    """00:00〜15:59 閉店 / 16:00〜23:59 通常"""
    return 16 <= jst_now.hour <= 23


def daily_reset_if_needed(jst_now: datetime):
    """日付が変わったら people/oysters を0に戻す"""
    today = jst_now.strftime("%Y-%m-%d")
    last_date = db.get("last_date", "")
    if last_date != today:
        db.set("people", "0")
        db.set("oysters", "0")
        db.set("last_date", today)
        print("Daily reset:", today)


def is_inventory_or_people_question(text: str) -> bool:
    """牡蠣個数 / 人数の問い合わせっぽい文か判定（雑に強め）"""
    t = text.strip().lower().replace("？", "").replace("?", "")
    # 人数系
    if ("何人" in t) or ("なんにん" in t) or ("混" in t) or ("空い" in t) or ("店内" in t):
        return True
    # 牡蠣/個数系
    if ("牡蠣" in t) or ("かき" in t) or ("何個" in t) or ("なんこ" in t) or ("残り" in t):
        return True
    return False


async def safe_ai_reply(user_text: str, cur_people: int, cur_oysters: int) -> str:
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

    # JST now（ここで日付リセットしておく）
    jst_now = datetime.now(JST)
    daily_reset_if_needed(jst_now)
    is_open_now = business_is_open(jst_now)

    for event in events:
        if not (isinstance(event, MessageEvent) and isinstance(event.message, TextMessageContent)):
            continue

        user_id = getattr(getattr(event, "source", None), "user_id", None)
        text = event.message.text.strip()

        print("DEBUG user_id:", user_id)
        print("DEBUG text:", text)

        cur_people = int(db.get("people", "0"))
        cur_oysters = int(db.get("oysters", "0"))

        is_owner = (user_id == ADMIN_USER_ID)

        # =====================
        # 客（店主以外）
        # =====================
        if not is_owner:
            # ① 人数/牡蠣の問い合わせが来たら店主に通知（表示名も）
            if is_inventory_or_people_question(text):
                name = get_display_name(user_id) or "（表示名不明）"
                push_text(
                    ADMIN_USER_ID,
                    f"【問い合わせ】{name}\nuser_id: {user_id}\n内容: {text}\n現在: {cur_people}人 / 牡蠣: {cur_oysters}個"
                )

            # ② 閉店時間は固定で閉店返信（AI呼ばない）
            #    ※「客だけ」閉店扱いでOK、という要件通り
            if not is_open_now:
                reply_text(event.reply_token, "ただいま閉店中やで🙏 16時から開くで！")
                continue

            # ③ 状態コマンド（営業時間中）
            if is_status(text):
                reply_text(event.reply_token, f"現在：{cur_people}人 / 牡蠣：{cur_oysters}個")
                continue

            # ④ AI返信（営業時間中）
            ans = await safe_ai_reply(text, cur_people, cur_oysters)
            if ans:
                reply_text(event.reply_token, ans)
            else:
                reply_text(event.reply_token, "ごめん、今AIの返事がうまく出えへん🙏 ちょい後でもっかい送ってな！")
            continue

        # =====================
        # 店主（管理コマンド）
        # ※店主は閉店中でも更新できる
        # =====================

        if text.lower() in ["id", "userid", "whoami"]:
            reply_text(event.reply_token, f"user_id: {user_id}")
            continue

        # Threads投稿（今は中身の方式はthreads_bot側に任せる）
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

        # 店主はコマンド以外AI（テスト用）
        ans = await safe_ai_reply(text, cur_people, cur_oysters)
        reply_text(event.reply_token, ans or "例：『#3人』『#牡蠣10個』『状態』『投稿 文章』")

    return PlainTextResponse("OK")
