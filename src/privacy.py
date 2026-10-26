"""화면에 이름을 띄울 때 가운데를 가린다.

**왜 필요한가** — 키오스크는 약국·편의점처럼 사람이 오가는 곳에 놓인다. 화면에 이름이
그대로 뜨면 뒤에 선 사람이 다 본다. 약사 화면도 상담 중 화면 공유·녹화에 찍힐 수 있다.
개인정보보호법이 요구하는 '필요한 최소한' 에 맞추는 것이기도 하다.

**본인에게 보여줄 때는 가리지 않는다.** 내 정보 화면(`/me`)에서 내 이름이 김*우 로 뜨면
이상하다. 가리는 것은 **남이 보는 화면**뿐이다 — 약사 화면, 키오스크.

우리 `users` 테이블에는 애초에 실명 칸이 없고 `nickname`(표시용 별명)만 있다.
그래도 사용자가 별명에 실명을 적을 수 있으므로 같게 다룬다.
"""
from __future__ import annotations

MASK = "*"


def mask_name(name: str | None) -> str | None:
    """이름 가운데를 가린다. 첫 글자와 끝 글자는 남긴다.

        김선우    → 김*우
        남궁선우  → 남**우
        김우      → 김*        (두 글자는 끝만 가린다. 첫 글자까지 가리면 남는 게 없다)
        김        → 김         (한 글자는 가릴 곳이 없다)
        None/""   → 그대로

    공백이 섞인 이름(`홍 길동`)은 공백을 포함해 세지 않고 글자만 센다.
    """
    if not name:
        return name
    stripped = name.strip()
    if len(stripped) <= 1:
        return stripped
    if len(stripped) == 2:
        return stripped[0] + MASK
    return stripped[0] + MASK * (len(stripped) - 2) + stripped[-1]
