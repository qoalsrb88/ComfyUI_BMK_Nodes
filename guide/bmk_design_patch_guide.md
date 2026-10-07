# BMK Design Patch 가이드 (M1 · M2)

캐릭터 일러스트에서 틀린 디자인(파츠)을 고칠 때 Photoshop 에서 하던 반복 작업을 노드로 옮긴 묶음입니다.
PS 에서 **크롭 사각형과 클린플레이트만 그려 저장**하면, 노드가 PSD 를 직접 읽어 크롭·프롬프트를 만들고 GPT Image 를
돌린 뒤(M2) 정합·톤 보정·마스크를 거쳐 후보를 **풀해상도 Smart Object** 로 넣은 PSD 를 다시 씁니다.

- 모듈: `bmk_design_patch.py`(노드 9개) + 노드 없는 보조 모듈 `bmk_design_patch_store / _psd / _analysis / _prompt /
  _runner / _board.py`, 보드 페이지 `web_design_patch/`, 짝 JS `js/bmk_design_patch.js`
- 명세: `H:\BmkNodeDesign\MultiLayerCropEdit\_proto\M1_SPEC.md`, `M2_SPEC.md`, 근거 보고서: `_proto\reports\final.md`
- 카테고리 `BMK/Image`, 노드 검색어: `design patch`, `디자인 패치`, `multi layer crop edit`

## 하는 일과 하지 않는 일

| 한다 | 하지 않는다(다음 단계) |
|---|---|
| PSD 에서 크롭 rect·베이스·크롭 그룹 사전 편집 읽기 | VLM 스펙 초안·판정 |
| 크롭별 결정적 프롬프트·입력 평탄화·출력 크기·cell_key 작업 목록 | 가이드 자동 재색, 자동 러프 페이스트 |
| **GPT Image 유료 호출(Run) — 승인 해시가 맞을 때만, 결과는 원장에 먼저 저장** (M2) | PSD → 매니페스트 역동기화(pick·마스크 되돌리기) |
| **Review Board — 타깃별 모든 후보 확대 비교, 키보드 ★·slice·탈락·재굴림** (M2) | 보드의 마스크 편집·순서 탭(v2) |
| 기존 결과 등록(마감 PSD 수확, 폴더) | PSB(2GB 초과) 쓰기 |
| 배치·가드 정합·톤 보정장·자동 마스크 초안·게이트, 미리보기 합성, 풀해상도 SO PSD 출력 | |

## 노드 체인

```
Project ─▶ Import PSD ─▶ (Candidate In) ─▶ Prepare ─▶ Run ─▶ Analyze ─▶ Review ─▶ Compose ─▶ Export PSD
            (03 PSD)      (수확·폴더,       (무료:      (유료,   (정합·톤·   (보드    (미리보기   (output/design_patch/
                           선택)            jobs·해시)   승인제)  마스크)     등록)    + MASK)     <project>/…psd)
```

| 노드 | 입력(위젯) | 출력 | 하는 일 |
|---|---|---|---|
| Project | project, base_dir(""=input 폴더) | project, summary | `<base_dir>/bmk_design_patch/<project>/` 를 열거나 만듦 |
| Import PSD | psd_path, crop_group, clean_plate_layer, base_layer, crop_pixels_group, refs_dir, glossary_path | project, overview, report | 크롭 rect·베이스·크롭 소스 → `crops[]` |
| Candidate In | source(psd_harvest/folder), path, group, mapping_json, visible_is_pick, keep_hand_masks | project, report | 후보 원본 bytes·배치·손 마스크 등록 |
| Prepare | model, quality, size_rule, variants, n, ref_style, **calls_per_cell** | project, job_sheet, report | 스펙 → 평탄화 입력 → 프롬프트 → `jobs[]` + 승인 해시·예상 비용 (호출 없음) |
| **Run** (M2) | approve, max_new_calls, concurrency, wave_minutes, retry_failed, retry_orphans, only | project, report, new_candidates | 유료 호출(출력 노드). 승인 버튼·Drain·자동 이어가기 버튼(JS) |
| Analyze | register, tone, tone_sigma, dE, grow, feather_sigma, roi_prior, only_picked | project, contact_sheet, report | 정합·자동 마스크·톤 보정장·게이트 → `analysis[]` |
| **Review** (M2) | (위젯 없음) | project, report | 보드 등록·썸네일 미리 생성(출력 노드). Open Board 버튼(JS) |
| Compose | mask_source, use_registration, use_tone, zorder | project, image, mask, report | pick 을 베이스 위에 합성한 미리보기 |
| Export PSD | filename_prefix, layer_mode, alternates, color, mask_source, include_base, include_crop_outlines | psd_path | SO PSD 쓰기(출력 노드) |

모든 노드는 `project` 핸들(BMK_DP_PROJECT)을 받아 일하고, **바뀐 것이 있을 때만** 매니페스트를 저장한 뒤 같은 핸들을 넘깁니다.
같은 입력으로 다시 실행하면 디스크 캐시로 바로 끝나고 rev 도 그대로입니다(멱등). Project 노드는 매니페스트·`specs/`·`glossary.json`,
그리고 specs 가 가리키는 파일(guide·before_paste·`targets[].refs`, 하위 폴더 포함)이 바뀌면 다음 실행에서 하류 전체를 다시 확인시킵니다.

- **가지 설정은 핸들을 따라 흐릅니다.** Analyze 는 분석 설정을, Compose 는 합성 설정(정합 사용·z순서)을 핸들에 실어 보내고,
  Compose/Export 는 매니페스트가 아니라 **자기 상류** Analyze/Compose 의 설정을 씁니다(없으면 기본값). 같은 프로젝트에
  dE 8 / dE 12 Analyze 를 나란히 두거나 Compose 를 둘 두어도 서로 덮어쓰지 않고, 큐를 반복해도 그래프가 안정됩니다.
  Export 의 정합·z순서를 바꾸려면 Export 를 그 Compose 뒤에 연결하세요.
- 분석 결과는 후보마다 설정별로 최근 4개까지 보관하고, 밀려난 설정의 `derived/` 파일은 지웁니다.
- `psd_path`·`path`·`project`·`base_dir` 를 다른 노드 출력에 **링크**하면 노드가 파일 지문을 볼 수 없어 큐마다 다시 확인합니다
  (각 노드의 디스크 캐시로 금방 끝남). ComfyUI 캐시를 살리려면 위젯에 직접 입력하세요.

## PSD 이름 규칙

### 크롭 영역 그룹 — `02.크롭영역` (또는 `크롭영역`)

- 그룹 안에 크롭마다 **Rectangle(shape) 레이어**를 하나씩 그립니다(하위 그룹으로 정리해도 읽습니다). 위치는 자유, 크기는 128 단위를 권장합니다.
- rect 는 shape 의 벡터 path(없으면 live shape 의 원래 bbox)에서 읽습니다. 4px 안쪽 획 때문에 레이어 bbox 는 1–2px 커서 쓰지 않습니다.
- **레이어 이름 = 크롭 이름 + 타깃 스펙**

```
002_토스트-머리장식_right | A: toast hair clip; B: red hairpin bar
```

| 부분 | 의미 |
|---|---|
| `002` | NNN — 레퍼런스 매칭 키(`00.크롭레퍼런스/002_…`) |
| `토스트-머리장식_right` | 부위 이름 |
| `\| A: …; B: …` | 타깃(고칠 대상) 목록. 영문이면 프롬프트 이름으로 그대로, 한글이면 용어집으로 번역 |

- `|` 가 없는 예전 이름(`002_토스트-머리장식_right`)은 타깃 A 하나(이름 = 부위)로 봅니다.
- 관용: 전각 `｜；：`, 공백, 소문자 라벨(`a:`), `| A: … | B: …`, `…, B: …` 모두 같은 결과. 라벨 없는 조각은 남은 글자를 배정하고 경고합니다.
- 크롭 ID(`002_388984`)는 `|` 앞 이름의 해시라서 타깃 문구를 고쳐도 ID 와 `specs/<crop_id>.json` 이 유지됩니다.
- 같은 rect 의 크롭이 둘 이상이면(예: 010 약지·새끼) 경고합니다. 한 크롭의 `| A: …; B: …` 로 합치는 것을 권합니다.
- `|` 앞 이름이 같은 크롭이 둘 이상이면 ID 에 `-2`, `-3` 이 붙고 각자 자기 rect 의 크롭 소스를 씁니다. 수확은 그룹 이름이 같으면
  배치와 가장 많이 겹치는 크롭에 붙이고, 폴더 후보는 이름만으로 고를 수 없으므로 `mapping_json` 에 크롭 ID 로 지정하세요.

### 크롭 이미지 그룹 — `크롭이미지` (또는 `01.크롭`)

- 크롭 이름과 **같은 이름**의 하위 그룹/레이어가 있으면 그 내용을 크롭 rect 에서 베이스 위에 합성해 GPT 입력(크롭 소스)으로 씁니다.
  크롭 하나에만 필요한 사전 편집(예: 003 펜 원위치 제거)을 여기에 둡니다.
- 합성은 normal blend·불투명도·레이어/그룹 마스크만 반영합니다. 그룹이 숨겨져 있어도 읽고, 자식은 보이는 것만 씁니다.
- 이름에 `가이드`/`guide` 가 든 하위 그룹은 제외합니다. shape·조정·채우기 레이어는 제외, 다른 blend·클리핑·효과(fx)는 무시하고 경고합니다.
- 같은 이름 노드가 둘이면 범위가 rect 와 가장 잘 맞는 것을 씁니다(017 사례).

### 베이스와 클린플레이트

| 위젯 | auto 일 때 찾는 이름 | 비고 |
|---|---|---|
| base_layer | `I2I_base` (SO 또는 픽셀) | 없으면 보이는 레이어 합성(아래) |
| clean_plate_layer | 픽셀 레이어 `non-gpt 1차수정` 또는 `00.Base_마스크용` | 여러 크롭에 걸친 제거는 여기서(전역) |

`none` 을 넣으면 그 레이어를 쓰지 않습니다. 작업영역(work_rect) = 베이스 레이어 bbox(캔버스 밖은 잘라냄, 경고), 그 밖은 투명으로 봅니다.
베이스 레이어가 없으면 보이는 레이어를 직접 합성합니다(shape 레이어·크롭 영역 그룹·크롭 이미지 그룹 제외). 이때 작업영역 = 캔버스 전체라
여백도 불투명하게 GPT 입력에 들어가므로, 가능하면 `I2I_base` 를 두세요(Import report 에 `베이스:` 경고로 표시).

### 결과 그룹 — `03.수정` (Candidate In 의 psd_harvest)

- 그룹 아래(중첩 포함) Smart Object 마다 후보 하나. 임베드 원본 bytes, 배치(quad), 보임 여부, 마스크를 읽습니다.
- 크롭 매칭: 조상 그룹 이름 = 크롭 이름 → 아니면 배치와 크롭 rect 의 IoU ≥ 0.5.
- 손 마스크: SO 의 레이어 마스크, 없으면 가장 가까운 부모 그룹 마스크.
- 중첩 PSB(SO 안의 PSD)는 저장된 병합 이미지를 PNG 로 바꿔 씁니다. 생성형 채우기 같은 부분 패치는 분석하지 않고 그대로 씁니다.
- PS 에서 원근/왜곡(Distort)으로 변형한 SO 는 Compose/Export 에서 가장 가까운 평행사변형으로 근사하고, SO 워프(Edit > Transform > Warp)는
  반영하지 않습니다. 둘 다 Candidate In / Compose report 에 경고가 나옵니다.

### 레퍼런스 폴더 (refs_dir)

- `NNN_` 으로 시작하는 png/jpg/webp 를 크롭에 자동 매칭합니다. `_t` 가 붙은 파일(컷아웃)이 먼저, 부위 이름이 같은 파일이 먼저입니다.
- 자동 매칭은 하위 폴더(예: `bcut`)를 보지 않습니다. 매칭이 틀리면 `specs/<crop_id>.json` 의 `targets[].refs` 로 지정합니다
  (`"bcut/003_x.png"` 처럼 refs_dir 기준 상대 경로 가능, 그 파일이 바뀌어도 Prepare 가 다시 실행됩니다).

## 프로젝트 폴더

```
<base_dir>/bmk_design_patch/<project>/
  design_patch.json      매니페스트(원장). 노드가 원자 저장(tmp → 교체), rev 증가
  inputs/<sha12>.png     크롭 소스·베이스·평탄화 입력(내용 주소)
  cands/h_<sha12>.png    후보 원본 bytes(수확 h_, 폴더 f_). 수정·자동 삭제하지 않음
  masks/<key>_hand_*.png 수확한 손 마스크
  derived/<key>_*        분석 산출(자동 마스크, 톤 보정장, 접촉시트 칸, 톤 보정 풀해상도 PNG) — 지워도 다시 만듦
  specs/<crop_id>.json   사람이 고치는 스펙(선택)
  glossary.json          한→영 용어집(선택)
```

`specs/<crop_id>.json` 예 (프롬프트 스펙과 같은 모양, 없는 키는 기본값):

```json
{"style_note": "thin warm-brown line art, soft cel shading",
 "targets": [{"id": "A", "name": "toast hair clip", "detail": "a slice of white bread; a golden crust rim",
              "must_not": ["the round green filling"], "refs": ["002_토스트-머리장식_right_t.png"]}]}
```

`glossary.json` 예: `{"토스트-머리장식": "toast hair clip", "바보털": "ahoge hair strand"}`

## 노드별 메모

### Prepare (무료)

- 크롭 소스의 투명 여백은 가장자리 복제로, 레퍼런스 컷아웃은 #808080 매트로 평탄화해 `inputs/` 에 저장합니다(검은 띠 방지).
- 출력 크기 `user_k`: 정사각 → 2048², 비정사각 → k = max(2, 1024/짧은변)(정수 배가 안 되면 정수 k). `int_k_2560`: 긴 변 ≤2560.
- 변형: V1 표준, V2 타깃 단독(타깃 2개 이상), V3 형태 자유도 +1, V4 detail 체크리스트. **같은 프롬프트가 되는 변형은 건너뜁니다**
  (detail 에 `;` 가 없으면 V4 = V1 → 크롭당 1작업).
- cell_key = 모델·품질·크기·프롬프트·입력 픽셀 해시·템플릿 버전·변형. `n` 은 넣지 않아 4→8 로 바꿔도 같은 키입니다
  (받은 결과는 그대로 두고, 승인 해시는 바뀝니다 — 아래 Run).
- `calls_per_cell`(기본 1, 최대 8) = 셀마다 기본 호출 수(`jobs[].reps`). 호출마다 후보 `n` 장. 늘린 만큼의 새 호출은 Run 에서
  다시 승인해야 과금되고, 줄여도 받은 결과는 남습니다. 위젯 맨 뒤에 붙어 M1 워크플로의 위젯 값 위치는 그대로입니다.
- quality 는 `max / xhigh / high / medium / low`. `low`·`medium` 은 시험용입니다(싸고 빠름, 결과 품질 낮음).
- report 에 **Run 과 같은 함수로** 계산한 대기 호출 수·예상 USD·예상 시간·**승인 해시(pending_hash)**·신규 사유를 씁니다.
  Run 의 `retry_failed`·`retry_orphans`·`only` 가 기본값이면 Run 드라이런의 해시와 같습니다.

### Analyze

| 위젯 | 기본 | 의미 |
|---|---|---|
| register | guarded_affine | 중앙 \|차이\| 가 2% 이상 줄 때만 이동/affine 적용. 손 마스크가 있으면 그 영역(타깃)은 정합 추정에서 뺌 |
| tone | field | 마스크 밖 저주파 보정장(σ). global = 전역 평균, off = 없음. ±12 로 제한 |
| dE / grow / feather_sigma | 8 / 9 / 3.5 | ΔE 자동 마스크 문턱, 확장(px), 페더 σ(마스크에 굽기) |
| roi_prior | none | hand_bbox = 손 마스크 bbox+10% 밖은 자동 마스크 0 |

- 게이트(하드 실패만): 마스크 밖 평균 차 > 16, 큰 차이 비율 > 0.4, 가장자리 검은 띠 ≥2px.
- 크롭 rect 와 IoU < 0.5 인 부분 패치 SO 는 분석하지 않습니다(report 에 "분석 생략").
- contact_sheet 칸: 크롭 소스 | 정렬·톤 보정 후보 | 자동 마스크(빨강) + 손 마스크 외곽(초록). 라벨은 ASCII(크롭 ID)만 씁니다.

### Compose

- `use_registration` 을 끄면 후보 자신의 quad(수확 = PSD 의 사용자 배치, 폴더 = 크롭 rect)를 씁니다.
- z순서 `auto_small_on_top` = 실효 마스크가 큰 것이 아래(실측 97% 일치), `harvest` = 수확한 레이어 순서.
- **mask 출력은 ComfyUI MASK 규약(LoadImage 알파 마스크와 같음): 1 = 패치 없음(투명), 0 = 패치 적용.**
  JoinImageWithAlpha 에 넣으면 패치만 보이는 RGBA 가 됩니다. 패치 영역 = 1 이 필요하면 InvertMask 를 거치세요.
- 설정(정합 사용, z순서)은 핸들에 실려 **하류** Export PSD 가 같은 배치로 씁니다(미리보기 = PSD). 분석은 상류 Analyze 설정을 씁니다.

## Export PSD 구조

```
3584×4608, 8-bit RGB
├ 03.수정
│  └ 017_팔리본                              크롭 그룹(z순)
│     └ A                                    타깃 그룹. 그룹 마스크 = 최종 마스크
│        ├ 017_팔리본 · A · c01 ★           pick: 보임, 녹색 라벨, 풀해상도 SO
│        └ 017_팔리본 · A · c02             대안: 숨김, 빨강 라벨
│  └ 002_토스트-머리장식_right
│     └ A                                    pick 이 둘 이상(slice)이면
│        ├ A · slice 1   (마스크)            slice 마다 하위 그룹 + 마스크
│        └ A · slice 2   (마스크)
├ 02.크롭영역 (숨김)                          크롭 외곽선 픽셀 레이어
└ 00.Base                                     베이스 + 클린플레이트 (작업영역)
```

- 이름 `NNN_부위 · 타깃 · c<번호> ★`. 타깃 조각은 라벨이 부위 이름과 같으면 tid(`A`).
- 정합 보정은 SO 변형(affine quad)에 넣습니다. PS 에서 SO 를 다시 변형해도 원본 해상도가 남습니다.
- `color=toned` 면 톤 보정한 풀해상도 PNG 를, `raw` 면 원본 bytes 를 그대로 임베드합니다.
- 마스크는 **그룹**이 가지므로 PS 에서 후보를 바꿔 켜도 다듬은 마스크가 유지됩니다.
- 출력: `ComfyUI/output/design_patch/<project>/<prefix>_r<rev>.psd` + 같은 이름의 `.json`(매니페스트 스냅샷 + 레이어 목록),
  `_mask.png`(보이는 패치의 최종 마스크 합집합, L). `<project>` 는 프로젝트 폴더 이름입니다.
  바뀐 것이 없고 그 파일을 이 export 가 썼으면(스냅샷 해시 일치) 다시 쓰지 않고 기존 경로를 돌려줍니다.
- **덮어쓰지 않습니다.** 같은 이름의 파일이 이미 있으면(다른 base_dir 의 같은 이름 프로젝트, 지우고 다시 만든 프로젝트, PS 에서 고쳐
  저장한 파일) `<prefix>_r<rev>_2.psd`, `_3` … 으로 씁니다.
- 병합 프리뷰(PSD 를 레이어 없이 여는 프로그램이 보는 그림)는 흰 바탕 위에 보이는 레이어를 같은 순서로 직접 합성해 RLE 로 넣습니다.
- `layer_mode=pixel` 이면 SO 대신 배치된 픽셀 레이어로 씁니다(작고 빠름, PS 에서 재변형 불가).

## M2 — Run (GPT Image 유료 호출)

Prepare 의 `jobs` 를 comfy.org 프록시(`/proxy/openai/images/edits`, 내장 GPT Image 노드와 같은 요청)로 실행합니다.
지키는 두 가지: **돈을 낸 결과를 잃지 않는다**, **같은 호출을 두 번 과금하지 않는다.**

- 호출 단위 = (셀, rep). `call_id = <cell_key>_r<rep>`(rep 은 0부터). 셀 목표 호출 수 = `calls_per_cell` + 보드 재굴림 수.
- 과금 대상 = **보드 재굴림(사전 승인)** + **approve == 지금의 pending_hash 일 때 나머지 전부**. 그 밖에는 과금하지 않습니다.
- pending_hash 는 백엔드(mock / comfy_org)와 승인이 필요한 호출의 `call_id + n` (재시도는 + 시도 번호) 목록의 해시입니다. 그래서
  - 크롭·프롬프트·calls_per_cell 을 바꾸거나, `n` 만 바꾸거나, 실패·orphaned 를 다시 시도하면 해시가 바뀌어
    approve 위젯에 남은 옛 해시로는 과금되지 않습니다(과금 없이 드라이런만 다시 함).
  - MOCK 과 유료를 바꾸면(`_MOCK_API` 파일·환경변수) 해시가 바뀝니다. MOCK 드라이런에서 받은 해시로는 유료 호출이 나가지 않고,
    유료 드라이런을 한 번 더 해 새 해시(버튼 `[MOCK]` 없음 + 확인 창)를 받아야 합니다.
  - 보드 재굴림이 생겨도 사용자가 승인할 해시는 그대로입니다(재굴림은 따로 사전 승인). 재굴림 몫은 재시도로 다시 호출해도
    돌아오지 않습니다(calls_per_cell 을 늘려 생긴 호출은 항상 승인 필요).
- `seed` 라는 위젯은 일부러 두지 않았습니다(프론트가 randomize 를 붙여 큐마다 재과금하는 문제).

### 승인 흐름

1. 워크플로를 큐에 넣으면 Run 은 approve 가 비어 있어 **드라이런**만 합니다: 승인 필요 N건, 예상 USD(내장 노드 가격 배지와 같은 표),
   예상 시간(호출당 91초 기준), `pending_hash`, 신규 사유(새 셀 / 이전 셀 대비 바뀐 항목 / 셀당 호출 수 증가).
2. Run 노드 버튼이 **"승인 후 실행 (N건 ≈ $X)"** 로 바뀝니다. 누르면 확인 창(N건·예상 USD) → approve 위젯 = 해시 →
   **Run 노드만 부분 실행**(Analyze·Export 는 돌지 않음). MOCK 이면 확인 창 없이 `[MOCK]` 표시.
   버튼 승인은 **그 큐 하나에만** 씁니다: 큐에 들어가는 즉시(또는 큐 제출이 실패하면) approve 가 다시 비워집니다. 그 큐를
   지우거나 취소했으면 버튼을 다시 누르세요(나중의 다른 큐가 남은 해시로 과금하지 않음).
3. 실행이 끝나면 보고서에 호출별 결과·크레딧·시간이 나오고, 버튼은 남은 승인분(없으면 "승인할 호출 없음")으로 바뀝니다.
4. 전체 워크플로를 다시 큐에 넣으면 Analyze → Review → Compose → Export 가 새 후보로 돕니다. Run 은 이때 0 호출입니다
   (끝난 호출은 다시 하지 않음).

approve 위젯에 직접 해시를 붙여 넣어도 됩니다(이 경우 비워지지 않습니다 — 다 쓰면 직접 지우세요. 보드 큐 요청은 보낼 때
모든 Run 의 approve 를 비웁니다). 버튼을 누른 뒤 대기 목록이 바뀌었으면(Prepare 위젯 변경 등) 서버가 과금 없이
드라이런만 다시 하고 새 해시를 보여 줍니다.

| 위젯 | 기본 | 의미 |
|---|---|---|
| approve | "" | 승인 해시. 비우면 드라이런 |
| max_new_calls | 64 | 이번 실행에서 새로 과금할 호출 수 상한(넘는 것은 대기로 남음, 보고 `max_new_calls`) |
| concurrency | 4 | 동시 호출 수(1–8) |
| wave_minutes | 25 | 실행 시작 후 이 시간이 지나면 새 발사 중지(진행 중 호출은 받음) |
| retry_failed | False | 실패한 호출 재시도(과금, 새 승인) |
| retry_orphans | False | orphaned 호출 재시도(과금됐을 수 있음, 새 승인) |
| only | "" | 승인이 필요한 호출만 거르는 쉼표 필터: crop_id, NNN(`030`), cell_key 앞 6자 이상, call_id. 승인 해시도 걸러진 목록 기준. 보드 재굴림(사전 승인)·다운로드 복구는 걸러지지 않음 |

### wave 와 자동 이어가기

- comfy.org 로그인 토큰은 오래 가지 않으므로(약 1시간 추정) 한 번의 실행을 `wave_minutes` 로 끊습니다. 셀마다 첫 호출부터
  돌리고(breadth-first) 시간이 되면 새 발사를 멈춘 뒤 진행 중인 것만 받고 끝납니다(보고 "wave").
- Run 노드의 **"자동 이어가기 (wave): 켜짐"** 을 켜 두면, 이 탭에서 넣은 실행이 wave 로 끝나는 즉시 남은 호출의 새 해시를
  자동 승인해 Run 만 다시 큐에 넣습니다(큐마다 새 토큰). 자동 승인은 남은 대기가 **그 실행이 승인받고 남긴 호출과 정확히 같은
  집합**일 때만 합니다(서버가 주는 continuation_hash 와 비교). 승인하지 않은 호출이 섞이거나, 남은 수가 줄지 않거나, 오류·취소·
  Drain·토글 끔이면 멈추고 알립니다. 남은 것이 보드 재굴림(사전 승인)뿐이면 approve 없이 다시 넣습니다.
  꺼져 있으면 알림만 나오고, 끝난 뒤 승인 버튼으로 이어서 실행하면 됩니다.
- 자동 이어가기는 Run 만 큐에 넣습니다. 마지막 wave 뒤에는 전체 워크플로를 한 번 큐에 넣으세요.

### Drain 과 Cancel

- **Drain**(Run 노드 버튼 또는 보드 상단 버튼) = 새 발사만 멈추고, **진행 중인 호출은 끝까지 받아 저장**한 뒤 정상 종료합니다.
  wave 상한 뒤 진행 중 호출을 기다리는 동안 눌러도 보고는 drain 이고 자동 이어가기를 하지 않습니다.
  실행 중이 아닐 때 누르면 아무것도 하지 않습니다(다음 Run 을 막지 않음). 자동 이어가기로 넣은 Run 이 아직 시작 전이면
  Drain 이 닿지 않으므로(알림이 뜸) 큐에서 그 항목을 지우세요.
- **Cancel(인터럽트)** = 진행 중인 호출을 끊습니다. 이미 도착한 결과는 저장하지만 끊긴 호출은 **돈을 냈을 수 있는데 결과가 없습니다**
  (orphaned). 비용을 아끼려면 Cancel 대신 Drain.

### 원장과 orphaned · 실패 · 다운로드 복구

```
<project>/cands/<cell_key>/
  r0.req.json        요청 원문(모델·품질·크기·n·프롬프트·입력 파일/sha·승인 출처·시도 이력)
  r0.inflight.json   API 호출 직전에 기록 → 끝나면 지움
  r0.resp.json       응답 받은 즉시(다운로드 전): URL/b64 여부, 크레딧
  r0_0.png …         원본 바이트 그대로(디코드·재인코드 없음)
  r0.done.json       모든 장 저장 후 / r0.failed.json  실패(오류·HTTP 상태·과금 가능성)
```

- 원장 파일이 진실이고 매니페스트 `calls[]` 는 요약입니다. 매니페스트에 반영되기 전에 꺼졌으면 다음 Run 이 원장에서 되살립니다.
- **orphaned**(inflight 만 남음: 크래시·Cancel): 과금됐을 수 있습니다. comfy.org 사용 내역을 확인하고, 다시 하려면 `retry_orphans` 를
  켠 뒤 새 해시로 승인합니다.
- **응답은 받았는데 다운로드가 안 된 호출**(resp.json 있음)은 다음 Run 이 **다운로드만** 다시 합니다(과금·승인 없음).
  URL 이 영구 실패(만료 서명 URL 400/401/403, 404/410 등 4xx)하면 받은 장만으로 끝내고(partial) 다시 과금하지 않습니다.
  응답이 URL 대신 b64 이면 resp.json 보다 먼저 `r0_<slot>.b64.bin` 사본을 써 두므로 저장 전에 끊겨도 사본에서 복구합니다
  (끝나면 지움). 다운로드 복구는 **응답을 받은 백엔드로만** 합니다 — 지금 백엔드가 다르면(MOCK ↔ 유료) 보고서에 "건너뜀"으로
  남고 그 백엔드로 실행할 때 받습니다.
- **실패**: 한 호출 실패가 나머지를 막지 않습니다. 다시 하려면 `retry_failed`(과금, 새 승인). 이전 시도는 req.json 의 history 에 남습니다.
  입력 이미지를 읽지 못한 실패(요청을 보내지 않음)는 "과금됐을 수 있음" 이 붙지 않습니다.
- **401/402/403**(로그인 만료·크레딧 부족·거부, comfy-api 의 평평한 오류 봉투 포함): 새 발사를 멈추고 그 호출은 시도 전 상태로
  되돌립니다(과금 전 거부 — 새 호출은 대기, retry_failed/retry_orphans 재시도는 원래의 실패/orphaned 그대로). 다시 로그인·충전 후
  큐에 넣으면 같은 해시로 이어집니다.
- 결과 후보: 장마다 `r_<cell_key 12자>_<rep>_<slot>`(다중 타깃이면 `_<tid>`), 크롭 rect 에 배치(quad = rect), pick 아님.

### MOCK 모드 (과금 없는 시험)

- 켜는 법: 환경변수 `BMK_DP_MOCK_API=1`(또는 true/yes/on), 또는 **`<base_dir>/bmk_design_patch/_MOCK_API` 파일**(빈 파일이면 기본,
  JSON 이면 옵션: `{"delay_s": 1.5, "response": "url"|"b64", "fail_plan": {"*": {"error": 500}}}`).
- 결과는 canvas 입력을 요청 크기로 키우고 색조를 돌리고 도형과 "MOCK" 글자를 넣은 PNG 입니다. 보고서·버튼·보드에 MOCK 표시.
- mock URL 은 그 ComfyUI 프로세스 메모리에만 있어 재시작 뒤 "다운로드만 다시" 는 404(partial)가 됩니다.
  유료 응답의 다운로드 복구는 MOCK 을 켠 동안 건너뛰고(보고서 "건너뜀") 유료로 돌릴 때 받습니다 — 반대도 같습니다.
- **유료로 돌리기 전에 `_MOCK_API` 파일·환경변수가 없는지 꼭 확인하세요**(Run 보고서 첫 줄이 `Run [comfy.org(유료)]` 여야 함).

## M2 — Review Board

Review 노드를 실행하면 프로젝트를 보드에 등록하고(키 = 폴더 경로 해시 12자, URL 에 경로를 싣지 않음) 후보 썸네일을 미리 만듭니다.
노드의 **Open Board** 버튼이 별도 창 `/bmk/design_patch/board/?root=<키>` 를 엽니다(두 번째 모니터 권장). 재시작 뒤에도
input 폴더 프로젝트는 `_roots.json` 에서 키를 되살리고, 다른 base_dir 프로젝트는 Review 를 한 번 실행하면 됩니다.

- 왼쪽: 타깃 목록(칩: 후보 수 · 게이트 실패 · ★ · reframed · MOCK · 대기 호출). 가운데 위: **Before | Reference | 현재 후보** 고정 비교,
  아래: 그 타깃의 **모든 후보**를 같은 초점(자동 마스크 bbox 합집합 +20%, 분석 없으면 크롭 전체)으로 확대한 격자.
- 썸네일은 Analyze 와 같은 배치(크롭 rect 로 LANCZOS → 정합 → 톤 보정)라 Before 와 픽셀 정렬됩니다. 보드는 상류 Analyze 설정의
  분석 항목을 씁니다(Review 가 그 설정을 알려 줌).
- 2초마다 바뀐 것만 다시 읽고(`since_rev`), 쓰기 직후 바로 갱신합니다. 이름·라벨은 텍스트로만 넣습니다(레이어명 XSS 안전).

| 키 | 동작 |
|---|---|
| ↑ / ↓ | 타깃 이동 |
| ← / → | 후보 이동 |
| Space | ★ 선택(그 타깃의 pick 을 이 후보 하나로 교체) |
| Shift+Space | slice 추가/제거(★ 여러 개 = Export 에서 slice 별 하위 그룹) |
| X | 탈락 토글(★ 에서도 빠짐. 다시 ★ 하면 탈락 해제) |
| Q (누르는 동안) | Before 로 깜빡임 비교 |
| D | ΔE 히트맵 토글 |
| C | 컨텍스트 뷰(베이스 위에 현재 마스크로 합성한 1.5배 영역) |
| R 두 번(2.5초 안) | **재굴림 +1 호출**(같은 셀, 사전 승인 = 유료) → ComfyUI 탭에 큐 요청 |
| Esc / ? | 보기 초기화·도움말 닫기 / 도움말 |

- **재굴림 흐름**: R R → 매니페스트 `rerolls[셀] += 1` → 보드가 ComfyUI 탭(opener)에 메시지 → 그 탭이 0.8초 모아 **Review 노드만
  부분 실행** → Run 이 사전 승인된 그 호출만 과금 → Analyze → Review(썸네일). Export 는 돌지 않습니다. 버튼으로 누르면 확인 창이
  뜹니다. 셀당 아직 소화하지 않은 대기 호출은 16개까지(넘으면 거부). ComfyUI 탭이 아닌 곳에서 연 보드는 큐를 넣을 수 없어
  "ComfyUI 탭에서 큐" 안내만 합니다(보드 상단 "ComfyUI 큐" 버튼도 같은 요청).
- 보드에서 고른 ★·slice·탈락은 매니페스트에 바로 저장됩니다. 그 결과를 PSD 로 내려면 **전체 워크플로를 큐에 넣으세요**
  (Project 지문이 바뀌어 Compose·Export 가 최신 pick 으로 다시 돕니다).

## M2 — 동시 쓰기와 탈락(rejects)

- 보드 라우트와 Run 은 노드가 실행 중일 때도 매니페스트를 고칩니다. 모든 노드 저장은 **3-way 병합**(노드가 연 시점 ↔ 노드 결과 ↔
  디스크 최신본)이라 그 사이의 ★·탈락·재굴림·호출 결과를 덮지 않습니다. 같은 항목을 양쪽이 다르게 바꾼 드문 경우는 노드 값으로
  저장하고 보고서 끝에 `매니페스트 병합 충돌(이 노드의 값으로 저장): …` 경고를 붙입니다.
- 탈락한 후보는 Analyze(분석)·Compose(합성)·Export(숨김 대안 포함)에서 빠집니다. 탈락을 풀면 다음 Export 가 새 PSD 를 씁니다.
- Candidate In(psd_harvest)의 `visible_is_pick=True` 는 큐마다 수확 후보의 pick 을 PSD 보임 상태로 되돌립니다. 보드에서 **수확 후보**의
  ★ 를 바꿀 거면 `visible_is_pick` 을 끄세요(Run 후보는 영향 없음).
- 잠금은 한 ComfyUI 프로세스 안에서만 유효합니다. 같은 프로젝트 폴더를 두 ComfyUI 에서 동시에 쓰지 마세요.

## 유료 스모크 테스트 (사용자가 직접, 1회 ≈ $0.03–0.05)

처음 실제 호출은 **크롭 1개 · quality low · n 1 · 호출 1건**으로 확인합니다(예상: sunburst/flare low 2048² 입력 1장 $0.029–0.035,
입력 2장 $0.040–0.052 ≈ 6–11 크레딧). 테스트·mock 에서 확인할 수 없었던 것(실제 응답 URL 모양, 크레딧 헤더, 응답 시간)을 봅니다.

1. **준비**
   - ComfyUI 를 **재시작**합니다(Python 변경은 재시작해야 반영, JS 는 새로고침). 시작 로그에 `IMPORT FAILED` 가 없는지 확인.
   - `<input>/bmk_design_patch/_MOCK_API` 파일이 없고 환경변수 `BMK_DP_MOCK_API` 가 없는지 확인.
   - ComfyUI 에 comfy.org 로그인(또는 API 키) 상태이고 크레딧이 있는지 확인. comfy.org 사용 내역 화면을 열어 둡니다.
   - 샘플 워크플로: `guide/bmk_design_patch_smoke_workflow.json` (아래 2의 그래프·위젯 값·안전장치 `max_new_calls=1`·`concurrency=1`·
     `only=030` 과 절차 메모가 들어 있음. 03 PSD·레퍼런스 경로는 `H:\BmkNodeDesign\MultiLayerCropEdit` 기준이라 자료 위치가 다르면 고칠 것).
2. **그래프**: Project(`m2_smoke`) → Import PSD(03 PSD, refs_dir) → Prepare(model `gpt-image-2.5-sunburst`, quality **low**,
   size_rule `user_k`, variants **`V1`**, n **1**, ref_style `at`, calls_per_cell **1**) → Run(**only `030`**, 나머지 기본) →
   Analyze → Review → Compose → Export PSD.
3. **드라이런**: 큐 실행(approve 비움). 확인할 것
   - Run report 첫 줄 `Run [comfy.org(유료)] 드라이런(과금 없음): 승인 필요 1건`, `≈ $0.03…`, `pending_hash xxxxxxxxxxxx`.
   - Run 버튼 `승인 후 실행 (1건 ≈ $0.03…)`(`[MOCK]` 이 붙으면 중단하고 1을 다시 확인).
   - `<input>/bmk_design_patch/m2_smoke/cands/` 에 `r0.req.json` 이 아직 없음.
4. **승인**: Run 의 `승인 후 실행` → 확인 창의 1건·금액 확인 → 확인. Run 만 실행되고 진행 텍스트가 나옵니다(보통 30–90초).
5. **결과 확인**
   - Run report `실행: 발사 1 · 완료 1 · 실패 0`, 크레딧 값, new_candidates 미리보기에 결과 1장.
   - `cands/<cell_key>/` 에 `r0.req.json`·`r0.resp.json`·`r0_0.png`(또는 jpg/webp)·`r0.done.json`, `r0.inflight.json` 은 없음.
   - comfy.org 사용 내역에 1건, 금액이 예상 범위인지. report 의 크레딧과 비교.
6. **재과금 없음 확인**: 전체 워크플로를 한 번 더 큐 → Run `할 일 없음(대기 호출 0)`, 사용 내역에 새 과금 없음.
7. **보드**: Review 의 Open Board → 030 타깃에서 Space(★) → 전체 큐 → Export PSD 에 그 후보가 보이는 SO 로 들어갔는지.
8. (선택, +1건) 보드에서 R R → ComfyUI 탭에 `보드 요청으로 Review 를 큐에 넣었습니다` → Run report `발사 1`(원장 req.json 의
   `approved_by: "board"`), Export 는 돌지 않음.
9. **알려 줄 것**: `r0.resp.json` 의 `data[0].url` 모양(`/proxy/...` 상대 경로인지 서명된 절대 URL 인지)과 `credits` 값, 호출 시간,
   결과 PNG 가 정상인지, 401/402/403 등 오류 문구. 문제가 생기면 Drain 후 report 를 그대로 보내 주세요.

## 제한·주의

- **PS 확인 상태**: SO 레이어 바이트 구조(lnk2 v8 꼬리), 픽셀 PSD, 그리고 실제 노드 출력 PSD(`e2e_r4.psd`: 그룹 마스크,
  정합 affine SO quad, 중첩 그룹, 숨김 대안, RLE 병합 프리뷰)가 Photoshop 2026 에서 정상으로 열림을 확인했습니다(2026-10-07).
- PSD 2GB 한계: 저장한 PSD 가 2GB(2^31-1 바이트)를 넘으면 오류(임베드 외에 SO 캐시·베이스·병합 이미지도 포함, M1 은 PSB 를 쓰지 않음).
  대안 후보가 많으면 `alternates=none` 이나 `layer_mode=pixel`.
- 크롭 소스 합성은 shape 레이어를 그리지 못합니다(034 사례). 다른 blend·효과는 normal 로 합성됩니다(경고).
- 수확 시 같은 원본을 쓰는 SO 가 둘이면 key 에 `-2` 가 붙습니다. `visible_is_pick=False` 면 기존 pick 을 유지합니다.
- 폴더 후보: 파일명이 크롭 이름(가장 긴 일치)이나 유일한 NNN 으로 시작해야 합니다. 타임스탬프 이름은 `mapping_json` 으로 지정하세요
  (`{"2026-09-11_16-59-23_edit_2.png": {"crop": "008_목부분"}}`). 입력 크롭과 픽셀이 같은 이미지는 거부합니다(bypass 통과 의심).
  이번 폴더 후보 중, pick 이 하나도 없는 타깃의 첫 후보를 자동 선택합니다(수확 후보 — PS 에서 숨겨 거절한 것 — 는 건드리지 않음).
- 정합은 05 사용자 보정과 다를 수 있습니다: 007-왼쪽은 사용자가 x −2px 만 고쳤고 자동은 회전 0.9° 까지 보정, 030 은 회전 −1.3° 를 더 넣습니다.
- 매니페스트는 노드 실행 하나 안에서 읽고 → 고치고 → 3-way 병합으로 저장합니다. 두 ComfyUI 인스턴스에서 같은 프로젝트를 동시에 돌리지 마세요.
- (M2) 유료 스모크 테스트(2026-10-07, sunburst low 2048², 입력 1장, n 1) 실측: 1건 17초, 크레딧 4.65(원장 = comfy.org 활동 내역 일치),
  usage 토큰(입력 545 · 출력 397) 기록. 응답은 `storage.googleapis.com` 의 **서명된 절대 URL** 이고 유효 기간은 약 24시간이라,
  다운로드만 다시 하는 복구(`r*.resp.json`)는 24시간 안에만 됩니다. 401/402/403 본문은 아직 실제로 받아 보지 않았습니다.
- Export 의 `skip_empty`(기본 켬): 넣을 후보가 하나도 없으면 PSD 를 쓰지 않습니다(드라이런 직후 전체 큐가 빈 PSD 를 남기던 문제).
  Run 은 ComfyUI 0.39 의 `comfy_api_nodes`(sync_op·다운로드·크레딧 기억)에 결합해 있어 ComfyUI 업데이트 뒤에는 테스트의
  C2(내장 노드와 요청 동등성)·C4(오류 매핑)를 다시 돌려 확인하세요.
- (M2) 보드 썸네일 캐시(`derived/thumbs/`)는 자동으로 지우지 않습니다(지워도 다시 만듦).

## 테스트

```
python_embeded\python.exe -X utf8 -W ignore ComfyUI\custom_nodes\ComfyUI_BMK_Nodes\tools\test_bmk_design_patch.py [--quick]
```

- `--quick`: 보조 모듈 단위 테스트(M1 + M2 store·runner(mock)·board, 약 35초). comfy 를 import 하지 않습니다.
- 전체: 위 + runner 의 comfy 묶음(CPU, 가짜 sync_op) + 참조 폴더 `H:\BmkNodeDesign\MultiLayerCropEdit` 의 03/05 PSD 회귀 + M1 노드 통합
  + M2 노드(합성 프로젝트, mock) + M2 체인(ComfyUI 로더·실행기로 Project → … → Export, mock, 서버 포트 없음).
  **유료 호출은 하지 않습니다**(mock 또는 테스트 안에서만 가짜로 바꾼 sync_op). 출력은 `_proto\m2_test_out\` 아래에만 씁니다.

| 검사 | 내용 | 실측(2026-10-07) |
|---|---|---|
| R1 | 03 크롭 rect == crops_03.json | 22/22 |
| R2 | 크롭 소스 vs 03.크롭이미지 PNG 비트 일치 | 15/19 |
| R3/R4 | 05 수확 / SO 배치 캐시 MAE ≤0.25 | SO 26, quad==rect 20/22 / 19/22 |
| R5 | 정합(001·007-왼쪽·030 적용, 발산 0) | 001 sy 1.035, 030 sy 1.034(손 마스크 제외) |
| R6 | 자동 마스크 soft IoU 평균 | 0.641 (roi 0.685) |
| R7 | 톤 홀드아웃 MAE 비 | 5.01 → 3.45 (0.69) |
| Q | 자동 정합 quad vs 05 사용자 quad 코너 오차 | 001 3.5px, 030 5.1px, 007-왼쪽 6.6px |
| R9 | Compose(hand, 05 quad) vs 05 렌더 작업영역 MAE | 0.200 (목표 ≤1.5) |
| R8 | Export 재열기: SO·크기·quad·그룹 마스크·꼬리 117B | 26/26, verify 문제 0, 병합 프리뷰 = Compose 미리보기(차 0, RLE) |
| N8 | 리뷰 결함 회귀(같은 이름 크롭, 숨김 후보 자동 pick, export 덮어쓰기, 분석 해시·가지, 원근 SO, JPEG SO, 2GB, 워프 등) | 합성 프로젝트로 전부 통과 |
| M2-S | 스레드 경합 4×100(pick vs 낡은 스냅샷 commit) / 폴링 중 저장 | 잃은 갱신 0·충돌 0 / 쓰기 610·읽기 889·실패 0 |
| M2-S | 가격표 = 내장 노드 가격 배지 식(파싱 비교) | 1098/1098 일치 |
| M2-R | runner(mock): 승인 해시·재큐잉 0·재굴림·동시성·500·401·인터럽트·크래시·다운로드 복구·wave·drain | 전부 통과, 내장 노드와 sync_op 인자 동일(C2) |
| M2-B | 보드 라우트·경로 순회·pick/reject/reroll·썸네일 캐시·XSS | 전부 통과 |
| M2-N | 노드(합성, mock): 드라이런 0 호출 → 승인 → 재실행 0, n 변경·재시도는 새 해시, wave 이벤트, 인터럽트, rejects, 병합 충돌 보고 | 전부 통과 |
| M2-C | ComfyUI 실행기 체인(03, only 004·005·030): 드라이런 → 3 호출 → 0 호출 → 안정 → 보드 ★·탈락·재굴림 → Export | 통과. 실행 중 보드 쓰기 406회(Run 중 133, Analyze 중 112) 잃은 갱신 0·충돌 0 |
