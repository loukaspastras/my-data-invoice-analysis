@echo off
chcp 65001 >nul
title Invoice Parser

echo.
echo ╔════════════════════════════════════════════╗
echo ║      📄 Invoice Parser - Εκκίνηση         ║
echo ╚════════════════════════════════════════════╝
echo.

:: Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ Η Python δεν είναι εγκατεστημένη!
    echo.
    echo Παρακαλώ διαβάστε το αρχείο ΟΔΗΓΙΕΣ.md
    echo.
    pause
    exit /b 1
)

echo ✓ Python βρέθηκε
echo.

:: Check if requirements are installed
python -c "import streamlit" >nul 2>&1
if errorlevel 1 (
    echo Εγκατάσταση απαιτούμενων βιβλιοθηκών...
    echo.
    pip install -r requirements.txt
    echo.
)

echo ✓ Βιβλιοθήκες έτοιμες
echo.
echo ════════════════════════════════════════════
echo   Η εφαρμογή θα ανοίξει στον browser σας
echo   Για να κλείσετε την εφαρμογή, κλείστε
echo   αυτό το παράθυρο.
echo ════════════════════════════════════════════
echo.

:: Run the app
streamlit run app.py --server.port 8501

pause


