import re
import pandas as pd
from rapidfuzz import process, fuzz
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
from openai import OpenAI, ChatCompletion
from dotenv import load_dotenv
import os
import io
import csv
import json

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

def extract_tasks(natural_text: str, model: str = "gpt-4o-mini"):
    """
      Convert natural language time logging into CSV format with two columns: task, hours.

      Parameters
      ----------
      natural_text : str
          Free-form text like "Today is 23rd Sept. I did 2 hours of X, spent 3 hours on Y..."
      model : str
          OpenAI model to use (default: gpt-4o-mini).

      """

    system_prompt = """You are an assistant that extracts structured time log data.
  Return ONLY valid JSON: a dictionary containing two keys:
  1 'extracted_date': date mentioned by the user for log entry
  2. 'tasks': a list of objects with exactly two keys: task (string formatted as Title Case), hours (float with 1 decimal place).
  - Preserve the order tasks are mentioned.
  - Ignore dates, words like "today", "spent", "doing".
  - Ensure hours are numeric (no text like "two").
  - Examples:
  1. Today is 1st September 2025. I spent 2.5 hours doing <task 1>, then I did 1 hour of <task 2> and also I was involved in 2 hours of <task 3>
  Output:
    {
        'extracted_date': '01-Sept-2025',
        'tasks': [{'task': <task 1>, 'hours': 2.5},
                {'task': <task 2>, 'hours': 1},
                {'task': <task 3>, 'hours': 3}]
    }
  """

    user_prompt = f"Extract the tasks and hours from this text:\n\n{natural_text}"

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    response = client.chat.completions.create(
              model=model,
              messages=[{"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}],
              temperature=0)

    content = response.choices[0].message.content.strip()

    try:
        result = json.loads(content)
    except json.JSONDecodeError:
        raise ValueError(f"Model did not return valid JSON:\n{content}")


    return result['extracted_date'], result['tasks']

# -----------------------------
# STEP 5: MAP TASKS TO CHARGECODES
# -----------------------------

def map_to_chargecodes(tasks, date, ref_df):
    """
    tasks: list of dicts like [{"task": "...", "hours": 2}, ...]
    date: date (kept as-is)
    ref_df: pandas.DataFrame with columns: "Description", "Note", "WBS element"

    Returns: list of mappings with row_number (0-based), row_index (df index label), chargecode_id, score, etc.
    """
    mapped = []
    hours_till_now = 0.0

    choices = ref_df["Description"].tolist()

    for entry in tasks:
        task = str(entry.get("task", "")).strip()
        hours = entry.get("hours", 0)
        # rapidfuzz.process.extractOne returns (match_string, score, index)
        match_result = process.extractOne(task, choices, scorer=fuzz.token_set_ratio)

        match_str, score, pos = match_result

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

    extracted_date, tasks = extract_tasks(transcription)

    print(f"Parsed: {tasks}, {extracted_date}")

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
