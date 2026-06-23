"""Interactive manual-login flow.

Launches the same Camoufox browser the worker uses (persistent profile +
pinned fingerprint), opens Ultimate Guitar, and waits for you to log in by
hand. Pressing Enter in the terminal closes the browser cleanly, which flushes
the now-authenticated cookies / localStorage to PROFILE_DIR so subsequent runs
of the service start already logged in.

Each run starts from a clean device: any previously saved profile and
fingerprint are discarded first, so this login overwrites whatever was stored
before.

Run via `scripts/start-scraper.sh --login`, or directly:

    python -m app.manual_login
"""
from __future__ import annotations

import asyncio
import shutil

from app.browser.session import CamoufoxBrowserSession
from app.config import get_settings


def _reset_saved_session(settings) -> None:
    """Discard the previously saved profile + fingerprint so this login starts
    from a clean device and overwrites whatever was stored before."""
    shutil.rmtree(settings.profile_dir, ignore_errors=True)
    settings.fingerprint_path.unlink(missing_ok=True)


async def main() -> None:
    settings = get_settings()
    settings.headless = False  # must be visible so you can log in by hand

    _reset_saved_session(settings)

    session = CamoufoxBrowserSession(settings)
    print("Launching browser — a fresh fingerprint and profile will be created…")
    await session.start()
    await session.open_home()

    print(
        "\nUltimate Guitar is open in the browser window.\n"
        "Log in to your account there. Once you can see you're logged in,\n"
        "come back to this terminal and press Enter to save the session and exit.\n"
    )
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, input, "")

    print("Saving session and closing browser…")
    await session.close()
    print(
        f"Done. Session saved to {settings.profile_dir} and fingerprint to "
        f"{settings.fingerprint_path}.\n"
        "Run scripts/start-scraper.sh (without --login) to use it."
    )


if __name__ == "__main__":
    asyncio.run(main())
