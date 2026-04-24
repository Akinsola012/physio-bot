import os
import logging
import asyncio
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import json

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CLINICIAN_ID = os.getenv("TELEGRAM_CLINICIAN_ID")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
GOOGLE_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

logging.basicConfig(level=logging.INFO)

PATIENTS = {}
PATIENT_EXERCISES = {}

BUTTONS = [["✅ DONE", "⚠️ PAIN", "❌ SKIP"]]
PAIN_BUTTONS = [["1", "2", "3", "4", "5"], ["6", "7", "8", "9", "10"]]
TIME_BUTTONS = [
    ["🌅 8am / 6pm", "🌄 9am / 7pm"],
    ["☀️ 10am / 8pm", "🌙 7am / 5pm"],
    ["⏰ Custom (tell me)", "🔴 Test now"]
]

SHEET = None
application = None

def setup_google_sheets():
    global SHEET
    creds_dict = json.loads(GOOGLE_JSON)
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    SHEET = client.open_by_key(GOOGLE_SHEET_ID)
    print("✅ Google Sheets connected")

def load_patients():
    global PATIENT_EXERCISES
    patients_sheet = SHEET.worksheet("Patients")
    records = patients_sheet.get_all_records()
    PATIENT_EXERCISES = {}
    for row in records:
        if str(row.get("Active", "")).lower() == "yes":
            phone = str(row.get("Phone", "")).strip()
            PATIENT_EXERCISES[phone] = {
                "name": row.get("Name", ""),
                "morning": row.get("Morning Exercise", ""),
                "evening": row.get("Evening Excercise", ""),
                "video_url": row.get("Video URL", "")
            }
    print(f"✅ Loaded {len(PATIENT_EXERCISES)} patients")

def save_time_preference(phone, time_pref):
    try:
        patients_sheet = SHEET.worksheet("Patients")
        cell = patients_sheet.find(phone)
        if cell:
            patients_sheet.update_cell(cell.row, 8, time_pref)
            print(f"Saved time for {phone}: {time_pref}")
    except Exception as e:
        print(f"Save error: {e}")

def parse_time_pref(time_pref):
    try:
        time_pref = time_pref.lower().replace(" ", "")
        if "," in time_pref:
            parts = time_pref.split(",")
            morning = int(parts[0].replace("am", ""))
            evening = int(parts[1].replace("pm", ""))
            return (morning, evening)
    except:
        pass
    return (8, 18)

def parse_video_links(video_url_string):
    if not video_url_string or video_url_string.strip() == "":
        return []
    videos = []
    parts = video_url_string.split(",")
    for part in parts:
        part = part.strip()
        if ":" in part:
            name, url = part.split(":", 1)
            videos.append((name.strip(), url.strip()))
    return videos

async def send_reminder(chat_id, name, exercises, period, video_url_string=None):
    try:
        message = f"Good {period} {name}! 👋\n\nToday's exercises:\n{exercises}"
        await application.bot.send_message(chat_id=chat_id, text=message)
        
        if video_url_string:
            videos = parse_video_links(video_url_string)
            for video_name, video_url in videos:
                await application.bot.send_message(
                    chat_id=chat_id,
                    text=f"🎥 {video_name}:\n{video_url}"
                )
        
        await application.bot.send_message(
            chat_id=chat_id,
            text="Tap a button when done:",
            reply_markup=ReplyKeyboardMarkup(BUTTONS, resize_keyboard=True)
        )
        print(f"Sent {period} reminder to {name}")
    except Exception as e:
        print(f"Error: {e}")

async def scheduled_jobs():
    while True:
        now = datetime.now()
        current_hour = now.hour
        current_minute = now.minute
        
        for phone, data in PATIENT_EXERCISES.items():
            for chat_id, patient in PATIENTS.items():
                if patient.get("phone") == phone:
                    time_pref = patient.get("time_pref", "8am,6pm")
                    morning_hour, evening_hour = parse_time_pref(time_pref)
                    
                    if current_hour == morning_hour and current_minute == 0:
                        await send_reminder(chat_id, data["name"], data["morning"], "morning", data.get("video_url"))
                    if current_hour == evening_hour and current_minute == 0:
                        await send_reminder(chat_id, data["name"], data["evening"], "evening", data.get("video_url"))
        
        await asyncio.sleep(30)

def log_response(patient_name, response, pain_score=""):
    try:
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
    except Exception as e:
        print(f"Log error: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    PATIENTS[chat_id] = {"awaiting_phone": True}
    await update.message.reply_text(
        "👋 Welcome!\n\nPlease enter your phone number.\n\nFormat: 2348079877837"
    )

async def handle_phone(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, phone: str):
    phone = phone.replace("+", "").replace(" ", "").strip()
    if phone in PATIENT_EXERCISES:
        patient_data = PATIENT_EXERCISES[phone]
        PATIENTS[chat_id] = {"phone": phone, "name": patient_data["name"], "awaiting_time": True}
        await update.message.reply_text(
            f"Welcome {patient_data['name']}! ✅\n\nChoose your reminder times:",
            reply_markup=ReplyKeyboardMarkup(TIME_BUTTONS, resize_keyboard=True)
        )
    else:
        await update.message.reply_text("❌ Phone number not found.")

async def send_test_reminder(chat_id, name, exercises, video_url_string=None):
    try:
        message = f"🔴 TEST REMINDER 🔴\n\nGood {name}! 👋\n\nToday's exercises:\n{exercises}"
        await application.bot.send_message(chat_id=chat_id, text=message)
        
        if video_url_string:
            videos = parse_video_links(video_url_string)
            for video_name, video_url in videos:
                await application.bot.send_message(
                    chat_id=chat_id,
                    text=f"🎥 {video_name}:\n{video_url}"
                )
        
        await application.bot.send_message(
            chat_id=chat_id,
            text="Tap a button when done:",
            reply_markup=ReplyKeyboardMarkup(BUTTONS, resize_keyboard=True)
        )
        print(f"Sent test reminder to {name}")
    except Exception as e:
        print(f"Error: {e}")

async def handle_time_choice(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str):
    phone = PATIENTS[chat_id]["phone"]
    patient_data = PATIENT_EXERCISES[phone]
    
    time_map = {
        "🌅 8am / 6pm": "8am,6pm",
        "🌄 9am / 7pm": "9am,7pm",
        "☀️ 10am / 8pm": "10am,8pm",
        "🌙 7am / 5pm": "7am,5pm"
    }
    
    if text == "🔴 Test now":
        await send_test_reminder(chat_id, patient_data["name"], patient_data["morning"], patient_data.get("video_url"))
        return
    
    if text in time_map:
        time_value = time_map[text]
        save_time_preference(phone, time_value)
        PATIENTS[chat_id]["time_pref"] = time_value
        PATIENTS[chat_id]["awaiting_time"] = False
        morning_hour, evening_hour = parse_time_pref(time_value)
        
        await update.message.reply_text(
            f"✅ Reminders set for {morning_hour}:00 and {evening_hour}:00\n\nYou will receive automatic reminders at these times daily.\n\nTap a button to log:",
            reply_markup=ReplyKeyboardMarkup(BUTTONS, resize_keyboard=True)
        )
    elif text == "⏰ Custom (tell me)":
        await update.message.reply_text("Type your times (e.g., 8am,6pm or 9am,7pm)")
    else:
        await update.message.reply_text("Tap a button or type example: 8am,6pm")

async def handle_custom_time(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, custom_time: str):
    phone = PATIENTS[chat_id]["phone"]
    
    try:
        clean = custom_time.lower().replace(" ", "")
        if "," in clean:
            parts = clean.split(",")
            morning = int(parts[0].replace("am", ""))
            evening = int(parts[1].replace("pm", ""))
            if 5 <= morning <= 12 and 16 <= evening <= 22:
                time_value = f"{morning}am,{evening}pm"
                save_time_preference(phone, time_value)
                PATIENTS[chat_id]["time_pref"] = time_value
                PATIENTS[chat_id]["awaiting_time"] = False
                await update.message.reply_text(
                    f"✅ Reminders set for {morning}:00 and {evening}:00\n\nTap a button to log:",
                    reply_markup=ReplyKeyboardMarkup(BUTTONS, resize_keyboard=True)
                )
                return
    except:
        pass
    
    await update.message.reply_text("❌ Invalid. Try: 8am,6pm", reply_markup=ReplyKeyboardMarkup(TIME_BUTTONS, resize_keyboard=True))

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text
    
    if chat_id in PATIENTS and PATIENTS[chat_id].get("awaiting_phone"):
        await handle_phone(update, context, chat_id, text)
        return
    
    if chat_id in PATIENTS and PATIENTS[chat_id].get("awaiting_time"):
        if "," in text and ("am" in text.lower() or "pm" in text.lower()):
            await handle_custom_time(update, context, chat_id, text)
        else:
            await handle_time_choice(update, context, chat_id, text)
        return
    
    patient_name = PATIENTS.get(chat_id, {}).get("name", "Unknown")
    
    if "DONE" in text:
        log_response(patient_name, "DONE")
        await update.message.reply_text("Great job! 👏", reply_markup=ReplyKeyboardMarkup(BUTTONS, resize_keyboard=True))
    elif "PAIN" in text:
        log_response(patient_name, "PAIN_SELECTED")
        await update.message.reply_text("Rate pain 1-10:", reply_markup=ReplyKeyboardMarkup(PAIN_BUTTONS, resize_keyboard=True))
        context.user_data["awaiting_pain"] = True
    elif "SKIP" in text:
        log_response(patient_name, "SKIP")
        await update.message.reply_text("Try not to skip 💪", reply_markup=ReplyKeyboardMarkup(BUTTONS, resize_keyboard=True))
    elif context.user_data.get("awaiting_pain"):
        try:
            score = int(text)
            if 0 <= score <= 10:
                log_response(patient_name, "PAIN_SCORE", str(score))
                if score >= 7:
                    await context.bot.send_message(chat_id=CLINICIAN_ID, text=f"🚨 {patient_name} pain: {score}/10")
                await update.message.reply_text(f"Pain recorded: {score}/10", reply_markup=ReplyKeyboardMarkup(BUTTONS, resize_keyboard=True))
                context.user_data["awaiting_pain"] = False
        except:
            await update.message.reply_text("Send number 0-10")
    else:
        await update.message.reply_text("Tap: DONE, PAIN, SKIP", reply_markup=ReplyKeyboardMarkup(BUTTONS, resize_keyboard=True))

def run_bot():
    global application
    application = Application.builder().token(BOT_TOKEN).build()
    
    setup_google_sheets()
    load_patients()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("🤖 Bot running...")
    
    # Start background tasks
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    # Start scheduled jobs in background
    loop.create_task(scheduled_jobs())
    
    # Run the bot
    application.run_polling()

if __name__ == "__main__":
    run_bot()