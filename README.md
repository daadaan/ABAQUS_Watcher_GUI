# ABAQUS Watcher GUI

A modern, cross-platform Desktop Application to monitor SIMULIA ABAQUS jobs remotely via Telegram. This tool watches your ABAQUS working directory for lock files (`.lck`) and status files (`.sta`), providing real-time updates, convergence plots, and remote termination capabilities.

**Author:** Souvik Biswas (@daadaan)  
**License:** MIT

---

## Features

- **Smart Notifications:** To prevent spamming your phone, **all routine updates (Heartbeats, Status checks) are sent silently.** You will only receive a sound/vibration notification for critical events: **Job Completion** or **Job Abort**.
- **Real-time Monitoring:** Automatically detects when jobs start, finish, or error out by monitoring `.lck` (lock) and `.sta` (status) files.
- **Time Estimation:** Uses linear extrapolation based on ODB frame output to estimate time remaining for running jobs. Estimates are displayed in both the app interface and Telegram messages.
- **Convergence Plotting:** Generates and sends a graph (Step Time vs. Increment Size) via Telegram to visualize solver stability and time step adaptation.
- **Remote Control:** Check status or terminate jobs remotely using Telegram commands from anywhere.
- **Secure Storage:** Credentials (Bot Token, Chat ID) are encrypted and stored in the **Windows Credential Locker** (or platform keyring), never in plain-text files.
- **System Tray Mode:** The app minimizes to the system tray, running unobtrusively in the background without cluttering your taskbar.
- **Single Instance Enforcement:** Automatically prevents multiple copies from running simultaneously. Launching a second instance will restore the first one from the tray.
- **Deployment-Aware Updates:** Script mode supports automatic self-updating; EXE mode prompts you to download new releases.

---

## Installation & Prerequisites

This application is written in Python and runs on **Windows, Linux, and macOS**.

### Prerequisites
* **Python 3.10 or higher** (tested and confirmed working with Python 3.10, 3.11, 3.12, and 3.14)
  - **Why 3.10+?** The application uses modern Python features including `match`/`case` statements and improved typing syntax.
  - Newer Python versions should work as long as all dependencies have compatible wheels available.
* **ABAQUS** installed on the host machine (simulation files must be accessible)
  - The app monitors output files (`.sta`, `.lck`) created by ABAQUS during job execution.
  - No direct ABAQUS API integration—works by parsing file system changes.

### Installation Steps
1.  **Clone the repository:**
    ```bash
    git clone https://github.com/daadaan/ABAQUS_Watcher_GUI.git
    cd ABAQUS_Watcher_GUI
    ```
2.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```
3.  **Run the application:**
    ```bash
    python abaqus_watcher_gui.py
    ```

### Bonus for Windows Users
For Windows users who prefer not to manage Python environments, a pre-compiled `.exe` is available.
1.  Go to the [**Releases Page**](https://github.com/daadaan/ABAQUS_Watcher_GUI/releases).
2.  Download `ABAQUSWatcherGUI_vX.X.X.exe`.
3.  Double-click to run. No installation required.

#### ⚠️ SmartScreen Warning

When you first run the `.exe`, **Microsoft SmartScreen** may display a warning like:

> **Windows protected your PC**  
> Microsoft Defender SmartScreen prevented an unrecognized app from starting.  
> Publisher: Unknown

**Why does this happen?**  
The executable is not code-signed with a paid certificate (which costs hundreds of dollars per year). This is common for open-source and personal projects.

**Is it safe?**  
✅ **Yes.** The `.exe` is built automatically by [GitHub Actions](https://github.com/daadaan/ABAQUS_Watcher_GUI/actions) directly from the Python source code in this repository. You can verify this yourself:
- View the [build workflow](.github/workflows/) to see exactly how the executable is generated.
- Inspect the [source code](abaqus_watcher_gui.py) – it's fully open and transparent.

**To run anyway:**
1. Click **"More info"** on the SmartScreen popup.
2. Click **"Run anyway"**.

---

## Technical Details

### Architecture
- **GUI Framework:** Built with [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter) for a modern dark/light mode interface
- **Threading Model:** 
  - Main thread handles all UI operations (thread-safe)
  - Background daemon thread monitors directory every 3 seconds
  - Separate threads for network operations (Telegram polling, update checks)
- **File Monitoring:** Polls directory for `.lck` files (active jobs) and `.sta` files (status/progress data)
- **Security:** OS-level credential storage via `keyring` library (Windows Credential Manager, macOS Keychain, Linux Secret Service)

### Performance Optimizations
- **Tail Reading:** Only reads the last 250KB of `.sta` files instead of loading entire files (10-100x speedup for large files)
- **Header Caching:** Start times and job metadata are cached after first read to avoid repeated parsing
- **Job Limiting:** Summary commands limited to 15 most recent jobs to prevent Telegram message size errors
- **Efficient Polling:** 3-second intervals balance responsiveness with CPU usage
- **Memory Management:** Console logs limited to 500 lines; matplotlib figures explicitly closed to prevent memory leaks

### Data Storage
- **Configuration File:** `%LOCALAPPDATA%\ABAQUSWatcherGUI\abaqus_watcher_config.json` (Windows) or `~/.config/ABAQUSWatcherGUI/` (Linux/macOS)
- **Stored Settings:** Directory path, heartbeat interval, theme preference, tray settings
- **Secure Credentials:** Bot token and chat ID stored in OS keyring (never in JSON files)

---

## Configuration Guide

The app features a GUI tab to handle setup easily. You do not need to edit config files manually.

### 1. Setting up the Telegram Bot
You need two things: a **Bot Token** and your **Chat ID**.

**Step A: Get Bot Token**
1.  Open Telegram and search for **[@BotFather](https://t.me/BotFather)**.
2.  Send the command `/newbot`.
3.  Follow the instructions to name your bot.
4.  BotFather will give you a long string (e.g., `123456:ABC-DEF1234...`). **Copy this Token.**

**Step B: Get Chat ID**
1.  Search for **[@userinfobot](https://t.me/userinfobot)** on Telegram.
2.  Click "Start" or send any message.
3.  It will reply with your "Id" (a number like `123456789`). **Copy this ID.**

### 2. Configure the App
1.  Open **ABAQUS Watcher GUI**.
2.  Go to the **Config** tab.
3.  Paste your **Bot Token** and **Chat ID**.
4.  **ABAQUS Temp Directory:** Browse to the folder where you run your jobs (this is where `.lck` and `.sta` files appear).
5.  **Heartbeat:** Set how often (in seconds) the bot should send a silent "Still Running" message for long jobs (Default: 3600s).
6.  Click **Save Settings**.

---

## Update Workflow

The application includes a built-in update checker in the **Config** tab.

* **If running as Script:** The app uses a "Deployment Aware" update system. If a new version is found, it will offer to **automatically download** the new code from GitHub, overwrite the current script, and restart itself.
* **If running as EXE:** The app will alert you and open the GitHub Releases page so you can download the latest executable.

---

## How Time Estimation Works

The app estimates job completion time using **linear extrapolation** based on ABAQUS's ODB frame output:

1. **Tracks Progress:** Monitors the current frame number vs. total frames from the `.sta` file (e.g., "ODB Field Frame Number 25 of 100").
2. **Measures Elapsed Time:** Extracts CPU time from the latest increment data (HH:MM:SS format).
3. **Calculates Rate:** Determines average time per frame: `seconds_per_frame = elapsed_time / current_frame`.
4. **Projects Completion:** Multiplies remaining frames by the average rate: `remaining = (total_frames - current_frame) * seconds_per_frame`.

**Example Output:**
- In App: `"1h 20m"` (time remaining only)
- In Telegram: `"⏱️ Left: 1h 20m (Tot: 3h 45m)"` (remaining time + total estimated duration)

**When It Works Best (±10-25% accuracy):**
- Uniform increment sizes (fixed time stepping)
- Linear analyses with stable convergence
- Constant material properties

**When It May Be Less Accurate (±50%+ error):**
- Highly adaptive time stepping (early vs. late increments differ significantly)
- Complex contact conditions with friction
- Jobs with severe convergence issues requiring cutbacks
- First 10% of job execution (insufficient data)

**Note:** 
- Estimates appear as `"Calculating..."` until the first ODB frame is written.
- Some ABAQUS jobs don't generate ODB frames (no estimation available).
- Updates every 3 seconds as new increment data becomes available.

---

## Telegram Commands

Control and monitor your simulations remotely using these commands:

### Status Filters

| Command | Description |
| --- | --- |
| `/status_all` | List **all** recent jobs (running, completed, and errors). Shows time estimates for active jobs. |
| `/status_running` | Show only currently **running** jobs with estimated time remaining. |
| `/status_completed` | Show only successfully **completed** jobs. |
| `/status_error` | Show only **failed** or aborted jobs. |

### Job Control

| Command | Description |
| :--- | :--- |
| `/status <Job-Name>` | Get detailed stats (Time, Increments, **Estimated Time Remaining**) and a **convergence plot** for a specific job.<br><br>*Example:* `/status Job-1` |
| `/kill <Job-Name>` | **Terminate** a specific job remotely.<br><br>*Example:* `/kill Job-1` |

> **Note:** Job names are case-sensitive and must match the filename exactly (excluding `.lck` or `.sta`).

### Setup Telegram Command Menu

To make these commands appear in a clickable menu inside your Telegram chat, follow these steps:

1. Open Telegram and search for **@BotFather**.
2. Send the command `/setcommands`.
3. Select your ABAQUS Watcher bot from the list.
4. Copy and paste the following command list when prompted:  


```text
status_all - List all recent jobs
status_running - List currently running jobs
status_completed - List successfully finished jobs
status_error - List failed or aborted jobs
```

> **Note:** Commands like `/status <job>` and `/kill <job>` require specific job names, so they are not included in the preset menu. You must type them manually.

---

## Troubleshooting

### App Won't Start
- **Ensure Python 3.10+** is installed: `python --version`
- **Install all dependencies:** `pip install -r requirements.txt`
- **Check for missing libraries:** The error message will indicate which module is missing

### "Keyring Error" on Startup
- **Windows:** Windows Credential Manager should work automatically
- **Linux:** Install `gnome-keyring` or `kwallet` depending on your desktop environment
- **Workaround:** Run app as administrator or use script mode instead of system keyring

### Watcher Not Detecting Jobs
- **Verify Directory Path:** Ensure the path points to where ABAQUS writes `.lck` and `.sta` files (typically the working directory where you run `abaqus job=...`)
- **Check File Permissions:** The app needs read access to the directory
- **ABAQUS Must Be Running:** The app monitors files created by ABAQUS, not ABAQUS itself

### Telegram Bot Not Responding
- **Test Connection:** Click "Test Connection" button in the Monitor tab
- **Verify Bot Token:** Get a fresh token from @BotFather if needed
- **Check Chat ID:** Ensure you copied the correct numeric ID from @userinfobot
- **Network Issues:** The app requires internet access to reach Telegram's servers
- **Bot Must Be Started:** Send `/start` to your bot in Telegram before using it

### Time Estimates Missing or "Calculating..."
- **Wait for First Frame:** Estimates require at least one ODB frame to be written
- **Check ODB Output:** Some jobs don't write ODB frames (no estimation possible)
- **Verify `.sta` File:** The app reads frame data from the status file

### Multiple Instances Running
- **Should Not Happen:** The app prevents this automatically via port 54321
- **If Port Blocked:** Another application may be using port 54321 (rare)
- **Manual Fix:** Close all instances and restart

### High Memory Usage
- **Large `.sta` Files:** The app only reads the last 250KB, not the entire file
- **Memory Leak:** Ensure you're using the latest version (memory management improved in v1.3+)
- **Restart App:** Close and reopen if memory grows over time

### Update Check Fails
- **Network Required:** Update check connects to GitHub API
- **GitHub Rate Limiting:** Try again in a few minutes
- **Firewall/Proxy:** May block access to api.github.com

---

## License

This project is licensed under the **MIT License**.  
Copyright (c) 2026, Souvik Biswas (daadaan).