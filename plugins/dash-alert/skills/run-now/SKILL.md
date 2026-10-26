---
name: run-now
description: 서울·경기 지하철/버스 실시간 도착정보를 조회해 지금 뛰어야 하는지 판정하고 카카오톡 나와의 채팅으로 알린다. 사용자가 "지금 뛰어야 해?", "버스 언제 와", "출근 알림", "/run-now" 라고 하거나 스케줄로 반복 실행될 때 사용한다.
---

# run-now

## 절차

1. Bash 로 조회 스크립트를 실행한다.
   ```
   python -I "${CLAUDE_PLUGIN_ROOT}/scripts/arrivals.py"
   ```
   - 설정: `~/.commute-alert/config.json`, 키: `~/.commute-alert/.env`
   - 파일이 없으면 플러그인의 `config.example.json`, `.env.example` 을 복사해 채우라고 안내하고 멈춘다.
2. 출력 JSON 의 `summary` 를 사용자에게 보여 준다. `verdict` 가 `error` 인 대상은 오류 내용을 그대로 알린다.
3. PlayMCP 의 `KakaotalkChat-MemoChat` 도구로 `summary` 를 나와의 채팅에 보낸다.
   - 사용자가 직접 호출했으면 항상 보낸다.
   - 스케줄로 실행됐으면 `notify` 가 `true` 일 때만 보낸다 (반복 알림 스팸 방지).
   - 도구가 연결돼 있지 않으면 전송하지 못했다고 보고한다.

## 주의

- 서울 실시간 지하철 API 는 하루 1,000건 제한이 있다. 스케줄 간격은 1분보다 길게 잡는다.
- 응답이 없거나 값이 이상하면 추측하지 말고 원문 오류를 보고한다.
