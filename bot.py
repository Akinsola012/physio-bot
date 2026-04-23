import os
import logging
import asyncio
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import gspread
from oauth2client.service_account import ServiceAccountCredentials

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CLINICIAN_ID = os.getenv("TELEGRAM_CLINICIAN_ID")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
GOOGLE_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

logging.basicConfig(level=logging.INFO)

PATIENTS = {}
PATIENT_EXERCISES = {}
PATIENT_TIME_PREFS = {}

BUTTONS = [["✅ DONE", "⚠️ PAIN", "❌ SKIP"]]
PAIN_BUTTONS = [["1", "2", "3", "4", "5"], ["6", "7", "8", "9", "10"]]
TIME_BUTTONS = [
    ["🌅 8am / 6pm", "🌄 9am / 7pm"],
    ["☀️ 10am / 8pm", "🌙 7am / 5pm"],
    ["⏰ Custom (tell me)"]
]

SHEET = None
application = None

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
                }
                print(f"Loaded patient: {name}")
        print(f"✅ Loaded {len(PATIENT_EXERCISES)} patients")
    except Exception as e:
        print(f"Error loading patients: {e}")

def save_time_preference(phone, time_pref):
    try:
        if not SHEET:
            setup_google_sheets()
        patients_sheet = SHEET.worksheet("Patients")
        cell = patients_sheet.find(phone)
        if cell:
            patients_sheet.update_cell(cell.row, 8, time_pref)
            print(f"Saved time preference for {phone}: {time_pref}")
    except Exception as e:
        print(f"Save error: {e}")

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

def update_last_contact(patient_name):
    try:
        if not SHEET:
            setup_google_sheets()
        patients_sheet = SHEET.worksheet("Patients")
        cell = patients_sheet.find(patient_name)
        if cell:
            patients_sheet.update_cell(cell.row, 7, datetime.now().strftime("%Y-%m-%d %H:%M"))
    except Exception as e:
        print(f"Update error: {e}")

def save_patient(chat_id, name, phone=None, time_pref=None):
    PATIENTS[chat_id] = {
        "name": name,
        "chat_id": chat_id,
        "phone": phone,
        "time_pref": time_pref,
        "awaiting_phone": False,
        "awaiting_time": False
    }
    if time_pref:
        PATIENT_TIME_PREFS[chat_id] = time_pref
    print(f"Patient saved: {name}")

def parse_time_pref(time_pref):
    try:
        time_pref = time_pref.lower().replace(" ", "")
        if "am" in time_pref and "pm" in time_pref:
            parts = time_pref.split(",")
            morning = int(parts[0].replace("am", ""))
            evening = int(parts[1].replace("pm", ""))
            return (morning, evening)
    except:
        pass
    return (8, 18)

def convert_button_to_time(button_text):
    if "8am" in button_text:
        return "8am,6pm"
    elif "9am" in button_text:
        return "9am,7pm"
    elif "10am" in button_text:
        return "10am,8pm"
    elif "7am" in button_text:
        return "7am,5pm"
    return None

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
        print(f"Failed to send: {e}")

async def send_morning_reminders():
    current_hour = datetime.now().hour
    for phone, data in PATIENT_EXERCISES.items():
        for chat_id, patient in PATIENTS.items():
            if patient.get("phone") == phone:
                time_pref = patient.get("time_pref", "8am,6pm")
                morning_hour, _ = parse_time_pref(time_pref)
                if current_hour == morning_hour:
                    await send_reminder(chat_id, data["name"], data["morning"], "morning")
                break

async def send_evening_reminders():
    current_hour = datetime.now().hour
    for phone, data in PATIENT_EXERCISES.items():
        for chat_id, patient in PATIENTS.items():
            if patient.get("phone") == phone:
                time_pref = patient.get("time_pref", "8am,6pm")
                _, evening_hour = parse_time_pref(time_pref)
                if current_hour == evening_hour:
                    await send_reminder(chat_id, data["name"], data["evening"], "evening")
                break

async def scheduled_jobs():
    last_hour = -1
    while True:
        now = datetime.now()
        current_hour = now.hour
        if current_hour != last_hour:
            await send_morning_reminders()
            await send_evening_reminders()
            last_hour = current_hour
        await asyncio.sleep(60)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in PATIENTS and PATIENTS[chat_id].get("time_pref"):
        name = PATIENTS[chat_id]["name"]
        await update.message.reply_text(
            f"Welcome back {name}! 👋\n\nTap a button to log your exercise:",
            reply_markup=ReplyKeyboardMarkup(BUTTONS, resize_keyboard=True)
        )
    else:
        PATIENTS[chat_id] = {"awaiting_phone": True}
        await update.message.reply_text(
            "👋 Welcome to PhysioRemind!\n\nPlease enter the phone number you gave your physiotherapist.\n\nFormat: 2348079877837 (no + sign)"
        )

async def handle_phone(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, phone: str):
    phone = phone.replace("+", "").replace(" ", "").strip()
    if phone in PATIENT_EXERCISES:
        patient_data = PATIENT_EXERCISES[phone]
        save_patient(chat_id, patient_data["name"], phone)
        await update.message.reply_text(
            f"Welcome {patient_data['name']}! ✅\n\nNow let's set up your reminder times.\n\nTap one of the options below:",
            reply_markup=ReplyKeyboardMarkup(TIME_BUTTONS, resize_keyboard=True)
        )
        PATIENTS[chat_id]["awaiting_time"] = True
    else:
        await update.message.reply_text("❌ Phone number not found.\n\nPlease contact your physiotherapist to register.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text
    
    if chat_id in PATIENTS and PATIENTS[chat_id].get("awaiting_phone"):
        await handle_phone(update, context, chat_id, text)
        return
    
    if chat_id in PATIENTS and PATIENTS[chat_id].get("awaiting_time"):
        phone = PATIENTS[chat_id].get("phone")
        patient_data = PATIENT_EXERCISES.get(phone)
        
        # Check if it's a button tap or custom text
        if text == "⏰ Custom (tell me)":
            await update.message.reply_text(
                "Please type your preferred times.\n\nExamples:\n• 8am,6pm\n• 9am,7pm\n• 10am,8pm"
            )
            return
        
        # Convert button text to time string
        time_value = convert_button_to_time(text)
        
        if time_value:
            # It was a button tap
            save_time_preference(phone, time_value)
            PATIENTS[chat_id]["time_pref"] = time_value
            PATIENTS[chat_id]["awaiting_time"] = False
            morning_hour, evening_hour = parse_time_pref(time_value)
            
            await update.message.reply_text(
                f"✅ Time saved!\n\nYou will receive reminders at:\n🌅 Morning: {morning_hour}:00\n🌙 Evening: {evening_hour}:00\n\nYour exercises:\n🌅 Morning: {patient_data['morning']}\n🌙 Evening: {patient_data['evening']}\n\nTap a button to log your progress:",
                reply_markup=ReplyKeyboardMarkup(BUTTONS, resize_keyboard=True)
            )
            update_last_contact(patient_data["name"])
        else:
            # Try to parse as custom time (e.g., "10am,8pm")
            try:
                clean = text.lower().replace(" ", "")
                if "am" in clean and "pm" in clean and "," in clean:
                    parts = clean.split(",")
                    morning = int(parts[0].replace("am", ""))
                    evening = int(parts[1].replace("pm", ""))
                    if 5 <= morning <= 12 and 16 <= evening <= 22:
                        time_value = f"{morning}am,{evening}pm"
                        save_time_preference(phone, time_value)
                        PATIENTS[chat_id]["time_pref"] = time_value
                        PATIENTS[chat_id]["awaiting_time"] = False
                        await update.message.reply_text(
                            f"✅ Custom time saved!\n\nReminders at: {morning}:00 and {evening}:00\n\nTap a button to log:",
                            reply_markup=ReplyKeyboardMarkup(BUTTONS, resize_keyboard=True)
                        )
                        update_last_contact(patient_data["name"])
                    else:
                        await update.message.reply_text("❌ Invalid times. Morning 5-12, Evening 16-22. Try again:", reply_markup=ReplyKeyboardMarkup(TIME_BUTTONS, resize_keyboard=True))
                else:
                    await update.message.reply_text("❌ Invalid format. Use example: 10am,8pm", reply_markup=ReplyKeyboardMarkup(TIME_BUTTONS, resize_keyboard=True))
            except:
                await update.message.reply_text("❌ Invalid. Try: 8am,6pm or tap a button", reply_markup=ReplyKeyboardMarkup(TIME_BUTTONS, resize_keyboard=True))
        return
    
    # Normal flow after setup
    patient_name = PATIENTS.get(chat_id, {}).get("name", "Unknown")
    
    if "DONE" in text:
        log_response(patient_name, "DONE")
        await update.message.reply_text("Great job! 👏", reply_markup=ReplyKeyboardMarkup(BUTTONS, resize_keyboard=True))
        update_last_contact(patient_name)
    elif "PAIN" in text:
        log_response(patient_name, "PAIN_SELECTED")
        await update.message.reply_text("Rate pain 1-10:", reply_markup=ReplyKeyboardMarkup(PAIN_BUTTONS, resize_keyboard=True))
        context.user_data["awaiting_pain"] = True
    elif "SKIP" in text:
        log_response(patient_name, "SKIP")
        await update.message.reply_text("Try to stay consistent 💪", reply_markup=ReplyKeyboardMarkup(BUTTONS, resize_keyboard=True))
        update_last_contact(patient_name)
    elif context.user_data.get("awaiting_pain"):
        try:
            score = int(text)
            if 0 <= score <= 10:
                log_response(patient_name, "PAIN_SCORE", str(score))
                if score >= 7:
                    await context.bot.send_message(chat_id=CLINICIAN_ID, text=f"🚨 ALERT: {patient_name} reported pain {score}/10")
                await update.message.reply_text(f"Pain recorded: {score}/10", reply_markup=ReplyKeyboardMarkup(BUTTONS, resize_keyboard=True))
                context.user_data["awaiting_pain"] = False
        except:
            await update.message.reply_text("Please send a number 0-10")
    else:
        await update.message.reply_text("Tap: DONE, PAIN, or SKIP", reply_markup=ReplyKeyboardMarkup(BUTTONS, resize_keyboard=True))

async def main():
    global application
    application = Application.builder().token(BOT_TOKEN).build()
    setup_google_sheets()
    load_patients()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("🤖 Bot is running...")
    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    asyncio.create_task(scheduled_jobs())
    while True:
        await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main())