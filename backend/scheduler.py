import time
import fetch_data
import datetime

INTERVAL_HOURS = 6
INTERVAL_SECONDS = INTERVAL_HOURS * 3600

def run_scheduler():
    print(f"Scheduler started. Will fetch data every {INTERVAL_HOURS} hours.")
    while True:
        try:
            fetch_data.fetch_all_data()
            print(f"[{datetime.datetime.now()}] Next fetch in {INTERVAL_HOURS} hours.")
        except Exception as e:
            print(f"[{datetime.datetime.now()}] Error in scheduler: {e}")
        
        time.sleep(INTERVAL_SECONDS)

if __name__ == "__main__":
    run_scheduler()
