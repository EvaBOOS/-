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

### Docker Compose (рекомендуется)

```bash
cd docker

# Создание .env файла
cp .env.example .env
nano .env  # Заполните все переменные

# Запуск всех сервисов
docker-compose up -d

# Проверка статуса
docker-compose ps

# Просмотр логов
docker-compose logs -f app
```

### Production с Nginx

```bash
# Запуск с Nginx reverse proxy
docker-compose --profile production up -d

# Настройка SSL (Let's Encrypt)
# Добавьте сертификаты в docker/ssl/
```

### Масштабирование

```bash
# Увеличение количества воркеров
docker-compose up -d --scale celery_worker=3
```

## ⚙️ Конфигурация

### Переменные окружения

| Переменная | Описание | По умолчанию |
|------------|----------|--------------|
| `DATABASE_URL` | PostgreSQL connection string | postgresql+asyncpg://... |
| `REDIS_URL` | Redis connection string | redis://localhost:6379/0 |
| `SECRET_KEY` | JWT secret key (мин. 32 символа) | - |
| `OPENAI_API_KEY` | OpenAI API ключ | - |
| `ELEVENLABS_API_KEY` | ElevenLabs API ключ | - |
| `HEYGEN_API_KEY` | HeyGen API ключ | - |
| `FIRST_ADMIN_EMAIL` | Email администратора | admin@example.com |
| `FIRST_ADMIN_PASSWORD` | Пароль администратора | changeme123 |

### Тарифные планы

| План | Лимит видео/месяц |
|------|-------------------|
| Basic | 15 |
| Standard | 30 |
| Premium | 60 |

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

## 📞 Поддержка

При возникновении вопросов создайте Issue в репозитории.

## 📄 Лицензия

MIT License
