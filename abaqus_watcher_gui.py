# Copyright 2026 Souvik Biswas
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
ABAQUS Watcher GUI
==================

A modern, cross-platform Desktop GUI application for monitoring SIMULIA ABAQUS simulation jobs
remotely via Telegram Bot API. Provides real-time job status tracking, convergence analysis,
time estimation, and remote control capabilities.

Overview
--------
This application watches a designated directory for ABAQUS lock files (.lck) and status files
(.sta), parsing job progress and sending notifications through a Telegram bot. It runs
unobtrusively in the system tray with a customizable dark/light mode interface.

Key Features
------------
- **Real-time Job Monitoring:** Automatically detects job starts, completions, and failures
- **Time Estimation:** Linear extrapolation of remaining job duration based on ODB frame progress
- **Convergence Plots:** Generates matplotlib graphs showing increment performance
- **Remote Control:** Execute Telegram commands from anywhere to check status or terminate jobs
- **Secure Credential Storage:** OS-level keyring integration (Windows Credential Manager, macOS
  Keychain, Linux Secret Service) - no plain-text tokens
- **System Tray Integration:** Minimize to tray for background operation without taskbar clutter
- **Single Instance Enforcement:** Prevents multiple copies from running simultaneously
- **Deployment-Aware Updates:** Script mode supports auto-updates; EXE mode provides download links
- **Heartbeat Notifications:** Periodic status updates for long-running jobs (configurable interval)

Platform Support
----------------
- **Primary:** Windows 10/11 (fully tested)
- **Compatible:** Linux, macOS (community tested)
- **Python:** 3.10+ required (tested with 3.10, 3.11, 3.12, 3.14)

Dependencies
------------
Install all requirements with:
    pip install -r requirements.txt

Core packages:
    - customtkinter (modern themed Tkinter widgets)
    - packaging (semantic version comparison)
    - requests (Telegram API, GitHub API)
    - matplotlib (convergence plot generation)
    - keyring (OS-level credential storage)
    - pystray (system tray functionality)
    - Pillow (image processing for icons)

Usage
-----
Run the application:
    python abaqus_watcher_gui.py

Or use the pre-built executable (Windows only):
    ABAQUS_Watcher_GUI.exe

For complete setup instructions and Telegram bot configuration, see:
    https://github.com/daadaan/ABAQUS_Watcher_GUI#readme

Author & License
----------------
Author: Souvik Biswas (@daadaan)
License: Apache License 2.0
Repository: https://github.com/daadaan/ABAQUS_Watcher_GUI

For bug reports and feature requests:
    https://github.com/daadaan/ABAQUS_Watcher_GUI/issues
"""

from __future__ import annotations

# ==================== THREADING MODEL ====================
# CRITICAL FOR GUI STABILITY - NEVER VIOLATE THESE RULES:
#
# MAIN THREAD (GUI Event Loop):
# - All Tk/customtkinter widget operations (configure, pack, destroy)
# - User interaction handling (button clicks, text entry)
# - Widget creation and layout management
#
# BACKGROUND THREADS (Daemon):
# - Directory polling (8-second intervals)
# - Network calls (Telegram API, GitHub API)
# - File I/O operations (.sta/.lck parsing)
# - Long-running computations (plot generation)
#
# THREAD COMMUNICATION:
# - Background → Main: Use self.after(0, callback) to schedule UI updates
# - Main → Background: Use threading.Event for stop signals
# - NEVER call widget methods directly from background threads
# - NEVER block the main thread with time.sleep() or network calls
# ==========================================================

import customtkinter as ctk
import tkinter as tk
import tkinter.font as tkFont
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
# Application Identity
# Used for keyring service name, window title, and API User-Agent headers
APP_NAME = "ABAQUS Watcher GUI"

# GitHub Repository (format: "owner/repo")
# Used for: Update checks, release downloads, issue tracker links
GITHUB_REPO = "daadaan/ABAQUS_Watcher_GUI"

# Semantic Version String (format: "MAJOR.MINOR.PATCH")
# CRITICAL: Must match Git release tags (without 'v' prefix) for update detection
CURRENT_VERSION = "2.2.0"

# Configuration File Location Strategy
# WHY %LOCALAPPDATA%:
# - User-specific (no admin rights needed)
# - Survives application updates and reinstalls
# - Backed up with Windows user profile
# - Standard convention for Windows applications
# - Automatically available as environment variable
#
# TYPICAL PATH: C:\Users\YourName\AppData\Local\ABAQUSWatcherGUI\abaqus_watcher_config.json
# LIMITATION: The LOCALAPPDATA environment variable is Windows-only; on Linux/macOS the
#             os.environ[] lookup raises KeyError and the application will crash at startup.
app_data_dir = os.path.join(os.environ["LOCALAPPDATA"], "ABAQUSWatcherGUI")
os.makedirs(app_data_dir, exist_ok=True)  # Create directory tree if missing (recursive)
CONFIG_FILE = os.path.join(app_data_dir, "abaqus_watcher_config.json")

# Performance Constants - Tuned for typical ABAQUS usage patterns
# Adjust these values if you experience performance issues or need different behavior

# Tail Reading Limit (bytes)
# Rationale: 250KB covers ~3000 lines (last 30-60 increments) of typical .sta files
# Impact: Reading only tail provides 10-50x speedup vs. full file reads
# Trade-off: Very old increment data will not be accessible (rarely needed)
MAX_TAIL_BYTES = 250_000

# Console Buffer Limit (lines)
# Rationale: 500 lines = ~2-4 hours of activity at typical logging rate
# Impact: Prevents Text widget memory growth from unlimited history
# Trade-off: Oldest log entries are discarded when limit exceeded
MAX_CONSOLE_LINES = 500

# Job Summary Limit (count)
# Rationale: 15 jobs fits within Telegram's 4096-character message limit
# Impact: Prevents "Message Too Long" errors from Telegram API
# Trade-off: Oldest jobs beyond limit will not appear in /status_all
MAX_SUMMARY_JOBS = 15

# Header Scan Depth (lines)
# Rationale: Start date typically appears within first 30 lines of .sta file
# Impact: Avoids scanning entire file for header information
# Trade-off: Non-standard ABAQUS output may not be detected (rare)
HEADER_SCAN_LINES = 30

# Job Summary Scan Depth (lines)
# Rationale: Balances accuracy of start time detection with read performance
# Impact: Faster job detection during /status commands
# Trade-off: May miss start time if header is unusually large
START_SCAN_LINES = 200
# =================================================

class AbaqusWatcherApp(ctk.CTk):
    """
    Root application window for ABAQUS Watcher GUI.

    Inherits from ``customtkinter.CTk`` (the themed Tk root window) and owns the
    entire application lifecycle: UI construction, configuration persistence,
    background monitoring thread, Telegram communication, and system-tray
    integration.

    Responsibilities
    ----------------
    - Build and manage the tabbed UI (Monitor / Config / Help).
    - Start/stop the background ``run_loop`` thread that scans for .lck/.sta files.
    - Send job lifecycle notifications (start, completion, heartbeat) via Telegram.
    - Poll the Telegram Bot API for remote commands (/status, /kill, etc.).
    - Persist non-sensitive settings to JSON and credentials to the OS keyring.
    - Minimize to the system tray and restore via ``pystray``.

    Threading contract
    ------------------
    All widget operations **must** happen on the main (Tk event-loop) thread.
    Background threads communicate back to the UI exclusively through
    ``self.after(0, callback)``.
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
        
        # --- Font Definitions (instance attributes used across all widgets) ---
        self.font_head = ("Roboto Medium", 14)
        self.font_body = ("Roboto", 12)
        self.font_mono = ("Consolas", 11)  # Monospace for logs
        self.font_bold = ("Roboto", 12, "bold")
        self.font_small = ("Roboto", 10)

        # --- State Variables (Thread-Safe Management) ---
        
        # Watcher Thread: Background job monitoring loop
        # Type: threading.Thread | None
        # Lifecycle: Created in toggle_watcher(), terminates when stop_event is set
        self.watcher_thread = None
        
        # Stop Signal: Thread-safe flag to coordinate shutdown
        # Type: threading.Event
        # Usage: Set in toggle_watcher()/quit_app(), checked in run_loop()
        # IMPORTANT: Always use .set()/.clear()/.is_set() for thread safety
        self.stop_event = threading.Event()
        
        # UI State: Indicates if monitoring is currently active
        # Type: bool
        # CRITICAL: Only modified from main thread, used to update button states
        self.is_running = False
        
        # Job Heartbeat Tracker: Maintains state for each active job
        # Type: dict[str, dict[str, Any]]
        # Structure: {"job_name": {"last_hb": float, "start_date": str}}
        # Purpose: Tracks last heartbeat time and caches start date to avoid re-parsing
        # Cleanup: Entries removed when job finishes (no .lck file found)
        self.job_heartbeats = {}
        
        # Telegram Polling Offset: Prevents processing duplicate messages
        # Type: int
        # Behavior: Incremented with each processed update, sent in next getUpdates call
        # Ref: https://core.telegram.org/bots/api#getupdates
        self.last_telegram_update_id = 0
        
        # Tray Thread: Runs pystray icon event loop
        # Type: threading.Thread | None
        # CRITICAL: pystray.Icon.run() is blocking, must run in separate daemon thread
        # Lifecycle: Created when minimizing to tray, stopped when restoring window
        self.tray_thread = None

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
        self.container_jobs.pack_propagate(False)  # Maintains fixed height

        # Scrollable frame for job list - automatically adds scrollbar when content overflows
        self.frame_jobs_list = ctk.CTkScrollableFrame(self.container_jobs, fg_color=("white", "gray20"))
        self.frame_jobs_list.pack(fill="both", expand=True)
        # Resize the built-in vertical scrollbar to match the horizontal one (12 px)
        self.frame_jobs_list._scrollbar.configure(width=12)

        # Placeholder label for initial state (replaced when jobs are detected)
        ctk.CTkLabel(self.frame_jobs_list, text="Watcher Stopped", text_color="gray").pack(pady=20)

        # Persistent horizontal scrollbar for the job name column.
        # Lives outside container_jobs so it sits at the very bottom of the job activity
        # monitor area, below all job rows. Wired to canvases in update_job_table().
        self.hscroll_jobs = ctk.CTkScrollbar(
            self.tab_monitor, orientation="horizontal", height=12,
            command=self._on_jobs_hscroll,
        )
        self.hscroll_jobs.pack(padx=10, pady=(0, 2), fill="x")

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
        ctk.CTkLabel(parent, text=label, anchor="w", font=self.font_bold, text_color=("gray50", "gray50")).pack(padx=5, pady=(5,0), fill="x")
        
        # Row frame: transparent background so it inherits the parent colour
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.pack(padx=5, pady=0, fill="x")
        
        entry = ctk.CTkEntry(frame, height=34, font=self.font_body)
        entry.pack(side="left", fill="x", expand=True)
        entry.insert(0, default)
        
        # Add visibility toggle for sensitive fields
        if secret:
            entry.configure(show="●")
            btn_eye = ctk.CTkButton(frame, text="👁", width=34, height=34, 
                                    fg_color=("gray80", "gray30"), hover_color=("gray70", "gray40"), 
                                    text_color=("black", "white"),
                                    command=lambda: self.toggle_password(entry, btn_eye))
            btn_eye.pack(side="right", padx=(5, 0))
        
        # Dynamically assign the entry widget to the class using the provided variable name
        setattr(self, var_name, entry)

    def toggle_password(self, entry, btn):
        """Toggle a CTkEntry between bullet-masked ("●") and plain-text display."""
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
        Checks GitHub Releases API for newer versions and handles updates based on deployment mode.
        
        FLOW:
        1. Fetch latest release metadata from GitHub API
        2. Normalize version strings (strip 'v' prefix, pre-release tags)
        3. Compare using semantic versioning (handles 1.10 > 1.9 correctly)
        4. If newer version found:
           - EXE: Open browser to GitHub Releases download page
           - Script: Download raw .py file, validate, overwrite current file
        
        DEPLOYMENT MODES:
        - Frozen (EXE): User must manually download and replace executable
          - Rationale: Can't self-replace running executable on Windows (file lock)
        - Script (.py): Automatic in-place update with restart
          - Rationale: Python script can overwrite itself while running
        
        SECURITY:
        - Uses User-Agent header to avoid GitHub rate limiting
        - Validates downloaded content before overwriting (checks for class definition)
        - Conservative timeouts prevent hanging on network issues
        
        RUNS IN: Background thread (daemon) to avoid blocking UI
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
        Downloads raw Python script from GitHub and performs in-place update.
        
        PRECONDITION: Only called when NOT running as frozen executable
        
        PROCESS:
        1. Construct URL to raw file on GitHub (using tag reference)
        2. Download file content with timeout and User-Agent header
        3. Validate content (must contain class definition)
        4. Overwrite current script file (__file__)
        5. Prompt user for restart using os.execv()
        
        SECURITY MEASURES:
        - Content validation: rejects download unless it contains the expected
          class definition (prevents partial downloads or wrong files)
        - Overwrite confirmation happens in the caller (check_updates) before
          this method is invoked
        - User confirmation required before restarting via os.execv()
        
        ERROR HANDLING:
        - HTTP errors: Show error dialog, keep current version
        - File write errors: Show error dialog, current version unchanged
        - Validation failure: Show error dialog, abort update
        
        LIMITATIONS:
        - Requires write permission to script file (typically yes for user files)
        - Network connection required (no offline mode)
        - Filename must match repository exactly (abaqus_watcher_gui.py)
        
        Args:
            tag_name: Git tag reference (e.g., "v1.3.3") from GitHub Release
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

            new_code = resp.text

            # Content validation: ensure the download contains the expected class
            # definition before overwriting the script.
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

    def start_single_instance_server(self):
        """Starts a background listener to wake up the app if a second instance starts."""
        def listen():
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                # Bind to localhost only
                server.bind(("127.0.0.1", 54321))
                server.listen(1)
                
                while True:
                    # Wait for a connection (blocking)
                    conn, _ = server.accept()
                    # We don't need to read data; the connection itself is the signal
                    conn.close()
                    
                    # Trigger the GUI restore on the main thread
                    self.after(0, self.show_window_from_tray)
            except Exception:
                pass  # socket.bind() failed: port already taken by another process,
                      # or the socket was closed because the app is shutting down.

        # Run as daemon so it dies when the app closes
        threading.Thread(target=listen, daemon=True).start()
    
    # --- SYSTEM TRAY LOGIC ---
    def _create_tray_icon(self):
        """
        Creates a fresh pystray.Icon instance for system tray integration.
        
        PYSTRAY LIMITATIONS (WHY WE CREATE NEW INSTANCES):
        - Each Icon instance can only be run() once
        - After stop() is called, the instance becomes unusable
        - Must create fresh instance for each minimize-to-tray cycle
        
        ICON DESIGN:
        - 64x64 RGBA image with transparent background
        - Circular shape with brand color (#6769a2)
        - Matches window icon for visual consistency
        
        MENU STRUCTURE:
        - "Open Monitor" (default=True): Triggered on double-click or single click
        - "Quit": Cleanly shuts down application and threads
        
        THREADING:
        - Caller must run icon.run() in a separate daemon thread
        - icon.run() is blocking and pumps the tray icon event loop
        - Must call icon.stop() before destroying icon or exiting app
        
        Returns:
            pystray.Icon: Configured but not yet running icon instance
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
        """
        Restore the main window from the system tray.

        Stops the running ``pystray`` icon (which is single-use), then
        schedules ``deiconify`` on the main thread to make the window visible
        again.  The ``icon`` and ``item`` parameters are provided by pystray's
        menu callback signature and are not used directly.

        Args:
            icon: pystray.Icon instance passed by the tray menu callback (unused).
            item: pystray.MenuItem that triggered the call (unused).
        """
        if self.tray_icon is not None:
            self.tray_icon.stop()
        self.after(0, self.deiconify)

    def change_theme(self, new_theme: str):
        """Apply a customtkinter appearance mode ('System', 'Dark', or 'Light')."""
        ctk.set_appearance_mode(new_theme)

    def on_closing(self):
        """Handle the window close (X) button by delegating to ``quit_app``."""
        self.quit_app()

    def quit_app(self, icon=None, item=None):
        """
        Cleanly shut down the application.

        Stops the ``pystray`` tray icon if active, signals the background
        monitoring thread to exit via ``stop_event``, then calls
        ``self.quit()`` to end the Tkinter main loop.  The ``icon`` and
        ``item`` parameters support being called directly from a pystray
        menu item.

        Args:
            icon: pystray.Icon instance (passed by tray menu callback; unused).
            item: pystray.MenuItem that triggered the call (unused).
        """
        if self.tray_icon:
            self.tray_icon.stop()
        self.stop_event.set()
        self.quit()

    # --- CONFIGURATION IO ---
    def load_config(self):
        """
        Loads configuration from two sources:
        1. Non-sensitive settings from JSON file (directory, heartbeat, theme)
        2. Sensitive credentials from the OS keyring backend via the keyring library
           (Windows Credential Manager, macOS Keychain, or Linux Secret Service)
        
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
        2. Sensitive credentials to the OS keyring backend
           (Windows Credential Manager, macOS Keychain, or Linux Secret Service)
        
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
        """
        Test HTTP reachability by sending a GET request to Google's
        ``/generate_204`` endpoint (returns 204 No Content with no body,
        minimising bandwidth).  Logs "Online." on success or "Offline." if
        the request raises any exception.

        Runs in a short-lived daemon thread to avoid blocking the UI.
        """
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

    def _on_jobs_hscroll(self, *args):
        """Relay the persistent horizontal scrollbar's commands to all job name canvases."""
        for c in getattr(self, "_name_canvases", []):
            c.xview(*args)

    def update_job_table(self, job_data):
        """
        Updates the active jobs list UI with current job status and time estimates.

        Job names are rendered in canvas widgets that clip long text; a single
        CTkScrollbar (self.hscroll_jobs) below the entire job section lets the
        user pan all name cells simultaneously.

        IMPORTANT: Must be called from the main thread via self.after().

        Args:
            job_data: List of tuples [(job_name, time_remaining_str), ...]
        """
        # Clear all existing rows
        for w in self.frame_jobs_list.winfo_children():
            w.destroy()

        # Reset canvas list and scrollbar when there are no active jobs
        self._name_canvases: list[tk.Canvas] = []
        self.hscroll_jobs.set(0.0, 1.0)  # Full-width thumb = nothing to scroll

        # Show placeholder if no jobs are active
        if not job_data:
            ctk.CTkLabel(self.frame_jobs_list, text="No active jobs", font=self.font_small, text_color="gray").pack(pady=10)
            return

        NAME_COL_W = 150
        ROW_H = 20 

        # Use a smaller font for job name labels to keep rows tidy
        name_font = self.font_small  # ("Roboto", 10)

        # Measure pixel width of the longest job name to define the shared scroll region
        tk_font = tkFont.Font(font=name_font)
        max_text_w = max((tk_font.measure(name) for name, _ in job_data), default=NAME_COL_W)
        # Add a small right-padding; ensure virtual width is at least the visible width
        virtual_w = max(max_text_w + 8, NAME_COL_W)

        # Canvas background / text colour must match the surrounding frame and theme
        mode = ctk.get_appearance_mode()
        canvas_bg = "white" if mode == "Light" else "#333333"
        canvas_fg = "black" if mode == "Light" else "white"

        # Create a row for each active job
        for name, time_str in job_data:
            row = ctk.CTkFrame(self.frame_jobs_list, fg_color="transparent")
            row.pack(fill="x", pady=2)
            row.grid_columnconfigure(0, weight=1, minsize=NAME_COL_W)
            row.grid_columnconfigure(1, weight=0, minsize=50)

            # Left: Job name clipped inside a canvas (scrollbar drives panning)
            name_canvas = tk.Canvas(
                row,
                width=NAME_COL_W, height=ROW_H,
                bg=canvas_bg, highlightthickness=0, bd=0,
            )
            name_canvas.grid(row=0, column=0, padx=(5, 4), sticky="ew")
            name_canvas.create_text(4, ROW_H // 2, text=name, anchor="w",
                                    font=name_font, fill=canvas_fg)
            name_canvas.configure(scrollregion=(0, 0, virtual_w, ROW_H))
            # Wire canvas to the persistent scrollbar so its thumb reflects position
            name_canvas.configure(xscrollcommand=self.hscroll_jobs.set)
            self._name_canvases.append(name_canvas)

            # Right: Time estimate with colour coding (fixed width)
            color = "#10B981" if "h" in time_str or "m" in time_str else "gray"
            time_label = ctk.CTkLabel(row, text=time_str, font=self.font_mono,
                                      text_color=color, anchor="e", width=50)
            time_label.grid(row=0, column=1, padx=(0, 5), sticky="e")

        # Initialise scrollbar thumb to reflect the visible fraction
        visible_frac = min(NAME_COL_W / virtual_w, 1.0)
        self.hscroll_jobs.set(0.0, visible_frac)
    
    def run_loop(self):
        """
        Main background monitoring loop - runs continuously in daemon thread.
        
        EXECUTION MODEL:
        - Spawned by toggle_watcher() with daemon=True
        - Exits when self.stop_event.is_set() == True
        - 8-second poll interval (balances responsiveness vs. CPU usage)
        - All exceptions caught to prevent thread death
        
        RESPONSIBILITIES:
        1. Configuration Validation
           - Load settings from UI widgets
           - Verify token, chat_id, directory exist
           - Stop watcher if invalid (safety measure)
        
        2. Active Job Detection
           - Scan directory for .lck files (ABAQUS creates these for running jobs)
           - Build list of currently active job names
        
        3. Job Lifecycle Events
           - NEW: .lck file appears → Send start notification
           - FINISHED: .lck file disappears → Parse .sta for status, send completion notification
        
        4. Heartbeat Updates
           - Track last notification time for each job
           - Send periodic status updates for long-running jobs
           - Prevents "silent" failures (job hung but .lck still exists)
        
        5. Remote Control
           - Poll Telegram API for user commands (/status, /kill, etc.)
           - Process commands and send responses
        
        6. UI Synchronization
           - Update active jobs table with time estimates
           - Use self.after() to schedule updates on main thread
        
        THREAD-SAFETY:
        - All UI updates via self.after(0, callback)
        - Read-only access to UI widgets (get() calls)
        - Shared state (job_heartbeats) only accessed from this thread
        
        ERROR HANDLING:
        - Catches all exceptions to prevent thread crash
        - Logs errors to console
        - 5-second delay after exceptions (avoids spam)
        - Invalid config triggers watcher stop from main thread
        
        EDGE CASES:
        - Directory deleted during monitoring: active list becomes empty, no crash
        - Network failures: Telegram calls fail silently, retry next iteration
        - File read errors: Individual jobs fail, others continue
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
                    solver_type = self._get_solver_type(sta)
                    if solver_type == "STANDARD":
                        est_str, _, _ = self._estimate_completion_standard(watch_dir, job, tail)
                    else:
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
                time.sleep(8)  # Poll every 8 seconds
            except Exception as e:
                self.log(f"Loop Err: {e}")
                time.sleep(5)  # Longer delay on error to avoid spam

    # --- TELEGRAM HELPERS ---
    def send_tg(self, token, chat_id, text, icon, img=None, silent=True):
        """
        Sends notifications to Telegram via Bot API (text or photo messages).
        
        API ENDPOINTS USED:
        - sendMessage: Text-only notifications
        - sendPhoto: Image with caption (for convergence plots)
        
        MARKDOWN SUPPORT:
        - Uses parse_mode="Markdown" (Telegram Bot API v1 Markdown)
        - Supports: **bold**, _italic_, `inline code`, [text](url)
        - Note: v1 Markdown is permissive; only _ * ` [ need escaping inside formatted spans
        
        MESSAGE STRUCTURE:
        - Format: "[icon] [text]" for text messages
        - Format: "[icon] [text]" as caption for photos
        - Icon: Emoji for visual classification (🚀 start, ✅ done, ⚠️ error)
        
        SILENT NOTIFICATIONS:
        - When True: No sound/vibration on recipient device
        - Rationale: Avoids disturbing user during work hours
        - User can still see notification in Telegram app
        
        ERROR HANDLING:
        - All exceptions caught and logged (prevents watcher thread crash)
        - Network timeouts: 10 s for text messages, 20 s for photo uploads
        - File cleanup: Deletes temporary plot files after sending
        - Logs only exception type (avoids verbose stack traces)
        
        THREADING:
        - Called from watcher thread (background)
        - Conservative timeout prevents blocking main monitoring loop
        - Failures are non-fatal (logged but watcher continues)
        
        RATE LIMITING:
        - Telegram Bot API: 30 messages/second limit
        - Our usage: ~1-5 messages/hour typically (well within limit)
        - Edge case: Rapid job starts/stops may approach limit
        
        Args:
            token: Bot API token from @BotFather (format: "123456:ABC-DEF...")
            chat_id: Numeric user/group ID (can be negative for groups)
            text: Message content (max 4096 chars for text, 1024 for captions)
            icon: Emoji prefix for message classification
            img: Optional path to image file (PNG/JPG, automatically deleted after send)
            silent: Disable notification sound/vibration (default: True)
        
        Raises:
            None: All exceptions are caught and logged internally
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
        Polls Telegram Bot API for user commands and executes them.
        
        POLLING MECHANISM:
        - Uses getUpdates API with long polling (timeout=1s)
        - Offset-based deduplication (only fetches new messages)
        - Updates self.last_telegram_update_id to acknowledge processed messages
        - Non-blocking: 3-second total timeout prevents watcher loop delays
        
        COMMAND CATEGORIES:
        
        1. List Commands (Filter by Status):
           - /status_all: All jobs regardless of status
           - /status_running: Jobs with .lck files present
           - /status_completed: Jobs that finished successfully
           - /status_error: Jobs that terminated with errors
           Response: Text summary (limited to MAX_SUMMARY_JOBS entries)
        
        2. Detail Command:
           - /status <job_name>: Individual job statistics
           Response: Convergence plot (PNG) + detailed text
        
        3. Control Command:
           - /kill <job_name>: Terminate a running job
           Response: Sends `abaqus terminate job=<name>` via subprocess, then confirms via Telegram
        
        SECURITY MEASURES:
        - Chat ID Verification: Only responds to configured chat
           - Prevents unauthorized users from controlling the app
           - Checked on every message before processing
        - Job Name Validation: All names validated with _is_safe_job_name()
           - Prevents path traversal attacks (../../etc/passwd)
           - Blocks command injection attempts
        - Rate Limiting: Implicit via 3-second monitoring loop
           - Prevents DoS via command flooding
        
        ERROR HANDLING:
        - All exceptions caught silently (prevents watcher crash)
        - Invalid commands: No response (avoids spam)
        - Network failures: Retry on next iteration (3s later)
        - Invalid job names: Send error message to user
        
        API CONSTRAINTS:
        - Message length: 4096 characters (enforced by MAX_SUMMARY_JOBS)
        - Photo size: 10MB max (convergence plots are ~100-500KB)
        - Timeout: 1s long polling + 3s total request timeout
        
        THREADING:
        - Called from run_loop() (background thread)
        - Safe to block briefly (3s timeout)
        - No UI updates (only network I/O and file reads)
        
        Args:
            token: Telegram Bot API token
            chat_id: Authorized chat/user ID (as string for comparison)
            watch_dir: Directory to scan for .sta/.lck files
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
        Validates job names from user input to prevent security vulnerabilities.
        
        ⚠️ SECURITY CRITICAL - THIS IS THE PRIMARY DEFENSE AGAINST:
        
        1. PATH TRAVERSAL ATTACKS:
           - Attack: /status ../../Windows/System32/hosts
           - Result: Would read sensitive system files
           - Prevention: Reject any job name containing slashes
        
        2. COMMAND INJECTION ATTACKS:
           - Attack: /kill job1; rm -rf /
           - Result: Would execute arbitrary shell commands
           - Prevention: Reject any job name with shell metacharacters
        
        3. NULL BYTE INJECTION:
           - Attack: /status job1\x00.sta
           - Result: Could truncate filename parsing
           - Prevention: Reject job names with null bytes (handled by fullmatch)
        
        WHITELIST APPROACH:
        - Only explicitly safe characters are allowed
        - Regex: ^[A-Za-z0-9._-]+$
        - Covers typical ABAQUS job naming conventions
        
        ALLOWED CHARACTERS:
        - Letters: A-Z, a-z (case-sensitive)
        - Numbers: 0-9
        - Separators: . (dot), _ (underscore), - (hyphen)
        - Rationale: Standard filename-safe characters across Windows/Linux
        
        BLOCKED CHARACTERS (Examples):
        - Path separators: / \\ (prevents directory traversal)
        - Shell metacharacters: ; | & $ ` ( ) < > (prevents command injection)
        - Wildcards: * ? [ ] { } (prevents glob expansion)
        - Whitespace: space, tab, newline (prevents argument splitting)
        - Null byte: \\x00 (prevents filename truncation)
        
        TYPICAL VALID JOB NAMES:
        - "Job-1" ✓
        - "analysis_v2.3" ✓
        - "beam_model" ✓
        - "test.inp" ✓
        
        TYPICAL INVALID JOB NAMES:
        - "../../../etc/passwd" ✗ (contains slashes)
        - "job1; rm -rf /" ✗ (contains semicolon and spaces)
        - "job name" ✗ (contains space)
        - "job|other" ✗ (contains pipe)
        
        WHERE THIS IS ENFORCED:
        - check_telegram(): Before processing /status and /kill commands
        - File operations: Before constructing paths like f"{job}.sta"
        - Subprocess calls: Before passing the name to `abaqus terminate job=<name>`
        
        Args:
            job: Job name from Telegram command or UI input
            
        Returns:
            bool: True if job name matches whitelist pattern, False if suspicious
        """
        return bool(re.fullmatch(r"[A-Za-z0-9._-]+", job))

    def _read_tail_text(self, path: str, max_bytes: int = MAX_TAIL_BYTES) -> str:
        """
        Efficiently reads the last N bytes of large text files (typically .sta files).
        
        PERFORMANCE ANALYSIS:
        
        Full File Read (Naive Approach):
        - 10 MB file: ~500ms read + 100ms processing = 600ms
        - 100 MB file: ~5000ms read + 1000ms processing = 6000ms
        - Memory: Entire file loaded into RAM
        - Repeated every 3 seconds = unsustainable for large files
        
        Tail Read (This Method):
        - Any file size: ~10ms seek + 20ms read + 5ms decode = 35ms
        - Memory: Only 250KB loaded regardless of file size
        - 10-100x faster than full read for typical files
        - Enables sub-second response times in UI
        
        ABAQUS .STA FILE STRUCTURE:
        - Header: ~100-500 lines (metadata, model info, start time)
        - Body: N increment blocks (40-100 lines each)
        - Tail: Latest increment (always at end, newest data)
        - Total size: 100KB (small jobs) to 500MB (massive models)
        
        WHY 250KB TAIL IS SUFFICIENT:
        - Covers ~3000 lines of typical output
        - Represents last 30-60 increments (depends on increment detail)
        - All recent convergence data available
        - Latest increment always captured
        - Older increment history not needed for monitoring
        
        ALGORITHM:
        1. Get file size via tell() after seeking to end
        2. Seek backward from end (or to file start if smaller than max_bytes)
        3. Read remaining bytes into memory
        4. Decode to UTF-8 with error handling
        
        TRADE-OFFS:
        - ✓ Massive speedup for large files
        - ✓ Constant memory usage
        - ✓ Latest data always available
        - ✗ Historical increments beyond tail not accessible
        - ✗ Must re-read header separately for start time (cached once)
        
        ERROR HANDLING:
        - FileNotFoundError: Returns empty string (caller checks length)
        - PermissionError: Returns empty string (logged elsewhere)
        - UnicodeDecodeError: Uses 'ignore' strategy (graceful degradation)
        - Any other exception: Returns empty string (defensive)
        
        THREAD SAFETY:
        - Read-only operation (safe from any thread)
        - No shared state modified
        - File handle closed before returning (RAII pattern with 'with' statement)
        
        Args:
            path: Absolute path to text file (typically .sta file)
            max_bytes: Tail size in bytes (default: MAX_TAIL_BYTES = 250000)
            
        Returns:
            str: Decoded text from file tail, or "" if file unreadable
            
        Example:
            tail = self._read_tail_text("/path/to/Job-1.sta")
            if "COMPLETED SUCCESSFULLY" in tail:
                # Latest status indicates completion
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
        Determine the final status of a finished job by scanning the .sta file tail
        for ABAQUS completion keywords.

        Keyword mapping:
        - ``"COMPLETED SUCCESSFULLY"`` → ("SUCCESS", "✅", "Converged")
        - ``"ERROR"``                  → ("ABORTED", "🚨", "Check .msg")
        - Neither found               → ("TERMINATED", "⚠️", "Stopped")
          (e.g., job killed via Ctrl-C or OS signal before writing a final status line)
        - .sta file absent            → ("FINISHED", "⚠️", "No Data")

        Args:
            d: Watch directory containing the .sta file.
            j: Job name without extension.

        Returns:
            tuple[str, str, str]: (status_label, emoji_icon, detail_message)
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

    def _get_start_datetime(self, d: str, j: str) -> Optional[datetime]:
        """
        Parse the job start date/time from the .sta file header as a ``datetime``
        object for wall-clock elapsed-time calculations.

        Uses the same scan logic as ``_get_start_date_once`` but returns a
        ``datetime`` instead of a formatted string.  Returns ``None`` if the
        header line cannot be found or parsed.

        Args:
            d: Watch directory containing the .sta file.
            j: Job name without extension.

        Returns:
            datetime | None: Parsed start datetime in local time, or None.
        """
        sta = os.path.join(d, j + ".sta")
        if not os.path.exists(sta):
            return None
        try:
            with open(sta, 'r', encoding='utf-8', errors='ignore') as f:
                for _ in range(HEADER_SCAN_LINES):
                    line = f.readline()
                    if "DATE" in line and "TIME" in line:
                        p = line.split("DATE")[-1].split("TIME")
                        date_str = p[0].strip()
                        time_str = p[1].strip()
                        return datetime.strptime(f"{date_str} {time_str}", "%d-%b-%Y %H:%M:%S")
        except Exception:
            pass
        return None

    def _get_solver_type(self, sta_path):
        """Detect solver type from the first line of a .sta file."""
        if not os.path.exists(sta_path):
            return "UNKNOWN"
        try:
            with open(sta_path, 'r', encoding='utf-8', errors='ignore') as f:
                first_line = f.readline().strip().lower()
            if "abaqus/standard" in first_line:
                return "STANDARD"
            if "abaqus/explicit" in first_line:
                return "EXPLICIT"
        except Exception:
            pass
        return "UNKNOWN"

    def _parse_standard_sta_progress(self, tail_text):
        """
        Parse the latest Abaqus/Standard summary row from .sta tail text.

        Row format:
            STEP INC ATT SEVERE EQUIL TOTAL TOTAL_TIME STEP_TIME INC_OF_TIME
        """
        try:
            for line in reversed(tail_text.splitlines()):
                parts = line.split()
                if len(parts) < 9:
                    continue
                if not (parts[0].isdigit() and parts[1].isdigit()):
                    continue
                if not re.fullmatch(r"\d+U?", parts[2]):
                    continue
                if not (parts[3].isdigit() and parts[4].isdigit() and parts[5].isdigit()):
                    continue

                return {
                    "step": parts[0],
                    "increment": parts[1],
                    "attempt": parts[2],
                    "severe": parts[3],
                    "equil": parts[4],
                    "total_iters": parts[5],
                    "total_time": parts[6],
                    "step_time": parts[7],
                    "inc_time": parts[8],
                }
        except Exception:
            pass
        return None

    def _estimate_completion_standard(self, d, j, sta_tail_text="", actual_elapsed_sec: Optional[float] = None):
        """
        Estimate remaining duration for Abaqus/Standard jobs.

        Uses latest step progress from .sta and fraction-of-step from .msg:
            total_step_time ~= step_time_completed / fraction_of_step_completed
            remaining ~= total_step_time - step_time_completed

        When ``actual_elapsed_sec`` is provided (wall-clock seconds since job
        start) the displayed total is ``actual_elapsed_sec + remaining`` so
        the number reflects real-world duration rather than simulation step time.
        """
        msg_path = os.path.join(d, j + ".msg")
        if not os.path.exists(msg_path):
            return None, 0, 0

        try:
            current_step_time = 0.0

            # Prefer step time from .sta summary row
            if sta_tail_text:
                standard_row = self._parse_standard_sta_progress(sta_tail_text)
                if standard_row:
                    try:
                        current_step_time = float(standard_row["step_time"])
                    except Exception:
                        current_step_time = 0.0

            msg_tail = self._read_tail_text(msg_path)

            fraction_matches = list(re.finditer(
                r"FRACTION OF STEP COMPLETED\s+([+\-]?\d+(?:\.\d+)?(?:[Ee][+\-]?\d+)?)",
                msg_tail
            ))
            if not fraction_matches:
                return None, 0, 0

            fraction = float(fraction_matches[-1].group(1))
            if fraction <= 0:
                return "Calculating...", 0, 0

            # Fallback / alignment source from .msg
            step_completed_matches = list(re.finditer(
                r"STEP TIME COMPLETED\s+([+\-]?\d+(?:\.\d+)?(?:[Ee][+\-]?\d+)?)",
                msg_tail
            ))
            if step_completed_matches:
                step_completed = float(step_completed_matches[-1].group(1))
                if step_completed > 0:
                    current_step_time = step_completed

            if current_step_time <= 0:
                return None, 0, 0

            total_step_time = current_step_time / fraction
            remaining = max(total_step_time - current_step_time, 0.0)

            def fmt(sec):
                hm = int(sec // 60)
                return f"{hm // 60}h {hm % 60}m"

            # Total display: use actual wall-clock elapsed + remaining when available;
            # fall back to simulation step-time extrapolation otherwise.
            total_display = (actual_elapsed_sec + remaining) if actual_elapsed_sec is not None else total_step_time
            msg = f"⏱️ Left: {fmt(remaining)} (Tot: {fmt(total_display)})"
            return msg, remaining, total_display
        except Exception:
            return None, 0, 0
    
    def _estimate_completion(self, tail_text, actual_elapsed_sec: Optional[float] = None):
        """
        Estimates job completion time using linear extrapolation from ODB frame progress.
        
        ALGORITHM OVERVIEW:
        1. Find latest "ODB Field Frame Number X of Y" in tail
        2. Extract current frame (X) and total frames (Y)
        3. Find latest cumulative CPU time (HH:MM:SS) logged by the solver in the .sta file
           (ABAQUS reports cumulative wall-clock CPU time at each increment)
        4. Convert cumulative CPU time to total elapsed seconds
        5. Calculate: seconds_per_frame = elapsed / current_frame
        6. Extrapolate: remaining = (total - current) * seconds_per_frame
        
        LINEAR EXTRAPOLATION ASSUMPTIONS:
        - Each frame takes roughly the same amount of CPU time
        - Time step size remains relatively constant
        - Solver convergence rate doesn't change significantly
        - Hardware performance remains stable
        
        ACCURACY FACTORS:
        
        HIGH ACCURACY (±10% error):
        - Uniform increment sizes (fixed time stepping)
        - Constant material properties
        - Linear analysis (small deformations)
        - No contact or complex boundary conditions
        
        MODERATE ACCURACY (±25% error):
        - Variable increment sizes (adaptive time stepping)
        - Geometric nonlinearity
        - Moderate contact conditions
        
        LOW ACCURACY (±50%+ error):
        - Highly adaptive time stepping (early vs. late increments differ 100x)
        - Severe convergence issues (many cutbacks)
        - Complex contact with friction
        - Material failure (crack propagation)
        - First 10% of job (not enough data)
        
        PARSING STRATEGY:
        - ODB Frame Pattern: "ODB Field Frame Number\\s+(\\d+)\\s+of\\s+(\\d+)"
        - CPU Time Pattern: HH:MM:SS in increment summary lines
        - Scan backwards from tail to find latest data
        - Multiple frame entries may exist (use last one)
        
        OUTPUT FORMATS:
        - User-facing: "⏱️ Left: 2h 34m (Tot: 5h 12m)"
        - Numeric: (remaining_seconds, total_seconds) for further calculations
        
        EDGE CASES:
        - No ODB frames: Returns None (some jobs don't write ODB)
        - Current frame = 0: Returns None (division by zero prevention)
        - No CPU time found: Returns None (can't extrapolate)
        - Malformed data: Returns None (parsing exception caught)
        
        LIMITATIONS:
        - Requires ODB output frequency > 0 (some jobs have none)
        - Assumes linear progress (rarely true for complex analyses)
        - First few frames unreliable (solver startup overhead)
        - Cannot predict cutbacks or divergence
        
        Args:
            tail_text: Last N bytes of .sta file (from _read_tail_text)
            
        Returns:
            tuple: (message_str, remaining_sec, total_sec) or (None, 0, 0) if estimation fails
            
        Example Return Values:
            Success: ("⏱️ Left: 1h 23m (Tot: 4h 56m)", 4980, 17760)
            No Data: (None, 0, 0)
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

            # Total display: use actual wall-clock elapsed + remaining when available;
            # fall back to CPU-time-based elapsed otherwise.
            wall_elapsed = actual_elapsed_sec if actual_elapsed_sec is not None else elapsed_seconds
            total_est_seconds = wall_elapsed + remaining_seconds

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
        """
        Build a multi-line progress summary for a running or finished job.

        Reads the last ``MAX_TAIL_BYTES`` of the .sta file once, then makes a
        single backwards pass to extract:
        - The latest increment line (step time, dt, kinetic energy, total energy)
        - The latest ODB frame counter (current / total)
        - The current STEP number

        The completion estimate from ``_estimate_completion`` is appended when
        available.

        Args:
            d: Watch directory containing the .sta file.
            j: Job name without extension.
            cached_start: Pre-formatted start-date string from
                          ``_get_start_date_once`` (avoids re-reading header).

        Returns:
            str: Formatted status string ready for Telegram or the UI, or
                 "Waiting..." / "Error" on failure.
        """
        sta = os.path.join(d, j + ".sta")
        if not os.path.exists(sta): return "Waiting..."
        try:
            start = f"{cached_start}\n" if cached_start else ""
            tail_text = self._read_tail_text(sta) # Read once
            solver_type = self._get_solver_type(sta)

            # Compute actual wall-clock elapsed since the job started.
            # Used by both estimation methods so the displayed total reflects
            # real-world duration: actual_elapsed + projected_remaining.
            start_dt = self._get_start_datetime(d, j)
            actual_elapsed_sec: Optional[float] = (
                (datetime.now() - start_dt).total_seconds() if start_dt else None
            )

            # Abaqus/Standard path: parse latest summary-row progress from .sta
            # and estimate completion using .msg fraction-of-step progress.
            if solver_type == "STANDARD":
                standard_row = self._parse_standard_sta_progress(tail_text)
                if not standard_row:
                    return f"{start}Abaqus/Standard\nWaiting for summary rows..."

                est_msg, _, _ = self._estimate_completion_standard(d, j, tail_text, actual_elapsed_sec)
                eta = f"\n{est_msg}" if est_msg else ""

                return (
                    f"{start}Abaqus/Standard | Step {standard_row['step']}\n"
                    f"Inc: {standard_row['increment']} (Att: {standard_row['attempt']}) | Severe: {standard_row['severe']}\n"
                    f"Step Time: {standard_row['step_time']} | Δt: {standard_row['inc_time']}\n"
                    f"Iters (Equil/Total): {standard_row['equil']}/{standard_row['total_iters']}"
                    f"{eta}"
                )

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
            est_msg, _, _ = self._estimate_completion(tail_text, actual_elapsed_sec)
            if est_msg:
                fra += f"\n{est_msg}"
            # -----------------------------------

            return f"{start}Step {step} | {dat}\n📁 {fra}"
        except: return "Error"
    
    def get_full_summary(self, watch_dir, filter_mode="ALL"):
        """
        Scan the watch directory for .sta files and build a multi-job Telegram summary.

        Reads the tail of each .sta file to classify its status, then filters
        the results by ``filter_mode``.  A .lck file present alongside a .sta
        file indicates the job is still running.

        Results are sorted by .sta modification time (newest first) and capped at
        ``MAX_SUMMARY_JOBS`` entries to stay within Telegram's 4 096-character
        message limit.

        Args:
            watch_dir: Directory to scan for .sta files.
            filter_mode: One of ``"ALL"``, ``"RUNNING"``, ``"COMPLETED"``, or
                         ``"ERROR"``.

        Returns:
            str: Newline-separated Markdown-formatted job entries, or a
                 descriptive message if no matching jobs are found.
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
                    solver_type = self._get_solver_type(sta)
                    if solver_type == "STANDARD":
                        est_msg, _, _ = self._estimate_completion_standard(watch_dir, job, tail)
                    else:
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
        """
        Generates convergence plot (Increment Time vs. Time Step Size) for a completed job.
        
        PLOT DESIGN:
        - X-axis: Step Time (cumulative simulation time)
        - Y-axis: Time Step Size (dt) on logarithmic scale
        - Purpose: Visualize solver stability and time step adaptation
        - Interpretation: 
          - Horizontal lines = stable time stepping
          - Sudden drops = solver cutbacks (convergence issues)
          - Gradual increase = successful adaptive stepping
        
        MATPLOTLIB BACKEND:
        - Uses 'Agg' (non-interactive) backend set at module top
        - Why: Default backend opens Tk windows (conflicts with customtkinter)
        - Allows rendering without display (safe for background threads)
        
        MEMORY MANAGEMENT (CRITICAL):
        - Each plt.figure() allocates ~2-5 MB of memory
        - Without plt.close(), memory accumulates over time
        - 1000 plots without cleanup = 2-5 GB memory leak
        - SOLUTION: try-finally block ensures figure closure
        
        FIGURE LIFECYCLE:
        1. Create explicit figure: fig = plt.figure()
        2. Plot data: plt.plot(), plt.title(), etc.
        3. Save to file: plt.savefig()
        4. Close figure: plt.close(fig) in finally block
        5. Delete temp file after Telegram send
        
        OUTPUT LOCATION:
        - Directory: {watch_dir}/job_watcher/
        - Filename: plot_{job_name}.png
        - Automatically created if missing (os.makedirs exist_ok=True)
        - Temporary file deleted after Telegram transmission
        
        ERROR HANDLING:
        - File read errors: Return None (caller handles missing plot)
        - No data found: Return None (empty t list)
        - Plot generation errors: Return None, log exception
        - Figure always closed via finally block
        
        PARSING LOGIC:
        - Reads entire .sta file line-by-line (convergence plot = final summary)
        - Searches for "SUMMARY" lines with increment data
        - Extracts: increment number, step time, dt (time step size)
        - Filters scientific notation patterns
        
        THREAD SAFETY:
        - matplotlib.use('Agg') is thread-safe after initial set
        - Each figure is independent (no shared pyplot state)
        - Safe to call from background thread
        
        Args:
            d: Watch directory path (parent of .sta file)
            j: Job name without extension (e.g., "Job-1")
            
        Returns:
            str: Absolute path to generated PNG file, or None if generation failed
            
        Example:
            plot_path = self.gen_plot(watch_dir, "Job-1")
            if plot_path:
                self.send_tg(token, chat_id, "Convergence Plot", "📊", img=plot_path)
        """
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
    import socket
    
    # ==================== SINGLE INSTANCE ENFORCEMENT ====================
    # PREVENTS: Multiple app instances running simultaneously (confusing UX)
    # MECHANISM: TCP socket on localhost acts as inter-process lock
    #
    # FLOW:
    # 1. First Instance:
    #    - Connection to port 54321 fails (no server listening)
    #    - App starts normally
    #    - Starts background server listening on port 54321
    #
    # 2. Second Instance:
    #    - Connection to port 54321 succeeds (first instance listening)
    #    - Connection itself triggers first instance to restore from tray
    #    - Second instance exits immediately (sys.exit(0))
    #
    # WHY PORT 54321:
    # - Arbitrary high port (above 1024, no admin rights needed)
    # - Unlikely to conflict with other applications
    # - Could be made configurable if needed
    #
    # EDGE CASES:
    # - Port already in use by another app: Second instance will start anyway
    # - First instance crashes: Port released, new instance can start
    # - Firewall blocks localhost: No impact (localhost always allowed)
    # =====================================================================
    SINGLE_INSTANCE_PORT = 54321
    
    # 1. Try to connect to an existing instance
    try:
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.settimeout(0.5)  # Short timeout to avoid hanging
        client.connect(("127.0.0.1", SINGLE_INSTANCE_PORT))
        
        # If we reach here, the connection worked -> App is already running.
        # The connection itself woke up the existing instance (via listener thread).
        client.close()
        sys.exit(0)  # Close this second instance gracefully
        
    except (socket.error, ConnectionRefusedError):
        # Connection failed -> No instance running. We are the first.
        pass

    # 2. Start the App as the primary instance
    app = AbaqusWatcherApp()
    app.start_single_instance_server()  # Start listening for subsequent launches
    app.mainloop()  # Enter Tkinter event loop (blocking until quit)
