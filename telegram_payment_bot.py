import logging
import re
from datetime import datetime, timedelta, time
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

# --- កូដ Regex និងទម្រង់កាលបរិច្ឆេទ ---
TRANSACTION_REGEX = r"Received ([\d\.,]+) (USD|KHR).* on (\d{2}-[A-Za-z]{3}-\d{4} \d{2}:\d{2}[AP]M)"
DATE_FORMAT_IN = "%d-%b-%Y %I:%M%p" 
DATE_FORMAT_QUERY = "%Y-%m-%d"
DATETIME_FORMAT_QUERY = "%Y-%m-%d %H:%M"
TIME_FORMAT_QUERY = "%H:%M" # សម្រាប់បញ្ចូលម៉ោង

# --- (*** បានកែសម្រួល ***) បន្ថែម States ថ្មី ---
(
    SELECT_ACTION, 
    GET_DAY, 
    GET_MONTH, 
    GET_CUSTOM_START, 
    GET_CUSTOM_END,
    CUSTOM_RANGE_CHOICE,  # State សម្រាប់ ក្នុងថ្ងៃនេះ vs កំណត់ខ្លួនឯង
    GET_TODAY_START_TIME, # State សម្រាប់ម៉ោងចាប់ផ្ដើម (ក្នុងថ្ងៃនេះ)
    GET_TODAY_END_TIME    # State សម្រាប់ម៉ោងបញ្ចប់ (ក្នុងថ្ងៃនេះ)
) = range(8)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Global variable សម្រាប់ Firestore client
db = None

# --- មុខងារ Database (ដូចដើម) ---

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

# --- មុខងារ Sync សម្រាប់រត់ក្នុង Thread (ដូចដើម) ---

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


# --- មុខងារ Bot Handlers ---

def format_totals_message(prefix: str, totals: dict) -> str:
    """រៀបចំទម្រង់សារឆ្លើយតបសម្រាប់ USD និង KHR"""
    usd_total = totals.get('USD', 0.0)
    khr_total = totals.get('KHR', 0.0)
    
    # ប្រើ :.2f សម្រាប់ USD (2 ខ្ទង់) និង :_,,.0f សម្រាប់ KHR (មានសញ្ញា ,)
    # ចំណាំ៖ ការប្រើ f-string ជាមួយ comma (,) សម្រាប់ KHR
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
            
        except Exception as e:
            logger.error(f"Failed to parse or add transaction: {e}\nText: {text}")

# --- (*** មុខងារថ្មី ***) សម្រាប់ Command /sum ---
async def handle_sum_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    ដោះស្រាយ Command /sum ជាមួយ arguments
    ទម្រង់ដែលអាចទទួលយកបាន៖
    /sum
    /sum 2025-11-13
    /sum 2025-11
    /sum 2025-11-13 to 2025-11-14
    /sum 2025-11-13 08:00 to 2025-11-13 17:00
    """
    if not context.args:
        # បើវាយ /sum គ្មាន arguments, បង្ហាញ Menu
        await start_command(update, context)
        return

    chat_id = update.message.chat_id
    arg_text = " ".join(context.args)

    try:
        # 1. ពិនិត្យទម្រង់ Datetime Range (YYYY-MM-DD HH:MM to YYYY-MM-DD HH:MM)
        match = re.match(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}) to (\d{4}-\d{2}-\d{2} \d{2}:\d{2})', arg_text, re.IGNORECASE)
        if match:
            start_dt = datetime.strptime(match.group(1), DATETIME_FORMAT_QUERY)
            end_dt = datetime.strptime(match.group(2), DATETIME_FORMAT_QUERY)
            prefix = f"សរុបពី {match.group(1)} ដល់ {match.group(2)}"
            totals = await get_sum_db(chat_id, start_dt, end_dt)
            await update.message.reply_text(format_totals_message(prefix, totals))
            return

        # 2. ពិនិត្យទម្រង់ Date Range (YYYY-MM-DD to YYYY-MM-DD)
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

        # 3. ពិនិត្យទម្រង់ ថ្ងៃ (YYYY-MM-DD)
        match = re.match(r'^\d{4}-\d{2}-\d{2}$', arg_text)
        if match:
            day_obj = datetime.strptime(arg_text, DATE_FORMAT_QUERY).date()
            start_dt = datetime.combine(day_obj, datetime.min.time())
            end_dt = datetime.combine(day_obj, datetime.max.time())
            prefix = f"សរុប (ថ្ងៃ {arg_text})"
            totals = await get_sum_db(chat_id, start_dt, end_dt)
            await update.message.reply_text(format_totals_message(prefix, totals))
            return

        # 4. ពិនិត្យទម្រង់ ខែ (YYYY-MM)
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


# --- (*** មុខងារថ្មី ***) សម្រាប់បង្កើតប៊ូតុង Custom Range ---
def make_custom_range_keyboard():
    """បង្កើតប៊ូតុងសម្រាប់ Custom Range"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⌚️ ក្នុងថ្ងៃនេះ (បញ្ចូលម៉ោង)", callback_data='today_range')],
        [InlineKeyboardButton("🗓️ កំណត់ខ្លួនឯង (Y-m-d H:M)", callback_data='manual_range')]
    ])

async def handle_button_press(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ដោះស្រាយនៅពេលអ្នកប្រើចុចប៊ូតុង Inline"""
    query = update.callback_query
    await query.answer() # ឆ្លើយតបទៅ Telegram ថាបានទទួល
    data = query.data
    chat_id = query.message.chat_id

    if data == 'sum_today':
        # (*** បានកែសម្រួល ***) លុបប៊ូតុង
        await query.edit_message_text(text="... 🗓️ កំពុងគណនាបូកតាមថ្ងៃ (Today) ...")
        
        today = datetime.now().date()
        start_dt = datetime.combine(today, datetime.min.time())
        end_dt = datetime.combine(today, datetime.max.time())
        
        totals = await get_sum_db(chat_id, start_dt, end_dt)
        prefix = f"សរុបទឹកប្រាក់ (ថ្ងៃនេះ {today.strftime(DATE_FORMAT_QUERY)})"
        message = format_totals_message(prefix, totals)
        
        # (*** បានកែសម្រួល ***) បង្ហាញលទ្ធផលដោយកែសម្រួលសារចាស់
        await query.edit_message_text(message)
        return ConversationHandler.END

    elif data == 'sum_this_month':
        # (*** បានកែសម្រួល ***) លុបប៊ូតុង
        await query.edit_message_text(text="... 📅 កំពុងគណនាបូកតាមខែ (This Month) ...")

        today = datetime.now().date()
        start_dt_date = today.replace(day=1) 
        start_dt = datetime.combine(start_dt_date, datetime.min.time()) 
        
        next_month = start_dt_date.replace(day=28) + timedelta(days=4)
        end_dt_date = next_month - timedelta(days=next_month.day)
        end_dt = datetime.combine(end_dt_date, datetime.max.time())
        
        totals = await get_sum_db(chat_id, start_dt, end_dt)
        prefix = f"សរុបទឹកប្រាក់ (ខែ {today.strftime('%Y-%m')})"
        message = format_totals_message(prefix, totals)

        # (*** បានកែសម្រួល ***) បង្ហាញលទ្ធផលដោយកែសម្រួលសារចាស់
        await query.edit_message_text(message)
        return ConversationHandler.END

    elif data == 'select_day':
        # (*** បានកែសម្រួល ***) លុបប៊ូតុង ហើយសួរសំណួរ
        await query.edit_message_text(text=f"☀️ សូមវាយបញ្ចូលថ្ងៃ (ទម្រង់ YYYY-MM-DD ឧ: {datetime.now().strftime(DATE_FORMAT_QUERY)}):")
        return GET_DAY

    elif data == 'select_month':
        # (*** បានកែសម្រួល ***) លុបប៊ូតុង ហើយសួរសំណួរ
        await query.edit_message_text(text=f"🌙 សូមវាយបញ្ចូលខែ (ទម្រង់ YYYY-MM ឧ: {datetime.now().strftime('%Y-%m')}):")
        return GET_MONTH

    elif data == 'custom_range':
        # (*** បានកែសម្រួល ***) លុប Menu, បង្ហាញជម្រើសថ្មី
        keyboard = make_custom_range_keyboard()
        await query.edit_message_text(text="🔢 សូមជ្រើសរើសប្រភេទបូកសរុប៖", reply_markup=keyboard)
        return CUSTOM_RANGE_CHOICE # ទៅ State ថ្មី
        
    return ConversationHandler.END

# --- (*** មុខងារថ្មី ***) សម្រាប់ដោះស្រាយជម្រើស Custom Range ---
async def handle_custom_range_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ដោះស្រាយប៊ូតុង 'ក្នុងថ្ងៃនេះ' vs 'កំណត់ខ្លួនឯង'"""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == 'today_range':
        await query.edit_message_text(text=f"⌚️ សូមវាយបញ្ចូលម៉ោងចាប់ផ្ដើម (ទម្រង់ HH:MM ឧ: 08:00):")
        return GET_TODAY_START_TIME # ទៅ State ថ្មី
    
    elif data == 'manual_range':
        await query.edit_message_text(text=f"🗓️ សូមវាយបញ្ចូល ថ្ងៃ/ម៉ោង ចាប់ផ្ដើម (ទម្រង់ YYYY-MM-DD HH:MM ឧ: 2025-11-12 08:00):")
        return GET_CUSTOM_START # ទៅ State ចាស់

# --- (*** មុខងារថ្មី ***) សម្រាប់ដោះស្រាយម៉ោង 'ក្នុងថ្ងៃនេះ' ---
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

# --- (*** មុខងារថ្មី ***) សម្រាប់ដោះស្រាយម៉ោង 'ក្នុងថ្ងៃនេះ' ---
async def handle_today_end_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """គណនាបូកសរុប (ក្នុងថ្ងៃនេះ)"""
    try:
        end_time_str = update.message.text
        end_time_obj = datetime.strptime(end_time_str, TIME_FORMAT_QUERY).time()
        start_time_obj = context.user_data['today_start_time']
        
        today = datetime.now().date()
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
    
    # 2. បង្កើត Application
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # 3. Conversation Handler (*** បានកែសម្រួល ***)
    conv_handler = ConversationHandler(
        entry_points=[
            # Conversation ចាប់ផ្តើមតែពេលចុចប៊ូតុង Menu គោល
            CallbackQueryHandler(handle_button_press) 
        ],
        states={
            # States សម្រាប់ Menu គោល
            GET_DAY: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_get_day)],
            GET_MONTH: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_get_month)],
            
            # States សម្រាប់ Custom Range
            CUSTOM_RANGE_CHOICE: [CallbackQueryHandler(handle_custom_range_choice)],
            
            # ... សម្រាប់ 'កំណត់ខ្លួនឯង'
            GET_CUSTOM_START: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_custom_start)],
            GET_CUSTOM_END: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_custom_end)],
            
            # ... សម្រាប់ 'ក្នុងថ្ងៃនេះ'
            GET_TODAY_START_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_today_start_time)],
            GET_TODAY_END_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_today_end_time)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
        # អនុញ្ញាតឱ្យ Conversation ផ្សេងទៀតចាប់ផ្តើមដោយមិនបាច់ /cancel
        allow_reentry=True 
    )

    # 4. បន្ថែម Handlers
    
    application.add_handler(CommandHandler("start", start_command))
    
    # (*** បានកែសម្រួល ***) បន្ថែម CommandHandler ថ្មី សម្រាប់ /sum
    application.add_handler(CommandHandler("sum", handle_sum_command))
    
    # បន្ថែម Conversation Handler (ដែលចាប់ផ្តើមដោយការចុចប៊ូតុង)
    application.add_handler(conv_handler)
    
    # Handler សម្រាប់ស្តាប់សារ (នៅខាងក្រោមគេ)
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, listen_to_messages)
    )

    # 5. ចាប់ផ្ដើម Bot
    logger.info("Bot is starting...")
    application.run_polling()


if __name__ == "__main__":
    main()
