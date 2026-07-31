# VideoGen — что дальше (порядок)

## Сделано в MVP
- Viral / AI-clips / avatar, watermark по плану, стили монтажа
- Импорт по ссылке (yt-dlp + cookies + Node EJS)
- Радар: YouTube Shorts
- Silent-видео без кликбейта и чужого B-roll

## Очередь

1. **Ручной QA silent** — прогнать банкет/party без речи, убедиться: нет хука, нет pip-стока.
2. **Прод Docker** — `docs/DEPLOY.md`: заполнить `docker/.env`, `compose up`, Celery+Postgres.
3. **Ограничить / сменить YouTube API key** в Google Cloud (ключ светился в чате).
4. **TikTok / IG / VK радар** — MVP-2 (не Creative Center «как есть»).
5. **Cobalt** (опционально) — свой инстанс для более стабильного скачивания.

Клиент локально: `client@example.com` / `client12345`  
Админ: `admin@example.com` / `changeme123` (сменить в проде).
