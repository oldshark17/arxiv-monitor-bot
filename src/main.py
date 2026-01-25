import os
import asyncio
import logging
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from google import genai 
import arxiv
from telegraph import Telegraph
from aiogram.utils.keyboard import InlineKeyboardBuilder

# 1. Настройки и инициализация
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")

# Настройка клиента Gemini
client = genai.Client(
    api_key=GEMINI_KEY,
    http_options={'api_version': 'v1'}
)

# Telegraph клиент
telegraph = Telegraph()
telegraph.create_account(short_name='ArXivBot')

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
logging.basicConfig(level=logging.INFO)

# Хранилище для пагинации (user_id -> {query, offset})
user_search_state = {}

# 2. Функция поиска статей через ArXiv API
def get_arxiv_articles(query: str, max_results: int = 5, start: int = 0):
    """Search arXiv using the official API."""
    arxiv_client = arxiv.Client()
    search = arxiv.Search(
        query=query,
        max_results=max_results,
        sort_by=arxiv.SortCriterion.SubmittedDate,
        sort_order=arxiv.SortOrder.Descending
    )
    
    articles = []
    try:
        results = list(arxiv_client.results(search))
        # Manual offset since arxiv library doesn't support start parameter directly
        for result in results[start:start + max_results]:
            articles.append({
                "title": result.title,
                "link": result.entry_id,
                "abstract": result.summary,
                "year": result.published.year if result.published else "N/A",
                "arxiv_id": result.entry_id.split("/")[-1]
            })
    except Exception as e:
        logging.error(f"ArXiv API error: {e}")
    
    return articles

# 3. Функция работы с AI
async def get_summary(text: str) -> str:
    prompt = f"Ты — научный ассистент. Переведи на русский и кратко (3-4 предложения) объясни суть этой статьи: {text}"
    try:
        response = await asyncio.to_thread(
            client.models.generate_content,
            model="gemini-2.5-flash",
            contents=prompt
        )
        return response.text
    except Exception as e:
        logging.error(f"Gemini Error: {e}")
        return "Не удалось создать описание, но статья доступна по ссылке."

# 4. Создание Telegraph страницы для статьи
async def create_telegraph_page(article: dict) -> str:
    """Create a Telegraph page for an article with Russian summary."""
    summary = await get_summary(article['abstract'])
    
    # HTML контент для Telegraph
    content = f"""
    <p><b>Год публикации:</b> {article['year']}</p>
    <p><b>Краткое содержание:</b></p>
    <p>{summary}</p>
    <p><a href="{article['link']}">📄 Открыть оригинал на arXiv</a></p>
    """
    
    try:
        response = telegraph.create_page(
            title=article['title'][:256],  # Telegraph title limit
            html_content=content,
            author_name="ArXiv Monitor Bot"
        )
        return response['url']
    except Exception as e:
        logging.error(f"Telegraph error: {e}")
        return article['link']  # Fallback to arXiv link

# 5. Функция поиска и отправки результатов
async def process_search(message: types.Message, query: str, offset: int = 0):
    user_id = message.from_user.id if message.from_user else message.chat.id
    
    # Сохраняем состояние поиска
    user_search_state[user_id] = {"query": query, "offset": offset}
    
    if offset == 0:
        status_msg = await message.answer(f"🔎 Ищу статьи по теме: {query}...")
    else:
        status_msg = await message.answer("🔄 Загружаю ещё статьи...")
    
    try:
        # Fetch more than needed to check if there are more results
        articles = get_arxiv_articles(query, max_results=6, start=offset)
        
        if not articles:
            await status_msg.edit_text("❌ По этой теме ничего не найдено.")
            return
        
        has_more = len(articles) > 5
        articles_to_show = articles[:5]
        
        # Создаём Telegraph страницы для каждой статьи
        telegraph_urls = []
        for art in articles_to_show:
            url = await create_telegraph_page(art)
            telegraph_urls.append(url)
        
        # Формируем сообщение со списком статей
        result_lines = [f"📚 Найдено по запросу: {query}\n"]
        for i, (art, url) in enumerate(zip(articles_to_show, telegraph_urls), 1):
            result_lines.append(f"{i}. <a href=\"{url}\">{art['title']}</a> ({art['year']})")
        
        result_text = "\n".join(result_lines)
        
        # Добавляем кнопку "Загрузить ещё" если есть ещё результаты
        builder = InlineKeyboardBuilder()
        if has_more:
            new_offset = offset + 5
            builder.button(text="📥 Загрузить ещё", callback_data=f"more_{new_offset}")
        
        await status_msg.delete()
        await message.answer(
            result_text, 
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=builder.as_markup() if has_more else None
        )
        
        # Обновляем offset
        user_search_state[user_id]["offset"] = offset + 5
        
    except Exception as e:
        logging.error(f"Search processing error: {e}")
        await message.answer("⚠️ Произошла ошибка при поиске.")

# 6. Обработчики команд и сообщений
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "� Привет! Я помогу найти научные статьи на arXiv.\n\n"
        "Просто напиши тему поиска (на английском), например:\n"
        "• machine learning\n"
        "• quantum computing\n"
        "• neural networks"
    )

@dp.callback_query(F.data.startswith("more_"))
async def handle_load_more(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    
    if user_id not in user_search_state:
        await callback.answer("Сессия истекла. Введите запрос заново.")
        return
    
    offset = int(callback.data.split("_")[1])
    query = user_search_state[user_id]["query"]
    
    await callback.answer()
    await process_search(callback.message, query, offset)

@dp.message()
async def handle_text(message: types.Message):
    # Любой текст от пользователя считается темой для поиска
    await process_search(message, message.text)

# 7. Запуск
async def main():
    print("Бот в сети!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())