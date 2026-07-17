import httpx
from typing import Optional
from app.core.config import settings


class OpenAIService:
    """Service for GPT-4o API integration."""
    
    def __init__(self):
        self.api_key = settings.OPENAI_API_KEY
        self.base_url = "https://api.openai.com/v1"
        
    async def generate_viral_script(
        self, 
        original_text: str, 
        language: str = "ru"
    ) -> str:
        """
        Transform user's raw text into a viral short-form video script.
        
        Args:
            original_text: User's original text/idea
            language: Target language code (ISO 639-1)
            
        Returns:
            Generated viral script optimized for short-form video
        """
        if not self.api_key:
            raise ValueError("OpenAI API key not configured")
        
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
            "hi": "Hindi"
        }
        
        target_language = language_names.get(language, "Russian")
        
        system_prompt = f"""You are an expert viral video scriptwriter specializing in short-form content (TikTok, Reels, Shorts).

Your task is to transform the user's raw text into a compelling, viral-worthy script for a talking head video (30-90 seconds).

Guidelines:
1. HOOK: Start with an attention-grabbing hook in the first 3 seconds
2. STRUCTURE: Problem → Agitation → Solution or Story → Lesson format
3. LANGUAGE: Conversational, energetic, use power words
4. LENGTH: 100-300 words optimal for 30-90 second video
5. PACING: Short sentences, natural pauses, emotional peaks
6. CTA: End with a clear call-to-action or thought-provoking question

The script must be written in {target_language}.
Do NOT include any stage directions, timestamps, or formatting.
Output ONLY the spoken script text that will be read by the AI avatar."""

        user_prompt = f"""Transform this into a viral video script:

{original_text}

Remember: Output only the script text in {target_language}, no directions or formatting."""

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "gpt-4o",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    "temperature": 0.8,
                    "max_tokens": 1000
                }
            )
            
            if response.status_code != 200:
                error_data = response.json()
                raise Exception(f"OpenAI API error: {error_data}")
            
            data = response.json()
            script = data["choices"][0]["message"]["content"].strip()
            
            return script
    
    async def generate_subtitles_text(self, script: str) -> list:
        """
        Break script into subtitle segments with timestamps.
        This is used for SRT generation.
        """
        if not self.api_key:
            raise ValueError("OpenAI API key not configured")
        
        system_prompt = """Break the following script into subtitle segments.
Each segment should be 1-2 short sentences or phrases (max 10 words per line).
Return as JSON array with format: [{"text": "segment text", "word_count": N}, ...]
Only return the JSON array, no other text."""

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "gpt-4o",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": script}
                    ],
                    "temperature": 0.3,
                    "max_tokens": 2000
                }
            )
            
            if response.status_code != 200:
                error_data = response.json()
                raise Exception(f"OpenAI API error: {error_data}")
            
            data = response.json()
            content = data["choices"][0]["message"]["content"].strip()
            
            import json
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
            
            segments = json.loads(content)
            return segments
