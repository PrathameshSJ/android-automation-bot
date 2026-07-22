"""
native_click.py

Uses Android's native uiautomator dump to find text on screen
and tap it. Extremely fast and requires no third-party libraries.

Usage:
    python native_click.py "string to find"
"""

import sys
import os
import subprocess
import tempfile
import xml.etree.ElementTree as ET

ADB_SERIAL = "emulator-5554"  # Update this to match `adb devices`

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
    # Using /data/local/tmp because it is always writable by adb shell [1]
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

def main():
    if len(sys.argv) < 2:
        print('Usage: python native_click.py "<text-to-find>"')
        sys.exit(1)

    target_text = " ".join(sys.argv[1:])
    
    # Quick connectivity check
    try:
        devices = subprocess.run(["adb", "devices"], capture_output=True, text=True).stdout
        if ADB_SERIAL not in devices:
             print(f"Device '{ADB_SERIAL}' not found. Update ADB_SERIAL in the script.")
             sys.exit(1)
    except FileNotFoundError:
        print("ADB is not installed or not in your PATH.")
        sys.exit(1)

    print("Dumping screen UI...")
    xml_path = dump_ui()
    
    print(f"Looking for '{target_text}'...")
    bounds = find_text_bounds(xml_path, target_text)
    
    if bounds:
        x1, y1, x2, y2 = bounds
        
        # Calculate center coordinates by averaging the bounds
        center_x = (x1 + x2) // 2
        center_y = (y1 + y2) // 2
        
        print(f"Match found! Node bounds: {bounds}")
        print(f"Tapping center coordinate: ({center_x}, {center_y})")
        tap(center_x, center_y)
        print("Done.")
    else:
        print("Text not found on the current screen.")
        sys.exit(2)

if __name__ == "__main__":
    main()