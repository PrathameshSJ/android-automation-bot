# Android Attendance Bot

An automated attendance-marking workflow bot for Android (specifically targeting the `edu.somaiya.somaiyaapp` package). It utilizes native ADB command-line tools to dump the UI XML tree, check for specific text and time slot bounds, tap interactive elements, and gracefully close the app using task stack operations.

---

## Prerequisites

Before running the bot on either a mobile phone or an emulator, ensure you have:
1. **Python 3.x** installed and added to your system environment variables.
2. **ADB (Android Debug Bridge)** installed and added to your system `PATH`.
   * Test this by opening a terminal and running `adb devices`.

---

## 1. Running on Windows Emulator (Android Studio AVD)

### Setup
1. Open Android Studio and go to **Device Manager**.
2. Create an Android Virtual Device (AVD) named `Medium_Phone` (or update the AVD name in the `bot.py` script if you use a different name).

### Running the Bot
You can run the bot in two ways depending on whether the emulator is already running:

* **If the Emulator is Already Running**:
  Simply run:
  ```bash
  py bot.py
  ```
  The script will automatically detect the running emulator under `adb devices` and proceed immediately.

* **If the Emulator is Stopped**:
  Run:
  ```bash
  py bot.py
  ```
  The script will automatically start the `Medium_Phone` emulator in a new command window, wait up to 40 seconds for it to boot and connect, and then proceed with the workflow.

---

## 2. Running on a Physical Mobile Phone via PC

You can run the automation script directly on your physical Android device instead of an emulator.

### Setup
1. **Enable Developer Options** on your mobile phone:
   * Go to **Settings** > **About Phone**.
   * Tap **Build Number** 7 times until you see a message saying "You are now a developer!".
2. **Enable USB Debugging**:
   * Go to **Settings** > **System** > **Developer Options**.
   * Turn on **USB Debugging**.
3. **Connect to Computer**:
   * Connect your mobile phone to your PC/laptop using a USB cable.
   * A prompt will appear on your phone asking to "Allow USB debugging?". Check the box for "Always allow from this computer" and tap **Allow**.

### Running the Bot
1. Open a command prompt or terminal on your PC.
2. Get your mobile phone's unique device serial number:
   ```bash
   adb devices
   ```
   You will see an output resembling:
   ```text
   List of devices attached
   9889db4f454e4f5a34    device
   ```
3. Run the bot by passing your device serial number as a launch argument:
   ```bash
   py bot.py 9889db4f454e4f5a34
   ```
   *(Replace `9889db4f454e4f5a34` with the serial number returned by your device).*

---

## 3. Running via Termux (Directly on your Android Phone)

You can run the script entirely on your Android device using **Termux** (no PC/laptop required after the initial pairing setup).

### Setup in Android
1. Go to **Settings** > **Developer Options**.
2. Enable **Wireless Debugging**.
3. Tap on **Wireless Debugging** (the text label, not the toggle) to open its settings.
4. Tap **Pair device with pairing code**. You will see:
   * **Wi-Fi pairing code** (e.g., `123456`)
   * **IP address & Port** (e.g., `192.168.1.100:37283` — *Note this port; it is the pairing port*)

### Setup in Termux
1. Download and open **Termux** on your phone.
2. Update packages and install dependencies:
   ```bash
   pkg update && pkg upgrade
   pkg install android-tools python
   ```
3. **Pair Termux with Wireless Debugging**:
   Run the pairing command using the IP address and the *pairing port* shown on your Android settings screen:
   ```bash
   adb pair 192.168.1.100:37283
   ```
   * Enter the **Wi-Fi pairing code** when prompted.
4. **Connect Termux to the Device**:
   Go back to the main Wireless Debugging screen in Android settings and find the main **IP address & Port** listed under the toggle (this is different from the pairing port, e.g., `192.168.1.100:45821`).
   Run:
   ```bash
   adb connect 192.168.1.100:45821
   ```
   * You should see `connected to 192.168.1.100:45821`.
5. Verify the connection:
   ```bash
   adb devices
   ```
   Your local device should be listed as `device`.

### Running the Bot
Once paired and connected:

1. **Request Storage Permission in Termux**:
   To allow Termux to read and copy files from your phone's storage (like your Downloads folder), run:
   ```bash
   termux-setup-storage
   ```
   *Accept the storage permission prompt popup on your phone screen.*

2. **Copy `bot.py` from Downloads to Termux Home**:
   Copy the downloaded script to your Termux home directory:
   ```bash
   cp /sdcard/Download/bot.py ~/
   ```
   *(Or using the storage symlink: `cp ~/storage/downloads/bot.py ~/`)*

3. **Run the bot script**:
   ```bash
   python bot.py
   ```
   The script will automatically detect the active Wireless Debugging connection and run the workflow directly on your screen.

---

## How It Works

* **Zero Third-Party Library Overhead**: The script dumps the UI layout directly using Android's native `uiautomator` utility:
  ```bash
  adb shell uiautomator dump /data/local/tmp/window_dump.xml
  ```
* **Soft Exit (No UI Fiddling)**: Rather than trying to open the recent apps tray and swipe the app closed using screen coordinates, the bot queries the current active `taskId` of the application package and instructs Activity Manager to cleanly remove it from the stack:
  ```bash
  adb shell am stack remove <TASK_ID>
  ```
