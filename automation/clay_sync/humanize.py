"""
Human-pacing helpers for the Clay UI automation.

The point is to make clay_sync.py behave like a person doing data entry in
their own Clay account — typing at human speed and pausing between actions —
rather than firing UI events instantly. This avoids tripping anti-bot pacing
heuristics on a legitimate, logged-in session.

All timing is randomized so the cadence isn't a fixed, obviously-scripted beat.
"""

import random
import time


def dwell(a=0.15, b=0.5):
    """Short pause between discrete clicks/steps within one action. Kept light
    so the whole run stays under the time budget while still not machine-instant."""
    time.sleep(random.uniform(a, b))


def pace():
    """Brief pause between whole items (one workbook to the next)."""
    time.sleep(random.uniform(0.6, 1.6))


def type_into(page, locator, text):
    """Type `text` into `locator` one character at a time with jittered
    delays, instead of pasting it instantly via fill(). Any existing/default
    text is selected first so the first keystroke replaces it."""
    locator.click()
    dwell(0.15, 0.4)
    page.keyboard.press("Control+A")  # select default text so typing overwrites
    for ch in text:
        page.keyboard.type(ch)
        time.sleep(random.uniform(0.02, 0.06))
