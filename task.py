from asyncio import subprocess
import os
import time
import sys
import re
from datetime import datetime, timedelta
import xml.etree.ElementTree as ET

try:
    from native_click import dump_ui, find_text_bounds, tap, adb
except ImportError:
    print("Error: Could not find 'native_click.py' in the same directory.")
    sys.exit(1)


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


def close_app_routine():
    """Executes the coordinated clicks and swipes to close the app via recent drawer."""
    print("\n--- Quitting App from Drawer ---")
    quit_steps = [
        {"action": "coord", "x": 850, "y": 2330}, # Tap recent apps drawer
        {"action": "sleep", "duration": 1.5},     # Brief pause to let drawer load
        {"action": "swipe", "start_x": 540, "start_y": 1200, "end_x": 540, "end_y": 100, "duration": 200}, # Swipe up
        {"action": "sleep", "duration": 1.0},     # Pause for dismissal animation
    ]
    for step in quit_steps:
        execute_step(step)


def main():
    print("Starting Automated Attendance Workflow...\n")

    os.system('start cmd /k emulator -avd Medium_Phone')

    time.sleep(40)  # Wait for the emulator to fully boot
    
    while True:
        print("\n--- Starting New Attempt ---")
        
        # Define the setup sequence
        setup_steps = [
            {"action": "launch_app", "package": "edu.somaiya.somaiyaapp"}, # Update this package name!
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