import re
import pandas as pd
from rapidfuzz import process, fuzz
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
from openai import OpenAI
from dotenv import load_dotenv
import os

# -----------------------------
# CONFIG
# -----------------------------
REFERENCE_SHEET_ID = open("reference_sheet.txt").read().strip()
TIMESHEET_SHEET_ID = open("timesheet.txt").read().strip()
load_dotenv()

# -----------------------------
# STEP 1: CONNECT TO GOOGLE SHEETS
# -----------------------------
def connect_sheets():
    scope = ["https://spreadsheets.google.com/feeds",
             "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name("service_account.json", scope)
    client = gspread.authorize(creds)
    return client

# -----------------------------
# STEP 2: LOAD REFERENCE DATA
# -----------------------------
def load_reference(client):
    sheet = client.open_by_key(REFERENCE_SHEET_ID).sheet1
    data = pd.DataFrame(sheet.get_all_records())
    return data

# -----------------------------
# STEP 3: TRANSCRIBE VOICE NOTE
# -----------------------------
def transcribe_audio(file_path: str) -> str:

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    with open(file_path, "rb") as audio_file:
        transcription = client.audio.transcriptions.create(
            model="whisper-1",   # or "whisper-1"
            file=audio_file
        )
    return transcription.text

# -----------------------------
# STEP 4: PARSE HOURS & TASKS
# Example: "I spent 4 hours on data analysis, 2 hours in calls"
# -----------------------------

def parse_decimal_words(text, word_to_num):
    """
    Converts phrases like 'one point five', 'another point five', 'point two five' into numeric floats.
    """
    # Match patterns like "one point five", "another point two five", "point five"
    decimal_pattern = r'(?:another\s+)?(?:(zero|one|two|three|four|five|six|seven|eight|nine|ten)?\s*)?point\s+(two|five|seven|zero|one|three|four|six|eight|nine)(?:\s+(five|two|zero|one|three|four|six|seven|eight|nine))?'

    def repl(match):
        whole = match.group(1)
        d1 = match.group(2)
        d2 = match.group(3)

        val = 0
        if whole:
            val += word_to_num[whole]
        val += word_to_num[d1] / 10
        if d2:
            val += word_to_num[d2] / 100
        return str(val)

    return re.sub(decimal_pattern, repl, text)

def parse_tasks(transcription):
# Dictionary to map word numbers to digits
    # Dictionary to map word numbers to digits
    word_to_num = {
        'zero': 0, 'one': 1, 'two': 2, 'three': 3, 'four': 4,
        'five': 5, 'six': 6, 'seven': 7, 'eight': 8, 'nine': 9,
        'ten': 10
    }

    transcription = parse_decimal_words(transcription.lower(), word_to_num)

    # Extract date from transcription
    # Pattern matches various date formats: DD/MM/YYYY, DD-MM-YYYY, Month DD, YYYY, etc.

    date_patterns = [
        (r'(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{4})', '%d/%m/%Y'),  # DD/MM/YYYY or DD-MM-YYYY
        (r'(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{2})', '%d/%m/%y'),   # DD/MM/YY or DD-MM-YY
        (r'(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2}),?\s+(\d{4})', '%B %d %Y'),  # Month DD, YYYY
        (r'(\d{1,2})\s+(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{4})', '%d %B %Y'),  # DD Month YYYY
        (r'(\d{1,2})(st|nd|rd|th)\s+(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{4})', '%d %B %Y')  # DDth Month YYYY
    ]

    extracted_date = None
    for pattern, date_format in date_patterns:
        match = re.search(pattern, transcription.lower())
        if match:
            matched_text = match.group(0)
            try:
                # Handle ordinal numbers (1st, 2nd, 3rd, 4th) by removing the suffix
                if 'st' in matched_text or 'nd' in matched_text or 'rd' in matched_text or 'th' in matched_text:
                    matched_text = re.sub(r'(\d+)(st|nd|rd|th)', r'\1', matched_text)

                # Parse the date and convert to YYYY-MM-DD format
                parsed_date = datetime.strptime(matched_text, date_format)
                extracted_date = parsed_date.strftime("%Y-%m-%d")
                break
            except ValueError:
                raise ValueError("Please enter a date in valid date formats")

    # Create pattern that matches both digits and word numbers
    word_numbers = '|'.join(word_to_num.keys())

    pattern = rf"({word_numbers}|\d+(\.\d+)?+)\s*hour[s]?\s*(?:of|on|for|doing)?\s*([\w\s]+)"

    matches = re.findall(pattern, transcription.lower())
    tasks = []

    for hours_str, _, task in matches:
        # Convert word numbers to digits if needed
        if hours_str in word_to_num:
            hours = float(word_to_num[hours_str])
        else:
            hours = float(hours_str)

        tasks.append({"task": task.strip(), "hours": hours})

    return tasks, extracted_date

# -----------------------------
# STEP 5: MAP TASKS TO CHARGECODES
# -----------------------------
from rapidfuzz import process, fuzz

def map_to_chargecodes(tasks, date, ref_df):
    """
    tasks: list of dicts like [{"task": "...", "hours": 2}, ...]
    date: date (kept as-is)
    ref_df: pandas.DataFrame with columns: "Description", "Note", "WBS element"

    Returns: list of mappings with row_number (0-based), row_index (df index label), chargecode_id, score, etc.
    """
    mapped = []
    sep = " ||| "  # unlikely to appear in descriptions/notes
    hours_till_now = 0.0

    # Build choices: safe string for each row
    #descs = ref_df["Description"].fillna("").astype(str).tolist()
    #notes = ref_df["Note"].fillna("").astype(str).tolist()
    choices = ref_df["Description"].tolist()

    for entry in tasks:
        task = str(entry.get("task", "")).strip()
        hours = entry.get("hours", 0)
        print("hello1")
        # rapidfuzz.process.extractOne returns (match_string, score, index)
        match_result = process.extractOne(task, choices, scorer=fuzz.token_set_ratio)

        match_str, score, pos = match_result
        print("hello2")

        # get df row by integer location pos
        row_label = ref_df.index[pos]           # actual index label (could be non-int)
        chargecode_id = ref_df.iloc[pos]["WBS element"]

        mapped.append({
            "date": date,
            "chargecode_id": chargecode_id,
            "hours": hours,
            "matched_with": match_str,
            "score": score,
        })

        hours_till_now = hours_till_now + hours
        print("Hello4")

    # Scale total hours to 8 hours in a day

    if hours_till_now != 8.0:
        scaling_factor = 8.0 / hours_till_now
        for entry in mapped:
            entry["hours"] = int((entry["hours"] * scaling_factor) * 100) / 100

    return mapped

# -----------------------------
# STEP 6: APPEND TO TIMESHEET
# -----------------------------
def append_timesheet(client, entries):
    sheet = client.open_by_key(TIMESHEET_SHEET_ID).sheet1
    for e in entries:
        sheet.append_row([e["date"], e["chargecode_id"], e["hours"], e["matched_with"], e["score"]])

# -----------------------------
# MAIN
# -----------------------------
def run_workflow(voice_file):

    print("🔄 Starting workflow...")

    client = connect_sheets()
    ref_df = load_reference(client)

    print("🎙️ Transcribing audio...")

    transcription = transcribe_audio(voice_file)

    print("Transcript:", transcription)

    print("📝 Parsing tasks...")

    tasks, extracted_date = parse_tasks(transcription)

    print("Parsed:", tasks)

    print("🔍 Mapping to chargecodes...")

    mapped_entries = map_to_chargecodes(tasks, extracted_date, ref_df)
    for m in mapped_entries:
        print(f"{m['hours']}h → {m['chargecode_id']} ({m['matched_with']}, score={m['score']})")

    print("📊 Appending to timesheet...")
    append_timesheet(client, mapped_entries)

    print("✅ Workflow complete.")

    return {
        "transcription": transcription,
        "date": extracted_date,
        "tasks": tasks
    }
