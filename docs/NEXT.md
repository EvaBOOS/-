# VideoGen — что дальше (порядок)

## Сделано в MVP
- Viral / AI-clips / avatar, watermark по плану, стили монтажа
- Импорт по ссылке (yt-dlp + cookies + Node EJS)
- Радар: YouTube Shorts
- Silent-видео без кликбейта и чужого B-roll
- **Мозг VideoGen (MVP-1)** — JSON-правила `hooks` / `editing` / `storytelling` в `backend/app/data/brain/`, подмешиваются в LLM (`plan_viral_edit`, `generate_viral_script`). Флаги: `BRAIN_ENABLED`, `BRAIN_MAX_RULES`. Админ: `GET /api/v1/admin/brain`.

## Очередь

1. **Ручной QA silent** — прогнать банкет/party без речи, убедиться: нет хука, нет pip-стока.
2. **Прод Docker** — `docs/DEPLOY.md`: заполнить `docker/.env`, `compose up`, Celery+Postgres.
3. **Ограничить / сменить YouTube API key** в Google Cloud (ключ светился в чате).
4. **TikTok / IG / VK радар** — MVP-2 (не Creative Center «как есть»).
5. **Cobalt** (опционально) — свой инстанс для более стабильного скачивания.
6. **Мозг фаза 2** — ingest текста/PDF → LLM → только structured rules (исходник удалять); позже retrieval/векторная БД. Новые правила сейчас: править JSON в `backend/app/data/brain/` и задеплоить.

Клиент локально: `client@example.com` / `client12345`  
Админ: `admin@example.com` / `changeme123` (сменить в проде).
