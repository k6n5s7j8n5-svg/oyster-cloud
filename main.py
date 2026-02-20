import os
import re
import requests
from fastapi import FastAPI, Request
from openai import OpenAI

app = FastAPI()

LINE_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY".lower())
OWNER_USER_ID = os.getenv("OWNER_USER_ID")

# ====== 店内状態（メモリ保存：再起動でリセット） ======
state = {
    "count": None,      # 店内人数（int）
    "status": "不明",   # "空き" / "満席" / "不明"
    "note": "",         # 例: "ビニールカーテン中で最大10名"
    "oysters": None,    # 牡蠣残り数（int）
}

def get_client():
    if not OPENAI_API_KEY:
        return None
    return OpenAI(api_key=OPENAI_API_KEY)

def is_owner(user_id: str | None) -> bool:
    return bool(OWNER_USER_ID) and (user_id == OWNER_USER_ID)

def line_reply(reply_token: str, text: str):
    if not LINE_TOKEN:
        print("LINE token missing")
        return
    r = requests.post(
        "https://api.line.me/v2/bot/message/reply",
        headers={
            "Authorization": f"Bearer {LINE_TOKEN}",
            "Content-Type": "application/json",
        },
        json={
            "replyToken": reply_token,
            "messages": [{"type": "text", "text": text}],
        },
        timeout=10,
    )
    print("reply status:", r.status_code, r.text)

def crowd_message() -> str:
    c = state.get("count")
    status = state.get("status") or "不明"
    note = state.get("note") or ""

    # status を count から自動補正（countがある時だけ）
    if isinstance(c, int):
        if c >= 10:
            status = "満席"
        elif c <= 3:
            status = "空き"
        else:
            status = "普通"

    base = "いまの店内状況やで👇\n"
    if isinstance(c, int):
        base += f"・人数：{c}名くらい\n"
    else:
        base += "・人数：未更新\n"

    base += f"・状態：{status}\n"
    if note:
        base += f"・メモ：{note}\n"

    # 空いてる時の一言
    if isinstance(c, int) and c <= 3:
        base += "\nいま少ないし、サクッと牡蠣いけるで〜来て来て🦪✨"

    return base.strip()

def shell_oysters_message() -> str:
    n = state.get("shell_oysters")
    if not isinstance(n, int):
        return "殻付き（生牡蠣）の在庫、まだ未更新やねん🙏"

    if n <= 0:
        return (
            "ごめん！殻付き（生牡蠣）は今日は売り切れやねん🙏\n"
            "でも **カキフライ** と **ホイル焼き** はいけるで🦪🔥\n"
            "どっち食べたい？「フライ」か「ホイル」って送って〜"
        )
    if n <= 10:
        return f"殻付き（生牡蠣）あと **{n}個** くらい⚠️ なくなる前に急げ〜！"
    return f"殻付き（生牡蠣）はまだあるで😎（残り目安 {n}個）"

@app.get("/")
def health():
    return {"ok": True}

@app.post("/webhook")
async def webhook(request: Request):
    body = await request.json()
    print("LINEきた", body)

    if not LINE_TOKEN:
        return {"ok": False, "error": "LINE_CHANNEL_ACCESS_TOKEN is missing"}

    events = body.get("events", [])
    for ev in events:
        reply_token = ev.get("replyToken")

        source = ev.get("source") or {}
        user_id = source.get("userId")

        msg = ev.get("message") or {}
        text = msg.get("text")

        if not reply_token or text is None:
            continue

        text = text.strip()

        # ======================
        # ① 店主だけが使える更新コマンド
        # ======================
        if is_owner(user_id):
            # #人数 7 / #人数:7 / 人数 7
            m = re.match(r"^#?人数\s*[:：]?\s*(\d+)\s*$", text)
            if m:
                state["count"] = int(m.group(1))
                # ざっくり状態も更新
                state["status"] = "満席" if state["count"] >= 10 else ("空き" if state["count"] <= 3 else "普通")
                line_reply(reply_token, f"OK！いま店内{state['count']}名くらいに更新したで👌")
                continue

            # #満席 / 満席
            if text in ("#満席", "満席"):
                state["status"] = "満席"
                line_reply(reply_token, "OK！状態を「満席」にしたで👌")
                continue

            # #空き / 空き
            if text in ("#空き", "空き"):
                state["status"] = "空き"
                line_reply(reply_token, "OK！状態を「空き」にしたで👌 いま来どきやな🦪")
                continue

            # #メモ ビニールカーテン中で最大10名
            m = re.match(r"^#?メモ\s*[:：]?\s*(.+)\s*$", text)
            if m:
                state["note"] = m.group(1).strip()
                line_reply(reply_token, f"OK！メモ更新したで👌\n{state['note']}")
                continue

            # #牡蠣 12
            m = re.match(r"^#?牡蠣\s*[:：]?\s*(\d+)\s*$", text)
            if m:
                state["oysters"] = int(m.group(1))
                n = state["oysters"]
                if n <= 10:
                    msg2 = f"OK！牡蠣残り {n}個やで⚠️ なくなる前に急げ〜！"
                elif n >= 50:
                    msg2 = f"OK！牡蠣残り {n}個。まだまだあるで😎"
                else:
                    msg2 = f"OK！牡蠣残り {n}個やで〜"
                line_reply(reply_token, msg2)
                continue

            # #状況 まとめ表示（店主用）
            if text in ("#状況", "状況", "#ステータス", "ステータス"):
                line_reply(reply_token, crowd_message() + "\n\n" + oysters_message())
                continue

        # ======================
        # ② お客さんが聞ける質問（誰でも）
        # ======================
        # 店内人数 / 混み具合 / 空いてる？
        if re.search(r"(人数|混み|混んで|空いて|席|入れる)", text):
            line_reply(reply_token, crowd_message())
            continue

        # 牡蠣残り / 在庫
        if re.search(r"(牡蠣|かき).*(残り|あと|在庫)|残り.*(牡蠣|かき).(生|殻).*牡蠣|牡蠣.*(生|殻)|在庫", text):
            line_reply(reply_token, oysters_message())
            continue# ===== 牡蠣ある？系（超重要）=====
        if re.search(r"(牡蠣|かき)", text):
        # 「ある」「あります」「いける」「食べれる」「？」など含む場合
        if re.search(r"(ある|あります|いける|食べれる|食べられる|\?)", text):
        line_reply(reply_token, oysters_message())
        continue

         # 「牡蠣ある？」みたいに短文でも拾う
        if text.strip() in ("牡蠣ある", "牡蠣ある？", "牡蠣ありますか", "牡蠣あるかな"):
        line_reply(reply_token, oysters_message())
        continue
            (生|殻).*牡蠣|牡蠣.*(生|殻)
        # ======================
        # ③ それ以外はOpenAIで雑談（任意）
        # ======================
        ai_text = "まいど！どうしたん？🦪"
        client = get_client()

        if client is None:
            ai_text = "OpenAIキー読めてへんっぽい！RailwayのVariables見て〜"
        else:
            try:
                resp = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": "あなたは大阪の立ち飲み牡蠣小屋の店主の相棒AI。関西弁で短めに返事して。"},
                        {"role": "user", "content": text},
                    ],
                )
                ai_text = (resp.choices[0].message.content or "").strip() or ai_text
            except Exception as e:
                print("OpenAI error:", repr(e))
                ai_text = "ごめん、AI側が一瞬コケたわ💦 もっかい送って〜"

        line_reply(reply_token, ai_text)

    return {"ok": True}
