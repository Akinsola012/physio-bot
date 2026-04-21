import os
import logging
import asyncio
from datetime import datetime, time
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
PATIENT_EXERCISES = {}  # Store exercises for each patient (by phone)
PATIENT_TIME_PREFS = {}  # Store time preferences (by chat_id)

# Buttons
BUTTONS = [["✅ DONE", "⚠️ PAIN", "❌ SKIP"]]
PAIN_BUTTONS = [["1", "2", "3", "4", "5"], ["6", "7", "8", "9", "10"]]
TIME_BUTTONS = [
    ["🌅 8am / 6pm", "🌄 9am / 7pm"],
    ["☀️ 10am / 8pm", "🌙 7am / 5pm"],
    ["⏰ Custom (tell me)"]
]

# Global variables
SHEET = None
application = None

# Default time slots
TIME_SLOTS = {
    "8am / 6pm": (8, 18),
    "9am / 7pm": (9, 19),
    "10am / 8pm": (10, 20),
    "7am / 5pm": (7, 17)
}

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

# Load patients from Google Sheets
def load_patients():
    global PATIENT_EXERCISES
    try:
        if not SHEET:
            setup_google_sheets()
        
        patients_sheet = SHEET.worksheet("Patients")
        records = patients_sheet.get_all_records()
        
        PATIENT_EXERCISES = {}
        for row in records:
            if str(row.get("Active", "")).lower() == "yes":
                phone = str(row.get("Phone", "")).strip()
                name = row.get("Name", "")
                
                PATIENT_EXERCISES[phone] = {
                    "name": name,
                    "phone": phone,
                    "morning": row.get("Morning Exercise", ""),
                    "evening": row.get("Evening Excercise", ""),
                    "pain_threshold": int(row.get("Pain Alert Threshold", 7)) if row.get("Pain Alert Threshold") else 7,
                    "preferred_time": row.get("Preferred Time", "8am,6pm")
                }
                print(f"Loaded patient: {name} (Phone: {phone}, Time: {PATIENT_EXERCISES[phone]['preferred_time']})")
        
        print(f"✅ Loaded {len(PATIENT_EXERCISES)} patients from Google Sheets")
        return PATIENT_EXERCISES
    except Exception as e:
        print(f"Error loading patients: {e}")
        return {}

# Save time preference to Google Sheets
def save_time_preference(phone, time_pref):
    try:
        if not SHEET:
            setup_google_sheets()
        
        patients_sheet = SHEET.worksheet("Patients")
        # Find the row with this phone number
        cell = patients_sheet.find(phone)
        if cell:
            # Update Preferred Time column (column H = 8)
            patients_sheet.update_cell(cell.row, 8, time_pref)
            print(f"Saved time preference for {phone}: {time_pref}")
    except Exception as e:
        print(f"Save time preference error: {e}")

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
        cell = patients_sheet.find(patient_name)
        if cell:
            patients_sheet.update_cell(cell.row, 7, datetime.now().strftime("%Y-%m-%d %H:%M"))
            print(f"Updated last contact for {patient_name}")
    except Exception as e:
        print(f"Update last contact error: {e}")

# Save patient name and link phone to chat_id
def save_patient(chat_id, name, phone=None, time_pref=None):
    PATIENTS[chat_id] = {
        "name": name, 
        "chat_id": chat_id, 
        "phone": phone,
        "time_pref": time_pref,
        "awaiting_name": False,
        "awaiting_phone": False,
        "awaiting_time": False
    }
    if time_pref:
        PATIENT_TIME_PREFS[chat_id] = time_pref
    print(f"Patient saved: {name} (chat_id: {chat_id}, phone: {phone}, time: {time_pref})")

# Get patient's time preference
def get_patient_time_pref(chat_id):
    if chat_id in PATIENT_TIME_PREFS:
        return PATIENT_TIME_PREFS[chat_id]
    
    # Check if patient has phone linked
    phone = PATIENTS.get(chat_id, {}).get("phone")
    if phone and phone in PATIENT_EXERCISES:
        return PATIENT_EXERCISES[phone].get("preferred_time", "8am,6pm")
    
    return "8am,6pm"

# Parse time preference to hours
def parse_time_pref(time_pref):
    if time_pref in TIME_SLOTS:
        return TIME_SLOTS[time_pref]
    
    # Handle "8am,6pm" format
    if "," in time_pref:
        parts = time_pref.split(",")
        morning_hour = int(parts[0].replace("am", "").strip())
        evening_hour = int(parts[1].replace("pm", "").strip())
        return (morning_hour, evening_hour)
    
    return (8, 18)  # Default

# Send reminder to a specific chat_id
async def send_reminder(chat_id, name, exercises, period):
    try:
        message = f"Good {period} {name}! 👋\n\nToday's exercises:\n{exercises}\n\nTap a button when done:"
        await application.bot.send_message(
            chat_id=chat_id,
            text=message,
            reply_markup=ReplyKeyboardMarkup(BUTTONS, resize_keyboard=True)
        )
        print(f"Sent {period} reminder to {name}")
    except Exception as e:
        print(f"Failed to send to {name}: {e}")

# Send morning reminders to all patients
async def send_morning_reminders():
    print(f"\n--- Sending Morning Reminders: {datetime.now()} ---")
    for phone, data in PATIENT_EXERCISES.items():
        for chat_id, patient in PATIENTS.items():
            if patient.get("phone") == phone:
                time_pref = get_patient_time_pref(chat_id)
                morning_hour, evening_hour = parse_time_pref(time_pref)
                current_hour = datetime.now().hour
                if current_hour == morning_hour:
                    await send_reminder(chat_id, data["name"], data["morning"], "morning")
                break

# Send evening reminders to all patients
async def send_evening_reminders():
    print(f"\n--- Sending Evening Reminders: {datetime.now()} ---")
    for phone, data in PATIENT_EXERCISES.items():
        for chat_id, patient in PATIENTS.items():
            if patient.get("phone") == phone:
                time_pref = get_patient_time_pref(chat_id)
                morning_hour, evening_hour = parse_time_pref(time_pref)
                current_hour = datetime.now().hour
                if current_hour == evening_hour:
                    await send_reminder(chat_id, data["name"], data["evening"], "evening")
                break

# Scheduled job runner
async def scheduled_jobs():
    last_minute = -1
    while True:
        now = datetime.now()
        current_minute = now.minute
        
        # Check every minute (at minute 0)
        if current_minute == 0 and current_minute != last_minute:
            await send_morning_reminders()
            await send_evening_reminders()
            last_minute = current_minute
        
        await asyncio.sleep(30)

# Ask for time preference
async def ask_time_preference(update: Update, chat_id: int, phone: str):
    PATIENTS[chat_id] = PATIENTS.get(chat_id, {})
    PATIENTS[chat_id]["awaiting_time"] = True
    PATIENTS[chat_id]["phone"] = phone
    
    await update.message.reply_text(
        "🕐 Choose your preferred reminder times:\n\n"
        "When would you like to receive your exercise reminders?\n\n"
        "Tap one of the options below:",
        reply_markup=ReplyKeyboardMarkup(TIME_BUTTONS, resize_keyboard=True)
    )

# Handle time preference input
async def handle_time_preference(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, time_choice: str):
    phone = PATIENTS[chat_id].get("phone")
    patient_data = PATIENT_EXERCISES.get(phone)
    
    if time_choice == "⏰ Custom (tell me)":
        await update.message.reply_text(
            "Please tell me your preferred times.\n\n"
            "Format: morning hour then evening hour\n"
            "Example: 9am,7pm\n\n"
            "Or: 8am,6pm\n"
            "Or: 10am,8pm"
        )
        return
    
    # Save the time preference
    save_time_preference(phone, time_choice)
    PATIENTS[chat_id]["time_pref"] = time_choice
    PATIENTS[chat_id]["awaiting_time"] = False
    PATIENT_TIME_PREFS[chat_id] = time_choice
    
    morning_hour, evening_hour = parse_time_pref(time_choice)
    
    name = patient_data["name"]
    await update.message.reply_text(
        f"✅ Time preference saved!\n\n"
        f"You will receive reminders at:\n"
        f"🌅 Morning: {morning_hour}:00 AM\n"
        f"🌙 Evening: {evening_hour}:00 PM\n\n"
        f"Your exercises:\n"
        f"Morning: {patient_data['morning']}\n"
        f"Evening: {patient_data['evening']}\n\n"
        f"Tap a button to log your progress:",
        reply_markup=ReplyKeyboardMarkup(BUTTONS, resize_keyboard=True)
    )
    update_last_contact(name)

# Handle custom time input
async def handle_custom_time(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, custom_time: str):
    phone = PATIENTS[chat_id].get("phone")
    
    # Validate format
    try:
        parts = custom_time.lower().replace(" ", "").split(",")
        morning = parts[0].replace("am", "").strip()
        evening = parts[1].replace("pm", "").strip()
        morning_hour = int(morning)
        evening_hour = int(evening)
        
        if 5 <= morning_hour <= 12 and 16 <= evening_hour <= 22:
            time_pref = f"{morning_hour}am,{evening_hour}pm"
            save_time_preference(phone, time_pref)
            PATIENTS[chat_id]["time_pref"] = time_pref
            PATIENTS[chat_id]["awaiting_time"] = False
            PATIENT_TIME_PREFS[chat_id] = time_pref
            
            patient_data = PATIENT_EXERCISES[phone]
            await update.message.reply_text(
                f"✅ Custom time saved!\n\n"
                f"You will receive reminders at {morning_hour}:00 AM and {evening_hour}:00 PM\n\n"
                f"Tap a button to log your progress:",
                reply_markup=ReplyKeyboardMarkup(BUTTONS, resize_keyboard=True)
            )
        else:
            await update.message.reply_text(
                "❌ Invalid times. Morning should be 5-12, evening 16-22.\n\n"
                "Example: 9am,7pm\n\n"
                "Please try again or tap a button:",
                reply_markup=ReplyKeyboardMarkup(TIME_BUTTONS, resize_keyboard=True)
            )
    except Exception as e:
        await update.message.reply_text(
            "❌ Invalid format. Please use:\n"
            "Example: 9am,7pm\n\n"
            "Or tap a button:",
            reply_markup=ReplyKeyboardMarkup(TIME_BUTTONS, resize_keyboard=True)
        )

# Start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    
    if chat_id in PATIENTS and PATIENTS[chat_id].get("name") and PATIENTS[chat_id].get("time_pref"):
        name = PATIENTS[chat_id]["name"]
        await update.message.reply_text(
            f"Welcome back {name}! 👋\n\n"
            "Tap a button to log your exercise:",
            reply_markup=ReplyKeyboardMarkup(BUTTONS, resize_keyboard=True)
        )
    else:
        PATIENTS[chat_id] = {"awaiting_phone": True}
        await update.message.reply_text(
            "👋 Welcome to PhysioRemind!\n\n"
            "Please enter the phone number you gave your physiotherapist.\n\n"
            "Format: 2348079877837 (no + sign)"
        )

# Handle phone number input
async def handle_phone(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, phone: str):
    phone = phone.replace("+", "").replace(" ", "").strip()
    
    if phone in PATIENT_EXERCISES:
        patient_data = PATIENT_EXERCISES[phone]
        name = patient_data["name"]
        save_patient(chat_id, name, phone)
        
        await update.message.reply_text(
            f"Welcome {name}! ✅\n\n"
            "Now let's set up your reminder times.",
            reply_markup=ReplyKeyboardMarkup(TIME_BUTTONS, resize_keyboard=True)
        )
        await ask_time_preference(update, chat_id, phone)
    else:
        await update.message.reply_text(
            "❌ Phone number not found.\n\n"
            "Please contact your physiotherapist to register."
        )

# Handle all messages
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    message_text = update.message.text
    
    # Check if waiting for phone
    if chat_id in PATIENTS and PATIENTS[chat_id].get("awaiting_phone"):
        await handle_phone(update, context, chat_id, message_text)
        return
    
    # Check if waiting for time preference
    if chat_id in PATIENTS and PATIENTS[chat_id].get("awaiting_time"):
        if message_text == "⏰ Custom (tell me)":
            await handle_time_preference(update, context, chat_id, message_text)
        elif "am" in message_text.lower() and "pm" in message_text.lower():
            await handle_custom_time(update, context, chat_id, message_text)
        else:
            await handle_time_preference(update, context, chat_id, message_text)
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
    
    setup_google_sheets()
    load_patients()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("🤖 Bot is running with time preference selection and scheduled reminders...")
    
    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    
    # Start scheduled jobs in background
    asyncio.create_task(scheduled_jobs())
    
    # Keep running
    while True:
        await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main())