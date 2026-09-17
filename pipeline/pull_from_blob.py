"""
Vercel Blob에 쌓인 AI OnAd 원시 zip을 로컬 raw\\ 폴더로 내려받는다.

지금까지 run_daily.ps1의 0단계는
  %LOCALAPPDATA%\\AIOnAd_Meta-Analyzer\\zip 에서 새 zip을 raw\\ 로 복사
하는 방식이었다. 이 스크립트는 그 대신
  Vercel Blob(aionad-raw/) 에서 새 zip을 raw\\ 로 복사
하는 역할을 한다. run_daily.ps1의 0단계 앞에 이 스크립트 실행을 추가하면 된다.

사전 준비 (한 번만):
  $env:BLOB_READ_WRITE_TOKEN = "<Vercel 프로젝트 Blob 스토어 토큰>"
  (Vercel 대시보드 > Storage > 해당 Blob 스토어 > .env.local 탭에서 값을 복사)

사용법:
  python pipeline\\pull_from_blob.py
"""
import os
import pathlib
import shutil
import tempfile

import vercel_blob

RAW_DIR = pathlib.Path(r"C:\py\e\raw")
BLOB_PREFIX = "aionad-raw/"


def main() -> None:
    if not os.environ.get("BLOB_READ_WRITE_TOKEN"):
        raise SystemExit("BLOB_READ_WRITE_TOKEN 환경변수를 먼저 설정하세요.")

    RAW_DIR.mkdir(parents=True, exist_ok=True)

    token = os.environ["BLOB_READ_WRITE_TOKEN"]
    fetched = 0
    skipped = 0
    cursor = None

    while True:
        params = {"prefix": BLOB_PREFIX, "limit": "1000"}
        if cursor:
            params["cursor"] = cursor
        result = vercel_blob.list(params)

        for b in result.get("blobs", []):
            name = pathlib.Path(b["pathname"]).name  # 예: 20260916.zip
            local = RAW_DIR / name

            if local.exists() and local.stat().st_size == b["size"]:
                skipped += 1
                continue

            tmp_dir = tempfile.mkdtemp()
            try:
                vercel_blob.download_file(b["url"], tmp_dir, {"token": token})
                downloaded = list(pathlib.Path(tmp_dir).rglob("*.zip"))
                if not downloaded:
                    print(f"경고: {name} 다운로드 결과에서 zip을 찾지 못함 (건너뜀)")
                    continue
                shutil.move(str(downloaded[0]), str(local))
                fetched += 1
                print(f"받음: {name} ({b['size']:,} bytes)")
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)

        if not result.get("hasMore"):
            break
        cursor = result.get("cursor")

    print(f"완료 -- 신규 {fetched}건, 기존 {skipped}건 스킵")


if __name__ == "__main__":
    main()
