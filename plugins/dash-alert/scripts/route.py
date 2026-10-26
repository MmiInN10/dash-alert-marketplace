"""출발지→도착지 대중교통 경로를 찾아 도착 시각에 맞는 출발 시각을 계산하고 알림 문구를 만든다.

경로: ODsay 길찾기 (시간 지정 파라미터가 없어 '지금 기준' 경로), 장소 → 좌표: 카카오 로컬 키워드 검색.
설정은 ~/.commute-alert/config.json 의 places / home, 키는 ~/.commute-alert/.env.
"""
import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from arrivals import KST, http_get, load_env, require_key  # noqa: E402

KAKAO_ADDRESS_URL = "https://dapi.kakao.com/v2/local/search/address.json"
KAKAO_KEYWORD_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"
ODSAY_URL = "https://api.odsay.com/v1/api/searchPubTransPathT"
MEMO_LIMIT = 200


def _kakao_docs(url, query, key):
    req = urllib.request.Request(url + "?" + urllib.parse.urlencode({"query": query, "size": 1}),
                                 headers={"Authorization": "KakaoAK " + key})
    with urllib.request.urlopen(req, timeout=10) as res:
        return json.loads(res.read().decode("utf-8")).get("documents") or []


def geocode(query, places):
    """저장된 장소(x, y)가 있으면 그것을, 없으면 카카오 주소 검색 → 키워드 검색 순으로 첫 결과를 쓴다.

    주소를 키워드로만 찾으면 같은 건물의 입점 업체가 먼저 잡혀 이름이 어긋나므로 주소 검색을 먼저 한다.
    저장된 장소는 가게 이름 대신 저장 이름(집, 회사)을 그대로 돌려준다.
    """
    p = places.get(query)
    if p and "x" in p and "y" in p:
        return float(p["x"]), float(p["y"]), query
    key = require_key("KAKAO_REST_API_KEY")
    q = (p or {}).get("query", query)
    docs = _kakao_docs(KAKAO_ADDRESS_URL, q, key)
    if docs:
        return float(docs[0]["x"]), float(docs[0]["y"]), query
    docs = _kakao_docs(KAKAO_KEYWORD_URL, q, key)
    if not docs:
        raise RuntimeError("장소를 찾지 못했습니다: %s" % query)
    return float(docs[0]["x"]), float(docs[0]["y"]), query if p else docs[0].get("place_name", query)


def odsay_path(sx, sy, ex, ey):
    key = urllib.parse.unquote(require_key("ODSAY_API_KEY"))
    qs = urllib.parse.urlencode({"apiKey": key, "SX": sx, "SY": sy, "EX": ex, "EY": ey})
    data = json.loads(http_get(ODSAY_URL + "?" + qs).decode("utf-8"))
    if "error" in data:
        raise RuntimeError("ODsay 오류: %s" % data["error"])
    paths = (data.get("result") or {}).get("path") or []
    if not paths:
        raise RuntimeError("경로 없음")
    return paths[0]  # 추천 경로


def summarize(path):
    """ODsay path 를 단계 목록과 총 소요(분)로 줄인다."""
    steps = []
    for sp in path.get("subPath") or []:
        t = sp.get("trafficType")
        mins = int(sp.get("sectionTime") or 0)
        lane = (sp.get("lane") or [{}])[0]
        if t == 3:
            if mins:
                steps.append({"kind": "walk", "minutes": mins})
        elif t == 2:
            steps.append({"kind": "bus", "name": "%s번 버스" % lane.get("busNo", "?"), "number": lane.get("busNo"),
                          "from": sp.get("startName"), "to": sp.get("endName"), "minutes": mins})
        elif t == 1:
            steps.append({"kind": "subway", "name": lane.get("name", "지하철"),
                          "from": sp.get("startName"), "to": sp.get("endName"), "minutes": mins})
    return {"total_min": int((path.get("info") or {}).get("totalTime") or 0), "steps": steps}


def first_walk_min(steps):
    """첫 탑승 지점까지 걸어가는 시간. 첫 구간이 도보가 아니면 0."""
    return steps[0]["minutes"] if steps and steps[0]["kind"] == "walk" else 0


def fmt_minutes(m):
    h, mm = divmod(m, 60)
    if not h:
        return "%d분" % mm
    return "%d시간 %d분" % (h, mm) if mm else "%d시간" % h


def short_name(step):
    return step["name"].replace("수도권 ", "").replace(" 버스", "")


def first_transit_index(steps):
    for i, s in enumerate(steps):
        if s["kind"] != "walk":
            return i
    return None


def first_bus(steps):
    """경로의 첫 탑승 수단이 버스면 그 구간, 아니면 None."""
    i = first_transit_index(steps)
    return steps[i] if i is not None and steps[i]["kind"] == "bus" else None


def route_line(steps, total_min, start, real_first=None):
    """탑승 시각을 보여 준다. 앞에 ~ 가 붙은 건 출발 시각에 앞 구간 소요를 더한 예상값이고,
    real_first 로 받은 첫 버스 시각은 실시간 값이라 ~ 가 없다."""
    icons = {"bus": "🚌", "subway": "🚇"}
    first = first_transit_index(steps)
    parts = []
    elapsed = 0
    for i, s in enumerate(steps):
        if s["kind"] != "walk":
            if real_first is not None and i == first:
                at = real_first.strftime("%H:%M")
            else:
                at = "~" + (start + timedelta(minutes=elapsed)).strftime("%H:%M")
            parts.append("%s%s %s" % (icons[s["kind"]], short_name(s), at))
        elapsed += s["minutes"]
    return " → ".join(parts) or "🚶 도보 %d분" % total_min


def plan_message(dest, arrive_at, now, total_min, steps, buffer_min=5, bus_arrivals=None, bus_buffer_min=1):
    """알림 문구를 만든다. arrive_at 이 있으면 도착 시각을 역산해 run/soon/wait, 없으면 지금 출발(go).

    arrive_at 이 없고 첫 탑승 수단이 버스일 때 bus_arrivals(실시간 도착까지 남은 초 목록)를 주면,
    승차 지점까지 걷는 시간으로 탈 수 있는 첫 버스를 골라 run(뛰어라)/wait/miss 로 판정한다.
    카톡 메모챗이 200자 제한이라 문구 전체를 그 안에 맞춘다.
    """
    real_first = None
    if arrive_at is None:
        depart_at = now
        verdict, head = "go", "[뛰어라] 지금 출발하기"
        if bus_arrivals and first_bus(steps):
            walk_sec = first_walk_min(steps) * 60
            catchable = [a for a in sorted(bus_arrivals) if a >= walk_sec]
            if not catchable:
                verdict, head = "miss", "[뛰어라] 첫 버스를 놓쳤어요 😭"
            else:
                slack = catchable[0] - walk_sec
                real_first = now + timedelta(seconds=catchable[0])
                depart_at = now + timedelta(seconds=slack)
                if slack <= bus_buffer_min * 60:
                    verdict, head = "run", "[뛰어라] 지금 바로 출발하기!! 🏃💨"
                else:
                    verdict, head = "wait", "[뛰어라] %s 뒤 출발하기" % fmt_minutes(max(1, slack // 60))
    else:
        depart_at = arrive_at - timedelta(minutes=total_min + buffer_min)
        slack = int((depart_at - now).total_seconds() // 60)
        if slack <= 0:
            depart_at = now  # 이미 지난 출발 시각으로 도착·탑승 시각을 계산하지 않는다
            verdict, head = "run", "[뛰어라] 지금 바로 출발하기!! 🏃💨"
        elif slack <= 3:
            verdict, head = "soon", "[뛰어라] %d분 뒤 출발하기" % slack
        else:
            verdict, head = "wait", "[뛰어라] %s 뒤(%s) 출발하기" % (fmt_minutes(slack), depart_at.strftime("%H:%M"))
    eta = depart_at + timedelta(minutes=total_min)
    goal = "📍 %s [%s 도착]" % (dest, eta.strftime("%H:%M"))
    if arrive_at is not None and eta > arrive_at:
        goal += " ⚠%d분 늦음" % int((eta - arrive_at).total_seconds() // 60)
    walk = first_walk_min(steps)
    tail = "⏱ 총 %d분" % total_min
    if walk:
        tail = "🚶 승차지점까지 %d분 · %s" % (walk, tail)
    text = "\n".join([head, goal, route_line(steps, total_min, depart_at, real_first), tail])
    if len(text) > MEMO_LIMIT:
        text = text[:MEMO_LIMIT - 1] + "…"
    return {"verdict": verdict, "depart_at": depart_at.strftime("%H:%M"), "message": text}


def expected_bus_matches(fb, expect):
    """--expect-bus '번호|정류장' 이 지금 경로의 첫 버스와 같은지. 지정하지 않으면 항상 True."""
    if not expect:
        return True
    return bool(fb) and "%s|%s" % (fb.get("number"), fb.get("from")) == expect


def parse_arrive(hhmm, now):
    t = datetime.strptime(hhmm, "%H:%M")
    at = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
    return at if at > now else at + timedelta(days=1)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--to", required=True, help="도착 장소 (저장된 이름 또는 검색어)")
    ap.add_argument("--arrive", help="도착 목표 시각 HH:MM (없으면 지금 출발 기준)")
    ap.add_argument("--from", dest="origin", help="출발 장소 (기본: config 의 home)")
    ap.add_argument("--bus-sec", help="첫 버스의 실시간 도착까지 남은 초, 쉼표로 구분 (예: 120,1440)")
    ap.add_argument("--expect-bus", help="실시간 값을 조회한 버스 '번호|정류장'. 지금 경로의 첫 버스와 다르면 실시간 값을 쓰지 않음")
    ap.add_argument("--home", default=os.environ.get("COMMUTE_ALERT_HOME") or str(Path.home() / ".commute-alert"))
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    home = Path(args.home)
    load_env(home / ".env")
    cfg_path = home / "config.json"
    if not cfg_path.exists():
        print("설정 파일이 없습니다: %s" % cfg_path, file=sys.stderr)
        return 2
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    places = cfg.get("places", {})
    origin = args.origin or cfg.get("home")
    if not origin:
        print("출발지가 없습니다. --from 을 주거나 config.json 에 home 을 설정하세요.", file=sys.stderr)
        return 2

    try:
        sx, sy, _ = geocode(origin, places)
        ex, ey, dest_name = geocode(args.to, places)
        info = summarize(odsay_path(sx, sy, ex, ey))
    except Exception as e:  # 네트워크·API 응답 경계의 오류는 원문 그대로 보고
        print("조회 실패: %s" % e, file=sys.stderr)
        return 1

    now = datetime.now(KST)
    arrive_at = parse_arrive(args.arrive, now) if args.arrive else None
    try:
        bus_arrivals = [int(x) for x in args.bus_sec.split(",") if x.strip()] if args.bus_sec else None
    except ValueError:
        print("--bus-sec 는 쉼표로 구분한 정수여야 합니다: %s" % args.bus_sec, file=sys.stderr)
        return 2
    fb = first_bus(info["steps"])
    realtime_ignored = bool(bus_arrivals) and not expected_bus_matches(fb, args.expect_bus)
    if realtime_ignored:
        bus_arrivals = None  # 재조회로 경로가 달라졌으면 앞서 조회한 버스의 실시간 값을 다른 버스에 적용하지 않는다
    out = plan_message(dest_name, arrive_at, now, info["total_min"], info["steps"],
                       cfg.get("route_buffer_min", 5), bus_arrivals, cfg.get("buffer_min", 1))
    if realtime_ignored:
        out["realtime_ignored"] = True
    if fb and arrive_at is None:
        out["first_bus"] = {"number": fb["number"], "stop": fb["from"]}  # 실시간 조회가 필요한 첫 버스
    out["steps"] = info["steps"]
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
