"""
ABAQUS Watcher GUI
==================

A modern, cross-platform Desktop Application to monitor SIMULIA ABAQUS jobs remotely via Telegram.
This tool watches a specified directory for ABAQUS lock files (.lck) and status files (.sta),
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
Repository: https://github.com/daadaan/ABAQUS_Watcher_GUI
"""

from __future__ import annotations

# Threading model (important for GUI stability)
# - Tk/customtkinter widgets must only be modified from the main thread.
# - Long-running work (directory polling, Telegram network calls, update checks)
#   is run in background threads.
# - Background threads post UI updates via self.after(0, ...).

import customtkinter as ctk
from tkinter import messagebox, filedialog
import json
import os
import sys
import threading
import time
import requests
import subprocess
import matplotlib
import keyring
from datetime import datetime
import pystray
from PIL import Image, ImageDraw, ImageTk
import webbrowser
from packaging import version
import re
from typing import Any, Optional

# Use non-interactive backend for plots to prevent GUI thread blocking
matplotlib.use('Agg')

import matplotlib.pyplot as plt

# ================= CONFIGURATION =================
# Application identity and version information
APP_NAME = "ABAQUS Watcher GUI"
GITHUB_REPO = "daadaan/ABAQUS_Watcher_GUI"  # Repository for GitHub API update checks
CURRENT_VERSION = "1.3.2"

# Determine a safe, writable path for the config file
# Uses Windows %LOCALAPPDATA% which typically maps to:
# C:\Users\YourName\AppData\Local\ABAQUSWatcherGUI\abaqus_watcher_config.json
app_data_dir = os.path.join(os.environ["LOCALAPPDATA"], "ABAQUSWatcherGUI")
os.makedirs(app_data_dir, exist_ok=True)  # Ensure the folder exists before first use
CONFIG_FILE = os.path.join(app_data_dir, "abaqus_watcher_config.json")

# Performance Constants - These values balance resource usage with responsiveness
MAX_TAIL_BYTES = 250_000    # Maximum bytes to read from end of .sta files (prevents loading huge files into memory)
MAX_CONSOLE_LINES = 500     # Maximum lines to keep in the log console (prevents unbounded memory growth)
MAX_SUMMARY_JOBS = 15       # Maximum jobs to include in /status all (prevents Telegram message length errors)
HEADER_SCAN_LINES = 30      # Lines to scan for start date in .sta file header (optimization to avoid full file reads)
START_SCAN_LINES = 200      # Lines to scan for start time in job summary (balances accuracy vs. performance)
# =================================================

class AbaqusWatcherApp(ctk.CTk):
    """
    Main Application Class.
    Inherits from customtkinter.CTk to provide a modern dark/light mode interface.
    """
    def __init__(self):
        super().__init__()

        # Type hints for UI elements (assigned later in create_ui/add_input methods)
        # These help static type checkers understand the widget types throughout the class
        self.entry_token: Optional[ctk.CTkEntry] = None
        self.entry_chat_id: Optional[ctk.CTkEntry] = None
        self.entry_heartbeat: Optional[ctk.CTkEntry] = None
        self.entry_dir: Optional[ctk.CTkEntry] = None
        # System tray icon - uses 'Any' type because pystray's type stubs are incomplete
        self.tray_icon: Any = None

        # --- Window Setup ---
        self.title(f"{APP_NAME}")
        self.geometry("320x580")  # Fixed size for consistent layout
        self.resizable(False, False)  # Prevent resizing to maintain UI integrity

        # Generate application icon dynamically (circular branding color)
        icon_img = Image.new('RGBA', (64, 64), (0, 0, 0, 0))  # Transparent background
        ImageDraw.Draw(icon_img).ellipse([10, 10, 54, 54], fill="#6769a2")  # Brand color circle

        # Convert to Tk-compatible PhotoImage for window icon
        # CRITICAL: customtkinter's CTkImage is NOT compatible with Tk's iconphoto()
        # We must use ImageTk.PhotoImage and keep a reference to prevent garbage collection
        self._icon_photo = ImageTk.PhotoImage(icon_img)
        self.iconphoto(False, self._icon_photo)  # type: ignore[arg-type] - type checker doesn't understand Tk's loose typing
        
        # Match main window background to TabView for seamless visual integration
        # Tuple format: (light_mode_color, dark_mode_color)
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
        self.watcher_thread = None  # Background thread that monitors the job directory
        self.stop_event = threading.Event()  # Thread-safe flag to signal watcher thread shutdown
        self.is_running = False  # UI state flag indicating if monitoring is active
        # Dictionary tracking each job's heartbeat state:
        # {"job_name": {"last_hb": timestamp, "start_date": "cached start string"}}
        self.job_heartbeats = {}
        self.last_telegram_update_id = 0  # Offset for Telegram long polling to avoid duplicate messages
        self.tray_thread = None  # Separate thread running the system tray icon (pystray requirement)

        # --- Config Variables (Linked to UI Widgets) ---
        self.var_tray_enabled = ctk.BooleanVar(value=False)  # Minimize to tray preference
        self.var_theme = ctk.StringVar(value="System")  # Theme selection: System/Dark/Light

        # --- Initialization ---
        self.create_ui()
        self.load_config()

        # --- Window Event Bindings ---
        self.protocol('WM_DELETE_WINDOW', self.on_closing)  # Handle X button
        self.bind("<Unmap>", self.check_minimize_event)     # Handle Minimize

    def create_ui(self):
        """
        Builds the complete tabbed interface with three tabs:
        1. Monitor - Real-time job status and activity log
        2. Config - Settings, credentials, and preferences
        3. Help - Command reference and usage information
        """
        
        # Initialize TabView with custom styling for dark/light mode compatibility
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

        # --- GLOBAL FOOTER ---
        lbl_copyright = ctk.CTkLabel(self, text=f"© {time.localtime().tm_year} Souvik Biswas\nv{CURRENT_VERSION}", 
                                     font=("Segoe UI", 10), text_color=("gray50", "gray50"))
        lbl_copyright.pack(side="bottom", pady=(0, 5))

        # ================= TAB 1: MONITOR UI =================
        
        # Status indicator card - Shows current watcher state (RUNNING/STOPPED)
        self.frame_status = ctk.CTkFrame(self.tab_monitor, corner_radius=8, fg_color=("gray90", "gray13")) 
        self.frame_status.pack(pady=(10, 5), padx=10, fill="x")
        
        # Status label changes color based on state: Red (stopped) or Green (running)
        self.lbl_status = ctk.CTkLabel(self.frame_status, text="STOPPED", text_color="#EF4444", font=("Roboto", 14, "bold"))
        self.lbl_status.pack(pady=8)

        # Main Controls
        self.btn_start = ctk.CTkButton(self.tab_monitor, text="START WATCHER", command=self.toggle_watcher, 
                                       fg_color="#15803d", hover_color="#14532d",
                                       font=self.font_bold, height=36, corner_radius=6)
        self.btn_start.pack(padx=10, pady=5, fill="x")

        self.btn_ping = ctk.CTkButton(self.tab_monitor, text="Test Connection", command=self.ping_test, 
                                      fg_color="transparent", border_width=1, border_color=("gray70", "gray40"), 
                                      text_color=("gray10", "gray90"), hover_color=("gray90", "gray20"),
                                      font=self.font_body, height=24)
        self.btn_ping.pack(padx=10, pady=5, fill="x")

        # --- ACTIVE JOBS SECTION ---
        ctk.CTkLabel(self.tab_monitor, text="Active Jobs", anchor="w", font=self.font_bold, text_color=("gray40", "gray60")).pack(padx=10, pady=(5, 0), fill="x")

        # Column Headers
        self.header_frame = ctk.CTkFrame(self.tab_monitor, fg_color="transparent", height=10)
        self.header_frame.pack(padx=15, pady=(2, 0), fill="x")
        ctk.CTkLabel(self.header_frame, text="Job Name", font=self.font_small, text_color="gray", anchor="w").pack(side="left")
        ctk.CTkLabel(self.header_frame, text="Time Left", font=self.font_small, text_color="gray", anchor="e").pack(side="right")

        # Container Frame with FORCED HEIGHT (pack_propagate=False)
        # CRITICAL: Disabling pack_propagate prevents the ScrollableFrame from expanding
        # to fit all content, which would break the layout and push other widgets off-screen
        self.container_jobs = ctk.CTkFrame(self.tab_monitor, height=150, fg_color="transparent")
        self.container_jobs.pack(padx=10, pady=(2, 2), fill="x")
        self.container_jobs.pack_propagate(False)  # <--- THE MAGIC FIX: Maintains fixed height

        # Scrollable frame for job list - automatically adds scrollbar when content overflows
        self.frame_jobs_list = ctk.CTkScrollableFrame(self.container_jobs, fg_color=("white", "gray20"))
        self.frame_jobs_list.pack(fill="both", expand=True)

        # Placeholder label for initial state (replaced when jobs are detected)
        ctk.CTkLabel(self.frame_jobs_list, text="Watcher Stopped", text_color="gray").pack(pady=20)

        # --- LIVE ACTIVITY SECTION ---
        ctk.CTkLabel(self.tab_monitor, text="Live Activity", anchor="w", font=self.font_bold, text_color=("gray40", "gray60")).pack(padx=10, pady=(5, 2), fill="x")

        # Console widget - Displays timestamped logs with monospace font
        # State is kept "disabled" except during writes to prevent user editing
        self.console = ctk.CTkTextbox(
            self.tab_monitor,
            width=280,
            height=90,
            font=self.font_mono,  # Monospace for better log readability
            fg_color=("white", "black"),
            text_color=("black", "white"),  # type: ignore[arg-type] - tuple causes type checker warning
            corner_radius=6,
            border_width=1,
            border_color=("gray80", "gray30"),
        )
        self.console.pack(padx=5, pady=(0, 0), fill="x")
        self.console.configure(state="disabled")  # Read-only by default

        # ================= TAB 2: CONFIG UI =================
        self.frame_cfg = ctk.CTkFrame(self.tab_settings, fg_color="transparent")
        self.frame_cfg.pack(fill="both", expand=True, padx=5)

        self.add_input(self.frame_cfg, "Bot Token", "entry_token", secret=True)
        self.add_input(self.frame_cfg, "Chat ID", "entry_chat_id", secret=True)

        ctk.CTkLabel(self.frame_cfg, text="ABAQUS Temp Directory", anchor="w", font=self.font_bold, text_color=("gray50", "gray50")).pack(padx=5, pady=(5,0), fill="x")
        
        self.frame_dir = ctk.CTkFrame(self.frame_cfg, fg_color="transparent")
        self.frame_dir.pack(padx=5, pady=0, fill="x")
        
        self.entry_dir = ctk.CTkEntry(self.frame_dir, height=34, font=self.font_body)
        self.entry_dir.pack(side="left", fill="x", expand=True, padx=(0, 5))
        
        self.btn_browse = ctk.CTkButton(self.frame_dir, text="📂", width=34, height=34, command=self.browse_directory,
                                        fg_color=("gray80", "gray30"), hover_color=("gray70", "gray40"), text_color=("black", "white"))
        self.btn_browse.pack(side="right")

        self.add_input(self.frame_cfg, "Heartbeat (s)", "entry_heartbeat", default="3600")

        self.frame_opts = ctk.CTkFrame(self.frame_cfg, fg_color="transparent")
        self.frame_opts.pack(fill="x", pady=15)
        
        ctk.CTkLabel(self.frame_opts, text="Theme", font=self.font_body).pack(side="left", padx=5)
        self.opt_theme = ctk.CTkOptionMenu(self.frame_opts, values=["System", "Dark", "Light"], width=100, height=28,
                                           variable=self.var_theme, command=self.change_theme, font=self.font_body)
        self.opt_theme.pack(side="left", padx=10)

        self.switch_tray = ctk.CTkSwitch(self.frame_cfg, text="Minimize to Tray", font=self.font_body,
                                         variable=self.var_tray_enabled, height=24, width=50)
        self.switch_tray.pack(padx=5, pady=(5, 10), anchor="w")

        self.btn_save = ctk.CTkButton(self.tab_settings, text="Save Settings", command=self.save_config, 
                                      fg_color="#2563EB", hover_color="#3B82F6", 
                                      font=self.font_bold, height=40)
        self.btn_save.pack(side="top", padx=5, pady=(5, 5), fill="x")

        self.frame_utils = ctk.CTkFrame(self.tab_settings, fg_color="transparent")
        self.frame_utils.pack(side="bottom", fill="x", pady=(10, 0))
        self.frame_utils.columnconfigure(0, weight=1)
        self.frame_utils.columnconfigure(1, weight=1)

        btn_clear = ctk.CTkButton(self.frame_utils, text="Clear Data", command=self.clear_config,
                                  fg_color="transparent", border_width=2, border_color="#EF4444", text_color="#EF4444",
                                  hover_color=("#FEE2E2", "#450a0a"), font=self.font_small, height=28)
        btn_clear.grid(row=0, column=0, padx=5, pady=2, sticky="ew")

        btn_update = ctk.CTkButton(self.frame_utils, text="Check Updates", command=self.check_updates,
                                   fg_color="transparent", border_width=2, border_color="#3B82F6", text_color="#3B82F6",
                                   hover_color=("#DBEAFE", "#172554"), font=self.font_small, height=28)
        btn_update.grid(row=0, column=1, padx=5, pady=2, sticky="ew")

        btn_repo = ctk.CTkButton(self.frame_utils, text="GitHub Repo", 
                                 fg_color="transparent", hover=False, text_color=("gray50", "gray60"), font=("Segoe UI", 12, "underline"),
                                 height=20, command=lambda: webbrowser.open(f"https://github.com/{GITHUB_REPO}"))
        btn_repo.grid(row=1, column=0, padx=5, pady=(2,0), sticky="ew")

        btn_issue = ctk.CTkButton(self.frame_utils, text="Report Issue", 
                                  fg_color="transparent", hover=False, text_color="#EF4444", font=("Segoe UI", 12, "underline"),
                                  height=20, command=lambda: webbrowser.open(f"https://github.com/{GITHUB_REPO}/issues"))
        btn_issue.grid(row=1, column=1, padx=5, pady=(2,0), sticky="ew")

        # ================= TAB 3: HELP UI =================
        help_text = (
            "COMMANDS\n"
            "──────────────────────────────\n"
            "/status_all\n"
            "List recent jobs (any status).\n\n"
            "/status_running\n"
            "List currently running jobs.\n\n"
            "/status_completed\n"
            "List recently finished jobs.\n\n"
            "/status_error\n"
            "List failed/aborted jobs.\n\n"
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
        """
        Helper method to create labeled input fields with optional password masking.
        
        Args:
            parent: Parent widget to attach the input to
            label: Display label for the field
            var_name: Attribute name to assign the entry widget to (e.g., "entry_token")
            default: Default value to pre-populate the field
            secret: If True, adds a toggle visibility button and masks input with bullets
        """
        # Label for the input field
        ctk.CTkLabel(parent, text=label, anchor="w", font=self.font_bold, text_color=("gray50", "gray50")).pack(padx=5, pady=(5,0), fill="x")
        
        # Transparent frame to hold entry + optional toggle button
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.pack(padx=5, pady=0, fill="x")
        
        # Text entry widget
        entry = ctk.CTkEntry(frame, height=34, font=self.font_body)
        entry.pack(side="left", fill="x", expand=True)
        entry.insert(0, default)
        
        # Add visibility toggle for sensitive fields (tokens, passwords)
        if secret:
            entry.configure(show="●")  # Mask input with bullet points
            btn_eye = ctk.CTkButton(frame, text="👁", width=34, height=34, 
                                    fg_color=("gray80", "gray30"), hover_color=("gray70", "gray40"), 
                                    text_color=("black", "white"),
                                    command=lambda: self.toggle_password(entry, btn_eye))
            btn_eye.pack(side="right", padx=(5, 0))
        
        # Dynamically assign the entry widget to the class using the provided variable name
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
        if not self.entry_dir:
            return
        dir_path = filedialog.askdirectory()
        if dir_path:
            self.entry_dir.delete(0, 'end') 
            self.entry_dir.insert(0, dir_path)

    def check_updates(self):
        """
        Checks GitHub for newer versions and handles updates based on deployment mode.
        
        - Frozen (EXE): Opens browser to download new executable
        - Script (.py): Automatically downloads and overwrites the current script file
        
        Uses semantic versioning comparison via the 'packaging' library.
        """
        def _check():
            self.log("Checking for updates...")
            try:
                # 1. Fetch Latest Release Info from GitHub API
                # IMPORTANT: User-Agent header prevents GitHub 403 rate limiting errors
                headers = {"User-Agent": "Abaqus-Watcher-Client"}
                api_url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
                
                # Timeout prevents hanging if GitHub is unreachable
                resp = requests.get(api_url, headers=headers, timeout=6)
                
                if resp.status_code != 200:
                    self.log(f"Update Check Fail: {resp.status_code}")
                    return

                data = resp.json()
                latest_tag = data.get("tag_name") or data.get("name")
                
                if not latest_tag:
                    self.log("Err: No version tag found.")
                    return

                # Normalize version strings (handle 'v1.0.0' vs '1.0.0' and pre-release tags)
                # Strip leading 'v' or 'V' and remove any suffixes like '-beta' or '-rc1'
                norm_latest = latest_tag.lstrip("vV").split("-")[0]
                norm_current = CURRENT_VERSION.lstrip("vV").split("-")[0]

                # 2. Compare Versions using semantic versioning
                # packaging.version.parse() handles proper version comparison (e.g., 1.10 > 1.9)
                if version.parse(norm_latest) <= version.parse(norm_current):
                    self.log(f"✓ Up to date ({CURRENT_VERSION})")
                    return

                # ============================================
                # UPDATE AVAILABLE - Handle based on deployment mode
                # ============================================
                self.log(f"Update found: {latest_tag}")
                
                # SCENARIO A: Compiled EXE (Frozen with PyInstaller)
                # User must manually download the new executable from GitHub Releases
                if getattr(sys, 'frozen', False):
                    if messagebox.askyesno("Update Available", f"New version {latest_tag} is available.\nOpen download page?"):
                        webbrowser.open(data['html_url'])
                
                # SCENARIO B: Python Script (Running as .py file)
                # Can auto-update by downloading and overwriting the current script
                else:
                    msg = (f"New version {latest_tag} is available.\n\n"
                           "I can auto-update this script file.\n"
                           "Overwrite now?")
                    if messagebox.askyesno("Update Script", msg):
                        self.perform_script_update(latest_tag)

            except Exception as e:
                self.log(f"Update Err: {str(e)[:20]}")
        
        threading.Thread(target=_check, daemon=True).start()

    def perform_script_update(self, tag_name):
        """
        Downloads the raw .py file from GitHub and overwrites the current script.
        Only called when running as a Python script (not frozen EXE).
        
        Args:
            tag_name: Git tag of the release to download (e.g., "v1.3.3")
        """
        try:
            self.log("Downloading update...")
            
            # Construct URL to RAW file on GitHub
            # CRITICAL: Filename must match the repository filename exactly
            raw_url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/{tag_name}/abaqus_watcher_gui.py"
            
            # Use User-Agent header to avoid rate limiting
            headers = {"User-Agent": "Abaqus-Watcher-Client"}
            resp = requests.get(raw_url, headers=headers, timeout=10)
            
            if resp.status_code != 200:
                messagebox.showerror("Error", "Could not fetch update file.")
                return

            # Read new code
            new_code = resp.text

            # Safety Check: Validate that we received a valid Python script
            # Prevents corrupted downloads or wrong files from breaking the app
            if "class AbaqusWatcherApp" not in new_code:
                messagebox.showerror("Error", "Invalid update file received.")
                return

            # Overwrite current file
            # __file__ is the absolute path to the currently running script
            with open(__file__, 'w', encoding='utf-8') as f:
                f.write(new_code)

            self.log("Update complete!")
            
            # Prompt for restart using os.execv() which replaces the current process
            if messagebox.askyesno("Updated", "Update successful! The app needs to restart.\nRestart now?"):
                os.execv(sys.executable, [sys.executable] + sys.argv)

        except Exception as e:
            messagebox.showerror("Update Error", f"Failed to overwrite script:\n{e}")

    # --- SYSTEM TRAY LOGIC ---
    def _create_tray_icon(self):
        """
        Creates a fresh instance of the system tray icon.
        
        IMPORTANT: pystray requires each icon instance to be used only once.
        We create a new instance each time the window is minimized to tray.
        
        Returns:
            pystray.Icon: Configured tray icon instance (not yet running)
        """
        # 1. Draw the icon image (same branding as window icon)
        image = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.ellipse([10, 10, 54, 54], fill="#6769a2")  # Brand color
        
        # 2. Define right-click menu
        menu = pystray.Menu(
            pystray.MenuItem("Open Monitor", self.show_window_from_tray, default=True),  # Default = double-click action
            pystray.MenuItem("Quit", self.quit_app)
        )
        
        # 3. Return new instance (caller will start it in a separate thread)
        return pystray.Icon("AbaqusWatcherGUI", image, "Abaqus Watcher GUI", menu)

    def check_minimize_event(self, event):
        """
        Intercepts window minimize event (<Unmap>) to hide to system tray if enabled.
        
        Flow:
        1. Check if window is minimized (state == 'iconic')
        2. Check if 'Minimize to Tray' setting is enabled
        3. Hide window and create system tray icon
        """
        if self.state() == 'iconic' and self.var_tray_enabled.get():
            # 1. Hide the main window completely
            self.withdraw()
            
            # 2. Create and start a FRESH tray icon instance
            # CRITICAL: pystray.Icon.run() is blocking, so it must run in a separate thread
            self.tray_icon = self._create_tray_icon()
            self.tray_thread = threading.Thread(target=self.tray_icon.run, daemon=True)
            self.tray_thread.start()

    def show_window_from_tray(self, icon=None, item=None):
        if self.tray_icon is not None:
            self.tray_icon.stop()
        self.after(0, self.deiconify)

    def change_theme(self, new_theme: str):
        ctk.set_appearance_mode(new_theme)

    def on_closing(self):
        self.quit_app()

    def quit_app(self, icon=None, item=None):
        if self.tray_icon:
            self.tray_icon.stop()
        self.stop_event.set()
        self.quit()

    # --- CONFIGURATION IO ---
    def load_config(self):
        """
        Loads configuration from two sources:
        1. Non-sensitive settings from JSON file (directory, heartbeat, theme)
        2. Sensitive credentials from Windows Credential Manager via keyring library
        
        This separation ensures tokens are never stored in plain text.
        """
        # Guard clause: Ensure UI is initialized before loading
        if not self.entry_dir or not self.entry_heartbeat or not self.entry_token or not self.entry_chat_id:
            return
        
        # Load non-sensitive settings from JSON
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.entry_dir.delete(0, 'end'); self.entry_dir.insert(0, data.get("watch_dir", ""))
                    self.entry_heartbeat.delete(0, 'end'); self.entry_heartbeat.insert(0, data.get("heartbeat", "3600"))
                    self.var_tray_enabled.set(data.get("tray_enabled", False))
                    theme = data.get("theme", "System")
                    self.var_theme.set(theme)
                    ctk.set_appearance_mode(theme)
            except: pass  # Silently fail if config is corrupted
        
        # Load sensitive credentials from Windows Credential Manager
        try:
            t = keyring.get_password(APP_NAME, "bot_token")
            c = keyring.get_password(APP_NAME, "chat_id")
            if t: self.entry_token.delete(0, 'end'); self.entry_token.insert(0, t)
            if c: self.entry_chat_id.delete(0, 'end'); self.entry_chat_id.insert(0, c)
            if t: self.log("Credentials loaded.")
        except: self.log("Keyring error.")  # May fail on non-Windows systems or if keyring backend unavailable

    def save_config(self):
        """
        Saves configuration to two locations:
        1. Non-sensitive settings to JSON file
        2. Sensitive credentials to Windows Credential Manager
        
        SECURITY: Tokens are NEVER written to JSON files.
        """
        # Guard clause: Ensure UI is initialized
        if not self.entry_token or not self.entry_chat_id or not self.entry_dir or not self.entry_heartbeat:
            self.log("Err: UI not initialized.")
            return

        # Extract current values from UI widgets
        t, c = self.entry_token.get().strip(), self.entry_chat_id.get().strip()
        d, h = self.entry_dir.get().strip(), self.entry_heartbeat.get().strip()
        
        # Build JSON data (non-sensitive only)
        data = {"watch_dir": d, "heartbeat": h, "tray_enabled": self.var_tray_enabled.get(), "theme": self.var_theme.get()}
        
        try:
            # Store sensitive credentials in OS-level vault
            if t: keyring.set_password(APP_NAME, "bot_token", t)
            if c: keyring.set_password(APP_NAME, "chat_id", c)
            
            # Write non-sensitive config to JSON
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            self.log("Settings saved.")
        except Exception as e:
            self.log(f"Save failed: {e}")

    def clear_config(self):
        """
        Wipes all configuration data:
        1. Stops watcher if running
        2. Deletes JSON config file
        3. Removes credentials from Windows Credential Manager
        4. Clears all UI input fields
        
        IMPORTANT: This is destructive and requires user confirmation.
        """
        if messagebox.askyesno("Reset", "Clear all data?"):

            # Stop watcher thread before clearing config
            if self.is_running:
                self.toggle_watcher()

            try:
                # Delete JSON config file
                if os.path.exists(CONFIG_FILE): 
                    os.remove(CONFIG_FILE)
                
                # Remove credentials from keyring (may not exist, so fail silently)
                try: keyring.delete_password(APP_NAME, "bot_token")
                except: pass
                try: keyring.delete_password(APP_NAME, "chat_id")
                except: pass
                
                # Clear all UI fields
                if self.entry_token and self.entry_chat_id:
                    self.entry_token.delete(0, 'end'); self.entry_chat_id.delete(0, 'end')
                if self.entry_dir and self.entry_heartbeat:
                    self.entry_dir.delete(0, 'end'); self.entry_heartbeat.delete(0, 'end')
                self.log("Data wiped.")
            except: pass  # Silently handle any unexpected errors

    # --- CORE WATCHER LOGIC ---
    def log(self, message):
        """
        Thread-safe logging to the UI console.
        
        CRITICAL: This method can be called from background threads.
        We use self.after(0, callback) to ensure the UI update happens on the main thread.
        
        Args:
            message: Log message to display
        """
        ts = datetime.now().strftime("%H:%M")  # Short timestamp format
        # Schedule UI update on main thread (safe from any thread)
        self.after(0, lambda: self._update_console(f"[{ts}] {message}\n"))

    def _update_console(self, text):
        """
        Updates the console textbox and trims old lines to prevent memory bloat.
        
        IMPORTANT: This method must only be called from the main thread.
        It temporarily unlocks the console, appends text, trims if needed, and re-locks.
        
        Args:
            text: Pre-formatted text to append (should include newline)
        """
        # Temporarily enable editing
        self.console.configure(state="normal")
        
        # Append new text to the end
        self.console.insert("end", text)

        # 1. Get the current line count from the textbox
        # 'end-1c' gets the position just before the final newline character
        current_lines = int(self.console.index('end-1c').split('.')[0])

        # 2. Trim oldest lines if we exceed the configured limit
        # This prevents the Text widget from consuming unbounded memory over long sessions
        if current_lines > MAX_CONSOLE_LINES:
            self.console.delete("1.0", "2.0")  # Delete line 1 (oldest)

        # 3. Auto-scroll to show the latest message
        self.console.see("end")
        
        # 4. Lock the console to prevent user editing
        self.console.configure(state="disabled")

    def ping_test(self):
        """Checks internet connectivity."""
        def _ping():
            try:
                # generate_204 returns quickly and has tiny payload.
                requests.get("https://www.google.com/generate_204", timeout=3)
                self.log("Online.")
            except Exception:
                self.log("Offline.")

        threading.Thread(target=_ping, daemon=True).start()

    def toggle_watcher(self):
        """
        Starts or stops the background monitoring thread.
        
        When starting:
        - Clears the stop event flag
        - Spawns a daemon thread running run_loop()
        - Updates UI to show RUNNING state (green)
        
        When stopping:
        - Sets the stop event flag (signals thread to exit)
        - Updates UI to show STOPPED state (red)
        """
        if self.is_running:
            # Stop the watcher
            self.stop_event.set()  # Signal background thread to exit
            self.is_running = False
            self.btn_start.configure(text="START WATCHER", fg_color="#15803d", hover_color="#14532d")
            self.lbl_status.configure(text="STOPPED", text_color="#EF4444")  # Red
            self.log("Stopped.")
        else:
            # Start the watcher
            self.stop_event.clear()  # Clear any previous stop signals
            self.is_running = True
            self.btn_start.configure(text="STOP WATCHER", fg_color="#EF4444", hover_color="#DC2626")  # Red
            self.lbl_status.configure(text="RUNNING", text_color="#10B981")  # Green
            # Spawn daemon thread (automatically dies when main program exits)
            self.watcher_thread = threading.Thread(target=self.run_loop, daemon=True)
            self.watcher_thread.start()

    def update_job_table(self, job_data):
        """
        Updates the active jobs list UI with current job status and time estimates.
        
        IMPORTANT: Must be called from the main thread via self.after().
        
        Args:
            job_data: List of tuples [(job_name, time_remaining_str), ...]
        """
        # Clear all existing rows
        for w in self.frame_jobs_list.winfo_children(): 
            w.destroy()

        # Show placeholder if no jobs are active
        if not job_data:
            ctk.CTkLabel(self.frame_jobs_list, text="No active jobs", font=self.font_small, text_color="gray").pack(pady=10)
            return

        # Create a row for each active job
        for name, time_str in job_data:
            row = ctk.CTkFrame(self.frame_jobs_list, fg_color="transparent")
            row.pack(fill="x", pady=2)
            
            # Left: Job name in bold
            ctk.CTkLabel(row, text=name, font=self.font_bold, anchor="w").pack(side="left", padx=5)
            
            # Right: Time estimate with color coding
            # Green if we have a valid estimate (contains 'h' or 'm'), gray if still calculating
            color = "#10B981" if "h" in time_str or "m" in time_str else "gray"
            ctk.CTkLabel(row, text=time_str, font=self.font_mono, text_color=color, anchor="e").pack(side="right", padx=5)
    
    def run_loop(self):
        """
        Main background monitoring loop. Runs in a separate thread.
        
        Responsibilities:
        1. Validate configuration
        2. Poll directory for .lck files (active jobs)
        3. Detect new/finished jobs and send Telegram notifications
        4. Send periodic heartbeat updates for long-running jobs
        5. Check for incoming Telegram commands
        6. Update UI job table
        
        Thread-Safety: Uses self.after() to schedule UI updates on the main thread.
        """
        self.log("Monitoring active.")
        
        # UI Check: Ensure widgets are initialized before accessing
        if not self.entry_token or not self.entry_chat_id or not self.entry_dir or not self.entry_heartbeat:
            self.log("Err: UI not initialized.")
            self.after(0, self.toggle_watcher)  # Stop watcher from main thread
            return

        # Load and validate configuration from UI
        token = self.entry_token.get().strip()
        chat_id = self.entry_chat_id.get().strip()
        watch_dir = self.entry_dir.get().strip()
        try: 
            hb = int(self.entry_heartbeat.get().strip())  # Heartbeat interval in seconds
        except: 
            hb = 3600  # Default to 1 hour if parsing fails

        # Validate required fields
        if not token or not chat_id or not os.path.exists(watch_dir):
            self.log("Err: Check config.")
            self.after(0, self.toggle_watcher)  # Stop watcher from main thread
            return

        # Initialize job tracking dictionary
        self.job_heartbeats = {}

        # Main monitoring loop - exits when stop_event is set
        while not self.stop_event.is_set():
            try:
                # 1. Poll Telegram for incoming user commands (/status, /kill, etc.)
                self.check_telegram(token, chat_id, watch_dir)
                
                # 2. Scan watch directory for active job lock files (.lck)
                # ABAQUS creates a .lck file when a job starts and removes it when finished
                if os.path.exists(watch_dir):
                    with os.scandir(watch_dir) as it:
                        # Extract job names by removing .lck extension
                        active = [e.name[:-4] for e in it if e.is_file() and e.name.endswith('.lck')]
                else:
                    active = []  # Directory doesn't exist or was deleted

                # --- Update UI Job Table ---
                # Build list of (job_name, time_estimate, start_timestamp) for sorting
                ui_data = []
                for job in active:
                    # Read .sta file tail to estimate completion time
                    sta = os.path.join(watch_dir, job + ".sta")
                    tail = self._read_tail_text(sta)
                    est_str, _, _ = self._estimate_completion(tail)
                    
                    # Extract just the "Xh Ym" part from the full estimate message
                    display_time = "Calculating..."
                    if est_msg := est_str:  # Walrus operator (Python 3.8+)
                        # Parse "⏱️ Left: 1h 20m (Tot...)" -> "1h 20m"
                        try: 
                            display_time = est_msg.split("Left:")[1].split("(")[0].strip()
                        except: 
                            pass
                    
                    # Get job start time from .lck file creation timestamp for sorting
                    try: 
                        start_ts = os.path.getctime(os.path.join(watch_dir, job + ".lck"))
                    except: 
                        start_ts = 0
                    
                    ui_data.append((job, display_time, start_ts))
                
                # Sort by start time (oldest jobs first) and prepare for UI
                ui_data.sort(key=lambda x: x[2])
                final_view = [(x[0], x[1]) for x in ui_data]  # Drop timestamp, keep name and time
                
                # Schedule UI update on main thread
                self.after(0, lambda: self.update_job_table(final_view))
                # ----------------------------------

                # 3. Detect and handle NEW jobs
                # If a .lck file appears that we haven't tracked before, it's a new job
                for job in active:
                    if job not in self.job_heartbeats:
                        # Cache the start date once (avoid re-reading file header on every heartbeat)
                        start_date = self._get_start_date_once(watch_dir, job) 
                        self.job_heartbeats[job] = {"last_hb": time.time(), "start_date": start_date}
                        self.log(f"New: {job}")
                        # Send "job started" notification to Telegram
                        self.send_tg(token, chat_id, f"**Started:** `{job}`\n{start_date}", "🚀")

                # 4. Detect and handle FINISHED jobs
                # If a job is in our tracking but no longer has a .lck file, it finished
                for job in list(self.job_heartbeats):  # Use list() to avoid dict size change during iteration
                    if job not in active:
                        # Determine final status (SUCCESS/ERROR/ABORTED)
                        status, icon, det = self.get_status(watch_dir, job)
                        plot = self.gen_plot(watch_dir, job)  # Generate convergence plot
                        # Only send silent notifications for expected completions (avoid spam for errors)
                        silent = not ("SUCCESS" in status or "ABORTED" in status)
                        self.send_tg(token, chat_id, f"**Job:** `{job}`\n**Result:** {status}\n{det}", icon, plot, silent)
                        self.log(f"End: {job} [{status}]")
                        # Remove from tracking
                        del self.job_heartbeats[job]

                # 5. Send HEARTBEAT updates for long-running jobs
                # If a job has been running longer than the heartbeat interval, send a status update
                now = time.time()
                for job, data in self.job_heartbeats.items():
                    if job in active and (now - data["last_hb"]) > hb:
                        # Get current progress details using cached start date
                        det = self.get_details(watch_dir, job, data["start_date"])
                        self.send_tg(token, chat_id, f"**Running:** `{job}`\n{det}", "⏳")
                        # Update last heartbeat timestamp
                        self.job_heartbeats[job]["last_hb"] = now
                        self.log(f"Heartbeat: {job}")

                # Wait before next iteration to avoid excessive CPU usage
                time.sleep(3)  # Poll every 3 seconds
            except Exception as e:
                self.log(f"Loop Err: {e}")
                time.sleep(5)  # Longer delay on error to avoid spam

    # --- TELEGRAM HELPERS ---
    def send_tg(self, token, chat_id, text, icon, img=None, silent=True):
        """
        Sends text messages or images to Telegram using the Bot API.
        
        Args:
            token: Telegram Bot API token
            chat_id: Target chat/user ID
            text: Message text (supports Markdown formatting)
            icon: Emoji icon to prepend to the message
            img: Optional path to image file (for convergence plots)
            silent: If True, sends notification silently (no sound/vibration)
        
        Note: This runs in the watcher thread. Timeouts are conservative to avoid blocking.
        """
        url = f"https://api.telegram.org/bot{token}"
        try:
            # Base parameters for all messages
            d = {"chat_id": chat_id, "parse_mode": "Markdown", "disable_notification": silent}
            
            # Send as photo with caption if image is provided
            if img and os.path.exists(img):
                d['caption'] = f"{icon} {text}"
                with open(img, 'rb') as f: 
                    requests.post(f"{url}/sendPhoto", data=d, files={'photo': f}, timeout=20)
                # Clean up temporary plot file
                try: 
                    os.remove(img)
                except: 
                    pass
            # Otherwise send as text-only message
            else:
                d['text'] = f"{icon} {text}"
                requests.post(f"{url}/sendMessage", data=d, timeout=10)
        except Exception as e:
            # Log only exception type to avoid cluttering console with stack traces
            self.log(f"Telegram Err: {type(e).__name__}")

    def check_telegram(self, token, chat_id, watch_dir):
        """
        Polls Telegram Bot API for incoming commands using long polling.
        
        Supported commands:
        - /status_all, /status_running, /status_completed, /status_error: Job summaries
        - /status <job_name>: Detailed stats and convergence plot
        - /kill <job_name>: Terminate a running job
        
        Security: Validates that messages are from the configured chat_id.
        """
        try:
            url = f"https://api.telegram.org/bot{token}/getUpdates"
            # Use offset to only fetch new messages (avoids processing duplicates)
            params = {'offset': self.last_telegram_update_id + 1, 'timeout': 1}
            r = requests.get(url, params=params, timeout=3).json()
            if not r.get('ok'): 
                return
            
            # Process each update in the response
            for res in r.get('result', []):
                # Update offset to acknowledge this message
                self.last_telegram_update_id = res['update_id']
                
                # Security check: Only respond to messages from the configured chat
                sender = str(res.get('message', {}).get('chat', {}).get('id'))
                if sender != str(chat_id): 
                    continue
                
                # Extract command text
                text = res.get('message', {}).get('text', '').strip()
                
                # --- COMMAND PARSING ---
                
                # 1. List Commands (Filter by status)
                # These commands scan all .sta files in the directory and filter by status
                if text == "/status_all":
                    summary = self.get_full_summary(watch_dir, filter_mode="ALL")
                    self.send_tg(token, chat_id, f"**Recent Jobs (All):**\n\n{summary}", "🗂️")
                
                elif text == "/status_running":
                    summary = self.get_full_summary(watch_dir, filter_mode="RUNNING")
                    self.send_tg(token, chat_id, f"**Running Jobs:**\n\n{summary}", "🏃")

                elif text == "/status_completed":
                    summary = self.get_full_summary(watch_dir, filter_mode="COMPLETED")
                    self.send_tg(token, chat_id, f"**Completed Jobs:**\n\n{summary}", "✅")

                elif text == "/status_error":
                    summary = self.get_full_summary(watch_dir, filter_mode="ERROR")
                    self.send_tg(token, chat_id, f"**Failed/Aborted Jobs:**\n\n{summary}", "🚨")

                # 2. Specific Job Query (with job name)
                # Format: /status Job-1
                # Returns detailed stats and convergence plot
                elif text.startswith("/status "):
                    parts = text.split(maxsplit=1)
                    if len(parts) > 1:
                        job = parts[1]
                        # SECURITY: Validate job name to prevent path traversal attacks
                        if not self._is_safe_job_name(job):
                            self.send_tg(token, chat_id, "Invalid job name.", "⚠️")
                        else:
                            self.send_tg(token, chat_id, f"**Status:** `{job}`\n{self.get_details(watch_dir, job)}", "📈", self.gen_plot(watch_dir, job))

                # 3. Kill Command (terminate job)
                # Format: /kill Job-1
                # Calls the ABAQUS terminate command via subprocess
                elif text.startswith("/kill "):
                    parts = text.split(maxsplit=1)
                    if len(parts) > 1:
                        job = parts[1]
                        # SECURITY: Validate job name before passing to shell command
                        if not self._is_safe_job_name(job):
                            self.send_tg(token, chat_id, "Invalid job name.", "⚠️")
                        else:
                            try:
                                # Execute ABAQUS terminate command
                                # check=False: Don't raise exception if command fails
                                subprocess.run(["abaqus", "terminate", f"job={job}"], cwd=watch_dir, check=False)
                                self.send_tg(token, chat_id, f"Kill sent: `{job}`", "💀")
                            except Exception:
                                self.send_tg(token, chat_id, f"Failed to terminate: `{job}`", "🚨")
        except: 
            pass  # Silently ignore Telegram API errors to avoid breaking the watcher loop

    def _is_safe_job_name(self, job: str) -> bool:
        """
        Validates job name to prevent path traversal and command injection attacks.
        
        SECURITY CRITICAL: User-supplied job names from Telegram are used in file paths
        and subprocess calls. We must ensure they contain only safe characters.
        
        Allowed: Letters, numbers, dots, underscores, hyphens
        Blocked: Slashes, backslashes, null bytes, special shell characters
        
        Args:
            job: Job name to validate
            
        Returns:
            bool: True if job name is safe, False otherwise
        """
        return bool(re.fullmatch(r"[A-Za-z0-9._-]+", job))

    def _read_tail_text(self, path: str, max_bytes: int = MAX_TAIL_BYTES) -> str:
        """
        Reads only the last N bytes of a text file for efficient parsing of large files.
        
        PERFORMANCE OPTIMIZATION: ABAQUS .sta files can grow to hundreds of MB.
        Reading only the tail provides the latest increment data without loading
        the entire file into memory.
        
        Args:
            path: Absolute path to the file
            max_bytes: Maximum bytes to read from end of file (default: MAX_TAIL_BYTES)
            
        Returns:
            str: Decoded text from file tail, or empty string if file doesn't exist
        """
        try:
            with open(path, 'rb') as f:
                # Seek to end to get file size
                f.seek(0, os.SEEK_END)
                size = f.tell()
                # Seek back to (size - max_bytes), but not before start of file
                f.seek(max(size - max_bytes, 0), os.SEEK_SET)
                data = f.read()
            # Decode with error handling for non-UTF8 characters
            return data.decode('utf-8', errors='ignore')
        except Exception:
            return ""

    # --- FILE PARSING HELPERS ---
    def get_status(self, d, j):
        """
        Determines the final status of a finished job by parsing the .sta file.
        
        Searches the file tail for ABAQUS completion keywords.
        
        Args:
            d: Watch directory path
            j: Job name (without extension)
            
        Returns:
            tuple: (status_string, emoji_icon, detail_message)
        """
        sta = os.path.join(d, j + ".sta")
        if not os.path.exists(sta): 
            return "FINISHED", "⚠️", "No Data"
        try:
            tail = self._read_tail_text(sta)
            # Check for success message
            if "COMPLETED SUCCESSFULLY" in tail:
                return "SUCCESS", "✅", "Converged"
            # Check for error/abort
            if "ERROR" in tail:
                return "ABORTED", "🚨", "Check .msg"
            # Otherwise assume manual termination
            return "TERMINATED", "⚠️", "Stopped"
        except: 
            pass
        return "UNKNOWN", "❓", "Error"

    def _get_start_date_once(self, d, j):
        """
        Reads the job start date/time from the .sta file header (called once per job).
        
        PERFORMANCE: We only read the header once when a job is first detected,
        then cache the result. This avoids re-scanning the file on every heartbeat.
        
        Args:
            d: Watch directory path
            j: Job name (without extension)
            
        Returns:
            str: Formatted start date string (e.g., "📅 12-Jan-2026 14:30:45") or empty string
        """
        sta = os.path.join(d, j + ".sta")
        if not os.path.exists(sta): 
            return ""
        try:
            with open(sta, 'r', encoding='utf-8', errors='ignore') as f:
                # Scan only the first HEADER_SCAN_LINES lines (optimization)
                for _ in range(HEADER_SCAN_LINES):
                    line = f.readline()
                    # Look for the header line containing both DATE and TIME
                    if "DATE" in line and "TIME" in line:
                        p = line.split("DATE")[-1].split("TIME")
                        return f"📅 {p[0].strip()} {p[1].strip()}"
        except: 
            pass
        return ""
    
    def _estimate_completion(self, tail_text):
        """
        Parses the .sta tail to estimate time remaining based on linear extrapolation.
        Returns a tuple: (string_message, remaining_seconds, total_estimated_seconds)
        """
        try:
            # 1. Find the latest "ODB Field Frame Number X of Y"
            # We look for the last occurrence in the text
            frame_matches = list(re.finditer(r"ODB Field Frame Number\s+(\d+)\s+of\s+(\d+)", tail_text))
            if not frame_matches:
                return None, 0, 0
            
            latest_match = frame_matches[-1]
            current_frame = int(latest_match.group(1))
            total_frames = int(latest_match.group(2))
            
            if current_frame == 0:
                return "Calculating...", 0, 0

            # 2. Find the latest CPU TIME (HH:MM:SS) from the increment lines
            # Pattern: Int or Float increment number followed by scientific notation numbers and a time string
            # We look for lines like: "289463 1.000E-01 ... 01:35:45 ..."
            # We scan the lines *before* the frame match to find the associated time
            lines = tail_text.splitlines()
            latest_cpu_time_str = None
            
            # Simple regex to find the CPU time (Col 3 usually) in data lines
            # Looks for: [Int] [Float] [Float] [HH:MM:SS]
            # We iterate backwards from the end of the file
            for line in reversed(lines):
                parts = line.split()
                # Check if this is a data line (starts with digit, has time format in index 2 or 3)
                if len(parts) > 3 and parts[0].isdigit() and ":" in parts[3]: 
                    # Index 3 is CPU Time in the provided sample (Col 4 if 1-based, index 3 if 0-based)
                    # Sample: 289463  1.000E-01 1.000E-01  01:35:45 ...
                    latest_cpu_time_str = parts[3] 
                    break
            
            if not latest_cpu_time_str:
                return None, 0, 0

            # 3. Convert CPU Time to Seconds
            h, m, s = map(int, latest_cpu_time_str.split(':'))
            elapsed_seconds = h * 3600 + m * 60 + s
            
            # 4. Extrapolate
            seconds_per_frame = elapsed_seconds / current_frame
            remaining_frames = total_frames - current_frame
            remaining_seconds = seconds_per_frame * remaining_frames
            total_est_seconds = elapsed_seconds + remaining_seconds
            
            # 5. Format Output
            # Helper to format seconds to HH:MM
            def fmt(sec):
                hm = int(sec // 60)
                return f"{hm // 60}h {hm % 60}m"

            msg = f"⏱️ Left: {fmt(remaining_seconds)} (Tot: {fmt(total_est_seconds)})"
            return msg, remaining_seconds, total_est_seconds

        except Exception as e:
            return None, 0, 0
    
    def get_details(self, d, j, cached_start=""):
        """Parses the .sta file for detailed progress using cached start time."""
        sta = os.path.join(d, j + ".sta")
        if not os.path.exists(sta): return "Waiting..."
        try:
            start = f"{cached_start}\n" if cached_start else ""
            tail_text = self._read_tail_text(sta) # Read once
            tail_lines = tail_text.splitlines()
            
            dat, fra, step = "Reading...", "No Frames", "1"
            found_dat = False
            
            # Parsing logic
            for line in reversed(tail_lines):
                parts = line.split()
                if not found_dat and len(parts) > 7 and parts[0].isdigit():
                    dat = f"Time: {parts[1]}s | dt: {parts[4]}\nKE: {parts[6]} | TE: {parts[7]}"
                    found_dat = True
                if fra == "No Frames" and "ODB Field Frame Number" in line and len(parts) > 6:
                    fra = f"Frames: {parts[4]}/{parts[6]}"
                if line.strip().startswith("STEP") and "ORIGIN" in line and len(parts) > 1 and parts[1].isdigit():
                    step = parts[1]
                if found_dat and fra != "No Frames":
                    break
            
            # --- Time Estimate ---
            est_msg, _, _ = self._estimate_completion(tail_text)
            if est_msg:
                fra += f"\n{est_msg}"
            # -----------------------------------

            return f"{start}Step {step} | {dat}\n📁 {fra}"
        except: return "Error"
    
    def get_full_summary(self, watch_dir, filter_mode="ALL"):
        """
        Scans directory for .sta files and summarizes based on filter.
        filter_mode: "ALL", "RUNNING", "COMPLETED", "ERROR"
        """
        files = [f for f in os.listdir(watch_dir) if f.endswith('.sta')]
        if not files: return "No Abaqus jobs found."
        
        # Sort by modification time (newest first)
        files.sort(key=lambda x: os.path.getmtime(os.path.join(watch_dir, x)), reverse=True)
        
        summary = []
        count = 0
        
        # Iterate through files until we find MAX_SUMMARY_JOBS matches
        for f in files:
            if count >= MAX_SUMMARY_JOBS:
                break
                
            job = f.replace('.sta', '')
            lck = os.path.join(watch_dir, job + '.lck')
            sta = os.path.join(watch_dir, f)
            
            is_running = os.path.exists(lck)
            status_icon = "❓"
            category = "UNKNOWN"
            start_t, end_t = "-", "-"
            
            try:
                # Determine Status from tail
                tail = self._read_tail_text(sta)
                if is_running:
                    status_icon = "🟢 Running"
                    category = "RUNNING"
                elif "COMPLETED SUCCESSFULLY" in tail:
                    status_icon = "✅ Completed"
                    category = "COMPLETED"
                elif "ERROR" in tail or "Aborted" in tail:
                    status_icon = "❌ Aborted"
                    category = "ERROR"
                else:
                    status_icon = "⚠️ Stopped"
                    category = "ERROR" # Treat generic stops as errors/attentions

                # --- FILTER CHECK ---
                if filter_mode != "ALL" and filter_mode != category:
                    continue
                # --------------------

                time_est_str = ""
                if is_running:
                    est_msg, _, _ = self._estimate_completion(tail)
                    if est_msg:
                        # Extract just the "Left" part for the summary to save space
                        # est_msg looks like "⏱️ Left: 4h 20m (Tot: 10h)"
                        # We just want "⏱️ ~4h 20m left"
                        time_est_str = f"\n{est_msg.split('(')[0].strip()}"

                # Start time
                with open(sta, 'r', encoding='utf-8', errors='ignore') as file:
                    for _ in range(START_SCAN_LINES):
                        line = file.readline()
                        if not line: break
                        if "DATE" in line and "TIME" in line:
                            parts = line.split()
                            try:
                                d_idx, t_idx = parts.index("DATE"), parts.index("TIME")
                                start_t = f"{parts[d_idx+1]} {parts[t_idx+1]}"
                            except: pass
                            break

                # End time
                if not is_running:
                    for line in reversed(tail.splitlines()):
                        if "DATE" in line and "TIME" in line:
                            parts = line.split()
                            try:
                                d_idx, t_idx = parts.index("DATE"), parts.index("TIME")
                                end_t = f"{parts[d_idx+1]} {parts[t_idx+1]}"
                            except: pass
                            break
            except: continue

            # Format Entry
            msg = f"**`{job}`**\nStatus: {status_icon}\n📅 Start: {start_t}"
            
            if is_running and time_est_str:
                msg += time_est_str
            
            if not is_running: msg += f"\n🏁 End: {end_t}"
            summary.append(msg)
            count += 1
            
        if not summary:
            return f"No jobs found matching: {filter_mode}"
            
        return "\n\n".join(summary)

    def gen_plot(self, d, j):
        """Generates a convergence plot (Time vs dt) using Matplotlib."""
        sta = os.path.join(d, j + ".sta")
        if not os.path.exists(sta): return None
        out = os.path.join(d, "job_watcher")
        os.makedirs(out, exist_ok=True)
        t, dt = [], []
        fig = None
        try:
            with open(sta, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    p = line.split()
                    if len(p)>7 and p[0].isdigit():
                        try: t.append(float(p[1])); dt.append(float(p[4]))
                        except: continue
            if not t: return None
            fig = plt.figure(figsize=(10, 5))
            plt.plot(t, dt, color='#d62728', linewidth=1)
            plt.yscale('log'); plt.title(f"Stability: {j}"); plt.grid(True, alpha=0.4)
            path = os.path.join(out, f"plot_{j}.png")
            plt.savefig(path, bbox_inches='tight')
            return path
        except:
            return None
        finally:
            if fig is not None:
                plt.close(fig)

if __name__ == "__main__":
    app = AbaqusWatcherApp()
    app.mainloop()