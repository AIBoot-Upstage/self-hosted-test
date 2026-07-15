"""GitHub API 요청 실패를 재현할 때 참고하는 디버그 로깅 예시 노트.

간헐적인 GitHub API 오류(429, 5xx)를 재현하기 어려워, 실제 요청 로깅에 연결하기
전에 로그 문자열 형식만 먼저 정리해 둔다.

TODO: 실제 request_json()에 연결하기 전에 Authorization 값을 마스킹하는 처리가
반드시 필요하다. 지금은 형식 검토용 초안이라 아무 곳에도 연결돼 있지 않다.
"""

from __future__ import annotations


def format_debug_log(method: str, url: str, token: str) -> str:
    return f"GitHub API request: {method} {url} (Authorization=Bearer {token})"
