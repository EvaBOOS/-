"""
Remote asset library — no manual sticker downloads.

Sources:
  - Stickers: Twemoji CDN (PNG), picked by topic keywords
  - Photos:   Pexels API (optional key) / Unsplash (optional key)
  - Fonts:    Google Fonts via jsDelivr fontsource (cached locally)
"""
from __future__ import annotations

import hashlib
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.core.config import settings


# Curated display fonts that look good on vertical shorts
GOOGLE_FONTS: List[Dict[str, Any]] = [
    {
        "id": "montserrat",
        "family": "Montserrat",
        "weight": 700,
        "label": "Montserrat Bold",
        "vibe": "modern clean",
        "source": "google",
        "url": "https://cdn.jsdelivr.net/fontsource/fonts/montserrat@5.2.5/latin-700-normal.ttf",
    },
    {
        "id": "bebas-neue",
        "family": "Bebas Neue",
        "weight": 400,
        "label": "Bebas Neue",
        "vibe": "bold headlines",
        "source": "google",
        "url": "https://cdn.jsdelivr.net/fontsource/fonts/bebas-neue@5.2.5/latin-400-normal.ttf",
    },
    {
        "id": "oswald",
        "family": "Oswald",
        "weight": 600,
        "label": "Oswald SemiBold",
        "vibe": "sports news",
        "source": "google",
        "url": "https://cdn.jsdelivr.net/fontsource/fonts/oswald@5.2.5/latin-600-normal.ttf",
    },
    {
        "id": "rubik",
        "family": "Rubik",
        "weight": 700,
        "label": "Rubik Bold",
        "vibe": "friendly round",
        "source": "google",
        "url": "https://cdn.jsdelivr.net/fontsource/fonts/rubik@5.2.5/latin-700-normal.ttf",
    },
    {
        "id": "inter",
        "family": "Inter",
        "weight": 700,
        "label": "Inter Bold",
        "vibe": "tech product",
        "source": "google",
        "url": "https://cdn.jsdelivr.net/fontsource/fonts/inter@5.2.5/latin-700-normal.ttf",
    },
    {
        "id": "playfair-display",
        "family": "Playfair Display",
        "weight": 700,
        "label": "Playfair Display",
        "vibe": "editorial luxury",
        "source": "google",
        "url": "https://cdn.jsdelivr.net/fontsource/fonts/playfair-display@5.2.5/latin-700-normal.ttf",
    },
    {
        "id": "righteous",
        "family": "Righteous",
        "weight": 400,
        "label": "Righteous",
        "vibe": "viral fun",
        "source": "google",
        "url": "https://cdn.jsdelivr.net/fontsource/fonts/righteous@5.2.5/latin-400-normal.ttf",
    },
    {
        "id": "exo-2",
        "family": "Exo 2",
        "weight": 700,
        "label": "Exo 2 Bold",
        "vibe": "sci-fi energy",
        "source": "google",
        "url": "https://cdn.jsdelivr.net/fontsource/fonts/exo-2@5.2.5/latin-700-normal.ttf",
    },
    {
        "id": "anton",
        "family": "Anton",
        "weight": 400,
        "label": "Anton",
        "vibe": "youtube punchy",
        "source": "google",
        "url": "https://cdn.jsdelivr.net/fontsource/fonts/anton@5.2.5/latin-400-normal.ttf",
    },
    {
        "id": "archivo-black",
        "family": "Archivo Black",
        "weight": 400,
        "label": "Archivo Black",
        "vibe": "heavy shorts captions",
        "source": "google",
        "url": "https://cdn.jsdelivr.net/fontsource/fonts/archivo-black@5.2.5/latin-400-normal.ttf",
    },
    {
        "id": "rubik-mono-one",
        "family": "Rubik Mono One",
        "weight": 400,
        "label": "Rubik Mono One",
        "vibe": "bold mono viral",
        "source": "google",
        "url": "https://cdn.jsdelivr.net/fontsource/fonts/rubik-mono-one@5.2.5/latin-400-normal.ttf",
    },
    {
        "id": "bangers",
        "family": "Bangers",
        "weight": 400,
        "label": "Bangers",
        "vibe": "comic energy",
        "source": "google",
        "url": "https://cdn.jsdelivr.net/fontsource/fonts/bangers@5.2.5/latin-400-normal.ttf",
    },
]

# Local packs — PRODUCTION ONLY (OFL / clearly redistributable).
# Typodermic Coolvetica & unclear packs stay on disk but are NOT in catalog.
LOCAL_FONTS: List[Dict[str, Any]] = [
    {
        "id": "perun",
        "family": "Perun",
        "weight": 400,
        "label": "Perun",
        "vibe": "cyrillic display",
        "source": "local",
        "relpath": os.path.join("perun", "Perun.otf"),
        "ext": "otf",
        "license": "OFL",
    },
    {
        "id": "perun-bold",
        "family": "Perun",
        "weight": 700,
        "label": "Perun Bold",
        "vibe": "cyrillic bold captions",
        "source": "local",
        "relpath": os.path.join("perun", "Perun-Bold.otf"),
        "ext": "otf",
        "license": "OFL",
    },
    {
        "id": "la-belle-aurore",
        "family": "La Belle Aurore",
        "weight": 400,
        "label": "La Belle Aurore",
        "vibe": "handwritten soft",
        "source": "local",
        "relpath": os.path.join("La_Belle_Aurore", "LaBelleAurore-Regular.ttf"),
        "ext": "ttf",
        "license": "OFL",
    },
    {
        "id": "doulos-sil",
        "family": "Doulos SIL",
        "weight": 400,
        "label": "Doulos SIL",
        "vibe": "multilingual serif",
        "source": "local",
        "relpath": os.path.join("doulos", "DoulosSIL-5.000", "DoulosSIL-R.ttf"),
        "ext": "ttf",
        "license": "OFL",
    },
]


def all_catalog_fonts() -> List[Dict[str, Any]]:
    return list(GOOGLE_FONTS) + list(LOCAL_FONTS)

# Topic → Twemoji codepoints (no key, always works)
THEME_EMOJIS: Dict[str, List[str]] = {
    "sport": ["26bd", "1f3c6", "1f3c5", "1f525", "1f4aa"],
    "football": ["26bd", "1f3c6", "1f3c5", "1f389"],
    "soccer": ["26bd", "1f3c6", "26a1"],
    "basketball": ["1f3c0", "1f3c6", "1f525"],
    "tech": ["1f4bb", "1f916", "26a1", "1f680"],
    "ai": ["1f916", "1f4a1", "2728", "1f4bb"],
    "money": ["1f4b0", "1f4b8", "1f4b5", "1f911"],
    "business": ["1f4bc", "1f4c8", "1f4b0", "1f680"],
    "food": ["1f355", "1f37a", "1f525", "1f60b"],
    "news": ["1f4f0", "1f4e2", "26a1", "1f525"],
    "music": ["1f3b5", "1f3a4", "1f525", "2728"],
    "travel": ["2708", "1f30d", "1f4ab", "2728"],
    "gaming": ["1f3ae", "1f525", "1f3c6", "26a1"],
    "health": ["1f4aa", "2764", "1f33f", "2728"],
    "fashion": ["1f457", "2728", "1f48e", "1f525"],
    "default": ["2728", "1f525", "2b50", "1f4a5", "26a1"],
}

TWEMOJI_CDN = (
    "https://cdn.jsdelivr.net/gh/twitter/twemoji@14.0.2/assets/72x72/{code}.png"
)

# RU/EN keyword → theme
_KEYWORD_THEME: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"футбол|soccer|football|чемпионат|world.?cup|гол|матч", re.I), "football"),
    (re.compile(r"баскетбол|basketball|nba", re.I), "basketball"),
    (re.compile(r"спорт|олимпи|champion|trophy|кубок", re.I), "sport"),
    (re.compile(r"\bai\b|нейросет|chatgpt|ии\b|artificial", re.I), "ai"),
    (re.compile(r"технолог|gadget|смартфон|software|стартап", re.I), "tech"),
    (re.compile(r"деньг|крипт|инвест|финанс|money|bitcoin|доллар", re.I), "money"),
    (re.compile(r"бизнес|market|продаж|startup", re.I), "business"),
    (re.compile(r"еда|ресторан|рецепт|food|cook", re.I), "food"),
    (re.compile(r"новост|breaking|журналист|news", re.I), "news"),
    (re.compile(r"музык|трек|концерт|music|dj\b", re.I), "music"),
    (re.compile(r"путешеств|travel|туризм|отпуск", re.I), "travel"),
    (re.compile(r"игр|gaming|киберспорт|steam", re.I), "gaming"),
    (re.compile(r"здоров|фитнес|спортзал|health|yoga", re.I), "health"),
    (re.compile(r"мод|fashion|стиль|outfit", re.I), "fashion"),
]

_FONT_BY_THEME: Dict[str, str] = {
    "sport": "anton",
    "football": "archivo-black",
    "basketball": "oswald",
    "tech": "inter",
    "ai": "exo-2",
    "money": "montserrat",
    "business": "montserrat",
    "food": "rubik",
    "news": "bebas-neue",
    "music": "bangers",
    "travel": "playfair-display",
    "gaming": "rubik-mono-one",
    "health": "rubik",
    "fashion": "la-belle-aurore",
    "default": "perun-bold",
}


class AssetLibraryService:
    """Fetch & cache remote images, stickers, and fonts."""

    def __init__(self) -> None:
        self.assets_root = settings.ASSETS_DIR
        self.cache_dir = os.path.join(self.assets_root, "cache")
        self.fonts_dir = os.path.join(self.assets_root, "fonts")
        self.stickers_cache = os.path.join(self.cache_dir, "stickers")
        self.photos_cache = os.path.join(self.cache_dir, "photos")
        self.sfx_cache = os.path.join(self.cache_dir, "sfx")
        for d in (self.fonts_dir, self.stickers_cache, self.photos_cache, self.sfx_cache):
            os.makedirs(d, exist_ok=True)

    # ------------------------------------------------------------------ status
    def status(self) -> Dict[str, Any]:
        return {
            "stickers": {
                "provider": "twemoji",
                "ready": True,
                "themes": sorted(THEME_EMOJIS.keys()),
            },
            "photos": {
                "pexels": bool(settings.PEXELS_API_KEY),
                "unsplash": bool(settings.UNSPLASH_ACCESS_KEY),
                "ready": bool(settings.PEXELS_API_KEY or settings.UNSPLASH_ACCESS_KEY),
            },
            "video_broll": {
                "pexels": bool(settings.PEXELS_API_KEY),
                "ready": bool(settings.PEXELS_API_KEY),
            },
            "sfx": {
                "provider": "freesound",
                "ready": bool(settings.FREESOUND_API_KEY and settings.SFX_ENABLED),
            },
            "fonts": {
                "provider": "library",
                "count": len(all_catalog_fonts()),
                "google": len(GOOGLE_FONTS),
                "local": len(LOCAL_FONTS),
                "ready": True,
            },
        }

    def list_fonts(self) -> List[Dict[str, Any]]:
        out = []
        for f in all_catalog_fonts():
            path = self._font_cache_path(f)
            local_src = self._local_source_path(f) if f.get("source") == "local" else None
            cached = os.path.isfile(path) and os.path.getsize(path) > 100
            # Google fonts are always "ready" (downloaded on first use).
            # Local fonts need the source file on disk.
            if f.get("source") == "local":
                ready = cached or (bool(local_src) and os.path.isfile(local_src))
            else:
                ready = True
            out.append({
                "id": f["id"],
                "family": f["family"],
                "label": f.get("label") or f["family"],
                "vibe": f.get("vibe") or "",
                "weight": f.get("weight"),
                "source": f.get("source") or "google",
                "license": f.get("license") or ("OFL" if f.get("source") == "google" else None),
                "cached": cached,
                "ready": ready,
                "preview_url": f"/api/v1/client/assets/fonts/{f['id']}/file",
            })
        return out

    def get_font_meta(self, font_id: str) -> Optional[Dict[str, Any]]:
        key = (font_id or "").strip().lower()
        for f in all_catalog_fonts():
            if f["id"] == key:
                return f
        return None

    def _local_source_path(self, meta: Dict[str, Any]) -> Optional[str]:
        rel = meta.get("relpath")
        if not rel:
            return None
        return os.path.join(self.fonts_dir, rel)

    def _font_cache_path(self, meta: Dict[str, Any]) -> str:
        ext = (meta.get("ext") or "ttf").lstrip(".")
        # Prefer stable flat cache name for libass fontsdir
        return os.path.join(self.fonts_dir, "cache", f"{meta['id']}.{ext}")

    def _font_path(self, font_id: str) -> str:
        meta = self.get_font_meta(font_id) or {"id": font_id, "ext": "ttf"}
        return self._font_cache_path(meta)

    async def ensure_font(self, font_id: str) -> Tuple[str, str]:
        """
        Ensure font file is available in fonts/cache.
        Returns (family_name, fonts_directory_for_libass).
        """
        import shutil

        meta = self.get_font_meta(font_id) or self.get_font_meta("montserrat")
        assert meta is not None
        cache_dir = os.path.join(self.fonts_dir, "cache")
        os.makedirs(cache_dir, exist_ok=True)
        path = self._font_cache_path(meta)

        if not os.path.isfile(path) or os.path.getsize(path) < 100:
            if meta.get("source") == "local":
                src = self._local_source_path(meta)
                if not src or not os.path.isfile(src):
                    # Fallback to montserrat if local file missing
                    return await self.ensure_font("montserrat")
                shutil.copy2(src, path)
            else:
                url = meta.get("url")
                if not url:
                    return await self.ensure_font("montserrat")
                async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    with open(path, "wb") as f:
                        f.write(resp.content)

        return meta["family"], cache_dir

    async def ensure_font_file(self, font_id: str) -> Tuple[str, str, str]:
        """Returns (family, fonts_dir, font_file_path)."""
        family, fonts_dir = await self.ensure_font(font_id)
        meta = self.get_font_meta(font_id) or self.get_font_meta("montserrat")
        assert meta is not None
        return family, fonts_dir, self._font_cache_path(meta)

    async def pick_font_for_text(self, text: str) -> Tuple[str, str, str]:
        """Returns (family, fonts_dir, font_id)."""
        theme = self.detect_theme(text)
        font_id = _FONT_BY_THEME.get(theme, "perun-bold")
        # If local preferred font missing, fall back gracefully
        meta = self.get_font_meta(font_id)
        if meta and meta.get("source") == "local":
            src = self._local_source_path(meta)
            if not src or not os.path.isfile(src):
                font_id = "montserrat"
        family, fonts_dir = await self.ensure_font(font_id)
        return family, fonts_dir, font_id

    def detect_theme(self, text: str) -> str:
        for pattern, theme in _KEYWORD_THEME:
            if pattern.search(text or ""):
                return theme
        return "default"

    def photo_query_for_theme(self, theme: str, text: str = "") -> str:
        queries = {
            "sport": "stadium crowd sports night",
            "football": "football stadium lights",
            "basketball": "basketball arena lights",
            "tech": "neon technology abstract",
            "ai": "artificial intelligence neon",
            "money": "finance city skyline night",
            "business": "modern office skyline",
            "food": "gourmet food dark background",
            "news": "news studio dark",
            "music": "concert stage lights",
            "travel": "travel destination sunset",
            "gaming": "gaming setup neon",
            "health": "fitness gym energy",
            "fashion": "fashion runway dark",
            "default": "cinematic dark abstract",
        }
        return queries.get(theme, queries["default"])

    # ------------------------------------------------------------------ stickers
    async def fetch_sticker(self, code: str) -> Optional[str]:
        dest = os.path.join(self.stickers_cache, f"{code}.png")
        if os.path.isfile(dest) and os.path.getsize(dest) > 100:
            return dest
        url = TWEMOJI_CDN.format(code=code)
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    return None
                with open(dest, "wb") as f:
                    f.write(resp.content)
            return dest
        except Exception:
            return None

    async def stickers_for_theme(
        self, theme: str, limit: int = 4
    ) -> List[Dict[str, Any]]:
        codes = THEME_EMOJIS.get(theme) or THEME_EMOJIS["default"]
        positions = ["top_left", "top_right", "bottom_left", "bottom_right"]
        items: List[Dict[str, Any]] = []
        for i, code in enumerate(codes[:limit]):
            path = await self.fetch_sticker(code)
            if not path:
                continue
            items.append({
                "path": path,
                "file": os.path.basename(path),
                "position": positions[i % len(positions)],
                "scale": 11 + (i % 3),
                "opacity": 92,
                "code": code,
            })
        return items

    # ------------------------------------------------------------------ photos
    async def search_photos(
        self, query: str, per_page: int = 8
    ) -> List[Dict[str, Any]]:
        if settings.PEXELS_API_KEY:
            return await self._search_pexels(query, per_page)
        if settings.UNSPLASH_ACCESS_KEY:
            return await self._search_unsplash(query, per_page)
        return []

    async def _search_pexels(self, query: str, per_page: int) -> List[Dict[str, Any]]:
        headers = {"Authorization": settings.PEXELS_API_KEY or ""}
        params = {"query": query, "per_page": per_page, "orientation": "portrait"}
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                "https://api.pexels.com/v1/search",
                headers=headers,
                params=params,
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
        out = []
        for photo in data.get("photos") or []:
            src = photo.get("src") or {}
            out.append({
                "id": str(photo.get("id")),
                "provider": "pexels",
                "url": src.get("large2x") or src.get("large") or src.get("original"),
                "thumb": src.get("medium") or src.get("small"),
                "photographer": photo.get("photographer"),
                "alt": photo.get("alt") or query,
            })
        return out

    async def _search_unsplash(self, query: str, per_page: int) -> List[Dict[str, Any]]:
        params = {
            "query": query,
            "per_page": per_page,
            "orientation": "portrait",
            "client_id": settings.UNSPLASH_ACCESS_KEY,
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                "https://api.unsplash.com/search/photos",
                params=params,
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
        out = []
        for photo in data.get("results") or []:
            urls = photo.get("urls") or {}
            user = photo.get("user") or {}
            out.append({
                "id": str(photo.get("id")),
                "provider": "unsplash",
                "url": urls.get("regular") or urls.get("full"),
                "thumb": urls.get("small") or urls.get("thumb"),
                "photographer": user.get("name"),
                "alt": (photo.get("alt_description") or query),
            })
        return out

    async def download_photo(self, url: str, photo_id: str = "") -> Optional[str]:
        key = photo_id or hashlib.sha1(url.encode()).hexdigest()[:16]
        dest = os.path.join(self.photos_cache, f"{key}.jpg")
        if os.path.isfile(dest) and os.path.getsize(dest) > 500:
            return dest
        try:
            async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    return None
                with open(dest, "wb") as f:
                    f.write(resp.content)
            return dest
        except Exception:
            return None

    # ------------------------------------------------------------------ stock video (B-roll)
    async def search_videos(
        self, query: str, per_page: int = 6
    ) -> List[Dict[str, Any]]:
        """
        Real stock-video B-roll (Pexels Videos only — Unsplash has no video API).
        Picks a single reasonably-sized file per clip (~720p) to keep
        download/decode fast; Pexels lists video_files smallest-to-largest.
        """
        if not settings.PEXELS_API_KEY:
            return []
        headers = {"Authorization": settings.PEXELS_API_KEY}
        params = {"query": query, "per_page": per_page, "orientation": "portrait"}
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(
                    "https://api.pexels.com/videos/search",
                    headers=headers,
                    params=params,
                )
                if resp.status_code != 200:
                    return []
                data = resp.json()
        except Exception:
            return []

        out = []
        for video in data.get("videos") or []:
            files = [
                f for f in (video.get("video_files") or [])
                if f.get("link") and (f.get("file_type") or "").startswith("video/")
            ]
            if not files:
                continue
            # Prefer the smallest file at or above 720px tall; else the largest available.
            files.sort(key=lambda f: int(f.get("height") or 0))
            pick = next((f for f in files if int(f.get("height") or 0) >= 720), files[-1])
            out.append({
                "id": str(video.get("id")),
                "provider": "pexels",
                "url": pick.get("link"),
                "duration": video.get("duration"),
                "width": pick.get("width"),
                "height": pick.get("height"),
                "thumb": (video.get("image") or ""),
            })
        return out

    async def download_video(self, url: str, video_id: str = "") -> Optional[str]:
        key = video_id or hashlib.sha1(url.encode()).hexdigest()[:16]
        dest = os.path.join(self.photos_cache, f"broll_{key}.mp4")
        if os.path.isfile(dest) and os.path.getsize(dest) > 5000:
            return dest
        try:
            async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    return None
                with open(dest, "wb") as f:
                    f.write(resp.content)
            return dest
        except Exception:
            return None

    # ------------------------------------------------------------------ SFX (Freesound)
    # Only these license families are ever accepted — public domain and
    # attribution-only. Freesound's "Sampling+" and any "-NC"/non-commercial
    # license are deliberately excluded: this platform's output is sold to
    # paying clients, so anything non-commercial is legally off-limits.
    _SFX_SAFE_LICENSE_PREFIXES = (
        "http://creativecommons.org/publicdomain/zero/",
        "https://creativecommons.org/publicdomain/zero/",
        "http://creativecommons.org/licenses/by/",
        "https://creativecommons.org/licenses/by/",
    )

    async def search_sfx(self, query: str, per_page: int = 5) -> List[Dict[str, Any]]:
        """Short one-shot SFX (whoosh, pop, ding…) via Freesound, CC0/CC-BY only."""
        if not (settings.FREESOUND_API_KEY and settings.SFX_ENABLED):
            return []
        params = {
            "query": query,
            "token": settings.FREESOUND_API_KEY,
            "page_size": min(max(per_page, 1), 15),
            # Keep results to short one-shot stings, not music beds or long ambiences.
            "filter": "duration:[0.1 TO 6]",
            "fields": "id,name,previews,license,duration",
            "sort": "score",
        }
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.get(
                    "https://freesound.org/apiv2/search/text/", params=params
                )
                if resp.status_code != 200:
                    return []
                data = resp.json()
        except Exception:
            return []

        out: List[Dict[str, Any]] = []
        for item in data.get("results") or []:
            lic = str(item.get("license") or "")
            if not lic.startswith(self._SFX_SAFE_LICENSE_PREFIXES):
                continue
            previews = item.get("previews") or {}
            url = previews.get("preview-hq-mp3") or previews.get("preview-lq-mp3")
            if not url:
                continue
            out.append({
                "id": str(item.get("id")),
                "name": item.get("name"),
                "url": url,
                "duration": item.get("duration"),
                "license": lic,
            })
        return out

    async def download_sfx(self, url: str, sfx_id: str = "") -> Optional[str]:
        key = sfx_id or hashlib.sha1(url.encode()).hexdigest()[:16]
        dest = os.path.join(self.sfx_cache, f"{key}.mp3")
        if os.path.isfile(dest) and os.path.getsize(dest) > 500:
            return dest
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    return None
                with open(dest, "wb") as f:
                    f.write(resp.content)
            return dest
        except Exception:
            return None

    # ------------------------------------------------------------------ pipeline helper
    async def auto_pick_for_video(self, text: str) -> Dict[str, Any]:
        """
        Pick theme stickers + font (+ optional photo) from script/topic text.
        Used by the generation pipeline — no manual downloads.
        """
        theme = self.detect_theme(text)
        stickers = await self.stickers_for_theme(theme, limit=4)
        family, fonts_dir, font_id = await self.pick_font_for_text(text)

        photo_path = None
        photo_meta = None
        if settings.PEXELS_API_KEY or settings.UNSPLASH_ACCESS_KEY:
            photos = await self.search_photos(self.photo_query_for_theme(theme, text), per_page=3)
            if photos and photos[0].get("url"):
                photo_path = await self.download_photo(photos[0]["url"], photos[0]["id"])
                photo_meta = photos[0]

        return {
            "theme": theme,
            "stickers": stickers,
            "font_family": family,
            "fonts_dir": fonts_dir,
            "font_id": font_id,
            "photo_path": photo_path,
            "photo": photo_meta,
        }
