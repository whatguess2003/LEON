"""Separate sandbox-manager process. It is not part of the LEON server.

The desk stays on its own process. This process asks that desk over HTTP and
writes the recipe library through the catalog publish function, which stores
one new version and indexes only that recipe.

From c:\\LEON:

    .venv\\Scripts\\python.exe -m sandbox_manager dry-run

    .venv\\Scripts\\python.exe -m sandbox_manager

dry-run asks one question, does not publish a recipe, and serves the status
page until the process is stopped. The second command asks about one question
every 14 minutes, up to 100 questions on each Asia/Kuala_Lumpur day.

SANDBOX_MAIN_URL overrides the desk base URL (default http://127.0.0.1:8002).
SANDBOX_STATUS_PORT overrides the status port (default 8012).
Status: http://127.0.0.1:8012/
Report: c:\\LEON\\sandbox_manager\\report.txt and report.json, rewritten after each ask.
Re-asks: c:\\LEON\\sandbox_manager\\reask.txt. After a normal ask publishes a recipe,
the same question is asked once more on a fresh thread. That re-ask is not one of
the 100 daily questions, does not publish, and does not send a flag sentence.
"""

from sandbox_manager.service import main

if __name__ == "__main__":
    main()
