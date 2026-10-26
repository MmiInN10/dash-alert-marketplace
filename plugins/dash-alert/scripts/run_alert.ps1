# 작업 스케줄러가 호출한다: route.py 로 경로를 조회하고 claude -p 로 카톡 나와의 채팅에 보낸다.
# 사용: pwsh -NoProfile -File run_alert.ps1 -From 집 -To 회사
param(
    [Parameter(Mandatory)][string]$From,
    [Parameter(Mandatory)][string]$To
)
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$routePy = Join-Path $PSScriptRoot "route.py"
$logDir = Join-Path $env:USERPROFILE ".commute-alert\logs"
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir "alert.log"

# 프롬프트는 영어로 둔다 (인자 인코딩 문제와 불필요한 해석 여지를 줄이기 위해)
$cmd = "python -I `"$routePy`" --from `"$From`" --to `"$To`""
$prompt = "Step 1: run this with Bash and read the JSON it prints: $cmd . " +
    "Step 2: if the JSON has a 'first_bus' key, call the koreaBus-getKoreaBusArrival tool with stopName=first_bus.stop " +
    "and busNumber=first_bus.number (omit direction and stopNumber). If the result has exactly one candidate with an " +
    "arrivals entry, take firstArrivalSeconds and secondArrivalSeconds (skip null ones) and run the same Bash command again " +
    "with the extra argument --bus-sec `"<first>,<second>`"; use that new JSON. If the tool fails, finds no candidate or " +
    "more than one candidate, keep the step 1 JSON. " +
    "Step 3: send the 'message' field of the final JSON, unchanged, with the KakaotalkChat-MemoChat tool. " +
    "Do nothing else. If route.py fails, print the error text in one line and do not send anything."

"[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $From -> $To" | Add-Content -Encoding UTF8 $log
claude -p $prompt --model haiku `
    --allowedTools "Bash(python -I *route.py*)" "mcp__claude_ai_PlayMCP__KakaotalkChat-MemoChat" "mcp__claude_ai_PlayMCP__koreaBus-getKoreaBusArrival" 2>&1 |
    Add-Content -Encoding UTF8 $log
"exit=$LASTEXITCODE" | Add-Content -Encoding UTF8 $log
exit $LASTEXITCODE
