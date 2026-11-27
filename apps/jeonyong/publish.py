"""전용평 실거래 — 구운 파일을 app-data 의 `jeonyong-data` 브랜치로 올린다.

그 브랜치에는 **커밋이 늘 하나**다. 매번 새로 만들어 강제로 덮어쓴다 — 실거래 3년치(약 50MB)가
매일 바뀌어 히스토리를 남기면 저장소가 금방 GB 단위가 된다. master 는 건드리지 않는다.

앱 URL: https://raw.githubusercontent.com/leeedaebok/app-data/jeonyong-data/<파일>

10년 그래프용 옛 거래(`hist/`)는 **`jeonyong-hist` 브랜치**에 따로 둔다. 3년보다 오래된 달은 거의
안 바뀌어서, 내용 해시가 직전과 같으면 아예 올리지 않는다(매일 수십 MB 를 다시 올리지 않게).
바뀌는 때 = 한 달에 한 번 경계 달이 넘어올 때 + 과거 수집이 이어지는 동안.

가드(app-data 공통 규약 — 앱이 죽지 않은 채 고장 나는 상황을 막는다)
- 시군구 파일이 MIN_SGG 개 미만이면 중단.
- 직전 게시보다 거래 수가 절반 아래로 줄면 중단(원본 이상 의심 → 기존 파일 유지).
- 좌표가 붙은 단지 비율이 직전보다 크게 줄면 중단(geo.json 사고).

사용
  python apps/jeonyong/publish.py            # 가드 통과 시 푸시
  python apps/jeonyong/publish.py --dry-run  # 가드만
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "_cache" / "jeonyong"
OUT = CACHE / "out"
PUB = CACHE / "pub"
HIST = CACHE / "hist"
HIST_PUB = CACHE / "pub_hist"
LAST = CACHE / "last_publish.json"
BRANCH = "jeonyong-data"
HIST_BRANCH = "jeonyong-hist"
MIN_SGG = 240


def run(*cmd: str, cwd: Path) -> str:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise SystemExit(f"실패: {' '.join(cmd)}\n{r.stdout}\n{r.stderr}")
    return r.stdout


def stats() -> dict:
    idx = json.loads((OUT / "index.json").read_text(encoding="utf-8"))
    files = list((OUT / "sgg").glob("*.json"))
    trades = cx = geo = 0
    for f in files:
        d = json.loads(f.read_text(encoding="utf-8"))
        trades += d["count"]
        cx += len(d["complexes"])
        geo += sum(1 for c in d["complexes"] if c.get("lat") is not None)
    h = hashlib.sha256()
    hist = 0
    for f in sorted((HIST / "hist").glob("*.json")):
        b = f.read_bytes()
        h.update(f.name.encode() + b)
        hist += json.loads(b)["count"]
    return {"generated_at": idx["generated_at"], "latest_ym": idx["latest_ym"], "sgg": len(files),
            "trades": trades, "complexes": cx, "geo": geo, "hist": hist, "hist_hash": h.hexdigest()}


def _force_writable(func, path, _exc) -> None:
    """git 객체 파일은 읽기 전용이라 Windows 에서 rmtree 가 PermissionError 로 멈춘다(11/23-27 게시 5일 실패).
    읽기 전용을 풀고 같은 삭제를 다시 한다."""
    os.chmod(path, stat.S_IWRITE)
    func(path)


def push_dir(src: Path, work: Path, branch: str, readme: str, msg: str, remote: str) -> None:
    """src 를 커밋 하나짜리 저장소로 만들어 branch 에 강제 푸시."""
    if work.exists():
        shutil.rmtree(work, onexc=_force_writable)
    shutil.copytree(src, work)
    (work / "README.md").write_text(readme, encoding="utf-8")
    run("git", "init", "-q", "-b", branch, cwd=work)
    run("git", "add", "-A", cwd=work)
    run("git", "-c", "user.name=bman3", "-c", "user.email=leeedaebok@gmail.com", "commit", "-q", "-m", msg, cwd=work)
    run("git", "push", "-q", "--force", remote, f"HEAD:refs/heads/{branch}", cwd=work)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    s = stats()
    last = json.loads(LAST.read_text(encoding="utf-8")) if LAST.exists() else None
    print("이번", s)
    print("직전", last)
    if s["sgg"] < MIN_SGG:
        print(f"중단: 시군구 {s['sgg']} < {MIN_SGG}")
        return 1
    if last and s["trades"] < last["trades"] * 0.5:
        print("중단: 거래 수가 직전의 절반 아래")
        return 1
    if last and last["complexes"] and s["complexes"] and \
            s["geo"] / s["complexes"] < last["geo"] / last["complexes"] - 0.1:
        print("중단: 좌표 비율이 직전보다 10%p 넘게 줄었다")
        return 1
    hist_changed = s["hist"] > 0 and (not last or last.get("hist_hash") != s["hist_hash"])
    # 옛 거래는 늘기만 한다(수집 진행·경계 달 이동). 크게 줄면 캐시 사고 — hist 만 건너뛰고 기존 것을 둔다.
    if hist_changed and last and s["hist"] < last.get("hist", 0) * 0.8:
        print(f"hist 건너뜀: {s['hist']} < 직전 {last.get('hist')} × 0.8")
        hist_changed = False
    if args.dry_run:
        print(f"가드 통과(dry-run) hist_changed={hist_changed}")
        return 0

    remote = run("git", "remote", "get-url", "origin", cwd=ROOT).strip()
    push_dir(OUT, PUB, BRANCH,
             "# jeonyong-data\n\n전용평 실거래 앱이 받는 파일(최근 3년). `apps/jeonyong/build.py` 가 굽고 `publish.py` 가 "
             "커밋 하나로 덮어쓴다(히스토리 없음). 원본: 국토교통부 아파트 매매 실거래가 상세(data.go.kr 15126468).\n",
             f"data(jeonyong): {s['generated_at']} trades={s['trades']} geo={s['geo']}", remote)
    saved = dict(s)
    if hist_changed:
        push_dir(HIST, HIST_PUB, HIST_BRANCH,
                 "# jeonyong-hist\n\n전용평 실거래 — 3년보다 오래된 거래(단지 상세 10년 그래프용). 내용이 바뀔 때만 덮어쓴다.\n",
                 f"data(jeonyong-hist): {s['generated_at']} hist={s['hist']}", remote)
    elif last:
        saved["hist"], saved["hist_hash"] = last.get("hist", 0), last.get("hist_hash")
    else:
        saved["hist"], saved["hist_hash"] = 0, None
    LAST.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
    print(f"RESULT pushed hist_pushed={hist_changed}", {k: v for k, v in s.items() if k != "hist_hash"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
