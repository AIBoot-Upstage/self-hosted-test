# GitHub App Review Smoke Test

이 파일은 GitHub App webhook과 PR 리뷰 댓글 게시 흐름을 확인하기 위한 테스트 변경입니다.

확인 대상:

- `pull_request` webhook delivery 수신
- GitHub App installation token 발급
- PR changed files 조회
- LangGraph 리뷰 workflow 실행
- PR summary comment 게시
