"""
vision_click.py

Takes a screenshot from your Android emulator, sends it + a plain-English
instruction to Gemma 4 26B (vision), and taps whatever pixel coordinate
the model decides matches your instruction.

Setup:
    pip install google-genai

    Get an API key from https://aistudio.google.com/apikey
    Set it as an environment variable (PowerShell):
        setx GEMINI_API_KEY "your-key-here"
    (restart your terminal after setx so it picks up the new env var)

Usage:
    python vision_click.py "click the button that says smth"
    python vision_click.py "click anywhere green"
"""

import os
import re
import sys
import json
import subprocess
import tempfile

from google import genai
from google.genai import types
from PIL import Image

MODEL_NAME = "gemma-4-26b-a4b-it"
ADB_SERIAL = "emulator-5554"  # confirm with `adb devices` - "medium_phone" is just the AVD's window title


def get_client() -> genai.Client:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY not set. Get a key at https://aistudio.google.com/apikey "
            "and set it with: setx GEMINI_API_KEY \"your-key-here\""
        )
    return genai.Client(api_key=api_key)


def adb(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["adb", "-s", ADB_SERIAL, *args],
        capture_output=True,
        text=False,
        check=True,
    )


def take_screenshot() -> str:
    """Screenshots the emulator and pulls the PNG to a local temp file. Returns local path."""
    device_path = "/sdcard/vision_click_tmp.png"
    adb("shell", "screencap", "-p", device_path)

    local_path = os.path.join(tempfile.gettempdir(), "vision_click_screen.png")
    subprocess.run(
        ["adb", "-s", ADB_SERIAL, "pull", device_path, local_path],
        capture_output=True,
        check=True,
    )
    adb("shell", "rm", device_path)
    return local_path


def ask_model_for_coordinates(client: genai.Client, image_path: str, instruction: str) -> dict:
    img = Image.open(image_path)
    width, height = img.size

    prompt = f"""You are controlling an Android device by looking at a screenshot.

Instruction: "{instruction}"

Look at the screenshot and decide the single best point to tap to satisfy the
instruction. If the instruction refers to text, find that exact text on
screen and target its center. If it refers to a color, find the most
prominent region of that color and target its center.

Express the point using a NORMALIZED coordinate system from 0 to 1000 on
both axes, where (0,0) is the top-left corner of the image and (1000,1000)
is the bottom-right corner - NOT raw pixel values.

Respond with ONLY a JSON object, no other text, no markdown fences, in this
exact format:
{{"found": true, "x": <int 0-1000>, "y": <int 0-1000>, "reasoning": "<one short sentence>"}}

If nothing on screen matches the instruction, respond with:
{{"found": false, "x": null, "y": null, "reasoning": "<one short sentence>"}}
"""

    uploaded = client.files.upload(file=image_path)

    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=[uploaded, prompt],
    )

    text = response.text.strip()
    # Strip markdown fences if the model adds them despite instructions
    text = re.sub(r"^```(json)?|```$", "", text, flags=re.MULTILINE).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Model did not return valid JSON.\nRaw response:\n{text}") from e

    # --- ADD THIS BLOCK ---
    # Convert the 0-1000 normalized coordinates back to actual screen pixels
    if data.get("found") and data.get("x") is not None and data.get("y") is not None:
        data["x"] = int((data["x"] / 1000.0) * width)
        data["y"] = int((data["y"] / 1000.0) * height)
    # ----------------------

    return data


def tap(x: int, y: int) -> None:
    adb("shell", "input", "tap", str(x), str(y))


def main():
    if len(sys.argv) < 2:
        print('Usage: python vision_click.py "<instruction>"')
        sys.exit(1)

    instruction = " ".join(sys.argv[1:])

    print("Checking device connection...")
    devices = subprocess.run(["adb", "devices"], capture_output=True, text=True).stdout
    if ADB_SERIAL not in devices:
        print(f"Device '{ADB_SERIAL}' not found. Devices seen:\n{devices}")
        print("Update ADB_SERIAL at the top of this script to match.")
        sys.exit(1)

    client = get_client()

    print("Taking screenshot...")
    screenshot_path = take_screenshot()

    print(f"Asking {MODEL_NAME} to locate: {instruction!r}")
    result = ask_model_for_coordinates(client, screenshot_path, instruction)

    print(f"Model response: {result}")

    if result.get("found") and result.get("x") is not None and result.get("y") is not None:
        x, y = int(result["x"]), int(result["y"])
        print(f"Tapping ({x}, {y})...")
        tap(x, y)
        print("Done.")
    else:
        print(f"Model could not find a match. Reasoning: {result.get('reasoning')}")
        sys.exit(2)


if __name__ == "__main__":
    main()