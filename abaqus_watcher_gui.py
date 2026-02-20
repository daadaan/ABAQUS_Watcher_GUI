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
# - Event-driven file monitoring (watchdog) + slow fallback scan
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
import re
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from typing import Any, Optional, TYPE_CHECKING, cast

import keyring
import matplotlib
import pystray
import requests
from packaging import version
from PIL import Image, ImageDraw, ImageTk

if TYPE_CHECKING:
    from watchdog.events import FileSystemEventHandler as WatchdogEventHandlerType
else:
    WatchdogEventHandlerType = Any

try:
    from watchdog.events import FileSystemEventHandler as RuntimeFileSystemEventHandler  # type: ignore[reportMissingImports]
    from watchdog.observers import Observer  # type: ignore[reportMissingImports]
    WATCHDOG_AVAILABLE = True
except Exception:
    # Graceful fallback if watchdog isn't installed or unsupported on platform.
    class RuntimeFileSystemEventHandler:
        pass

    Observer = None  # type: ignore[assignment]
    WATCHDOG_AVAILABLE = False

# Use non-interactive backend for plots to prevent GUI thread blocking
matplotlib.use('Agg')

import matplotlib.pyplot as plt

# ================= CONFIGURATION =================

APP_NAME = "ABAQUS Watcher GUI"
GITHUB_REPO = "daadaan/ABAQUS_Watcher_GUI"
CURRENT_VERSION = "3.0.0"  # Must match Git release tags (without 'v' prefix)

# Config file: %LOCALAPPDATA%\ABAQUSWatcherGUI\abaqus_watcher_config.json
# NOTE: LOCALAPPDATA is Windows-only; Linux/macOS will need adaptation.
app_data_dir = os.path.join(os.environ["LOCALAPPDATA"], "ABAQUSWatcherGUI")
os.makedirs(app_data_dir, exist_ok=True)
CONFIG_FILE = os.path.join(app_data_dir, "abaqus_watcher_config.json")

# Performance tuning
MAX_TAIL_BYTES = 250_000       # .sta tail read limit (~3000 lines)
MAX_CONSOLE_LINES = 500        # UI console buffer
MAX_SUMMARY_JOBS = 15          # Telegram summary cap (fits 4096-char limit)
HEADER_SCAN_LINES = 30         # Lines to scan for header metadata
START_SCAN_LINES = 200         # Lines to scan for job start time

# Event-driven monitoring tuning
EVENT_DEBOUNCE_SECONDS = 5.0   # Debounce rapid .sta/.msg write bursts
FALLBACK_SCAN_SECONDS = 45.0   # Fallback full scan interval
TELEGRAM_POLL_SECONDS = 2.0    # Telegram command poll cadence

BRAND_COLOR = "#6769a2"         # App icon colour (purple-blue)

# =================================================


def _create_brand_icon(size: int = 64) -> Image.Image:
    """Create the circular brand icon used for the window and system tray."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse([10, 10, size - 10, size - 10], fill=BRAND_COLOR)
    return img


def _fmt_duration(seconds: float) -> str:
    """Format *seconds* as ``'Xh Ym'``."""
    total_minutes = int(seconds // 60)
    return f"{total_minutes // 60}h {total_minutes % 60}m"


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

        # UI element type hints (assigned in create_ui / add_input)
        self.entry_token: Optional[ctk.CTkEntry] = None
        self.entry_chat_id: Optional[ctk.CTkEntry] = None
        self.entry_heartbeat: Optional[ctk.CTkEntry] = None
        self.entry_dir: Optional[ctk.CTkEntry] = None
        self.tray_icon: Any = None

        # --- Window Setup ---
        self.title(APP_NAME)
        self.geometry("320x580")
        self.resizable(False, False)

        # Window icon (keep reference to prevent GC)
        self._icon_photo = ImageTk.PhotoImage(_create_brand_icon())
        self.iconphoto(False, self._icon_photo)  # type: ignore[arg-type]

        self.configure(fg_color=("gray95", "gray15"))

        # --- Theme ---
        ctk.set_appearance_mode("System")
        ctk.set_default_color_theme("blue")

        # --- Font Definitions ---
        self.font_body = ("Roboto", 12)
        self.font_mono = ("Consolas", 11)
        self.font_time = ("Consolas", 12)
        self.font_bold = ("Roboto", 12, "bold")
        self.font_small = ("Roboto", 10)

        # --- State Variables ---
        self.watcher_thread = None
        self.stop_event = threading.Event()
        self.is_running = False
        self.job_heartbeats: dict[str, dict[str, Any]] = {}
        self.last_telegram_update_id = 0
        self.tray_thread = None

        # --- Config Variables (linked to UI widgets) ---
        self.var_tray_enabled = ctk.BooleanVar(value=False)
        self.var_theme = ctk.StringVar(value="System")

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

        self.lbl_status = ctk.CTkLabel(self.frame_status, text="STOPPED", text_color="#EF4444", font=("Roboto", 14, "bold"))
        self.lbl_status.pack(pady=8)

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

        # Fixed-height container prevents ScrollableFrame from expanding the layout
        self.container_jobs = ctk.CTkFrame(self.tab_monitor, height=150, fg_color="transparent")
        self.container_jobs.pack(padx=10, pady=(2, 2), fill="x")
        self.container_jobs.pack_propagate(False)

        self.frame_jobs_list = ctk.CTkScrollableFrame(self.container_jobs, fg_color=("white", "gray20"))
        self.frame_jobs_list.pack(fill="both", expand=True)
        self.frame_jobs_list._scrollbar.configure(width=12)

        ctk.CTkLabel(self.frame_jobs_list, text="Watcher Stopped", text_color="gray").pack(pady=20)

        # Shared horizontal scrollbar for all job name canvases
        self.hscroll_jobs = ctk.CTkScrollbar(
            self.tab_monitor, orientation="horizontal", height=12,
            command=self._on_jobs_hscroll,
        )
        self.hscroll_jobs.pack(padx=10, pady=(0, 2), fill="x")

        # --- LIVE ACTIVITY ---
        ctk.CTkLabel(self.tab_monitor, text="Live Activity", anchor="w", font=self.font_bold, text_color=("gray40", "gray60")).pack(padx=10, pady=(5, 2), fill="x")

        self.console = ctk.CTkTextbox(
            self.tab_monitor,
            width=280,
            height=90,
            font=self.font_mono,
            fg_color=("white", "black"),
            text_color=("black", "white"),  # type: ignore[arg-type]
            corner_radius=6,
            border_width=1,
            border_color=("gray80", "gray30"),
        )
        self.console.pack(padx=5, pady=(0, 0), fill="x")
        self.console.configure(state="disabled")

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
        Checks GitHub Releases API for newer versions.

        Compares the latest release tag against ``CURRENT_VERSION`` using
        semantic versioning.  In frozen (EXE) mode the user is directed to
        the download page; in script mode an in-place update is offered.

        Runs in a background daemon thread.
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
        Downloads the script from GitHub and performs an in-place update.

        Only called in script (non-frozen) mode.  Downloads the raw ``.py``
        file for *tag_name*, validates it contains the expected class
        definition, overwrites ``__file__``, and offers a restart.

        Args:
            tag_name: Git tag reference (e.g., ``"v1.3.3"``).
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
        Creates a fresh pystray.Icon for the system tray.

        A new instance is required each cycle because pystray icons
        cannot be reused after ``stop()``.

        Returns:
            pystray.Icon: Configured but not yet running icon instance.
        """
        menu = pystray.Menu(
            pystray.MenuItem("Open Monitor", self.show_window_from_tray, default=True),
            pystray.MenuItem("Quit", self.quit_app),
        )
        return pystray.Icon("AbaqusWatcherGUI", _create_brand_icon(), APP_NAME, menu)

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
        
        # Load sensitive credentials from the OS keyring backend
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
        3. Removes credentials from the OS keyring backend
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
        """Thread-safe logging to the UI console via ``self.after()``."""
        ts = datetime.now().strftime("%H:%M")
        self.after(0, lambda: self._update_console(f"[{ts}] {message}\n"))

    def _update_console(self, text):
        """Append *text* to the console and trim excess lines. Main thread only."""
        self.console.configure(state="normal")
        self.console.insert("end", text)

        current_lines = int(self.console.index('end-1c').split('.')[0])
        if current_lines > MAX_CONSOLE_LINES:
            self.console.delete("1.0", "2.0")

        self.console.see("end")
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
        """Start or stop the background monitoring thread."""
        if self.is_running:
            self.stop_event.set()
            self.is_running = False
            self.btn_start.configure(text="START WATCHER", fg_color="#15803d", hover_color="#14532d")
            self.lbl_status.configure(text="STOPPED", text_color="#EF4444")
            self.log("Stopped.")
        else:
            self.stop_event.clear()
            self.is_running = True
            self.btn_start.configure(text="STOP WATCHER", fg_color="#DC2626", hover_color="#EF4444")
            self.lbl_status.configure(text="RUNNING", text_color="#10B981")
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
            time_label = ctk.CTkLabel(row, text=time_str, font=self.font_time,
                                      text_color=color, anchor="e", width=50)
            time_label.grid(row=0, column=1, padx=(0, 5), sticky="e")

        # Initialise scrollbar thumb to reflect the visible fraction
        visible_frac = min(NAME_COL_W / virtual_w, 1.0)
        self.hscroll_jobs.set(0.0, visible_frac)
    
    def run_loop(self):
        """Event-driven background monitoring loop with watchdog + fallback scan."""
        self.log("Monitoring active.")

        if not self.entry_token or not self.entry_chat_id or not self.entry_dir or not self.entry_heartbeat:
            self.log("Err: UI not initialized.")
            self.after(0, self.toggle_watcher)
            return

        token = self.entry_token.get().strip()
        chat_id = self.entry_chat_id.get().strip()
        watch_dir = self.entry_dir.get().strip()
        try:
            hb = int(self.entry_heartbeat.get().strip())
        except Exception:
            hb = 3600

        if not token or not chat_id or not os.path.exists(watch_dir):
            self.log("Err: Check config.")
            self.after(0, self.toggle_watcher)
            return

        self.job_heartbeats = {}

        state_lock = threading.Lock()
        dirty_jobs: dict[str, float] = {}
        refresh_requested = threading.Event()
        refresh_requested.set()  # Force initial full scan

        def mark_job_dirty(job_name: str):
            with state_lock:
                dirty_jobs[job_name] = time.time()
            refresh_requested.set()

        def request_refresh():
            refresh_requested.set()

        class AbaqusEventHandler(RuntimeFileSystemEventHandler):
            @staticmethod
            def _ext_and_job(path: str) -> tuple[str, str]:
                base = os.path.basename(path)
                job, ext = os.path.splitext(base)
                return ext.lower(), job

            def _handle_path(self, path: str, event_type: str):
                ext, job = self._ext_and_job(path)
                if ext == ".lck":
                    request_refresh()
                    return
                if ext in (".sta", ".msg") and job:
                    if event_type in ("created", "modified", "moved", "deleted"):
                        mark_job_dirty(job)

            def on_created(self, event):
                if not event.is_directory:
                    self._handle_path(event.src_path, "created")

            def on_modified(self, event):
                if not event.is_directory:
                    self._handle_path(event.src_path, "modified")

            def on_deleted(self, event):
                if not event.is_directory:
                    self._handle_path(event.src_path, "deleted")

            def on_moved(self, event):
                if not event.is_directory:
                    self._handle_path(event.src_path, "moved")
                    self._handle_path(event.dest_path, "moved")

        observer = None
        if WATCHDOG_AVAILABLE and Observer is not None and os.path.isdir(watch_dir):
            try:
                observer = Observer()
                handler = cast("WatchdogEventHandlerType", AbaqusEventHandler())
                observer.schedule(handler, watch_dir, recursive=False)
                observer.start()
                self.log("Watcher mode: Event-driven (.lck/.sta/.msg)")
            except Exception as e:
                observer = None
                self.log(f"Watchdog unavailable, fallback polling: {type(e).__name__}")
        else:
            self.log("Watchdog unavailable, fallback polling mode active")

        last_tg_poll = 0.0
        last_fallback_scan = 0.0

        try:
            # Main loop
            while not self.stop_event.is_set():
                try:
                    now = time.time()

                    # Poll Telegram commands.
                    if (now - last_tg_poll) >= TELEGRAM_POLL_SECONDS:
                        self.check_telegram(token, chat_id, watch_dir)
                        last_tg_poll = now

                    # Decide whether to refresh.
                    do_refresh = False
                    if refresh_requested.is_set():
                        do_refresh = True

                    if (now - last_fallback_scan) >= FALLBACK_SCAN_SECONDS:
                        do_refresh = True
                        last_fallback_scan = now

                    if do_refresh:
                        # Debounce rapid write bursts.
                        should_wait = False
                        with state_lock:
                            if dirty_jobs:
                                newest_dirty = max(dirty_jobs.values())
                                if (now - newest_dirty) < EVENT_DEBOUNCE_SECONDS:
                                    should_wait = True
                                else:
                                    dirty_jobs.clear()
                        if should_wait:
                            self.stop_event.wait(0.25)
                            continue

                        refresh_requested.clear()

                        # Build active-job set from .lck files.
                        if os.path.isdir(watch_dir):
                            with os.scandir(watch_dir) as it:
                                active = [e.name[:-4] for e in it if e.is_file() and e.name.endswith('.lck')]
                        else:
                            active = []

                        # Update UI table.
                        ui_data = []
                        for job in active:
                            sta = os.path.join(watch_dir, job + ".sta")
                            tail = self._read_tail_text(sta)
                            solver_type = self._get_solver_type(sta)
                            if solver_type == "STANDARD":
                                est_str, _, _ = self._estimate_completion_standard(watch_dir, job, tail)
                            else:
                                est_str, _, _ = self._estimate_completion(tail)

                            display_time = "Calculating..."
                            if est_msg := est_str:
                                try:
                                    display_time = est_msg.split("Left:")[1].split("(")[0].strip()
                                except Exception:
                                    pass

                            try:
                                start_ts = os.path.getctime(os.path.join(watch_dir, job + ".lck"))
                            except Exception:
                                start_ts = 0

                            ui_data.append((job, display_time, start_ts))

                        ui_data.sort(key=lambda x: x[2])
                        final_view = [(x[0], x[1]) for x in ui_data]
                        self.after(0, lambda: self.update_job_table(final_view))

                        # Notify on new jobs.
                        for job in active:
                            if job not in self.job_heartbeats:
                                start_date = self._get_start_date_once(watch_dir, job)
                                self.job_heartbeats[job] = {"last_hb": time.time(), "start_date": start_date}
                                self.log(f"New: {job}")
                                self.send_tg(token, chat_id, f"**Started:** `{job}`\n{start_date}", "🚀")

                        # Notify on finished jobs.
                        for job in list(self.job_heartbeats):
                            if job not in active:
                                status, icon, det = self.get_status(watch_dir, job)
                                plot = self.gen_plot(watch_dir, job)
                                silent = not ("SUCCESS" in status or "ABORTED" in status)
                                self.send_tg(token, chat_id, f"**Job:** `{job}`\n**Result:** {status}\n{det}", icon, plot, silent)
                                self.log(f"End: {job} [{status}]")
                                del self.job_heartbeats[job]

                    # Periodic heartbeats.
                    now = time.time()
                    active_jobs = set(self.job_heartbeats.keys())
                    for job, data in list(self.job_heartbeats.items()):
                        if job in active_jobs and (now - data["last_hb"]) > hb:
                            det = self.get_details(watch_dir, job, data["start_date"])
                            self.send_tg(token, chat_id, f"**Running:** `{job}`\n{det}", "⏳")
                            self.job_heartbeats[job]["last_hb"] = now
                            self.log(f"Heartbeat: {job}")

                    self.stop_event.wait(0.5)

                except Exception as e:
                    self.log(f"Loop Err: {e}")
                    self.stop_event.wait(1.5)
        finally:
            if observer is not None:
                try:
                    observer.stop()
                    observer.join(timeout=2)
                except Exception:
                    pass

    # --- TELEGRAM HELPERS ---
    def send_tg(self, token, chat_id, text, icon, img=None, silent=True):
        """
        Sends a notification to Telegram (text or photo with caption).

        Uses ``sendPhoto`` when *img* is provided, otherwise ``sendMessage``.
        Messages are formatted with Markdown and prefixed with *icon*.
        All exceptions are caught and logged to avoid crashing the watcher.

        Args:
            token: Bot API token.
            chat_id: Target chat/group ID.
            text: Message body (Markdown).
            icon: Emoji prefix for visual classification.
            img: Optional image path (deleted after send).
            silent: Suppress notification sound (default ``True``).
        """
        url = f"https://api.telegram.org/bot{token}"
        try:
            d = {"chat_id": chat_id, "parse_mode": "Markdown", "disable_notification": silent}

            if img and os.path.exists(img):
                d['caption'] = f"{icon} {text}"
                with open(img, 'rb') as f:
                    requests.post(f"{url}/sendPhoto", data=d, files={'photo': f}, timeout=20)
                try:
                    os.remove(img)
                except Exception:
                    pass
            else:
                d['text'] = f"{icon} {text}"
                requests.post(f"{url}/sendMessage", data=d, timeout=10)
        except Exception as e:
            # Log only exception type to avoid cluttering console with stack traces
            self.log(f"Telegram Err: {type(e).__name__}")

    def check_telegram(self, token, chat_id, watch_dir):
        """
        Polls Telegram Bot API for user commands and executes them.

        Supported commands:
        - ``/status_all``, ``/status_running``, ``/status_completed``,
          ``/status_error`` — list jobs filtered by status.
        - ``/status <job>`` — detailed info + convergence plot.
        - ``/kill <job>`` — terminate a running job.

        Only messages from *chat_id* are processed.  Job names are
        validated via ``_is_safe_job_name()``.  Called from the
        background watcher thread; all exceptions are silenced.

        Args:
            token: Telegram Bot API token.
            chat_id: Authorized chat/user ID.
            watch_dir: Directory containing .sta/.lck files.
        """
        try:
            url = f"https://api.telegram.org/bot{token}/getUpdates"
            params = {'offset': self.last_telegram_update_id + 1, 'timeout': 1}
            r = requests.get(url, params=params, timeout=3).json()
            if not r.get('ok'):
                return

            for res in r.get('result', []):
                self.last_telegram_update_id = res['update_id']

                sender = str(res.get('message', {}).get('chat', {}).get('id'))
                if sender != str(chat_id):
                    continue

                text = res.get('message', {}).get('text', '').strip()

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

                elif text.startswith("/status "):
                    parts = text.split(maxsplit=1)
                    if len(parts) > 1:
                        job = parts[1]
                        if not self._is_safe_job_name(job):
                            self.send_tg(token, chat_id, "Invalid job name.", "⚠️")
                        else:
                            self.send_tg(token, chat_id, f"**Status:** `{job}`\n{self.get_details(watch_dir, job)}", "📈", self.gen_plot(watch_dir, job))

                elif text.startswith("/kill "):
                    parts = text.split(maxsplit=1)
                    if len(parts) > 1:
                        job = parts[1]
                        if not self._is_safe_job_name(job):
                            self.send_tg(token, chat_id, "Invalid job name.", "⚠️")
                        else:
                            try:
                                subprocess.run(["abaqus", "terminate", f"job={job}"], cwd=watch_dir, check=False)
                                self.send_tg(token, chat_id, f"Kill sent: `{job}`", "💀")
                            except Exception:
                                self.send_tg(token, chat_id, f"Failed to terminate: `{job}`", "🚨")
        except Exception:
            pass

    def _is_safe_job_name(self, job: str) -> bool:
        """
        Validates a job name against a whitelist pattern.

        Only alphanumeric characters, dots, underscores, and hyphens are
        allowed (regex ``[A-Za-z0-9._-]+``).  This prevents path traversal
        and command injection from user-supplied names.

        Args:
            job: Job name to validate.

        Returns:
            ``True`` if the name is safe, ``False`` otherwise.
        """
        return bool(re.fullmatch(r"[A-Za-z0-9._-]+", job))

    def _read_tail_text(self, path: str, max_bytes: int = MAX_TAIL_BYTES) -> str:
        """
        Reads the last *max_bytes* bytes of a text file.

        Seeks to ``file_size - max_bytes`` and reads forward, avoiding
        the cost of loading entire multi-MB .sta files on every poll.

        Args:
            path: Absolute path to the text file.
            max_bytes: Maximum bytes to read from the end.

        Returns:
            Decoded tail text, or ``""`` on any read error.
        """
        try:
            with open(path, 'rb') as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(size - max_bytes, 0), os.SEEK_SET)
                data = f.read()
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
            if "COMPLETED SUCCESSFULLY" in tail:
                return "SUCCESS", "✅", "Converged"
            if "ERROR" in tail:
                return "ABORTED", "🚨", "Check .msg"
            return "TERMINATED", "⚠️", "Stopped"
        except: 
            pass
        return "UNKNOWN", "❓", "Error"

    def _get_log_job_times(self, d: str, j: str) -> tuple[Optional[datetime], Optional[datetime]]:
        """
        Parse job start/end timestamps from the Abaqus ``.log`` file.

        Markers used:
        - Start (Standard): line immediately after ``Begin Abaqus/Standard Analysis``
        - Start (Explicit): line immediately after ``Begin Abaqus/Explicit Packager``
        - End   (Standard): line immediately before ``End Abaqus/Standard Analysis``
        - End   (Explicit): line immediately before ``End Abaqus/Explicit Analysis``

        For running jobs, the end marker is absent and end time remains ``None``.

        Args:
            d: Watch directory containing the .log file.
            j: Job name without extension.

        Returns:
            tuple[datetime | None, datetime | None]: ``(start_time, end_time)``.
        """
        log_path = os.path.join(d, j + ".log")
        if not os.path.exists(log_path):
            return None, None

        def _parse_log_datetime(text: str) -> Optional[datetime]:
            value = text.strip()
            if not value:
                return None
            try:
                return datetime.strptime(value, "%Y-%m-%d %I:%M:%S %p")
            except Exception:
                return None

        try:
            with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
                lines = [line.rstrip("\n") for line in f]
        except Exception:
            return None, None

        start_dt: Optional[datetime] = None
        end_dt: Optional[datetime] = None

        for idx, line in enumerate(lines):
            if "Begin Abaqus/Explicit Packager" in line or "Begin Abaqus/Standard Analysis" in line:
                for next_idx in range(idx + 1, len(lines)):
                    candidate = _parse_log_datetime(lines[next_idx])
                    if candidate is not None:
                        start_dt = candidate
                        break
                if start_dt is not None:
                    break

        for idx, line in enumerate(lines):
            if "End Abaqus/Explicit Analysis" in line or "End Abaqus/Standard Analysis" in line:
                for prev_idx in range(idx - 1, -1, -1):
                    candidate = _parse_log_datetime(lines[prev_idx])
                    if candidate is not None:
                        end_dt = candidate
                        break

        return start_dt, end_dt

    def _get_start_date_once(self, d, j):
        """Return a formatted start-date string from the .log/.sta header (cached per job)."""
        start_dt, _ = self._get_log_job_times(d, j)
        if start_dt is not None:
            return f"📅 {start_dt.strftime('%Y-%m-%d %I:%M:%S %p')}"

        sta = os.path.join(d, j + ".sta")
        if not os.path.exists(sta):
            return ""
        try:
            with open(sta, 'r', encoding='utf-8', errors='ignore') as f:
                for _ in range(HEADER_SCAN_LINES):
                    line = f.readline()
                    if "DATE" in line and "TIME" in line:
                        p = line.split("DATE")[-1].split("TIME")
                        return f"📅 {p[0].strip()} {p[1].strip()}"
        except Exception:
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
        start_dt, _ = self._get_log_job_times(d, j)
        if start_dt is not None:
            return start_dt

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
        """
        Detect the ABAQUS solver product from the first line of a .sta file.

        ABAQUS writes a product identifier as the very first line, e.g.:
          ``Abaqus/Standard 2023``
          ``Abaqus/Explicit 2023``

        Args:
            sta_path: Absolute path to the .sta file.

        Returns:
            str: ``"STANDARD"`` for Abaqus/Standard,
                 ``"EXPLICIT"`` for Abaqus/Explicit,
                 ``"UNKNOWN"`` if the file is missing or the first line does
                 not match either product name.
        """
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
        Extract the most recent Abaqus/Standard increment-summary row from .sta tail text.

        Abaqus/Standard writes one summary row per completed increment attempt:

            STEP  INC  ATT  SEVERE EQUIL TOTAL  TOTAL TIME  STEP TIME  INC OF TIME

        Each column is whitespace-delimited.  The attempt column (index 2) has
        the format ``\\d+U?`` where the optional trailing ``U`` flag denotes a
        non-default increment size chosen by the automatic-increment controller.

        The method scans the text backwards so the first matching row is the
        most recent increment.

        Args:
            tail_text: Plain-text content from the tail of the .sta file,
                       as returned by ``_read_tail_text``.

        Returns:
            dict | None: Keys ``step``, ``increment``, ``attempt``, ``severe``,
                         ``equil``, ``total_iters``, ``total_time``, ``step_time``,
                         ``inc_time`` (all strings); or ``None`` if no matching
                         row is found.
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

        Reads the ``FRACTION OF STEP COMPLETED`` value from the .msg file tail
        and combines it with the simulation step time extracted from the .sta
        summary row to project total and remaining step time:

            total_step_time  = step_time_completed / fraction_completed
            remaining        = max(total_step_time - step_time_completed, 0)

        When ``actual_elapsed_sec`` is provided (wall-clock seconds measured
        from ``_get_start_datetime``), the displayed total is
        ``actual_elapsed_sec + remaining`` so it reflects real-world duration
        rather than simulation step time.

        The .msg file is preferred for the fraction value because Abaqus writes
        ``FRACTION OF STEP COMPLETED`` there at every increment, while the .sta
        only carries cumulative step time.

        Args:
            d: Watch directory containing the .sta and .msg files.
            j: Job name without extension.
            sta_tail_text: Pre-read .sta tail text (avoids a redundant file read if
                           the caller already holds it); may be empty string.
            actual_elapsed_sec: Wall-clock seconds elapsed since job start, or
                                ``None`` to fall back to simulation step-time.

        Returns:
            tuple[str | None, float, float]: ``(message, remaining_sec, total_sec)``
            or ``(None, 0, 0)`` if the .msg file is absent or cannot be parsed.
        """
        msg_path = os.path.join(d, j + ".msg")
        if not os.path.exists(msg_path):
            return None, 0, 0

        try:
            current_step_time = 0.0

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

            # Prefer step-time from .msg if available
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

            total_display = (actual_elapsed_sec + remaining) if actual_elapsed_sec is not None else total_step_time
            msg = f"⏱️ Left: {_fmt_duration(remaining)} (Tot: {_fmt_duration(total_display)})"
            return msg, remaining, total_display
        except Exception:
            return None, 0, 0
    
    def _estimate_completion(self, tail_text, actual_elapsed_sec: Optional[float] = None):
        """
        Estimates remaining job time via linear extrapolation of ODB frames.

        Parses the latest ``ODB Field Frame Number X of Y`` and cumulative
        CPU time from *tail_text*, then extrapolates the remaining duration.
        Accuracy depends on time-step uniformity; adaptive stepping or
        convergence difficulties reduce reliability.

        Args:
            tail_text: Tail content of the .sta file.
            actual_elapsed_sec: Wall-clock seconds since job start.  When
                given, the displayed total uses real elapsed time rather
                than solver CPU time.

        Returns:
            ``(message, remaining_sec, total_sec)`` on success,
            or ``(None, 0, 0)`` if estimation is not possible.
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

            # 2. Find the latest cumulative CPU time (HH:MM:SS) from the .sta data rows.
            # Abaqus writes one data row per increment; column layout (0-based index):
            #   0: increment counter (int)
            #   1: step time (float)
            #   2: step time increment (float)
            #   3: cumulative CPU time (HH:MM:SS)
            # Rows are scanned backwards so the first match is the most recent increment.
            lines = tail_text.splitlines()
            latest_cpu_time_str = None
            for line in reversed(lines):
                parts = line.split()
                # Check if this is a data line (starts with digit, has time format in index 2 or 3)
                if len(parts) > 3 and parts[0].isdigit() and ":" in parts[3]: 
                    latest_cpu_time_str = parts[3]  # column index 3 = cumulative CPU time (HH:MM:SS)
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

            wall_elapsed = actual_elapsed_sec if actual_elapsed_sec is not None else elapsed_seconds
            total_est_seconds = wall_elapsed + remaining_seconds

            msg = f"⏱️ Left: {_fmt_duration(remaining_seconds)} (Tot: {_fmt_duration(total_est_seconds)})"
            return msg, remaining_seconds, total_est_seconds

        except Exception as e:
            return None, 0, 0
    
    def get_details(self, d, j, cached_start=""):
        """
        Build a multi-line progress summary for a running or finished job.

        Reads the last ``MAX_TAIL_BYTES`` of the .sta file once, then branches
        on solver type:

        **Abaqus/Standard** (``solver_type == "STANDARD"``)
            Parses the latest summary row via ``_parse_standard_sta_progress``
            and estimates completion via ``_estimate_completion_standard``,
            which reads the ``FRACTION OF STEP COMPLETED`` field from the .msg
            file.

        **Abaqus/Explicit and unknown solvers**
            Makes a single backwards pass through the .sta tail to extract the
            latest increment row (step time, dt, energies), the ODB frame
            counter, and the current STEP number.  Completion is estimated by
            ``_estimate_completion`` using ODB frame progress.

        In both paths, wall-clock elapsed time since job start (from
        ``_get_start_datetime``) is passed to the estimation functions so the
        displayed total reflects real-world duration.

        Args:
            d: Watch directory containing the .sta file.
            j: Job name without extension.
            cached_start: Pre-formatted start-date string from
                          ``_get_start_date_once`` (avoids re-reading header).

        Returns:
            str: Formatted status string ready for Telegram or the UI, or
                 ``"Waiting..."`` / ``"Error"`` on failure.
        """
        sta = os.path.join(d, j + ".sta")
        if not os.path.exists(sta):
            return "Waiting..."
        try:
            start = f"{cached_start}\n" if cached_start else ""
            tail_text = self._read_tail_text(sta)
            solver_type = self._get_solver_type(sta)

            start_dt = self._get_start_datetime(d, j)
            actual_elapsed_sec: Optional[float] = (
                (datetime.now() - start_dt).total_seconds() if start_dt else None
            )

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
            
            est_msg, _, _ = self._estimate_completion(tail_text, actual_elapsed_sec)
            if est_msg:
                fra += f"\n{est_msg}"

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
        if not files:
            return "No Abaqus jobs found."

        files.sort(key=lambda x: os.path.getmtime(os.path.join(watch_dir, x)), reverse=True)

        summary = []
        count = 0
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
                    category = "ERROR"

                if filter_mode != "ALL" and filter_mode != category:
                    continue

                time_est_str = ""
                if is_running:
                    solver_type = self._get_solver_type(sta)
                    if solver_type == "STANDARD":
                        est_msg, _, _ = self._estimate_completion_standard(watch_dir, job, tail)
                    else:
                        est_msg, _, _ = self._estimate_completion(tail)
                    if est_msg:
                        time_est_str = f"\n{est_msg.split('(')[0].strip()}"

                # Start/end timestamps from .log markers.
                start_dt, end_dt = self._get_log_job_times(watch_dir, job)
                if start_dt is not None:
                    start_t = start_dt.strftime("%Y-%m-%d %I:%M:%S %p")
                if end_dt is not None:
                    end_t = end_dt.strftime("%Y-%m-%d %I:%M:%S %p")
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
        Generates a Step Time vs. Time Step Size convergence plot.

        Parses the .sta file for increment data, plots dt on a log scale,
        and saves the figure to ``{d}/job_watcher/plot_{j}.png``.
        The matplotlib figure is always closed in a ``finally`` block to
        prevent memory leaks.

        Args:
            d: Watch directory containing the .sta file.
            j: Job name without extension.

        Returns:
            Path to the generated PNG, or ``None`` on failure.
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
    # Single-instance enforcement: a TCP socket on localhost acts as an
    # inter-process lock.  If port 54321 is already listening, a first
    # instance is running and will be restored from tray.
    SINGLE_INSTANCE_PORT = 54321

    try:
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.settimeout(0.5)
        client.connect(("127.0.0.1", SINGLE_INSTANCE_PORT))
        client.close()
        sys.exit(0)
    except (socket.error, ConnectionRefusedError):
        pass

    app = AbaqusWatcherApp()
    app.start_single_instance_server()
    app.mainloop()
