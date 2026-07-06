@echo off
echo ================================================
echo   Rameshwaram Industries Ops - First Time Setup
echo ================================================
echo.

echo [1/3] Installing Python dependencies...
pip install -r requirements.txt
if errorlevel 1 (
    echo ERROR: pip install failed. Make sure Python is installed.
    pause
    exit /b 1
)

echo.
echo [2/3] Creating .streamlit folder...
if not exist ".streamlit" mkdir ".streamlit"

echo.
echo [3/3] Checking secrets file...
if exist ".streamlit\secrets.toml" (
    echo secrets.toml already exists - skipping.
) else (
    echo Creating secrets.toml template...
    (
        echo SUPABASE_URL = "YOUR_SUPABASE_URL"
        echo SUPABASE_KEY = "YOUR_SUPABASE_KEY"
        echo.
        echo [users.admin]
        echo password = "YOUR_ADMIN_PASSWORD"
        echo role = "admin"
        echo name = "Admin"
        echo.
        echo [users.production]
        echo password = "YOUR_PROD_PASSWORD"
        echo role = "production"
        echo name = "Production Operator"
        echo.
        echo [users.dispatch]
        echo password = "YOUR_DISP_PASSWORD"
        echo role = "dispatch"
        echo name = "Dispatch Operator"
    ) > .streamlit\secrets.toml
    echo.
    echo IMPORTANT: Open .streamlit\secrets.toml and fill in the real credentials.
    echo Fill in the Supabase URL, Key, and passwords for this project.
)

echo.
echo ================================================
echo   Setup complete! Run the app with: run.bat
echo ================================================
pause
