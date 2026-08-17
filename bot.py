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
    try:
        tree = ET.parse(xml_path)
    except ET.ParseError:
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
    
    # Reset PENDING_SLOTS for this scan
    PENDING_SLOTS = []
    
    now = datetime.now()
    time1 = now.time()
    time2 = (now - timedelta(minutes=10)).time()
    
    print(f"Looking for time slot... (Current time: {now.strftime('%H:%M')}, Adjusted target: {(now - timedelta(minutes=10)).strftime('%H:%M')})")
    print(f"Already tried slots: {list(TRIED_SLOTS)}")
    
    time_pattern = re.compile(r"(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})")
    
    # 1. Dump UI and scan current screen
    print("Checking for time slot on current screen...")
    xml_path = dump_ui()
    slots = find_untried_matching_slots(xml_path, time1, time2, time_pattern)
    untried_slots = [s for s in slots if s["slot_id"] not in TRIED_SLOTS]
    
    # 2. If not found on current screen, scroll twice and check again
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
        
    # We found at least one untried slot!
    target_slot = untried_slots[0]
    
    # Save the other untried slots in PENDING_SLOTS
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
        # Check time and click on a matching slot.
        slot_clicked = check_time_n_click()
        if not slot_clicked:
            print("No matching slots found during marking routine.")
            return False
            
        # A slot was found and clicked!
        # Now check for Submit button with 4-second timeout
        print("\n--- Checking for 'Submit' ---")
        submit_found = wait_for_text_and_click("Submit", timeout=4)
        if submit_found:
            print("Submit clicked! Waiting 4 seconds...")
            time.sleep(4)
            return True
            
        # Submit not found!
        print("Submit not found after clicking slot.")
        # Scroll 2 times with 0.5s delay and check for any additional slots
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

def check_device_connection(serial: str, timeout: int = 41) -> bool:
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
        time.sleep(2)
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
    global ADB_SERIAL, TRIED_SLOTS, PENDING_SLOTS
    
    # 1. Handle device connection
    if len(sys.argv) > 1:
        ADB_SERIAL = sys.argv[1]
        print(f"Target ADB Serial provided: {ADB_SERIAL}")
        
        # Check if the device exists in adb devices list
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
            print("Waiting up to 41 seconds for the emulator to start and connect...")
            if check_device_connection(ADB_SERIAL, timeout=41):
                print(f"Emulator '{ADB_SERIAL}' started and connected successfully.")
            else:
                print(f"Error: Emulator '{ADB_SERIAL}' failed to start or connect within 41 seconds.")
                sys.exit(1)

    # 2. Run the main workflow loop
    try:
        while True:
            print("\n--- Starting New Attempt ---")
            
            # App open
            print("Launching app package: edu.somaiya.somaiyaapp")
            adb("shell", "monkey", "-p", "edu.somaiya.somaiyaapp", "-c", "android.intent.category.LAUNCHER", "1")
            
            # Sleep 13 seconds
            print("Sleeping for 13 seconds...")
            time.sleep(13)
            
            attendance_completed = False
            
            while True:
                # Check attendance (wait up to 11 seconds for "Attendance" text, click if found)
                print("Checking for 'Attendance' button...")
                attendance_found = wait_for_text_and_click("Attendance", timeout=11)
                
                if attendance_found:
                    print("Attendance button clicked! Waiting 2 seconds...")
                    time.sleep(2)
                    
                    # Mark attendance routine with time slot selector
                    print("Starting attendance marking routine...")
                    attendance_completed = run_attendance_marking_routine()
                    break  # Break out of the inner loop to end the attempt
                else:
                    # If attendance is not found, check for login
                    print("Attendance button not found. Checking for LOGIN / login text...")
                    login_found = check_text_present_with_timeout("login", timeout=4)
                    
                    if login_found:
                        # Execute login routine
                        print("Login text found! Executing login routine...")
                        print("Tapping (660, 1800) exactly...")
                        tap(660, 1800)
                        print("Waiting 4 seconds...")
                        time.sleep(4)
                        print("Tapping (540, 1250) exactly...")
                        tap(540, 1250)
                        print("Waiting 5 seconds...")
                        time.sleep(5)
                        print("Tapping (975, 345) exactly...")
                        tap(975, 345)
                        print("Waiting 4 seconds...")
                        time.sleep(4)
                        
                        # Loop back to check attendance again
                        print("Login routine complete. Looping back to check attendance...")
                        continue
                    else:
                        print("Login text not found. Aborting this attempt.")
                        break  # Break out of the inner loop to restart
            
            if attendance_completed:
                print("\nWorkflow completed successfully!")
            else:
                print("\nAttempt completed/failed without attendance marking.")
                
            print("Cleaning up app...")
            close_app_routine()
            TRIED_SLOTS.clear()
            PENDING_SLOTS = []
            
            print("Sleeping for 30 seconds before the next attempt...")
            time.sleep(30)
    except KeyboardInterrupt:
        print("\nScript interrupted by user. Exiting.")
        sys.exit(0)

if __name__ == "__main__":
    main()
