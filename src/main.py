import os
import asyncio
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from google import genai 
import arxiv
from telegraph import Telegraph
from aiogram.utils.keyboard import InlineKeyboardBuilder
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import database as db

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

# Хранилище для пагинации и статей (user_id -> {query, offset, articles})
user_search_state = {}

# FSM состояния для создания подписки
class SubscriptionStates(StatesGroup):
    waiting_for_topic = State()

# arXiv обновляется ВС-ЧТ в ~20:00 ET = ~06:00 UTC+5 (Almaty)
# Пятница (4) и суббота (5) - нет обновлений
ARXIV_NO_UPDATE_DAYS = {4, 5}  # Friday, Saturday
ARXIV_UPDATE_HOUR = 6  # 06:00 local time when new papers appear

# 2. Функция поиска статей через ArXiv API
def get_arxiv_articles(query: str, max_results: int = 5, start: int = 0):
    """Search arXiv using the official API."""
    arxiv_client = arxiv.Client()
    search = arxiv.Search(
        query=query,
        max_results=max_results + start,  # Fetch enough to skip
        sort_by=arxiv.SortCriterion.SubmittedDate,
        sort_order=arxiv.SortOrder.Descending
    )
    
    articles = []
    try:
        results = list(arxiv_client.results(search))
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
    prompt = (
        "Ты — научный ассистент. Напиши краткое содержание статьи на русском языке.\n\n"
        "Требования:\n"
        "- 3-5 предложений\n"
        "- Простым языком, понятным неспециалисту\n"
        "- Основная идея, метод и результат\n\n"
        f"Абстракт статьи:\n{text}"
    )
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
    
    content = f"""
    <p><b>Год публикации:</b> {article['year']}</p>
    <p><b>Краткое содержание:</b></p>
    <p>{summary}</p>
    <p><a href="{article['link']}">📄 Открыть оригинал на arXiv</a></p>
    """
    
    try:
        response = telegraph.create_page(
            title=article['title'][:256],
            html_content=content,
            author_name="ArXiv Monitor Bot"
        )
        return response['url']
    except Exception as e:
        logging.error(f"Telegraph error: {e}")
        return article['link']

# 5. Функция поиска и отправки результатов (lazy loading)
async def process_search(message: types.Message, query: str, offset: int = 0):
    user_id = message.from_user.id if message.from_user else message.chat.id
    
    if offset == 0:
        status_msg = await message.answer(f"🔎 Ищу статьи по теме: {query}...")
    else:
        status_msg = await message.answer("🔄 Загружаю ещё статьи...")
    
    try:
        articles = get_arxiv_articles(query, max_results=6, start=offset)
        
        if not articles:
            await status_msg.edit_text("❌ По этой теме ничего не найдено.")
            return
        
        has_more = len(articles) > 5
        articles_to_show = articles[:5]
        
        # Сохраняем статьи для lazy loading
        user_search_state[user_id] = {
            "query": query,
            "offset": offset,
            "articles": {i: art for i, art in enumerate(articles_to_show)}
        }
        
        # Формируем сообщение со списком
        result_lines = [f"📚 Найдено по запросу: {query}\n"]
        for i, art in enumerate(articles_to_show):
            result_lines.append(f"{i+1}. {art['title'][:80]}{'...' if len(art['title']) > 80 else ''} ({art['year']})")
        
        result_lines.append("\n👆 Нажмите на номер статьи для просмотра:")
        result_text = "\n".join(result_lines)
        
        # Кнопки для каждой статьи
        builder = InlineKeyboardBuilder()
        for i in range(len(articles_to_show)):
            builder.button(text=f"📄 {i+1}", callback_data=f"article_{i}")
        builder.adjust(5)  # 5 кнопок в ряд
        
        if has_more:
            new_offset = offset + 5
            builder.button(text="📥 Загрузить ещё", callback_data=f"more_{new_offset}")
        
        await status_msg.delete()
        await message.answer(
            result_text,
            reply_markup=builder.as_markup()
        )
        
        user_search_state[user_id]["offset"] = offset + 5
        
    except Exception as e:
        logging.error(f"Search processing error: {e}")
        await message.answer("⚠️ Произошла ошибка при поиске.")

# 6. Обработчик клика по статье (lazy Telegraph creation)
@dp.callback_query(F.data.startswith("article_"))
async def handle_article_click(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    article_idx = int(callback.data.replace("article_", ""))
    
    if user_id not in user_search_state or "articles" not in user_search_state[user_id]:
        await callback.answer("Сессия истекла. Выполните поиск заново.")
        return
    
    articles = user_search_state[user_id]["articles"]
    if article_idx not in articles:
        await callback.answer("Статья не найдена.")
        return
    
    article = articles[article_idx]
    await callback.answer("⏳ Генерирую краткое содержание...")
    
    # Создаём Telegraph страницу только сейчас
    url = await create_telegraph_page(article)
    
    await callback.message.answer(
        f"📄 <b>{article['title']}</b>\n\n"
        f"<a href=\"{url}\">� Краткое содержание</a>\n"
        f"<a href=\"{article['link']}\">📎 Оригинал на arXiv</a>",
        parse_mode="HTML",
        disable_web_page_preview=True
    )

# 7. Главное меню
def get_main_menu():
    builder = InlineKeyboardBuilder()
    builder.button(text="🔍 Поиск статей", callback_data="search_mode")
    builder.button(text="📬 Мои подписки", callback_data="subscriptions")
    builder.adjust(1)
    return builder.as_markup()

# 7. Обработчики команд
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    db.add_user(message.from_user.id)
    await message.answer(
        "👋 Привет! Я помогу найти научные статьи на arXiv.\n\n"
        "Выберите действие:",
        reply_markup=get_main_menu()
    )

@dp.message(Command("test"))
async def cmd_test(message: types.Message):
    """Manually trigger subscription check for testing."""
    await message.answer("🧪 Запускаю проверку подписок...")
    await check_subscriptions(force=True)
    await message.answer("✅ Проверка завершена!")

@dp.callback_query(F.data == "main_menu")
async def handle_main_menu(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "👋 Выберите действие:",
        reply_markup=get_main_menu()
    )
    await callback.answer()

@dp.callback_query(F.data == "search_mode")
async def handle_search_mode(callback: types.CallbackQuery):
    await callback.message.answer(
        "🔍 Напишите тему для поиска (на английском):\n\n"
        "Например: machine learning, quantum computing, neural networks"
    )
    await callback.answer()

# 8. Обработчики подписок
@dp.callback_query(F.data == "subscriptions")
async def handle_subscriptions(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    subs = db.get_subscriptions(user_id)
    
    builder = InlineKeyboardBuilder()
    
    if subs:
        text = "📬 Ваши подписки:\n\n"
        for sub in subs:
            # Показываем время последней проверки
            last_check_str = "ещё не проверяли"
            if sub['last_checked']:
                last_dt = datetime.fromisoformat(sub['last_checked'])
                last_check_str = last_dt.strftime("%d.%m в %H:%M")
            text += f"• {sub['topic']}\n  🔄 Последняя проверка: {last_check_str}\n\n"
            builder.button(text=f"❌ {sub['topic'][:20]}", callback_data=f"delete_sub_{sub['id']}")
    else:
        text = "📬 У вас пока нет подписок.\n\nСоздайте подписку, чтобы получать уведомления о новых статьях!"
    
    builder.button(text="➕ Добавить подписку", callback_data="add_subscription")
    builder.button(text="◀️ Назад", callback_data="main_menu")
    builder.adjust(1)
    
    await callback.message.edit_text(text, reply_markup=builder.as_markup())
    await callback.answer()

@dp.callback_query(F.data == "add_subscription")
async def handle_add_subscription(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(SubscriptionStates.waiting_for_topic)
    
    builder = InlineKeyboardBuilder()
    builder.button(text="◀️ Отмена", callback_data="subscriptions")
    
    await callback.message.edit_text(
        "✏️ Введите тему для подписки (на английском):\n\n"
        "Например: deep learning, black holes, CRISPR",
        reply_markup=builder.as_markup()
    )
    await callback.answer()

@dp.message(SubscriptionStates.waiting_for_topic)
async def process_subscription_topic(message: types.Message, state: FSMContext):
    topic = message.text.strip()
    user_id = message.from_user.id
    
    # Создаём подписку сразу (ежедневная проверка)
    db.add_subscription(user_id, topic, "daily")
    await state.clear()
    
    await message.answer(
        f"✅ Подписка создана!\n\n"
        f"📌 Тема: {topic}\n\n"
        f"ℹ️ arXiv обновляется ВС-ЧТ в ~06:00.\n"
        f"ПТ-СБ проверки пропускаются — новых статей не бывает.\n\n"
        f"<a href=\"https://info.arxiv.org/help/availability.html\">Подробнее о расписании arXiv</a>",
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=InlineKeyboardBuilder().button(
            text="◀️ К подпискам", callback_data="subscriptions"
        ).as_markup()
    )

@dp.callback_query(F.data.startswith("delete_sub_"))
async def handle_delete_subscription(callback: types.CallbackQuery):
    sub_id = int(callback.data.replace("delete_sub_", ""))
    db.delete_subscription(sub_id)
    
    await callback.answer("Подписка удалена!")
    
    # Refresh subscription list
    await handle_subscriptions(callback)

# 9. Обработчик загрузки дополнительных статей
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

# 10. Обработчик текстовых сообщений (поиск)
@dp.message()
async def handle_text(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    
    # Если не в FSM состоянии — это поиск
    if current_state is None:
        await process_search(message, message.text)

# 11. Фоновая задача мониторинга
async def check_subscriptions(force: bool = False):
    """Проверка подписок и отправка новых статей.
    
    Args:
        force: If True, bypass all checks (for testing)
    """
    now = datetime.now()
    
    # Пропускаем пятницу и субботу — arXiv не обновляется
    if not force and now.weekday() in ARXIV_NO_UPDATE_DAYS:
        logging.info("Пропуск проверки: arXiv не обновляется в ПТ/СБ")
        return
    
    # Проверяем только после 06:00 (когда новые статьи уже опубликованы)
    if not force and now.hour < ARXIV_UPDATE_HOUR:
        logging.info(f"Пропуск проверки: ещё рано ({now.hour}:00 < {ARXIV_UPDATE_HOUR}:00)")
        return
    
    logging.info("Запуск проверки подписок...")
    
    subscriptions = db.get_all_subscriptions()
    
    for sub in subscriptions:
        try:
            # Проверяем, прошло ли 24 часа с последней проверки
            if not force:
                last_checked = sub['last_checked']
                if last_checked:
                    last_dt = datetime.fromisoformat(last_checked)
                    hours_since_last = (now - last_dt).total_seconds() / 3600
                    
                    if hours_since_last < 24:
                        continue  # Ещё не время
            
            # Ищем новые статьи
            articles = get_arxiv_articles(sub['topic'], max_results=5)
            
            # Фильтруем уже отправленные
            new_articles = []
            for art in articles:
                if not db.is_paper_seen(sub['user_id'], art['arxiv_id']):
                    new_articles.append(art)
            
            if new_articles:
                # Отправляем уведомление с новыми статьями
                telegraph_urls = []
                for art in new_articles[:3]:  # Максимум 3 статьи за раз
                    url = await create_telegraph_page(art)
                    telegraph_urls.append(url)
                    db.mark_paper_seen(sub['user_id'], sub['id'], art['arxiv_id'])
                
                result_lines = [f"🔔 Новые статьи по теме: {sub['topic']}\n"]
                for art, url in zip(new_articles[:3], telegraph_urls):
                    result_lines.append(f"• <a href=\"{url}\">{art['title']}</a> ({art['year']})")
                
                await bot.send_message(
                    sub['user_id'],
                    "\n".join(result_lines),
                    parse_mode="HTML",
                    disable_web_page_preview=True
                )
            else:
                # Уведомляем, что новых статей нет
                await bot.send_message(
                    sub['user_id'],
                    f"📭 По теме «{sub['topic']}» новых статей пока нет."
                )
            
            # Обновляем время проверки
            db.update_last_checked(sub['id'])
            
        except Exception as e:
            logging.error(f"Ошибка проверки подписки {sub['id']}: {e}")

# 12. Запуск
async def main():
    # Инициализация БД
    db.init_db()
    
    # Запуск планировщика (каждый час в :00)
    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_subscriptions, 'cron', minute=0)
    scheduler.start()
    
    print("Бот в сети!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())