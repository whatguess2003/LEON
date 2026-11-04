@echo off
echo Starting LEON PMS Chat Server...
echo.
echo Make sure you have:
echo 1. Python 3.12 installed
echo 2. All dependencies installed: pip install -r requirements.txt
echo 3. .env file configured with API keys
echo.
echo Server will be available at: http://localhost:8002
echo.
python main.py
pause 