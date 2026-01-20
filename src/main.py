import os
import asyncio
import logging
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from google import genai 

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from aiogram.utils.keyboard import InlineKeyboardBuilder

# 1. Настройки и инициализация
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")

# Настройка клиента Gemini с принудительной версией v1 (чтобы избежать 404)
client = genai.Client(
    api_key=GEMINI_KEY,
    http_options={'api_version': 'v1'}
)

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
logging.basicConfig(level=logging.INFO)

# 2. Функция парсинга ArXiv
def get_arxiv_articles(query):
    options = Options()
    options.add_argument("--headless") 
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    
    search_url = f"https://arxiv.org/search/?query={query.replace(' ', '+')}&searchtype=all&sort=-announced_date_first"
    
    articles = []
    try:
        driver.get(search_url)
        
        # Ожидаем появления хотя бы одного результата (до 10 сек)
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CLASS_NAME, "arxiv-result"))
        )
        
        results = driver.find_elements(By.CLASS_NAME, "arxiv-result")[:3]
        for res in results:
            title = res.find_element(By.CLASS_NAME, "title").text
            link = res.find_element(By.CSS_SELECTOR, "p.list-title a").get_attribute("href")
            
            # ArXiv часто прячет текст под классами 'abstract-full' или 'abstract-short'
            # Попробуем достать текст из более надежного места
            try:
                # Находим блок с аннотацией
                abs_element = res.find_element(By.CLASS_NAME, "abstract-full")
                # Убираем лишнее слово "Abstract" в начале, если оно есть
                abstract_text = abs_element.text.replace("Abstract:", "").strip()
            except:
                abstract_text = "Текст статьи не удалось извлечь автоматически."
                
            articles.append({"title": title, "link": link, "abstract": abstract_text})
    finally:
        driver.quit()
    return articles

# 3. Функция работы с AI
async def get_summary(text):
    prompt = f"Ты — научный ассистент. Переведи на русский и кратко (3-4 предложения) объясни суть этой статьи: {text}"
    try:
        # Используем стабильную модель 1.5-flash
        response = await asyncio.to_thread(
            client.models.generate_content,
            model="gemini-2.5-flash",
            contents=prompt
        )
        return response.text
    except Exception as e:
        logging.error(f"Gemini Error: {e}")
        return "Не удалось создать описание, но статья доступна по ссылке."

# 4. Вспомогательная функция для поиска и отправки (общая для кнопок и текста)
async def process_search(message: types.Message, query: str):
    status_msg = await message.answer(f"🔎 Ищу статьи по теме: **{query}**...")
    try:
        articles = get_arxiv_articles(query)
        if not articles:
            await status_msg.edit_text("❌ По этой теме ничего не найдено.")
            return

        for art in articles:
            summary = await get_summary(art['abstract'])
            response_text = (
                f"📄 **{art['title']}**\n\n"
                f"🤖 **Суть:** {summary}\n\n"
                f"🔗 [Открыть оригинал]({art['link']})"
            )
            # Отправляем без parse_mode, чтобы избежать ошибок с символами
            await message.answer(response_text)
            
        await status_msg.delete()
    except Exception as e:
        logging.error(f"Search processing error: {e}")
        await message.answer("⚠️ Произошла ошибка при поиске.")

# 5. Обработчики команд и сообщений
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    builder = InlineKeyboardBuilder()
    
    # Список готовых тем
    topics = {
        "🤖 AI / ML": "Artificial Intelligence",
        "🧬 Bio-AI": "Biology Intelligence",
        "🔐 Security": "Cybersecurity",
        "⚛️ Physics": "Quantum Physics"
    }
    
    for text, query in topics.items():
        builder.button(text=text, callback_data=f"topic_{query}")
    
    builder.button(text="🔍 Свой запрос (напиши текстом)", callback_data="manual_info")
    builder.adjust(2, 2, 1) # Сетка кнопок
    
    await message.answer(
        "👋 **Добро пожаловать в ArXiv Monitor!**\n\n"
        "Выберите одну из популярных тем или просто напишите мне свой запрос на английском.",
        reply_markup=builder.as_markup()
    )

@dp.callback_query(F.data.startswith("topic_"))
async def handle_topic(callback: types.CallbackQuery):
    query = callback.data.split("_")[1]
    await process_search(callback.message, query)
    await callback.answer()

@dp.callback_query(F.data == "manual_info")
async def handle_manual(callback: types.CallbackQuery):
    await callback.message.answer("⌨️ Просто напиши мне тему (на английском), например: `Black Holes`.")
    await callback.answer()

@dp.message()
async def handle_text(message: types.Message):
    # Любой текст от пользователя считается конкретной темой для поиска
    await process_search(message, message.text)

# 6. Запуск
async def main():
    print("Бот в сети!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())