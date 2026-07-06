@echo off
echo ================================================
echo   Push changes to GitHub + Deploy
echo ================================================
echo.

git add .
echo.
set /p msg="Enter commit message: "
git commit -m "%msg%"
echo.
echo Pushing to GitHub...
git push origin main
echo.
echo Done! Streamlit Cloud will redeploy in 2-3 minutes.
echo App: check your Streamlit Cloud dashboard for this app's URL
echo.
pause
