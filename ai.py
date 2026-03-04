# ai.py
import os
import httpx
from typing import Optional, List, Dict, Any

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")  # コスト安め推奨
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

# 店の基本設定（必要なら環境変数にしてもOK）
SHOP_NAME = os.getenv("SHOP_NAME", "オイスタークラウド")
SHOP_STYLE = os.getenv("SHOP_STYLE", "路地裏の立ち飲み牡蠣小屋")


def _extract_text(resp_json: Dict[str, Any]) -> str:
    """
    Responses API の返却からテキストを安全に取り出す。
    """
    # 1) もし output_text がある実装ならそれを優先
    if isinstance(resp_json.get("output_text"), str) and resp_json["output_text"].strip():
        return resp_json["output_text"].strip()

    # 2) output 配列から拾う
    out = resp_json.get("output", [])
    if isinstance(out, list):
        for item in out:
            # item: {"type":"message","content":[{"type":"output_text","text":"..."}]}
            content = item.get("content")
            if isinstance(content, list):
                texts = []
                for c in content:
                    if c.get("type") in ("output_text", "text") and isinstance(c.get("text"), str):
                        texts.append(c["text"])
                if texts:
                    return "\n".join(t.strip() for t in texts if t.strip()).strip()

    return ""


async def openai_text(messages: List[Dict[str, str]], temperature: float = 0.6) -> str:
    """
    OpenAI Responses API に投げて、テキストを返す。
    """
    if not OPENAI_API_KEY:
        return ""

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": OPENAI_MODEL,
        "input": messages,
        "temperature": temperature,
    }

    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(f"{OPENAI_BASE_URL}/responses", headers=headers, json=payload)
        r.raise_for_status()
        data = r.json()

    return _extract_text(data)


async def generate_customer_reply(user_text: str, cur_people: int, cur_oysters: int) -> str:
    """
    お客さん（非店主）向けのAI自動返信。
    """
    system = (
        f"あなたは{SHOP_STYLE}『{SHOP_NAME}』のLINE自動返信スタッフ。"
        "関西弁で、短く、感じよく、絵文字は少なめ。"
        "値段や営業時間など、わからないことは断定せず『店主に確認してな』と言う。"
        "危険行為や違法行為には協力しない。"
    )

    # 店の“今の状態”を軽く渡す（AIが案内に使える）
    context = f"現在の店内目安: {cur_people}人 / 牡蠣残り: {cur_oysters}個"

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"{context}\n\nお客さん: {user_text}"},
    ]

    text = await openai_text(messages, temperature=0.5)
    return text.strip()


async def generate_threads_post(cur_people: int, cur_oysters: int, hint: Optional[str] = None) -> str:
    """
    更新内容からThreads投稿文をAI生成（短め・読みやすい）。
    """
    system = (
        f"あなたは{SHOP_STYLE}『{SHOP_NAME}』のSNS担当。"
        "Threadsに載せる短文を1本だけ作る。"
        "宣伝臭すぎない。読みやすく改行。ハッシュタグは最大2個。"
        "現在の人数と牡蠣残数を自然に入れる。"
    )

    user = f"現在: {cur_people}人 / 牡蠣: {cur_oysters}個"
    if hint:
        user += f"\n補足: {hint}"

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    text = await openai_text(messages, temperature=0.7)
    return text.strip()
