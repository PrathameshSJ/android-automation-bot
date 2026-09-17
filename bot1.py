#!/usr/bin/env python3
"""
bot1.py

Variant of bot.py with a modified login sequence:
  - First login click uses find_and_click_by_class_and_index() targeting
    class="android.widget.ImageView" with index="7" instead of a fixed
    coordinate tap.
  - After the ImageView tap, checks for "Low Attendance Alert" text within 6 s.
    If found, clicks it and proceeds; if not found, gives up on the login step
    and loops back to the attendance check.

All other behaviour is identical to bot.py.

Usage:
    python3 bot1.py [adb_serial]
    e.g., python3 bot1.py emulator-5554
"""

import sys
import os
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import time
import re
from datetime import datetime, timedelta

def find_executable(name: str) -> str:
    """Finds an executable in PATH or standard Android SDK directories."""
    path = shutil.which(name)
    if path:
        return path
    
    # Common SDK root paths
    sdk_roots = [
        os.environ.get("ANDROID_HOME"),
        os.environ.get("ANDROID_SDK_ROOT"),
        os.path.expanduser("~/Android/Sdk"),
        os.path.expanduser("~/Library/Android/sdk"),
        os.path.expandvars(r"%LOCALAPPDATA%\Android\Sdk"),
        os.path.expandvars(r"%ANDROID_HOME%"),
        os.path.expandvars(r"%ANDROID_SDK_ROOT%"),
    ]
    
    exts = [".exe", ""] if sys.platform.startswith("win") else [""]
    subdirs = ["platform-tools", "emulator", "tools", "cmdline-tools/latest/bin"]
    
    for root in sdk_roots:
        if root and os.path.isdir(root):
            for subdir in subdirs:
                for ext in exts:
                    candidate = os.path.join(root, subdir, name + ext)
                    if os.path.isfile(candidate) and (not hasattr(os, "X_OK") or os.access(candidate, os.X_OK)):
                        return candidate
    return name

ADB_BIN = find_executable("adb")
EMULATOR_BIN = find_executable("emulator")

# Default ADB serial fallback
ADB_SERIAL = "emulator-5554"

# Default AVD name
AVD_NAME = "Medium_Phone"

def adb(*args: str) -> subprocess.CompletedProcess:
    """Helper to run adb commands."""
    return subprocess.run(
        [ADB_BIN, "-s", ADB_SERIAL, *args],
        capture_output=True,
        text=True,
        check=True,
    )

# Script directory for storing local dumps
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

def dump_ui(retries: int = 2, retry_delay: float = 0.5) -> str:
    """Asks Android to dump the UI tree to XML and pulls it locally to the script folder."""
    device_path = "/data/local/tmp/window_dump.xml"
    local_path = os.path.join(SCRIPT_DIR, "window_dump.xml")
    
    for attempt in range(retries):
        try:
            result = adb("shell", "uiautomator", "dump", device_path)
            if "ERROR" in (result.stdout or "") or "ERROR" in (result.stderr or ""):
                adb("shell", "uiautomator", "dump", "--compressed", device_path)
                
            adb("pull", device_path, local_path)
            if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
                return local_path
        except subprocess.CalledProcessError:
            pass
        except Exception:
            pass
            
        time.sleep(retry_delay)
        
    return None

def find_text_bounds(xml_path: str, target_text: str) -> tuple:
    """Parses the XML and returns the (x1, y1, x2, y2) bounds of the target text."""
    if not xml_path or not os.path.exists(xml_path):
        return None

    try:
        tree = ET.parse(xml_path)
    except (ET.ParseError, Exception):
        return None

    target_lower = target_text.lower()
    
    for node in tree.iter():
        text = node.get('text', '')
        content_desc = node.get('content-desc', '')
        
        if target_lower in text.lower() or target_lower in content_desc.lower():
            bounds = node.get('bounds')
            
            if bounds and bounds != "[0,0][0,0]":
                coords = bounds.replace('][', ',').strip('[]').split(',')
                try:
                    return tuple(map(int, coords))
                except ValueError:
                    continue
                    
    return None

def find_and_click_by_class_and_index(xml_path: str, class_name: str, index: str) -> bool:
    """
    Searches the XML dump for an element matching BOTH class_name AND index attribute,
    then taps the centre of its bounds.

    Args:
        xml_path:   Path to the local window_dump.xml file.
        class_name: The exact 'class' attribute value to match
                    (e.g. "android.widget.ImageView").
        index:      The exact string value of the 'index' attribute to match
                    (e.g. "7").

    Returns:
        True if the element was found and tapped, False otherwise.
    """
    if not xml_path or not os.path.exists(xml_path):
        print(f"  -> XML dump not found: {xml_path}")
        return False

    try:
        tree = ET.parse(xml_path)
    except (ET.ParseError, Exception) as e:
        print(f"  -> Failed to parse XML: {e}")
        return False

    for node in tree.iter():
        node_class = node.get('class', '')
        node_index = node.get('index', '')

        if node_class == class_name and node_index == str(index):
            bounds = node.get('bounds')
            if bounds and bounds != "[0,0][0,0]":
                coords = bounds.replace('][', ',').strip('[]').split(',')
                try:
                    x1, y1, x2, y2 = map(int, coords)
                    center_x = (x1 + x2) // 2
                    center_y = (y1 + y2) // 2
                    print(f"  -> Found class='{class_name}' index='{index}' at bounds {bounds}. "
                          f"Tapping centre ({center_x}, {center_y}).")
                    tap(center_x, center_y)
                    return True
                except ValueError:
                    continue

    print(f"  -> Element class='{class_name}' index='{index}' not found in XML.")
    return False

def tap(x: int, y: int):
    """Executes the adb tap command."""
    adb("shell", "input", "tap", str(x), str(y))

# Globals to keep track of checked slots and pending slots
TRIED_SLOTS = set()
PENDING_SLOTS = []

def find_untried_matching_slots(xml_path: str, time1, time2, time_pattern) -> list:
    """
    Parses the XML dump and returns a list of matching slots.
    Each slot in the list is a dict:
    {
        "slot_id": "11:00 - 12:00",
        "bounds": (x1, y1, x2, y2),
        "center": (center_x, center_y)
    }
    """
    if not xml_path or not os.path.exists(xml_path):
        return []

    try:
        tree = ET.parse(xml_path)
    except (ET.ParseError, Exception):
        return []

    slots = []
    seen_ids_in_dump = set()

    for node in tree.iter():
        text = node.get('text', '')
        content_desc = node.get('content-desc', '')
        
        match = time_pattern.search(text) or time_pattern.search(content_desc)
        if match:
            start_str, end_str = match.groups()
            slot_id = f"{start_str} - {end_str}"
            
            if slot_id in seen_ids_in_dump:
                continue
                
            try:
                start_h, start_m = map(int, start_str.split(':'))
                end_h, end_m = map(int, end_str.split(':'))
                
                from datetime import time as dt_time
                slot_start = dt_time(start_h, start_m)
                slot_end = dt_time(end_h, end_m)
            except ValueError:
                continue
                
            is_in_range = False
            for target_time in (time1, time2):
                if slot_start <= slot_end:
                    if slot_start <= target_time < slot_end:
                        is_in_range = True
                        break
                else:
                    if target_time >= slot_start or target_time < slot_end:
                        is_in_range = True
                        break
                        
            if is_in_range:
                bounds = node.get('bounds')
                if bounds and bounds != "[0,0][0,0]":
                    coords = bounds.replace('][', ',').strip('[]').split(',')
                    try:
                        x1, y1, x2, y2 = map(int, coords)
                        center_x, center_y = (x1 + x2) // 2, (y1 + y2) // 2
                        seen_ids_in_dump.add(slot_id)
                        slots.append({
                            "slot_id": slot_id,
                            "bounds": (x1, y1, x2, y2),
                            "center": (center_x, center_y)
                        })
                    except ValueError:
                        pass
    return slots

def scroll_up_twice():
    """Scrolls up 2 times with a 0.5 sec delay between/after swipes."""
    for i in range(2):
        print(f"Scrolling up (swipe {i+1}/2)...")
        try:
            adb("shell", "input", "swipe", "500", "1200", "500", "800", "300")
        except Exception as e:
            print(f"Swipe failed: {e}")
        time.sleep(0.5)

def check_text_present_with_timeout(target_text: str, timeout: int) -> bool:
    """Polls the UI until text is found, but does not click it."""
    print(f"Checking if '{target_text}' is present (timeout: {timeout}s)...")
    start_time = time.time()
    while True:
        xml_path = dump_ui()
        bounds = find_text_bounds(xml_path, target_text)
        if bounds:
            print(f"  -> '{target_text}' is present!")
            return True
        if (time.time() - start_time) >= timeout:
            break
        time.sleep(1.0)
    print(f"  -> '{target_text}' not found.")
    return False

def check_time_n_click(timeout: int = 11) -> bool:
    """
    Finds a time range matching current time or current time - 10 minutes.
    If multiple valid slots are found, tries the first untried one,
    and saves any other untried matching slots in PENDING_SLOTS.
    """
    global TRIED_SLOTS, PENDING_SLOTS
    
    PENDING_SLOTS = []
    
    now = datetime.now()
    time1 = now.time()
    time2 = (now - timedelta(minutes=10)).time()
    
    print(f"Looking for time slot... (Current time: {now.strftime('%H:%M')}, Adjusted target: {(now - timedelta(minutes=10)).strftime('%H:%M')})")
    print(f"Already tried slots: {list(TRIED_SLOTS)}")
    
    time_pattern = re.compile(r"(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})")
    
    print("Checking for time slot on current screen...")
    xml_path = dump_ui()
    slots = find_untried_matching_slots(xml_path, time1, time2, time_pattern)
    untried_slots = [s for s in slots if s["slot_id"] not in TRIED_SLOTS]
    
    if not untried_slots:
        print("No untried matching slots found on current screen. Scrolling twice...")
        scroll_up_twice()
            
        print("Checking for time slot once again after scrolling...")
        xml_path = dump_ui()
        slots_after_swipe = find_untried_matching_slots(xml_path, time1, time2, time_pattern)
        untried_slots = [s for s in slots_after_swipe if s["slot_id"] not in TRIED_SLOTS]
        
    if not untried_slots:
        print("  -> FAILED: No untried matching time range found.")
        TRIED_SLOTS.clear()
        PENDING_SLOTS = []
        return False
        
    target_slot = untried_slots[0]
    
    if len(untried_slots) > 1:
        PENDING_SLOTS = [s["slot_id"] for s in untried_slots[1:]]
        print(f"  -> Found multiple untried slots. Saving to pending: {PENDING_SLOTS}")
    
    print(f"  -> Tapping on slot '{target_slot['slot_id']}' at center {target_slot['center']}")
    tap(target_slot['center'][0], target_slot['center'][1])
    TRIED_SLOTS.add(target_slot['slot_id'])
    return True

def run_attendance_marking_routine() -> bool:
    """
    Tries to find a matching slot, clicks it, and checks for Submit.
    If Submit is found, clicks it and returns True.
    If Submit is not found, scrolls up twice (with 0.5s delay) and checks for additional slots,
    repeating the process.
    """
    global TRIED_SLOTS, PENDING_SLOTS
    
    while True:
        slot_clicked = check_time_n_click()
        if not slot_clicked:
            print("No matching slots found during marking routine.")
            return False
            
        print("\n--- Checking for 'Submit' ---")
        submit_found = wait_for_text_and_click("Submit", timeout=4)
        if submit_found:
            print("Submit clicked! Waiting 6 seconds...")
            time.sleep(6)
            return True
            
        print("Submit not found after clicking slot.")
        scroll_up_twice()

def wait_for_text_and_click(target_text: str, timeout: int) -> bool:
    """Polls the UI until text appears, then clicks its center."""
    print(f"Waiting for '{target_text}' (timeout: {timeout}s)...")
    start_time = time.time()
    
    while (time.time() - start_time) < timeout:
        xml_path = dump_ui()
        bounds = find_text_bounds(xml_path, target_text)
        
        if bounds:
            x1, y1, x2, y2 = bounds
            center_x, center_y = (x1 + x2) // 2, (y1 + y2) // 2
            
            print(f"  -> Found! Tapping ({center_x}, {center_y})")
            tap(center_x, center_y)
            return True
            
        time.sleep(1.5)
        
    print(f"  -> FAILED: '{target_text}' did not appear within {timeout} seconds.")
    return False

def execute_step(step: dict) -> bool:
    """Helper to execute a single workflow step and return success status."""
    action = step.get("action")
    
    if action == "text":
        return wait_for_text_and_click(step["target"], step.get("timeout", 11))
        
    elif action == "check_time":
        return check_time_n_click(step.get("timeout", 11))
        
    elif action == "coord":
        x, y = step["x"], step["y"]
        print(f"Executing fixed tap at ({x}, {y})")
        tap(x, y)
        return True
        
    elif action == "swipe":
        sx, sy = step["start_x"], step["start_y"]
        ex, ey = step["end_x"], step["end_y"]
        duration = step.get("duration", 300)
        print(f"Swiping from ({sx}, {sy}) to ({ex}, {ey}) over {duration}ms")
        adb("shell", "input", "swipe", str(sx), str(sy), str(ex), str(ey), str(duration))
        return True
        
    elif action == "sleep":
        duration = step["duration"]
        print(f"Sleeping for {duration} seconds...")
        time.sleep(duration)
        return True

    elif action == "launch_app":
        package = step["package"]
        print(f"Launching app package: {package}")
        adb("shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1")
        return True
        
    else:
        print(f"Unknown action type: {action}")
        return False

def close_app_routine(package_name: str = "edu.somaiya.somaiyaapp"):
    """
    Closes the app and removes it from the recent apps tray without touching the UI.
    Uses 'dumpsys activity recents' to find the task ID(s) and 'am stack remove' to dismiss them.
    """
    print(f"\n--- Soft-closing '{package_name}' and removing from Recents ---")
    try:
        result = adb("shell", "dumpsys", "activity", "recents")
        output = result.stdout
    except Exception as e:
        print(f"Failed to query recent tasks: {e}")
        return

    task_ids = []
    blocks = output.split("* Recent")
    for block in blocks:
        if package_name in block:
            task_id_match = re.search(r"\btaskId=(\d+)\b", block)
            if task_id_match:
                task_ids.append(task_id_match.group(1))
                continue
            
            id_match = re.search(r"\bid=(\d+)\b", block)
            if id_match:
                task_ids.append(id_match.group(1))
                continue

            hash_match = re.search(r"Task\{[a-f0-9]+\s+#(\d+)\b", block)
            if hash_match:
                task_ids.append(hash_match.group(1))
                continue

    unique_task_ids = list(dict.fromkeys(task_ids))

    if not unique_task_ids:
        print("No recent tasks found for this app.")
        return

    for task_id in unique_task_ids:
        print(f"Removing task ID {task_id} from recent apps stack...")
        try:
            adb("shell", "am", "stack", "remove", task_id)
        except Exception as e:
            print(f"Failed to remove task ID {task_id}: {e}")

def check_device_connection(serial: str, timeout: int = 41) -> bool:
    """Waits up to `timeout` seconds for the given device to be ready ('device' state)."""
    start_time = time.time()
    while (time.time() - start_time) < timeout:
        try:
            result = subprocess.run([ADB_BIN, "devices"], capture_output=True, text=True, check=True)
            for line in result.stdout.splitlines():
                parts = line.split()
                if parts and parts[0] == serial:
                    if len(parts) > 1 and parts[1] == "device":
                        return True
        except Exception:
            pass
        time.sleep(2)
    return False

def device_exists_in_list(serial: str) -> bool:
    """Checks if the device serial is present in the list of adb devices in any state."""
    try:
        result = subprocess.run([ADB_BIN, "devices"], capture_output=True, text=True, check=True)
        for line in result.stdout.splitlines():
            parts = line.split()
            if parts and parts[0] == serial:
                return True
    except Exception:
        pass
    return False

def start_emulator_cold_boot(avd_name: str = None):
    """
    Starts the emulator with a forced cold boot, then blocks until the device
    reports as ready in 'adb devices' (up to 120 seconds).

    A cold boot is requested via the -no-snapshot-load flag, which forces the
    emulator to boot from scratch rather than restoring a saved snapshot.

    Args:
        avd_name: The AVD name to launch. Defaults to the global AVD_NAME.
    """
    global ADB_SERIAL, AVD_NAME

    if avd_name is None:
        avd_name = AVD_NAME

    print(f"Starting emulator AVD '{avd_name}' with cold boot (-no-snapshot-load)...")

    if sys.platform.startswith("win"):
        cmd = f'start cmd /k "{EMULATOR_BIN}" -avd {avd_name} -no-snapshot-load'
        os.system(cmd)
    else:
        subprocess.Popen(
            [EMULATOR_BIN, "-avd", avd_name, "-no-snapshot-load"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    print(f"Waiting up to 120 seconds for '{ADB_SERIAL}' to come online after cold boot...")
    if check_device_connection(ADB_SERIAL, timeout=120):
        print(f"Emulator '{ADB_SERIAL}' is online and ready.")
    else:
        print(f"Warning: Emulator '{ADB_SERIAL}' did not report as ready within 120 seconds. "
              "Continuing anyway – the next ADB calls may fail if it is still booting.")

def force_close_emulator():
    """
    Force-closes the running emulator cleanly:
      1. Tries the graceful 'emu kill' ADB command.
      2. Falls back to killing the OS process if still running.
    """
    print("Force-closing emulator...")

    try:
        subprocess.run(
            [ADB_BIN, "-s", ADB_SERIAL, "emu", "kill"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        print("  -> 'emu kill' sent.")
    except Exception as e:
        print(f"  -> 'emu kill' failed or timed out: {e}")

    time.sleep(3)

    try:
        if sys.platform.startswith("win"):
            subprocess.run(
                ["taskkill", "/F", "/IM", "emulator.exe"],
                capture_output=True,
                timeout=10,
            )
        else:
            subprocess.run(
                ["pkill", "-f", f"emulator.*{AVD_NAME}"],
                capture_output=True,
                timeout=10,
            )
        print("  -> OS-level kill sent.")
    except Exception as e:
        print(f"  -> OS-level kill failed or timed out: {e}")

    time.sleep(2)
    print("Emulator force-close sequence complete.")

def restart_emulator_cold_boot(avd_name: str = None):
    """
    Full emulator restart cycle:
      1. Force-close the running emulator.
      2. Wait 3 seconds.
      3. Cold-boot the emulator.
      4. Wait for it to become ready (up to 120 s inside start_emulator_cold_boot).
    """
    if avd_name is None:
        avd_name = AVD_NAME

    print("\n=== Emulator Restart: Force-close + Cold Boot ===")
    force_close_emulator()
    print("Waiting 3 seconds before cold booting...")
    time.sleep(3)
    start_emulator_cold_boot(avd_name)
    print("=== Emulator restart complete. ===\n")

def next_half_hour_timestamp() -> datetime:
    """
    Returns the next datetime that falls on a :30-minute mark
    (e.g. 09:30, 10:30, 11:30 …).
    """
    now = datetime.now()
    if now.minute < 30:
        return now.replace(minute=30, second=0, microsecond=0)
    else:
        next_hour = now + timedelta(hours=1)
        return next_hour.replace(minute=30, second=0, microsecond=0)

# ---------------------------------------------------------------------------
# bot1-specific login routine
# ---------------------------------------------------------------------------

def login_routine_bot1() -> bool:
    """
    Modified 3-step login sequence for bot1:

    Step 1 – Click the ImageView at index 7.
        Uses find_and_click_by_class_and_index() on a fresh UI dump to locate
        class="android.widget.ImageView" with index="7" and tap its centre.
        If the element is not found the routine returns False immediately so the
        outer loop can go back to searching for attendance / whatever is on screen.

    Step 2 – Find and click the text "somaiya.edu".
        Polls the UI for up to 6 seconds. If not found within that window,
        returns False so the outer loop retries from the top.

    Step 3 – Find and click "Low Attendance Alert".
        Polls the UI for up to 6 seconds. If not found, returns False so the
        outer loop retries from the top (search for Attendance or Login text again).

    Returns:
        True  – all three steps completed successfully.
        False – any step failed; outer loop should continue searching.
    """
    print("\n--- bot1 Login Routine (3-step) ---")

    # ------------------------------------------------------------------
    # Step 1: Dump UI and click ImageView index=7
    # ------------------------------------------------------------------
    print("Step 1: Dumping UI to find class='android.widget.ImageView' index='7'...")
    xml_path = dump_ui()
    clicked = find_and_click_by_class_and_index(xml_path, "android.widget.ImageView", "7")

    if not clicked:
        print("  -> ImageView index=7 not found. Skipping login step, looping back...")
        return False

    print("  -> ImageView tapped. Waiting 2 seconds...")
    time.sleep(2)

    # ------------------------------------------------------------------
    # Step 2: Find and click "somaiya.edu" text (timeout: 6 s)
    # ------------------------------------------------------------------
    print("Step 2: Looking for 'somaiya.edu' text (timeout: 6s)...")
    start_time = time.time()
    somaiya_found = False

    while (time.time() - start_time) < 6:
        xml_path = dump_ui()
        bounds = find_text_bounds(xml_path, "somaiya.edu")
        if bounds:
            x1, y1, x2, y2 = bounds
            center_x, center_y = (x1 + x2) // 2, (y1 + y2) // 2
            print(f"  -> 'somaiya.edu' found! Tapping ({center_x}, {center_y}).")
            tap(center_x, center_y)
            somaiya_found = True
            break
        time.sleep(1.0)

    if not somaiya_found:
        print("  -> 'somaiya.edu' not found within 6 seconds. "
              "Timing out, looping back to attendance search...")
        return False

    print("  -> 'somaiya.edu' tapped. Waiting 2 seconds...")
    time.sleep(2)

    # ------------------------------------------------------------------
    # Step 3: Check if "Low Attendance Alert" text is present (timeout: 6 s).
    # If found, tap the fixed coordinate (975, 345) — same as the original
    # login routine in bot.py — instead of the element's own centre.
    # ------------------------------------------------------------------
    print("Step 3: Checking for 'Low Attendance Alert' text presence (timeout: 6s)...")
    start_time = time.time()
    alert_found = False

    while (time.time() - start_time) < 6:
        xml_path = dump_ui()
        bounds = find_text_bounds(xml_path, "Low Attendance Alert")
        if bounds:
            print("  -> 'Low Attendance Alert' present! Tapping fixed coordinate (975, 345).")
            tap(975, 345)
            alert_found = True
            break
        time.sleep(1.0)

    if not alert_found:
        print("  -> 'Low Attendance Alert' not found within 6 seconds. "
              "Timing out, looping back to attendance search...")
        return False

    print("  -> Login routine (bot1) complete.")
    return True

# ---------------------------------------------------------------------------

def main():
    global ADB_SERIAL, AVD_NAME, TRIED_SLOTS, PENDING_SLOTS
    
    if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help"):
        print(__doc__.strip())
        sys.exit(0)
        
    # 1. Handle device connection
    if len(sys.argv) > 1:
        ADB_SERIAL = sys.argv[1]
        print(f"Target ADB Serial provided: {ADB_SERIAL}")
        
        if device_exists_in_list(ADB_SERIAL):
            print(f"Device '{ADB_SERIAL}' exists. Waiting up to 41 seconds for it to wake/connect...")
            if check_device_connection(ADB_SERIAL, timeout=41):
                print(f"Device '{ADB_SERIAL}' is ready.")
            else:
                print(f"Error: Device '{ADB_SERIAL}' did not connect/wake within 41 seconds.")
                sys.exit(1)
        else:
            print(f"Error: Device '{ADB_SERIAL}' does not exist in the list of ADB devices.")
            sys.exit(1)
            
    else:
        print("No serial argument provided. Checking for active ADB devices...")
        connected_devices = []
        try:
            result = subprocess.run([ADB_BIN, "devices"], capture_output=True, text=True, check=True)
            for line in result.stdout.splitlines():
                parts = line.split()
                if parts and len(parts) > 1 and parts[1] == "device":
                    connected_devices.append(parts[0])
        except Exception as e:
            print(f"Error running adb devices: {e}")
            sys.exit(1)
            
        if connected_devices:
            ADB_SERIAL = connected_devices[0]
            print(f"Found active device '{ADB_SERIAL}'. Proceeding...")
        else:
            ADB_SERIAL = "emulator-5554"
            print("No active devices found. Cold-booting emulator...")
            start_emulator_cold_boot(AVD_NAME)
            if not check_device_connection(ADB_SERIAL, timeout=41):
                print(f"Error: Emulator '{ADB_SERIAL}' failed to start or connect.")
                sys.exit(1)

    # 2. Run the main workflow loop
    last_submit_time: datetime = datetime.now()
    emulator_restart_scheduled_after: datetime | None = None

    try:
        while True:
            now = datetime.now()

            # Scheduled :30-mark restart check
            if emulator_restart_scheduled_after is not None and now >= emulator_restart_scheduled_after:
                print(f"\n[SCHEDULER] Reached the scheduled :30-mark restart time "
                      f"({emulator_restart_scheduled_after.strftime('%H:%M')}). "
                      "Restarting emulator now...")
                restart_emulator_cold_boot()
                emulator_restart_scheduled_after = None
                last_submit_time = datetime.now()
                TRIED_SLOTS.clear()
                PENDING_SLOTS = []

            # Check whether 1 hour has elapsed without a Submit click
            if emulator_restart_scheduled_after is None:
                minutes_since_submit = (now - last_submit_time).total_seconds() / 60
                if minutes_since_submit >= 60:
                    next_half = next_half_hour_timestamp()
                    print(f"\n[SCHEDULER] No Submit clicked in the last "
                          f"{minutes_since_submit:.0f} minutes. "
                          f"Scheduling emulator restart at next :30 mark: "
                          f"{next_half.strftime('%H:%M')}.")
                    emulator_restart_scheduled_after = next_half

            print("\n--- Starting New Attempt ---")
            
            # App open
            print("Launching app package: edu.somaiya.somaiyaapp")
            adb("shell", "monkey", "-p", "edu.somaiya.somaiyaapp", "-c", "android.intent.category.LAUNCHER", "1")
            
            print("Sleeping for 13 seconds...")
            time.sleep(13)
            
            attendance_completed = False
            
            while True:
                print("Checking for 'Attendance' button...")
                attendance_found = wait_for_text_and_click("Attendance", timeout=11)
                
                if attendance_found:
                    print("Attendance button clicked! Waiting 2 seconds...")
                    time.sleep(2)
                    
                    print("Starting attendance marking routine...")
                    attendance_completed = run_attendance_marking_routine()
                    break
                else:
                    # If attendance is not found, check for login
                    print("Attendance button not found. Checking for LOGIN / login text...")
                    login_found = check_text_present_with_timeout("login", timeout=4)
                    
                    if login_found:
                        # Execute bot1's modified login routine
                        print("Login text found! Executing bot1 login routine...")
                        login_ok = login_routine_bot1()

                        if login_ok:
                            print("Login routine complete. Looping back to check attendance...")
                        else:
                            print("Login routine did not complete (element/alert not found). "
                                  "Looping back to top of inner loop...")
                        # Either way, loop back to the top of the inner while loop
                        continue
                    else:
                        print("Login text not found. Aborting this attempt.")
                        break
            
            if attendance_completed:
                print("\nWorkflow completed successfully! Restarting emulator (cold boot)...")
                last_submit_time = datetime.now()
                emulator_restart_scheduled_after = None

                close_app_routine()
                TRIED_SLOTS.clear()
                PENDING_SLOTS = []

                restart_emulator_cold_boot()

                print("Continuing loop after emulator restart...")
                continue

            else:
                print("\nAttempt completed/failed without attendance marking.")
                
            print("Cleaning up app...")
            close_app_routine()
            TRIED_SLOTS.clear()
            PENDING_SLOTS = []
            
            print("Sleeping for 10 seconds before the next attempt...")
            time.sleep(10)
    except KeyboardInterrupt:
        print("\nScript interrupted by user. Exiting.")
        sys.exit(0)

if __name__ == "__main__":
    main()
