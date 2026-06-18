import asyncio
import urllib.parse
from typing import List, Dict, Any
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel
import httpx
from bs4 import BeautifulSoup
import os
from dotenv import load_dotenv
from services.oylan import send_message
from typing import Optional

load_dotenv()

MY_API_KEY = os.getenv("OYLAN_API_KEY")

app = FastAPI(
    title="Oylan API",
    description="Полный бэкенд для ИИ-ассистента по подбору комплектующих ПК и агрегации цен в Казахстане",
    version="1.0.0"
)

# --- НАСТРОЙКА ИНТЕГРАЦИИ С OLX ---
# Замени эти значения на реальные, когда их одобрят в личном кабинете разработчика OLX
OLX_CLIENT_ID = "ТВОЙ_CLIENT_ID_ОТ_OLX"
OLX_CLIENT_SECRET = "ТВОЙ_CLIENT_SECRET_ОТ_OLX"
# Этот URL должен быть строго прописан в настройках приложения на OLX
OLX_REDIRECT_URI = "http://localhost:8000/auth/callback"


# --- ВСЕ МОДЕЛИ ДАННЫХ (Pydantic Схемы) ---

class ChatRequest(BaseModel):
    message: str

from typing import Optional

# Начиная с 38-й строки (или где он у тебя объявлен):
class PCConfigRequest(BaseModel):
    budget: int               # Бюджет в тенге
    target_games: str         # "хочу играть в Cyberpunk в 2K", "прибавка +50% FPS"
    current_specs: str        # "i3-10100, GTX 1650, 8GB" или "нет ПК, собираю с нуля"
    component_to_upgrade: str # "видеокарта", "процессор", "весь ПК"
    city: Optional[str] = "Almaty" # Город (по умолчанию Алматы, если юзер не ввел)

class ComponentList(BaseModel):
    cpu: str | None = None
    motherboard: str | None = None
    ram: str | None = None
    gpu: str | None = None
    psu_wattage: int | None = None

class BottleneckRequest(BaseModel):
    cpu: str
    gpu: str
    resolution: str = "1080p"
    target_game: str

class SaveBuildRequest(BaseModel):
    title: str
    components: ComponentList
    total_price: int

class MarketSearchRequest(BaseModel):
    component_name: str
    market_type: str = "all"


# --- ИМИТАЦИЯ БАЗЫ ДАННЫХ В ПАМЯТИ (In-Memory DB) ---

# История переписок
chat_histories: Dict[str, List[Dict[str, str]]] = {
    "demo-session-123": [
        {"role": "user", "message": "Привет! Помоги собрать ПК для Rust"},
        {"role": "assistant", "message": "Привет! Какой у тебя бюджет?"}
    ]
}

# Сохраненные сборки пользователей
saved_builds: Dict[int, Dict[str, Any]] = {}
build_id_counter = 1

# Наш локальный каталог железа
hardware_db = {
    "gpu": [
        {"id": 1, "name": "NVIDIA GeForce RTX 5070", "price": 320000, "specs": "12GB VRAM"},
        {"id": 2, "name": "NVIDIA GeForce RTX 4060 Ti", "price": 210000, "specs": "8GB VRAM"}
    ],
    "cpu": [
        {"id": 3, "name": "AMD Ryzen 7 7800X3D", "price": 220000, "specs": "8 cores, 16 threads"},
        {"id": 4, "name": "Intel Core i5-14600K", "price": 180000, "specs": "14 cores"}
    ]
}


# --- ФУНКЦИИ-ПАРСЕРЫ (Scrapers) ---

async def parse_shop_kz(component_name: str) -> list:
    """
    Прямой запрос к поисковому API Diginetica, который использует shop.kz.
    Исправлена обработка строковых цен формата '196990.0'.
    """
    safe_query = urllib.parse.quote_plus(component_name)
    
    url = (
        f"https://sort.diginetica.net/search?"
        f"apiKey=Z72L941338"
        f"&strategy=advanced_xname%2Czero_queries"
        f"&fullData=true"
        f"&withCorrection=true"
        f"&size=3"
        f"&regionId=global"
        f"&st={safe_query}"
        f"&lang=ru"
        f"&sort=DEFAULT"
    )
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json"
    }
    results = []

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, headers=headers)
        
        if response.status_code != 200:
            return results

        data = response.json()
        products = data.get("products", [])
        
        for prod in products:
            title = prod.get("name")
            price = prod.get("price")
            
            href = prod.get("link_url") or ""
            link = f"https://shop.kz{href}" if href.startswith("/") else href
            
            if title and price is not None:
                try:
                    # Сначала парсим в float (чтобы съесть '.0'), затем приводим к int
                    price_kzt = int(float(price))
                    
                    results.append({
                        "source": "Белый Ветер (shop.kz)",
                        "condition": "Новый",
                        "title": title,
                        "price_kzt": price_kzt,
                        "link": link
                    })
                except (ValueError, TypeError):
                    continue  # Если попался совсем кривой формат, пропускаем одну позицию
                    
    except Exception as e:
        print(f"Ошибка при работе с API поиска shop.kz: {e}")

    return results


# =====================================================================
# 1. ТВОИ ОРИГИНАЛЬНЫЕ ЭНДПОИНТЫ (Полностью сохранены)
# =====================================================================

@app.get("/")
def root():
    return {"message": "Oylan assistant is running!"}

@app.get("/health")
def health():
    return {"status": "ok"}
 
# Отрезок кода в main.py для обычного чата:

@app.post("/chat")
async def chat(req: ChatRequest):  # Имя функции должно быть chat, а не send_message!
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    try:
        # Вот здесь мы ВЫЗЫВАЕМ функцию из сервиса, а не заменяем её
        reply = await send_message(req.message)
        return {"reply": reply}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
@app.get("/user/location")
def get_user_location():
    return {
        "city": "Астана",
        "country": "Казахстан"
    }

@app.post("/assistant/pc_config")
async def analyze_pc_config(req: PCConfigRequest):
    if req.budget <= 0:
        raise HTTPException(status_code=400, detail="Бюджет должен быть больше 0")
        
    try:
        # Сжимаем промпт до сухого остатка. Никакой лишней болтовни, только факты.
        prompt = (
            f"Апгрейд ПК (Казахстан, г. {req.city}). "
            f"Бюджет: {req.budget} тенге. "
            f"Что меняем: {req.component_to_upgrade}. "
            f"Текущий ПК: {req.current_specs}. "
            f"Цель/Игры: {req.target_games}. "
            f"Сделай экспертный разбор совместимости, bottleneck, план апгрейда и предложи модели с shop.kz."
        )
        
        # Передаем этот компактный текст в Ойлан
        ai_reply = await send_message(prompt) # type: ignore
        
        return {
            "status": "success",
            "city": req.city,
            "budget_analyzed": req.budget,
            "ai_suggestion": ai_reply
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка анализа Oylan: {repr(e)}")

# =====================================================================
# 2. ДОБАВЛЕННЫЕ ЭНДПОИНТЫ (Интеграции, Анализ, История)
# =====================================================================

# --- Авторизация OLX ---

from fastapi import FastAPI, Query

@app.get("/auth/callback")
async def olx_callback(
    code: str = Query(...),           # Query(...) делает параметр обязательным
    state: str = Query(None)          # Query(None) делает параметр опциональным
):
    token_url = "https://www.olx.kz/api/open/oauth/token"
    
    payload = {
        "grant_type": "authorization_code",
        "client_id": OLX_CLIENT_ID,
        "client_secret": OLX_CLIENT_SECRET,
        "code": code,
        "scope": "v2 read write"
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(token_url, json=payload)
        
    if response.status_code == 200:
        token_data = response.json()
        access_token = token_data.get("access_token")
        return {
            "status": "success", 
            "message": "Успешно авторизовано в OLX!", 
            "access_token_preview": f"{access_token[:10]}..."
        }
    else:
        raise HTTPException(
            status_code=response.status_code, 
            detail=f"Ошибка авторизации OLX: {response.text}"
        )


# --- Управление сессиями и историей чата ---

@app.get("/chat/history/{session_id}")
def get_chat_history(session_id: str):
    """Загрузить историю сообщений для сохранения контекста ИИ"""
    if session_id not in chat_histories:
        chat_histories[session_id] = []
    return {"session_id": session_id, "history": chat_histories[session_id]}

@app.delete("/chat/history/{session_id}")
def clear_chat_history(session_id: str):
    """Удалить историю сессии (команда 'начать сначала')"""
    if session_id in chat_histories:
        chat_histories[session_id] = []
        return {"status": "success", "message": f"History for session {session_id} cleared."}
    raise HTTPException(status_code=404, detail="Session not found")


# --- Модули анализа и калькуляторы ---

@app.post("/assistant/check_compatibility")
def check_compatibility(components: ComponentList):
    """Техническая проверка совместимости выбранных комплектующих"""
    warnings = []
    errors = []
    
    if components.gpu and "5070" in components.gpu and components.psu_wattage:
        if components.psu_wattage < 650:
            warnings.append("Рекомендуемая мощность БП для RTX 5070 — от 650W. Текущего блока может не хватить.")
            
    if components.cpu and components.motherboard:
        if "Ryzen" in components.cpu and "LGA" in components.motherboard:
            errors.append("Процессор AMD Ryzen физически в сокет Intel (LGA) не встанет.")

    return {
        "status": "compatible" if not errors else "incompatible",
        "errors": errors,
        "warnings": warnings
    }

@app.post("/assistant/bottleneck_analysis")
def analyze_bottleneck(req: BottleneckRequest):
    """Калькулятор баланса процессора и видеокарты с упором на игры"""
    is_cpu_bound = req.target_game.lower() in ["rust", "valorant", "cs2", "minecraft"]
    
    if "i3" in req.cpu.lower() and "5070" in req.gpu and is_cpu_bound:
        percentage = 25
        limiting = "cpu"
        text = f"В {req.target_game} связка несбалансирована: слабый процессор ограничит возможности видеокарты RTX 5070."
    else:
        percentage = 5
        limiting = "none"
        text = f"Процессор и видеокарта отлично дополняют друг друга в {req.target_game} ({req.resolution})."

    return {
        "bottleneck_percentage": percentage,
        "limiting_component": limiting,
        "ai_analysis": text
    }


# --- Маркетплейсы и Каталог ---

@app.post("/api/market/search")
async def search_market_prices(req: MarketSearchRequest):
    """Агрегатор цен, запускающий живой парсинг магазинов КЗ и поиск б/у вариантов"""
    all_offers = []
    tasks = []
    
    if req.market_type in ["all", "new"]:
        # Добавляем задачу асинхронного парсинга "Белого Ветра"
        tasks.append(parse_shop_kz(req.component_name))
        
    if req.market_type in ["all", "used"]:
        # Место для будущего вызова официального OLX API
        pass

    # Выполняем параллельный сбор данных
    if tasks:
        results_lists = await asyncio.gather(*tasks)
        for res_list in results_lists:
            all_offers.extend(res_list)

    # Демо-заглушка для б/у рынка, если реальное OLX API ещё не настроено
    if req.market_type in ["all", "used"]:
        all_offers.append({
            "source": "OLX.kz (демо)",
            "condition": "Б/У",
            "title": f"Видеокарта {req.component_name} б/у, коробка есть",
            "price_kzt": 140000,
            "link": "https://www.olx.kz"
        })

    # Сортируем все найденные товары по возрастанию цены
    all_offers.sort(key=lambda x: x["price_kzt"])

    return {
        "query": req.component_name,
        "total_results": len(all_offers),
        "offers": all_offers
    }

@app.get("/api/components/{category}")
def get_components_by_category(category: str):
    """Вывод деталей из локальной базы комплектующих"""
    cat = category.lower()
    if cat not in hardware_db:
        raise HTTPException(status_code=404, detail="Категория не найдена. Доступны: cpu, gpu")
    return {"category": cat, "items": hardware_db[cat]}

@app.get("/api/components/search")
def search_components(query: str):
    """Внутренний поиск по названиям комплектующих базы бэкенда"""
    results = []
    for category, items in hardware_db.items():
        for item in items:
            if query.lower() in item["name"].lower():
                results.append({"category": category, **item})
    return {"query": query, "results": results}


# --- Сохранение и шеринг готовых сборок ---

@app.post("/builds/save", status_code=status.HTTP_201_CREATED)
def save_build(req: SaveBuildRequest):
    """Сохранить сборку в базу данных и сгенерировать ссылку доступа"""
    global build_id_counter
    
    new_build = {
        "id": build_id_counter,
        "title": req.title,
        "components": req.components.dict(),
        "total_price": req.total_price
    }
    
    saved_builds[build_id_counter] = new_build
    build_id_counter += 1
    
    return {
        "status": "saved",
        "build_id": new_build["id"],
        "url": f"/builds/{new_build['id']}"
    }

@app.get("/builds/{build_id}")
def get_saved_build(build_id: int):
    """Загрузить сохраненную конфигурацию ПК по её уникальному ID"""
    if build_id not in saved_builds:
        raise HTTPException(status_code=404, detail="Выбранная сборка ПК не найдена")
    return saved_builds[build_id]