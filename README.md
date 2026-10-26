# dash-alert

서울·경기 대중교통 경로를 조회해 "언제 출발해야 하는지"를 카카오톡 나와의 채팅으로 알려 주는 Claude Code 플러그인이다.

## 1. 목적

| 항목 | 내용 |
|------|------|
| 문제 | 버스·지하철 시간을 앱으로 매번 확인하지 않으면 출발 타이밍을 놓침 |
| 해결 | 출발지→도착지 경로와 소요시간을 계산하고, 첫 버스의 실시간 도착으로 "지금 뛰어야 하는지" 판정 |
| 전달 | 터미널 출력 + 카카오톡 나와의 채팅(PlayMCP 메모챗) |
| 자동화 | 평일 06:40(집→회사), 18:00(회사→집)에 자동 알림 (Windows 작업 스케줄러) |
| 범위 | 서울·경기 (경로는 ODsay, 버스 실시간은 서울·인천·경기) |

## 2. 구성 요소

| 구분 | 이름 | 역할 |
|------|------|------|
| 스킬 | `go` | 도착 장소·시각을 받아 경로 조회 → 터미널 출력 → 카톡 전송 |
| 스킬 | `run-now` | 고정 정류장·역의 실시간 도착 조회 (보조 기능, 키 필요) |
| 스크립트 | `scripts/route.py` | 장소→좌표 변환, 경로 조회, 출발 시각 계산, 알림 문구 생성 |
| 스크립트 | `scripts/arrivals.py` | 고정 정류장·역 도착 조회와 판정 (`run-now`용) |
| 스크립트 | `scripts/run_alert.ps1` | 스케줄러가 호출. `claude -p`로 조회·실시간 보정·전송을 수행 |
| 훅·에이전트 | 없음 | 현재 기능에 필요하지 않아 만들지 않음 |

## 3. 사용한 API

| 용도 | API | 키 |
|------|-----|-----|
| 대중교통 경로 | ODsay 길찾기 `searchPubTransPathT` | `ODSAY_API_KEY` (서버 플랫폼, 호출 PC의 공인 IP 등록 필요) |
| 주소→좌표 | 카카오 로컬 주소 검색 `search/address.json` | `KAKAO_REST_API_KEY` (앱에서 카카오맵 서비스 사용 설정 필요) |
| 장소명→좌표 | 카카오 로컬 키워드 검색 `search/keyword.json` | 위와 동일 (주소 검색 실패 시에만 사용) |
| 카톡 전송 | PlayMCP `KakaotalkChat-MemoChat` (최대 200자) | claude.ai에 연결한 PlayMCP. 플러그인에 포함하지 않음 |
| 첫 버스 실시간 도착 | PlayMCP `koreaBus-getKoreaBusArrival` | claude.ai에 연결한 PlayMCP |
| (선택) 서울 지하철 실시간 | 서울 열린데이터광장 `realtimeStationArrival` | `SEOUL_OPENAPI_KEY` |
| (선택) 서울·경기 버스 도착 | 공공데이터포털 서울·경기 버스도착정보 | `DATA_GO_KR_KEY` |

- `run-now`의 선택 API 3종은 키가 없어 실제 호출을 검증하지 못했다.
- 지하철 실제 배차 시각(국가철도공단 열차별 운행 시각표)은 연동하지 않았다. 승인 대기 중이며 11절 참고.

## 4. 장소(주소) 입력 방법

설정 파일 `~/.commute-alert/config.json`의 `places`에 자주 쓰는 장소를 이름으로 등록한다.

```json
{
  "home": "집",
  "places": {
    "집":   { "query": "경기 평택시 ○○로 123" },
    "회사": { "query": "경기도 부천시 ○○로 7" }
  },
  "route_buffer_min": 5,
  "buffer_min": 1
}
```

| 입력 | 처리 |
|------|------|
| `query`에 도로명·지번 주소 | 카카오 주소 검색 우선. 같은 건물의 가게 이름이 아니라 저장 이름("회사")으로 표시 |
| `query`에 역·건물 이름 | 주소 검색 실패 시 키워드 검색 첫 결과 사용 |
| `x`(경도), `y`(위도) 직접 지정 | API 호출 없이 그대로 사용 |
| `places`에 없는 이름 | 검색어로 간주해 즉석 검색. 표시 이름은 검색된 장소명 |

- `home`: 출발지를 생략했을 때의 기본 출발지.
- `route_buffer_min`: 도착 목표 시각 방식에서 도착 시각에 더하는 여유(분).
- `buffer_min`: 첫 버스를 탈 때 "뛰어라"로 판정하는 여유(분).

### 호출 방법

| 방법 | 예시 |
|------|------|
| 스킬 (자연어) | `/dash-alert:go 강남역 9시까지 가야 해` |
| 직접 실행, 지금 출발 기준 | `python -I scripts/route.py --from 집 --to 회사` |
| 직접 실행, 도착 목표 시각 | `python -I scripts/route.py --from 집 --to 회사 --arrive 09:00` |

## 5. 시간 표시 방식

```
[뛰어라] 30분 뒤 출발하기
📍 회사 [17:11 도착]
🚌1312번 15:17 → 🚇1호선 ~15:30 → 🚇1호선 ~16:48
🚶 승차지점까지 1분 · ⏱ 총 115분
```

| 표시 | 의미 |
|------|------|
| 첫 줄 `[뛰어라] …` | 출발 판정 (아래 표) |
| `📍 장소 [HH:MM 도착]` | 출발 시각 + 총 소요시간으로 계산한 도착 예정 시각 |
| 시각 앞 `~` | 예상값. 출발 시각에 앞 구간 소요시간을 더해 계산 |
| 시각 앞 `~` 없음 | 실시간 값. 현재는 **첫 버스만** 가능 |
| `🚶 승차지점까지 N분` | 첫 탑승 지점까지의 도보 시간 |
| `⏱ 총 N분` | 경로 전체 소요시간 |

| 판정 | 첫 줄 | 조건 (지금 출발 기준) |
|------|-------|----------------------|
| go | `[뛰어라] 지금 출발하기` | 첫 수단이 지하철이거나 버스 실시간 값이 없음 |
| wait | `[뛰어라] N분 뒤 출발하기` | 첫 버스 도착 − 도보 시간이 `buffer_min`보다 큼 |
| run | `[뛰어라] 지금 바로 출발하기!! 🏃💨` | 첫 버스 도착 − 도보 시간이 `buffer_min` 이하 |
| miss | `[뛰어라] 첫 버스를 놓쳤어요 😭` | 걸어서 탈 수 있는 첫 버스가 없음 |

- 문구는 카톡 메모챗 제한에 맞춰 200자를 넘으면 잘린다.
- ODsay는 "지금 기준" 경로만 제공한다. 지하철 시각은 항상 예상값이다.

## 6. 자동 알림 (스케줄)

| 작업 이름 | 시각 | 경로 |
|-----------|------|------|
| `dash-alert-morning` | 평일 06:40 | 집 → 회사 |
| `dash-alert-evening` | 평일 18:00 | 회사 → 집 |

- 실행: `pwsh -NoProfile -File scripts/run_alert.ps1 -From 집 -To 회사`
- 흐름: 경로 조회 → 첫 버스 실시간 조회 → 문구 재생성 → 메모챗 전송 (`claude -p --model haiku`)
- 로그: `~/.commute-alert/logs/alert.log`
- 삭제: `Unregister-ScheduledTask -TaskName dash-alert-morning -Confirm:$false`

## 7. 설치

Claude Code에서 아래 두 줄을 차례로 입력한다.

```
/plugin marketplace add MmiInN10/dash-alert-marketplace
/plugin install dash-alert@dash-alert-marketplace
```

설치 후 `/reload-plugins`를 입력하거나 새 세션을 시작한다. 로컬 폴더로 설치하려면 `add`의 인자를 폴더 경로로 바꾼다.

설정 파일 준비:

| 파일 | 내용 |
|------|------|
| `~/.commute-alert/.env` | `ODSAY_API_KEY`, `KAKAO_REST_API_KEY` (선택: `SEOUL_OPENAPI_KEY`, `DATA_GO_KR_KEY`). 템플릿은 `plugins/dash-alert/.env.example` |
| `~/.commute-alert/config.json` | 4절 참고. 템플릿은 `plugins/dash-alert/config.example.json` |

- 요구 사항: Windows, Python 3.8 이상, PowerShell 7(`pwsh`), Claude Code, claude.ai에 연결된 PlayMCP(카카오톡 메모챗, 버스 도착).
- 키는 저장소에 넣지 않는다. 설정 폴더는 프로젝트 밖(홈 디렉터리)에 있다.

## 8. 한계

| 항목 | 내용 |
|------|------|
| 지하철 시각 | 예상값만 제공. 서울 지하철 실시간 API는 서울 구간만 지원 |
| 버스 실시간 | 경로의 첫 구간이 버스일 때만 반영. 정류장 이름이 겹쳐 후보가 여럿이면 예상값 사용 |
| 조회 실패 | 오류 시 카톡을 보내지 않고 로그에만 기록 |
| 공휴일 | 구분하지 않음 (평일 공휴일에도 알림) |
| 실행 조건 | PC가 켜져 있고 로그인된 상태여야 함. 놓친 실행은 나중에 보충하지 않음 |
| 폴더 이동 | 프로젝트 폴더를 옮기면 스케줄 작업을 다시 등록해야 함 |
| ODsay | 서버 키는 IP 등록 필요. 공인 IP가 바뀌면 호출이 막힘 |

## 9. 테스트

```
cd plugins/dash-alert
python -I -m unittest discover -s tests
```

- 판정 경계값, 문구 형식, 200자 제한, 장소 검색 순서, 실시간 버스 판정 등 단위 테스트 30건.
- 실제 API 호출은 단위 테스트에 포함되지 않는다.

## 10. 파일 구성

```
.claude-plugin/marketplace.json
plugins/dash-alert/
├── .claude-plugin/plugin.json
├── .env.example
├── config.example.json
├── scripts/
│   ├── route.py
│   ├── arrivals.py
│   └── run_alert.ps1
├── skills/
│   ├── go/SKILL.md
│   └── run-now/SKILL.md
└── tests/
    ├── test_route.py
    └── test_arrivals.py
```

## 11. 추후 작업 (철도 데이터 포털 승인 후 추가 예정)

현재 버전에는 포함되지 않은 기능이다. API 승인이 나는 대로 추가한다.

| 항목 | 내용 |
|------|------|
| 대상 | 수도권 1호선 등 지하철의 실제 배차 시각 (현재는 `~` 예상값) |
| 데이터 | 국가철도공단 열차별 운행 시각표 OpenAPI (철도 데이터 포털) |
| 상태 | 활용신청 완료, 승인 대기 (통상 2~3일 소요) |
| 키 | `~/.commute-alert/.env`의 `KRIC_API_KEY` |
| 현재 대체 | 지하철 시각은 소요시간 기반 예상값으로 표시 |

### 추가할 동작 (설계안)

| 항목 | 내용 |
|------|------|
| 열차 선택 | 급행·일반 구분 없이 출발역과 도착역에 모두 정차하고, 탈 수 있는 열차 중 도착이 가장 빠른 열차를 선택 |
| 표시 | 급행이면 `🚇1호선(급행) 06:55`처럼 표기. 일반은 표기 없음 |
| 요일 | 평일·토요일·공휴일 시각표를 오늘 요일에 맞게 선택. 공휴일 판정 함께 추가 |
| 환승 | 환승역 대기 시간도 시각표 기준으로 재계산 |

- 시각표의 열차 종류 항목, 역 코드 체계, 요청 변수는 승인 후 API 명세로 확인한 뒤 확정한다.
- 승인 전까지 5절의 `~` 표시는 예상값 그대로 유지된다.
