@echo off
title SYLPHY Launcher
set REPO=D:\CWDI\SYLPHY_Sprint_10_plus_1_fixed\sylphy_work
set PYTHONPATH=%REPO%\src
set MODEL=lfm3-uncensored-32k:latest
set VOICE_MODEL=D:\CWDI\Sylphy_Archive\Sylphy\data\voice-models\kokoro-v1.0.onnx
set VOICE_VOICES=D:\CWDI\Sylphy_Archive\Sylphy\data\voice-models\voices-v1.0.bin
echo [SYLPHY] Starting Entity Daemon...
start "SYLPHY - Daemon" /MIN cmd /k "cd /d %REPO% && set PYTHONPATH=%PYTHONPATH% && py -3.13 -m sylphy_entity.daemon --conversation-model %MODEL% --voice-model "%VOICE_MODEL%" --voice-voices "%VOICE_VOICES%""
echo [SYLPHY] Waiting 8 seconds...
timeout /t 8 /nobreak > nul
echo [SYLPHY] Starting Avatar and Chat...
start "SYLPHY - Avatar" /B cmd /c "cd /d %REPO% && set PYTHONPATH=%PYTHONPATH% && py -3.13 -m sylphy_entity.embodiment.avatar_service start"
timeout /t 3 /nobreak > nul
exit
