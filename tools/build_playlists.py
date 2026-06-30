#!/usr/bin/env python3
"""선유듀오 영상 페이지 데이터 빌드 — 재생목록 ID들을 스크랩해 playlists.json 생성.

- YouTube Data API 불필요 (공개/일부공개 페이지 비로그인 스크랩).
- 재생목록 추가 = 아래 PLAYLISTS 에 {"id": "...", "name": None} 한 줄 추가.
- 안전장치: 스크랩이 비거나 실패하면 기존 playlists.json 을 덮어쓰지 않음(원자적 교체).

사용:
    python tools/build_playlists.py            # playlists.json 재생성
    python tools/build_playlists.py --check     # 변경 여부만 출력(쓰기 안 함), 변경 시 exit 1
"""
import json
import re
import html
import time
import sys
import os
import datetime
import urllib.request

# Windows 콘솔(cp949)에서 한글/em대시 출력 시 UnicodeEncodeError 방지
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

# === 설정: 재생목록을 여기에 추가하세요 (name=None 이면 YouTube 재생목록 제목 사용) ===
PLAYLISTS = [
    {"id": "PLqmCTHxdkjRPoKzLBVgeSm3t8oyB-aE_G", "name": None},
]

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "playlists.json")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
HEADERS = {"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9", "Cookie": "CONSENT=YES+1; SOCS=CAI"}
VID_RE = re.compile(r'"videoId":"([A-Za-z0-9_-]{11})"')
OGT_RE = re.compile(r'<meta property="og:title" content="([^"]*)"')
DATE_RE = re.compile(r'"uploadDate":"([^"]+)"')
PUBDATE_RE = re.compile(r'"publishDate":"([^"]+)"')


def fetch(url, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (i + 1))
    raise last


def playlist_title(plhtml):
    m = OGT_RE.search(plhtml)
    if not m:
        return ""
    t = html.unescape(m.group(1)).strip()
    return re.sub(r"\s*-\s*YouTube$", "", t)


def playlist_video_ids(plhtml):
    ids, seen = [], set()
    for m in VID_RE.finditer(plhtml):
        v = m.group(1)
        if v not in seen:
            seen.add(v)
            ids.append(v)
    return ids


def video_meta(vid):
    # bpctr/hl/gl: consent 인터스티셜 우회 (클라우드 IP에서 watch 페이지 차단 회피)
    h = fetch("https://www.youtube.com/watch?v=" + vid + "&bpctr=9999999999&hl=ko&gl=KR")
    tm = OGT_RE.search(h)
    dm = DATE_RE.search(h) or PUBDATE_RE.search(h)  # uploadDate 우선, 없으면 publishDate
    title = html.unescape(tm.group(1)).strip() if tm else vid
    title = re.sub(r"\s*-\s*YouTube$", "", title)
    date = dm.group(1) if dm else ""
    return {"id": vid, "title": title, "date": date}


def build():
    out = []
    for pl in PLAYLISTS:
        plid = pl["id"]
        plhtml = fetch("https://www.youtube.com/playlist?list=" + plid)
        name = pl.get("name") or playlist_title(plhtml) or plid
        vids = playlist_video_ids(plhtml)
        if not vids:
            raise RuntimeError("재생목록 %s 에서 영상 ID를 못 찾음 (스크랩 구조 변경/접근 차단 의심)" % plid)
        # 50~100개 초과 시 첫 페이지만 수집됨 → continuation 토큰 있으면 경고 (조용한 누락 방지)
        if "continuationItemRenderer" in plhtml:
            print("  ! 경고: '%s' 에 추가 페이지(continuation)가 있어 일부 영상이 누락될 수 있습니다 (현재 %d개 수집)" % (plid, len(vids)), file=sys.stderr)
        videos = []
        for vid in vids:
            videos.append(video_meta(vid))
            time.sleep(0.3)
        out.append({"name": name, "id": plid, "videos": videos})
        print("[%s] %d개" % (name, len(videos)))
    return {"generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "playlists": out}


def main():
    data = build()
    total = sum(len(p["videos"]) for p in data["playlists"])
    if total == 0:
        raise RuntimeError("영상 0개 — 기존 파일 보존하고 중단")
    # 검증: 제목/날짜 추출 실패(제목=영상ID 또는 날짜 빈값) 시 중단 → 깨진 데이터 커밋 방지
    bad = [v["id"] for p in data["playlists"] for v in p["videos"] if not v["date"] or v["title"] == v["id"]]
    if bad:
        raise RuntimeError(
            "제목/날짜 추출 실패 %d건(%s) — consent 월/IP 차단 의심, 기존 파일 보존하고 중단"
            % (len(bad), ", ".join(bad[:5]))
        )
    new_json = json.dumps(data, ensure_ascii=False, indent=2)

    # generated_at(타임스탬프) 줄 제외하고 기존 파일과 비교 → 내용 동일하면 안 씀(no-op 커밋 방지)
    norm = lambda s: re.sub(r'"generated_at":\s*"[^"]*",?\n', "", s)
    old = open(OUT, encoding="utf-8").read() if os.path.exists(OUT) else ""
    changed = norm(old) != norm(new_json)

    if "--check" in sys.argv:
        print("CHANGED" if changed else "UNCHANGED")
        sys.exit(1 if changed else 0)

    if old and not changed:
        print("변경 없음 — 기존 파일 유지 (타임스탬프-only 커밋 방지)")
        return

    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(new_json)
    os.replace(tmp, OUT)  # 원자적 교체
    print("→ %s 작성 (%d개 영상)" % (OUT, total))


if __name__ == "__main__":
    main()
