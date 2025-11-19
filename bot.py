import os
import logging
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import sqlite3
import pytz

# Налаштування логування
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Часовий пояс
TIMEZONE = pytz.timezone('Europe/Kiev')

# ID адміністратора
ADMIN_ID = int(os.environ.get('ADMIN_ID', '0'))

# Ініціалізація бази даних
def init_db():
    conn = sqlite3.connect('worktime.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS work_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            user_name TEXT,
            date TEXT,
            check_in TEXT,
            check_out TEXT,
            hours_worked REAL
        )
    ''')
    conn.commit()
    return conn

# Глобальне підключення до БД
db_conn = init_db()

# Словник для зберігання часу приходу
user_checkins = {}

def get_user_stats(user_id, days=None):
    """Отримати статистику користувача"""
    cursor = db_conn.cursor()
    
    if days:
        date_limit = (datetime.now(TIMEZONE) - timedelta(days=days)).strftime('%Y-%m-%d')
        cursor.execute('''
            SELECT date, check_in, check_out, hours_worked 
            FROM work_records 
            WHERE user_id = ? AND date >= ?
            ORDER BY date DESC, check_in DESC
        ''', (user_id, date_limit))
    else:
        cursor.execute('''
            SELECT date, check_in, check_out, hours_worked 
            FROM work_records 
            WHERE user_id = ?
            ORDER BY date DESC, check_in DESC
        ''', (user_id,))
    
    return cursor.fetchall()

def get_all_users_stats(days=None):
    """Отримати статистику всіх користувачів"""
    cursor = db_conn.cursor()
    
    if days:
        date_limit = (datetime.now(TIMEZONE) - timedelta(days=days)).strftime('%Y-%m-%d')
        cursor.execute('''
            SELECT user_name, SUM(hours_worked) as total_hours, COUNT(*) as days_worked
            FROM work_records 
            WHERE date >= ? AND hours_worked IS NOT NULL
            GROUP BY user_id, user_name
            ORDER BY total_hours DESC
        ''', (date_limit,))
    else:
        cursor.execute('''
            SELECT user_name, SUM(hours_worked) as total_hours, COUNT(*) as days_worked
            FROM work_records 
            WHERE hours_worked IS NOT NULL
            GROUP BY user_id, user_name
            ORDER BY total_hours DESC
        ''')
    
    return cursor.fetchall()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    user = update.effective_user
    welcome_message = (
        f"Привіт, {user.first_name}! 👋\n\n"
        f"Твій Telegram ID: {user.id}\n\n"
        "🕐 Відмітка часу:\n"
        "• /come - коли приходиш на роботу\n"
        "• /end - коли йдеш з роботи\n"
        "• Або напиши 'прийшов' / 'пішов'\n\n"
        "📊 Статистика:\n"
        "• /today - сьогоднішній день\n"
        "• /week - за тиждень\n"
        "• /month - за місяць\n"
        "• /stats - за весь час\n"
    )
    
    if user.id == ADMIN_ID:
        welcome_message += "\n🔑 Адмін команди:\n• /all - статистика всіх працівників"
    
    await update.message.reply_text(welcome_message)

async def come(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /come - прихід на роботу"""
    user = update.effective_user
    
    current_time = datetime.now(TIMEZONE)
    time_str = current_time.strftime('%H:%M')
    date_str = current_time.strftime('%Y-%m-%d')
    
    user_name = user.first_name
    if user.last_name:
        user_name += f" {user.last_name}"
    
    user_checkins[user.id] = current_time
    
    cursor = db_conn.cursor()
    try:
        cursor.execute('''
            INSERT INTO work_records (user_id, user_name, date, check_in)
            VALUES (?, ?, ?, ?)
        ''', (user.id, user_name, date_str, time_str))
        db_conn.commit()
        
        await update.message.reply_text(f"✅ Відмічено! Прийшов о {time_str}")
    except Exception as e:
        logger.error(f"Помилка запису: {e}")
        await update.message.reply_text("❌ Помилка запису.")

async def end(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /end - ухід з роботи"""
    user = update.effective_user
    
    if user.id not in user_checkins:
        await update.message.reply_text("⚠️ Спочатку потрібно відмітити прихід командою /come!")
        return
    
    current_time = datetime.now(TIMEZONE)
    time_str = current_time.strftime('%H:%M')
    date_str = current_time.strftime('%Y-%m-%d')
    
    checkin_time = user_checkins[user.id]
    checkout_time = current_time
    
    time_diff = checkout_time - checkin_time
    hours_worked = round(time_diff.total_seconds() / 3600, 2)
    
    cursor = db_conn.cursor()
    try:
        cursor.execute('''
            UPDATE work_records 
            SET check_out = ?, hours_worked = ?
            WHERE user_id = ? AND date = ? AND check_out IS NULL
            ORDER BY id DESC
            LIMIT 1
        ''', (time_str, hours_worked, user.id, date_str))
        db_conn.commit()
        
        del user_checkins[user.id]
        await update.message.reply_text(
            f"✅ Відмічено! Пішов о {time_str}\n"
            f"⏱ Відпрацьовано: {hours_worked} год"
        )
    except Exception as e:
        logger.error(f"Помилка оновлення: {e}")
        await update.message.reply_text("❌ Помилка запису.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробка повідомлень від користувачів"""
    user = update.effective_user
    text = update.message.text.lower().strip()
    
    current_time = datetime.now(TIMEZONE)
    time_str = current_time.strftime('%H:%M')
    date_str = current_time.strftime('%Y-%m-%d')
    
    user_name = user.first_name
    if user.last_name:
        user_name += f" {user.last_name}"
    
    cursor = db_conn.cursor()
    
    # Обробка приходу
    if any(word in text for word in ['прийшов', 'прийшла', 'пришел', 'пришла', 'прибув', 'прибула', 'на роботі', 'на работе']):
        user_checkins[user.id] = current_time
        
        try:
            cursor.execute('''
                INSERT INTO work_records (user_id, user_name, date, check_in)
                VALUES (?, ?, ?, ?)
            ''', (user.id, user_name, date_str, time_str))
            db_conn.commit()
            
            await update.message.reply_text(f"✅ Відмічено! Прийшов о {time_str}")
        except Exception as e:
            logger.error(f"Помилка запису: {e}")
            await update.message.reply_text("❌ Помилка запису.")
        return
    
    # Обробка уходу
    if any(word in text for word in ['пішов', 'пішла', 'ушел', 'ушла', 'йду', 'іду', 'вийшов', 'вийшла', 'вышел', 'вышла']):
        if user.id not in user_checkins:
            await update.message.reply_text("⚠️ Спочатку потрібно відмітити прихід!")
            return
        
        checkin_time = user_checkins[user.id]
        checkout_time = current_time
        
        time_diff = checkout_time - checkin_time
        hours_worked = round(time_diff.total_seconds() / 3600, 2)
        
        try:
            cursor.execute('''
                UPDATE work_records 
                SET check_out = ?, hours_worked = ?
                WHERE user_id = ? AND date = ? AND check_out IS NULL
                ORDER BY id DESC
                LIMIT 1
            ''', (time_str, hours_worked, user.id, date_str))
            db_conn.commit()
            
            del user_checkins[user.id]
            await update.message.reply_text(
                f"✅ Відмічено! Пішов о {time_str}\n"
                f"⏱ Відпрацьовано: {hours_worked} год"
            )
        except Exception as e:
            logger.error(f"Помилка оновлення: {e}")
            await update.message.reply_text("❌ Помилка запису.")
        return
    
    await update.message.reply_text(
        "🤔 Не зрозумів. Використай команди:\n"
        "• /come - прийшов\n"
        "• /end - пішов\n"
        "• /today, /week, /stats - статистика\n\n"
        "Або просто напиши 'прийшов' чи 'пішов'"
    )

async def today_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Статистика за сьогодні"""
    user = update.effective_user
    today = datetime.now(TIMEZONE).strftime('%Y-%m-%d')
    
    cursor = db_conn.cursor()
    cursor.execute('''
        SELECT check_in, check_out, hours_worked 
        FROM work_records 
        WHERE user_id = ? AND date = ?
    ''', (user.id, today))
    
    records = cursor.fetchall()
    
    if not records:
        await update.message.reply_text("📭 Сьогодні ще немає записів.")
        return
    
    message = "📅 Сьогодні:\n\n"
    total_hours = 0
    
    for check_in, check_out, hours in records:
        message += f"🕐 Прийшов: {check_in}\n"
        if check_out:
            message += f"🕐 Пішов: {check_out}\n"
            message += f"⏱ Відпрацьовано: {hours} год\n\n"
            total_hours += hours or 0
        else:
            message += "⏳ Ще на роботі...\n\n"
    
    if total_hours > 0:
        message += f"Загалом сьогодні: {total_hours} год"
    
    await update.message.reply_text(message)

async def week_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Статистика за тиждень"""
    user = update.effective_user
    records = get_user_stats(user.id, days=7)
    
    if not records:
        await update.message.reply_text("📭 Записів за тиждень немає.")
        return
    
    message = "📅 Статистика за тиждень:\n\n"
    total_hours = 0
    days_worked = set()
    
    for date, check_in, check_out, hours in records:
        if hours:
            days_worked.add(date)
            total_hours += hours
            date_formatted = datetime.strptime(date, '%Y-%m-%d').strftime('%d.%m')
            message += f"• {date_formatted}: {hours} год\n"
    
    message += f"\nЗагалом: {total_hours} год\n"
    message += f"Робочих днів: {len(days_worked)}"
    
    await update.message.reply_text(message)

async def month_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Статистика за місяць"""
    user = update.effective_user
    records = get_user_stats(user.id, days=30)
    
    if not records:
        await update.message.reply_text("📭 Записів за місяць немає.")
        return
    
    total_hours = 0
    days_worked = set()
    
    for date, check_in, check_out, hours in records:
        if hours:
            days_worked.add(date)
            total_hours += hours
    
    message = f"📅 Статистика за місяць:\n\n"
    message += f"⏱ Всього годин: {total_hours} год\n"
    message += f"📆 Робочих днів: {len(days_worked)}\n"
    
    if days_worked:
        avg_hours = round(total_hours / len(days_worked), 2)
        message += f"📊 В середньому: {avg_hours} год/день"
    
    await update.message.reply_text(message)

async def all_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Статистика за весь час"""
    user = update.effective_user
    records = get_user_stats(user.id)
    
    if not records:
        await update.message.reply_text("📭 Записів ще немає.")
        return
    
    total_hours = 0
    days_worked = set()
    
    for date, check_in, check_out, hours in records:
        if hours:
            days_worked.add(date)
            total_hours += hours
    
    message = f"📊 Статистика за весь час:\n\n"
    message += f"⏱ Всього годин: {total_hours} год\n"
    message += f"📆 Робочих днів: {len(days_worked)}\n"
    
    if days_worked:
        avg_hours = round(total_hours / len(days_worked), 2)
        message += f"📊 В середньому: {avg_hours} год/день"
    
    await update.message.reply_text(message)

async def admin_all_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Статистика всіх працівників (тільки для адміна)"""
    user = update.effective_user
    
    if user.id != ADMIN_ID:
        await update.message.reply_text("❌ Ця команда доступна тільки адміністратору.")
        return
    
    records = get_all_users_stats(days=30)
    
    if not records:
        await update.message.reply_text("📭 Немає даних.")
        return
    
    message = "👥 Статистика всіх працівників (30 днів):\n\n"
    
    for user_name, total_hours, days in records:
        avg = round(total_hours / days, 2) if days > 0 else 0
        message += f"👤 {user_name}\n"
        message += f"   ⏱ {total_hours} год за {days} днів (сер. {avg} год/день)\n\n"
    
    await update.message.reply_text(message)

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробка помилок"""
    logger.error(f"Помилка: {context.error}")

def main():
    """Запуск бота"""
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN не встановлено!")
        return
    
    application = Application.builder().token(token).build()
    
    # Команди
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("come", come))
    application.add_handler(CommandHandler("end", end))
    application.add_handler(CommandHandler("today", today_stats))
    application.add_handler(CommandHandler("week", week_stats))
    application.add_handler(CommandHandler("month", month_stats))
    application.add_handler(CommandHandler("stats", all_stats))
    application.add_handler(CommandHandler("all", admin_all_users))
    
    # Текстові повідомлення
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(error_handler)
    
    logger.info("Бот запущено!")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
