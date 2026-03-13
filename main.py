import os
import re
import json
import random
import sqlite3
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict

import requests
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse

from openai import OpenAI

from linebot.v3.webhook import WebhookParser
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    PushMessageRequest,
    TextMessage,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent


# =========================================================
# 基本設定
# =========================================================

JST = timezone(timedelta(hours=9))

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")

THREADS_ACCESS_TOKEN = os.getenv("THREADS_ACCESS_TOKEN", "")
THREADS_USER_ID = os.getenv("THREADS_USER_ID", "")

OWNER_USER_ID = os.getenv("OWNER_USER_ID", "")
CRON_SECRET = os.getenv("CRON_SECRET", "")
DB_PATH = os.getenv("DB_PATH", "oyster_cloud.db")

SHOP_NAME = os.getenv("SHOP_NAME", "キヨリト大阪福島店")
SHOP_AREA = os.getenv("SHOP_AREA", "大阪福島")
REVIEW_URL = os.getenv(
    "REVIEW_URL",
    "https://g.page/r/CXCoWU0ghRcQEBM/review"
)

OPEN_HOUR = int(os.getenv("OPEN_HOUR", "16"))
CLOSE_HOUR = int(os.getenv("CLOSE_HOUR", "24"))

POST_SLOTS = {
    1: "12:00",
    2: "18:00",
    3: "22:30",
}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("oyster_cloud")

app = FastAPI()

parser: Optional[WebhookParser] = None
messaging_api: Optional[MessagingApi] = None
api_client: Optional[ApiClient] = None

if LINE_CHANNEL_ACCESS_TOKEN and LINE_CHANNEL_SECRET:
    configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
    api_client = ApiClient(configuration)
    messaging_api = MessagingApi(api_client)
    parser = WebhookParser(LINE_CHANNEL_SECRET)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
client: Optional[OpenAI] = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None


# =========================================================
# 時刻・営業判定
# =========================================================

def now_jst() -> datetime:
    return datetime.now(JST)


def today_str() -> str:
    return now_jst().strftime("%Y-%m-%d")


def current_hm() -> str:
    return now_jst().strftime("%H:%M")


def is_open_now(dt: Optional[datetime] = None) -> bool:
    dt = dt or now_jst()
    hour = dt.hour
    return OPEN_HOUR <= hour < 24


# =========================================================
# DB
# =========================================================

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS app_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS line_users (
            user_id TEXT PRIMARY KEY,
            display_name TEXT,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            review_sent INTEGER NOT NULL DEFAULT 0
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_threads_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_date TEXT NOT NULL,
            slot INTEGER NOT NULL,
            post_text TEXT NOT NULL,
            posted INTEGER NOT NULL DEFAULT 0,
            posted_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(post_date, slot)
        )
    """)

    now = now_jst().isoformat()
    defaults = {
        "people_count": "0",
        "oyster_count": "0",
        "last_reset_date": today_str(),
    }
    for k, v in defaults.items():
        cur.execute("""
            INSERT OR IGNORE INTO app_state (key, value, updated_at)
            VALUES (?, ?, ?)
        """, (k, v, now))

    conn.commit()
    conn.close()


@app.on_event("startup")
def on_startup():
    init_db()
    logger.info("DB initialized")


def upsert_state(key: str, value: str):
    conn = get_conn()
    cur = conn.cursor()
    now = now_jst().isoformat()
    cur.execute("""
        INSERT INTO app_state (key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = excluded.updated_at
    """, (key, value, now))
    conn.commit()
    conn.close()


def get_state(key: str, default: Optional[str] = None) -> Optional[str]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT value FROM app_state WHERE key = ?", (key,))
    row = cur.fetchone()
    conn.close()
    return row["value"] if row else default


def maybe_daily_reset():
    last_reset_date = get_state("last_reset_date", "")
    today = today_str()
    if last_reset_date != today:
        upsert_state("people_count", "0")
        upsert_state("oyster_count", "0")
        upsert_state("last_reset_date", today)
        logger.info("Daily reset executed. people_count=0 oyster_count=0")


def set_people_count(n: int):
    upsert_state("people_count", str(max(0, n)))


def set_oyster_count(n: int):
    upsert_state("oyster_count", str(max(0, n)))


def get_people_count() -> int:
    maybe_daily_reset()
    return int(get_state("people_count", "0") or 0)


def get_oyster_count() -> int:
    maybe_daily_reset()
    return int(get_state("oyster_count", "0") or 0)


def save_or_update_user(user_id: str, display_name: str = ""):
    conn = get_conn()
    cur = conn.cursor()
    now = now_jst().isoformat()
    cur.execute("""
        INSERT INTO line_users (user_id, display_name, first_seen_at, last_seen_at, review_sent)
        VALUES (?, ?, ?, ?, 0)
        ON CONFLICT(user_id) DO UPDATE SET
            display_name = CASE
                WHEN excluded.display_name != '' THEN excluded.display_name
                ELSE line_users.display_name
            END,
            last_seen_at = excluded.last_seen_at
    """, (user_id, display_name, now, now))
    conn.commit()
    conn.close()


def get_user(user_id: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM line_users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row


def mark_review_sent(user_id: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        UPDATE line_users
        SET review_sent = 1
        WHERE user_id = ?
    """, (user_id,))
    conn.commit()
    conn.close()


# =========================================================
# LINE送受信
# =========================================================

def ensure_line_ready():
    if not messaging_api:
        raise RuntimeError("LINE Messaging API is not configured.")


def reply_line(reply_token: str, text: str):
    ensure_line_ready()
    messaging_api.reply_message(
        ReplyMessageRequest(
            reply_token=reply_token,
            messages=[TextMessage(text=text)]
        )
    )


def push_line(user_id: str, text: str):
    ensure_line_ready()
    messaging_api.push_message(
        PushMessageRequest(
            to=user_id,
            messages=[TextMessage(text=text)]
        )
    )


def get_line_display_name(user_id: str) -> str:
    if not messaging_api or not user_id:
        return "不明"
    try:
        profile = messaging_api.get_profile(user_id)
        return getattr(profile, "display_name", "") or "不明"
    except Exception:
        logger.exception("failed to get LINE profile")
        return "不明"


# =========================================================
# Threads投稿文生成
# =========================================================

def generate_ai_threads_post() -> str:
    if not client:
        return "大阪福島で牡蠣どうです？🦪"

    people = get_people_count()
    oysters = get_oyster_count()

    prompt = f"""
あなたは大阪福島の牡蠣屋『{SHOP_NAME}』の店員です。

Threads投稿を作ってください。

条件
・自然な関西弁
・短め
・牡蠣が食べたくなる内容
・大阪福島を入れる
・店内人数: {people}
・牡蠣残数: {oysters}
・絵文字OK
・日本語で出力
"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=120,
        )
        return response.choices[0].message.content.strip()

    except Exception as e:
        logger.exception("generate_ai_threads_post error: %s", e)
        return "大阪福島で牡蠣どうです？🦪"


def generate_daily_posts() -> Dict[int, str]:
    return {
        1: generate_ai_threads_post(),
        2: generate_ai_threads_post(),
        3: generate_ai_threads_post(),
    }


def save_daily_posts(post_date: str, posts: Dict[int, str]):
    conn = get_conn()
    cur = conn.cursor()
    now = now_jst().isoformat()
    for slot, text in posts.items():
        cur.execute("""
            INSERT INTO daily_threads_posts (
                post_date, slot, post_text, posted, posted_at, created_at, updated_at
            )
            VALUES (?, ?, ?, 0, NULL, ?, ?)
            ON CONFLICT(post_date, slot) DO UPDATE SET
                post_text = excluded.post_text,
                updated_at = excluded.updated_at
        """, (post_date, slot, text, now, now))
    conn.commit()
    conn.close()


def get_daily_posts(post_date: str) -> Dict[int, Dict]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT slot, post_text, posted, posted_at
        FROM daily_threads_posts
        WHERE post_date = ?
        ORDER BY slot ASC
    """, (post_date,))
    rows = cur.fetchall()
    conn.close()

    out = {}
    for row in rows:
        out[row["slot"]] = {
            "text": row["post_text"],
            "posted": bool(row["posted"]),
            "posted_at": row["posted_at"],
        }
    return out


def update_post_text(post_date: str, slot: int, new_text: str) -> bool:
    conn = get_conn()
    cur = conn.cursor()
    now = now_jst().isoformat()
    cur.execute("""
        UPDATE daily_threads_posts
        SET post_text = ?, updated_at = ?
        WHERE post_date = ? AND slot = ?
    """, (new_text, now, post_date, slot))
    updated = cur.rowcount > 0
    conn.commit()
    conn.close()
    return updated


def mark_posted(post_date: str, slot: int):
    conn = get_conn()
    cur = conn.cursor()
    now = now_jst().isoformat()
    cur.execute("""
        UPDATE daily_threads_posts
        SET posted = 1, posted_at = ?, updated_at = ?
        WHERE post_date = ? AND slot = ?
    """, (now, now, post_date, slot))
    conn.commit()
    conn.close()


def format_posts_for_line(post_date: str, posts: Dict[int, Dict]) -> str:
    lines = [f"【{post_date} のThreads投稿案】", ""]
    for slot in [1, 2, 3]:
        item = posts.get(slot)
        lines.append(f"{slot}本目（{POST_SLOTS[slot]}）")
        if item:
            status = "投稿済み" if item["posted"] else "未投稿"
            lines.append(f"状態: {status}")
            lines.append(item["text"])
        else:
            lines.append("未作成")
        lines.append("")
    lines.append("修正: #1 文章 / #2 文章 / #3 文章")
    lines.append("確認: #投稿確認")
    return "\n".join(lines)


# =========================================================
# Threads API
# =========================================================

def create_threads_post(text: str) -> str:
    if not THREADS_ACCESS_TOKEN or not THREADS_USER_ID:
        raise RuntimeError("THREADS_ACCESS_TOKEN or THREADS_USER_ID is missing.")

    url = f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads"
    payload = {
        "media_type": "TEXT",
        "text": text,
        "access_token": THREADS_ACCESS_TOKEN,
    }
    r = requests.post(url, data=payload, timeout=30)
    r.raise_for_status()
    data = r.json()
    creation_id = data.get("id")
    if not creation_id:
        raise RuntimeError(f"Threads create failed: {data}")
    return creation_id


def publish_threads_post(creation_id: str) -> Dict:
    url = f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads_publish"
    payload = {
        "creation_id": creation_id,
        "access_token": THREADS_ACCESS_TOKEN,
    }
    r = requests.post(url, data=payload, timeout=30)
    r.raise_for_status()
    return r.json()


def post_to_threads(text: str) -> Dict:
    creation_id = create_threads_post(text)
    result = publish_threads_post(creation_id)
    return {"creation_id": creation_id, "publish_result": result}


# =========================================================
# 問い合わせ判定
# =========================================================

OYSTER_PATTERNS = [
    r"牡蠣",
    r"かき",
    r"カキ",
    r"生牡蠣",
    r"焼き牡蠣",
    r"蒸し牡蠣",
]

OYSTER_STOCK_PATTERNS = [
    r"牡蠣.*(ある|あります|残|残り|在庫)",
    r"(ある|あります|残|残り|在庫).*(牡蠣|かき|カキ)",
    r"牡蠣(ある|あります|残り|在庫)\??",
    r"今日.*牡蠣",
]

CROWD_PATTERNS = [
    r"混ん",
    r"空いて",
    r"すいて",
    r"込み具合",
    r"人多い",
    r"今何人",
    r"何人",
    r"人数",
    r"店内人数",
    r"店内",
    r"席.*空",
    r"入れそう",
]

REVIEW_PATTERNS = [
    r"口コミ",
    r"レビュー",
    r"google",
    r"グーグル",
]


def matches_any(text: str, patterns) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def asks_people_and_oysters(text: str) -> bool:
    asks_people = bool(re.search(r"(人数|何人|混んで|混雑|店内|満席|空いて)", text))
    asks_oysters = bool(re.search(r"(牡蠣|かき|カキ|在庫|残り)", text))
    return asks_people and asks_oysters


def classify_message(text: str) -> Dict[str, bool]:
    return {
        "asks_oyster_stock": matches_any(text, OYSTER_STOCK_PATTERNS),
        "asks_crowd": matches_any(text, CROWD_PATTERNS),
        "asks_review": matches_any(text, REVIEW_PATTERNS),
        "mentions_oyster": matches_any(text, OYSTER_PATTERNS),
        "asks_people_and_oysters": asks_people_and_oysters(text),
    }


# =========================================================
# 自動返信文
# =========================================================

def oyster_stock_reply() -> str:
    count = get_oyster_count()
    if count <= 0:
        return (
            "問い合わせありがとうな🦪\n"
            "いま案内できる牡蠣は確認中やねん。\n"
            "気になるときは、もう一回メッセージしてな。"
        )
    return (
        f"問い合わせありがとうな🦪\n"
        f"今日の牡蠣は今、残り{count}個やで！\n"
        f"{SHOP_AREA}で牡蠣食べたなったら待ってるで。"
    )


def people_reply() -> str:
    people = get_people_count()
    return f"今の店内人数は {people} 人やで🍻"


def crowd_reply() -> str:
    people = get_people_count()
    if people <= 0:
        return (
            "問い合わせありがとう😊\n"
            "今んとこ店内はかなり落ち着いてるで。\n"
            "ふらっと入りやすいタイミングやわ。"
        )
    if people <= 3:
        return (
            f"問い合わせありがとう😊\n"
            f"今の店内人数は {people}人 やで。\n"
            "比較的ゆったりしてるわ。"
        )
    if people <= 6:
        return (
            f"問い合わせありがとう😊\n"
            f"今の店内人数は {people}人 やで。\n"
            "ちょいにぎわってるけど、案内できる可能性あるで。"
        )
    return (
        f"問い合わせありがとう😊\n"
        f"今の店内人数は {people}人 やで。\n"
        "ちょい混み気味やから、来る前にもう一回確認してもろたら安心やで。"
    )


def people_and_oysters_reply() -> str:
    people = get_people_count()
    oysters = get_oyster_count()
    return f"今の店内人数は {people} 人やで🍻\n牡蠣の残りは {oysters} 個やで🦪"


def closed_reply() -> str:
    return (
        "問い合わせありがとうな🦪\n"
        "今は営業時間外やねん。\n"
        f"営業時間は毎日 {OPEN_HOUR}:00〜23:59 やで。\n"
        "また営業中に連絡待ってるわ！"
    )


def default_open_reply() -> str:
    return (
        f"問い合わせありがとうな🦪\n"
        f"{SHOP_NAME}やで！\n"
        "順番に案内してるから、ちょい待ってな。"
    )


def review_reply() -> str:
    return (
        "ありがとう！\n"
        "Google口コミはここからお願いしてるで🙏\n"
        f"{REVIEW_URL}"
    )


def ai_kansai_reply(user_text: str, display_name: str = "") -> str:

    if not client:
        return "おおきに！ちょい今確認してるから少し待ってな🦪"

    people = get_people_count()
    oysters = get_oyster_count()

    prompt = f"""
あなたは大阪福島の牡蠣屋『{SHOP_NAME}』の店員です。

必ず自然な関西弁で短く返してください。

店情報
店名:{SHOP_NAME}
場所:{SHOP_AREA}
店内人数:{people}
牡蠣残数:{oysters}

お客様:{display_name}
メッセージ:{user_text}

ルール
・人数を聞かれたら店内人数を答える
・牡蠣を聞かれたら牡蠣残数を答える
・人数と牡蠣両方聞かれたら両方答える
・雑談は自然に返す
・標準語は禁止
"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=150,
        )

        return response.choices[0].message.content.strip()

    except Exception as e:
        logger.exception("ai_kansai_reply error: %s", e)
        return "おおきに！ちょい今バタついてるわ🙏"

# =========================================================
# 管理者コマンド
# =========================================================

def is_owner(user_id: str) -> bool:
    return bool(OWNER_USER_ID) and user_id == OWNER_USER_ID


def owner_help_text() -> str:
    return (
        "【管理コマンド】\n"
        "#3人\n"
        "#牡蠣80\n"
        "#状態\n"
        "#投稿確認\n"
        "#今日の投稿作成\n"
        "#1 文章\n"
        "#2 文章\n"
        "#3 文章\n"
        "#今すぐ1投稿\n"
        "#今すぐ2投稿\n"
        "#今すぐ3投稿\n"
        "#口コミURL"
    )


def handle_owner_command(text: str) -> str:
    maybe_daily_reset()
    t = text.strip()

    m = re.fullmatch(r"#\s*(\d+)\s*人", t)
    if m:
        count = int(m.group(1))
        set_people_count(count)
        return f"今の店内人数を {count}人 に更新したで。"

    m = re.fullmatch(r"#\s*牡蠣\s*(\d+)", t)
    if m:
        count = int(m.group(1))
        set_oyster_count(count)
        return f"牡蠣在庫を {count}個 に更新したで。"

    if t == "#状態":
        people = get_people_count()
        oyster = get_oyster_count()
        open_status = "営業中" if is_open_now() else "営業時間外"
        return (
            "【現在の状態】\n"
            f"営業: {open_status}\n"
            f"店内人数: {people}人\n"
            f"牡蠣在庫: {oyster}個\n"
            f"現在時刻: {today_str()} {current_hm()}"
        )

    if t == "#口コミURL":
        return REVIEW_URL

    if t == "#今日の投稿作成":
        posts = generate_daily_posts()
        save_daily_posts(today_str(), posts)
        saved = get_daily_posts(today_str())
        return "今日のThreads投稿案を作成したで。\n\n" + format_posts_for_line(today_str(), saved)

    if t == "#投稿確認":
        posts = get_daily_posts(today_str())
        if not posts:
            return "今日の投稿案はまだないで。\n#今日の投稿作成 で作れるで。"
        return format_posts_for_line(today_str(), posts)

    m = re.match(r"^#([123])\s+(.+)$", t, re.DOTALL)
    if m:
        slot = int(m.group(1))
        new_text = ensure_keywords(m.group(2).strip())
        if not update_post_text(today_str(), slot, new_text):
            save_daily_posts(today_str(), generate_daily_posts())
            update_post_text(today_str(), slot, new_text)
        return f"{slot}本目を更新したで。\n\n{new_text}"

    m = re.match(r"^#今すぐ([123])投稿$", t)
    if m:
        slot = int(m.group(1))
        posts = get_daily_posts(today_str())
        if slot not in posts:
            return "今日の投稿案がまだないで。\n先に #今日の投稿作成 をしてな。"
        result = post_to_threads(posts[slot]["text"])
        mark_posted(today_str(), slot)
        return f"{slot}本目をThreadsへ投稿したで。\n\n{json.dumps(result, ensure_ascii=False)}"

    return owner_help_text()


# =========================================================
# 新規客への口コミ案内
# =========================================================

def should_send_review_url(user_id: str, text: str) -> bool:
    user = get_user(user_id)
    if not user:
        return False
    if int(user["review_sent"]) == 1:
        return False
    if matches_any(text, REVIEW_PATTERNS):
        return True
    return True


# =========================================================
# Webhook
# =========================================================

@app.get("/")
def root():
    return {"ok": True, "service": "oyster_cloud_ultimate"}


@app.get("/health")
def health():
    maybe_daily_reset()
    return {
        "ok": True,
        "date": today_str(),
        "time": current_hm(),
        "people_count": get_people_count(),
        "oyster_count": get_oyster_count(),
    }


@app.post("/callback")
async def callback(request: Request):
    if not parser:
        raise HTTPException(status_code=500, detail="LINE parser is not configured.")

    signature = request.headers.get("X-Line-Signature", "")
    body = await request.body()
    body_text = body.decode("utf-8")

    try:
        events = parser.parse(body_text, signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    for event in events:
        if not isinstance(event, MessageEvent):
            continue
        if not isinstance(event.message, TextMessageContent):
            continue

        maybe_daily_reset()

        text = (event.message.text or "").strip()
        reply_token = event.reply_token
        source = event.source
        user_id = getattr(source, "user_id", "") or ""
        display_name = get_line_display_name(user_id) if user_id else "不明"

        logger.info("message user_id=%s text=%s", user_id, text)

        if user_id:
            save_or_update_user(user_id, display_name)

        try:
            if is_owner(user_id) and text.startswith("#"):
                response = handle_owner_command(text)
                reply_line(reply_token, response)
                continue

            if not is_open_now():
                reply_line(reply_token, closed_reply())
                continue

            flags = classify_message(text)

            if OWNER_USER_ID and (
                flags["asks_oyster_stock"]
                or flags["asks_crowd"]
                or flags["asks_review"]
                or flags["asks_people_and_oysters"]
            ):
                push_line(OWNER_USER_ID, compose_owner_alert(display_name, user_id, text, flags))

            if flags["asks_review"]:
                reply_line(reply_token, review_reply())
                if user_id:
                    mark_review_sent(user_id)
                continue

            if flags["asks_people_and_oysters"]:
                reply_line(reply_token, people_and_oysters_reply())
                if user_id and should_send_review_url(user_id, text):
                    try:
                        push_line(user_id, f"Google口コミはこちらやで🙏\n{REVIEW_URL}")
                        mark_review_sent(user_id)
                    except Exception:
                        logger.exception("failed to push review url")
                continue

            if flags["asks_oyster_stock"]:
                reply_line(reply_token, oyster_stock_reply())
                if user_id and should_send_review_url(user_id, text):
                    try:
                        push_line(user_id, f"Google口コミはこちらやで🙏\n{REVIEW_URL}")
                        mark_review_sent(user_id)
                    except Exception:
                        logger.exception("failed to push review url")
                continue

            if flags["asks_crowd"]:
                if re.search(r"(人数|何人|店内人数|店内)", text):
                    reply_line(reply_token, people_reply())
                else:
                    reply_line(reply_token, crowd_reply())
                if user_id and should_send_review_url(user_id, text):
                    try:
                        push_line(user_id, f"Google口コミはこちらやで🙏\n{REVIEW_URL}")
                        mark_review_sent(user_id)
                    except Exception:
                        logger.exception("failed to push review url")
                continue

            if flags["mentions_oyster"]:
                reply_line(reply_token, oyster_stock_reply())
                continue

            user = get_user(user_id) if user_id else None
            if user and int(user["review_sent"]) == 0:
                reply_line(
                    reply_token,
                    ai_kansai_reply(text) + f"\n\nGoogle口コミはこちらやで🙏\n{REVIEW_URL}"
                )
                mark_review_sent(user_id)
            else:
                reply_line(reply_token, ai_kansai_reply(text))

        except Exception as e:
            logger.exception("Webhook handling error")
            try:
                reply_line(reply_token, f"エラーが出たわ:\n{str(e)}")
            except Exception:
                pass

    return PlainTextResponse("OK")


# =========================================================
# Cron用
# =========================================================

def verify_cron_secret(secret: str):
    if not CRON_SECRET or secret != CRON_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")


@app.post("/cron/reset")
def cron_reset(secret: str):
    verify_cron_secret(secret)
    set_people_count(0)
    set_oyster_count(0)
    upsert_state("last_reset_date", today_str())
    return {"ok": True, "message": "reset completed"}


@app.api_route("/cron/generate-daily-posts", methods=["GET", "POST"])
def cron_generate_daily_posts(secret: str):
    verify_cron_secret(secret)
    posts = generate_daily_posts()
    save_daily_posts(today_str(), posts)
    saved = get_daily_posts(today_str())
    if OWNER_USER_ID:
        push_line(OWNER_USER_ID, format_posts_for_line(today_str(), saved))
    return {"ok": True, "message": "daily posts generated"}


@app.api_route("/cron/post/{slot}", methods=["GET", "POST"])
def cron_post_slot(slot: int, secret: str):
    verify_cron_secret(secret)

    if slot not in [1, 2, 3]:
        raise HTTPException(status_code=400, detail="slot must be 1,2,3")

    posts = get_daily_posts(today_str())
    if slot not in posts:
        raise HTTPException(status_code=404, detail="No post found for today")

    if posts[slot]["posted"]:
        return {"ok": True, "message": "already posted", "slot": slot}

    result = post_to_threads(posts[slot]["text"])
    mark_posted(today_str(), slot)

    if OWNER_USER_ID:
        push_line(
            OWNER_USER_ID,
            f"{slot}本目（{POST_SLOTS[slot]}）をThreadsに投稿したで🦪\n\n{posts[slot]['text']}"
        )

    return {"ok": True, "slot": slot, "result": result}


# =========================================================
# 確認用API
# =========================================================

@app.get("/posts/today")
def posts_today():
    return {"date": today_str(), "posts": get_daily_posts(today_str())}
