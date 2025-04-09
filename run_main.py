import subprocess
import time
from datetime import datetime, timedelta
import os
import psutil
import re
import requests

def kill_chrome_except_python():
    current_pid = os.getpid()
    for proc in psutil.process_iter(attrs=['pid', 'name']):
        if 'chrome' in proc.info['name'].lower() and proc.info['pid'] != current_pid:
            os.kill(proc.info['pid'], 9)

def cleanup_old_logs(log_dir):
    log_pattern = re.compile(r"log_(\d{4}-\d{2}-\d{2})_\d{2}-\d{2}-\d{2}\.txt")
    cutoff_date = datetime.now() - timedelta(days=7)

    for filename in os.listdir(log_dir):
        match = log_pattern.match(filename)
        if match:
            file_date = datetime.strptime(match.group(1), "%Y-%m-%d")
            if file_date < cutoff_date:
                os.remove(os.path.join(log_dir, filename))

def send_log_to_discord(log_filepath):
    webhook_url = "https://discordapp.com/api/webhooks/1352626299767885946/pKG3WtU7H5hfJ6IxmYNrpZSUV_L0zqvlBPXPnHFcLIThH3S49nQmY9UI16WmMtBzyun6"

    try:
        with open(log_filepath, "rb") as f:
            files = {
                "file": (os.path.basename(log_filepath), f, "text/plain")
            }
            data = {
                "content": f"`{os.path.basename(log_filepath)}`"
            }
            response = requests.post(webhook_url, data=data, files=files)

    except Exception as e:
        print(f"디스코드 전송 중 예외 발생: {e}", flush=True)

def run_main():
    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)

    while True:
        cleanup_old_logs(log_dir)

        now = datetime.now().hour
        if 0 <= now < 6:
            print("현재 시간이 밤 12시 ~ 오전 6시이므로 6시간 동안 대기합니다.", flush=True)
            WEBHOOK_URL = "https://discordapp.com/api/webhooks/1346451155605000223/-6UvPYWZS8BzZXSTg_-6EJYag8xK8z851bvxQDPOem7zVzB1MAWlpBTO3Z3NCzZ879gN"
            message = "------------------------------------------------------------------------------------------------------------"
            requests.post(WEBHOOK_URL, json={"content": message})
            time.sleep(6 * 60 * 60)
            continue

        timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        log_filename = os.path.join(log_dir, f"log_{timestamp}.txt")

        with open(log_filename, "w", encoding="utf-8") as logfile:
            process = subprocess.Popen(
                ["python3", "-u", "main.py"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True
            )

            for line in process.stdout:
                print(line, end='', flush=True)
                logfile.write(line)
                logfile.flush()

            process.wait()

        kill_chrome_except_python()
        print("\n메인 함수 종료됨. 10초 후 다시 실행\n\n\n", flush=True)

        send_log_to_discord(log_filename)

        time.sleep(10)

if __name__ == "__main__":
    run_main()
