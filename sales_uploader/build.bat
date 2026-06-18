@echo off
chcp 65001 > nul
echo ========================================
echo  매출 업로드 .exe 빌드
echo ========================================
echo.

echo [1/2] 필요 패키지 설치 중...
pip install pyinstaller xlwings --quiet

echo.
echo [2/2] .exe 빌드 중...
pyinstaller --onefile --windowed --name "매출업로드" --hidden-import xlwings upload_sales.py

echo.
echo ========================================
if exist dist\매출업로드.exe (
    echo  완료! dist\매출업로드.exe 를 사용하세요.
) else (
    echo  빌드 실패. 위 오류 메시지를 확인하세요.
)
echo ========================================
pause
