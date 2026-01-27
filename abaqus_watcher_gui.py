"""
Abaqus Watcher GUI
==================

A modern, cross-platform Desktop Application to monitor SIMULIA Abaqus jobs remotely via Telegram.
This tool watches a specified directory for Abaqus lock files (.lck) and status files (.sta),
providing real-time updates, convergence plots, and remote termination capabilities.

Features:
- **Real-time Monitoring:** Detects new jobs, completions, and errors.
- **Remote Control:** Check status or kill jobs via Telegram commands.
- **Convergence Plots:** Generates Step Time vs. Increment Size graphs.
- **Secure Storage:** Uses Windows Credential Locker (Keyring) for sensitive tokens.
- **System Tray:** Minimizes to background for unobtrusive monitoring.

Dependencies:
    pip install customtkinter packaging requests matplotlib keyring pystray Pillow

Author: Souvik Biswas
License: MIT
Repository: https://github.com/daadaan/ABAQUS_watcher_project
"""

import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox, filedialog
import json
import os
import threading
import time
import requests
import subprocess
import matplotlib
import matplotlib.pyplot as plt
import keyring
from datetime import datetime
import pystray
from PIL import Image, ImageDraw
import webbrowser
from packaging import version

# Use non-interactive backend for plots to prevent GUI thread blocking
matplotlib.use('Agg')

# ================= CONFIGURATION =================
CONFIG_FILE = "app_config.json"
APP_NAME = "AbaqusWatcherGUI"
GITHUB_REPO = "daadaan/ABAQUS_watcher_project"  # GitHub API Endpoint for updates
CURRENT_VERSION = "1.0.0"
# =================================================

class AbaqusWatcherApp(ctk.CTk):
    """
    Main Application Class.
    Inherits from customtkinter.CTk to provide a modern dark/light mode interface.
    """
    def __init__(self):
        super().__init__()

        # --- Window Setup ---
        self.title(f"Abaqus Watcher GUI v{CURRENT_VERSION}")
        self.geometry("320x580")  # Optimized height for footer
        self.resizable(False, False)
        
        # Match main window background to TabView for seamless look
        self.configure(fg_color=("gray95", "gray15"))

        # --- Theme Setup ---
        ctk.set_appearance_mode("System")  # Follows Windows Dark/Light mode
        ctk.set_default_color_theme("blue")
        
        # --- Typography Constants ---
        self.font_head = ("Roboto Medium", 14)
        self.font_body = ("Roboto", 12)
        self.font_mono = ("Consolas", 11)  # Monospace for logs
        self.font_bold = ("Roboto", 12, "bold")
        self.font_small = ("Roboto", 10)

        # --- State Variables ---
        self.watcher_thread = None
        self.stop_event = threading.Event()  # Controls the background thread
        self.is_running = False
        self.job_heartbeats = {}  # Tracks last update time for each job
        self.last_telegram_update_id = 0
        self.tray_icon = None
        self.tray_thread = None

        # --- Config Variables (Linked to UI) ---
        self.var_tray_enabled = ctk.BooleanVar(value=False)
        self.var_theme = ctk.StringVar(value="System")

        # --- Initialization ---
        self.create_ui()
        self.setup_tray_icon()
        self.load_config()

        # --- Window Event Bindings ---
        self.protocol('WM_DELETE_WINDOW', self.on_closing)  # Handle X button
        self.bind("<Unmap>", self.check_minimize_event)     # Handle Minimize

    def create_ui(self):
        """Builds the Tabbed Interface and Layout."""
        
        # Initialize TabView with custom colors for seamless integration
        self.tabview = ctk.CTkTabview(self, width=300, height=520, corner_radius=10,
                              fg_color=("gray95", "gray15"), 
                              segmented_button_fg_color=("gray85", "gray25"), 
                              segmented_button_unselected_color=("gray85", "gray25"),
                              segmented_button_selected_color=("#ACC9F7", "#0947CE"),
                              segmented_button_selected_hover_color=("#93AADD", "#1A67E3"),
                              text_color=("gray40", "gray80")
                              )
        self.tabview.pack(padx=0, pady=0, fill="both", expand=True)
        self.tabview._segmented_button.configure(font=self.font_bold)

        # Create Tabs
        self.tab_monitor = self.tabview.add("Monitor")
        self.tab_settings = self.tabview.add("Config")
        self.tab_help = self.tabview.add("Help")

        # --- GLOBAL FOOTER (COPYRIGHT) ---
        # Placed in the main window scope to appear on all tabs
        lbl_copyright = ctk.CTkLabel(self, text=f"© {time.localtime().tm_year} Souvik Biswas", 
                                     font=("Segoe UI", 10), text_color=("gray50", "gray50"))
        lbl_copyright.pack(side="bottom", pady=(0, 5))

        # ================= TAB 1: MONITOR UI =================
        
        # Status Card (Visual indicator of running state)
        self.frame_status = ctk.CTkFrame(self.tab_monitor, corner_radius=8, fg_color=("gray90", "gray13")) 
        self.frame_status.pack(pady=(20, 15), padx=10, fill="x")
        
        self.lbl_status = ctk.CTkLabel(self.frame_status, text="STOPPED", text_color="#EF4444", font=("Roboto", 14, "bold"))
        self.lbl_status.pack(pady=10)

        # Main Controls
        self.btn_start = ctk.CTkButton(self.tab_monitor, text="START WATCHER", command=self.toggle_watcher, 
                                       fg_color="#15803d", hover_color="#14532d",
                                       font=self.font_bold, height=45, corner_radius=6)
        self.btn_start.pack(padx=10, pady=5, fill="x")

        self.btn_ping = ctk.CTkButton(self.tab_monitor, text="Test Connection", command=self.ping_test, 
                                      fg_color="transparent", border_width=1, border_color=("gray70", "gray40"), 
                                      text_color=("gray10", "gray90"), hover_color=("gray90", "gray20"),
                                      font=self.font_body, height=30)
        self.btn_ping.pack(padx=10, pady=10, fill="x")

        # Log Console
        ctk.CTkLabel(self.tab_monitor, text="Live Activity", anchor="w", font=self.font_bold, text_color=("gray40", "gray60")).pack(padx=10, pady=(15, 2), fill="x")

        self.console = ctk.CTkTextbox(self.tab_monitor, width=280, height=200, font=self.font_mono, 
                                      fg_color=("white", "black"), text_color=("black", "white"), corner_radius=6, border_width=1, border_color=("gray80", "gray30"))
        self.console.pack(padx=5, pady=0, fill="both", expand=True)
        self.console.configure(state="disabled")

        # ================= TAB 2: CONFIG UI =================
        
        self.frame_cfg = ctk.CTkFrame(self.tab_settings, fg_color="transparent")
        self.frame_cfg.pack(fill="both", expand=True, padx=5)

        # 1. Credentials Input (Masked)
        self.add_input(self.frame_cfg, "Bot Token", "entry_token", secret=True)
        self.add_input(self.frame_cfg, "Chat ID", "entry_chat_id", secret=True)

        # 2. Directory Selector with Browse Button
        ctk.CTkLabel(self.frame_cfg, text="ABAQUS Temp Directory", anchor="w", font=self.font_bold, text_color=("gray50", "gray50")).pack(padx=5, pady=(5,0), fill="x")
        
        self.frame_dir = ctk.CTkFrame(self.frame_cfg, fg_color="transparent")
        self.frame_dir.pack(padx=5, pady=0, fill="x")
        
        self.entry_dir = ctk.CTkEntry(self.frame_dir, height=34, font=self.font_body)
        self.entry_dir.pack(side="left", fill="x", expand=True, padx=(0, 5))
        
        self.btn_browse = ctk.CTkButton(self.frame_dir, text="📂", width=34, height=34, command=self.browse_directory,
                                        fg_color=("gray80", "gray30"), hover_color=("gray70", "gray40"), text_color=("black", "white"))
        self.btn_browse.pack(side="right")

        # 3. Settings
        self.add_input(self.frame_cfg, "Heartbeat (s)", "entry_heartbeat", default="3600")

        # 4. Appearance Options
        self.frame_opts = ctk.CTkFrame(self.frame_cfg, fg_color="transparent")
        self.frame_opts.pack(fill="x", pady=15)
        
        ctk.CTkLabel(self.frame_opts, text="Theme", font=self.font_body).pack(side="left", padx=5)
        self.opt_theme = ctk.CTkOptionMenu(self.frame_opts, values=["System", "Dark", "Light"], width=100, height=28,
                                           variable=self.var_theme, command=self.change_theme, font=self.font_body)
        self.opt_theme.pack(side="left", padx=10)

        self.switch_tray = ctk.CTkSwitch(self.frame_cfg, text="Minimize to Tray", font=self.font_body,
                                         variable=self.var_tray_enabled, height=24, width=50)
        self.switch_tray.pack(padx=5, pady=(5, 10), anchor="w")

        # Save Button
        self.btn_save = ctk.CTkButton(self.tab_settings, text="Save Settings", command=self.save_config, 
                                      fg_color="#2563EB", hover_color="#3B82F6", 
                                      font=self.font_bold, height=40)
        self.btn_save.pack(side="top", padx=5, pady=(5, 5), fill="x")

        # --- UTILITY FOOTER (Config Tab) ---
        self.frame_utils = ctk.CTkFrame(self.tab_settings, fg_color="transparent")
        self.frame_utils.pack(side="bottom", fill="x", pady=(10, 0))

        # Grid Layout forFooter buttons
        self.frame_utils.columnconfigure(0, weight=1)
        self.frame_utils.columnconfigure(1, weight=1)

        # Button 1: Clear Data
        btn_clear = ctk.CTkButton(self.frame_utils, text="Clear Data", command=self.clear_config,
                                  fg_color="transparent", border_width=2, border_color="#EF4444", text_color="#EF4444",
                                  hover_color=("#FEE2E2", "#450a0a"), font=self.font_small, height=28)
        btn_clear.grid(row=0, column=0, padx=5, pady=2, sticky="ew")

        # Button 2: Check Updates
        btn_update = ctk.CTkButton(self.frame_utils, text="Check Updates", command=self.check_updates,
                                   fg_color="transparent", border_width=2, border_color="#3B82F6", text_color="#3B82F6",
                                   hover_color=("#DBEAFE", "#172554"), font=self.font_small, height=28)
        btn_update.grid(row=0, column=1, padx=5, pady=2, sticky="ew")

        # Link 1: GitHub Repo
        btn_repo = ctk.CTkButton(self.frame_utils, text="GitHub Repo", 
                                 fg_color="transparent", hover=False, text_color=("gray50", "gray60"), font=("Segoe UI", 12, "underline"),
                                 height=20, command=lambda: webbrowser.open(f"https://github.com/{GITHUB_REPO}"))
        btn_repo.grid(row=1, column=0, padx=5, pady=(2,0), sticky="ew")

        # Link 2: Report Issue
        btn_issue = ctk.CTkButton(self.frame_utils, text="Report Issue", 
                                  fg_color="transparent", hover=False, text_color="#EF4444", font=("Segoe UI", 12, "underline"),
                                  height=20, command=lambda: webbrowser.open(f"https://github.com/{GITHUB_REPO}/issues"))
        btn_issue.grid(row=1, column=1, padx=5, pady=(2,0), sticky="ew")

        # ================= TAB 3: HELP UI =================
        help_text = (
            "COMMANDS\n"
            "──────────────────────────────\n"
            "/status all\n"
            "List all running jobs.\n\n"
            "/status Job-1\n"
            "Stats + Convergence Plot.\n\n"
            "/kill Job-1\n"
            "Terminate job.\n\n\n"
            "INFO\n"
            "──────────────────────────────\n"
            "• Enable 'Minimize to Tray' to\n"
            "  keep running in background.\n"
            "• Credentials secured via\n"
            "  Windows Credential Locker."
        )
        lbl_help = ctk.CTkLabel(self.tab_help, text=help_text, justify="left", font=("Consolas", 12), anchor="nw", 
                                text_color=("gray20", "gray80"))
        lbl_help.pack(padx=15, pady=20, fill="both", expand=True)

    def add_input(self, parent, label, var_name, default="", secret=False):
        """Helper to create labeled input fields with optional password toggles."""
        ctk.CTkLabel(parent, text=label, anchor="w", font=self.font_bold, text_color=("gray50", "gray50")).pack(padx=5, pady=(5,0), fill="x")
        
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.pack(padx=5, pady=0, fill="x")
        
        entry = ctk.CTkEntry(frame, height=34, font=self.font_body)
        entry.pack(side="left", fill="x", expand=True)
        entry.insert(0, default)
        
        if secret:
            entry.configure(show="●") 
            btn_eye = ctk.CTkButton(frame, text="👁", width=34, height=34, 
                                    fg_color=("gray80", "gray30"), hover_color=("gray70", "gray40"), 
                                    text_color=("black", "white"),
                                    command=lambda: self.toggle_password(entry, btn_eye))
            btn_eye.pack(side="right", padx=(5, 0))
            
        setattr(self, var_name, entry)

    def toggle_password(self, entry, btn):
        """Toggles entry visibility between masked and plain text."""
        if entry.cget("show") == "●":
            entry.configure(show="")
            btn.configure(text="✕") # Icon when visible
        else:
            entry.configure(show="●")
            btn.configure(text="👁") # Icon when hidden

    def browse_directory(self):
        """Opens file dialog to select the Abaqus Temp directory."""
        dir_path = filedialog.askdirectory()
        if dir_path:
            self.entry_dir.delete(0, 'end')
            self.entry_dir.insert(0, dir_path)

    def check_updates(self):
        """Queries GitHub API to check for new releases."""
        def _check():
            try:
                url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
                resp = requests.get(url, timeout=3)
                if resp.status_code == 200:
                    data = resp.json()
                    latest_ver = data['tag_name'].replace('v', '')
                    if version.parse(latest_ver) > version.parse(CURRENT_VERSION):
                        if messagebox.askyesno("Update Available", f"New version {latest_ver} is available.\nOpen download page?"):
                            webbrowser.open(data['html_url'])
                    else:
                        messagebox.showinfo("Up to Date", f"You are running the latest version ({CURRENT_VERSION}).")
                else:
                    messagebox.showerror("Error", "Could not fetch update info.")
            except Exception as e:
                print(e)
                messagebox.showerror("Error", "Network error while checking updates.")
        
        threading.Thread(target=_check).start()

    # --- SYSTEM TRAY LOGIC ---
    def setup_tray_icon(self):
        """Configures the background system tray icon."""
        image = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.ellipse([10, 10, 54, 54], fill=(16, 185, 129)) 
        
        menu = pystray.Menu(
            pystray.MenuItem("Open Monitor", self.show_window_from_tray, default=True),
            pystray.MenuItem("Quit", self.quit_app)
        )
        self.tray_icon = pystray.Icon("AbaqusWatcher", image, "Abaqus Watcher", menu)

    def check_minimize_event(self, event):
        """Intercepts window minimize event to hide to tray if enabled."""
        if self.state() == 'iconic' and self.var_tray_enabled.get():
            self.withdraw()
            if not self.tray_thread or not self.tray_thread.is_alive():
                self.tray_thread = threading.Thread(target=self.tray_icon.run, daemon=True)
                self.tray_thread.start()

    def show_window_from_tray(self, icon=None, item=None):
        self.tray_icon.stop()
        self.after(0, self.deiconify)

    def change_theme(self, new_theme: str):
        ctk.set_appearance_mode(new_theme)

    def on_closing(self):
        self.quit_app()

    def quit_app(self, icon=None, item=None):
        if self.tray_icon: self.tray_icon.stop()
        self.stop_event.set()
        self.quit()

    # --- CONFIGURATION IO ---
    def load_config(self):
        """Loads non-sensitive config from JSON and secrets from Keyring."""
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r') as f:
                    data = json.load(f)
                    self.entry_dir.delete(0, 'end'); self.entry_dir.insert(0, data.get("watch_dir", ""))
                    self.entry_heartbeat.delete(0, 'end'); self.entry_heartbeat.insert(0, data.get("heartbeat", "3600"))
                    self.var_tray_enabled.set(data.get("tray_enabled", False))
                    theme = data.get("theme", "System")
                    self.var_theme.set(theme)
                    ctk.set_appearance_mode(theme)
            except: pass
        try:
            # Retrieve secrets safely from Windows Credential Manager
            t = keyring.get_password(APP_NAME, "bot_token")
            c = keyring.get_password(APP_NAME, "chat_id")
            if t: self.entry_token.delete(0, 'end'); self.entry_token.insert(0, t)
            if c: self.entry_chat_id.delete(0, 'end'); self.entry_chat_id.insert(0, c)
            if t: self.log("Credentials loaded.")
        except: self.log("Keyring error.")

    def save_config(self):
        """Saves settings to JSON and secrets to Keyring."""
        t, c = self.entry_token.get().strip(), self.entry_chat_id.get().strip()
        d, h = self.entry_dir.get().strip(), self.entry_heartbeat.get().strip()
        data = {"watch_dir": d, "heartbeat": h, "tray_enabled": self.var_tray_enabled.get(), "theme": self.var_theme.get()}
        
        try:
            if t: keyring.set_password(APP_NAME, "bot_token", t)
            if c: keyring.set_password(APP_NAME, "chat_id", c)
            with open(CONFIG_FILE, 'w') as f: json.dump(data, f)
            self.log("Settings saved.")
            tk.messagebox.showinfo("Saved", "Configuration updated.")
        except Exception as e:
            self.log(f"Save failed: {e}")

    def clear_config(self):
        """Wipes all local configuration and vault secrets."""
        if messagebox.askyesno("Reset", "Clear all data?"):
            try:
                if os.path.exists(CONFIG_FILE): os.remove(CONFIG_FILE)
                try: keyring.delete_password(APP_NAME, "bot_token")
                except: pass
                try: keyring.delete_password(APP_NAME, "chat_id")
                except: pass
                
                self.entry_token.delete(0, 'end'); self.entry_chat_id.delete(0, 'end')
                self.entry_dir.delete(0, 'end'); self.entry_heartbeat.delete(0, 'end')
                self.log("Data wiped.")
            except: pass

    # --- CORE WATCHER LOGIC ---
    def log(self, message):
        """Thread-safe logging to the UI console."""
        ts = datetime.now().strftime("%H:%M")
        self.after(0, lambda: self._update_console(f"[{ts}] {message}\n"))

    def _update_console(self, text):
        self.console.configure(state="normal")
        self.console.insert("end", text)
        self.console.see("end")
        self.console.configure(state="disabled")

    def ping_test(self):
        """Checks internet connectivity."""
        threading.Thread(target=lambda: self.log("Online.") if requests.get("https://google.com", timeout=3) else self.log("Offline.")).start()

    def toggle_watcher(self):
        """Starts or Stops the monitoring thread."""
        if self.is_running:
            self.stop_event.set()
            self.is_running = False
            self.btn_start.configure(text="START WATCHER", fg_color="#15803d", hover_color="#14532d")
            self.lbl_status.configure(text="STOPPED", text_color="#EF4444")
            self.log("Stopped.")
        else:
            self.stop_event.clear()
            self.is_running = True
            self.btn_start.configure(text="STOP WATCHER", fg_color="#EF4444", hover_color="#DC2626") # Red
            self.lbl_status.configure(text="RUNNING", text_color="#10B981") # Green
            self.watcher_thread = threading.Thread(target=self.run_loop, daemon=True)
            self.watcher_thread.start()

    def run_loop(self):
        """The main background loop that checks for Abaqus files and updates Telegram."""
        self.log("Monitoring active.")
        token = self.entry_token.get().strip()
        chat_id = self.entry_chat_id.get().strip()
        watch_dir = self.entry_dir.get().strip()
        try: hb = int(self.entry_heartbeat.get().strip())
        except: hb = 3600

        if not token or not chat_id or not os.path.exists(watch_dir):
            self.log("Err: Check config.")
            self.after(0, self.toggle_watcher)
            return

        self.job_heartbeats = {}
        while not self.stop_event.is_set():
            try:
                # 1. Check for incoming Telegram commands (/status, /kill)
                self.check_telegram(token, chat_id, watch_dir)
                
                # 2. Scan directory for Lock Files (.lck)
                files = os.listdir(watch_dir)
                active = [f.replace('.lck', '') for f in files if f.endswith('.lck')]

                # 3. Handle NEW jobs
                for job in active:
                    if job not in self.job_heartbeats:
                        self.job_heartbeats[job] = time.time()
                        self.log(f"New: {job}")
                        self.send_tg(token, chat_id, f"**Started:** `{job}`", "🚀")

                # 4. Handle FINISHED jobs (Lock file gone)
                for job in list(self.job_heartbeats):
                    if job not in active:
                        status, icon, det = self.get_status(watch_dir, job)
                        plot = self.gen_plot(watch_dir, job)
                        silent = not ("SUCCESS" in status or "ABORTED" in status)
                        self.send_tg(token, chat_id, f"**Job:** `{job}`\n**Result:** {status}\n{det}", icon, plot, silent)
                        self.log(f"End: {job} [{status}]")
                        del self.job_heartbeats[job]

                # 5. Handle HEARTBEATS (Regular updates for running jobs)
                now = time.time()
                for job in active:
                    if (now - self.job_heartbeats[job]) > hb:
                        det = self.get_details(watch_dir, job)
                        self.send_tg(token, chat_id, f"**Running:** `{job}`\n{det}", "⏳")
                        self.job_heartbeats[job] = now
                        self.log(f"Heartbeat: {job}")

                time.sleep(3)
            except Exception as e:
                self.log(f"Loop Err: {e}")
                time.sleep(5)

    # --- TELEGRAM HELPERS ---
    def send_tg(self, token, chat_id, text, icon, img=None, silent=True):
        """Sends text or images to Telegram API."""
        url = f"https://api.telegram.org/bot{token}"
        try:
            d = {"chat_id": chat_id, "parse_mode": "Markdown", "disable_notification": silent}
            if img and os.path.exists(img):
                d['caption'] = f"{icon} {text}"
                with open(img, 'rb') as f: requests.post(f"{url}/sendPhoto", data=d, files={'photo': f}, timeout=20)
                try: os.remove(img)
                except: pass
            else:
                d['text'] = f"{icon} {text}"
                requests.post(f"{url}/sendMessage", data=d, timeout=10)
        except: self.log("Telegram Err.")

    def check_telegram(self, token, chat_id, watch_dir):
        """Polls Telegram for user commands."""
        try:
            url = f"https://api.telegram.org/bot{token}/getUpdates"
            params = {'offset': self.last_telegram_update_id + 1, 'timeout': 1}
            r = requests.get(url, params=params, timeout=3).json()
            if not r.get('ok'): return
            for res in r.get('result', []):
                self.last_telegram_update_id = res['update_id']
                sender = str(res.get('message', {}).get('chat', {}).get('id'))
                if sender != str(chat_id): continue
                text = res.get('message', {}).get('text', '').strip()
                
                # Command Parsing
                if text.startswith("/status"):
                    parts = text.split()
                    if len(parts) > 1 and parts[1].lower() != "all":
                        job = parts[1]
                        self.send_tg(token, chat_id, f"**Status:** `{job}`\n{self.get_details(watch_dir, job)}", "📈", self.gen_plot(watch_dir, job))
                    else:
                        if not self.job_heartbeats: self.send_tg(token, chat_id, "No jobs running.", "zzz")
                        else: self.send_tg(token, chat_id, "\n\n".join([f"🔹 *{j}*\n{self.get_details(watch_dir, j)}" for j in self.job_heartbeats]), "📋")
                elif text.startswith("/kill"):
                    parts = text.split()
                    if len(parts) > 1:
                        job = parts[1]
                        subprocess.call(f"abaqus terminate job={job}", shell=True, cwd=watch_dir)
                        self.send_tg(token, chat_id, f"Kill sent: `{job}`", "💀")
        except: pass

    # --- FILE PARSING HELPERS ---
    def get_status(self, d, j):
        """Reads the .sta file to determine final job status (Success/Error)."""
        sta = os.path.join(d, j + ".sta")
        if not os.path.exists(sta): return "FINISHED", "⚠️", "No Data"
        try:
            with open(sta, 'r') as f:
                c = f.read()
                if "COMPLETED SUCCESSFULLY" in c: return "SUCCESS", "✅", "Converged"
                elif "ERROR" in c: return "ABORTED", "🚨", "Check .msg"
                else: return "TERMINATED", "⚠️", "Stopped"
        except: pass
        return "UNKNOWN", "❓", "Error"

    def get_details(self, d, j):
        """Parses the .sta file for detailed progress (Time, dt, Energies)."""
        sta = os.path.join(d, j + ".sta")
        if not os.path.exists(sta): return "Waiting..."
        try:
            with open(sta, 'r') as f:
                lines = f.readlines()
                dat, fra, step, start = "Reading...", "No Frames", "1", ""
                found_d, found_s = False, False
                for i in range(min(5, len(lines))):
                    if "DATE" in lines[i] and "TIME" in lines[i]:
                        try:
                            p = lines[i].split("DATE")[-1].split("TIME")
                            start = f"📅 {p[0].strip()} {p[1].strip()}\n"
                            found_s = True
                        except: pass
                    if found_s: break
                for line in reversed(lines):
                    p = line.split()
                    if not found_d and len(p)>7 and p[0].isdigit():
                        dat = f"Time: {p[1]}s | dt: {p[4]}\nKE: {p[6]} | TE: {p[7]}"
                        found_d = True
                    if "ODB Field Frame Number" in line and len(p)>6: fra = f"Frames: {p[4]}/{p[6]}"
                    if line.strip().startswith("STEP") and "ORIGIN" in line and len(p)>1 and p[1].isdigit(): step = p[1]
                    if found_d and fra != "No Frames": break
                return f"{start}Step {step} | {dat}\n📁 {fra}"
        except: return "Error"

    def gen_plot(self, d, j):
        """Generates a convergence plot (Time vs dt) using Matplotlib."""
        sta = os.path.join(d, j + ".sta")
        if not os.path.exists(sta): return None
        out = os.path.join(d, "job_watcher")
        if not os.path.exists(out): os.makedirs(out)
        t, dt = [], []
        try:
            with open(sta, 'r') as f:
                for line in f:
                    p = line.split()
                    if len(p)>7 and p[0].isdigit():
                        try: t.append(float(p[1])); dt.append(float(p[4]))
                        except: continue
            if not t: return None
            plt.figure(figsize=(10, 5))
            plt.plot(t, dt, color='#d62728', linewidth=1)
            plt.yscale('log'); plt.title(f"Stability: {j}"); plt.grid(True, alpha=0.4)
            path = os.path.join(out, f"plot_{j}.png")
            plt.savefig(path, bbox_inches='tight'); plt.close()
            return path
        except: return None

if __name__ == "__main__":
    app = AbaqusWatcherApp()
    app.mainloop()