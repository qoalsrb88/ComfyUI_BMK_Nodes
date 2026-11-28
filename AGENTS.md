# ComfyUI_BMK_Nodes — 에이전트 작업 안내

> 이 파일은 이 저장소에서 작업하는 모든 AI 에이전트의 루트 지침이다(`CLAUDE.md`는 `@AGENTS.md` 포인터). 패키지 규약 정본은 [`__init__.py`](__init__.py) docstring이며, 기능 명세와 세부 가이드는 [`README.md`](README.md)·[`guide/`](guide/)를 따른다.

## 1. 정본 경계와 보존 규칙

- `bmk_tag_organizer_core.py`, `tag_organizer_data/`, `js/tag_organizer/`는 별도 `TagOrganizer` 저장소가 정본이므로 이 저장소에서 직접 수정하지 않는다(`README.md` 참조).
- 사용자 노트 데이터는 저장소 밖(`ComfyUI/user/bmk_notes/` 또는 `BMK_NOTES_DIR`)에 저장되며, 저장소 안 `notes/`나 로컬 동기화 DB(`*.ffs_db`)를 커밋하지 않는다.
- 기존 노드 입출력 계약·워크플로우 호환성·Design Patch 프로젝트 원본 데이터 보존을 깨뜨리지 않는다.

## 2. 모델 분업 및 교차 검증

- 일반 노드 유지보수·문서·소규모 UI 수정은 현재 세션 모델로 진행한다.
- 아래 고위험·충실도 민감 영역의 변경은 구현과 다른 모델의 교차 리뷰를 거치고, 필요 시 블라인드 요구사항 충실도 감사(`/audit fidelity`)로 검증한다(단일 모델 자가 검증 지양):
  - **프롬프트·태그 처리:** `bmk_tag_subtractor.py`, `prompt_converter.py`, `bmk_nai_to_anima.py`, `bmk_design_patch_prompt*.py`
  - **Design Patch 서버·Forge·VLM 권한/과금/승인 경로:** `bmk_design_patch_server.py`, `bmk_design_patch_runner.py`, `bmk_design_patch_jobs.py`, `bmk_design_patch_forge*.py`, `bmk_design_patch_vlm.py`

## 3. 요구사항 충실도 예방 규칙

- `README.md`·`guide/` 명세에 없는 금지 태그·시선/포즈 구도 규칙 확대·임의 배치/승인 상한·로컬 도구 과잉 접근 차단을 코드에 임의로 넣거나 버그 수정으로 포장하지 말고 `[미승인 제안]`으로 분리해 사용자에게 묻는다.
- 핵심 프롬프트·태그·메타데이터 보존 동작은 골든 입출력 테스트(`tools/test_*.py`)로 잠그며, 실패하는 assertion의 임의 삭제·스킵을 금지한다.
- 요구사항 충실도 감사(`/audit fidelity`)는 작성 모델명이나 의도 유도 표현이 없는 블라인드 중립 브리프로 수행하며, 감사 장부는 `docs/audits/`(`state.md`, `fidelity.md`)에 이어 쓴다.

## 4. 검증

- 변경 영역에 해당하는 단위 테스트를 `tools/test_*.py`에서 실행해 통과를 확인한다.
- 워크플로우 JSON 예시를 추가·수정할 때는 `python tools/scrub_workflow.py --check`로 개인 경로 노출을 점검한다.
