import os
import logging
import asyncio
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# Load environment variables
load_dotenv()

# Get tokens from environment
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CLINICIAN_ID = os.getenv("TELEGRAM_CLINICIAN_ID")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
GOOGLE_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

# Setup logging
logging.basicConfig(level=logging.INFO)

# Patient storage
PATIENTS = {}
PATIENT_EXERCISES = {}  # Store exercises for each patient (by chat_id)

# Buttons
BUTTONS = [["✅ DONE", "⚠️ PAIN", "❌ SKIP"]]
PAIN_BUTTONS = [["1", "2", "3", "4", "5"], ["6", "7", "8", "9", "10"]]

# Global sheet variable
SHEET = None

# Google Sheets setup
def setup_google_sheets():
    global SHEET
    try:
        import json
        creds_dict = json.loads(GOOGLE_JSON)
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        SHEET = client.open_by_key(GOOGLE_SHEET_ID)
        print("✅ Google Sheets connected")
        return SHEET
    except Exception as e:
        print(f"Google Sheets error: {e}")
        return None

# Load patients from Google Sheets (matches your column headers)
def load_patients():
    global PATIENT_EXERCISES
    try:
        if not SHEET:
            setup_google_sheets()
        
        patients_sheet = SHEET.worksheet("Patients")
        records = patients_sheet.get_all_records()
        
        PATIENT_EXERCISES = {}
        for row in records:
            if row.get("Active") == "Yes" or str(row.get("Active")).lower() == "yes":
                # Phone number will be used as identifier until we have chat_id
                phone = str(row.get("Phone", ""))
                name = row.get("Name", "")
                
                # Store by phone number for now (will update when user starts bot)
                PATIENT_EXERCISES[phone] = {
                    "name": name,
                    "phone": phone,
                    "morning": row.get("Morning Exercise", ""),
                    "evening": row.get("Evening Excercise", ""),
                    "pain_threshold": int(row.get("Pain Alert Threshold", 7)) if row.get("Pain Alert Threshold") else 7
                }
                print(f"Loaded patient: {name} (Phone: {phone})")
        
        print(f"✅ Loaded {len(PATIENT_EXERCISES)} patients from Google Sheets")
        return PATIENT_EXERCISES
    except Exception as e:
        print(f"Error loading patients: {e}")
        return {}

# Log to Google Sheets Logs tab
def log_response(patient_name, response, pain_score=""):
    try:
        if not SHEET:
            setup_google_sheets()
        
        log_sheet = SHEET.worksheet("Logs")
        alert_sent = "YES" if pain_score and pain_score.isdigit() and int(pain_score) >= 7 else ""
        log_sheet.append_row([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            patient_name,
            response,
            pain_score,
            "telegram",
            alert_sent
        ])
        print(f"Logged: {patient_name} - {response}")
    except Exception as e:
        print(f"Log error: {e}")

# Update Last Contact in Patients sheet
def update_last_contact(patient_name):
    try:
        if not SHEET:
            setup_google_sheets()
        
        patients_sheet = SHEET.worksheet("Patients")
        # Find the row with this patient name
        cell = patients_sheet.find(patient_name)
        if cell:
            # Update Last Contact column (column G = 7)
            patients_sheet.update_cell(cell.row, 7, datetime.now().strftime("%Y-%m-%d %H:%M"))
            print(f"Updated last contact for {patient_name}")
    except Exception as e:
        print(f"Update last contact error: {e}")

# Save patient name and link phone to chat_id
def save_patient(chat_id, name, phone=None):
    PATIENTS[chat_id] = {
        "name": name, 
        "chat_id": chat_id, 
        "phone": phone,
        "awaiting_name": False
    }
    print(f"Patient saved: {name} (chat_id: {chat_id}, phone: {phone})")

# Start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    
    # Check if already have this patient
    if chat_id in PATIENTS and PATIENTS[chat_id].get("name"):
        name = PATIENTS[chat_id]["name"]
        await update.message.reply_text(
            f"Welcome back {name}! 👋\n\n"
            "Tap a button to log your exercise:",
            reply_markup=ReplyKeyboardMarkup(BUTTONS, resize_keyboard=True)
        )
    else:
        # Ask for phone number to match with patient list
        PATIENTS[chat_id] = {"awaiting_phone": True}
        await update.message.reply_text(
            "👋 Welcome to PhysioRemind!\n\n"
            "Please enter the phone number you gave your physiotherapist.\n\n"
            "Format: 2348079877837 (no + sign)"
        )

# Handle phone number input
async def handle_phone(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, phone: str):
    # Clean phone number
    phone = phone.replace("+", "").replace(" ", "").strip()
    
    # Check if this phone exists in patients list
    if phone in PATIENT_EXERCISES:
        patient_data = PATIENT_EXERCISES[phone]
        name = patient_data["name"]
        save_patient(chat_id, name, phone)
        
        await update.message.reply_text(
            f"Welcome {name}! ✅\n\n"
            f"Your exercises: {patient_data['morning']}\n\n"
            "Tap a button to log your progress:",
            reply_markup=ReplyKeyboardMarkup(BUTTONS, resize_keyboard=True)
        )
        update_last_contact(name)
    else:
        await update.message.reply_text(
            "❌ Phone number not found in our records.\n\n"
            "Please contact your physiotherapist to register.\n\n"
            "Or type your name to continue without exercises:"
        )
        PATIENTS[chat_id] = {"awaiting_name": True}

# Handle name input (for unregistered patients)
async def handle_name(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, name: str):
    save_patient(chat_id, name)
    
    await update.message.reply_text(
        f"Thanks {name}! ✅\n\n"
        "I've noted your name. Your physiotherapist will assign your exercises soon.\n\n"
        "For now, tap a button to log:",
        reply_markup=ReplyKeyboardMarkup(BUTTONS, resize_keyboard=True)
    )

# Handle all messages
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    message_text = update.message.text
    
    # Check if waiting for phone
    if chat_id in PATIENTS and PATIENTS[chat_id].get("awaiting_phone"):
        await handle_phone(update, context, chat_id, message_text)
        return
    
    # Check if waiting for name
    if chat_id in PATIENTS and PATIENTS[chat_id].get("awaiting_name"):
        await handle_name(update, context, chat_id, message_text)
        return
    
    # Get patient name
    patient_name = PATIENTS.get(chat_id, {}).get("name", "Unknown")
    
    # Handle button responses
    if "DONE" in message_text:
        log_response(patient_name, "DONE")
        await update.message.reply_text(
            "Great job! 👏 Keep it up!",
            reply_markup=ReplyKeyboardMarkup(BUTTONS, resize_keyboard=True)
        )
        update_last_contact(patient_name)
    
    elif "PAIN" in message_text:
        log_response(patient_name, "PAIN_SELECTED")
        await update.message.reply_text(
            "I'm sorry you're in pain. Please rate it 1-10:",
            reply_markup=ReplyKeyboardMarkup(PAIN_BUTTONS, resize_keyboard=True)
        )
        context.user_data["awaiting_pain"] = True
    
    elif "SKIP" in message_text:
        log_response(patient_name, "SKIP")
        await update.message.reply_text(
            "Try to stay consistent 💪 Your recovery depends on it.",
            reply_markup=ReplyKeyboardMarkup(BUTTONS, resize_keyboard=True)
        )
        update_last_contact(patient_name)
    
    elif context.user_data.get("awaiting_pain"):
        try:
            pain_score = int(message_text)
            if 0 <= pain_score <= 10:
                log_response(patient_name, "PAIN_SCORE", str(pain_score))
                
                if pain_score >= 7:
                    await context.bot.send_message(
                        chat_id=CLINICIAN_ID,
                        text=f"🚨 PATIENT ALERT 🚨\n\n{patient_name} reported pain: {pain_score}/10\n\nPlease review immediately."
                    )
                
                await update.message.reply_text(
                    f"Pain recorded: {pain_score}/10. Take care! 🙏",
                    reply_markup=ReplyKeyboardMarkup(BUTTONS, resize_keyboard=True)
                )
                context.user_data["awaiting_pain"] = False
        except ValueError:
            await update.message.reply_text("Please send a number between 0-10.")
    
    else:
        await update.message.reply_text(
            "Tap a button: DONE, PAIN, or SKIP",
            reply_markup=ReplyKeyboardMarkup(BUTTONS, resize_keyboard=True)
        )

# Main function
async def main():
    global application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Setup Google Sheets and load patients
    setup_google_sheets()
    load_patients()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("🤖 Bot is running with patient list from Google Sheets...")
    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    
    # Keep running
    while True:
        await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main())