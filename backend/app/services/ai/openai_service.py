import httpx
from app.core.config import settings


class OpenAIService:
    """
    Script LLM via OpenAI-compatible chat completions.
    Prefers AITUNNEL (₽, no VPN); falls back to OpenAI.
    """
    
    def __init__(self):
        aitunnel_key = (settings.AITUNNEL_API_KEY or "").strip()
        openai_key = (settings.OPENAI_API_KEY or "").strip()

        if aitunnel_key:
            self.provider = "aitunnel"
            self.api_key = aitunnel_key
            self.base_url = settings.AITUNNEL_BASE_URL.rstrip("/")
            self.model = settings.AITUNNEL_MODEL
        else:
            self.provider = "openai"
            self.api_key = openai_key or None
            self.base_url = "https://api.openai.com/v1"
            self.model = settings.OPENAI_MODEL

    def _require_key(self) -> None:
        if not self.api_key:
            raise ValueError(
                "LLM API key not configured. Set AITUNNEL_API_KEY (recommended) "
                "or OPENAI_API_KEY in .env"
            )

    def _brain_block(
        self,
        domains: list[str],
        prefer_tags: list[str] | None = None,
    ) -> str:
        try:
            from app.services.brain import get_brain
            return get_brain().get_prompt_block(domains, prefer_tags=prefer_tags)
        except Exception:
            return ""

    async def _chat_completion(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.8,
        max_tokens: int = 1000,
    ) -> str:
        self._require_key()

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        async with httpx.AsyncClient(timeout=90.0) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )

            if response.status_code != 200:
                try:
                    error_data = response.json()
                except Exception:
                    error_data = response.text
                raise Exception(
                    f"{self.provider} API error ({response.status_code}): {error_data}"
                )

            data = response.json()
            return data["choices"][0]["message"]["content"].strip()
        
    async def generate_viral_script(
        self, 
        original_text: str, 
        language: str = "ru",
    ) -> str:
        """Transform user's raw text into a viral short-form video script."""
        language_names = {
            "ru": "Russian",
            "en": "English",
            "es": "Spanish",
            "de": "German",
            "fr": "French",
            "it": "Italian",
            "pt": "Portuguese",
            "zh": "Chinese",
            "ja": "Japanese",
            "ko": "Korean",
            "ar": "Arabic",
            "hi": "Hindi",
        }
        
        target_language = language_names.get(language, "Russian")
        
        system_prompt = f"""You are a friendly TV/online journalist rewriting copy for a talking-head short (TikTok/Reels/Shorts), 30–90 seconds.

Tone: warm, clear, conversational journalist — not a hyped clickbait host.

HARD RULES (facts):
1. Do NOT invent scores, winners, brackets, injuries, quotes, dates, or any factual details that are not in the user's text.
2. Do NOT pretend you checked the internet or "bookmakers" — you have no live sports feed.
3. If the topic is unfinished, unknown, future, or the user asks "who won" without stating the winner: say honestly that the result is not known from the brief / that we cannot confirm yet, and invite the viewer to share what they know. Frame speculation only as speculation.
4. You may rephrase, compress, and add light emotional color (e.g. sympathy for Messi) ONLY around facts the user already gave.
5. Keep a short hook, clear body, soft CTA (comment/question). 100–300 words.
6. Language: {target_language}. No stage directions, timestamps, or labels — ONLY spoken script."""
        brain_block = self._brain_block(["hooks", "storytelling"])
        if brain_block:
            system_prompt = f"{system_prompt}\n\n{brain_block}"

        user_prompt = f"""Rewrite this as a friendly journalist short-form script.
Preserve meaning; invent nothing factual beyond the brief.

Brief:
{original_text}

Output only the spoken script in {target_language}."""

        return await self._chat_completion(
            system_prompt, user_prompt, temperature=0.45, max_tokens=1000
        )

    async def plan_viral_edit(
        self,
        transcript: str,
        words: list,
        duration: float,
        language: str = "ru",
        style: str = "dynamic",
    ) -> dict:
        """
        Rich edit plan for Stage 2 viral montage.

        Returns:
          {
            effects: [{effect, time, ...}],
            broll: [{time, duration, query}],
            hook: {text, use: bool} | null,
            mood: calm|energetic|motivational|dramatic,
            style: str,
          }
        """
        import json

        sample_words = words[:80] if words else []
        style = (style or "dynamic").lower()
        from app.services.video.edit_styles import get_style
        preset = get_style(style)
        style_hint = preset.get("hint") or "Balanced short-form edits."

        system_prompt = (
            "You are a short-form video editor AI for TikTok/Reels/Shorts. "
            "Return ONLY a JSON object (no markdown) with keys: "
            "effects (array), broll (array), hook (object|null), mood (string). "
            "effects items: "
            '{"effect":"zoom","time":float,"duration":float,"scale":1.08-1.2} or '
            '{"effect":"highlight_word","word":"string","time":float}. '
            "broll items (1-3 visual inserts by meaning of speech): "
            '{"time":float,"duration":1.2-2.5,"query":"english search keywords for stock photo"}. '
            "hook: if first 3 seconds are weak/generic (hello/today we talk), set "
            '{"use":true,"text":"short punchy hook grounded in the transcript"}; '
            "else {\"use\":false,\"text\":\"\"}. "
            "Never invent clickbait unrelated to transcript (no random 'musical explosion' etc). "
            "If transcript says SILENT / NO SPEECH: hook.use must be false and broll must be []. "
            "mood: one of calm, energetic, motivational, dramatic. "
            f"Style guide: {style_hint}"
        )
        brain_block = self._brain_block(["hooks", "editing"])
        if brain_block:
            system_prompt = f"{system_prompt}\n\n{brain_block}"
        user_prompt = (
            f"Language: {language}\nStyle: {style}\nDuration: {duration:.2f}s\n"
            f"Transcript:\n{transcript}\n\n"
            f"Word timings (sample):\n{json.dumps(sample_words, ensure_ascii=False)}"
        )
        content = await self._chat_completion(
            system_prompt, user_prompt, temperature=0.35, max_tokens=1600
        )
        content = content.strip()
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()

        empty = {
            "effects": [],
            "broll": [],
            "hook": None,
            "mood": "energetic" if style != "minimal" else "calm",
            "style": style,
        }
        empty["mood"] = get_style(style).get("mood_default", empty["mood"])
        try:
            data = json.loads(content)
        except Exception:
            return empty

        # Backward compatible if model returns a bare list of effects
        if isinstance(data, list):
            empty["effects"] = data
            return empty
        if not isinstance(data, dict):
            return empty

        effects = data.get("effects") if isinstance(data.get("effects"), list) else []
        broll = data.get("broll") if isinstance(data.get("broll"), list) else []
        hook = data.get("hook") if isinstance(data.get("hook"), dict) else None
        mood = str(data.get("mood") or empty["mood"]).lower()
        if mood not in {"calm", "energetic", "motivational", "dramatic"}:
            mood = empty["mood"]

        return {
            "effects": effects,
            "broll": broll[:3],
            "hook": hook,
            "mood": mood,
            "style": style,
        }

    async def translate_script(
        self,
        text: str,
        target_language: str = "en",
        source_language: str = "ru",
    ) -> str:
        """Translate spoken script for dubbing. Returns plain text only."""
        language_names = {
            "ru": "Russian",
            "en": "English",
            "es": "Spanish",
            "de": "German",
            "fr": "French",
            "it": "Italian",
            "pt": "Portuguese",
            "zh": "Chinese",
            "ja": "Japanese",
            "ko": "Korean",
            "ar": "Arabic",
            "hi": "Hindi",
        }
        src = language_names.get(source_language[:2], source_language)
        dst = language_names.get(target_language[:2], target_language)
        system_prompt = (
            f"Translate spoken video narration from {src} to {dst}. "
            "Keep it natural for voiceover, same meaning, no quotes, no markdown, "
            "no stage directions. Output only the translated spoken text."
        )
        return await self._chat_completion(
            system_prompt, text, temperature=0.2, max_tokens=2000
        )

    async def plan_ai_clips(
        self,
        transcript: str,
        segments: list,
        duration: float,
        language: str = "ru",
        max_clips: int = 5,
    ) -> list:
        """
        Pick highlight windows for Shorts from a long transcript.
        Returns: [{start, end, title, reason, score}]
        """
        import json

        sample = segments[:120] if segments else []
        system_prompt = (
            "You are a short-form clip editor. From a long video transcript, "
            "pick the most interesting standalone moments for TikTok/Reels/Shorts. "
            f"Return ONLY a JSON array with up to {max_clips} items: "
            '{"start":float,"end":float,"title":"short title","reason":"why it works","score":0-100}. '
            "Each clip must be 20-60 seconds. Prefer hooks, surprising claims, clear stories, CTAs. "
            "Avoid silence/filler intros. No markdown."
        )
        brain_block = self._brain_block(
            ["hooks", "editing", "storytelling"],
            prefer_tags=["long_to_short", "clips"],
        )
        if brain_block:
            system_prompt = f"{system_prompt}\n\n{brain_block}"
        user_prompt = (
            f"Language: {language}\nTotal duration: {duration:.1f}s\n"
            f"Transcript:\n{transcript[:6000]}\n\n"
            f"Segments sample:\n{json.dumps(sample, ensure_ascii=False)[:5000]}"
        )
        try:
            content = await self._chat_completion(
                system_prompt, user_prompt, temperature=0.35, max_tokens=1400
            )
            content = content.strip()
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
                content = content.strip()
            data = json.loads(content)
            if isinstance(data, dict):
                data = [data]
            elif not isinstance(data, list):
                data = []
            clips = []
            target_len = 25.0 if duration >= 30 else max(8.0, duration * 0.8)
            for item in data:
                if not isinstance(item, dict):
                    continue
                start = float(item.get("start") or 0)
                end = float(item.get("end") or 0)
                if end <= start:
                    end = start + target_len
                if end - start < min(15.0, target_len):
                    end = start + target_len
                if end - start > 70:
                    end = start + 55
                # Clamp into timeline instead of dropping (LLM often returns short/overhanging windows)
                start = max(0.0, min(start, max(0.0, duration - 3.0)))
                end = min(max(end, start + min(target_len, duration - start)), duration)
                if end - start < 3:
                    continue
                clips.append({
                    "start": round(start, 2),
                    "end": round(end, 2),
                    "title": str(item.get("title") or "Клип")[:80],
                    "reason": str(item.get("reason") or "")[:160],
                    "score": int(max(0, min(100, float(item.get("score") or 70)))),
                })
            clips = sorted(clips, key=lambda c: c.get("score", 0), reverse=True)[:max_clips]
            if clips:
                return clips
        except Exception:
            pass
        # Heuristic fallback: evenly spaced windows if LLM fails / returns nothing usable
        if duration < 25:
            return [{
                "start": 0,
                "end": round(duration, 2),
                "title": "Полный фрагмент",
                "reason": "короткое видео",
                "score": 60,
            }]
        out = []
        step = max(duration / max_clips, 35)
        t = 5.0
        while t + 25 < duration and len(out) < max_clips:
            out.append({
                "start": round(t, 2),
                "end": round(min(t + 35, duration), 2),
                "title": f"Момент {len(out)+1}",
                "reason": "автовыбор",
                "score": 55,
            })
            t += step
        if not out and duration >= 8:
            out = [{
                "start": 0,
                "end": round(min(duration, 45), 2),
                "title": "Клип",
                "reason": "автовыбор",
                "score": 50,
            }]
        return out

    async def score_virality(
        self,
        transcript: str,
        duration: float,
        language: str = "ru",
        style: str = "dynamic",
        has_hook: bool = False,
        broll_count: int = 0,
        zoom_count: int = 0,
    ) -> dict:
        """
        Score retention potential after montage.
        Returns: {score, hook_score, pacing_score, cta_score, tips:[...], summary}
        """
        import json

        system_prompt = (
            "You are a short-form growth editor for TikTok/Reels/Shorts. "
            "Score the edited video's viral potential. Return ONLY JSON: "
            '{"score":0-100,"hook_score":0-100,"pacing_score":0-100,'
            '"cta_score":0-100,"summary":"1 short sentence in the video language",'
            '"tips":["3-5 concrete improvement tips in the video language"]}. '
            "Be honest: weak openings, long sentences, no CTA, slow pacing lower the score."
        )
        user_prompt = (
            f"Language: {language}\nStyle: {style}\nDuration: {duration:.1f}s\n"
            f"Has AI hook overlay: {has_hook}\nB-roll inserts: {broll_count}\nZooms: {zoom_count}\n\n"
            f"Transcript:\n{transcript}"
        )
        fallback = {
            "score": 62,
            "hook_score": 55 if not has_hook else 75,
            "pacing_score": 60,
            "cta_score": 50,
            "summary": "Ролик собран; усильте первые 3 секунды и призыв к действию.",
            "tips": [
                "Сделайте более цепляющее начало",
                "Укоротите длинные фразы",
                "Добавьте явный CTA в конце",
            ],
        }
        try:
            content = await self._chat_completion(
                system_prompt, user_prompt, temperature=0.3, max_tokens=900
            )
            content = content.strip()
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
                content = content.strip()
            data = json.loads(content)
            if not isinstance(data, dict):
                return fallback
            score = int(max(0, min(100, float(data.get("score") or fallback["score"]))))
            tips = data.get("tips") if isinstance(data.get("tips"), list) else fallback["tips"]
            return {
                "score": score,
                "hook_score": int(max(0, min(100, float(data.get("hook_score") or 0)))),
                "pacing_score": int(max(0, min(100, float(data.get("pacing_score") or 0)))),
                "cta_score": int(max(0, min(100, float(data.get("cta_score") or 0)))),
                "summary": str(data.get("summary") or fallback["summary"])[:240],
                "tips": [str(t)[:160] for t in tips[:5]],
            }
        except Exception:
            return fallback

    async def analyze_trend_video(
        self,
        transcript: str,
        duration: float,
        language: str = "ru",
        title: str = "",
    ) -> dict:
        """
        Study a trending short for patterns.
        Returns hook, style_guess, pace tips, etc.
        """
        import json

        system_prompt = (
            "You are a short-form content researcher for TikTok/Reels/Shorts. "
            "Analyze the transcript of a trending video and extract reusable patterns. "
            "Return ONLY JSON: "
            '{"hook_text":"first-3s style hook in video language",'
            '"style_guess":"dynamic|minimal|ads|beast|hormozi",'
            '"pace_wpm":number,'
            '"summary":"2 sentences in video language",'
            '"tips":["3-5 concrete tips in video language for making a similar original video"],'
            '"cta":"typical CTA pattern or empty",'
            '"themes":["1-5 topic tags"]}. '
            "Do NOT suggest copying the video 1:1. Focus on structure and retention patterns."
        )
        user_prompt = (
            f"Language: {language}\nDuration: {duration:.1f}s\n"
            f"Title: {title or 'n/a'}\n\nTranscript:\n{transcript[:6000]}"
        )
        fallback = {
            "hook_text": "Смотри до конца — это важно",
            "style_guess": "dynamic",
            "pace_wpm": 140.0,
            "summary": "Короткий ролик с разговорным темпом; усильте первые секунды.",
            "tips": [
                "Цепляющий хук в первые 3 секунды",
                "Короткие фразы и динамичные кадры",
                "Явный CTA в конце",
            ],
            "cta": "",
            "themes": [],
        }
        try:
            content = await self._chat_completion(
                system_prompt, user_prompt, temperature=0.35, max_tokens=900
            )
            content = (content or "").strip()
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
                content = content.strip()
            data = json.loads(content)
            if not isinstance(data, dict):
                return fallback
            style = str(data.get("style_guess") or "dynamic").lower().strip()
            if style not in {"dynamic", "minimal", "ads", "beast", "hormozi"}:
                style = "dynamic"
            tips = data.get("tips") if isinstance(data.get("tips"), list) else fallback["tips"]
            themes = data.get("themes") if isinstance(data.get("themes"), list) else []
            try:
                pace = float(data.get("pace_wpm") or fallback["pace_wpm"])
            except (TypeError, ValueError):
                pace = fallback["pace_wpm"]
            return {
                "hook_text": str(data.get("hook_text") or fallback["hook_text"])[:300],
                "style_guess": style,
                "pace_wpm": max(60.0, min(280.0, pace)),
                "summary": str(data.get("summary") or fallback["summary"])[:500],
                "tips": [str(t)[:200] for t in tips[:5]],
                "cta": str(data.get("cta") or "")[:200],
                "themes": [str(t)[:40] for t in themes[:5]],
            }
        except Exception:
            return fallback

    async def generate_subtitles_text(self, script: str) -> list:
        """Break script into subtitle segments for SRT generation."""
        system_prompt = """Break the following script into subtitle segments.
Each segment should be 1-2 short sentences or phrases (max 10 words per line).
Return as JSON array with format: [{"text": "segment text", "word_count": N}, ...]
Only return the JSON array, no other text."""

        content = await self._chat_completion(
            system_prompt, script, temperature=0.3, max_tokens=2000
        )

        import json

        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]

        return json.loads(content)
