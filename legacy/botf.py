import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.ext import CallbackContext, Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, PicklePersistence
from typing import Optional
# from dotnet import load_dotnet
from dotenv import load_dotenv
import os

# Replace with your bot token and channel ID
load_dotenv()

# Replace with your bot token and channel ID
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME")
VALID_API_KEYS = set(os.getenv("VALID_API_KEYS").split(","))
SMS_API_URL = os.getenv("SMS_API_URL")
SMS_API_FROM = os.getenv("SMS_API_FROM")
ADMIN_USER_ID = {int(user_id) for user_id in os.getenv("ADMIN_USER_ID").split(",")}
persistence = PicklePersistence(filepath="bot_data")
user_data = {}

# Start command with the necessary buttons
async def start(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    context.application.persistence.user_data[user_id] = {}
    join_button = InlineKeyboardButton("Join Channel", url=f"https://t.me/hackelite01")
    check_button = InlineKeyboardButton("Check Membership", callback_data='check_membership')
    
    # Create inline keyboard with buttons
    keyboard = InlineKeyboardMarkup([[join_button], [check_button]])
    
    # Send welcome GIF
    await context.bot.send_animation(
        chat_id=update.message.chat_id,
        animation="https://media1.tenor.com/m/2pB1ng_3qQsAAAAC/welcome.gif",  # Replace with your GIF URL
        caption="𝐖𝐞𝐥𝐜𝐨𝐦𝐞 𝐭𝐨 𝐭𝐡𝐞 𝐛𝐨𝐭 ! 🎉"
    )
    
    # Send welcome message with buttons
    await update.message.reply_text(
        "𝐖𝐞𝐥𝐜𝐨𝐦𝐞! 𝐏𝐥𝐞𝐚𝐬𝐞 𝐣𝐨𝐢𝐧 𝐭𝐡𝐞 𝐜𝐡𝐚𝐧𝐧𝐞𝐥 𝐭𝐨 𝐮𝐬𝐞 𝐭𝐡𝐞 𝐛𝐨𝐭.",
        reply_markup=keyboard
    )

async def check_user_membership(user_id):
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status in ['member', 'administrator', 'creator']
    except Exception:
        return False

async def check_membership(update: Update, context: CallbackContext):
    query = update.callback_query
    user_id = query.from_user.id

    is_member = await check_user_membership(user_id)

    if is_member:
        await query.message.reply_text("𝐘𝐨𝐮 𝐚𝐫𝐞 𝐚 𝐦𝐞𝐦𝐛𝐞𝐫 𝐨𝐟 𝐭𝐡𝐞 𝐜𝐡𝐚𝐧𝐧𝐞𝐥! 𝐏𝐥𝐞𝐚𝐬𝐞 𝐩𝐫𝐞𝐬𝐬 𝐭𝐡𝐞 𝐛𝐮𝐭𝐭𝐨𝐧 𝐛𝐞𝐥𝐨𝐰 𝐭𝐨 𝐞𝐧𝐭𝐞𝐫 𝐲𝐨𝐮𝐫 𝐒𝐞𝐜𝐫𝐞𝐭 𝐤𝐞𝐲.")
        enter_api_key_button = InlineKeyboardButton("Enter Secret Key", callback_data='enter_api_key')
        buy_key_button = InlineKeyboardButton("Buy Key", url="https://t.me/NonRootedInsaan")
        keyboard = InlineKeyboardMarkup([[enter_api_key_button], [buy_key_button]])
        await query.message.reply_text("𝐏𝐫𝐞𝐬𝐬 𝐭𝐡𝐞 𝐛𝐮𝐭𝐭𝐨𝐧 𝐭𝐨 𝐞𝐧𝐭𝐞𝐫 𝐲𝐨𝐮𝐫 𝐒𝐞𝐜𝐫𝐞𝐭 𝐤𝐞𝐲:", reply_markup=keyboard)
    else:
        await query.message.reply_text("𝐘𝐨𝐮 𝐚𝐫𝐞 𝐧𝐨𝐭 𝐚 𝐦𝐞𝐦𝐛𝐞𝐫 𝐨𝐟 𝐭𝐡𝐞 𝐜𝐡𝐚𝐧𝐧𝐞𝐥. 𝐏𝐥𝐞𝐚𝐬𝐞 𝐣𝐨𝐢𝐧 𝐭𝐨 𝐮𝐬𝐞 𝐭𝐡𝐞 𝐛𝐨𝐭.")

async def handle_enter_api_key(update: Update, context: CallbackContext):
    query = update.callback_query
    # Notify the user to enter the API key
    await query.message.reply_text("𝐏𝐥𝐞𝐚𝐬𝐞 𝐞𝐧𝐭𝐞𝐫 𝐲𝐨𝐮𝐫 𝐒𝐞𝐜𝐫𝐞𝐭 𝐤𝐞𝐲:")
    context.user_data['state'] = 'awaiting_api_key'
    # Add a message handler that listens for the API key input
    context.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_api_key))

async def handle_api_key(update: Update, context: CallbackContext):
    api_key = update.message.text
    if api_key in VALID_API_KEYS:
        context.user_data['api_key'] = api_key
        await update.message.reply_text("🔑 𝐒𝐞𝐜𝐫𝐞𝐭 𝐤𝐞𝐲 𝐢𝐬 𝐯𝐚𝐥𝐢𝐝.\n 📞 𝐏𝐥𝐞𝐚𝐬𝐞 𝐩𝐫𝐨𝐯𝐢𝐝𝐞 𝐲𝐨𝐮𝐫 𝐩𝐡𝐨𝐧𝐞 𝐧𝐮𝐦𝐛𝐞𝐫 𝐰𝐢𝐭𝐡 𝐜𝐨𝐮𝐧𝐭𝐫𝐲 𝐜𝐨𝐝𝐞 (𝐰𝐢𝐭𝐡𝐨𝐮𝐭 +):")
        context.user_data['state'] = 'awaiting_phone_number'
    else:
        await update.message.reply_text("❌ 𝐈𝐧𝐯𝐚𝐥𝐢𝐝 𝐒𝐞𝐜𝐫𝐞𝐭 𝐤𝐞𝐲. 𝐏𝐥𝐞𝐚𝐬𝐞 𝐭𝐫𝐲 𝐚𝐠𝐚𝐢𝐧.")

async def handle_phone_number(update: Update, context: CallbackContext):
    phone_number = update.message.text
    if len(phone_number) == 12 and phone_number.isdigit():
        context.user_data['phone_number'] = phone_number
        await update.message.reply_text("🗨️ 𝐏𝐥𝐞𝐚𝐬𝐞 𝐩𝐫𝐨𝐯𝐢𝐝𝐞 𝐭𝐡𝐞 𝐦𝐞𝐬𝐬𝐚𝐠𝐞 𝐲𝐨𝐮 𝐰𝐨𝐮𝐥𝐝 𝐥𝐢𝐤𝐞 𝐭𝐨 𝐬𝐞𝐧𝐝 𝐭𝐨 𝐭𝐡𝐞 𝐩𝐡𝐨𝐧𝐞 𝐧𝐮𝐦𝐛𝐞𝐫:")
        context.user_data['state'] = 'awaiting_message'
    else:
        await update.message.reply_text("❌ 𝐈𝐧𝐯𝐚𝐥𝐢𝐝 𝐩𝐡𝐨𝐧𝐞 𝐧𝐮𝐦𝐛𝐞𝐫. 𝐏𝐥𝐞𝐚𝐬𝐞 𝐩𝐫𝐨𝐯𝐢𝐝𝐞 𝐚 12-𝐝𝐢𝐠𝐢𝐭 𝐩𝐡𝐨𝐧𝐞 𝐧𝐮𝐦𝐛𝐞𝐫 𝐰𝐢𝐭𝐡𝐨𝐮𝐭 𝐭𝐡𝐞 +.")

async def handle_message(update: Update, context: CallbackContext):
    message = update.message.text
    phone_number = context.user_data.get('phone_number')
    api_key = context.user_data.get('api_key')
    result = await send_sms(api_key, phone_number, SMS_API_FROM, message)
    await update.message.reply_text(result)
    context.user_data['state'] = None

async def send_sms(api_key: str, phone_number: str, sender_name: str, message: str):
    headers = {
        "Accept": "application/json",
        "Authorization": f"basic {api_key}"
    }
    data = {
        "to": phone_number,
        "from": sender_name,
        "text": message
    }
    response = requests.post(SMS_API_URL, headers=headers, data=data)
    print(f"SMS API response: {response.status_code}, {response.text}")  # Log the response
    try:
        response_json = response.json()
        success_code = response_json.get("success")
        messages = response_json.get("messages", [])
        if messages:
            message_info = messages[0]
            recipient = message_info.get("recipient")
            text = message_info.get("text")
            success = message_info.get("success")
            error_text = message_info.get("error_text")
            if error_text:
                return (
                    f"📩 𝐌𝐞𝐬𝐬𝐚𝐠𝐞 𝐃𝐞𝐭𝐚𝐢𝐥𝐬\n\n"
                    f"👤 𝐑𝐞𝐜𝐢𝐩𝐢𝐞𝐧𝐭: {recipient}\n"
                    f"💬 𝐌𝐞𝐬𝐬𝐚𝐠𝐞: {text}\n"
                    f"✅ 𝐒𝐞𝐧𝐭: {success}\n"
                    f"⚠️ 𝐄𝐫𝐫𝐨𝐫: {error_text}\n"
                    f"𝐏𝐥𝐞𝐚𝐬𝐞 𝐜𝐨𝐧𝐭𝐚𝐜𝐭 𝐌𝐚𝐲𝐚𝐧𝐤 𝐭𝐨 𝐠𝐞𝐭 𝐚 𝐧𝐞𝐰 𝐒𝐞𝐜𝐫𝐞𝐭 𝐤𝐞𝐲."
                )
            else:
                return (
                    f"📩 𝐌𝐞𝐬𝐬𝐚𝐠𝐞 𝐃𝐞𝐭𝐚𝐢𝐥𝐬\n\n"
                    f"👤 𝐑𝐞𝐜𝐢𝐩𝐢𝐞𝐧𝐭: {recipient}\n"
                    f"💬 𝐌𝐞𝐬𝐬𝐚𝐠𝐞: {text}\n"
                    f"✅ 𝐒𝐞𝐧𝐭: {success}\n"
                )
        else:
            return (
                f"❌ 𝐅𝐚𝐢𝐥𝐞𝐝 𝐭𝐨 𝐬𝐞𝐧𝐝 𝐒𝐌𝐒.\n"
                f"📄 𝐑𝐞𝐬𝐩𝐨𝐧𝐬𝐞 𝐜𝐨𝐝𝐞: {success_code}\n"
                f"❌ 𝐄𝐫𝐫𝐨𝐫: {response_json.get('error_text')}"
            )
    except ValueError:
        return f"❌ 𝐅𝐚𝐢𝐥𝐞𝐝 𝐭𝐨 𝐩𝐚𝐫𝐬𝐞 𝐫𝐞𝐬𝐩𝐨𝐧𝐬𝐞 𝐚𝐬 𝐉𝐒𝐎𝐍.\n📄 𝐑𝐞𝐬𝐩𝐨𝐧𝐬𝐞: {response.text}"

async def admin_panel(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    if user_id not in ADMIN_USER_ID:
        await update.message.reply_text("❌ 𝐘𝐨𝐮 𝐚𝐫𝐞 𝐧𝐨𝐭 𝐚𝐮𝐭𝐡𝐨𝐫𝐢𝐳𝐞𝐝 𝐭𝐨 𝐚𝐜𝐜𝐞𝐬𝐬 𝐭𝐡𝐞 𝐚𝐝𝐦𝐢𝐧 𝐩𝐚𝐧𝐞𝐥.")
        return

    keyboard = [
        [InlineKeyboardButton("Check Total Bot Users", callback_data='total_users')],
        [InlineKeyboardButton("Broadcast", callback_data='broadcast')],
        [InlineKeyboardButton("Check Balance", callback_data='check_balance')]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("🔧 Admin Panel:", reply_markup=reply_markup)

async def handle_admin_actions(update: Update, context: CallbackContext):
    query = update.callback_query 
    user_id = query.from_user.id

    if user_id not in ADMIN_USER_ID:
        await query.answer("❌ 𝐘𝐨𝐮 𝐚𝐫𝐞 𝐧𝐨𝐭 𝐚𝐮𝐭𝐡𝐨𝐫𝐢𝐳𝐞𝐝 𝐭𝐨 𝐮𝐬𝐞 𝐭𝐡𝐢𝐬 𝐜𝐨𝐦𝐦𝐚𝐧𝐝.")
        return

    if query.data == 'total_users':
        user_data = context.application.persistence.user_data
        total_users_count = len(user_data)
        await query.message.reply_text(f"👥 Total users: {total_users_count}")
    elif query.data == 'broadcast':
        await query.message.reply_text("📢 Please send the message to broadcast:")
        context.user_data['state'] = 'awaiting_broadcast_message'
    elif query.data == "check_balance":
        await query.message.reply_text("🔑 𝐏𝐥𝐞𝐚𝐬𝐞 𝐞𝐧𝐭𝐞𝐫 𝐲𝐨𝐮𝐫 𝐒𝐞𝐜𝐫𝐞𝐭 𝐤𝐞𝐲 𝐭𝐨 𝐜𝐡𝐞𝐜𝐤 𝐭𝐡𝐞 𝐛𝐚𝐥𝐚𝐧𝐜𝐞.")
        context.user_data['state'] = 'admin_balance_check'  # Set the state to await API key
        context.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_api_key_for_balance))

async def handle_admin_api_key(update: Update, context: CallbackContext):
    if context.user_data.get('state') == 'awaiting_admin_api_key':
        api_key = update.message.text
        if api_key:
            context.user_data['admin_api_key'] = api_key
            await update.message.reply_text("𝐒𝐞𝐜𝐫𝐞𝐭 𝐊𝐞𝐲 𝐫𝐞𝐜𝐞𝐢𝐯𝐞𝐝. 𝐘𝐨𝐮 𝐜𝐚𝐧 𝐧𝐨𝐰 𝐜𝐡𝐞𝐜𝐤 𝐭𝐡𝐞 𝐛𝐚𝐥𝐚𝐧𝐜𝐞.")
            # Reset the state after receiving the API key
            context.user_data['state'] = None
            # Remove the handler after processing the API key
            context.application.remove_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_api_key))

async def handle_check_balance(update: Update, context: CallbackContext):
    api_key = context.user_data['admin_api_key']
    balance = await check_balance(api_key)
    await update.message.reply_text(f"Current Balance: {balance}")

async def check_balance(api_key):
    headers = {"X-Api-Key": api_key}
    try:
        response = requests.get("https://gateway.seven.io/api/balance", headers=headers)

        if response.status_code == 200:
            try:
                balance = float(response.text)
            except ValueError:
                balance = "Error: Invalid balance format"
            return f"Current Balance: {balance}"

        else:
            return f"Error: Unable to fetch balance. Status Code: {response.status_code}"

    except requests.exceptions.RequestException as e:
        return f"Error: {str(e)}"
    
async def handle_api_key_for_balance(update: Update, context: CallbackContext):
    # Check for the specific state for admin balance check
    if context.user_data.get('state') == 'admin_balance_check':
        api_key = update.message.text

        # Save the API key for the balance check
        context.user_data['admin_api_key'] = api_key

        # Check the balance using the provided API key
        balance = await check_balance(api_key)

        # Reply with the balance
        await update.message.reply_text(balance)

        # Clear the API key and reset the state
        del context.user_data['admin_api_key']
        context.user_data['state'] = None
    
async def total_users(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    if user_id not in ADMIN_USER_ID:
        await update.message.reply_text("❌ 𝐘𝐨𝐮 𝐚𝐫𝐞 𝐧𝐨𝐭 𝐚𝐮𝐭𝐡𝐨𝐫𝐢𝐳𝐞𝐝 𝐭𝐨 𝐮𝐬𝐞 𝐭𝐡𝐢𝐬 𝐜𝐨𝐦𝐦𝐚𝐧𝐝.")
        return

    user_data = context.application.persistence.user_data
    total_users_count = len(user_data)
    await update.message.reply_text(f"👥 Total users: {total_users_count}")

async def broadcast_message(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    if user_id not in ADMIN_USER_ID:
        await update.message.reply_text("❌ You are not authorized to use this command.")
        return

    message = update.message.text
    if not message:
        await update.message.reply_text("❌ Please provide a message to broadcast.")
        return

    user_data = context.application.persistence.user_data
    for user_id in user_data.keys():
        try:
            await context.bot.send_message(chat_id=user_id, text=f"📢 𝑩𝒓𝒐𝒂𝒅𝒄𝒂𝒔𝒕 𝑴𝒆𝒔𝒔𝒂𝒈𝒆:\n\n{message}")
        except Exception as e:
            print(f"Error sending message to {user_id}: {e}")

    await update.message.reply_text("✅ Broadcast message sent to all users.")
    context.user_data['state'] = None

async def handle_input(update: Update, context: CallbackContext):
    state = context.user_data.get('state')
    
    if state == 'awaiting_api_key':
        await handle_api_key(update, context)
    elif state == 'awaiting_phone_number':
        await handle_phone_number(update, context)
    elif state == 'awaiting_message':
        await handle_message(update, context)
    elif state == 'awaiting_broadcast_message':
        await broadcast_message(update, context)
    elif state == 'admin_balance_check':  # New state for admin balance check
        await handle_api_key_for_balance(update, context)

application = Application.builder().token(TELEGRAM_BOT_TOKEN).persistence(persistence).build()

# Add command handlers
application.add_handler(CommandHandler("start", start))
application.add_handler(CallbackQueryHandler(check_membership, pattern='check_membership'))
application.add_handler(CallbackQueryHandler(handle_enter_api_key, pattern='enter_api_key'))
application.add_handler(CommandHandler("admin", admin_panel))
application.add_handler(CallbackQueryHandler(handle_admin_actions))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_input))
application.add_handler(CommandHandler("total_users", total_users))

# Start the bot
application.run_polling()