# 작업 스케줄러가 호출한다: route.py 로 경로를 조회하고 카톡 나와의 채팅으로 보낸다.
# 사용: pwsh -NoProfile -File run_alert.ps1 -From 집 -To 회사 [-DryRun]
#
# 경로 조회·판정·문구 생성은 이 스크립트가 직접 하고, claude -p 는 MCP 도구 호출(버스 조회, 카톡 전송)에만 쓴다.
# claude -p 는 도구가 실패해도 종료 코드 0 으로 끝나므로, 성공 여부는 문장이 아니라 도구 호출 기록으로 확인한다.
# 실패하면 로그에 남기고, 카톡으로 실패를 알리고(그것도 안 되면 윈도우 알림), 종료 코드 1 로 끝낸다.
param(
    [Parameter(Mandatory)][string]$From,
    [Parameter(Mandatory)][string]$To,
    [switch]$DryRun  # 카톡 전송과 실패 알림을 하지 않고 문구만 출력
)
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
Set-Location $PSScriptRoot  # 스케줄러 기본 작업 디렉터리는 System32

$Model = "claude-haiku-5-5"
$KakaoTool = "mcp__claude_ai_PlayMCP__KakaotalkChat-MemoChat"
$BusTool = "mcp__claude_ai_PlayMCP__koreaBus-getKoreaBusArrival"
$ClaudeTimeoutSec = 150

$routePy = Join-Path $PSScriptRoot "route.py"
$logDir = Join-Path $env:USERPROFILE ".commute-alert\logs"
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir "alert.log"
if ((Test-Path $log) -and (Get-Item $log).Length -gt 512KB) { Move-Item -Force $log "$log.1" }

function Write-Log([string]$Text) {
    "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Text | Add-Content -Encoding UTF8 $log
}

function Invoke-Claude([string]$Prompt, [string]$Tool) {
    $job = Start-Job -ScriptBlock {
        param($p, $m, $t)
        [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
        claude -p $p --model $m --output-format stream-json --verbose --max-budget-usd 0.5 --allowedTools $t 2>&1
    } -ArgumentList $Prompt, $Model, $Tool
    if (-not (Wait-Job $job -Timeout $ClaudeTimeoutSec)) {
        Stop-Job $job
        Remove-Job $job -Force
        throw "claude 시간 초과 ($ClaudeTimeoutSec 초)"
    }
    $lines = Receive-Job $job
    Remove-Job $job
    $events = @()
    $other = @()
    foreach ($l in $lines) {
        try { $events += ("$l" | ConvertFrom-Json -Depth 20) } catch { $other += "$l" }
    }
    $result = $events | Where-Object { $_.type -eq "result" } | Select-Object -Last 1
    if (-not $result) { throw "claude 결과 없음: $(($other -join ' ') | Select-Object -First 1)" }
    if ($result.is_error) { throw "claude 오류: $($result.result)" }
    return , $events
}

# stream-json 이벤트에서 지정한 도구의 호출 입력과 결과 텍스트를 뽑는다
function Get-ToolCalls($Events, [string]$Tool) {
    $uses = @{}
    foreach ($e in $Events) {
        if ($e.type -eq "assistant") {
            foreach ($c in $e.message.content) {
                if ($c.type -eq "tool_use" -and $c.name -eq $Tool) { $uses[$c.id] = $c.input }
            }
        }
    }
    $calls = @()
    foreach ($e in $Events) {
        if ($e.type -eq "user") {
            foreach ($c in $e.message.content) {
                if ($c.type -eq "tool_result" -and $uses.ContainsKey($c.tool_use_id)) {
                    $text = if ($c.content -is [string]) { $c.content } else { ($c.content | ForEach-Object { $_.text }) -join "" }
                    $calls += [pscustomobject]@{ Input = $uses[$c.tool_use_id]; Text = $text; IsError = [bool]$c.is_error }
                }
            }
        }
    }
    return , $calls
}

# 첫 버스의 실시간 도착(초) 목록. 조회 실패·후보 불명확이면 $null (이 경우 예상 시각으로 보낸다)
function Get-BusArrivals([string]$Number, [string]$Stop) {
    if ($Number -notmatch '^[0-9A-Za-z가-힣\-]{1,12}$' -or $Stop -notmatch '^[^\r\n"`$]{1,40}$') {
        Write-Log "버스 실시간 생략: 노선·정류장 이름이 비정상"
        return $null
    }
    try {
        $events = Invoke-Claude "Call the koreaBus-getKoreaBusArrival tool once with stopName=$Stop and busNumber=$Number. Do nothing else." $BusTool
    } catch {
        Write-Log "버스 실시간 생략: $($_.Exception.Message)"
        return $null
    }
    $call = Get-ToolCalls $events $BusTool |
        Where-Object { -not $_.IsError -and $_.Input.busNumber -eq $Number -and $_.Input.stopName -eq $Stop } |
        Select-Object -First 1
    if (-not $call) { Write-Log "버스 실시간 생략: 올바른 인자의 도구 호출 기록 없음"; return $null }
    try { $data = $call.Text | ConvertFrom-Json -Depth 20 } catch { Write-Log "버스 실시간 생략: 결과 해석 실패"; return $null }
    if (@($data.candidates).Count -ne 1) { Write-Log "버스 실시간 생략: 후보 $(@($data.candidates).Count)개"; return $null }
    $arr = @($data.candidates[0].arrivals) | Select-Object -First 1
    $ra = $data.retrievedAt
    $retrieved = if ($ra -is [datetime]) { $ra.ToUniversalTime() } else { [datetimeoffset]::Parse([string]$ra).UtcDateTime }
    $elapsed = [math]::Max(0, ((Get-Date).ToUniversalTime() - $retrieved).TotalSeconds)  # 조회 후 흐른 시간만큼 차감
    $secs = @($arr.firstArrivalSeconds, $arr.secondArrivalSeconds) |
        Where-Object { $null -ne $_ } | ForEach-Object { [int]($_ - $elapsed) } | Where-Object { $_ -gt 0 }
    if (-not $secs) { Write-Log "버스 실시간 생략: 유효한 도착 값 없음"; return $null }
    return , @($secs)
}

function ConvertTo-Normalized([string]$s) { return ($s -replace "`r`n", "`n").TrimEnd() }

# 도구 호출 입력이 원문과 같고 결과가 성공일 때만 전송 성공으로 본다
function Send-Kakao([string]$Text) {
    $prompt = "Send the exact text between the markers below with the KakaotalkChat-MemoChat tool, character for character, then stop.`n<<<BEGIN`n$Text`nEND>>>"
    for ($try = 1; $try -le 2; $try++) {
        try {
            $events = Invoke-Claude $prompt $KakaoTool
            $calls = Get-ToolCalls $events $KakaoTool
            $ok = $calls | Where-Object {
                -not $_.IsError -and $_.Text -match "성공적으로 보냈" -and
                (ConvertTo-Normalized $_.Input.message) -eq (ConvertTo-Normalized $Text)
            }
            if ($ok) { return $true }
            $first = $calls | Select-Object -First 1
            Write-Log "전송 확인 실패 (시도 $try): 호출 $(@($calls).Count)건, 결과=$($first.Text)"
        } catch {
            Write-Log "전송 오류 (시도 $try): $($_.Exception.Message)"
        }
    }
    return $false
}

function Show-Toast([string]$Title, [string]$Text) {
    try {
        Add-Type -AssemblyName System.Windows.Forms
        Add-Type -AssemblyName System.Drawing
        $n = New-Object System.Windows.Forms.NotifyIcon
        $n.Icon = [System.Drawing.SystemIcons]::Warning
        $n.Visible = $true
        $n.ShowBalloonTip(10000, $Title, $Text, [System.Windows.Forms.ToolTipIcon]::Warning)
        Start-Sleep -Seconds 11
        $n.Dispose()
    } catch {
        Write-Log "윈도우 알림 실패: $($_.Exception.Message)"
    }
}

function Report-Failure([string]$Reason) {
    Write-Log "실패: $Reason"
    if ($DryRun) { Write-Host "[DryRun] 실패 알림 생략: $Reason"; return }
    $short = if ($Reason.Length -gt 120) { $Reason.Substring(0, 120) } else { $Reason }
    $sent = $false
    try { $sent = Send-Kakao "[뛰어라] 알림 실패: $short" } catch { Write-Log "실패 알림 전송 오류: $($_.Exception.Message)" }
    if ($sent) {
        Write-Log "실패 알림 카톡 전송 확인됨"
    } else {
        Write-Log "카톡 실패 알림 불가 -> 윈도우 알림 표시"
        Show-Toast "dash-alert 알림 실패" $short
    }
}

function Invoke-Route([string[]]$ExtraArgs) {
    $py = (Get-Command python -ErrorAction Stop).Source
    $errFile = Join-Path $logDir "route.err"
    $stdout = & $py -I $routePy --from $From --to $To @ExtraArgs 2>$errFile
    if ($LASTEXITCODE -ne 0) {
        $err = if (Test-Path $errFile) { (Get-Content -Raw -Encoding UTF8 $errFile).Trim() } else { "" }
        throw "route.py 실패 (exit $LASTEXITCODE): $err"
    }
    return ($stdout -join "`n") | ConvertFrom-Json
}

try {
    Write-Log "시작 $From -> $To$(if ($DryRun) { ' (DryRun)' })"
    $route = Invoke-Route @()

    if ($route.first_bus) {
        $number = [string]$route.first_bus.number
        $stop = [string]$route.first_bus.stop
        $secs = Get-BusArrivals $number $stop
        if ($secs) {
            $second = Invoke-Route @("--bus-sec", ($secs -join ","), "--expect-bus", "$number|$stop")
            if ($second.realtime_ignored) { Write-Log "실시간 값 폐기: 재조회 경로의 첫 버스가 달라짐" }
            $route = $second
        }
    }

    $message = [string]$route.message
    Write-Log ("verdict={0} message={1}" -f $route.verdict, ($message -replace "`n", " / "))
    if ($DryRun) { Write-Host $message; exit 0 }
    if (-not (Send-Kakao $message)) { throw "카톡 전송을 확인하지 못함" }
    Write-Log "전송 확인됨"
    exit 0
} catch {
    Report-Failure $_.Exception.Message
    exit 1
}
