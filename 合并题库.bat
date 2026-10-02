@echo off
chcp 65001 >nul
copy /b "kaogong.db.part-*" "kaogong.db" >nul
echo 题库合并完成！已生成 kaogong.db，现在可以双击 启动考公系统.bat
pause
