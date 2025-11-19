import logging
import re
from datetime import datetime, timedelta, time
import os
import json
import asyncio

# --- Import Libraries ថ្មីសម្រាប់ Firebase ---
import firebase_admin
from firebase_admin import credentials, firestore

# --- Import សម្រាប់ Web Server ---
from aiohttp import web

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

# --- កំណត់រចនាសម្ព័ន្ធ (Configuration) ---

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
FIREBASE_CREDENTIALS_JSON = os.environ.get("FIREBASE_CREDENTIALS_JSON")

COLLECTION_NAME = "transactions" # ឈ្មោះ Collection ក្នុង Firestore

# កំណត់ម៉ោងកម្ពុជា (UTC+7)
CAMBODIA_TIME_OFFSET = 7

# --- កូដ Regex និងទម្រង់កាលបរិច្ឆេទ ---
# កែសម្រួល TRANSACTION_REGEX សម្រាប់ទម្រង់សារថ្មី៖
# Received 29.00 USD from PHEARA TAK,ABA Bank by KHQR,on 19-Nov-2025 08:08AM, ...
# ប្រើ .*?on\s* ដើម្បីចាប់យកអក្សរនៅកណ្តាល និងត្រូវនឹង 'on' ដែលប្រហែលគ្មាន Space បន្ទាប់ពី comma
TRANSACTION_REGEX = r"Received ([\d\.,]+) (USD|KHR).*?on\s*(\d{2}-[A-Za-z]{3}-\d{4} \d{2}:\d{2}[AP]M)"
DATE_FORMAT_IN = "%d-%b-%Y %I:%M%p" 
DATE_FORMAT_QUERY = "%Y-%m-%d"
DATETIME_FORMAT_QUERY = "%Y-%m-%d %H:%M"
TIME_FORMAT_QUERY = "%H:%M" # សម្រាប់បញ្ចូលម៉ោង

# --- States សម្រាប់ Conversation ---
(
    SELECT_ACTION, 
    GET_DAY, 
    GET_MONTH, 
    GET_CUSTOM_START, 
    GET_CUSTOM_END,
    CUSTOM_RANGE_CHOICE, 
    GET_TODAY_START_TIME, 
    GET_TODAY_END_TIME   
) = range(8)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Global variable សម្រាប់ Firestore client
db = None

# --- មុខងារ Database ---

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

def _add_transaction_sync(chat_id: int, amount: float, currency: str, dt_obj: datetime):
    """Sync function សម្រាប់បន្ថែមទិន្នន័យ (រត់ក្នុង thread)"""
    try:
        data = {
            "chat_id": chat_id, 
            "amount": amount,
            "currency": currency,
            "timestamp": dt_obj 
        }
        db.collection(COLLECTION_NAME).add(data)
        logger.info(f"Logged to Firestore for chat {chat_id}: {amount} {currency}")
    except Exception as e:
        logger.error(f"Failed to add transaction to Firestore: {e}")

async def add_transaction_db(chat_id: int, amount: float, currency: str, dt_obj: datetime):
    """បន្ថែមប្រតិបត្តិការថ្មីទៅក្នុង Firestore (async wrapper)"""
    if db:
        await asyncio.to_thread(_add_transaction_sync, chat_id, amount, currency, dt_obj)

def _get_sum_sync(chat_id: int, start_dt: datetime, end_dt: datetime) -> dict:
    """
    Sync function សម្រាប់បូកសរុប (រត់ក្នុង thread)
    """
    totals = {'USD': 0.0, 'KHR': 0.0}
    
    # Log Query Range
    logger.info(f"DEBUG QUERY: ChatID={chat_id}, Start={start_dt.strftime(DATETIME_FORMAT_QUERY)}, End={end_dt.strftime(DATETIME_FORMAT_QUERY)}")

    try:
        collection_ref = db.collection(COLLECTION_NAME)
        
        # បង្កើត query (តម្រូវការ Index)
        query = collection_ref.where("chat_id", "==", chat_id) \
                              .where("timestamp", ">=", start_dt) \
                              .where("timestamp", "<=", end_dt)
        
        results = query.stream()
        
        doc_count = 0
        for doc in results:
            doc_count += 1
            data = doc.to_dict()
            
            logger.debug(f"FOUND DOC #{doc_count}: {data}") 
            
            if 'currency' in data and 'amount' in data:
                if data['currency'] == 'USD':
                    totals['USD'] += data.get('amount', 0.0)
                elif data['currency'] == 'KHR':
                    totals['KHR'] += data.get('amount', 0.0)
        
        logger.info(f"Finished query. Total documents found: {doc_count}")
        return totals
    except Exception as e:
        # បង្ហាញ Error ក្នុង Logs ក្នុងករណី Index ខ្វះខាត
        logger.error(f"CRITICAL FIREBASE ERROR (Check Index!): {e}")
        return totals 

async def get_sum_db(chat_id: int, start_dt: datetime, end_dt: datetime) -> dict:
    """បូកសរុបទឹកប្រាក់ពី Firestore (async wrapper)"""
    if db:
        return await asyncio.to_thread(_get_sum_sync, chat_id, start_dt, end_dt)
    else:
        logger.error("Firestore 'db' client is not initialized.")
        return {'USD': 0.0, 'KHR': 0.0}


# --- មុខងារ Bot Handlers ---

def format_totals_message(prefix: str, totals: dict) -> str:
    """រៀបចំទម្រង់សារឆ្លើយតបសម្រាប់ USD និង KHR"""
    usd_total = totals.get('USD', 0.0)
    khr_total = totals.get('KHR', 0.0)
    
    return f"💰 {prefix}\n- សរុប: {usd_total:,.2f} USD\n- សរុប: {khr_total:,.0f} KHR"

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ផ្ញើសារស្វាគមន៍ និងប៊ូតុងនៅពេលវាយ /start ឬ /sum (ដោយគ្មាន arguments)"""
    await show_main_menu(update.message.chat_id, context, "សូមស្វាគមន៍! ខ្ញុំជា Bot បូកសរុបទឹកប្រាក់។")

async def show_main_menu(chat_id: int, context: ContextTypes.DEFAULT_TYPE, text: str):
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
    """ស្តាប់រាល់សារទាំងអស់ក្នុង Group ដើម្បីចាប់យកប្រតិបត្តិការ"""

    if not update.message or not update.message.text:
        return
        
    logger.info(f"DEBUG: Bot បានទទួលសារថ្មីក្នុង Chat ID {update.message.chat_id}")
    
    text = update.message.text
    chat_id = update.message.chat_id
    
    # ប្រើ TRANSACTION_REGEX ដែលបានកែសម្រួល
    match = re.search(TRANSACTION_REGEX, text, re.IGNORECASE)
    
    if match:
        try:
            amount_str = match.group(1).replace(",", "")
            amount = float(amount_str)
            
            currency = match.group(2).upper()
            date_str = match.group(3)
            
            dt_obj = datetime.strptime(date_str, DATE_FORMAT_IN)
            
            logger.info(f"DEBUG: កំពុងបញ្ជូនទិន្នន័យទៅ Firestore: {amount} {currency} at {dt_obj}")
            await add_transaction_db(chat_id, amount, currency, dt_obj)
            
        except Exception as e:
            logger.error(f"Failed to parse or add transaction: {e}\nText: {text}")

# --- មុខងារ Command /sum ---
async def handle_sum_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ដោះស្រាយ Command /sum ជាមួយ arguments"""
    if not context.args:
        await start_command(update, context)
        return

    chat_id = update.message.chat_id
    arg_text = " ".join(context.args)

    try:
        # 1. Datetime Range
        match = re.match(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}) to (\d{4}-\d{2}-\d{2} \d{2}:\d{2})', arg_text, re.IGNORECASE)
        if match:
            start_dt = datetime.strptime(match.group(1), DATETIME_FORMAT_QUERY)
            end_dt = datetime.strptime(match.group(2), DATETIME_FORMAT_QUERY)
            prefix = f"សរុបពី {match.group(1)} ដល់ {match.group(2)}"
            totals = await get_sum_db(chat_id, start_dt, end_dt)
            await update.message.reply_text(format_totals_message(prefix, totals))
            return

        # 2. Date Range
        match = re.match(r'(\d{4}-\d{2}-\d{2}) to (\d{4}-\d{2}-\d{2})', arg_text, re.IGNORECASE)
        if match:
            start_date = datetime.strptime(match.group(1), DATE_FORMAT_QUERY).date()
            end_date = datetime.strptime(match.group(2), DATE_FORMAT_QUERY).date()
            start_dt = datetime.combine(start_date, datetime.min.time())
            end_dt = datetime.combine(end_date, datetime.max.time())
            prefix = f"សរុបពី {match.group(1)} ដល់ {match.group(2)}"
            totals = await get_sum_db(chat_id, start_dt, end_dt)
            await update.message.reply_text(format_totals_message(prefix, totals))
            return

        # 3. Day
        match = re.match(r'^\d{4}-\d{2}-\d{2}$', arg_text)
        if match:
            day_obj = datetime.strptime(arg_text, DATE_FORMAT_QUERY).date()
            start_dt = datetime.combine(day_obj, datetime.min.time())
            end_dt = datetime.combine(day_obj, datetime.max.time())
            prefix = f"សរុប (ថ្ងៃ {arg_text})"
            totals = await get_sum_db(chat_id, start_dt, end_dt)
            await update.message.reply_text(format_totals_message(prefix, totals))
            return

        # 4. Month
        match = re.match(r'^\d{4}-\d{2}$', arg_text)
        if match:
            month_start_dt = datetime.strptime(arg_text, "%Y-%m")
            next_month = (month_start_dt.replace(day=28) + timedelta(days=4))
            month_end_date = next_month - timedelta(days=next_month.day)
            month_end_dt = datetime.combine(month_end_date, datetime.max.time())
            
            prefix = f"សរុប (ខែ {arg_text})"
            totals = await get_sum_db(chat_id, month_start_dt, month_end_dt)
            await update.message.reply_text(format_totals_message(prefix, totals))
            return

        # បើរកមិនឃើញទម្រង់ណាមួយ
        await update.message.reply_text("ទម្រង់ Command មិនត្រឹមត្រូវ។\nឧ: /sum 2025-11-13\nឬ /sum 2025-11")
    
    except ValueError:
        await update.message.reply_text("កាលបរិច្ឆេទមិនត្រឹមត្រូវ។")
    except Exception as e:
        logger.error(f"Error in handle_sum_command: {e}")
        await update.message.reply_text("មានបញ្ហាក្នុងការដំណើរការ Command។")


# --- មុខងារបង្កើតប៊ូតុង Custom Range ---
def make_custom_range_keyboard():
    """បង្កើតប៊ូតុងសម្រាប់ Custom Range"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⌚️ ក្នុងថ្ងៃនេះ (បញ្ចូលម៉ោង)", callback_data='today_range')],
        [InlineKeyboardButton("🗓️ កំណត់ខ្លួនឯង (Y-m-d H:M)", callback_data='manual_range')]
    ])

async def handle_button_press(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ដោះស្រាយនៅពេលអ្នកប្រើចុចប៊ូតុង Inline (Timezone Handled)"""
    query = update.callback_query
    await query.answer() 
    data = query.data
    chat_id = query.message.chat_id

    # គណនាម៉ោងបច្ចុប្បន្ននៅកម្ពុជា (UTC+7)
    kh_now = datetime.utcnow() + timedelta(hours=CAMBODIA_TIME_OFFSET)
    today = kh_now.date()

    if data == 'sum_today':
        await query.edit_message_text(text="... 🗓️ កំពុងគណនាបូកតាមថ្ងៃ (Today) ...")
        
        start_dt = datetime.combine(today, datetime.min.time())
        end_dt = datetime.combine(today, datetime.max.time())
        
        totals = await get_sum_db(chat_id, start_dt, end_dt)
        prefix = f"សរុបទឹកប្រាក់ (ថ្ងៃនេះ {today.strftime(DATE_FORMAT_QUERY)})"
        message = format_totals_message(prefix, totals)
        
        await query.edit_message_text(message)
        return ConversationHandler.END

    elif data == 'sum_this_month':
        await query.edit_message_text(text="... 📅 កំពុងគណនាបូកតាមខែ (This Month) ...")

        start_dt_date = today.replace(day=1) 
        start_dt = datetime.combine(start_dt_date, datetime.min.time()) 
        
        next_month = start_dt_date.replace(day=28) + timedelta(days=4)
        end_dt_date = next_month - timedelta(days=next_month.day)
        end_dt = datetime.combine(end_dt_date, datetime.max.time())
        
        totals = await get_sum_db(chat_id, start_dt, end_dt)
        prefix = f"សរុបទឹកប្រាក់ (ខែ {today.strftime('%Y-%m')})"
        message = format_totals_message(prefix, totals)

        await query.edit_message_text(message)
        return ConversationHandler.END

    elif data == 'select_day':
        await query.edit_message_text(text=f"☀️ សូមវាយបញ្ចូលថ្ងៃ (ទម្រង់ YYYY-MM-DD ឧ: {today.strftime(DATE_FORMAT_QUERY)}):")
        return GET_DAY

    elif data == 'select_month':
        await query.edit_message_text(text=f"🌙 សូមវាយបញ្ចូលខែ (ទម្រង់ YYYY-MM ឧ: {today.strftime('%Y-%m')}):")
        return GET_MONTH

    elif data == 'custom_range':
        keyboard = make_custom_range_keyboard()
        await query.edit_message_text(text="🔢 សូមជ្រើសរើសប្រភេទបូកសរុប៖", reply_markup=keyboard)
        return CUSTOM_RANGE_CHOICE
        
    return ConversationHandler.END

# --- មុខងារសម្រាប់ដោះស្រាយជម្រើស Custom Range ---
async def handle_custom_range_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    (*** បានកែសម្រួល: បន្ថែម Logging សម្រាប់ Debug ប៊ូតុង ***)
    ដោះស្រាយប៊ូតុង 'ក្នុងថ្ងៃនេះ' vs 'កំណត់ខ្លួនឯង'
    """
    query = update.callback_query
    await query.answer()
    data = query.data

    logger.info(f"DEBUG: handle_custom_range_choice received data: {data}") # Log ដើម្បីពិនិត្យមើល

    if data == 'today_range':
        await query.edit_message_text(text=f"⌚️ សូមវាយបញ្ចូលម៉ោងចាប់ផ្ដើម (ទម្រង់ HH:MM ឧ: 08:00):")
        return GET_TODAY_START_TIME 
    
    elif data == 'manual_range':
        # ប៊ូតុងនេះហើយដែលអ្នកមានបញ្ហា
        await query.edit_message_text(text=f"🗓️ សូមវាយបញ្ចូល ថ្ងៃ/ម៉ោង ចាប់ផ្ដើម (ទម្រង់ YYYY-MM-DD HH:MM ឧ: 2025-11-12 08:00):")
        logger.info("DEBUG: Transitioning to GET_CUSTOM_START from manual_range.")
        return GET_CUSTOM_START # ត្រូវប្រាកដថា State នេះបានត្រឡប់ត្រឹមត្រូវ

# --- មុខងារសម្រាប់ដោះស្រាយម៉ោង 'ក្នុងថ្ងៃនេះ' ---
async def handle_today_start_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """រក្សាទុកម៉ោងចាប់ផ្ដើម (ក្នុងថ្ងៃនេះ)"""
    try:
        time_str = update.message.text
        start_time_obj = datetime.strptime(time_str, TIME_FORMAT_QUERY).time()
        context.user_data['today_start_time'] = start_time_obj
        
        await update.message.reply_text(f"⌚️ សូមវាយបញ្ចូលម៉ោងបញ្ចប់ (ទម្រង់ HH:MM ឧ: 17:00):")
        return GET_TODAY_END_TIME
        
    except ValueError:
        await update.message.reply_text(f"ទម្រង់មិនត្រឹមត្រូវ. សូមវាយបញ្ចូលម៉ោង (HH:MM ឧ: 08:00):")
        return GET_TODAY_START_TIME

async def handle_today_end_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """គណនាបូកសរុប (ក្នុងថ្ងៃនេះ)"""
    try:
        end_time_str = update.message.text
        end_time_obj = datetime.strptime(end_time_str, TIME_FORMAT_QUERY).time()
        start_time_obj = context.user_data['today_start_time']
        
        kh_now = datetime.utcnow() + timedelta(hours=CAMBODIA_TIME_OFFSET)
        today = kh_now.date()
        
        start_dt = datetime.combine(today, start_time_obj)
        end_dt = datetime.combine(today, end_time_obj)
        
        if start_dt >= end_dt:
            await update.message.reply_text("ម៉ោងចាប់ផ្ដើម ត្រូវតែតូចជាងម៉ោងបញ្ចប់។ សូមព្យាយាមម្តងទៀត។")
            await show_main_menu(update.message.chat_id, context, "សូមជ្រើសរើសម្តងទៀត៖")
            return ConversationHandler.END

        totals = await get_sum_db(update.message.chat_id, start_dt, end_dt)
        
        prefix = f"សរុប (ថ្ងៃនេះ {today.strftime(DATE_FORMAT_QUERY)}) ពី {start_time_obj.strftime(TIME_FORMAT_QUERY)} ដល់ {end_time_obj.strftime(TIME_FORMAT_QUERY)}"
        message = format_totals_message(prefix, totals)
        
        await update.message.reply_text(message)
        
    except ValueError:
        await update.message.reply_text(f"ទម្រង់មិនត្រឹមត្រូវ. សូមវាយបញ្ចូលម៉ោង (HH:MM ឧ: 17:00):")
        return GET_TODAY_END_TIME
    except KeyError:
        await update.message.reply_text("មានបញ្ហា. សូមចាប់ផ្ដើមម្ដងទៀតដោយចុច /start")
    
    context.user_data.clear()
    return ConversationHandler.END


async def handle_get_day(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    """ដោះស្រាយការបូកសរុបប្រចាំខែ"""
    try:
        month_str = update.message.text
        month_start_dt = datetime.strptime(month_str, "%Y-%m")
        
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
    """រក្សាទុកថ្ងៃចាប់ផ្ដើម និងសួររកថ្ងៃបញ្ចប់"""
    try:
        start_str = update.message.text
        start_dt = datetime.strptime(start_str, DATETIME_FORMAT_QUERY)
        context.user_data['custom_start_dt'] = start_dt
        
        await update.message.reply_text(f"🗓️ សូមវាយបញ្ចូល ថ្ងៃ/ម៉ោង បញ្ចប់ (ទម្រង់ YYYY-MM-DD HH:MM ឧ: 2025-11-12 20:30):")
        return GET_CUSTOM_END
        
    except ValueError:
        await update.message.reply_text(f"ទម្រង់មិនត្រឹមត្រូវ. សូមវាយបញ្ចូល ថ្ងៃ/ម៉ោង ចាប់ផ្ដើម (YYYY-MM-DD HH:MM):")
        return GET_CUSTOM_START

async def handle_custom_end(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """គណនាបូកសរុប Custom Range"""
    try:
        end_str = update.message.text
        end_dt = datetime.strptime(end_str, DATETIME_FORMAT_QUERY)
        start_dt = context.user_data['custom_start_dt']
        
        if start_dt >= end_dt:
            await update.message.reply_text("ថ្ងៃចាប់ផ្ដើម ត្រូវតែតូចជាងថ្ងៃបញ្ចប់។ សូមព្យាយាមម្តងទៀត។")
            await show_main_menu(update.message.chat_id, context, "សូមជ្រើសរើសម្តងទៀត៖")
            return ConversationHandler.END

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
    """បោះបង់ Conversation"""
    await update.message.reply_text("បានបោះបង់. ចុច /start ដើម្បីចាប់ផ្ដើមម្ដងទៀត។")
    context.user_data.clear()
    return ConversationHandler.END

# --- មុខងារ Web Server ក្លែងក្លាយ ---
async def health_check(request):
    """Endpoint សម្រាប់ Render ពិនិត្យ (health check)"""
    logger.info("Render health check successful.")
    return web.Response(text="Bot is running and healthy!")

# --- មុខងារ Main ---

async def main_async():
    """ចាប់ផ្ដើម Bot និង Web Server ក្នុងពេលតែមួយ"""
    
    if not TELEGRAM_TOKEN:
        logger.critical("TELEGRAM_TOKEN environment variable is not set! Bot cannot start.")
        return
    if not setup_firebase():
        logger.critical("Failed to initialize Firebase. Bot cannot start.")
        return
    
    # 1. បង្កើត Application (Telegram Bot)
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # 2. Conversation Handler 
    conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(handle_button_press) 
        ],
        states={
            GET_DAY: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_get_day)],
            GET_MONTH: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_get_month)],
            CUSTOM_RANGE_CHOICE: [CallbackQueryHandler(handle_custom_range_choice)],
            GET_CUSTOM_START: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_custom_start)],
            GET_CUSTOM_END: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_custom_end)],
            GET_TODAY_START_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_today_start_time)],
            GET_TODAY_END_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_today_end_time)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
        allow_reentry=True 
    )

    # 3. បន្ថែម Handlers ទៅ Bot 
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("sum", handle_sum_command))
    application.add_handler(conv_handler)
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, listen_to_messages)
    )

    # 4. ចាប់ផ្ដើម Bot (ដោយមិន Block)
    try:
        logger.info("Initializing Bot...")
        await application.initialize()
        await application.start()
        await application.updater.start_polling()
        logger.info("Bot is starting...")
    except Exception as e:
        logger.error(f"Failed to start bot polling: {e}")
        return

    # 5. បង្កើត និងដំណើរការ Web Server (សម្រាប់ Render)
    app = web.Application()
    app.router.add_get('/', health_check) 
    
    port = int(os.environ.get("PORT", 10000))
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    
    try:
        await site.start()
        logger.info(f"Bot and Health Check Server are running on port {port}...")
        
        shutdown_event = asyncio.Event()
        await shutdown_event.wait()
        
    finally:
        logger.info("Shutting down...")
        await runner.cleanup()
        await application.updater.stop()
        await application.stop()

# --- របៀបរត់ Main ---
if __name__ == "__main__":
    try:
        asyncio.run(main_async())
    except RuntimeError as e:
        if "can't register atexit" in str(e):
             logger.warning("Ignoring atexit error during Render shutdown.")
        else:
             logger.critical(f"Critical asyncio error: {e}")
    except Exception as e:
        logger.critical(f"Application failed to run: {e}")
