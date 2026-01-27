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

# Хранилище для пагинации: user_id -> {query, offset}
user_search_state = {}
# Глобальное хранилище статей: arxiv_id -> article_data
articles_storage = {}

# FSM состояния для создания подписки
class SubscriptionStates(StatesGroup):
    waiting_for_topic = State()

# arXiv обновляется ВС-ЧТ в ~20:00 ET = ~06:00 UTC+5 (Almaty)
# Пятница (4) и суббота (5) - нет обновлений
ARXIV_NO_UPDATE_DAYS = {4, 5}  # Friday, Saturday
ARXIV_UPDATE_HOUR = 6  # 06:00 local time when new papers appear

# 2. Функция поиска статей через ArXiv API
def get_arxiv_articles(query: str, max_results: int = 5, offset: int = 0):
    """Search arXiv using the official API with pagination.

    Returns:
        tuple: (articles_list, has_next_page)
    """
    arxiv_client = arxiv.Client()
    # Search in title and abstract for better relevance
    # Use "ti:query OR abs:query" to find papers about the topic
    formatted_query = f"ti:{query} OR abs:{query}"
    search = arxiv.Search(
        query=formatted_query,
        max_results=100,  # Large limit for generator
        sort_by=arxiv.SortCriterion.SubmittedDate,
        sort_order=arxiv.SortOrder.Descending
    )

    articles = []
    try:
        # Use offset parameter with generator
        results_generator = arxiv_client.results(search, offset=offset)

        # Fetch max_results + 1 to check if there are more pages
        for i, result in enumerate(results_generator):
            articles.append({
                "title": result.title,
                "link": result.entry_id,
                "abstract": result.summary,
                "year": result.published.year if result.published else "N/A",
                "arxiv_id": result.entry_id.split("/")[-1]
            })
            if len(articles) >= max_results + 1:
                break
    except Exception as e:
        logging.error(f"ArXiv API error: {e}")

    # Check if there are more pages
    has_next = len(articles) > max_results
    current_batch = articles[:max_results]

    # Store articles in global storage
    for art in current_batch:
        articles_storage[art['arxiv_id']] = art

    return current_batch, has_next

# 3. Создание Telegraph страницы для статьи
async def create_telegraph_page(article: dict) -> str:
    """Create a Telegraph page for an article with Russian summary."""
    # Запрос к Gemini
    prompt = (
        f"Ты эксперт-ученый. Переведи на русский и сделай краткий пересказ статьи '{article['title']}'. "
        f"Используй HTML-теги <b>, <i>, <ul>, <li>. Опиши суть, методы и результат. "
        f"Абстракт статьи: {article['abstract']}"
    )

    try:
        response = await asyncio.to_thread(
            client.models.generate_content,
            model="gemini-2.5-flash",
            contents=prompt
        )

        # Очистка текста для Telegraph
        summary_clean = response.text.replace("```html", "").replace("```", "").strip()
        summary_html = summary_clean.replace('\n', '<br>')

        # Создание страницы
        page = telegraph.create_page(
            title=article['title'][:50],
            html_content=f"<h4>{article['title']}</h4><hr>{summary_html}<br><br><a href='{article['link']}'>Источник (arXiv)</a>"
        )

        return page['url']
    except Exception as e:
        logging.error(f"Telegraph/Gemini error: {e}")
        return article['link']

# 5. Функция поиска и отправки результатов
async def display_search_results(target, query: str, offset: int = 0, is_edit: bool = False):
    """Display search results. Can either send new message or edit existing."""
    user_id = target.from_user.id if hasattr(target, 'from_user') else target.chat.id

    # Run blocking arXiv API in thread
    loop = asyncio.get_event_loop()
    articles, has_next = await loop.run_in_executor(
        None, get_arxiv_articles, query, 5, offset
    )

    if not articles and offset == 0:
        text = "❌ По этой теме ничего не найдено."
        if is_edit:
            await target.edit_text(text)
        else:
            await target.answer(text)
        return

    # Build response text
    result_lines = [f"📚 <b>Тема:</b> {query}"]
    result_lines.append(f"Показаны статьи {offset + 1} — {offset + len(articles)}\n")

    builder = InlineKeyboardBuilder()
    for i, art in enumerate(articles):
        num = offset + i + 1
        # Button references article by arxiv_id
        builder.button(text=f"📄 {num}", callback_data=f"article_{art['arxiv_id']}")
        result_lines.append(f"{num}. {art['title'][:80]}{'...' if len(art['title']) > 80 else ''} ({art['year']})")

    result_lines.append("\n👆 Нажмите на номер статьи для просмотра")
    result_text = "\n".join(result_lines)

    builder.adjust(5)  # 5 buttons per row

    # Navigation buttons
    nav_buttons = []
    if offset > 0:
        nav_buttons.append(("◀️ Назад", f"nav_{offset - 5}"))
    if has_next:
        nav_buttons.append(("▶️ Вперёд", f"nav_{offset + 5}"))

    if nav_buttons:
        for text, callback in nav_buttons:
            builder.button(text=text, callback_data=callback)
        builder.adjust(5, len(nav_buttons))  # Articles in row of 5, nav buttons below

    # Save session state
    user_search_state[user_id] = {
        "query": query,
        "offset": offset
    }

    # Send or edit message
    if is_edit:
        await target.edit_text(
            result_text,
            parse_mode="HTML",
            reply_markup=builder.as_markup()
        )
    else:
        await target.answer(
            result_text,
            parse_mode="HTML",
            reply_markup=builder.as_markup()
        )

# 6. Обработчик клика по статье (lazy Telegraph creation)
@dp.callback_query(F.data.startswith("article_"))
async def handle_article_click(callback: types.CallbackQuery):
    arxiv_id = callback.data.replace("article_", "")

    article = articles_storage.get(arxiv_id)
    if not article:
        await callback.answer("Статья не найдена. Выполните поиск заново.", show_alert=True)
        return

    await callback.answer("⏳ Генерирую краткое содержание...")

    # Создаём Telegraph страницу только сейчас
    url = await create_telegraph_page(article)

    await callback.message.answer(
        f"📄 <b>{article['title']}</b>\n\n"
        f"<a href=\"{url}\">📖 Краткое содержание</a>\n"
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

# 8. Обработчики команд
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

# 9. Обработчики подписок
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

# 10. Обработчик навигации (вперёд/назад)
@dp.callback_query(F.data.startswith("nav_"))
async def handle_navigation(callback: types.CallbackQuery):
    user_id = callback.from_user.id

    if user_id not in user_search_state:
        await callback.answer("Сессия истекла. Введите запрос заново.", show_alert=True)
        return

    offset = int(callback.data.replace("nav_", ""))
    query = user_search_state[user_id]["query"]

    await callback.answer()
    await display_search_results(callback.message, query, offset, is_edit=True)

# 11. Обработчик текстовых сообщений (поиск)
@dp.message()
async def handle_text(message: types.Message, state: FSMContext):
    current_state = await state.get_state()

    # Если не в FSM состоянии — это поиск
    if current_state is None:
        await display_search_results(message, message.text, offset=0)

# 12. Фоновая задача мониторинга
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
            # Проверяем, не проверяли ли мы уже сегодня
            if not force:
                last_checked = sub['last_checked']
                if last_checked:
                    last_dt = datetime.fromisoformat(last_checked)
                    # Если уже проверяли сегодня - пропускаем
                    if last_dt.date() == now.date():
                        continue  # Уже проверяли сегодня

            # Ищем новые статьи (returns tuple now)
            articles, _ = get_arxiv_articles(sub['topic'], max_results=5)

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

# 13. Запуск
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
