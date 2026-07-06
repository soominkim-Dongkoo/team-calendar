@echo off
echo ========================================
echo  Build: Sales Upload exe
echo ========================================
echo.

echo [1/3] Installing packages...
pip install pyinstaller xlwings
if %errorlevel% neq 0 (
    echo ERROR: pip install failed
    pause
    exit /b 1
)

echo.
echo [2/3] Fixing encoding issues...
python fix_encoding.py
if %errorlevel% neq 0 (
    echo ERROR: fix_encoding.py failed
    pause
    exit /b 1
)

echo.
echo [3/3] Building exe...
pyinstaller --onefile --windowed --name "SalesUpload" ^
  --additional-hooks-dir . ^
  --hidden-import win32com ^
  --hidden-import win32com.client ^
  --hidden-import pywintypes ^
  --hidden-import pythoncom ^
  --collect-all win32com ^
  --add-binary "C:\Python\Lib\site-packages\pywin32_system32\pythoncom311.dll;." ^
  --add-binary "C:\Python\Lib\site-packages\pywin32_system32\pywintypes311.dll;." ^
  --exclude-module pandas ^
  --exclude-module numpy ^
  --exclude-module PIL ^
  --exclude-module matplotlib ^
  --exclude-module scipy ^
  --exclude-module xlwings ^
  --exclude-module sqlalchemy ^
  upload_sales.py
if %errorlevel% neq 0 (
    echo ERROR: PyInstaller failed
    pause
    exit /b 1
)

echo.
echo ========================================
if exist dist\SalesUpload.exe (
    echo  Done! Use dist\SalesUpload.exe
) else (
    echo  Build failed.
)
echo ========================================
pause
