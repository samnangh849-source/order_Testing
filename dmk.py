import logging
import re
import sqlite3
import os
import json
from threading import Thread
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ChatMemberHandler
from flask import Flask

# --- ផ្នែក FAKE WEB SERVER (សម្រាប់ RENDER) ---
app = Flask('')

@app.route('/')
def home():
    return "Bot is running successfully on Render!"

def run_http():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_http)
    t.start()

# --- ផ្នែក GOOGLE SHEETS LIBRARIES ---
try:
    import gspread
    from oauth2client.service_account import ServiceAccountCredentials
    HAS_GSHEET_LIB = True
except ImportError:
    HAS_GSHEET_LIB = False
    print("⚠️ មិនមាន Library 'gspread' ទេ។")

# --- ការកំណត់ (CONFIGURATION) ---
BOT_TOKEN = "8458218985:AAGUOXxAydg8HtbYNd4vbkzp2q_ih3K1JBo"
GOOGLE_SHEET_NAME = "DMK Finance Data"
CREDENTIALS_FILE = "credentials.json"

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# --- ផ្នែក DATABASE (SQLITE) ---
def init_db():
    conn = sqlite3.connect('transactions.db')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            amount REAL,
            currency TEXT,
            transaction_date DATETIME,
            raw_message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    try:
        c.execute("SELECT chat_id FROM transactions LIMIT 1")
    except sqlite3.OperationalError:
        c.execute("ALTER TABLE transactions ADD COLUMN chat_id INTEGER")
    conn.commit()
    conn.close()

# --- ផ្នែក GOOGLE SHEETS FUNCTION ---
def get_google_client():
    if not HAS_GSHEET_LIB: return None
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    try:
        json_creds = os.environ.get("GOOGLE_CREDENTIALS_JSON")
        if json_creds:
            creds_dict = json.loads(json_creds)
            if 'private_key' in creds_dict:
                creds_dict['private_key'] = creds_dict['private_key'].replace('\\n', '\n')
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        elif os.path.exists(CREDENTIALS_FILE):
            creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, scope)
        else:
            return None
        return gspread.authorize(creds)
    except Exception as e:
        logging.error(f"GSheet Auth Error: {e}")
        return None

def log_to_google_sheet(chat_id, amount, currency, date_str, raw_message):
    client = get_google_client()
    if not client: return
    try:
        sheet = client.open(GOOGLE_SHEET_NAME).sheet1
        dt_obj = datetime.strptime(date_str, "%d-%b-%Y %I:%M%p")
        row = [dt_obj.strftime("%Y-%m-%d"), dt_obj.strftime("%H:%M:%S"), amount, currency, str(chat_id), raw_message]
        sheet.append_row(row)
        logging.info(f"✅ Logged to Google Sheet: {amount} {currency}")
    except Exception as e:
        logging.error(f"❌ Google Sheet Error: {e}")

# --- មុខងារ RESTORE & SAVE ---
def sync_from_google_sheet():
    client = get_google_client()
    if not client: return 0, "Auth Failed"
    try:
        sheet = client.open(GOOGLE_SHEET_NAME).sheet1
        rows = sheet.get_all_values()
        conn = sqlite3.connect('transactions.db')
        c = conn.cursor()
        count = 0
        start_index = 1 if len(rows) > 0 and rows[0][0] == 'Date' else 0
        for row in rows[start_index:]:
            if len(row) < 6: continue
            try:
                dt_str = f"{row[0]} {row[1]}"
                dt_obj = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
                amount = float(row[2]); currency = row[3]; chat_id = int(row[4]); raw_message = row[5]
                c.execute("SELECT id FROM transactions WHERE chat_id=? AND transaction_date=? AND amount=?", (chat_id, dt_obj, amount))
                if c.fetchone(): continue
                c.execute("INSERT INTO transactions (chat_id, amount, currency, transaction_date, raw_message) VALUES (?, ?, ?, ?, ?)", (chat_id, amount, currency, dt_obj, raw_message))
                count += 1
            except Exception: continue
        conn.commit(); conn.close()
        return count, "ជោគជ័យ"
    except Exception as e: return 0, str(e)

def auto_restore_if_empty():
    try:
        conn = sqlite3.connect('transactions.db'); c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM transactions"); count = c.fetchone()[0]; conn.close()
        if count == 0:
            logging.info("⚠️ Database empty. Auto-Restoring..."); sync_from_google_sheet()
    except Exception: pass

def save_transaction(chat_id, amount, currency, date_str, raw_message):
    try:
        dt_obj = datetime.strptime(date_str, "%d-%b-%Y %I:%M%p")
        conn = sqlite3.connect('transactions.db'); c = conn.cursor()
        c.execute("INSERT INTO transactions (chat_id, amount, currency, transaction_date, raw_message) VALUES (?, ?, ?, ?, ?)", (chat_id, amount, currency, dt_obj, raw_message))
        conn.commit(); conn.close()
        log_to_google_sheet(chat_id, amount, currency, date_str, raw_message)
        return True
    except Exception as e: logging.error(f"Error saving: {e}"); return False

# --- QUERY & FORMAT FUNCTIONS ---
def get_sum_by_exact_range(chat_id, start_dt, end_dt):
    conn = sqlite3.connect('transactions.db'); c = conn.cursor()
    c.execute("SELECT currency, SUM(amount), COUNT(*) FROM transactions WHERE chat_id = ? AND transaction_date BETWEEN ? AND ? GROUP BY currency", (chat_id, start_dt, end_dt))
    rows = c.fetchall(); conn.close()
    sums = {'USD': 0.0, 'KHR': 0.0}; total_count = 0
    for row in rows:
        if row[0] in sums: sums[row[0]] = row[1]
        total_count += row[2]
    return sums, total_count

def format_amount_text(totals):
    lines = []
    has_usd = totals['USD'] > 0; has_khr = totals['KHR'] > 0
    if has_usd or (not has_usd and not has_khr): lines.append(f"💵 **{totals['USD']:,.2f} USD**")
    if has_khr: lines.append(f"💴 **{totals['KHR']:,.2f} KHR**")
    return "\n".join(lines)

# --- DYNAMIC BUTTONS ---
def get_available_years(chat_id):
    conn = sqlite3.connect('transactions.db'); c = conn.cursor()
    c.execute("SELECT DISTINCT strftime('%Y', transaction_date) FROM transactions WHERE chat_id = ? ORDER BY 1", (chat_id,))
    years = [row[0] for row in c.fetchall()]; conn.close(); return years
# (Functions for Month/Day/Time are similar, abbreviated to save space but logic remains same as previous file)
def get_available_months(chat_id, y): 
    conn=sqlite3.connect('transactions.db');c=conn.cursor();c.execute("SELECT DISTINCT strftime('%m', transaction_date) FROM transactions WHERE chat_id=? AND strftime('%Y', transaction_date)=? ORDER BY 1",(chat_id,y));r=[x[0] for x in c.fetchall()];conn.close();return r
def get_available_days(chat_id, y, m): 
    conn=sqlite3.connect('transactions.db');c=conn.cursor();c.execute("SELECT DISTINCT strftime('%d', transaction_date) FROM transactions WHERE chat_id=? AND strftime('%Y', transaction_date)=? AND strftime('%m', transaction_date)=? ORDER BY 1",(chat_id,y,m));r=[x[0] for x in c.fetchall()];conn.close();return r
def get_available_hours(chat_id, d): 
    conn=sqlite3.connect('transactions.db');c=conn.cursor();c.execute("SELECT DISTINCT strftime('%H', transaction_date) FROM transactions WHERE chat_id=? AND date(transaction_date)=? ORDER BY 1",(chat_id,d));r=[x[0] for x in c.fetchall()];conn.close();return r
def get_available_minutes(chat_id, d, h): 
    conn=sqlite3.connect('transactions.db');c=conn.cursor();c.execute("SELECT DISTINCT strftime('%M', transaction_date) FROM transactions WHERE chat_id=? AND date(transaction_date)=? AND strftime('%H', transaction_date)=? ORDER BY 1",(chat_id,d,h));r=[x[0] for x in c.fetchall()];conn.close();return r

def parse_message(text):
    pattern = r"Received\W+([\d\.,]+)\s*(USD|KHR).*?on\W+(\d{2}-[A-Za-z]{3}-\d{4}\s+\d{2}:\d{2}[AP]M)"
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        return float(match.group(1).replace(',', '')), match.group(2).upper(), match.group(3)
    return None

# --- HELPER: ADD DELETE BUTTON ---
def get_keyboard_with_delete(buttons=None):
    """បន្ថែមប៊ូតុងលុបទៅគ្រប់ Keyboard"""
    if buttons is None: buttons = []
    # បន្ថែមប៊ូតុងលុបនៅខាងក្រោមគេ
    buttons.append([InlineKeyboardButton("🗑️ បិទ (Close)", callback_data='delete_msg')])
    return InlineKeyboardMarkup(buttons)

# --- HANDLERS ---

async def track_chat_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    ចាប់យក Group ID ស្វ័យប្រវត្តិនៅពេល Bot ត្រូវបាន Add ចូល Group
    ទោះបីជាមិនទាន់ជា Admin ក៏ដោយ។
    """
    result = update.my_chat_member
    if not result: return

    chat = result.chat
    new_status = result.new_chat_member.status
    
    # Bot ត្រូវបាន Add ចូល ឬ Promote
    if new_status in ['member', 'administrator']:
        logging.info(f"🤖 Bot joined/promoted in chat: {chat.title} (ID: {chat.id})")
        # អាច Save chat_id ទុកក្នុង DB ប្រសិនបើត្រូវការប្រើពេលក្រោយ
        # ប៉ុន្តែសម្រាប់ពេលនេះ គ្រាន់តែ Log គឺគ្រប់គ្រាន់ដើម្បីដឹង ID

async def delete_msg_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """លុបសារនៅពេលចុចប៊ូតុង 🗑️"""
    query = update.callback_query
    await query.answer()
    try:
        await query.message.delete()
    except Exception as e:
        logging.error(f"Failed to delete message: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("☀️ បូកសរុបថ្ងៃនេះ (Today)", callback_data='sum_today')],
        [InlineKeyboardButton("🗓️ បូកសរុបខែនេះ (This Month)", callback_data='sum_month')],
        [InlineKeyboardButton("🔍 ស្វែងរកលម្អិត (Custom Search)", callback_data='nav_year')],
        [InlineKeyboardButton("❓ របៀបប្រើប្រាស់", callback_data='help')]
    ]
    reply_markup = get_keyboard_with_delete(keyboard)
    
    chat_title = update.effective_chat.title or "Chat នេះ"
    welcome_text = (
        f"សួស្តី! ស្វាគមន៍មកកាន់ **DMK Magic System**! 🤖✨\n\n"
        f"ខ្ញុំគឺជាជំនួយការឆ្លាតវៃសម្រាប់កត់ត្រា និងបូកសរុបចំណូល (USD & KHR) ក្នុង **{chat_title}** ដោយស្វ័យប្រវត្តិ។ 💸\n\n"
        "💡 ប្រសិនបើមានចម្ងល់ និងបញ្ហាសូមទាក់ទងទៅកាន់ **@OUDOM333**\n\n"

        "សូមជ្រើសរើសប្រតិបត្តិការខាងក្រោម 👇\n\n"
        
    )
    
    if update.message: 
        await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode='Markdown')
    elif update.callback_query: 
        await update.callback_query.edit_message_text(welcome_text, reply_markup=reply_markup, parse_mode='Markdown')

async def restore_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ កំពុងទាញទិន្នន័យពី Google Sheet...")
    count, msg = sync_from_google_sheet()
    
    # បង្កើតប៊ូតុងលុបសម្រាប់សារ Restore
    reply_markup = get_keyboard_with_delete()
    
    if count > 0:
        await update.message.reply_text(f"✅ **Restore ជោគជ័យ!**\nបានទាញយក **{count}** ប្រតិបត្តិការ។", reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await update.message.reply_text(f"⚠️ **Restore បរាជ័យ**\n{msg}", reply_markup=reply_markup, parse_mode='Markdown')

async def handle_incoming_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    chat_id = update.effective_chat.id
    if not text: return
    parsed = parse_message(text)
    if parsed:
        amount, currency, date_str = parsed
        if save_transaction(chat_id, amount, currency, date_str, text):
            print(f"✅ [{chat_id}] Saved: {amount} {currency}")
            # ឆ្លើយតបថាបាន Save ជោគជ័យ ជាមួយប៊ូតុងលុប
            reply_markup = get_keyboard_with_delete()
            await update.message.reply_text(
                f"✅ **កត់ត្រាទុក!**\n💰 `{amount:,.2f} {currency}`\n📅 `{date_str}`",
                parse_mode='Markdown',
                reply_markup=reply_markup
            )

async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = update.effective_chat.id
    # មិនបាច់ answer() ត្រង់នេះទេ ព្រោះនឹង edit ខាងក្រោម
    data = query.data.split(':'); action = data[0]; now = datetime.now()

    if action == 'delete_msg':
        await delete_msg_callback(update, context)
        return
    
    await query.answer()

    back_btn = [InlineKeyboardButton("🔙 ត្រឡប់ក្រោយ", callback_data='back_main')]

    if action == 'sum_today':
        start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = now.replace(hour=23, minute=59, second=59, microsecond=999999)
        totals, count = get_sum_by_exact_range(chat_id, start_dt, end_dt)
        msg = f"☀️ **បូកសរុបថ្ងៃនេះ ({start_dt.strftime('%d-%b-%Y')})**\n\n{format_amount_text(totals)}\n\n📝 ចំនួនប្រតិបត្តិការ: `{count}`"
        await query.edit_message_text(msg, reply_markup=get_keyboard_with_delete([back_btn]), parse_mode='Markdown')
    
    elif action == 'sum_month':
        start_dt = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end_dt = now
        totals, count = get_sum_by_exact_range(chat_id, start_dt, end_dt)
        msg = f"🗓️ **បូកសរុបខែនេះ ({start_dt.strftime('%B-%Y')})**\n\n{format_amount_text(totals)}\n\n📝 ចំនួនប្រតិបត្តិការ: `{count}`"
        await query.edit_message_text(msg, reply_markup=get_keyboard_with_delete([back_btn]), parse_mode='Markdown')

    elif action == 'nav_year':
        years = get_available_years(chat_id)
        if not years:
            await query.edit_message_text("❌ **មិនទាន់មានទិន្នន័យ។**", parse_mode='Markdown', reply_markup=get_keyboard_with_delete([back_btn]))
            return
        buttons = [[InlineKeyboardButton(f"ឆ្នាំ {y}", callback_data=f"nav_month:{y}")] for y in years]
        buttons.append(back_btn)
        await query.edit_message_text("📅 **សូមជ្រើសរើសឆ្នាំ:**", reply_markup=get_keyboard_with_delete(buttons), parse_mode='Markdown')

    elif action == 'nav_month':
        year = data[1]; months = get_available_months(chat_id, year)
        month_names = {"01":"Jan","02":"Feb","03":"Mar","04":"Apr","05":"May","06":"Jun","07":"Jul","08":"Aug","09":"Sep","10":"Oct","11":"Nov","12":"Dec"}
        buttons = []; row = []
        for m in months:
            m_name = month_names.get(m, m)
            row.append(InlineKeyboardButton(f"{m_name}", callback_data=f"nav_day:{year}:{m}"))
            if len(row)==3: buttons.append(row); row=[]
        if row: buttons.append(row)
        buttons.append([InlineKeyboardButton("🔙 ត្រឡប់ក្រោយ", callback_data='nav_year')])
        await query.edit_message_text(f"🗓️ **ឆ្នាំ {year} - សូមជ្រើសរើសខែ:**", reply_markup=get_keyboard_with_delete(buttons), parse_mode='Markdown')

    elif action == 'nav_day':
        year, month = data[1], data[2]; days = get_available_days(chat_id, year, month)
        buttons = []; row = []
        for d in days:
            row.append(InlineKeyboardButton(f"{d}", callback_data=f"nav_sh:{year}:{month}:{d}"))
            if len(row)==5: buttons.append(row); row=[]
        if row: buttons.append(row)
        buttons.append([InlineKeyboardButton("🔙 ត្រឡប់ក្រោយ", callback_data=f"nav_month:{year}")])
        await query.edit_message_text(f"📅 **ខែ {month}/{year} - សូមជ្រើសរើសថ្ងៃ:**", reply_markup=get_keyboard_with_delete(buttons), parse_mode='Markdown')
    
    # (កាត់ខ្លីផ្នែក Hour/Min ដើម្បីសន្សំ Space តែ Logic នៅដដែល និងប្រើ get_keyboard_with_delete ទាំងអស់)
    elif action == 'nav_sh':
        year, month, day = data[1], data[2], data[3]; hours = get_available_hours(chat_id, f"{year}-{month}-{day}")
        buttons = []; row = []
        for h in hours:
            row.append(InlineKeyboardButton(f"{h}:XX", callback_data=f"nav_sm:{year}:{month}:{day}:{h}")); 
            if len(row)==4: buttons.append(row); row=[]
        if row: buttons.append(row)
        buttons.append([InlineKeyboardButton("🔙 ត្រឡប់ក្រោយ", callback_data=f"nav_day:{year}:{month}")])
        await query.edit_message_text(f"⏰ **{day}/{month}/{year}**\nសូមជ្រើសរើស **ម៉ោងចាប់ផ្ដើម**:", reply_markup=get_keyboard_with_delete(buttons), parse_mode='Markdown')
    elif action == 'nav_sm':
        year, month, day, h_start = data[1], data[2], data[3], data[4]; mins = get_available_minutes(chat_id, f"{year}-{month}-{day}", h_start)
        buttons = []; row = []
        for m in mins:
            row.append(InlineKeyboardButton(f":{m}", callback_data=f"nav_eh:{year}:{month}:{day}:{h_start}:{m}")); 
            if len(row)==4: buttons.append(row); row=[]
        if row: buttons.append(row)
        buttons.append([InlineKeyboardButton("🔙 ត្រឡប់ក្រោយ", callback_data=f"nav_sh:{year}:{month}:{day}")])
        await query.edit_message_text(f"⏰ **ម៉ោង {h_start}:XX**\nសូមជ្រើសរើស **នាទីចាប់ផ្ដើម**:", reply_markup=get_keyboard_with_delete(buttons), parse_mode='Markdown')
    elif action == 'nav_eh':
        year, month, day, h_start, m_start = data[1], data[2], data[3], data[4], data[5]; all_hours = get_available_hours(chat_id, f"{year}-{month}-{day}")
        buttons = []; row = []
        for h in all_hours:
            if int(h)>=int(h_start): row.append(InlineKeyboardButton(f"{h}:XX", callback_data=f"nav_em:{year}:{month}:{day}:{h_start}:{m_start}:{h}")); 
            if len(row)==4: buttons.append(row); row=[]
        if row: buttons.append(row)
        buttons.append([InlineKeyboardButton("🔙 ត្រឡប់ក្រោយ", callback_data=f"nav_sm:{year}:{month}:{day}:{h_start}")])
        await query.edit_message_text(f"🏁 **ចាប់ផ្ដើមពី {h_start}:{m_start}**\nសូមជ្រើសរើស **ម៉ោងបញ្ចប់**:", reply_markup=get_keyboard_with_delete(buttons), parse_mode='Markdown')
    elif action == 'nav_em':
        year, month, day, h_start, m_start, h_end = data[1:]
        all_mins = get_available_minutes(chat_id, f"{year}-{month}-{day}", h_end); buttons = []
        buttons.append([InlineKeyboardButton("⚡ គិតត្រឹមពេលនេះ (Now)", callback_data=f"calc_now:{year}:{month}:{day}:{h_start}:{m_start}")])
        row = []
        for m in all_mins:
            if h_start==h_end and int(m)<int(m_start): continue
            row.append(InlineKeyboardButton(f":{m}", callback_data=f"calc:{year}:{month}:{day}:{h_start}:{m_start}:{h_end}:{m}"))
            if len(row)==4: buttons.append(row); row=[]
        if row: buttons.append(row)
        buttons.append([InlineKeyboardButton("🔙 ត្រឡប់ក្រោយ", callback_data=f"nav_eh:{year}:{month}:{day}:{h_start}:{m_start}")])
        await query.edit_message_text(f"🏁 **ដល់ម៉ោង {h_end}:XX**\nសូមជ្រើសរើស **នាទីបញ្ចប់**:", reply_markup=get_keyboard_with_delete(buttons), parse_mode='Markdown')

    elif action == 'calc' or action == 'calc_now':
        year, month, day, h_start, m_start = data[1], data[2], data[3], data[4], data[5]
        start_dt = datetime.strptime(f"{year}-{month}-{day} {h_start}:{m_start}", "%Y-%m-%d %H:%M")
        if action == 'calc_now':
            temp_now = datetime.now()
            end_dt = temp_now if temp_now.strftime("%Y-%m-%d") == f"{year}-{month}-{day}" else datetime.strptime(f"{year}-{month}-{day} 23:59", "%Y-%m-%d %H:%M")
            end_label = "បច្ចុប្បន្ន"
        else:
            h_end, m_end = data[6], data[7]
            end_dt = datetime.strptime(f"{year}-{month}-{day} {h_end}:{m_end}", "%Y-%m-%d %H:%M")
            end_label = f"{h_end}:{m_end}"
        
        totals, count = get_sum_by_exact_range(chat_id, start_dt, end_dt)
        msg = f"🔍 **លទ្ធផលស្វែងរក ({day}-{month}-{year})**\n🕒 ចាប់ពី: `{h_start}:{m_start}` ដល់ `{end_label}`\n-----------------------------\n{format_amount_text(totals)}\n\n📝 ចំនួនប្រតិបត្តិការ: `{count}`"
        
        nav_btns = [[InlineKeyboardButton("🔄 គណនាម្តងទៀត", callback_data='nav_year'), InlineKeyboardButton("🏠 ម៉ឺនុយដើម", callback_data='back_main')]]
        await query.edit_message_text(msg, reply_markup=get_keyboard_with_delete(nav_btns), parse_mode='Markdown')

    elif action == 'back_main': await start(update, context)
    elif action == 'help':
        help_text = (
            "📖 **DMK Magic System**\n\n"
            "🗑️ **ប៊ូតុងលុប:** គ្រប់សារទាំងអស់ឥឡូវនេះអាចលុបបានដោយចុច 'បិទ (Close)'។\n"
            "🤖 **Group ID:** Bot នឹងស្គាល់ ID ដោយស្វ័យប្រវត្តិពេលចូល Group។\n"
            "📥 **Auto-Restore:** ទិន្នន័យត្រូវបានការពារមិនអោយបាត់។\n\n"
            "📞 ជំនួយ: **@OUDOM333**"
        )
        await query.edit_message_text(help_text, reply_markup=get_keyboard_with_delete([back_btn]), parse_mode='Markdown')

if __name__ == '__main__':
    init_db()
    keep_alive()
    auto_restore_if_empty()
    print("Bot started on Render (Hybrid + AutoID + DeleteBtn)...")
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Handlers
    application.add_handler(ChatMemberHandler(track_chat_status, ChatMemberHandler.MY_CHAT_MEMBER)) # សម្រាប់ចាប់ Group ID ថ្មី
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('restore', restore_command))
    application.add_handler(CallbackQueryHandler(button_click))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_incoming_message))
    
    application.run_polling()
