import logging
import re
# ... (Code ទាំងអស់ខាងលើគឺដូចដើម) ...
import firebase_admin
from firebase_admin import credentials, firestore

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
# ...
# ... (Code ទាំងអស់ខាងលើគឺដូចដើម) ...
# ...
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

    # 3. Conversation Handler (*** បានកែសម្រួលនៅទីនេះ ***)
    conv_handler = ConversationHandler(
        entry_points=[
            # Conversation ឥឡូវនេះចាប់ផ្តើមតែនៅពេលមានការចុចប៊ូតុងប៉ុណ្ណោះ
            CallbackQueryHandler(handle_button_press) 
        ],
        states={
            GET_DAY: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_get_day)],
            GET_MONTH: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_get_month)],
            GET_CUSTOM_START: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_custom_start)],
            GET_CUSTOM_END: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_custom_end)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
    )

    # 4. បន្ថែម Handlers (*** បានកែសម្រួលនៅទីនេះ ***)
    
    # /start និង /sum ឥឡូវនេះ ទាំងពីរ ហៅ 'start_command' (ដើម្បីបង្ហាញ menu)
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("sum", start_command)) # <-- ជួសជុល Error
    
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
