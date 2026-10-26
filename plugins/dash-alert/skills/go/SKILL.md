---
name: go
description: 도착 장소와 도착 시각(또는 출발 시각)을 받아 대중교통(버스·지하철) 경로와 출발 시각을 계산하고, 지금 뛰어야 하는지 터미널에 보여 준 뒤 카카오톡 나와의 채팅으로 보낸다. 사용자가 "강남역 9시까지 가야 해", "회사 8시 반 도착", "몇 시에 출발해야 해", "/go" 라고 할 때 사용한다.
---

# go

## 입력 해석

- 도착 장소: 저장된 이름(`config.json` 의 `places`, 예: 회사)이나 검색어(예: 강남역).
- 도착 시각: `HH:MM` 으로 바꾼다. "9시" → 09:00, "오후 6시 반" → 18:30. 모호하면(오전/오후) 되묻는다. 도착 시각이 없으면(예약 알림 등) `--arrive` 를 생략해 "지금 출발" 기준으로 조회한다.
- 출발 시각: "18시에 출발해", "7시 반 출발" 처럼 출발 시각을 말하면 `HH:MM` 으로 바꿔 `--depart` 로 넘긴다. `--arrive` 와 함께 쓰지 않는다. 지난 시각이면 내일로 계산된다.
- 출발지: 말하지 않으면 `config.json` 의 `home`. 사용자가 다른 출발지를 말하면 `--from` 으로 넘긴다.

## 절차

1. Bash 로 경로 스크립트를 실행한다.
   ```
   python -I "${CLAUDE_PLUGIN_ROOT}/scripts/route.py" --to "<도착 장소>" [--arrive "<HH:MM>" | --depart "<HH:MM>"] [--from "<출발지>"]
   ```
   - 설정: `~/.commute-alert/config.json`, 키: `~/.commute-alert/.env` (`ODSAY_API_KEY`, `KAKAO_REST_API_KEY`)
   - 종료 코드가 0 이 아니면 stderr 원문을 그대로 알리고 멈춘다. 파일·키가 없으면 플러그인의 `config.example.json`, `.env.example` 을 복사해 채우라고 안내한다.
2. `--arrive`, `--depart` 없이 "지금 출발" 기준으로 실행했고 출력 JSON 에 `first_bus` 가 있으면, 첫 버스의 실시간 도착을 반영한다.
   - PlayMCP 의 `koreaBus-getKoreaBusArrival` 도구를 `stopName=first_bus.stop`, `busNumber=first_bus.number` 로 호출한다 (방향·정류장 번호는 추측해서 넣지 않는다).
   - 후보(`candidates`)가 정확히 하나이고 `arrivals` 가 있으면 `firstArrivalSeconds`, `secondArrivalSeconds`(null 제외)를 모아 같은 명령에 `--bus-sec "<첫째>,<둘째>"` 를 붙여 다시 실행하고 그 결과를 쓴다.
   - 조회 실패, 후보 없음, 후보가 여럿이면 실시간 반영 없이 1단계 결과를 그대로 쓴다. 이 경우 시각은 모두 예상(`~`)이다.
3. 출력 JSON 의 `message` 를 터미널에 그대로 보여 주고, PlayMCP 의 `KakaotalkChat-MemoChat` 도구로 그대로 보낸다. 이 도구는 200자 제한이고, 스크립트가 이미 200자 안으로 맞춰 둔다.
   - 도구가 연결돼 있지 않으면 전송하지 못했다고 보고한다.
4. `verdict` 가 `wait` 이고 사용자가 원하면, `depart_at` 5분 전에 같은 명령을 다시 실행해 알림을 보내는 스케줄 등록을 제안한다. 먼저 등록하지 않는다.

## 주의

- 길찾기 API 는 "지금 기준" 경로만 주므로 열차·버스 시각은 소요시간으로 계산한 예상값이다. 시각 앞의 `~` 가 예상 표시이고, `~` 없는 시각만 실시간 값이다 (현재는 첫 버스만 가능). 지하철 시각은 항상 예상이다.
- 출발이 임박하면 다시 조회하라고 안내한다.
- 응답이 비정상이면 추측하지 말고 원문 오류를 보고한다.
