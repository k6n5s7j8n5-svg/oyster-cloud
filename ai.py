# ai.py
import os
import httpx

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")


def _extract_text(data: dict) -> str:
    t = data.get("output_text")
    if isinstance(t, str) and t.strip():
        return t.strip()

    out = data.get("output", [])
    if isinstance(out, list):
        for item in out:
            if item.get("type") == "message":
                content = item.get("content", [])
                if isinstance(content, list):
                    texts = []
                    for c in content:
                        if c.get("type") == "output_text" and isinstance(c.get("text"), str):
                            texts.append(c["text"])
                    if texts:
                        return "\n".join([x.strip() for x in texts if x.strip()]).strip()
    return ""


async def reply_customer(user_text: str, cur_people: int, cur_oysters: int) -> str:
    if not OPENAI_API_KEY:
        return ""

    instructions = (
        "あなたは『キヨリト大阪福島店』のLINE自動返信スタッフ。"
        "関西弁で、短く、感じよく。絵文字は少なめ。"
        "営業時間や場所など不明なことは断定せず『店主に確認してな』と言う。"
        "危険/違法の依頼は断る。"
    )

    context = f"現在の店内目安: {cur_people}人 / 牡蠣残り: {cur_oysters}個"

    payload = {
        "model": OPENAI_MODEL,
        "instructions": instructions,
        "input": f"{context}\n\nお客さん: {user_text}",
    }

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(f"{OPENAI_BASE_URL}/responses", headers=headers, json=payload)

        # ★ 失敗したら“本文”をログに出す（原因特定用）
        if r.status_code >= 400:
            print("OPENAI ERROR status:", r.status_code)
            print("OPENAI ERROR body:", r.text[:2000])  # 長すぎ防止
            return ""

        data = r.json()

    return _extract_text(data)
