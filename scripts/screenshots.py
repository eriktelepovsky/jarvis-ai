"""The README's screenshots, taken from demo mode.

    uv run --with playwright --with pillow python scripts/screenshots.py

Starts myOrb in demo mode with an empty data directory, in a time zone where it is now mid-morning
(so the clock fits the demo's day), plays a YouTube video in the dock and writes
docs/images/{welcome,report,details,ask,phone}.jpg. Needs Google Chrome: Playwright's own Chromium
cannot play YouTube. The welcome screen is the frame given by --frame (the moment the video shows
its title card); every frame is kept in the printed directory to pick another.
"""

import argparse
import asyncio
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, available_timezones

from PIL import Image
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
IMAGES = ROOT / "docs" / "images"
PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"
PASSWORD = "demo-screenshots-1"

POST = """async ([url, data]) => {
  const form = new FormData();
  for (const [key, value] of Object.entries(data)) form.append(key, value);
  return (await fetch(url, {method: "POST", body: form})).status;
}"""


def mid_morning() -> str:
    """A time zone where it is now 10:15–11:30, preferring America and Europe."""
    found = None
    for name in sorted(available_timezones()):
        if "/" not in name or name.startswith(("Etc/", "SystemV/")):
            continue
        now = datetime.now(ZoneInfo(name))
        if 615 <= now.hour * 60 + now.minute <= 690:
            found = name
            if name.startswith(("America/", "Europe/")):
                break
    if found is None:
        sys.exit("No time zone has mid-morning now; try again in a few minutes.")
    return found


def serve(data: Path, timezone: str) -> tuple[subprocess.Popen, str]:
    """myOrb in demo mode, and its setup token from the log."""
    log = data / "server.log"
    env = {
        **os.environ,
        "MYORB_DEMO": "1",
        "MYORB_DATA_DIR": str(data / "data"),
        "MYORB_TIMEZONE": timezone,
        "MYORB_PORT": str(PORT),
        "MYORB_PUBLIC_URL": BASE,
    }
    command = ["uv", "run", "myorb", "serve"]
    server = subprocess.Popen(
        command, cwd=ROOT, env=env, stdout=log.open("w"), stderr=subprocess.STDOUT
    )
    for _ in range(120):
        if token := re.search(r"setup\?token=(\S+)", log.read_text()):
            return server, token.group(1)
        if server.poll() is not None:
            sys.exit(f"myOrb did not start:\n{log.read_text()}")
        time.sleep(0.5)
    server.terminate()
    sys.exit("myOrb did not print its setup link.")


async def shoot(out: Path, token: str, timezone: str, video: str, width: int, height: int) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            channel="chrome", args=["--autoplay-policy=no-user-gesture-required"]
        )
        context = await browser.new_context(
            viewport={"width": width, "height": height},
            device_scale_factor=2,
            color_scheme="dark",
            timezone_id=timezone,
            locale="en-GB",
        )
        page = await context.new_page()
        await page.goto(f"{BASE}/setup?token={token}")
        setup = {
            "token": token,
            "password": PASSWORD,
            "password_confirmation": PASSWORD,
            "name": "Tony",
            "assistant": "J.A.R.V.I.S.",
        }
        await page.evaluate(POST, ["/setup", setup])
        music = {"source": "youtube", "url": f"https://www.youtube.com/watch?v={video}"}
        await page.evaluate(POST, ["/integrations/music", music])

        await page.goto(f"{BASE}/")
        await page.wait_for_timeout(2500)
        await page.click("[data-toggle='music']")
        for i in range(6):
            await page.wait_for_timeout(1500)
            await page.screenshot(path=out / f"frame-{i}.png")
        await page.click("[data-toggle='music']")

        for name, opener in [
            ("report", "[data-open-report]"),
            ("details", "[data-open$='jira']"),
            ("ask", "[data-ask-quick^='Plan my day']"),
        ]:
            await page.click(opener)
            await page.wait_for_timeout(2000)
            await page.screenshot(path=out / f"{name}.png")
            await page.keyboard.press("Escape")

        phone = await context.new_page()
        await phone.set_viewport_size({"width": 390, "height": 844})
        await phone.goto(f"{BASE}/")
        await phone.wait_for_timeout(5000)
        await phone.screenshot(path=out / "phone.png")
        await browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--video", default="0GvTE_kG1yY", help="YouTube video in the dock")
    parser.add_argument("--size", default="1600x1100", help="the window, WIDTHxHEIGHT")
    parser.add_argument("--frame", type=int, default=3, help="frame (0–5) for welcome.jpg")
    args = parser.parse_args()
    width, height = (int(n) for n in args.size.split("x"))

    out = Path(tempfile.mkdtemp(prefix="myorb-screenshots-"))
    timezone = mid_morning()
    server, token = serve(out, timezone)
    try:
        asyncio.run(shoot(out, token, timezone, args.video, width, height))
    finally:
        server.terminate()
        server.wait()

    sources = {
        "welcome": f"frame-{args.frame}",
        "report": "report",
        "details": "details",
        "ask": "ask",
        "phone": "phone",
    }
    for name, source in sources.items():
        image = Image.open(out / f"{source}.png").convert("RGB")
        image.save(IMAGES / f"{name}.jpg", quality=85, optimize=True, progressive=True)
    print(f"Wrote docs/images in {timezone}. Every frame is in {out}")


if __name__ == "__main__":
    main()
