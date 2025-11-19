import logging
import re
import sqlite3
import os
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters, CallbackQueryHandler

# --- ផ្នែក GOOGLE SHEETS LIBRARIES ---
try:
    import gspread
    from oauth2client.service_account import ServiceAccountCredentials
    HAS_GSHEET_LIB = True
except ImportError:
    HAS_GSHEET_LIB = False
    print("⚠️ មិនមាន Library 'gspread' ទេ។")

# --- ការកំណត់ (CONFIGURATION) ---
# នៅលើ Render យើងគួរប្រើ Environment Variable សម្រាប់ Token (សុវត្ថិភាព)
# ប៉ុន្តែដាក់ផ្ទាល់ក៏បានសម្រាប់អ្នកចាប់ផ្តើម
BOT_TOKEN = "8251257361:AAGz_QLrrskQYD6hGoNhjak4KZAKUaqwZCw" 
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
    if not HAS_GSHEET_LIB or not os.path.exists(CREDENTIALS_FILE):
        return None
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, scope)
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
        row = [
            dt_obj.strftime("%Y-%m-%d"), 
            dt_obj.strftime("%H:%M:%S"), 
            amount, 
            currency, 
            str(chat_id), 
            raw_message
        ]
        sheet.append_row(row)
        logging.info(f"✅ Logged to Google Sheet: {amount} {currency}")
    except Exception as e:
        logging.error(f"❌ Google Sheet Error: {e}")

# --- មុខងារ RESTORE (Manual & Auto) ---
def sync_from_google_sheet():
    client = get_google_client()
    if not client: return 0, "រកមិនឃើញ credentials.json ឬ Library បញ្ហា"

    try:
        sheet = client.open(GOOGLE_SHEET_NAME).sheet1
        rows = sheet.get_all_values()
        conn = sqlite3.connect('transactions.db')
        c = conn.cursor()
        
        count = 0
        # រំលង Header (ជួរទី 1) បើមាន
        start_index = 1 if len(rows) > 0 and rows[0][0] == 'Date' else 0

        for row in rows[start_index:]:
            if len(row) < 6: continue
            try:
                # [0:Date, 1:Time, 2:Amount, 3:Currency, 4:ChatID, 5:Message]
                dt_str = f"{row[0]} {row[1]}"
                dt_obj = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
                amount = float(row[2])
                currency = row[3]
                chat_id = int(row[4])
                raw_message = row[5]
                
                # Check ស្ទួន
                c.execute("SELECT id FROM transactions WHERE chat_id=? AND transaction_date=? AND amount=?", 
                          (chat_id, dt_obj, amount))
                if c.fetchone(): continue 
                
                c.execute("INSERT INTO transactions (chat_id, amount, currency, transaction_date, raw_message) VALUES (?, ?, ?, ?, ?)",
                          (chat_id, amount, currency, dt_obj, raw_message))
                count += 1
            except Exception: continue

        conn.commit()
        conn.close()
        return count, "ជោគជ័យ"
    except Exception as e:
        return 0, str(e)

def auto_restore_if_empty():
    """
    មុខងារពិសេសសម្រាប់ Render:
    ពិនិត្យមើលថាបើ DB ទទេ (ទើប Restart) ឱ្យ Restore ស្វ័យប្រវត្តិ
    """
    try:
        conn = sqlite3.connect('transactions.db')
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM transactions")
        count = c.fetchone()[0]
        conn.close()

        if count == 0:
            logging.info("⚠️ Database is empty (Possible Render Restart). Starting Auto-Restore...")
            restored_count, msg = sync_from_google_sheet()
            logging.info(f"✅ Auto-Restore complete: {restored_count} transactions recovered. ({msg})")
        else:
            logging.info(f"ℹ️ Database initialized with {count} existing transactions.")
            
    except Exception as e:
        logging.error(f"Auto-restore check failed: {e}")

# --- SAVE TRANSACTION ---
def save_transaction(chat_id, amount, currency, date_str, raw_message):
    try:
        dt_obj = datetime.strptime(date_str, "%d-%b-%Y %I:%M%p")
        conn = sqlite3.connect('transactions.db')
        c = conn.cursor()
        c.execute("INSERT INTO transactions (chat_id, amount, currency, transaction_date, raw_message) VALUES (?, ?, ?, ?, ?)",
                  (chat_id, amount, currency, dt_obj, raw_message))
        conn.commit()
        conn.close()
        
        # Save ទៅ Google Sheet ផងដែរ
        log_to_google_sheet(chat_id, amount, currency, date_str, raw_message)
        return True
    except Exception as e:
        logging.error(f"Error saving: {e}")
        return False

# --- QUERY FUNCTIONS ---
def get_available_years(chat_id):
    conn = sqlite3.connect('transactions.db'); c = conn.cursor()
    c.execute("SELECT DISTINCT strftime('%Y', transaction_date) FROM transactions WHERE chat_id = ? ORDER BY 1", (chat_id,))
    years = [row[0] for row in c.fetchall()]; conn.close(); return years

def get_available_months(chat_id, year):
    conn = sqlite3.connect('transactions.db'); c = conn.cursor()
    c.execute("SELECT DISTINCT strftime('%m', transaction_date) FROM transactions WHERE chat_id = ? AND strftime('%Y', transaction_date) = ? ORDER BY 1", (chat_id, year))
    months = [row[0] for row in c.fetchall()]; conn.close(); return months

def get_available_days(chat_id, year, month):
    conn = sqlite3.connect('transactions.db'); c = conn.cursor()
    c.execute("SELECT DISTINCT strftime('%d', transaction_date) FROM transactions WHERE chat_id = ? AND strftime('%Y', transaction_date) = ? AND strftime('%m', transaction_date) = ? ORDER BY 1", (chat_id, year, month))
    days = [row[0] for row in c.fetchall()]; conn.close(); return days

def get_available_hours(chat_id, date_str):
    conn = sqlite3.connect('transactions.db'); c = conn.cursor()
    c.execute("SELECT DISTINCT strftime('%H', transaction_date) FROM transactions WHERE chat_id = ? AND date(transaction_date) = ? ORDER BY 1", (chat_id, date_str))
    hours = [row[0] for row in c.fetchall()]; conn.close(); return hours

def get_available_minutes(chat_id, date_str, hour):
    conn = sqlite3.connect('transactions.db'); c = conn.cursor()
    c.execute("SELECT DISTINCT strftime('%M', transaction_date) FROM transactions WHERE chat_id = ? AND date(transaction_date) = ? AND strftime('%H', transaction_date) = ? ORDER BY 1", (chat_id, date_str, hour))
    minutes = [row[0] for row in c.fetchall()]; conn.close(); return minutes

def get_sum_by_exact_range(chat_id, start_dt, end_dt):
    conn = sqlite3.connect('transactions.db'); c = conn.cursor()
    c.execute('''
        SELECT currency, SUM(amount), COUNT(*) FROM transactions 
        WHERE chat_id = ? AND transaction_date BETWEEN ? AND ?
        GROUP BY currency
    ''', (chat_id, start_dt, end_dt))
    rows = c.fetchall(); conn.close()
    sums = {'USD': 0.0, 'KHR': 0.0}; total_count = 0
    for row in rows:
        currency = row[0]; amount = row[1]; count = row[2]
        if currency in sums: sums[currency] = amount
        total_count += count
    return sums, total_count

def format_amount_text(totals):
    lines = []
    has_usd = totals['USD'] > 0; has_khr = totals['KHR'] > 0
    if has_usd or (not has_usd and not has_khr): lines.append(f"💵 **{totals['USD']:,.2f} USD**")
    if has_khr: lines.append(f"💴 **{totals['KHR']:,.2f} KHR**")
    return "\n".join(lines)

def parse_message(text):
    pattern = r"Received\W+([\d\.,]+)\s*(USD|KHR).*?on\W+(\d{2}-[A-Za-z]{3}-\d{4}\s+\d{2}:\d{2}[AP]M)"
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        amount_str = match.group(1).replace(',', '')
        return float(amount_str), match.group(2).upper(), match.group(3)
    return None

# --- HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("☀️ បូកសរុបថ្ងៃនេះ (Today)", callback_data='sum_today')],
        [InlineKeyboardButton("🗓️ បូកសរុបខែនេះ (This Month)", callback_data='sum_month')],
        [InlineKeyboardButton("🔍 ស្វែងរកលម្អិត (Custom Search)", callback_data='nav_year')],
        [InlineKeyboardButton("❓ របៀបប្រើប្រាស់", callback_data='help')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    chat_title = update.effective_chat.title or "Chat នេះ"
    welcome_text = (
        f"សួស្តី! ស្វាគមន៍មកកាន់ **DMK Magic System**! 🤖✨\n\n"
        f"ខ្ញុំដំណើរការលើ **Render (Cloud)** ☁️\n"
        f"ប្រើប្រាស់ **Database Hybrid (Auto-Restore)** សម្រាប់ **{chat_title}**។\n\n"
        "សូមជ្រើសរើសប្រតិបត្តិការខាងក្រោម 👇\n\n"
        "💡 ជំនួយ: **@OUDOM333**"
    )
    if update.message: await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode='Markdown')
    elif update.callback_query: await update.callback_query.edit_message_text(welcome_text, reply_markup=reply_markup, parse_mode='Markdown')

async def restore_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ បង្ខំអោយទាញទិន្នន័យពី Google Sheet (Manual Force)...")
    count, msg = sync_from_google_sheet()
    if count > 0:
        await update.message.reply_text(f"✅ **Restore ជោគជ័យ!**\nបានទាញយក **{count}** ប្រតិបត្តិការមកវិញ។", parse_mode='Markdown')
    else:
        await update.message.reply_text(f"⚠️ **Restore បរាជ័យ ឬគ្មានទិន្នន័យថ្មី**\n{msg}", parse_mode='Markdown')

async def handle_incoming_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    chat_id = update.effective_chat.id
    if not text: return
    parsed = parse_message(text)
    if parsed:
        amount, currency, date_str = parsed
        if save_transaction(chat_id, amount, currency, date_str, text):
            print(f"✅ [{chat_id}] Saved to DB & GSheet: {amount} {currency}")

async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = update.effective_chat.id
    await query.answer()
    data = query.data.split(':'); action = data[0]; now = datetime.now()

    if action == 'sum_today':
        start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = now.replace(hour=23, minute=59, second=59, microsecond=999999)
        totals, count = get_sum_by_exact_range(chat_id, start_dt, end_dt)
        msg = f"☀️ **បូកសរុបថ្ងៃនេះ ({start_dt.strftime('%d-%b-%Y')})**\n\n{format_amount_text(totals)}\n\n📝 ចំនួនប្រតិបត្តិការ: `{count}`"
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 ត្រឡប់ក្រោយ", callback_data='back_main')]]), parse_mode='Markdown')
    
    elif action == 'sum_month':
        start_dt = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end_dt = now
        totals, count = get_sum_by_exact_range(chat_id, start_dt, end_dt)
        msg = f"🗓️ **បូកសរុបខែនេះ ({start_dt.strftime('%B-%Y')})**\n\n{format_amount_text(totals)}\n\n📝 ចំនួនប្រតិបត្តិការ: `{count}`"
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 ត្រឡប់ក្រោយ", callback_data='back_main')]]), parse_mode='Markdown')

    elif action == 'nav_year':
        years = get_available_years(chat_id)
        if not years:
            await query.edit_message_text("❌ **មិនទាន់មានទិន្នន័យ។**\n(Auto-restore ប្រហែលជាកំពុងដំណើរការ ឬ GSheet ទទេ)", parse_mode='Markdown', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 ត្រឡប់ក្រោយ", callback_data='back_main')]]))
            return
        buttons = [[InlineKeyboardButton(f"ឆ្នាំ {y}", callback_data=f"nav_month:{y}")] for y in years]
        buttons.append([InlineKeyboardButton("🔙 ត្រឡប់ក្រោយ", callback_data='back_main')])
        await query.edit_message_text("📅 **សូមជ្រើសរើសឆ្នាំ:**", reply_markup=InlineKeyboardMarkup(buttons), parse_mode='Markdown')

    # ... (កូដ Month/Day/Hour/Min ដូចគ្នា) ...
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
        await query.edit_message_text(f"🗓️ **ឆ្នាំ {year} - សូមជ្រើសរើសខែ:**", reply_markup=InlineKeyboardMarkup(buttons), parse_mode='Markdown')

    elif action == 'nav_day':
        year, month = data[1], data[2]; days = get_available_days(chat_id, year, month)
        buttons = []; row = []
        for d in days:
            row.append(InlineKeyboardButton(f"{d}", callback_data=f"nav_sh:{year}:{month}:{d}"))
            if len(row)==5: buttons.append(row); row=[]
        if row: buttons.append(row)
        buttons.append([InlineKeyboardButton("🔙 ត្រឡប់ក្រោយ", callback_data=f"nav_month:{year}")])
        await query.edit_message_text(f"📅 **ខែ {month}/{year} - សូមជ្រើសរើសថ្ងៃ:**", reply_markup=InlineKeyboardMarkup(buttons), parse_mode='Markdown')

    elif action == 'nav_sh':
        year, month, day = data[1], data[2], data[3]
        hours = get_available_hours(chat_id, f"{year}-{month}-{day}")
        buttons = []; row = []
        for h in hours:
            row.append(InlineKeyboardButton(f"{h}:XX", callback_data=f"nav_sm:{year}:{month}:{day}:{h}"))
            if len(row)==4: buttons.append(row); row=[]
        if row: buttons.append(row)
        buttons.append([InlineKeyboardButton("🔙 ត្រឡប់ក្រោយ", callback_data=f"nav_day:{year}:{month}")])
        await query.edit_message_text(f"⏰ **{day}/{month}/{year}**\nសូមជ្រើសរើស **ម៉ោងចាប់ផ្ដើម**:", reply_markup=InlineKeyboardMarkup(buttons), parse_mode='Markdown')

    elif action == 'nav_sm':
        year, month, day, h_start = data[1], data[2], data[3], data[4]
        mins = get_available_minutes(chat_id, f"{year}-{month}-{day}", h_start)
        buttons = []; row = []
        for m in mins:
            row.append(InlineKeyboardButton(f":{m}", callback_data=f"nav_eh:{year}:{month}:{day}:{h_start}:{m}"))
            if len(row)==4: buttons.append(row); row=[]
        if row: buttons.append(row)
        buttons.append([InlineKeyboardButton("🔙 ត្រឡប់ក្រោយ", callback_data=f"nav_sh:{year}:{month}:{day}")])
        await query.edit_message_text(f"⏰ **ម៉ោង {h_start}:XX**\nសូមជ្រើសរើស **នាទីចាប់ផ្ដើម**:", reply_markup=InlineKeyboardMarkup(buttons), parse_mode='Markdown')

    elif action == 'nav_eh':
        year, month, day, h_start, m_start = data[1], data[2], data[3], data[4], data[5]
        all_hours = get_available_hours(chat_id, f"{year}-{month}-{day}")
        buttons = []; row = []
        for h in all_hours:
            if int(h) >= int(h_start):
                row.append(InlineKeyboardButton(f"{h}:XX", callback_data=f"nav_em:{year}:{month}:{day}:{h_start}:{m_start}:{h}"))
                if len(row)==4: buttons.append(row); row=[]
        if row: buttons.append(row)
        buttons.append([InlineKeyboardButton("🔙 ត្រឡប់ក្រោយ", callback_data=f"nav_sm:{year}:{month}:{day}:{h_start}")])
        await query.edit_message_text(f"🏁 **ចាប់ផ្ដើមពី {h_start}:{m_start}**\nសូមជ្រើសរើស **ម៉ោងបញ្ចប់**:", reply_markup=InlineKeyboardMarkup(buttons), parse_mode='Markdown')

    elif action == 'nav_em':
        year, month, day, h_start, m_start, h_end = data[1:]
        all_mins = get_available_minutes(chat_id, f"{year}-{month}-{day}", h_end)
        buttons = []
        buttons.append([InlineKeyboardButton("⚡ គិតត្រឹមពេលនេះ (Now)", callback_data=f"calc_now:{year}:{month}:{day}:{h_start}:{m_start}")])
        row = []
        for m in all_mins:
            if h_start == h_end and int(m) < int(m_start): continue
            row.append(InlineKeyboardButton(f":{m}", callback_data=f"calc:{year}:{month}:{day}:{h_start}:{m_start}:{h_end}:{m}"))
            if len(row)==4: buttons.append(row); row=[]
        if row: buttons.append(row)
        buttons.append([InlineKeyboardButton("🔙 ត្រឡប់ក្រោយ", callback_data=f"nav_eh:{year}:{month}:{day}:{h_start}:{m_start}")])
        await query.edit_message_text(f"🏁 **ដល់ម៉ោង {h_end}:XX**\nសូមជ្រើសរើស **នាទីបញ្ចប់**:", reply_markup=InlineKeyboardMarkup(buttons), parse_mode='Markdown')

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
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 គណនាម្តងទៀត", callback_data='nav_year'), InlineKeyboardButton("🏠 ម៉ឺនុយដើម", callback_data='back_main')]]), parse_mode='Markdown')

    elif action == 'back_main': await start(update, context)
    elif action == 'help':
        help_text = (
            "📖 **ការប្រើប្រាស់លើ Render**\n\n"
            "🤖 **ប្រភេទ Bot:** Hybrid (SQLite + Google Sheet)\n"
            "⚙️ **Auto-Restore:** បើកដំណើរការ។\n\n"
            "រាល់ពេល Render Restart ហើយលុប DB ចោល, Bot នឹងទាញទិន្នន័យពី Google Sheet មកវិញដោយស្វ័យប្រវត្តិ។\n\n"
            "📞 ជំនួយ: **@OUDOM333**"
        )
        await query.edit_message_text(help_text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 ត្រឡប់ក្រោយ", callback_data='back_main')]]), parse_mode='Markdown')

if __name__ == '__main__':
    init_db()
    # ហៅមុខងារពិនិត្យមើល និង Restore ដោយស្វ័យប្រវត្តិមុនពេល Start Bot
    auto_restore_if_empty()
    
    print("Bot started on Render (Hybrid Mode with Auto-Restore)...")
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('restore', restore_command))
    application.add_handler(CallbackQueryHandler(button_click))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_incoming_message))
    application.run_polling()
