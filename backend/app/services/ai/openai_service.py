import httpx
from app.core.config import settings


class OpenAIService:
    """
    Script LLM via OpenAI-compatible chat completions.
    Prefers AITUNNEL (₽); falls back to OpenAI.
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
        genre: str = "default",
        product_brief: dict | None = None,
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
        from app.services.video.content_presets import get_genre, normalize_genre

        genre = normalize_genre(genre)
        genre_hint = get_genre(genre).get("hint") or ""
        
        system_prompt = f"""You are a friendly TV/online journalist rewriting copy for a talking-head short (TikTok/Reels/Shorts), 30–90 seconds.

Tone: warm, clear, conversational journalist — not a hyped clickbait host.

HARD RULES (facts):
1. Do NOT invent scores, winners, brackets, injuries, quotes, dates, or any factual details that are not in the user's text.
2. Do NOT pretend you checked the internet or "bookmakers" — you have no live sports feed.
3. If the topic is unfinished, unknown, future, or the user asks "who won" without stating the winner: say honestly that the result is not known from the brief / that we cannot confirm yet, and invite the viewer to share what they know. Frame speculation only as speculation.
4. You may rephrase, compress, and add light emotional color (e.g. sympathy for Messi) ONLY around facts the user already gave.
5. Keep a short hook, clear body, soft CTA (comment/question). 100–300 words.
6. Language: {target_language}. No stage directions, timestamps, or labels — ONLY spoken script.
7. Genre guide: {genre_hint}"""
        prefer = list(get_genre(genre).get("brain_tags") or [])
        brain_block = self._brain_block(
            ["hooks", "storytelling"],
            prefer_tags=prefer or None,
        )
        if brain_block:
            system_prompt = f"{system_prompt}\n\n{brain_block}"

        brief_extra = ""
        if product_brief and isinstance(product_brief, dict):
            bullets = product_brief.get("bullets") or []
            bullets_txt = "\n".join(f"- {b}" for b in bullets[:5])
            brief_extra = (
                f"\n\nProduct brief (use only these facts):\n"
                f"Title: {product_brief.get('title') or ''}\n"
                f"Summary: {product_brief.get('summary') or ''}\n"
                f"Benefits:\n{bullets_txt}\n"
                f"URL: {product_brief.get('url') or ''}"
            )

        user_prompt = f"""Rewrite this as a friendly journalist short-form script.
Preserve meaning; invent nothing factual beyond the brief.

Brief:
{original_text}
{brief_extra}

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
        genre: str = "default",
        platform: str = "auto",
        style_hint_override: str | None = None,
        hook_variants: int = 1,
        lite: bool = False,
    ) -> dict:
        """
        Rich edit plan for Stage 2 viral montage.

        Returns:
          {
            effects: [{effect, time, ...}],
            broll: [{time, duration, query}],
            hook: {text, use: bool} | null,
            hook_variants: [str, ...],
            mood: calm|energetic|motivational|dramatic,
            style: str,
          }
        """
        import json

        from app.services.video.content_presets import (
            get_genre,
            get_platform,
            normalize_genre,
            normalize_platform,
        )
        from app.services.video.edit_styles import get_style

        sample_words = words[:80] if words else []
        style = (style or "dynamic").lower()
        genre = normalize_genre(genre)
        platform = normalize_platform(platform)
        hook_n = max(1, min(int(hook_variants or 1), 3))
        preset = get_style(style)
        style_hint = style_hint_override or preset.get("hint") or "Balanced short-form edits."
        genre_hint = get_genre(genre).get("hint") or ""
        plat = get_platform(platform)
        platform_hint = plat.get("caption_safe_hint") or ""
        lite_note = (
            "Lite mode: prefer fewer zooms, empty broll [], strong opening hook only."
            if lite
            else ""
        )

        system_prompt = (
            "You are a short-form video editor AI for TikTok/Reels/Shorts. "
            "Return ONLY a JSON object (no markdown) with keys: "
            "effects (array), broll (array), hook (object|null), "
            "hook_variants (array of strings), mood (string). "
            "effects items: "
            '{"effect":"zoom","time":float,"duration":float,"scale":1.08-1.2} or '
            '{"effect":"highlight_word","word":"string","time":float}. '
            "broll items (1-3 visual inserts by meaning of speech): "
            '{"time":float,"duration":1.2-2.5,"query":"english search keywords for stock photo",'
            '"size":"small|medium|large","position":"top_left|top_right|bottom_left|bottom_right|bottom_bar"}. '
            "size/position: pick per-insert based on how important that visual beat is — "
            "\"small\" or \"medium\" (a corner card, any of the 4 corner positions) is the default "
            "for most inserts, so the speaker's face stays clearly visible and the frame doesn't "
            "feel cluttered; use \"large\" (position must then be \"bottom_bar\") at most ONCE per "
            "video, only for a genuinely important visual moment. Never schedule two broll windows "
            "that overlap in time. For a light/funny beat, the query can lean reaction/meme-style "
            "wording instead of a literal description — still in English, still stock-searchable. "
            "hook: if first 3 seconds are weak/generic (hello/today we talk), set "
            '{"use":true,"text":"short punchy hook grounded in the transcript"}; '
            "else {\"use\":false,\"text\":\"\"}. "
            f"hook_variants: up to {hook_n} distinct short A/B hook lines (same meaning, different wording); "
            "first should match hook.text when hook.use is true. "
            "Never invent clickbait unrelated to transcript (no random 'musical explosion' etc). "
            "If transcript says SILENT / NO SPEECH: hook.use must be false, hook_variants=[], and broll must be []. "
            "mood: one of calm, energetic, motivational, dramatic. "
            f"Style guide: {style_hint} "
            f"Genre guide: {genre_hint} "
            f"Platform guide: {platform_hint} "
            f"{lite_note}"
        )
        prefer_tags = list(
            dict.fromkeys(
                ["shorts_viral", "retention", "pace"]
                + list(plat.get("brain_tags") or [])
                + list(get_genre(genre).get("brain_tags") or [])
            )
        )
        brain_block = self._brain_block(
            ["hooks", "editing", "platforms"],
            prefer_tags=prefer_tags,
        )
        if brain_block:
            system_prompt = f"{system_prompt}\n\n{brain_block}"
        user_prompt = (
            f"Language: {language}\nStyle: {style}\nGenre: {genre}\n"
            f"Platform: {platform}\nDuration: {duration:.2f}s\n"
            f"Transcript:\n{transcript}\n\n"
            f"Word timings (sample):\n{json.dumps(sample_words, ensure_ascii=False)}"
        )
        content = await self._chat_completion(
            system_prompt, user_prompt, temperature=0.35, max_tokens=1800
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
            "hook_variants": [],
            "mood": "energetic" if style != "minimal" else "calm",
            "style": style,
        }
        empty["mood"] = get_style(style).get("mood_default", empty["mood"])
        # Let a malformed-JSON response propagate — ViralEditPipeline.process()
        # already wraps this call in its own try/except and records
        # api_responses["edit_plan_error"]. Swallowing it here used to mean
        # that except block never fired for the single most likely failure
        # mode (bad LLM JSON), so a bare video shipped with no trace of why.
        data = json.loads(content)

        # Backward compatible if model returns a bare list of effects
        if isinstance(data, list):
            empty["effects"] = data
            return empty
        if not isinstance(data, dict):
            return empty

        effects = data.get("effects") if isinstance(data.get("effects"), list) else []
        broll = data.get("broll") if isinstance(data.get("broll"), list) else []
        if lite:
            broll = []
        hook = data.get("hook") if isinstance(data.get("hook"), dict) else None
        mood = str(data.get("mood") or empty["mood"]).lower()
        if mood not in {"calm", "energetic", "motivational", "dramatic"}:
            mood = empty["mood"]

        variants_raw = data.get("hook_variants") if isinstance(data.get("hook_variants"), list) else []
        variants: list[str] = []
        for v in variants_raw:
            t = str(v or "").strip()[:90]
            if t and t not in variants:
                variants.append(t)
        if hook and hook.get("use") and hook.get("text"):
            primary = str(hook.get("text") or "").strip()[:90]
            if primary and primary not in variants:
                variants.insert(0, primary)
        variants = variants[:hook_n]

        return {
            "effects": effects,
            "broll": broll[:3],
            "hook": hook,
            "hook_variants": variants,
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
        platform: str = "auto",
    ) -> list:
        """
        Pick highlight windows for Shorts from a long transcript.

        Long transcripts are chunked by time (map) — each chunk gets its own
        LLM call over its own slice of segments — then all chunks' candidates
        are merged, deduped by overlap, and pruned to max_clips by score
        (reduce). Previously the transcript was hard-truncated to the first
        ~6000 chars (roughly the first 6-10 minutes for spoken content), so
        for anything longer — podcasts, streams — highlights past that point
        were never even seen by the model.

        Returns: [{start, end, title, reason, score, moment}]
        """
        from app.services.video.content_presets import (
            clip_duration_bounds,
            get_platform,
            normalize_platform,
        )

        platform = normalize_platform(platform)
        plat = get_platform(platform)
        clip_min, clip_max, clip_target = clip_duration_bounds(platform)

        chunk_char_limit = 5500
        all_candidates: list = []
        if segments and len(transcript) > chunk_char_limit:
            chunks = self._chunk_segments_by_chars(segments, chunk_char_limit)
            per_chunk_budget = max(2, (max_clips // max(len(chunks), 1)) + 1)
            for chunk_segments in chunks:
                chunk_text = " ".join(s.get("text", "") for s in chunk_segments).strip()
                if not chunk_text:
                    continue
                all_candidates.extend(
                    await self._plan_ai_clips_call(
                        transcript=chunk_text,
                        segments=chunk_segments,
                        duration=duration,
                        language=language,
                        max_clips=per_chunk_budget,
                        platform=platform,
                        plat=plat,
                        clip_min=clip_min,
                        clip_max=clip_max,
                        clip_target=clip_target,
                    )
                )
        else:
            all_candidates = await self._plan_ai_clips_call(
                transcript=transcript,
                segments=segments,
                duration=duration,
                language=language,
                max_clips=max_clips,
                platform=platform,
                plat=plat,
                clip_min=clip_min,
                clip_max=clip_max,
                clip_target=clip_target,
            )

        if all_candidates:
            all_candidates.sort(key=lambda c: c.get("score", 0), reverse=True)
            picked: list = []
            for c in all_candidates:
                if any(self._clip_windows_overlap(c, p) for p in picked):
                    continue
                picked.append(c)
                if len(picked) >= max_clips:
                    break
            if picked:
                return picked

        # Heuristic fallback: evenly spaced windows if LLM fails / returns nothing usable
        if duration < clip_min:
            return [{
                "start": 0,
                "end": round(duration, 2),
                "title": "Полный фрагмент",
                "reason": "короткое видео",
                "score": 60,
                "moment": "other",
            }]
        out = []
        step = max(duration / max_clips, clip_target)
        t = 5.0
        while t + clip_min < duration and len(out) < max_clips:
            out.append({
                "start": round(t, 2),
                "end": round(min(t + clip_target, duration), 2),
                "title": f"Момент {len(out)+1}",
                "reason": "автовыбор",
                "score": 55,
                "moment": "other",
            })
            t += step
        if not out and duration >= 8:
            out = [{
                "start": 0,
                "end": round(min(duration, clip_max), 2),
                "title": "Клип",
                "reason": "автовыбор",
                "score": 50,
                "moment": "other",
            }]
        return out

    @staticmethod
    def _chunk_segments_by_chars(segments: list, target_chars: int) -> list:
        """Group time-ordered transcript segments into ~target_chars-sized batches."""
        chunks: list = []
        current: list = []
        current_len = 0
        for seg in segments:
            seg_len = len(seg.get("text") or "")
            if current and current_len + seg_len > target_chars:
                chunks.append(current)
                current = []
                current_len = 0
            current.append(seg)
            current_len += seg_len
        if current:
            chunks.append(current)
        return chunks

    @staticmethod
    def _clip_windows_overlap(a: dict, b: dict) -> bool:
        return not (float(a["end"]) <= float(b["start"]) or float(b["end"]) <= float(a["start"]))

    async def _plan_ai_clips_call(
        self,
        transcript: str,
        segments: list,
        duration: float,
        language: str,
        max_clips: int,
        platform: str,
        plat: dict,
        clip_min: float,
        clip_max: float,
        clip_target: float,
    ) -> list:
        """One LLM call over a (possibly chunk-sized) transcript slice.

        Returns [] on any failure — the caller (plan_ai_clips) decides whether
        to try other chunks or fall back to the evenly-spaced heuristic.
        """
        import json

        from app.services.video.content_presets import normalize_moment_label

        sample = segments[:120] if segments else []
        system_prompt = (
            "You are a short-form clip editor. From a long video transcript, "
            "pick the most interesting standalone moments for TikTok/Reels/Shorts. "
            f"Return ONLY a JSON array with up to {max_clips} items: "
            '{"start":float,"end":float,"title":"short title","reason":"why it works",'
            '"score":0-100,"moment":"hook|punchline|story|cta|debate|tip|other"}. '
            f"Each clip should be about {clip_target:.0f}s "
            f"(hard range {clip_min:.0f}-{clip_max:.0f}s). "
            "Prefer hooks, surprising claims, clear stories, CTAs. "
            "Label each moment type accurately. "
            f"Platform: {platform}. {plat.get('caption_safe_hint') or ''} "
            "Avoid silence/filler intros. No markdown."
        )
        prefer_tags = list(
            dict.fromkeys(
                ["long_to_short", "clips", "shorts_viral"]
                + list(plat.get("brain_tags") or [])
            )
        )
        brain_block = self._brain_block(
            ["hooks", "editing", "storytelling", "platforms"],
            prefer_tags=prefer_tags,
        )
        if brain_block:
            system_prompt = f"{system_prompt}\n\n{brain_block}"
        user_prompt = (
            f"Language: {language}\nPlatform: {platform}\nTotal duration: {duration:.1f}s\n"
            f"Transcript:\n{transcript[:6000]}\n\n"
            f"Segments sample:\n{json.dumps(sample, ensure_ascii=False)[:5000]}"
        )
        try:
            content = await self._chat_completion(
                system_prompt, user_prompt, temperature=0.35, max_tokens=1600
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
            target_len = clip_target if duration >= clip_min else max(8.0, duration * 0.8)
            for item in data:
                if not isinstance(item, dict):
                    continue
                start = float(item.get("start") or 0)
                end = float(item.get("end") or 0)
                if end <= start:
                    end = start + target_len
                if end - start < min(clip_min, target_len):
                    end = start + target_len
                if end - start > clip_max + 10:
                    end = start + clip_max
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
                    "moment": normalize_moment_label(item.get("moment")),
                })
            return clips
        except Exception:
            return []

    async def brief_from_product_url(
        self,
        product_url: str,
        language: str = "ru",
    ) -> dict:
        """
        Fetch a product/landing page and build a short marketing brief for avatar scripts.
        Returns: {title, summary, bullets, raw_excerpt, url}
        """
        import json
        import re
        from urllib.parse import urlparse

        url = (product_url or "").strip()
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Invalid product URL")

        async with httpx.AsyncClient(
            timeout=25.0,
            follow_redirects=True,
            headers={"User-Agent": "VideoGenBot/1.0 (+product-brief)"},
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text[:120_000]

        def _meta(name: str) -> str:
            patterns = [
                rf'<meta[^>]+property=["\']{re.escape(name)}["\'][^>]+content=["\']([^"\']+)',
                rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']{re.escape(name)}["\']',
                rf'<meta[^>]+name=["\']{re.escape(name)}["\'][^>]+content=["\']([^"\']+)',
                rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']{re.escape(name)}["\']',
            ]
            for pat in patterns:
                m = re.search(pat, html, flags=re.I)
                if m:
                    return re.sub(r"\s+", " ", m.group(1)).strip()
            return ""

        title_m = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.I | re.S)
        title = re.sub(r"\s+", " ", (title_m.group(1) if title_m else "")).strip()[:200]
        desc = _meta("og:description") or _meta("description")
        og_title = _meta("og:title")
        if og_title:
            title = og_title[:200] or title
        textish = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.I)
        textish = re.sub(r"<style[\s\S]*?</style>", " ", textish, flags=re.I)
        textish = re.sub(r"<[^>]+>", " ", textish)
        textish = re.sub(r"\s+", " ", textish).strip()[:3500]
        excerpt = f"Title: {title}\nDescription: {desc}\nBody: {textish}"[:4000]

        language_names = {
            "ru": "Russian",
            "en": "English",
            "es": "Spanish",
            "de": "German",
            "fr": "French",
        }
        lang_name = language_names.get((language or "ru")[:2], "Russian")
        system_prompt = (
            "Extract a concise product marketing brief from page text. "
            "Return ONLY JSON: "
            '{"title":"string","summary":"2-4 sentences","bullets":["benefit1","benefit2","benefit3"]}. '
            f"Write summary and bullets in {lang_name}. "
            "Do not invent pricing, awards, or claims absent from the text."
        )
        brain_block = self._brain_block(["hooks", "storytelling"], prefer_tags=["ugc", "cta"])
        if brain_block:
            system_prompt = f"{system_prompt}\n\n{brain_block}"
        content = await self._chat_completion(
            system_prompt,
            f"URL: {url}\n\n{excerpt}",
            temperature=0.3,
            max_tokens=700,
        )
        content = content.strip()
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()
        try:
            data = json.loads(content)
        except Exception:
            data = {}
        bullets = data.get("bullets") if isinstance(data.get("bullets"), list) else []
        bullets = [str(b).strip()[:120] for b in bullets if str(b).strip()][:5]
        return {
            "url": url,
            "title": str(data.get("title") or title or parsed.netloc)[:200],
            "summary": str(data.get("summary") or desc or "")[:800],
            "bullets": bullets,
            "raw_excerpt": excerpt[:1500],
        }

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
