import asyncio
import os
import random
from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv()

UG_EMAIL = os.getenv("UG_EMAIL")
UG_PASSWORD = os.getenv("UG_PASSWORD")

TAB_URL = "https://tabs.ultimate-guitar.com/tab/eagles/hotel-california-official-1910943"
OUTPUT_FILE = "hotel_california.gp"


async def human_pause(min_ms=120, max_ms=420):
    await asyncio.sleep(random.uniform(min_ms, max_ms) / 1000)


async def human_click(page, locator, *, timeout=10000):
    await locator.wait_for(state="visible", timeout=timeout)
    box = await locator.bounding_box()

    if box:
        target_x = box["x"] + box["width"] * random.uniform(0.35, 0.65)
        target_y = box["y"] + box["height"] * random.uniform(0.35, 0.65)
        current_x = target_x + random.uniform(-180, 180)
        current_y = target_y + random.uniform(-90, 90)

        await page.mouse.move(current_x, current_y)
        await human_pause(80, 220)
        await page.mouse.move(target_x, target_y, steps=random.randint(8, 18))
        await human_pause(90, 260)
        await page.mouse.down()
        await human_pause(45, 130)
        await page.mouse.up()
        return

    await locator.click()


async def human_type(page, locator, text):
    await locator.wait_for(state="visible")
    await human_click(page, locator)
    await human_pause(120, 300)

    for char in text:
        await locator.type(char, delay=random.randint(45, 170))

        if random.random() < 0.08:
            await human_pause(180, 520)


async def login(page):
    if not UG_EMAIL or not UG_PASSWORD:
        raise ValueError("UG_EMAIL and UG_PASSWORD must be set in .env before logging in.")

    await page.goto("https://www.ultimate-guitar.com/")
    await human_pause(700, 1600)

    # Click the Log In button in the nav (disambiguate by child span text)
    await human_click(
        page,
        page.locator(
            "button.GZm7j.KKBhY._8WVi7._6yJZx",
            has=page.get_by_text("Log In", exact=True),
        ),
    )

    await human_pause(800, 1400)  # wait for the modal animation to complete

    # Wait for the modal to appear
    await page.wait_for_selector("input.HDV3t.YZj3H.vcq4Y")
    await human_pause(250, 650)

    # Fill email and password by placeholder to disambiguate the two inputs
    await human_type(
        page,
        page.locator("input.HDV3t.YZj3H.vcq4Y[placeholder='Username or e-mail']"),
        UG_EMAIL,
    )
    await human_pause(250, 700)
    await human_type(
        page,
        page.locator("input.HDV3t.YZj3H.vcq4Y[placeholder='Password']"),
        UG_PASSWORD,
    )
    await human_pause(350, 900)

    # Click the submit button (disambiguate by child text)
    await human_click(
        page,
        page.locator(
            "button.t5rMH._8WRlq.mY3ch.nA0TX.NhiSc._281Nj.RKiQg.Ud8hS.j8yVh",
            has=page.get_by_text("Log In", exact=True),
        ),
    )

    # Wait for login to complete — modal closes and user menu appears
    await page.wait_for_selector("input.HDV3t.YZj3H.vcq4Y", state="hidden")

    print("Logged in.")



async def download_tab(page):
    await page.goto(TAB_URL)

    # TODO: wait for the page to fully load
    # hint: wait for a specific element that signals the tab viewer is ready

    # TODO: find and click the download button
    # hint: inspect the download button's selector on the page

    # The download is triggered as a browser download event, so you need to
    # intercept it with Playwright's expect_download() context manager.
    # TODO: wrap the click in an expect_download block and save the file
    # hint:
    # async with page.expect_download() as download_info:
    #     await page.click("selector")
    # download = await download_info.value
    # await download.save_as(OUTPUT_FILE)

    print(f"Downloaded to {OUTPUT_FILE}")


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)  # keep visible for debugging
        context = await browser.new_context()
        page = await context.new_page()

        await login(page)
        await download_tab(page)

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
