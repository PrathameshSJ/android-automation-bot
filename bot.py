"""
bot.py

Combines native UI text detection, clicking capabilities, and the attendance
automated workflow into a single file. Supports passing the target ADB serial 
via command line arguments.

Usage:
    py bot.py [adb_serial]
    e.g., py bot.py emulator-5444
"""

import sys
import os
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import time
import re
from datetime import datetime, timedelta

# Default ADB serial fallback
ADB_SERIAL = "emulator-5554"

def adb(*args: str) -> subprocess.CompletedProcess:
    """Helper to run adb commands."""
    return subprocess.run(
        ["adb", "-s", ADB_SERIAL, *args],
        capture_output=True,
        text=True,
        check=True,
    )

def dump_ui() -> str:
    """Asks Android to dump the UI tree to XML and pulls it locally."""
    # Using /data/local/tmp because it is always writable by adb shell
    device_path = "/data/local/tmp/window_dump.xml"
    local_path = os.path.join(tempfile.gettempdir(), "window_dump.xml")
    
    try:
        # Generate the XML dump on the device
        adb("shell", "uiautomator", "dump", device_path)
        # Pull the XML to our local machine
        adb("pull", device_path, local_path)
    except subprocess.CalledProcessError as e:
        print(f"Failed to dump UI: {e.stderr}")
        sys.exit(1)
        
    return local_path

def find_text_bounds(xml_path: str, target_text: str) -> tuple:
    """Parses the XML and returns the (x1, y1, x2, y2) bounds of the target text."""
    try:
        tree = ET.parse(xml_path)
    except ET.ParseError:
        print("Failed to parse the XML dump.")
        sys.exit(1)

    target_lower = target_text.lower()
    
    # Iterate through every UI element on the screen
    for node in tree.iter():
        text = node.get('text', '')
        content_desc = node.get('content-desc', '')
        
        # Check both visible text and accessibility descriptions
        if target_lower in text.lower() or target_lower in content_desc.lower():
            bounds = node.get('bounds')
            
            # bounds string format looks like this: "[0,100][200,300]"
            if bounds and bounds != "[0,0][0,0]":
                # Clean up brackets to extract raw integers
                coords = bounds.replace('][', ',').strip('[]').split(',')
                try:
                    return tuple(map(int, coords))
                except ValueError:
                    continue
                    
    return None

def tap(x: int, y: int):
    """Executes the adb tap command."""
    adb("shell", "input", "tap", str(x), str(y))

def check_time_n_click(timeout: int = 10) -> bool:
    """
    Finds a time range (e.g., 11:00 - 12:00) on screen that matches the current system time,
    with a 10-minute offset backward.
    """
    now = datetime.now()
    target_datetime = now - timedelta(minutes=10)
    target_time = target_datetime.time()
    
    print(f"Looking for time slot... (Current time: {now.strftime('%H:%M')}, Adjusted target: {target_datetime.strftime('%H:%M')})")
    
    time_pattern = re.compile(r"(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})")
    
    start_time = time.time()
    while (time.time() - start_time) < timeout:
        xml_path = dump_ui()
        
        try:
            tree = ET.parse(xml_path)
        except ET.ParseError:
            time.sleep(0.5)
            continue
            
        for node in tree.iter():
            text = node.get('text', '')
            content_desc = node.get('content-desc', '')
            
            match = time_pattern.search(text) or time_pattern.search(content_desc)
            
            if match:
                start_str, end_str = match.groups()
                
                try:
                    start_h, start_m = map(int, start_str.split(':'))
                    end_h, end_m = map(int, end_str.split(':'))
                    
                    from datetime import time as dt_time
                    slot_start = dt_time(start_h, start_m)
                    slot_end = dt_time(end_h, end_m)
                except ValueError:
                    continue
                
                is_in_range = False
                if slot_start <= slot_end:
                    is_in_range = slot_start <= target_time < slot_end
                else:
                    is_in_range = target_time >= slot_start or target_time < slot_end
                    
                if is_in_range:
                    bounds = node.get('bounds')
                    if bounds and bounds != "[0,0][0,0]":
                        coords = bounds.replace('][', ',').strip('[]').split(',')
                        try:
                            x1, y1, x2, y2 = map(int, coords)
                            center_x, center_y = (x1 + x2) // 2, (y1 + y2) // 2
                            print(f"  -> Found active slot '{start_str} - {end_str}'! Tapping ({center_x}, {center_y})")
                            tap(center_x, center_y)
                            return True
                        except ValueError:
                            pass
                        
        time.sleep(0.5) 
        
    print(f"  -> FAILED: No matching time range found within {timeout} seconds.")
    return False

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
            
        time.sleep(0.5)
        
    print(f"  -> FAILED: '{target_text}' did not appear within {timeout} seconds.")
    return False

def execute_step(step: dict) -> bool:
    """Helper to execute a single workflow step and return success status."""
    action = step.get("action")
    
    if action == "text":
        return wait_for_text_and_click(step["target"], step.get("timeout", 10))
        
    elif action == "check_time":
        return check_time_n_click(step.get("timeout", 10))
        
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
        # Get the recent tasks list
        result = adb("shell", "dumpsys", "activity", "recents")
        output = result.stdout
    except Exception as e:
        print(f"Failed to query recent tasks: {e}")
        return

    # Find all task IDs matching the package name
    task_ids = []
    blocks = output.split("* Recent")
    for block in blocks:
        if package_name in block:
            # Try to find taskId=X
            task_id_match = re.search(r"\btaskId=(\d+)\b", block)
            if task_id_match:
                task_ids.append(task_id_match.group(1))
                continue
            
            # Try to find id=X
            id_match = re.search(r"\bid=(\d+)\b", block)
            if id_match:
                task_ids.append(id_match.group(1))
                continue

            # Try to find #X in Task{... #X ...}
            hash_match = re.search(r"Task\{[a-f0-9]+\s+#(\d+)\b", block)
            if hash_match:
                task_ids.append(hash_match.group(1))
                continue

    # Deduplicate task IDs
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

def check_device_connection(serial: str, timeout: int = 40) -> bool:
    """Waits up to `timeout` seconds for the given device to be ready ('device' state)."""
    start_time = time.time()
    while (time.time() - start_time) < timeout:
        try:
            result = subprocess.run(["adb", "devices"], capture_output=True, text=True, check=True)
            for line in result.stdout.splitlines():
                parts = line.split()
                if parts and parts[0] == serial:
                    if len(parts) > 1 and parts[1] == "device":
                        return True
        except Exception:
            pass
        time.sleep(1)
    return False

def device_exists_in_list(serial: str) -> bool:
    """Checks if the device serial is present in the list of adb devices in any state."""
    try:
        result = subprocess.run(["adb", "devices"], capture_output=True, text=True, check=True)
        for line in result.stdout.splitlines():
            parts = line.split()
            if parts and parts[0] == serial:
                return True
    except Exception:
        pass
    return False

def main():
    global ADB_SERIAL
    
    # 1. Handle device connection
    if len(sys.argv) > 1:
        ADB_SERIAL = sys.argv[1]
        print(f"Target ADB Serial provided: {ADB_SERIAL}")
        
        # Check if the device exists in adb devices list
        if device_exists_in_list(ADB_SERIAL):
            print(f"Device '{ADB_SERIAL}' exists. Waiting up to 40 seconds for it to wake/connect...")
            if check_device_connection(ADB_SERIAL, timeout=40):
                print(f"Device '{ADB_SERIAL}' is ready.")
            else:
                print(f"Error: Device '{ADB_SERIAL}' did not connect/wake within 40 seconds.")
                sys.exit(1)
        else:
            print(f"Error: Device '{ADB_SERIAL}' does not exist in the list of ADB devices.")
            sys.exit(1)
            
    else:
        # No args: Check if any device is already connected
        print("No serial argument provided. Checking for active ADB devices...")
        connected_devices = []
        try:
            result = subprocess.run(["adb", "devices"], capture_output=True, text=True, check=True)
            for line in result.stdout.splitlines():
                parts = line.split()
                if parts and len(parts) > 1 and parts[1] == "device":
                    connected_devices.append(parts[0])
        except Exception as e:
            print(f"Error running adb devices: {e}")
            sys.exit(1)
            
        if connected_devices:
            # Use the first connected device
            ADB_SERIAL = connected_devices[0]
            print(f"Found active device '{ADB_SERIAL}'. Proceeding...")
        else:
            # No devices are connected, start the default emulator
            ADB_SERIAL = "emulator-5554"
            print("No active devices found. Starting emulator AVD 'Medium_Phone'...")
            os.system('start cmd /k emulator -avd Medium_Phone')
            print("Waiting up to 40 seconds for the emulator to start and connect...")
            if check_device_connection(ADB_SERIAL, timeout=40):
                print(f"Emulator '{ADB_SERIAL}' started and connected successfully.")
            else:
                print(f"Error: Emulator '{ADB_SERIAL}' failed to start or connect within 40 seconds.")
                sys.exit(1)

    # 2. Run the main workflow loop
    while True:
        print("\n--- Starting New Attempt ---")
        
        # Define the setup sequence
        setup_steps = [
            {"action": "launch_app", "package": "edu.somaiya.somaiyaapp"},
            {"action": "sleep", "duration": 12},
            {"action": "text", "target": "Attendance", "timeout": 10}, 
            {"action": "sleep", "duration": 1},
            {"action": "check_time", "timeout": 10},
            {"action": "sleep", "duration": 3},
        ]
        
        setup_success = True
        for i, step in enumerate(setup_steps, 1):
            if not execute_step(step):
                print(f"Step {i} failed. Aborting current attempt.")
                setup_success = False
                break
        
        # If any part of the setup fails, reset the app and wait 1.5 mins
        if not setup_success:
            print("Error during setup sequence. Retrying in 1.5 mins...")
            close_app_routine()
            time.sleep(90)  # 1.5 minutes
            continue
            
        # The main conditional check: Search for 'Submit' with a 3-second timeout
        print("\n--- Checking for 'Submit' ---")
        submit_found = wait_for_text_and_click("Submit", timeout=3)
        
        if submit_found:
            print("Submit clicked! Waiting 3 seconds before exiting...")
            time.sleep(3)
            close_app_routine()
            print("\nWorkflow completed successfully! Exiting script.")
            break # Breaks the infinite loop and ends the script
        else:
            print("Submit text not found after 3 seconds. Quitting app and retrying...")
            close_app_routine()
            print("Sleeping for 1.5 minutes (90 seconds) before the next attempt...")
            time.sleep(90)

if __name__ == "__main__":
    main()
