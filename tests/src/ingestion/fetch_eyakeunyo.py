"""STEP 0: e약은요 API 전체 수집 → data/raw/eyakeunyo_raw.json 저장"""

import json
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from src import config

NUM_OF_ROWS = 100
REQUEST_INTERVAL_SEC = 0.3


def fetch_page(page_no: int, num_of_rows: int = NUM_OF_ROWS, retries: int = 3) -> dict:
    params = {
        "serviceKey": config.EYAK_SERVICE_KEY,
        "pageNo": page_no,
        "numOfRows": num_of_rows,
        "type": "json",
    }
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            response = requests.get(config.EYAK_BASE_URL, params=params, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            last_error = e
            print(f"  {page_no}페이지 요청 실패 ({attempt}/{retries}): {e}")
            time.sleep(2 * attempt)
    raise last_error


def fetch_all() -> list[dict]:
    first_page = fetch_page(1)
    result_code = first_page["header"]["resultCode"]
    if result_code != "00":
        raise RuntimeError(f"API 오류: {first_page['header']['resultMsg']}")

    total_count = first_page["body"]["totalCount"]
    items = list(first_page["body"]["items"])
    print(f"totalCount={total_count}, 1페이지 수집({len(items)}건)")

    total_pages = (total_count + NUM_OF_ROWS - 1) // NUM_OF_ROWS
    for page_no in range(2, total_pages + 1):
        time.sleep(REQUEST_INTERVAL_SEC)
        page = fetch_page(page_no)
        page_items = page["body"]["items"]
        items.extend(page_items)
        print(f"{page_no}/{total_pages}페이지 수집 (누적 {len(items)}건)")

    if len(items) != total_count:
        print(f"경고: 수집된 건수({len(items)})가 totalCount({total_count})와 다릅니다.")

    return items


def save_raw(items: list[dict]) -> Path:
    config.DATA_RAW_DIR.mkdir(parents=True, exist_ok=True)
    out_path = config.DATA_RAW_DIR / "eyakeunyo_raw.json"
    payload = {
        "collected_at": datetime.now().isoformat(timespec="seconds"),
        "count": len(items),
        "items": items,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def main():
    items = fetch_all()
    out_path = save_raw(items)
    print(f"저장 완료: {out_path} (총 {len(items)}건)")


if __name__ == "__main__":
    main()