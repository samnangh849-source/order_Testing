import logging
import re
from datetime import datetime, timedelta
import os # ប្រើសម្រាប់ Environment Variables
import json # ប្រើសម្រាប់អាន Firebase credentials
import asyncio # ប្រើសម្រាប់ Threading

# --- Import Libraries ថ្មីសម្រាប់ Firebase ---
import firebase_admin
from firebase_admin import credentials, firestore

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# --- ការកំណត់រចនាសម្ព័ន្ធ (Configuration) ---

# Bot នឹងអាន TOKEN ពី Environment Variable លើ Server
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")

# យក Firebase credentials ពី Environment Variable
# (អ្នកត្រូវដាក់ Content របស់ .json ចូលទៅក្នុង Variable នេះ)
FIREBASE_CREDENTIALS_JSON = os.environ.get("FIREBASE_CREDENTIALS_JSON")

COLLECTION_NAME = "transactions" # ឈ្មោះ Collection ក្នុង Firestore

# --- កូដ Regex និងទម្រង់កាលបរិច្ឆេទ (ដដែល) ---
TRANSACTION_REGEX = r"Received ([\d\.,]+) (USD|KHR).* on (\d{2}-[A-Za-z]{3}-\d{4} \d{2}:\d{2}[AP]M)"
DATE_FORMAT_IN = "%d-%b-%Y %I:%M%p" 
DATE_FORMAT_QUERY = "%Y-%m-%d"
DATETIME_FORMAT_QUERY = "%Y-%m-%d %H:%M"

(SELECT_ACTION, GET_DAY, GET_MONTH, GET_CUSTOM_START, GET_CUSTOM_END) = range(5)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Global variable សម្រាប់ Firestore client
db = None

# --- មុខងារ Database (ປ່ຽນទៅ Firestore) ---

def setup_firebase():
    """ចាប់ផ្ដើម Firebase Admin SDK"""
    global db
    if not FIREBASE_CREDENTIALS_JSON:
        logger.error("FIREBASE_CREDENTIALS_JSON environment variable is not set.")
        logger.error("Bot មិនអាចដំណើរការបានទេ បើគ្មាន Firebase credentials។")
        return False
    
    try:
        # បម្លែង JSON string ពី environment variable ទៅជា dict
        cred_dict = json.loads(FIREBASE_CREDENTIALS_JSON)
        cred = credentials.Certificate(cred_dict)
        
        # ពិនិត្យមើល
        if not firebase_admin._DEFAULT_APP_NAME in firebase_admin._apps:
             firebase_admin.initialize_app(cred)
        
        db = firestore.client()
        logger.info("Firebase Firestore connected successfully.")
        return True
    
    except json.JSONDecodeError:
        logger.error("Failed to parse FIREBASE_CREDENTIALS_JSON. Is it valid JSON?")
        return False
    except ValueError as e:
        logger.error(f"Firebase credentials error (ប្រហែល credentials មិនត្រឹមត្រូវ): {e}")
        return False
    except Exception as e:
        logger.error(f"Failed to initialize Firebase: {e}")
        return False

# --- មុខងារ Sync សម្រាប់រត់ក្នុង Thread ---
# Firebase Admin SDK ធម្មតាជា (sync) ដូច្នេះយើងត្រូវរត់វាក្នុង thread 
# ដើម្បីកុំឱ្យវា Block កូដ (async) របស់ Telegram Bot

def _add_transaction_sync(chat_id: int, amount: float, currency: str, dt_obj: datetime):
    """Sync function សម្រាប់បន្ថែមទិន្នន័យ (រត់ក្នុង thread)"""
    try:
        data = {
            "chat_id": chat_id,
            "amount": amount,
            "currency": currency,
            "timestamp": dt_obj # Firestore អាចប្រើ datetime object ផ្ទាល់ (ល្អណាស់)
        }
        # .add() បង្កើត Document ID ដោយស្វ័យប្រវត្តិ
        db.collection(COLLECTION_NAME).add(data)
        logger.info(f"Logged to Firestore for chat {chat_id}: {amount} {currency}")
    except Exception as e:
        logger.error(f"Failed to add transaction to Firestore: {e}")

async def add_transaction_db(chat_id: int, amount: float, currency: str, dt_obj: datetime):
    """បន្ថែមប្រតិបត្តិការថ្មីទៅក្នុង Firestore (async wrapper)"""
    if db:
        # រត់ function (sync) ក្នុង thread ដាច់ដោយឡែក
        await asyncio.to_thread(_add_transaction_sync, chat_id, amount, currency, dt_obj)

def _get_sum_sync(chat_id: int, start_dt: datetime, end_dt: datetime) -> dict:
    """Sync function សម្រាប់បូកសរុប (រត់ក្នុង thread)"""
    totals = {'USD': 0.0, 'KHR': 0.0}
    try:
        collection_ref = db.collection(COLLECTION_NAME)
        
        # បង្កើត query
        query = collection_ref.where("chat_id", "==", chat_id) \
                              .where("timestamp", ">=", start_dt) \
                              .where("timestamp", "<=", end_dt)
        
        results = query.stream()
        
        # បូកសរុបលទ្ធផល (client-side)
        for doc in results:
            data = doc.to_dict()
            if 'currency' in data and 'amount' in data:
                if data['currency'] == 'USD':
                    totals['USD'] += data.get('amount', 0.0)
                elif data['currency'] == 'KHR':
                    totals['KHR'] += data.get('amount', 0.0)
        
        return totals
    except Exception as e:
        logger.error(f"Failed to get sum from Firestore: {e}")
        return totals # បង្វែរ 0.0

async def get_sum_db(chat_id: int, start_dt: datetime, end_dt: datetime) -> dict:
    """បូកសរុបទឹកប្រាក់ពី Firestore (async wrapper)"""
    if db:
        return await asyncio.to_thread(_get_sum_sync, chat_id, start_dt, end_dt)
    else:
        logger.error("Firestore 'db' client is not initialized.")
        return {'USD': 0.0, 'KHR': 0.0}


# --- មុខងារ Bot Handlers (ដូចមុន) ---

def format_totals_message(prefix: str, totals: dict) -> str:
# ... (Code នេះដូចដើម មិនបាច់កែ) ...
    """រៀបចំទម្រង់សារឆ្លើយតបសម្រាប់ USD និង KHR"""
    usd_total = totals.get('USD', 0.0)
    khr_total = totals.get('KHR', 0.0)
    
    # ប្រើ :.2f សម្រាប់ USD (2 ខ្ទង់) និង :_,,.0f សម្រាប់ KHR (មានសញ្ញា ,)
    # ចំណាំ៖ ការប្រើ f-string ជាមួយ comma (,) សម្រាប់ KHR
    return f"💰 {prefix}\n- សរុប: {usd_total:,.2f} USD\n- សរុប: {khr_total:,.0f} KHR"

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
# ... (Code នេះដូចដើម មិនបាច់កែ) ...
    """ផ្ញើសារស្វាគមន៍ និងប៊ូតុងនៅពេលវាយ /start"""
    await show_main_menu(update.message.chat_id, context, "សូមស្វាគមន៍! ខ្ញុំជា Bot បូកសរុបទឹកប្រាក់។")

async def show_main_menu(chat_id: int, context: ContextTypes.DEFAULT_TYPE, text: str):
# ... (Code នេះដូចដើម មិនបាច់កែ) ...
    """បង្ហាញប៊ូតុង Menu គោល"""
    keyboard = [
        [InlineKeyboardButton("🗓️ បូកតាមថ្ងៃ (Today)", callback_data='sum_today')],
        [InlineKeyboardButton("📅 បូកតាមខែ (This Month)", callback_data='sum_this_month')],
        [
            InlineKeyboardButton("☀️ ជ្រើសរើសថ្ងៃ", callback_data='select_day'),
            InlineKeyboardButton("🌙 ជ្រើសរើសខែ", callback_data='select_month'),
        ],
        [InlineKeyboardButton("🔢 កំណត់ពេលវេលាផ្ទាល់ខ្លួន", callback_data='custom_range')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)


async def listen_to_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
# ... (Code នេះដូចដើម មិនបាច់កែ) ...
    """ស្តាប់រាល់សារទាំងអស់ក្នុង Group ដើម្បីចាប់យកប្រតិបត្តិការ"""
    if not update.message or not update.message.text:
        return

    text = update.message.text
    chat_id = update.message.chat_id
    
    match = re.search(TRANSACTION_REGEX, text, re.IGNORECASE)
    
    if match:
        try:
            # 1. យក amount string និងដក , ចេញ
            amount_str = match.group(1).replace(",", "")
            # 2. បម្លែងទៅជា float
            amount = float(amount_str)
            
            # 3. យក Currency (USD ឬ KHR)
            currency = match.group(2).upper()
            date_str = match.group(3)
            
            # បម្លែងទម្រង់កាលបរិច្ឆេទ
            dt_obj = datetime.strptime(date_str, DATE_FORMAT_IN)
            
            # បន្ថែមទៅ database
            await add_transaction_db(chat_id, amount, currency, dt_obj)
            
            # (ជាជម្រើស) ឆ្លើយតបទៅសារនោះថា "បានកត់ត្រា"
            # await update.message.reply_text(f"✅ បានកត់ត្រា: {amount} {currency}")
            
        except Exception as e:
            logger.error(f"Failed to parse or add transaction: {e}\nText: {text}")


async def handle_button_press(update: Update, context: ContextTypes.DEFAULT_TYPE):
# ... (Code នេះដូចដើម មិនបាច់កែ) ...
    """ដោះស្រាយនៅពេលអ្នកប្រើចុចប៊ូតុង Inline"""
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.message.chat_id

    if data == 'sum_today':
        today = datetime.now().date()
        start_dt = datetime.combine(today, datetime.min.time())
        end_dt = datetime.combine(today, datetime.max.time())
        
        totals = await get_sum_db(chat_id, start_dt, end_dt)
        prefix = f"សរុបទឹកប្រាក់ (ថ្ងៃនេះ {today.strftime(DATE_FORMAT_QUERY)})"
        message = format_totals_message(prefix, totals)
        
        await query.message.reply_text(message)
        return ConversationHandler.END

    elif data == 'sum_this_month':
        today = datetime.now().date()
        start_dt = today.replace(day=1)
        # រកថ្ងៃចុងខែ
        next_month = start_dt.replace(day=28) + timedelta(days=4)
        end_dt_date = next_month - timedelta(days=next_month.day)
        end_dt = datetime.combine(end_dt_date, datetime.max.time())
        
        totals = await get_sum_db(chat_id, start_dt, end_dt)
        prefix = f"សរុបទឹកប្រាក់ (ខែ {today.strftime('%Y-%m')})"
        message = format_totals_message(prefix, totals)

        await query.message.reply_text(message)
        return ConversationHandler.END

    elif data == 'select_day':
        await query.message.reply_text(f"សូមវាយបញ្ចូលថ្ងៃ (ទម្រង់ YYYY-MM-DD ឧ: {datetime.now().strftime(DATE_FORMAT_QUERY)}):")
        return GET_DAY

    elif data == 'select_month':
        await query.message.reply_text(f"សូមវាយបញ្ចូលខែ (ទម្រង់ YYYY-MM ឧ: {datetime.now().strftime('%Y-%m')}):")
        return GET_MONTH

    elif data == 'custom_range':
        await query.message.reply_text(f"សូមវាយបញ្ចូល ថ្ងៃ/ម៉ោង ចាប់ផ្ដើម (ទម្រង់ YYYY-MM-DD HH:MM ឧ: 2025-11-12 08:00):")
        return GET_CUSTOM_START
        
    return ConversationHandler.END

async def handle_get_day(update: Update, context: ContextTypes.DEFAULT_TYPE):
# ... (Code នេះដូចដើម មិនបាច់កែ) ...
    """ដោះស្រាយការបូកសរុបប្រចាំថ្ងៃ (បន្ទាប់ពីអ្នកប្រើវាយបញ្ចូល)"""
    try:
        day_str = update.message.text
        day_obj = datetime.strptime(day_str, DATE_FORMAT_QUERY).date()
        
        start_dt = datetime.combine(day_obj, datetime.min.time())
        end_dt = datetime.combine(day_obj, datetime.max.time())
        
        totals = await get_sum_db(update.message.chat_id, start_dt, end_dt)
        prefix = f"សរុបទឹកប្រាក់ (ថ្ងៃ {day_str})"
        message = format_totals_message(prefix, totals)
        
        await update.message.reply_text(message)
        
    except ValueError:
        await update.message.reply_text(f"ទម្រង់មិនត្រឹមត្រូវ. សូមវាយបញ្ចូលថ្ងៃ (YYYY-MM-DD):")
        return GET_DAY # សួរម្ដងទៀត
    
    return ConversationHandler.END

async def handle_get_month(update: Update, context: ContextTypes.DEFAULT_TYPE):
# ... (Code នេះដូចដើម មិនបាច់កែ) ...
    """ដោះស្រាយការបូកសរុបប្រចាំខែ"""
    try:
        month_str = update.message.text
        month_start_dt = datetime.strptime(month_str, "%Y-%m")
        
        # រកថ្ងៃចុងខែ
        next_month = (month_start_dt.replace(day=28) + timedelta(days=4))
        month_end_date = next_month - timedelta(days=next_month.day)
        month_end_dt = datetime.combine(month_end_date, datetime.max.time())
        
        totals = await get_sum_db(update.message.chat_id, month_start_dt, month_end_dt)
        prefix = f"សរុបទឹកប្រាក់ (ខែ {month_str})"
        message = format_totals_message(prefix, totals)
        
        await update.message.reply_text(message)
        
    except ValueError:
        await update.message.reply_text(f"ទម្រង់មិនត្រឹមត្រូវ. សូមវាយបញ្ចូលខែ (YYYY-MM):")
        return GET_MONTH # សួរម្ដងទៀត
    
    return ConversationHandler.END

async def handle_custom_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
# ... (Code នេះដូចដើម មិនបាច់កែ) ...
    """រក្សាទុកថ្ងៃចាប់ផ្ដើម និងសួររកថ្ងៃបញ្ចប់"""
    try:
        start_str = update.message.text
        start_dt = datetime.strptime(start_str, DATETIME_FORMAT_QUERY)
        context.user_data['custom_start_dt'] = start_dt
        
        await update.message.reply_text(f"សូមវាយបញ្ចូល ថ្ងៃ/ម៉ោង បញ្ចប់ (ទម្រង់ YYYY-MM-DD HH:MM ឧ: 2025-11-12 20:30):")
        return GET_CUSTOM_END
        
    except ValueError:
        await update.message.reply_text(f"ទម្រង់មិនត្រឹមត្រូវ. សូមវាយបញ្ចូល ថ្ងៃ/ម៉ោង ចាប់ផ្ដើម (YYYY-MM-DD HH:MM):")
        return GET_CUSTOM_START

async def handle_custom_end(update: Update, context: ContextTypes.DEFAULT_TYPE):
# ... (Code នេះដូចដើម មិនបាច់កែ) ...
    """គណនាបូកសរុប Custom Range"""
    try:
        end_str = update.message.text
        end_dt = datetime.strptime(end_str, DATETIME_FORMAT_QUERY)
        start_dt = context.user_data['custom_start_dt']
        
        totals = await get_sum_db(update.message.chat_id, start_dt, end_dt)
        
        start_display = start_dt.strftime(DATETIME_FORMAT_QUERY)
        end_display = end_dt.strftime(DATETIME_FORMAT_QUERY)
        
        prefix = f"សរុបទឹកប្រាក់ពី {start_display} ដល់ {end_display}"
        message = format_totals_message(prefix, totals)
        
        await update.message.reply_text(message)
        
    except ValueError:
        await update.message.reply_text(f"ទម្រង់មិនត្រឹមត្រូវ. សូមវាយបញ្ចូល ថ្ងៃ/ម៉ោង បញ្ចប់ (YYYY-MM-DD HH:MM):")
        return GET_CUSTOM_END
    except KeyError:
        await update.message.reply_text("មានបញ្ហា. សូមចាប់ផ្ដើមម្ដងទៀតដោយចុច /start")
    
    context.user_data.clear()
    return ConversationHandler.END


async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
# ... (Code នេះដូចដើម មិនបាច់កែ) ...
    """បោះបង់ Conversation"""
    await update.message.reply_text("បានបោះបង់. ចុច /start ដើម្បីចាប់ផ្ដើមម្ដងទៀត។")
    context.user_data.clear()
    return ConversationHandler.END

# --- មុខងារ Main ---

def main():
    """ចាប់ផ្ដើម Bot"""
    
    # ពិនិត្យមើល Environment Variables
    if not TELEGRAM_TOKEN:
        logger.critical("TELEGRAM_TOKEN environment variable is not set! Bot cannot start.")
        return

    # 1. ចាប់ផ្ដើម Firebase
    if not setup_firebase():
        logger.critical("Failed to initialize Firebase. Bot cannot start.")
        return
    
    # 2. បង្កើត Application (ដូចដើម)
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # 3. Conversation Handler (ដូចដើម)
    conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(handle_button_press),
            CommandHandler("sum", handle_button_press) # អនុញ្ញាតអោយ /sum ចាប់ផ្ដើម
        ],
        states={
            GET_DAY: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_get_day)],
            GET_MONTH: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_get_month)],
            GET_CUSTOM_START: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_custom_start)],
            GET_CUSTOM_END: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_custom_end)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
    )

    # 4. បន្ថែម Handlers (ដូចដើម)
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(conv_handler)
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, listen_to_messages)
    )

    # 5. ចាប់ផ្ដើម Bot
    logger.info("Bot is starting...")
    application.run_polling()


if __name__ == "__main__":
    main()
