# 🎬 AI Video Generator - B2B SaaS Platform

Многопользовательская платформа для автоматической генерации коротких видеороликов с использованием AI API (GPT-4o, ElevenLabs, HeyGen).

## 📋 Содержание

- [Возможности](#-возможности)
- [Архитектура](#-архитектура)
- [Технологический стек](#-технологический-стек)
- [Быстрый старт](#-быстрый-старт)
- [API документация](#-api-документация)
- [Развертывание](#-развертывание)
- [Конфигурация](#-конфигурация)

## ✨ Возможности

### Панель администратора
- 👥 Управление клиентами (создание, редактирование, деактивация)
- 💳 Управление балансом (тарифы: 15/30/60 видео в месяц)
- 🎨 Настройка брендинга клиентов:
  - Загрузка вотермарка (логотипа)
  - Настройка шрифта субтитров
  - Привязка AI-голоса (ElevenLabs)
  - Привязка AI-аватара (HeyGen)
- 📊 Статистика платформы

### Личный кабинет клиента
- 📝 Ввод текста сценария (мультиязычная поддержка)
- 🚀 Автоматическая генерация видео
- 📁 История генераций с фильтрацией
- ⬇️ Скачивание готовых видео (9:16, Full HD)
- 💎 Отслеживание баланса кредитов

### AI-пайплайн
1. **GPT-4o** - генерация вирусного сценария из пользовательского текста
2. **ElevenLabs** - озвучка сценария реалистичным голосом
3. **HeyGen** - создание видео с AI-аватаром и липсинком
4. **FFmpeg** - наложение субтитров и вотермарка

## 🏗 Архитектура

```
┌─────────────────────────────────────────────────────────────────┐
│                         Frontend                                 │
│  ┌─────────────────┐         ┌─────────────────┐                │
│  │   Admin Panel   │         │ Client Dashboard│                │
│  │   (HTML/CSS/JS) │         │   (HTML/CSS/JS) │                │
│  └────────┬────────┘         └────────┬────────┘                │
└───────────┼───────────────────────────┼─────────────────────────┘
            │                           │
            ▼                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                     FastAPI Backend                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐        │
│  │   Auth   │  │  Admin   │  │  Client  │  │ Pipeline │        │
│  │  /auth/* │  │ /admin/* │  │/client/* │  │ Service  │        │
│  └──────────┘  └──────────┘  └──────────┘  └────┬─────┘        │
└──────────────────────────────────────────────────┼──────────────┘
                                                   │
            ┌──────────────────────────────────────┼──────────────┐
            │              AI Services             │              │
            │  ┌──────────┐ ┌───────────┐ ┌───────┴────┐         │
            │  │  GPT-4o  │ │ElevenLabs │ │   HeyGen   │         │
            │  │ (Script) │ │  (Voice)  │ │  (Avatar)  │         │
            │  └──────────┘ └───────────┘ └────────────┘         │
            └─────────────────────────────────────────────────────┘
                                   │
                                   ▼
            ┌─────────────────────────────────────────────────────┐
            │                    FFmpeg                            │
            │         (Subtitles + Watermark Processing)          │
            └─────────────────────────────────────────────────────┘
                                   │
                                   ▼
            ┌─────────────────────────────────────────────────────┐
            │                  PostgreSQL                          │
            │        (Users, Clients, Generations)                 │
            └─────────────────────────────────────────────────────┘
```

## 🛠 Технологический стек

| Компонент | Технология |
|-----------|------------|
| Backend | Python 3.11, FastAPI |
| Database | PostgreSQL 15 |
| Cache | Redis 7 |
| ORM | SQLAlchemy 2.0 (async) |
| Video Processing | FFmpeg |
| AI - Script | OpenAI GPT-4o |
| AI - Voice | ElevenLabs |
| AI - Avatar | HeyGen |
| Auth | JWT (python-jose) |
| Frontend | Vanilla HTML/CSS/JS |
| Deployment | Docker, Docker Compose |

## 🚀 Быстрый старт

### Требования
- Docker & Docker Compose
- API ключи: OpenAI, ElevenLabs, HeyGen

### 1. Клонирование репозитория
```bash
git clone <repository-url>
cd ai-video-generator
```

### 2. Настройка окружения
```bash
cd docker
cp .env.example .env
# Отредактируйте .env, добавив API ключи
```

### 3. Запуск
```bash
docker-compose up -d
```

### 4. Доступ
- **API Documentation**: http://localhost:8000/docs
- **Admin Panel**: http://localhost:8000/admin
- **Client Dashboard**: http://localhost:8000/dashboard

### Учетные данные по умолчанию
- Email: `admin@example.com`
- Password: `changeme123`

## 📚 API Документация

После запуска приложения доступна интерактивная документация:
- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

### Основные эндпоинты

#### Аутентификация
```
POST /api/v1/auth/login     - Вход в систему
GET  /api/v1/auth/me        - Информация о текущем пользователе
```

#### Админ-панель
```
GET    /api/v1/admin/clients              - Список клиентов
POST   /api/v1/admin/clients              - Создание клиента
GET    /api/v1/admin/clients/{id}         - Детали клиента
PATCH  /api/v1/admin/clients/{id}         - Обновление клиента
POST   /api/v1/admin/clients/{id}/add-credits     - Добавление кредитов
POST   /api/v1/admin/clients/{id}/reset-cycle     - Сброс цикла биллинга
PATCH  /api/v1/admin/clients/{id}/branding        - Обновление брендинга
POST   /api/v1/admin/clients/{id}/watermark       - Загрузка вотермарка
POST   /api/v1/admin/clients/{id}/font            - Загрузка шрифта
GET    /api/v1/admin/stats                        - Статистика платформы
```

#### Клиентский кабинет
```
GET  /api/v1/client/profile           - Профиль клиента
GET  /api/v1/client/branding          - Настройки брендинга
GET  /api/v1/client/credits           - Баланс кредитов
POST /api/v1/client/generate          - Создание видео
GET  /api/v1/client/generations       - История генераций
GET  /api/v1/client/generations/{id}  - Детали генерации
GET  /api/v1/client/generations/{id}/download - Скачивание видео
```

## 🚢 Развертывание

Полный чеклист (Postgres/Redis/Celery, секреты, Linux hardening): **[docs/DEPLOY.md](docs/DEPLOY.md)**.

### Docker Compose (рекомендуется)

```bash
cd docker
cp .env.example .env   # заполните секреты
docker compose up -d --build
docker compose ps
docker compose logs -f app celery_worker
```

Генерации в Docker идут в **Celery** (`USE_CELERY=true`). Локально на SQLite — через FastAPI BackgroundTasks.

### Локальный API (Windows)

```powershell
cd backend
.\scripts\run_api.ps1
```

Один uvicorn **без** `--reload`.

### Production с Nginx

```bash
docker compose --profile production up -d
# Сертификаты → docker/ssl/
```

### Масштабирование

```bash
docker compose up -d --scale celery_worker=3
```

## ⚙️ Конфигурация

### Переменные окружения

| Переменная | Описание | По умолчанию |
|------------|----------|--------------|
| `DATABASE_URL` | PostgreSQL connection string | postgresql+asyncpg://... |
| `REDIS_URL` | Redis connection string | redis://localhost:6379/0 |
| `USE_CELERY` | Очередь Celery (`true`/`false`/auto) | auto |
| `SECRET_KEY` | JWT secret key (мин. 32 символа) | - |
| `OPENAI_API_KEY` | OpenAI API ключ | - |
| `ELEVENLABS_API_KEY` | ElevenLabs API ключ | - |
| `HEYGEN_API_KEY` | HeyGen API ключ | - |
| `PEXELS_API_KEY` | Pexels (фото по теме, опционально) | - |
| `UNSPLASH_ACCESS_KEY` | Unsplash (альтернатива фото) | - |
| `STICKERS_ENABLED` | Автостикеры Twemoji по теме | true |
| `AUTO_FONT_ENABLED` | Автошрифт Google Fonts по теме | true |
| `FIRST_ADMIN_EMAIL` | Email администратора | admin@example.com |
| `FIRST_ADMIN_PASSWORD` | Пароль администратора | changeme123 |

### Тарифные планы

| План | Лимит видео/месяц | Watermark |
|------|-------------------|-----------|
| Basic | 15 | VideoGen или ваш логотип |
| Standard | 30 | Ваш логотип или лёгкий VideoGen |
| Premium | 60 | Только ваш логотип (или чистое видео) |

## 📁 Структура проекта

```
/workspace
├── backend/
│   ├── app/
│   │   ├── api/
│   │   │   └── v1/
│   │   │       └── endpoints/
│   │   │           ├── auth.py      # Аутентификация
│   │   │           ├── admin.py     # Админ API
│   │   │           └── client.py    # Клиентский API
│   │   ├── core/
│   │   │   ├── config.py            # Конфигурация
│   │   │   └── security.py          # JWT, хеширование
│   │   ├── db/
│   │   │   └── session.py           # Database session
│   │   ├── models/
│   │   │   ├── user.py              # Модель пользователя
│   │   │   ├── client.py            # Модель клиента
│   │   │   └── generation.py        # Модель генерации
│   │   ├── schemas/                 # Pydantic схемы
│   │   ├── services/
│   │   │   ├── ai/
│   │   │   │   ├── openai_service.py
│   │   │   │   ├── elevenlabs_service.py
│   │   │   │   └── heygen_service.py
│   │   │   ├── video/
│   │   │   │   └── ffmpeg_service.py
│   │   │   └── pipeline.py          # Оркестратор
│   │   └── main.py                  # Точка входа
│   └── requirements.txt
├── frontend/
│   ├── admin/                       # Админ-панель
│   └── client/                      # Клиентский дашборд
├── docker/
│   ├── Dockerfile
│   ├── docker-compose.yml
│   ├── nginx.conf
│   └── .env.example
└── media/                           # Загруженные и сгенерированные файлы
```

## 🔒 Безопасность

- JWT токены с настраиваемым временем жизни
- Хеширование паролей (bcrypt)
- Разделение ролей (admin/client)
- Rate limiting через Nginx
- Валидация входных данных (Pydantic)
- CORS настройки
- CI: TruffleHog secret scan (`.github/workflows/secret-scan.yml`)
- См. также [docs/DEPLOY.md](docs/DEPLOY.md)

## 📞 Поддержка

При возникновении вопросов создайте Issue в репозитории.

## 📄 Лицензия

MIT License
