# StudyRadar для нативного Windows (без make).
# Запуск:  powershell -ExecutionPolicy Bypass -File scripts\win.ps1 setup
# Команды: setup | doctor | simulate | simulate-fast | ui | bot | test | eval | eval-quick | screenshots | slides | presentation
param([string]$cmd = "help", [string]$arg = "")

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
$py = ".venv\Scripts\python.exe"
$radar = ".venv\Scripts\study-radar.exe"
$env:PYTHONIOENCODING = "utf-8"
chcp 65001 > $null

switch ($cmd) {
  "setup" {
    if (-not (Test-Path $py)) { py -3 -m venv .venv; if (-not $?) { python -m venv .venv } }
    & $py -m pip install --upgrade pip -q
    & $py -m pip install -e ".[dev,slides]" -q
    if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env" }
    Write-Host "OK. Dalshe: scripts\win.ps1 doctor"
  }
  "doctor"        { & $radar doctor }
  "simulate"      { & $radar simulate }
  "simulate-fast" { $env:LLM_MODEL = "qwen3:4b"; & $radar simulate }
  "ui"            { & $radar ui }
  "bot"           { & $radar bot }
  "ask"           { & $radar ask $arg }
  "index"         { & $radar index --export $arg }
  "test"          { & $py -m pytest }
  "eval"          { & $radar eval }
  "eval-quick"    { & $radar eval --limit 80 --act-sample 10 }
  "screenshots"   { & $py -m playwright install chromium; & $py scripts\screenshots.py }
  "slides"        { & $py scripts\build_slides.py }
  "presentation"  {
    & $radar simulate
    & $py -m playwright install chromium
    & $py scripts\screenshots.py
    & $py scripts\build_slides.py
  }
  default { Write-Host "Komandy: setup | doctor | simulate | simulate-fast | ui | bot | ask | index | test | eval | eval-quick | screenshots | slides | presentation" }
}
