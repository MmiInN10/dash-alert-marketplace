"""지하철·버스 도착 예정 시간을 조회하고 '지금 뛰어야 하는지' 판정해 JSON 으로 출력한다.

설정은 ~/.commute-alert/config.json, API 키는 ~/.commute-alert/.env (--home 으로 변경).
"""
import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
TIMEOUT = 10

SUBWAY_URL = "http://swopenapi.seoul.go.kr/api/subway/{key}/json/realtimeStationArrival/0/30/{station}"
SEOUL_BUS_URL = "http://ws.bus.go.kr/api/rest/arrive/getArrInfoByRouteAll"
GG_BUS_URL = "https://apis.data.go.kr/6410000/busarrivalservice/v2/getBusArrivalItemv2"


def load_env(path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def require_key(name):
    val = os.environ.get(name, "")
    if not val:
        raise RuntimeError("환경변수 %s 가 비어 있습니다 (.env 확인)" % name)
    return val


def http_get(url):
    with urllib.request.urlopen(url, timeout=TIMEOUT) as res:
        return res.read()


def msg_to_sec(msg):
    """'3분20초후', '곧 도착' 같은 도착 메시지를 초로 바꾼다. 알 수 없으면 None."""
    if not msg:
        return None
    if "곧 도착" in msg:
        return 0
    m = re.search(r"(\d+)분", msg)
    s = re.search(r"(\d+)초", msg)
    if not m and not s:
        return None
    return (int(m.group(1)) * 60 if m else 0) + (int(s.group(1)) if s else 0)


def find_key(obj, key):
    """중첩된 dict/list 에서 key 의 첫 값을 찾는다 (응답 구조 변화에 둔감하게)."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            r = find_key(v, key)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = find_key(v, key)
            if r is not None:
                return r
    return None


def fetch_subway(t):
    key = require_key("SEOUL_OPENAPI_KEY")
    url = SUBWAY_URL.format(key=key, station=urllib.parse.quote(t["station"]))
    data = json.loads(http_get(url).decode("utf-8"))
    code = (data.get("errorMessage") or {}).get("code") or data.get("code")
    if code not in (None, "INFO-000", "INFO-200"):
        raise RuntimeError("지하철 API 오류 %s: %s" % (code, data.get("message")))
    now = datetime.now(KST)
    out = []
    for r in data.get("realtimeArrivalList") or []:
        if t.get("subway_id") and r.get("subwayId") != t["subway_id"]:
            continue
        d = t.get("direction")
        if d and d != r.get("updnLine") and d not in (r.get("trainLineNm") or ""):
            continue
        sec = int(r.get("barvlDt") or 0)
        if sec <= 0:
            sec = msg_to_sec(r.get("arvlMsg2"))
            if sec is None:
                continue
        # 응답 생성 시각 이후 흐른 시간만큼 보정
        rec = r.get("recptnDt")
        if rec:
            try:
                made = datetime.strptime(rec[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
                sec -= max(0, int((now - made).total_seconds()))
            except ValueError:
                pass
        out.append(max(sec, 0))
    return sorted(out)


def fetch_seoul_bus(t):
    key = urllib.parse.unquote(require_key("DATA_GO_KR_KEY"))
    qs = urllib.parse.urlencode({"serviceKey": key, "busRouteId": t["route_id"]})
    root = ET.fromstring(http_get(SEOUL_BUS_URL + "?" + qs))
    head = root.findtext(".//headerCd")
    if head not in ("0", "4"):
        raise RuntimeError("서울 버스 API 오류 %s: %s" % (head, root.findtext(".//headerMsg")))
    out = []
    for item in root.iter("itemList"):
        if item.findtext("stId") != t["st_id"]:
            continue
        for n in ("1", "2"):
            msg = item.findtext("arrmsg" + n)
            sec = int(item.findtext("exps" + n) or 0)
            if sec <= 0:
                sec = msg_to_sec(msg)
            if sec is not None:
                out.append(sec)
    return sorted(out)


def fetch_gyeonggi_bus(t):
    key = urllib.parse.unquote(require_key("DATA_GO_KR_KEY"))
    qs = urllib.parse.urlencode({
        "serviceKey": key,
        "stationId": t["station_id"],
        "routeId": t["route_id"],
        "staOrder": t["sta_order"],
        "format": "json",
    })
    data = json.loads(http_get(GG_BUS_URL + "?" + qs).decode("utf-8"))
    code = find_key(data, "resultCode")
    if code is not None and str(code) not in ("0", "4"):
        raise RuntimeError("경기 버스 API 오류 %s: %s" % (code, find_key(data, "resultMessage")))
    item = find_key(data, "busArrivalItem")
    if isinstance(item, list):
        item = item[0] if item else None
    if not item:
        return []
    out = []
    for n in ("1", "2"):
        sec = item.get("predictTimeSec" + n)
        if sec in (None, ""):
            mins = item.get("predictTime" + n)
            sec = int(mins) * 60 if mins not in (None, "") else None
        if sec not in (None, "") and int(sec) > 0:
            out.append(int(sec))
    return sorted(out)


FETCHERS = {
    "subway": fetch_subway,
    "seoul_bus": fetch_seoul_bus,
    "gyeonggi_bus": fetch_gyeonggi_bus,
}


def fmt(sec):
    m, s = divmod(int(sec), 60)
    return "%d분 %02d초" % (m, s) if m else "%d초" % s


def decide(arrivals, walk_min, buffer_min, label):
    """도착(초) 목록에서 탈 수 있는 첫 차를 골라 run / wait / none 으로 판정한다."""
    walk = walk_min * 60
    buf = buffer_min * 60
    if not arrivals:
        return {"verdict": "none", "message": "%s: 도착 정보 없음" % label}
    for i, a in enumerate(sorted(arrivals)):
        slack = a - walk
        if slack < 0:
            continue
        missed = " (앞 차는 놓침)" if i > 0 else ""
        if slack <= buf:
            return {
                "verdict": "run",
                "message": "지금 뛰세요! %s %s 후 도착 (도보 %d분)%s" % (label, fmt(a), walk_min, missed),
            }
        return {
            "verdict": "wait",
            "message": "%s %s 후 도착, %s 뒤에 출발하세요%s" % (label, fmt(a), fmt(slack), missed),
        }
    return {"verdict": "none", "message": "%s: 지금 출발해도 탈 수 있는 차 없음 (가장 빠른 차 %s 후)" % (label, fmt(min(arrivals)))}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--home", default=os.environ.get("COMMUTE_ALERT_HOME") or str(Path.home() / ".commute-alert"))
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    home = Path(args.home)
    load_env(home / ".env")
    cfg_path = home / "config.json"
    if not cfg_path.exists():
        print("설정 파일이 없습니다: %s (config.example.json 을 복사해 채우세요)" % cfg_path, file=sys.stderr)
        return 2
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))

    results = []
    for t in cfg["targets"]:
        label = t.get("label") or t["type"]
        try:
            arrivals = FETCHERS[t["type"]](t)
            r = decide(arrivals, t.get("walk_min", cfg.get("walk_min", 5)), t.get("buffer_min", cfg.get("buffer_min", 1)), label)
            r["arrivals_sec"] = arrivals
        except Exception as e:  # 한 대상의 네트워크·응답 오류가 나머지를 막지 않게 한다
            r = {"verdict": "error", "message": "%s: 조회 실패 (%s)" % (label, e)}
        r["label"] = label
        results.append(r)

    out = {
        "notify": any(r["verdict"] == "run" for r in results),
        "summary": "\n".join(r["message"] for r in results),
        "results": results,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 1 if all(r["verdict"] == "error" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
