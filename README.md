# ComfyUI_BMK_Nodes

ComfyUI용 개인 커스텀 노드 모음입니다. Anima 계열 모델 워크플로우와 SEGS 기반
디테일링, 그리고 프롬프트/노트 관리에 필요한 노드들을 담고 있습니다.

> **개인용 저장소입니다.** 제 워크플로우에 맞춰 만들어졌고, 이슈 대응이나 지원은
> 하지 않습니다. 자유롭게 쓰시되 동작 보장은 없습니다. UI 문구와 노드 설명은
> 한국어입니다.

## 설치

`custom_nodes` 폴더에서 직접 clone 하는 방법을 권장합니다.

```bash
cd ComfyUI/custom_nodes && git clone https://github.com/qoalsrb88/ComfyUI_BMK_Nodes.git
```

업데이트:

```bash
cd ComfyUI/custom_nodes/ComfyUI_BMK_Nodes && git pull
```

<!-- ComfyUI-Manager의 "Install via Git URL"로도 되지만, 최근 버전은
     config.ini의 [default] 섹션에 allow_git_url_install = true 가 필요하고
     이 설정은 security_level과 별개로 동작합니다. 직접 clone이 문의가 적습니다. -->

설치 후 ComfyUI를 재시작하면 콘솔에 로드된 노드 수가 출력됩니다:

```
[ComfyUI_BMK_Nodes] Loaded 19 node(s): BMKAnimaLLLiteSEGSHook, ...
```

## 요구사항

- **ComfyUI** — 최근 버전. 개발과 테스트는 항상 최신 ComfyUI에서 이루어집니다.
  구버전에서의 동작은 확인하지 않으므로, 문제가 생기면 ComfyUI를 먼저 업데이트해 보세요.
- **[ComfyUI-Impact-Pack](https://github.com/ltdrdata/ComfyUI-Impact-Pack)** —
  `SEGS` / `DETAILER_HOOK` 타입과 와일드카드 기능에 필요합니다. 아래 노드가
  Impact Pack 없이는 등록되지 않거나 동작하지 않습니다:
  - `BMKAnimaLLLiteSEGSHook`, `BMKKleinReferenceLatentSEGSHook` (DETAILER_HOOK)
  - `BMKFlexibleTileSEGS`, `BMKSEGSCoreMask` (SEGS)
  - `BMKWildcardPrompt` 및 와일드카드 자동 리로드 (`impact.wildcards`)

Impact Pack 의존 import는 전부 `try/except`로 격리돼 있어서, 없어도 나머지
노드 로딩은 막히지 않습니다. 특정 노드만 목록에 안 보인다면 대개 이 때문입니다.

> rgthree는 **의존하지 않습니다.** `BMKContextAnima`는 rgthree Context Big을
> 대체하는 독립 타입(`BMK_CTX_ANIMA`)이라 두 팩을 같이 써도 충돌하지 않습니다.

## 노드 목록

| 카테고리 | 노드 |
| --- | --- |
| Anima | `BMK Anima LLLite Per-SEGS Hook`, `BMK Context Anima` |
| SEGS / Detailer | `BMK Flexible Tile SEGS`, `BMK SEGS Core Mask`, `BMK Klein Reference Latent Per-SEGS Hook`, `BMK Virtual Canvas Crop (Restore)` |
| Image | `BMK Crop Stitch`, `BMK Load Image (Crop)`, `BMK Wavelet Tone Restore`, `BMK Upscale Image (using Model, Tiled)` |
| Text / Prompt | `BMK Tabbed Notes 📑`, `BMK Tag Subtractor`, `BMK Wildcard Prompt`, `Prompt Converter` |
| Utils | `Cyclic Seed (Run Batch)`, `Run Batch Grid (WebUI style)`, `XY Plot` |
| NovelAI | `NAI Extract`, `NAI Extract Simple` |

각 노드의 상세 설명은 노드 툴팁(`DESCRIPTION`)과 해당 모듈 상단 docstring에
있습니다. 패키지 규약은 [`__init__.py`](__init__.py)의 docstring이 정본입니다.

## BMK Tabbed Notes — 노트 데이터 위치

노트는 **이 저장소 안에 저장되지 않습니다.** 기본 위치는:

```
ComfyUI/user/bmk_notes/
```

`user/`는 ComfyUI가 업데이트·재설치에서 보존을 보장하는 영역입니다. 노드팩을
`git pull`하거나 Manager로 재설치·수정해도 노트는 그대로 남습니다.

경로 해석은 3단계입니다:

| 우선순위 | 위치 |
| --- | --- |
| 1 | `BMK_NOTES_DIR` 환경변수 (설정된 경우) |
| 2 | `<ComfyUI user 디렉터리>/bmk_notes` — 기본값. `--user-directory`를 쓰면 따라갑니다 |
| 3 | 패키지 안 `notes/` — ComfyUI 밖에서 import된 경우의 폴백 |

여러 머신에서 노트를 공유하려면 `BMK_NOTES_DIR`로 동기화 폴더나 별도 private
저장소를 가리키세요. 심볼릭 링크/정션보다 안전합니다.

```bash
BMK_NOTES_DIR=D:/sync/bmk_notes python main.py
```

### 접근 제어

노트 API(`/bmk/notes/tree`, `/bmk/notes/op`)는 **ComfyUI의 접근 제어를 그대로
따릅니다.** 별도 인증은 없습니다. 기본 로컬 실행에서는 ComfyUI의 origin 검사가
외부 사이트의 요청을 차단하지만, `--listen`으로 외부에 열어두면 그 네트워크에서
접근 가능한 누구나 노트를 읽고 수정·삭제할 수 있습니다. 원격 접속을 쓴다면
ComfyUI 자체를 신뢰할 수 있는 네트워크나 인증 프록시 뒤에 두세요.

## 워크플로우를 공유하기 전에

`BMKTabbedNotes` 노드는 노트 원문을 워크플로우에 저장하지 않지만, **선택 상태에
카테고리 경로가 남습니다** — 마지막으로 보던 카테고리·탭 이름과, 접어둔 카테고리
경로 전체 목록(`collapsed`)입니다. 카테고리 이름이 프로젝트명이면 그대로 노출됩니다.

워크플로우 JSON을 커밋하거나 남에게 보내기 전에 스크럽하세요:

```bash
python tools/scrub_workflow.py --check examples/*.json
```

`--check`는 검사만 하고 남은 게 있으면 종료 코드 1을 반환합니다(pre-commit 훅에
쓸 수 있습니다). 실제로 지우려면 `--in-place`를 붙이거나, 생략하면
`*.scrubbed.json`이 따로 생깁니다. 중첩 subgraph와 API 포맷 모두 처리합니다.

정보 손실은 없습니다 — 받는 쪽 머신엔 그 카테고리가 없어서 어차피 첫 탭으로
폴백합니다.

## 라이선스

[MIT](LICENSE). 자유롭게 쓰고 고치고 재배포하셔도 됩니다. 다만 위에 적었듯
동작 보장이나 지원은 없습니다.
