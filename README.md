# Abaqus Watcher Bot

A Python-based monitoring tool for SIMULIA Abaqus jobs. This script watches your Abaqus working directory, parses status files (`.sta`, `.msg`), and sends real-time updates, convergence plots, and error alerts to your Telegram.

It supports **remote control**, allowing you to check status or terminate jobs directly from your phone.

## Features

* **Real-time Notifications:** Alerts for Job Start, Completion (Success), and Aborts (Error).
* **Convergence Plotting:** Auto-generates and sends a **convergence graph** (Step Time vs. Increment Size) whenever you check a specific job status.
* **Heartbeat Monitor:** Sends hourly "Still Running" updates to ensure long jobs haven't frozen.
* **Smart Error Parsing:** Reads the `.msg` file to send you the specific error message (e.g., "Too many cutbacks").
* **Remote Commands:**
    * `/status JobName` - Generates a **Convergence Graph** + detailed stats (Time, KE, Frames).
    * `/status all` - Gets a text summary of all running jobs.
    * `/kill JobName` - Remotely terminates a runaway job.
* **Multi-Solver Support:** Automatically detects Abaqus/Standard vs. Abaqus/Explicit.

## Prerequisites

Before running the script, ensure you have the following installed on your workstation:

1.  **Python 3.x:** (Tested on Python 3.10+)
2.  **Python Libraries:** You must install these two packages for the script to work:
    ```bash
    pip install requests matplotlib
    ```
    * `requests`: Used to communicate with the Telegram API.
    * `matplotlib`: Used to generate the convergence graphs.
3.  **Abaqus:** Must be installed and running on the host machine (the script looks for `.lck` and `.sta` files).
4.  **Telegram App:** Installed on your phone to receive notifications.

## Installation

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/daadaan/ABAQUS_watcher_project.git
    cd ABAQUS_watcher_project
    ```

2.  **Install dependencies:**
    ```bash
    pip install requests matplotlib
    ```

## Configuration (Important)

This project uses a **secrets file** to keep your Telegram credentials safe. This file is ignored by Git, so you must create it manually.

1.  Create a file named `secrets.py` in the main folder.
2.  Paste the following code into it and fill in your details:

    ```python
    # secrets.py
    
    # 1. Open Telegram > Search @BotFather > Create New Bot to get Token
    BOT_TOKEN = "YOUR_LONG_TOKEN_HERE"
    
    # 2. Search @userinfobot to get your numerical Chat ID
    CHAT_ID = "123456789"
    
    # 3. The folder where Abaqus writes .lck and .sta files
    WATCH_DIR = r"C:\ABAQUS Temp"
    
    # 4. How often (in seconds) to send "Still Running" updates
    HEARTBEAT_INTERVAL = 3600
    ```

## Usage

1.  Open your terminal or command prompt.
2.  Run the watcher script:
    ```bash
    python watcher.py
    ```
3.  **That's it!** The script will print `--- Abaqus Multi-Job Watchman Active ---`.
4.  Submit an Abaqus job normally. You will receive a Telegram notification within 3 seconds.

## Telegram Commands

| Command | Usage | Description |
| :--- | :--- | :--- |
| **Status (All)** | `/status all` | Text summary of **all** currently running jobs. Useful for a quick overview. |
| **Status (Job)** | `/status Job-1` | Generates and sends a **Convergence Plot** along with detailed info (Step, Time, Kinetic Energy) for `Job-1`. |
| **Kill** | `/kill Job-1` | Terminates `Job-1` immediately. |

## Notes for Multi-Workstation Use

If you plan to run this on multiple computers (e.g., Laptop and Lab Workstation):
* **Do not use the same Bot Token.**
* Create a separate Bot for each computer (e.g., `@MyLabBot` and `@MyLaptopBot`).
* This prevents the "Update Lottery" where one computer "steals" the command meant for the other.

## License
Private / Personal Use.