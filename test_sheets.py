import os
import json
from dotenv import load_dotenv
import gspread
from oauth2client.service_account import ServiceAccountCredentials

load_dotenv()

GOOGLE_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")

print("Testing Google Sheets connection...")
print(f"Sheet ID: {GOOGLE_SHEET_ID}")

try:
    # Parse JSON
    creds_dict = json.loads(GOOGLE_JSON)
    print("✅ JSON parsed successfully")
    
    # Setup credentials
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    print("✅ Credentials created")
    
    # Authorize
    client = gspread.authorize(creds)
    print("✅ Client authorized")
    
    # Open sheet
    sheet = client.open_by_key(GOOGLE_SHEET_ID)
    print(f"✅ Sheet opened: {sheet.title}")
    
    # Try to write a test row
    log_sheet = sheet.worksheet("Logs")
    print("✅ Logs tab found")
    
    # Append test row
    from datetime import datetime
    log_sheet.append_row([
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "TEST",
        "TEST_CONNECTION",
        "",
        "test_script"
    ])
    print("✅ Test row written to Logs tab")
    print("\n🎉 Google Sheets is working correctly!")
    
except Exception as e:
    print(f"❌ ERROR: {e}")