"""Inspect plan_ai_clips LLM raw output."""
import asyncio
import json

from app.services.ai.openai_service import OpenAIService


async def main():
    llm = OpenAIService()
    text = (
        "Всем привет. Сегодня разберём самую большую ошибку новичков в футболе. "
        "Смотри внимательно до конца. "
    ) * 8
    system = (
        "You are a short-form clip editor. From a long video transcript, "
        "pick the most interesting standalone moments for TikTok/Reels/Shorts. "
        "Return ONLY a JSON array with up to 3 items: "
        '{"start":float,"end":float,"title":"short title","reason":"why it works","score":0-100}. '
        "Each clip must be 20-60 seconds. Prefer hooks, surprising claims, clear stories, CTAs. "
        "Avoid silence/filler intros. No markdown."
    )
    user = f"Language: ru\nTotal duration: 90.0s\nTranscript:\n{text}"
    raw = await llm._chat_completion(system, user, temperature=0.35, max_tokens=1400)
    print("RAW_REPR", repr(raw[:1000]))
    try:
        content = raw.strip()
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()
        data = json.loads(content)
        print("PARSED_TYPE", type(data), "LEN", len(data) if isinstance(data, list) else None)
        print("PARSED", json.dumps(data, ensure_ascii=False)[:800])
    except Exception as e:
        print("PARSE_ERR", e)
    plan = await llm.plan_ai_clips(text, [], 90.0, "ru", 3)
    print("PLAN", plan)


if __name__ == "__main__":
    asyncio.run(main())
