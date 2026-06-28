@echo off
setlocal
cd /d "%~dp0"

echo ==============================================
echo  US Stock AI — 一鍵更新 + 上傳 + 匯智慧庫
echo ==============================================

:: 1. 評分更新
echo.
echo [1/4] 執行每日評分 pipeline...
python main.py
if errorlevel 1 (
    echo [ERROR] 評分失敗，中止。
    pause
    exit /b 1
)

:: 2. 上傳到 GitHub
echo.
echo [2/4] 上傳 dashboard 到 GitHub...
git add docs\dashboard_data.json docs\performance_data.json docs\divergence_history.json
git add -f data\us_stock_ai.sqlite3 2>nul
git diff --staged --quiet && (echo 無變更，略過 commit。) || git commit -m "chore: update dashboard %DATE%"
git push
if errorlevel 1 echo [Warning] git push 失敗，請手動確認。

:: 3. 匯入智慧庫
echo.
echo [3/4] 匯出已結算訊號到 Trading Knowledge Hub...
python scripts\export_learning_to_knowledge_hub.py
if errorlevel 1 echo [Warning] KB 匯出部分失敗，請檢查 MCP server。

:: 4. 提交 KB 匯出旗標
echo.
echo [4/4] 提交 KB 匯出記錄...
git add -f data\us_stock_ai.sqlite3 2>nul
git diff --staged --quiet && (echo 無新匯出記錄。) || git commit -m "chore: kb export %DATE%"
git push 2>nul

echo.
echo ==============================================
echo  完成！
echo ==============================================
pause
