# VideoGen — деплой и hardening

## Архитектура в Docker

| Сервис | Роль |
|--------|------|
| `db` | PostgreSQL 15 |
| `redis` | брокер/бэкенд Celery |
| `app` | FastAPI (uvicorn без `--reload`) |
| `celery_worker` | фоновые генерации (avatar / viral / clips) |
| `nginx` | reverse proxy (профиль `production`) |

Локально на Windows с SQLite Celery **не** используется: задачи идут через FastAPI `BackgroundTasks` (`USE_CELERY=false` или auto при `sqlite`).

## Быстрый прод-старт

```bash
cd docker
cp .env.example .env   # заполнить реальные секреты
docker compose up -d --build
docker compose ps
docker compose logs -f app celery_worker
```

Проверки:

- API: `http://localhost:8000/health`
- Docs: `http://localhost:8000/docs`
- В логах app при генерации: `Enqueued … via Celery`

## Локальный API (Windows)

Один процесс, без `--reload` (иначе зоопарк процессов на кириллических путях):

```powershell
cd backend
.\scripts\run_api.ps1
```

Или вручную из `backend` с активированным `.venv`:

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Не запускайте второй uvicorn на том же порту. Перед стартом: `Get-NetTCPConnection -LocalPort 8000`.

## Импорт по ссылке

Viral / AI-клипы принимают `source_url` (YouTube, Reels, TikTok и др.):

1. API сразу ставит задачу в очередь (файл ещё не скачан).
2. Воркер скачивает через **yt-dlp** (или Cobalt, если задан `COBALT_API_URL`).
3. Дальше обычный пайплайн.

Переменные: `LINK_INGEST_ENABLED`, `COBALT_API_URL`, `COBALT_API_KEY`.  
Нужны `yt-dlp` в образе и FFmpeg (уже есть). Приватные/localhost URL блокируются (SSRF).

## Watermark по тарифу

| План | Поведение |
|------|-----------|
| Basic | Всегда знак VideoGen или клиентский логотип |
| Standard | Логотип клиента или лёгкий VideoGen |
| Premium | Только логотип клиента; иначе чистое видео |

Применяется в avatar / viral / clips при экспорте.

## Радар трендов

- UI: кабинет → **Радар**
- API: `GET /api/v1/client/trends`, `POST /api/v1/client/trends/analyze`
- YouTube Shorts: нужен `YOUTUBE_API_KEY` (Data API v3, бесплатная квота); только ролики ≤60 сек
- TikTok: Creative Center (хрупко, часто HTML/капча/гео) — **MVP-2**: другой источник; пока вручную ссылка справа
- Instagram / VK: coming soon
- Разбор ролика: yt-dlp → Whisper → LLM-паттерны (`TRENDS_ANALYZE_COST_CREDITS`, по умолчанию 1)

### YouTube скачивание (бот-проверка)

1. Экспорт cookies: Edge → расширение **Get cookies.txt LOCALLY** на youtube.com
2. `cd backend && .\scripts\import_youtube_cookies.ps1`
3. В `.env`: `YTDLP_COOKIES_FILE=./secrets/youtube_cookies.txt` (не под `/media` — статика больше не отдаёт весь MEDIA_ROOT)
4. Нужны Node.js ≥20 и пакеты `yt-dlp[default]`, `yt-dlp-ejs` (n-challenge)
5. Прод: `ENVIRONMENT=production` + уникальный `SECRET_KEY` ≥32 символов (иначе API не стартует)

## Вирусный монтаж без речи

Silent-клипы (нет озвучки) — валидный сценарий: формат + зумы + музыка, **без** кликбейт-хуков и чужого B-roll.  
Опционально поле `voiceover_text` / «Текст озвучки» — TTS поверх видео.

## Секреты

- Реальные ключи только в `.env` (уже в `.gitignore`). Никогда не коммитить `.env` / cookies.
- В CI — GitHub Secrets / TruffleHog (см. `.github/workflows/secret-scan.yml`).
- После утечки в чат/лог — ротация AITUNNEL / HeyGen / Pexels / YouTube API key / `SECRET_KEY`.
- Google Cloud: ограничить API key только **YouTube Data API v3** (+ IP при возможности).
- Прод: длинный уникальный `SECRET_KEY`, сильный `POSTGRES_PASSWORD`, смена дефолтного admin-пароля.

## Чеклист Linux-hardening (VPS)

1. **SSH**: ключи, отключить пароль root, `PermitRootLogin no`, нестандартный порт по желанию.
2. **Firewall**: ufw/firewalld — только 22/80/443 (и 8000 только с localhost, если за nginx).
3. **Обновления**: `unattended-upgrades` или регулярный `apt upgrade`.
4. **Docker**: не публиковать Postgres/Redis наружу в проде (убрать `ports:` у `db`/`redis` или bind `127.0.0.1`).
5. **TLS**: nginx + Let's Encrypt; HSTS после проверки HTTPS.
6. **Пользователь**: контейнеры уже под `appuser`; хост — отдельный deploy-user без лишних sudo.
7. **Бэкапы**: volume `postgres_data` + `media_data` по расписанию.
8. **Логи**: ротация; не логировать тела запросов с API-ключами.
9. **Fail2ban** / rate limit на `/api/v1/auth/login` (nginx `limit_req`).
10. **Мониторинг диска**: FFmpeg/media быстро раздувают volume.

## Масштаб

```bash
docker compose up -d --scale celery_worker=2
```

Долгие задачи: soft limit ~45 мин, hard ~60 мин (`app/celery_app.py`).
