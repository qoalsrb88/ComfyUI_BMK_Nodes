# BMK Design Patch 가이드 (M1)

캐릭터 일러스트에서 틀린 디자인(파츠)을 고칠 때 Photoshop 에서 하던 반복 작업을 노드로 옮긴 묶음입니다.
PS 에서 **크롭 사각형과 클린플레이트만 그려 저장**하면, 노드가 PSD 를 직접 읽어 크롭·프롬프트·정합·톤 보정·마스크를 만들고
후보를 **풀해상도 Smart Object** 로 넣은 PSD 를 다시 씁니다.

- 모듈: `bmk_design_patch.py`(노드 7개) + 노드 없는 보조 모듈 `bmk_design_patch_store / _psd / _analysis / _prompt.py`
- 명세: `H:\BmkNodeDesign\MultiLayerCropEdit\_proto\M1_SPEC.md`, 근거 보고서: `_proto\reports\final.md`
- 카테고리 `BMK/Image`, 노드 검색어: `design patch`, `디자인 패치`, `multi layer crop edit`

## M1 이 하는 일과 하지 않는 일

| 한다 | 하지 않는다(M2 이후) |
|---|---|
| PSD 에서 크롭 rect·베이스·크롭 그룹 사전 편집 읽기 | GPT 유료 호출(Run 노드) |
| 크롭별 결정적 프롬프트·입력 평탄화·출력 크기·cell_key 작업 목록 | Review Board, VLM 스펙 초안·판정 |
| 기존 결과 등록(마감 PSD 수확, 폴더) | 가이드 자동 재색, 자동 러프 페이스트 |
| 배치·가드 정합·톤 보정장·자동 마스크 초안·게이트 | PSD → 매니페스트 역동기화(pick·마스크 되돌리기) |
| 미리보기 합성, 풀해상도 SO PSD 출력 | PSB(2GB 초과) 쓰기 |

M1 은 **유료 API 를 부르지 않습니다.** 후보는 이미 있는 결과(05 PSD 의 SO, 04 폴더의 PNG)를 등록해 씁니다.

## 노드 체인

```
BMK Design Patch Project ─▶ Import PSD ─▶ Candidate In ─▶ Prepare ─▶ Analyze ─▶ Compose ─▶ Export PSD
   (project, base_dir)       (03 PSD)      (05 PSD 수확     (무료:      (정합·톤·   (미리보기   (output/design_patch/
                             overview      또는 폴더)        jobs 기록)   마스크)     + MASK)      <project>/…psd)
```

| 노드 | 입력(위젯) | 출력 | 하는 일 |
|---|---|---|---|
| Project | project, base_dir(""=input 폴더) | project, summary | `<base_dir>/bmk_design_patch/<project>/` 를 열거나 만듦 |
| Import PSD | psd_path, crop_group, clean_plate_layer, base_layer, crop_pixels_group, refs_dir, glossary_path | project, overview, report | 크롭 rect·베이스·크롭 소스 → `crops[]` |
| Candidate In | source(psd_harvest/folder), path, group, mapping_json, visible_is_pick, keep_hand_masks | project, report | 후보 원본 bytes·배치·손 마스크 등록 |
| Prepare | model, quality, size_rule, variants, n, ref_style | project, job_sheet, report | 스펙 → 평탄화 입력 → 프롬프트 → `jobs[]` (호출 없음) |
| Analyze | register, tone, tone_sigma, dE, grow, feather_sigma, roi_prior, only_picked | project, contact_sheet, report | 정합·자동 마스크·톤 보정장·게이트 → `analysis[]` |
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
- cell_key = 모델·품질·크기·프롬프트·입력 픽셀 해시·템플릿 버전·변형. `n` 은 넣지 않아 4→8 로 바꿔도 같은 키입니다.
- report 의 **승인 해시**는 대기 작업 집합의 해시입니다(M2 Run 이 이 값이 맞을 때만 과금하도록 할 예정).

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

## 제한·주의

- **PS 확인 상태**: SO 레이어 바이트 구조(lnk2 v8 꼬리)와 픽셀 PSD 는 Photoshop 2026 에서 열림을 확인했습니다. 그룹 마스크,
  회전/affine SO, 투명도 채널 있는 픽셀 레이어, 중첩 그룹은 psd_tools 재열기까지만 확인했고 PS 에서는 아직 열어 보지 않았습니다.
  `_proto\m1_test_out\psd\rw_full_so.psd` 와 노드 출력 PSD 를 PS 에서 한 번 열어 확인해 주세요.
- PSD 2GB 한계: 저장한 PSD 가 2GB(2^31-1 바이트)를 넘으면 오류(임베드 외에 SO 캐시·베이스·병합 이미지도 포함, M1 은 PSB 를 쓰지 않음).
  대안 후보가 많으면 `alternates=none` 이나 `layer_mode=pixel`.
- 크롭 소스 합성은 shape 레이어를 그리지 못합니다(034 사례). 다른 blend·효과는 normal 로 합성됩니다(경고).
- 수확 시 같은 원본을 쓰는 SO 가 둘이면 key 에 `-2` 가 붙습니다. `visible_is_pick=False` 면 기존 pick 을 유지합니다.
- 폴더 후보: 파일명이 크롭 이름(가장 긴 일치)이나 유일한 NNN 으로 시작해야 합니다. 타임스탬프 이름은 `mapping_json` 으로 지정하세요
  (`{"2026-09-11_16-59-23_edit_2.png": {"crop": "008_목부분"}}`). 입력 크롭과 픽셀이 같은 이미지는 거부합니다(bypass 통과 의심).
  이번 폴더 후보 중, pick 이 하나도 없는 타깃의 첫 후보를 자동 선택합니다(수확 후보 — PS 에서 숨겨 거절한 것 — 는 건드리지 않음).
- 정합은 05 사용자 보정과 다를 수 있습니다: 007-왼쪽은 사용자가 x −2px 만 고쳤고 자동은 회전 0.9° 까지 보정, 030 은 회전 −1.3° 를 더 넣습니다.
- 매니페스트는 노드 실행 하나 안에서 읽고 → 고치고 → 저장합니다. 두 ComfyUI 인스턴스에서 같은 프로젝트를 동시에 돌리지 마세요.

## 테스트

```
python_embeded\python.exe -X utf8 -W ignore ComfyUI\custom_nodes\ComfyUI_BMK_Nodes\tools\test_bmk_design_patch.py [--quick]
```

- `--quick`: 보조 모듈 단위 테스트만(약 15초).
- 전체(약 4분): 참조 폴더 `H:\BmkNodeDesign\MultiLayerCropEdit` 의 03/05 PSD 로 회귀 + 노드 통합. 출력은 `_proto\m1_test_out\` 아래에만 씁니다
  (노드 프로젝트와 PSD 는 `m1_test_out\nodes\`).

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
