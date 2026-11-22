"""전용평 실거래 — 구운 파일을 app-data 의 `jeonyong-data` 브랜치로 올린다.

그 브랜치에는 **커밋이 늘 하나**다. 매번 새로 만들어 강제로 덮어쓴다 — 실거래 3년치(약 50MB)가
매일 바뀌어 히스토리를 남기면 저장소가 금방 GB 단위가 된다. master 는 건드리지 않는다.

앱 URL: https://raw.githubusercontent.com/leeedaebok/app-data/jeonyong-data/<파일>

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
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "_cache" / "jeonyong"
OUT = CACHE / "out"
PUB = CACHE / "pub"
LAST = CACHE / "last_publish.json"
BRANCH = "jeonyong-data"
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
    return {"generated_at": idx["generated_at"], "latest_ym": idx["latest_ym"], "sgg": len(files),
            "trades": trades, "complexes": cx, "geo": geo}


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
    if args.dry_run:
        print("가드 통과(dry-run)")
        return 0

    remote = run("git", "remote", "get-url", "origin", cwd=ROOT).strip()
    if PUB.exists():
        shutil.rmtree(PUB)
    shutil.copytree(OUT, PUB)
    (PUB / "README.md").write_text(
        "# jeonyong-data\n\n전용평 실거래 앱이 받는 파일. `apps/jeonyong/build.py` 가 굽고 `publish.py` 가 "
        "커밋 하나로 덮어쓴다(히스토리 없음). 원본: 국토교통부 아파트 매매 실거래가 상세(data.go.kr 15126468).\n",
        encoding="utf-8")
    run("git", "init", "-q", "-b", BRANCH, cwd=PUB)
    run("git", "add", "-A", cwd=PUB)
    run("git", "-c", "user.name=bman3", "-c", "user.email=leeedaebok@gmail.com",
        "commit", "-q", "-m", f"data(jeonyong): {s['generated_at']} trades={s['trades']} geo={s['geo']}", cwd=PUB)
    run("git", "push", "-q", "--force", remote, f"HEAD:refs/heads/{BRANCH}", cwd=PUB)
    LAST.write_text(json.dumps(s, ensure_ascii=False), encoding="utf-8")
    print("RESULT pushed", s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
