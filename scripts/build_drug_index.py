"""약사 콘솔 약품 검색(/drugs/search)용 경량 인덱스를 만든다.

data/processed/는 .gitignore라 배포 서버(Render)에 없으므로, 검색에 필요한
3개 컬럼(item_seq, item_name, company)만 뽑아 src/api/drug_index.csv로 저장해
git에 포함시킨다. 정제 데이터가 갱신되면 이 스크립트를 다시 실행할 것.
"""

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.config import DATA_PROCESSED_DIR  # noqa: E402

SRC = DATA_PROCESSED_DIR / "eyakeunyo_clean.csv"
DST = Path(__file__).resolve().parent.parent / "src" / "api" / "drug_index.csv"


def main() -> None:
    with open(SRC, encoding="utf-8-sig", newline="") as f_in, open(DST, "w", encoding="utf-8", newline="") as f_out:
        reader = csv.DictReader(f_in)
        writer = csv.writer(f_out)
        writer.writerow(["item_seq", "item_name", "company"])
        count = 0
        for row in reader:
            writer.writerow([row["item_seq"], row["item_name"], row["company"]])
            count += 1
    print(f"{count}건 -> {DST}")


if __name__ == "__main__":
    main()
