import os
import re
import requests
from fastapi import FastAPI, Request
from openai import OpenAI

app = FastAPI()

LINE_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OWNER_USER_ID = os.getenv("OWNER_USER_ID")  # ← Railway Variablesに入れる

# ====== 店内状況（まずはメモリ保存） ======
state = {
    "count": None,          # 人数
    "status": "不明",       # "空き" / "満席" / "不明"
    "note": "",             # 例: "ビニールカーテン中で最大10名" 
    "oyster_left":None,     # 牡蠣残り数（None＝未設定）
}

def get_client():
    if not OPENAI_API_KEY:
        return None
    return OpenAI(api_key=OPENAI_API_KEY)

def line_reply(reply_token: str, text: str):
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

def line_push(to_user_id: str, text: str):
    # 管理者にだけ通知する用（reply_token不要）
    r = requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={
            "Authorization": f"Bearer {LINE_TOKEN}",
            "Content-Type": "application/json",
        },
        json={
            "to": to_user_id,
            "messages": [{"type": "text", "text": text}],
        },
        timeout=10,
    )
    print("push status:", r.status_code, r.text)

def is_owner(user_id: str | None) -> bool:
    return bool(user_id) and bool(OWNER_USER_ID) and user_id == OWNER_USER_ID



@app.get("/")
def health():
    return {"ok": True}
def crowd_text():
    max_people = 10  # 今の上限（ビニールカーテン中）
    count = state["count"]

    if count is None:
        return "いまの店内人数は未更新やねん🙏 店主に直接聞いてみて〜"

    if count == 0:
        return (
            "いま0人や🤣\n"
            "ほぼ貸切状態やで！！\n"
            "今来たら店主独り占めや✨牡蠣ゆっくりいこや〜🔥"
        )

    elif count <= 3:
        return (
            f"いま店内 {count}名くらい！\n"
            "今めっちゃゆったりやで✨牡蠣ゆっくり食べたい人チャンスやで〜"
        )

    elif count <= 6:
        return (
            f"いま店内 {count}名くらい！\n"
            "まだ余裕あるで👍ふらっと寄れる感じやで！"
        )

    elif count < max_people:
        return (
            f"いま店内 {count}名くらい！\n"
            "ちょい混み気味やけどタイミング次第でいけるで！"
        )

    else:
        return "いま満席気味や🙏 空いたらまた更新するで！"
@app.post("/webhook")
async def webhook(request: Request):
    body = await request.json()
    print("LINEきた", body)

    events = body.get("events", [])
    for ev in events:
        reply_token = ev.get("replyToken")
        source = ev.get("source", {}) or {}
        user_id = source.get("userId")

        msg = ev.get("message", {}) or {}
        text = msg.get("text")

        if not reply_token or text is None:
            continue

        text = text.strip()

        # ====== 管理者コマンド ======
        if is_owner(user_id):
            # #人数 7
            m = re.match(r"^#?人数\s*[:：]?\s*(\d+)\s*$", text)
            if m:
                state["count"] = int(m.group(1))
                state["status"] = "満席" if state["count"] >= 10 else "空き"
                line_reply(reply_token, f"OK！いま {state['count']}名で更新したで👍（状態：{state['status']}）")
                continue

            if text in ("#満席", "満席"):
                state["status"] = "満席"
                line_reply(reply_token, "OK！状態を「満席」にしたで👍")
                continue

            if text in ("#空き", "空いてる", "空き"):
                state["status"] = "空き"
                line_reply(reply_token, "OK！状態を「空き」にしたで👍")
                continue

            if text.startswith("#状況"):
                line_reply(reply_token, crowd_text())
                continue
                        user_id = (ev.get("source") or {}).get("userId")
        is_owner = (OWNER_USER_ID is not None) and (user_id == OWNER_USER_ID)

        # =========================
        # 店主コマンド：#牡蠣 40
        # =========================
        m_oyster_set = re.match(r"^\s*#牡蠣\s*(\d+)?\s*$", text)
        if m_oyster_set and is_owner:
            num = m_oyster_set.group(1)
            if num is None:
                # #牡蠣 だけ送ったら現在値を返す
                if state["oyster_left"] is None:
                    line_reply(reply_token, "牡蠣残り数まだ未設定やで！例：#牡蠣 40")
                else:
                    line_reply(reply_token, f"いま牡蠣残りは {state['oyster_left']} 個やで🦪")
            else:
                state["oyster_left"] = int(num)
                if state["oyster_left"] <= 0:
                    line_reply(reply_token, "OK！牡蠣は完売(0)に更新したで🙏")
                else:
                    line_reply(reply_token, f"OK！牡蠣残り {state['oyster_left']} 個に更新したで🦪")
            continue

        # 店主以外が #牡蠣 送ってきたら軽くガード
        if re.match(r"^\s*#牡蠣", text) and not is_owner:
            line_reply(reply_token, "それは店主専用コマンドやで🙏")
            continue

        # =========================
        # お客さん質問：牡蠣ある？
        # =========================
                if re.search(r"(牡蠣|カキ).*(ある|まだ|残|いける|あります|残って)", text) or re.search(r"(生牡蠣|焼き牡蠣|蒸し牡蠣)", text):
            left = state.get("oyster_left")

            if left is None:
                line_reply(reply_token, "牡蠣の残り数まだ更新されてへん🙏 店主に聞いてみて〜")

            elif left <= 0:
                line_reply(reply_token, "今日は牡蠣完売や🙏 また仕入れたら言うで！")

            elif left <= 5:
                line_reply(reply_token, f"残り {left} 個…！売り切れ寸前やで💦 今すぐ来た方がええ！")

            elif left <= 15:
                line_reply(reply_token, f"牡蠣まだあるで🦪 残り {left} 個！早めにおいで〜")

            elif left <= 50:
                line_reply(reply_token, f"牡蠣いけるで🦪 残り {left} 個！まだ余裕あるで〜")

            else:
                line_reply(reply_token, f"牡蠣まだまだあるで🦪✨ 残り {left} 個！ゆっくりおいで〜")

            continue

        # ====== お客さん向け：混雑質問に即答 ======
        crowd_keywords = ("何人", "店内", "混んで", "混雑", "空いて", "満席", "入れる")
        if any(k in text for k in crowd_keywords):
            line_reply(reply_token, crowd_text())
            continue

        # ====== それ以外はAI返答（今のまま） ======
        ai_text = "ごめん、AI側が一瞬コケたわ💦 もっかい送って〜"
        client = get_client()
        if client is None:
            ai_text = "OpenAIキー読めてへんっぽい！RailwayのVariables見て〜"
        else:
            try:
                resp = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": "あなたは大阪の立ち飲み牡蠣小屋の相棒AI。関西弁で短めに返事して。"},
                        {"role": "user", "content": text},
                    ],
                )
                ai_text = (resp.choices[0].message.content or "").strip() or ai_text
            except Exception as e:
                print("OpenAI error:", repr(e))

        line_reply(reply_token, ai_text)

    return {"ok": True}
