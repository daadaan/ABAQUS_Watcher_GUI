import os
import time
import requests
import sys
import re
import subprocess
import matplotlib.pyplot as plt

# ================= CONFIGURATION =================
# IMPORT SECRETS
# This pulls variables from secrets.py
from secrets import BOT_TOKEN, CHAT_ID, WATCH_DIR, HEARTBEAT_INTERVAL
# =================================================

print(f"--- Abaqus Multi-Job Watchman Active ---")
print(f"Monitoring: {WATCH_DIR}")

last_update_id = 0
job_heartbeats = {} # Tracks running jobs and their last update time

def send_telegram(message, icon="ℹ️", image_path=None, silent=True):
    base_url = f"https://api.telegram.org/bot{BOT_TOKEN}"
    payload = {
        "chat_id": CHAT_ID, 
        "parse_mode": "Markdown", 
        "disable_notification": silent
    }

    try:
        if image_path and os.path.exists(image_path):
            url = f"{base_url}/sendPhoto"
            payload['caption'] = f"{icon} {message}"
            with open(image_path, 'rb') as img:
                requests.post(url, data=payload, files={'photo': img}, timeout=20)
            try: os.remove(image_path)
            except: pass
        else:
            url = f"{base_url}/sendMessage"
            payload['text'] = f"{icon} {message}"
            requests.post(url, data=payload, timeout=10)
        
        print(f"-> Sent: {message.splitlines()[0]}")
    except Exception as e:
        print(f"-> Network Error: {e}")

def get_detailed_progress(job_name):
    """Parses .sta for Start Time, Step, Frame, Energies, and Stable Dt."""
    sta_file = os.path.join(WATCH_DIR, job_name + ".sta")
    if not os.path.exists(sta_file): return "Waiting for data..."
    
    current_step = "1" 
    frame_info = "No frames."
    data_info = "Reading..."
    start_time = ""
    
    found_step = False
    found_frame = False
    found_data = False
    found_start = False

    try:
        with open(sta_file, 'r') as f:
            lines = f.readlines()
            
            # 1. FIND START TIME (Top of file)
            for i in range(min(5, len(lines))):
                if "DATE" in lines[i] and "TIME" in lines[i]:
                    try:
                        parts = lines[i].split("DATE")[-1].split("TIME")
                        start_time = f"📅 {parts[0].strip()} {parts[1].strip()}\n"
                        found_start = True
                    except: pass
                if found_start: break

            # 2. FIND PROGRESS (Bottom of file)
            for line in reversed(lines):
                parts = line.split()
                
                # Data Line extraction
                if not found_data and len(parts) > 7 and parts[0].isdigit():
                    # Explicit Mapping:
                    # Col 1: Step Time
                    # Col 4: Stable Increment (dt)
                    # Col 6: Kinetic Energy (KE)
                    
                    time_val = parts[1]
                    dt_val = parts[4]  # <--- NEW: Stable Increment
                    ke_val = parts[6]
                    
                    data_info = f"Time: {time_val}s | dt: {dt_val} | KE: {ke_val}"
                    found_data = True

                # Frame Info
                if not found_frame and "ODB Field Frame Number" in line:
                    if len(parts) > 6:
                        current_frame = parts[4]
                        total_frames = parts[6]
                        frame_info = f"Frames: {current_frame}/{total_frames}"
                        found_frame = True

                # Current Step
                if not found_step and line.strip().startswith("STEP") and "ORIGIN" in line:
                    if len(parts) > 1 and parts[1].isdigit():
                        current_step = parts[1]
                        found_step = True
                
                if found_step and found_frame and found_data:
                    break

        return f"{start_time}Step {current_step} | {data_info}\n📁 {frame_info}"

    except: return "Error reading file."

def generate_convergence_plot(job_name):
    sta_file = os.path.join(WATCH_DIR, job_name + ".sta")
    script_folder = os.path.join(WATCH_DIR, "job_watcher")
    if not os.path.exists(script_folder): os.makedirs(script_folder)

    if not os.path.exists(sta_file): return None
    
    step_times, increments = [], []
    try:
        with open(sta_file, 'r') as f:
            for line in f:
                parts = line.split()
                # Explicit mapping: Col 1 (Step Time), Col 4 (Stable Inc)
                if len(parts) > 7 and parts[0].isdigit():
                    try:
                        step_times.append(float(parts[1]))
                        increments.append(float(parts[4]))
                    except: continue
        
        if not step_times: return None
        
        plt.figure(figsize=(10, 5))
        plt.plot(step_times, increments, marker='', linestyle='-', color='#d62728', linewidth=1.0)
        plt.title(f"Stability: {job_name}")
        plt.xlabel("Step Time (s)")
        plt.ylabel("Stable Increment (s)")
        plt.yscale('log')
        plt.grid(True, which="both", alpha=0.4)

        img_path = os.path.join(script_folder, f"plot_{job_name}.png")
        plt.savefig(img_path, bbox_inches='tight')
        plt.close()
        return img_path
    except: return None

def check_for_commands():
    global last_update_id
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    try:
        params = {'offset': last_update_id + 1, 'timeout': 2}
        response = requests.get(url, params=params, timeout=5)
        data = response.json()
        if not data.get('ok'): return

        for result in data.get('result', []):
            last_update_id = result['update_id']
            sender_id = str(result.get('message', {}).get('chat', {}).get('id'))
            if sender_id != str(CHAT_ID): continue

            text = result.get('message', {}).get('text', '').strip()
            
            # --- COMMAND 1: /status all (Summary, No Graphs) ---
            if text == "/status all":
                running_jobs = list(job_heartbeats.keys())
                if not running_jobs:
                    send_telegram("No jobs currently running.", "zzz", silent=True)
                else:
                    msg_lines = []
                    for job in running_jobs:
                        details = get_detailed_progress(job)
                        # Add a clean separator line
                        msg_lines.append(f"🔹 *{job}*\n{details}")
                    
                    full_msg = "\n\n".join(msg_lines)
                    send_telegram(full_msg, "📋", silent=True)

            # --- COMMAND 2: /status JobName (Detailed + Graph) ---
            elif text.startswith("/status "):
                parts = text.split()
                if len(parts) > 1:
                    job_name = parts[1]
                    # Generate plot and details
                    plot = generate_convergence_plot(job_name)
                    details = get_detailed_progress(job_name)
                    
                    if plot:
                        send_telegram(f"**Status:** `{job_name}`\n{details}", "📈", image_path=plot, silent=True)
                    else:
                        send_telegram(f"Could not find data for `{job_name}` (or job not started).", "⚠️", silent=True)
            
            # --- COMMAND 3: /kill JobName (Strict) ---
            elif text.startswith("/kill"):
                parts = text.split()
                if len(parts) > 1:
                    target = parts[1]
                    if target.lower() == "all":
                         send_telegram("⚠️ Safety: `/kill all` is not allowed. Specify a job name.", "🛑", silent=True)
                    else:
                         terminate_job(target)
                else:
                    send_telegram("Usage: `/kill JobName`", "🤖", silent=True)

    except: pass

def terminate_job(job_name):
    send_telegram(f"Killing `{job_name}`...", "💀", silent=True)
    subprocess.call(f"abaqus terminate job={job_name}", shell=True, cwd=WATCH_DIR)

def get_job_status(job_name):
    sta_file = os.path.join(WATCH_DIR, job_name + ".sta")
    if not os.path.exists(sta_file): return "FINISHED", "⚠️", "No details."
    try:
        with open(sta_file, 'r') as f:
            content = f.read()
            if "COMPLETED SUCCESSFULLY" in content: return "SUCCESS", "✅", "Converged."
            elif "ERROR" in content: return "ABORTED", "🚨", "Check .msg file."
            else: return "TERMINATED", "⚠️", "Job stopped."
    except: pass
    return "UNKNOWN", "❓", "Error"

# --- MAIN LOOP ---
while True:
    try:
        check_for_commands()
        
        files = os.listdir(WATCH_DIR)
        current_locks = [f for f in files if f.endswith('.lck')]
        active_names = [l.replace('.lck', '') for l in current_locks]
        
        # 1. NEW JOBS
        for job_name in active_names:
            if job_name not in job_heartbeats:
                job_heartbeats[job_name] = time.time()
                send_telegram(f"**Started:** `{job_name}`", "🚀", silent=True)

        # 2. FINISHED JOBS
        for job_name in list(job_heartbeats):
            if job_name not in active_names:
                status, icon, details = get_job_status(job_name)
                plot = generate_convergence_plot(job_name)
                # Loud only for Success/Abort
                is_silent = not ("SUCCESS" in status or "ABORTED" in status)
                send_telegram(f"**Job:** `{job_name}`\n**Result:** {status}\n{details}", icon, image_path=plot, silent=is_silent)
                del job_heartbeats[job_name]

        # 3. HEARTBEAT (Per Job, Text Only)
        current_time = time.time()
        for job_name in active_names:
            if (current_time - job_heartbeats[job_name]) > HEARTBEAT_INTERVAL:
                details = get_detailed_progress(job_name)
                send_telegram(f"**Running:** `{job_name}`\n{details}", "⏳", silent=True)
                job_heartbeats[job_name] = current_time

        time.sleep(3)
    except KeyboardInterrupt: sys.exit()
    except Exception as e: 
        print(f"Error: {e}")
        time.sleep(5)