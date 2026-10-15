# BMK Design Patch 가이드 (M1 · M2 · M3)

캐릭터 일러스트에서 틀린 디자인(파츠)을 고칠 때 Photoshop 에서 하던 반복 작업을 노드로 옮긴 묶음입니다.
PS 에서 **크롭 사각형과 클린플레이트만 그려 저장**하면, 노드가 PSD 를 직접 읽어 크롭·프롬프트를 만들고 GPT Image 를
돌린 뒤(M2) 정합·톤 보정·마스크를 거쳐 후보를 **풀해상도 Smart Object** 로 넣은 PSD 를 다시 씁니다.
크롭마다 사람이 쓰던 스펙(`specs/<crop_id>.json`)의 초안은 VLM 이 씁니다(M3 Spec Draft).

- 모듈: `bmk_design_patch.py`(노드 10개) + 노드 없는 보조 모듈 `bmk_design_patch_store / _psd / _analysis / _prompt /
  _runner / _board / _vlm.py`, 보드 페이지 `web_design_patch/`, 짝 JS `js/bmk_design_patch.js`
- 명세: `H:\BmkNodeDesign\MultiLayerCropEdit\_proto\M1_SPEC.md`, `M2_SPEC.md`, `M3_SPEC.md`, 근거 보고서: `_proto\reports\final.md`,
  `_proto\design_m3\report_*.md`
- 카테고리 `BMK/Image`, 노드 검색어: `design patch`, `디자인 패치`, `multi layer crop edit`

## 하는 일과 하지 않는 일

| 한다 | 하지 않는다(다음 단계) |
|---|---|
| PSD 에서 크롭 rect·베이스·크롭 그룹 사전 편집 읽기 | VLM 판정(후보 자동 채점·Judge) |
| **VLM 스펙 초안(Spec Draft) — 크롭마다 `specs/<crop_id>.json` 초안, 승인 해시가 맞을 때만 호출, 사람 편집 보존** (M3) | 보드의 스펙 탭(필드별 수락·거절) |
| 크롭별 결정적 프롬프트·입력 평탄화·출력 크기·cell_key 작업 목록 | 가이드 자동 재색, 자동 러프 페이스트 |
| **GPT Image 유료 호출(Run) — 승인 해시가 맞을 때만, 결과는 원장에 먼저 저장** (M2) | PSD → 매니페스트 역동기화(pick·마스크 되돌리기) |
| **Review Board — 타깃별 모든 후보 확대 비교, 키보드 ★·slice·탈락·재굴림** (M2) | 보드의 마스크 편집·순서 탭(v2) |
| 기존 결과 등록(마감 PSD 수확, 폴더) | PSB(2GB 초과) 쓰기 |
| 배치·가드 정합·톤 보정장·자동 마스크 초안·게이트, 미리보기 합성, 풀해상도 SO PSD 출력 | |

## 노드 체인

```
Project ─▶ Import PSD ─▶ (Candidate In) ─▶ (Spec Draft) ─▶ Prepare ─▶ Run ─▶ Analyze ─▶ Review ─▶ Compose ─▶ Export PSD
            (03 PSD)      (수확·폴더,       (VLM 스펙 초안,  (무료:      (유료,   (정합·톤·   (보드    (미리보기   (output/design_patch/
                           선택)            승인제, M3)      jobs·해시)   승인제)  마스크)     등록)    + MASK)     <project>/…psd)
```

| 노드 | 입력(위젯) | 출력 | 하는 일 |
|---|---|---|---|
| Project | project, base_dir(""=input 폴더) | project, summary | `<base_dir>/bmk_design_patch/<project>/` 를 열거나 만듦 |
| Import PSD | psd_path, crop_group, clean_plate_layer, base_layer, crop_pixels_group, refs_dir, glossary_path | project, overview, report | 크롭 rect·베이스·크롭 소스 → `crops[]`. 타깃 = 레이어명 + `specs/<crop_id>.json` 의 `_layer_targets`(채택, 레이어명 문구 우선) |
| Candidate In | source(psd_harvest/folder), path, group, mapping_json, visible_is_pick, keep_hand_masks | project, report | 후보 원본 bytes·배치·손 마스크 등록 |
| **Spec Draft** (M3) | approve, vlm, effort, image_detail, write_mode, only, draft_round, concurrency, credit_cap, keep_loaded, apply, adopt | project, report, sheet | VLM 으로 `specs/` 초안(출력 노드, 매니페스트 안 씀). 기본 apply=targets 는 타깃 영문 이름·bbox 만, adopt 로 제안 타깃 채택. 'VLM 초안 승인' 버튼(JS) |
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
| `\| A: …; B: …` | 타깃(고칠 대상) 목록. 영문이면 프롬프트 이름으로 그대로, 한글이면 용어집으로 번역. **Spec Draft 를 쓰면 한글 짧은 라벨(`\| A: 토스트; B: 빨간 핀`)로 충분**합니다 — 영문 이름은 VLM 이 짓습니다(용어집 불필요, apply=targets 도 같음) |

- `|` 가 없는 예전 이름(`002_토스트-머리장식_right`)은 타깃 A 하나(이름 = 부위)로 봅니다. 이 A 의 영문 이름과 VLM 이 찾은 새 타깃은
  Spec Draft 의 **채택**(adopt → `specs/<crop_id>.json` 의 `_layer_targets`)으로만 들어갑니다 — Import PSD 가 그 문자열을 `|` 뒤에
  적은 것처럼 읽으므로 PS 레이어명을 고치지 않아도 됩니다(「M3 — 타깃만 + 채택」).
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
  specs/<crop_id>.json   사람이 고치는 스펙(선택, M3 Spec Draft 가 초안을 씀)
  specs/_project.json    프로젝트 화풍 style_note(M3) / specs/_backup/  force 백업(하위 폴더라 지문에 안 보임)
  vlm/                   Spec Draft 원장(호출 기록·원문), review.md, last_report.txt (M3, 지우지 않음)
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
  (orphaned). 비용을 아끼려면 Cancel 대신 Drain. 같은 큐의 다른 노드가 실패해 프롬프트가 먼저 끝나도 같은 결과입니다 — 콘솔에
  "Run 실행이 취소됨 … orphaned N건" 경고가 남습니다(승인은 버튼 = Run 만 부분 실행으로).

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
- 레이어명이나 `_layer_targets` 에서 지운 타깃의 후보도 보드·Compose·Export 에서 빠집니다(Compose·Export 는 분석·skip_empty 판단 전에
  빼고 경고, 지운 것이 채택 타깃이면 Import PSD 보고서에도 남은 후보·pick 수). 지운 뒤 도착한 Run 결과도 작업의 타깃 id 그대로 등록되어
  같이 빠집니다(작업의 타깃이 모두 없으면 Run 보고서에 경고). 같은 글자를 다시 쓰면 옛 후보가 그 타깃에 다시 붙습니다.
- Candidate In(psd_harvest)의 `visible_is_pick=True` 는 큐마다 수확 후보의 pick 을 PSD 보임 상태로 되돌립니다. 보드에서 **수확 후보**의
  ★ 를 바꿀 거면 `visible_is_pick` 을 끄세요(Run 후보는 영향 없음).
- 잠금은 한 ComfyUI 프로세스 안에서만 유효합니다. 같은 프로젝트 폴더를 두 ComfyUI 에서 동시에 쓰지 마세요.

## M3 — Spec Draft (VLM 스펙 초안)

크롭마다 VLM 이 `specs/<crop_id>.json` 초안을 씁니다. 입력은 크롭 캔버스(Prepare 와 같은 평탄화), `NNN_` 레퍼런스, 한국어 레이어
라벨, 용어집입니다. Prepare 는 이 파일을 지금처럼 오버라이드로 읽으므로, 초안은 사람이 검토·수정하는 출발점입니다.
지키는 세 가지: **사람이 고친 값은 조용히 덮지 않는다**, **돈은 승인 해시가 맞을 때만 쓴다**, **입력이 같으면 다시 큐에 넣어도 호출 0건**.

- 위치: Import PSD 와 Prepare 사이. 매니페스트는 쓰지 않습니다(rev 그대로). 쓰는 곳은 `specs/` 와 `vlm/` 뿐입니다.
- 호출: 크롭당 1회(단일 패스) + 화풍(style) 1회/프로젝트(apply=full 만). 유료 재질문은 없습니다.
- **apply**: `targets`(기본)는 타깃 목록과 영문 이름만 스펙에 남기고(문장 필드 없음), `full` 은 문장 필드까지 씁니다(참고용 —
  아래 「실측 결과와 백엔드 고르기」의 확인 목록). targets 렌더는 레거시 A·영문/용어집 이름이 있는 타깃이면 레이어명 + 용어집과 같고,
  용어집 없는 한글 라벨 타깃은 VLM 영문 이름입니다(보고서에서 확인). 요청·캐시 키가 같아 full → targets 는 0 호출입니다.
  targets → full 은 같은 키의 크롭이면 원장 초안으로 문장 필드를 다시 채우지만(0 호출), 원장에 화풍 초안이 없으면 "화풍 신규" 1회, 채택 등으로 타깃이
  늘거나 바뀐 크롭은 "타깃 추가"·"타깃 변경" 재초안이 승인 대기(유료)로 올라옵니다.
- 타깃 목록은 매니페스트(레이어명 + 채택한 `_layer_targets`)가 정합니다. VLM 이 목록 밖에서 찾은 틀린 요소는 **제안**으로만 나옵니다.
  보고서 `[제안]` 을 보고 **adopt** 로 채택하거나(아래 「타깃만 + 채택」, PS 왕복 없음), 레이어명을 그 제안대로 고치고 Import PSD 를
  다시 돌리면 새 타깃이 생깁니다(`|` 뒤를 고쳐도 crop_id·스펙 파일은 그대로). full 이면 fill 모드가 그 크롭을 "타깃 추가" 로 다시 초안합니다.
- 예제 워크플로: `guide/bmk_design_patch_draft_workflow.json`(Project → Import PSD → Spec Draft → Prepare → Run → 검토·출력).
  스모크 예제와 달리 **전체 실행 설정**입니다(Prepare max·V1,V4·n4, Run 대기 전부) — 승인 버튼 라벨의 건수·금액을 확인하세요.

| 위젯 | 기본 | 의미 |
|---|---|---|
| approve | "" | 드라이런 보고의 `pending_hash` 와 같을 때만 새 호출. 비우거나 다르면 새 호출 없이 드라이런(원장에 이미 받은 초안은 write_mode 대로 병합·쓰기 — force 는 바로 적용). 버튼이 채우고 큐 직후 비움. 값이 있으면 캐시와 무관하게 매번 실행 |
| vlm | openai/gpt-6-sol | 백엔드·모델. 품질을 더 올릴 때 openai/gpt-6-astra(effort high). `openai/…` = comfy.org 유료(`_MOCK_API`·`BMK_DP_MOCK_API` 가 있으면 mock), `local_qwen/Qwen3-VL-8B-Instruct` = 무료(CUDA GPU, VRAM 19.5 GiB, ComfyUI 모델을 내림), `mock` = 시험 |
| effort | xhigh | 추론 effort(openai 만, 키에 포함). gpt-5.5-pro 는 medium/high/xhigh 만 — 맞지 않으면 큐에 넣을 때 검증 오류(호출 없음) |
| image_detail | high | openai 이미지 detail(키에 포함). `original` 이 거부되면(400, 과금 전) 보고서가 high 로 바꾸라고 안내 |
| write_mode | fill | 아래 「쓰기 모드」 |
| only | "" | 승인 대상 거르기: crop_id, NNN(`030`), `style`(화풍). 무료 복구·원장 캐시 병합에는 적용 안 함. force 는 필수. 걸러진 초안 대기는 보고서 첫 줄 "only 로 보류 N" 과 Run 경고로 남음 |
| draft_round | 0 | 같은 입력으로 다시 초안을 받을 때 올림(키에 포함, seed 아님) |
| concurrency | 4 | openai 동시 호출 수(1–8). local_qwen 은 항상 순차 |
| credit_cap | 0 | 유료 호출 하나를 띄우기 직전 `이번 실행 실측(보고 안 된 것은 단위 최대) + 이 단위의 최대` 가 이 값을 넘으면 그 실행의 새 발사를 멈춤(나머지는 대기). 진행 중 단위들의 최대까지 더해야 넘치면 그것들이 끝날 때까지 기다렸다 다시 판정. **단위 하나의 최대보다 작으면 0건**. 0 = 상한 없음. 아래 「비용」 참고 |
| keep_loaded | False | Qwen 을 실행 뒤에도 VRAM 에 남김(보고서에 상주 경고). 끄고 한 번 실행하면 vlm 이 무엇이든 남은 Qwen 을 내림 |
| apply | targets | 스펙에 쓰는 범위. `targets` = 타깃 영문 이름(레거시 A 제외)·bbox 만, 나머지는 쓰지 않고 노드가 전에 쓴 값은 양보(지움) · 화풍 호출 없음. `full` = 모든 필드(참고용). 키 밖이라 full → targets 는 0 호출. targets → full 은 같은 키 크롭 0 호출 + 원장에 없는 화풍·타깃이 바뀐 크롭은 재초안 대기(유료) |
| adopt | "" | 채택할 크롭: crop_id, NNN, `all`(쉼표). 보고서 `[제안]` 의 제안 타깃을 `_layer_targets` 에 씀(0 호출). **큐 직후 비워지는 1회 명령**(JS) — 쓴 `_layer_targets` 는 남음 |

출력: `project`(rev 그대로 + 가지 설정 `draft` = 대기 수·화풍 대기·해시 — 하류 Run 이 경고에 씀), `report`, `sheet`(크롭마다 캔버스 + 타깃 bbox
A 빨강 · B 초록 · C 파랑 · D 주황 · 제안 회색 점선 · 채택한 타깃은 그 제안의 bbox, 보여 준 레퍼런스 최대 4장 — 고른 것 초록 테두리(full 만)).

### 승인 흐름

1. 워크플로를 큐에 넣으면 Draft 는 approve 가 비어 있어 **드라이런**만 합니다. 보고서 첫 줄 예:
   `Spec Draft [OpenAI gpt-6-astra high · 유료] 드라이런: 승인 필요 6건(신규 5 · 타깃 추가 1) ≈ 1,080 크레딧(최대 3,030) | 캐시 14 · …`
2. 노드 버튼 **"VLM 초안 승인 (N건 ≈ X 크레딧)"** → 확인 창(openai: 보통·최대 크레딧, orphan 재시도 포함 여부 / local_qwen:
   "ComfyUI 모델을 VRAM 에서 내립니다" / mock: 확인 창 없음, `[MOCK]`) → approve = 해시 → **Draft 와 그 조상만 부분 실행**.
   Prepare·Run 은 돌지 않습니다. 승인은 그 큐 하나에만 쓰입니다(제출 즉시 비움). approve 에 해시를 직접 넣고 일반 Queue 를 누르면
   워크플로 전체가 함께 실행되고 approve 는 비워지지 않으니 버튼을 쓰세요.
3. 전체 워크플로를 다시 큐에 넣으면 specs·원장(`vlm/`)의 변경으로 Project 지문이 바뀌어 Prepare 가 새 스펙을 읽습니다. Draft 는
   이때 0 호출이고, 하류 노드는 한 번 다시 확인됩니다(디스크 캐시라 금방 끝남).

- **승인 큐는 항상 실행됩니다.** approve 에 값이 있으면 ComfyUI 캐시를 쓰지 않습니다. 로그인하지 않은 채 승인했거나(0 호출,
  "comfy.org 로그인 필요") VRAM 이 모자랐다면, 로그인·정리 뒤 같은 버튼을 다시 누르면 됩니다(해시가 그대로라 그대로 승인됨).
- **유료 승인 버튼은 사람이 누릅니다.** 자동화·오케스트레이터는 계획(건수·예상 크레딧)을 보고하고 그래프를 준비할 뿐입니다.
- 해시 = `백엔드 + "<crop_id>:<키>:a<시도>"` 목록. mock 드라이런의 해시로 유료 호출은 나가지 않습니다(백엔드가 해시에 들어감).
- **한 큐에 승인 하나**: Draft 승인 큐·Run 승인 큐·자동 이어가기·보드 큐는 다른 유료 노드(Run·Draft)의 approve 를 비우고 넣습니다
  (Run 부분 실행은 조상인 Draft 도 다시 실행하므로).
- 과금됐을 수 있는 실패(orphan·실패·파싱 실패)는 시도 번호가 올라가 **새 승인**이 필요합니다. 과금 전 거부(사다리 밖 400, 401/402/403,
  전송 전 로컬 실패)는 같은 시도로 다시 승인 목록에 오릅니다.
- 승인 없이 하는 일은 셋뿐: 기록된 response id 의 GET 재폴링, 원장 원문 재파싱, 원장 캐시로 병합·쓰기.
- 승인한 Draft 가 Run 부분 큐 안에서 스펙을 쓰면 그 큐의 Run 해시가 어긋나 Run 은 드라이런이 됩니다(의도된 동작). 채택
  (`_layer_targets`)만 쓴 큐도 같습니다 — Prepare 의 채택 가드가 그 크롭의 jobs 를 빼므로 옛 타깃으로 과금되지 않습니다.
- 초안이 대기 중인데 Run 을 승인하려 하면 Run 보고서와 확인 창에 "스펙 초안 대기 N크롭 + 화풍" 경고가 붙습니다(초안 없는 프롬프트로 과금).
  `only` 로 일부만 초안했으면 나머지는 "only 로 보류된 초안 대기 N크롭 + 화풍" 으로 따로 경고합니다(only 를 비우면 승인 목록에 오름).
  `vlm = mock` 으로 승인한 초안은 실제 스펙 파일에 자리표시 문구로 써집니다 — Run 이 유료면 "스펙 N개가 mock 초안" 경고가 붙고,
  Draft 를 실제 vlm 으로 바꾸면 "mock 초안 교체" 로 다시 초안할 수 있습니다. 스펙 파일을 쓰지 못했으면(편집기·백신 잠금) Draft 보고서에 그 크롭이 `[실패] … 쓰지 않음(파일 잠김)` 으로, Run 보고서·확인 창에
  "스펙 파일 N개에 초안·화풍 변경을 아직 못 씀" 경고가 붙습니다. 잠금을 풀고 다시 큐하면 Draft 가 원장으로 다시 씁니다
  (0 호출, `vlm/write_pending.json` 표식이 남아 있는 동안 매 큐 재시도).
- 로그인 정보가 없으면(openai) 보고서에 "comfy.org 로그인 필요" 가 나오고 0 호출입니다.

**캐시 키**(같으면 다시 호출하지 않음) = 보낼 요청 그 자체: 버전, 백엔드, 모델, effort, detail, 프롬프트 지문, 사용자 텍스트(라벨·영문 이름·
용어집 적중·매니페스트 사람 mode·`_hint`·숨긴 레퍼런스·패딩 변·크기), 이미지(역할·이름·원본 픽셀 sha·전송 크기), draft_round.
스펙 내용(`_hint` 제외)·write_mode·only·apply·adopt·동시성·상한은 키 밖이라, 노드가 쓴 스펙이 재호출을 부르지 않습니다.
채택한 `_layer_targets` 는 Import PSD 가 매니페스트 타깃으로 만든 뒤에야 라벨로 키에 들어갑니다.

### 타깃만 + 채택 (apply · adopt)

A/B 결론(아래 「실측 결과와 백엔드 고르기」 — 문장 필드가 결과를 깎음)에 맞춘 기본 사용법입니다. `apply = targets` 는 타깃 목록과
영문 이름만 받고, 목록에 더할 타깃은 사람이 보고서를 보고 고릅니다(제안이 틀리는 경우가 있어 자동 채택하지 않음).

- **targets 가 쓰는 것**: 타깃 `name`(레이어명 영문·용어집 이름이 있거나 레거시 A 면 쓰지 않음), `bbox`(시트용), `_review`·`_draft`.
  mode·action·refs·ref_object·detail·must_not·shape·text·location 등과 크롭 preserve·style_note 는 쓰지 않고, 노드가 전에(full 로) 쓴 값은
  초안 유무와 무관하게 매 실행 지웁니다(사람이 고친 값은 유지). 렌더에 더해지는 것은 이름뿐입니다 — 레거시 A·영문/용어집 이름이 있는
  타깃은 레이어명 + 용어집만 쓴 것과 같고, 용어집 없는 한글 라벨 타깃은 VLM 영문 이름이 렌더됩니다. 이름도 범위를 좁힐 수 있으니
  (한 쌍 → 위쪽 하나) 보고서 크롭 줄의 이름을 확인하세요. 채택하면 타깃이 늘어납니다.
- **대기**: 화풍 단위가 없고(`_project.json` 을 쓰지 않음 — 사람이 쓴 style_note 만 크롭에 퍼짐), "타깃 추가"·"타깃 변경" 은 영문 이름이 없는
  타깃 때문일 때만 대기입니다. 채택한 타깃은 이름이 있어 다시 초안하지 않습니다(크롭당 초안 1회). 채택으로 키가 바뀐 크롭은
  "제안 다시 받기" 로 보고만 됩니다(write_mode=refresh 면 유료 재초안 — targets 에서는 이름·bbox·제안만 바뀜).
- **보고서**: 첫 줄 `Spec Draft [백엔드] [타깃만] …`. 크롭 줄은 타깃 이름과 이름·bbox·구조 플래그만, `기본 설명`·`v4_equals_v1` 은
  기대값이라 숨기고, 레거시는 `A 영문 이름 없음 → adopt 또는 용어집`. 쓰지 않은 문장 필드·신뢰도는 `vlm/review.md` 의
  "참고(적용 안 함)". `[제안]` 묶음(경고와 정상 사이)에 크롭마다 채택할 값·제안 이유·bbox, 끝에 붙여 넣을 adopt 값이 나옵니다.

흐름(레거시 크롭 `003_펜이름표` 하나 — 레이어명에 타깃 문구 없음):

1. 큐 → Draft 드라이런(대기 1) → 'VLM 초안 승인' → 초안: A name badge, 제안 B red pen. 스펙에는 bbox·`_review`·`_draft` 만 써지고
   (레거시 A 이름도 아직 안 씀), 보고서에 `[제안] 003_… adopt 하면 _layer_targets = "A: name badge; B: red pen"` 과
   `→ 채택: adopt 에 003 (전부 = all)`.
2. 보고서·시트를 보고 `adopt = 003` → 큐(0 호출) → `specs/003_….json` 에 `"_layer_targets": "A: name badge; B: red pen"`.
   틀렸으면 이 문자열을 직접 고치거나(예: A 를 `capped red pen` 으로) `; B: …` 를 지웁니다(PS 레이어명과 같은 문법). 이 큐에서는
   Import PSD 가 Draft 보다 먼저 돌았으므로 Prepare 는 그 크롭을 "매니페스트 타깃이 레이어명 + _layer_targets 와 다름" 으로 빼고(jobs 없음) Run 은 드라이런입니다.
3. 다음 큐: specs 가 바뀌어 Import PSD 가 다시 돌고 매니페스트 타깃이 A name badge · B red pen(`origin: "adopt"`) → Draft 0 호출 →
   Prepare 가 두 타깃으로 jobs → Run 드라이런 → 승인.

- **이번 실행에 처음 받은 제안은 채택하지 않습니다**: 승인 큐에 adopt 가 있어도 그 초안의 제안은 보고서로 먼저 보이고
  ("채택 안 함 — 새 제안 …"), 다시 adopt 해야 써집니다. 채택 원천은 이미 스펙에 저장돼 있던 `_draft.suggestions` 입니다.
  그래서 `[제안]` 의 `adopt 하면 …` 값과 adopt 안내도 다음 큐가 읽을 스펙 파일 기준입니다. preview 처럼 쓰지 않은 실행의
  새 제안은 `새 제안 "…" — 쓴 뒤 다시 adopt` 로만 보입니다(이미 `_layer_targets` 가 있는 크롭은 adopt 로 안 바뀌므로
  `노드가 쓴 … 그대로`·`사람이 고친 … 유지` 로 보임).
- **adopt 는 1회용**: JS 가 큐 직후 비웁니다(꺼진 노드·제출 실패·이 Draft 가 빠진 부분 실행 — 다른 가지만 '선택한 출력 실행'·다른 Draft 의
  승인 버튼 — 이면 남김. 다만 Draft 만 입력 오류로 빠지고 다른 출력이 제출된
  큐는 제출 성공이라 비워지니, 보고서에 `채택함` 이 없으면 다시 적으세요). 남겨 두면 그 뒤 큐마다 새로 받은 제안(재초안·새 크롭,
  `all`)까지 검토 없이 채택되기 때문입니다. 비워도 `_layer_targets` 는 스펙에 남습니다(sticky). 되돌리려면 스펙에서 그 키를 지우세요(다음 Import 가 레이어명 타깃으로 되돌림).
  사람이 고치거나 지운 `_layer_targets` 는 다시 adopt 해도 덮지 않습니다(다시 채택하려면 문자열을 직접 씀).
- 채택하는 값 = 제안 레이어명에서 **PSD 레이어명에 문구가 없는 타깃만**(명명 크롭 `| A: …; B: …` 에 제안이 하나면 `C: <제안>`).
  초안 뒤 레이어명에서 지운 타깃은 다시 살리지 않습니다. 새 제안에는 초안 때 스펙 targets·`_draft.labels` 에 남은 지운 타깃의 글자를
  주지 않습니다(옛 사람 값·후보가 붙지 않게). 초안 뒤 PS 에서 같은 글자 타깃을 썼다 지운 경우까지는 못 막으므로, 새로 채택한 글자에
  이미 후보가 있으면 Import PSD 보고서가 `새로 채택한 타깃 X(…) 에 같은 글자의 옛 후보가 있음` 으로 알립니다.
  문구는 name_en → 용어집 → 초안 이름 순이라 vlm·effort·용어집에 따라 다르고, **초안을 받을 때 정해져 저장됩니다**. 그 뒤 용어집·name_en 을
  고쳐도 `[제안]` 문구(레거시 A 포함)는 그대로이니 채택 전에 그 문구를 확인하거나, 제안 다시 받기(write_mode=refresh) 뒤 채택하세요.
  채택하지 않는 경우(보고서 `채택 안 함 — …`): 초안 없음, `_accepted`, 초안 뒤 레이어명이 바뀜(재초안 뒤 채택), mock 초안의 제안을 실제 vlm
  으로 채택, 채택할 제안 없음(타깃 26개 포함), 렌더 점검 실패(쓰지 않음).
  preview 는 "채택 예정" 으로 보고만, force 에 only 가 없으면(idle) 쓰지 않습니다. 어느 크롭과도 맞지 않는 adopt 토큰은 보고서에 알립니다.
- `vlm = mock` 으로 받은 제안을 채택한 `_layer_targets` 는 실제 vlm 으로 바꾸면 노드가 지웁니다(자리표시 이름이 렌더로 새지 않게).
- 채택 타깃을 나중에 지우면 그 타깃의 후보는 보드·Compose·Export 에서 빠지고 Import PSD 보고서에 남은 후보·pick 수가 나옵니다.
  레거시 A 를 채택하면 A 의 뜻이 "부위 전체" 에서 채택 이름으로 바뀌므로, 이미 있는 A 후보는 보고서 안내를 보고 확인하세요.
- `apply` 를 full → targets 로 바꾸면 노드가 쓴 문장 필드를 지울 뿐 0 호출입니다. targets → full 은 같은 키의 크롭이면 원장 초안으로
  문장 필드를 채우지만(0 호출), targets 는 화풍을 받지 않으므로 원장에 화풍 초안이 없으면 첫 full 실행에 "화풍 신규"(프로젝트당 1회)가,
  채택(또는 영문 이름을 적은 레이어명 수정)으로 타깃이 늘거나 바뀐 크롭은 "타깃 추가"·"타깃 변경" 재초안이 승인 대기(유료)로 올라옵니다.

### 쓰기 모드와 사람 편집 보호

| 모드 | 동작 |
|---|---|
| fill (기본) | 아래 「fill 이 다시 초안하는 경우」 만 초안을 받습니다. 키만 바뀐 크롭은 "갱신 가능" 으로 보고만(수락한 초안이 용어집·레퍼런스 변경만으로 조용히 바뀌지 않음) |
| refresh | 키가 바뀐 크롭을 다시 초안하고, 손대지 않은 필드를 새 값으로 |
| preview | refresh 처럼 계산·호출하되 쓰지 않고 diff 만 보고 |
| force | `only` 필수. `specs/_backup/<crop_id>.<시각>.json` 에 백업한 뒤 노드가 소유한 필드를 전부 새 값으로(사람 값 포함). **초안 키마다 1회** |

**fill 이 다시 초안하는 경우**(보고서·승인 목록의 사유)

| 사유 | 언제 | 병합 |
|---|---|---|
| 신규 | 그 크롭에 초안이 없음 | 빈 필드를 채움 |
| 타깃 추가 | 레이어명에 타깃(B, C …)을 더하고 Import PSD 를 다시 돌림 | 새 타깃만 채움, 기존 타깃은 그대로(`_review` 포함) |
| 타깃 변경(A) | 초안 뒤 그 타깃의 **라벨이 바뀜**(레이어명 수정·순서 변경·지운 tid 재사용. 오타 수정도 해당) | 그 타깃만 refresh 규칙 — 손대지 않은 필드는 새 값, 사람이 고친 필드는 유지하고 새 값은 pending |
| _hint 변경 | 스펙의 `_hint` 를 쓰거나 고침 | 그 크롭 전체를 refresh 규칙으로 |
| mock 초안 교체 | mock 으로 쓴 초안이 있는데 지금은 실제 백엔드 | 그 크롭(화풍 포함)을 refresh 규칙으로 |

- 모두 **유료 호출이라 승인이 필요**합니다(사유가 여럿이면 모두 표시). 라벨은 초안 때 `_draft.labels` 에 기록됩니다.
- **force 는 키마다 1회**: force 가 한 번 적용되면 `_draft.forced_key` 에 그 키가 남고, write_mode 가 force 로 남아 있어도
  같은 키로는 fill 처럼 동작합니다(그 뒤의 사람 편집·`_accepted` 를 되돌리지 않음, 새 백업 없음). 다시 force 하려면 `draft_round` 를
  올리세요. `forced_key` 는 apply 를 가리지 않습니다(targets 로 force 한 키는 full 로 바꿔도 이미 force 한 것으로 봄). **원장에 지금 키의 초안(done)이 이미 있으면 force 는 승인 없이 첫 큐에서 바로 적용됩니다**(백업 뒤 사람 값 교체, 0 호출).
  초안이 없는 크롭만 승인 대기(그때까지는 fill)입니다. 적용 전에 바뀔 값을 보려면 force 대신 preview 로 diff 를 보세요(preview 는
  refresh 규칙이라 사람 값은 유지로 표시됨 — force 는 그 유지 행까지 덮습니다).
- **업그레이드 직후 한 번**: 이미 초안된 스펙은 다음 Draft 실행에서 한 번 다시 쓰입니다(정규화 dp-draft-norm/3, `_draft.normalizer`·
  `_review` 갱신). 0 호출이고 파일 시각이 한 번 바뀌어 하류가 한 번 다시 확인됩니다. 그 뒤로는 같은 입력이면 바이트가 그대로입니다.
  fill 에서는 노드가 전에 쓴 값(예: shape locked)이 그대로 남으니, 새 규칙을 값에도 적용하려면 preview 로 확인한 뒤 refresh 로 한 번
  돌리세요(키가 같은 크롭은 원장으로 다시 쓰므로 0 호출).

필드 단위 3-way 병합입니다. `_draft.written` 이 "노드가 지난번에 쓴 값" 이고, 파일 값과 비교해 사람 편집을 알아냅니다.

- **고친 필드**(파일 ≠ 노드가 쓴 값): 유지. 새 초안 값은 `_draft.pending` 에 남습니다(보고서 `=`). force 만 덮음(백업, 키마다 1회).
- **지운 필드**: 다시 채우지 않고 `_draft.deleted` 에 기록(보고서 `-`). force 만 다시 씀.
- **손으로 쓴 스펙**(`_draft` 없음): 빈 필드만 채우고, 다른 값은 pending 으로.
- **양보**(`↓`): 사람 출처가 생겨 노드가 소유권을 잃은 필드는, 노드가 쓴 그대로이면 지웁니다 — `name`(레이어명 영문·용어집 적중이
  생김), `mode`·`refs`(매니페스트에 사람 값이 생김). 매니페스트 타깃의 refs 가 있거나 mode 가 ref_correct 가 아니면 초안은
  mode·refs 를 쓰지 않고 `_draft.suggestions` 로만 냅니다(오버라이드가 매니페스트 값을 가리므로).
- **사람이 고른 레퍼런스가 우선**: 스펙이나 매니페스트에 사람이 refs 를 넣으면, 초안은 mode 를 쓰지 않고(노드가 전에 쓴 mode 는
  양보) 기본 ref_correct 로 그 refs 가 렌더됩니다. VLM 이 self_restore 를 원했으면 `suggestions.mode` 와 플래그
  `self_restore_vs_human_refs` 로만 알려 줍니다. force 는 스펙의 사람 mode·refs 를 지우지 않고 초안 값으로 바꿉니다(매니페스트 값은 유지).
  - VLM 은 스펙에만 적은 refs·6장 밖으로 밀린 refs 를 보지 못합니다(`human_ref_not_shown`). VLM 이 사람 refs 밖의 이미지를 골랐거나
    아무것도 고르지 않았으면, 그 이미지를 보고 쓴 ref_object·ref_ignore·ref_view·detail·must_not 은 렌더하지 않고(`_review.unrendered`),
    글자 정책 exact_ref 는 keep 으로 낮추며, 신뢰 낮음 + `refs_differ_from_human` 으로 알립니다. 이때 프롬프트는 기본 설명
    ("its shape, colors, materials, and surface details")으로 나가니, 필요하면 detail 을 사람 refs 를 보고 직접 쓰세요.
    초안을 받은 뒤 refs 를 고친 경우에도 다음 큐(fill)에서 그 타깃은 refresh 규칙으로 다시 병합되어, 노드가 전에 쓴 ref 필드·detail 이
    지워집니다(사람이 고친 값은 유지). 사람이 mode 를 self_restore 로 정했으면 refs 가 렌더되지 않아 이 규칙은 적용되지 않습니다.
- **제안 레이어명**은 기존 타깃 문구(한글 라벨 포함)를 그대로 두고 새 제안만 붙입니다. 그대로 붙여 넣어도 기존 타깃의 매니페스트
  mode·refs 가 유지됩니다. 단, `|` 가 없는 레거시 레이어명에 붙이면 A 의 라벨이 바뀌어 "타깃 변경(A)" 가 뜹니다.
- `"_hint": "버터는 하나뿐"`(한국어 가능) = 재초안 지시. 키에 들어가 fill 에서도 그 크롭만 다시 초안하고, 그 크롭은 refresh 규칙으로 병합.
- `"_accepted": true` = 확정. force + only 외에는 건드리지 않습니다 — apply=targets 의 양보도 하지 않아 full 로 쓴 문장 필드가 남아
  렌더되고, 채택은 force 여도 하지 않습니다.
- `_review`(타깃마다): confidence, 검토 메모, found, counts, 렌더되지 않아 스펙에 쓰지 않은 값(unrendered), 플래그(side_mismatch,
  count_mismatch, text_contradiction, must_not_in_detail, self_restore_vs_human_refs, refs_differ_from_human,
  human_ref_not_shown, ref_object_singular, add_but_visible,
  truncated_output(잘린 출력 — 마지막 값을 버리고 신뢰 낮음) 등), bbox 출처, shape_suggestion. 크롭 플래그에는 preserve_dropped·
  preserve_in_target 등. `_` 로 시작하는 키는 Prepare 가 조용히 무시합니다.
- **shape 는 초안이 locked 를 쓰지 않습니다**(10/13). VLM 이 locked 를 내면 moderate 로 쓰고 `_review.shape_suggestion: "locked"` 로만
  남깁니다. 모양(실루엣)이 정말 맞고 표면만 틀린 부품이면 사람이 스펙에 `"shape": "locked"` 를 쓰세요.
- 렌더되지 않는 필드는 쓰지 않습니다(예: ref_correct·add 의 must_not, ref_correct 의 occluded_by). 쓰기 전에 실제
  `spec_from_manifest → validate_spec → variants` 로 렌더를 점검하고, 내용이 같으면 파일을 다시 쓰지 않습니다(mtime 그대로 →
  하류 재실행 없음). 렌더가 바뀐 크롭은 Prepare 에서 새 cell_key → Run 재승인이 필요합니다(보고서에 개수 표시).
- 사람 값 때문에 렌더가 깨지는 크롭은 쓰지 않고 "사람 값 때문에 Prepare 가 이 크롭에서 멈춥니다" 로 보고합니다(그 크롭의 diff 는
  세지 않고, adopt 했으면 `채택 안 함 — 렌더 점검 실패(쓰지 않음)`).
- 쓰기는 잠금 안에서 파일을 다시 읽어 병합합니다. 편집기로 파일을 연 채 실행했다면 편집기의 "외부에서 바뀜" 경고를 따르세요.

**화풍 `specs/_project.json`** = `{"style_note": "…", "_draft": {…}}`. 화풍 호출(베이스 작업영역 + 첫 3 크롭)이 한 번 쓰고, 크롭 스펙의
`style_note` 로 퍼집니다(손대지 않은 크롭은 모든 모드에서 프로젝트 값을 따라감). 고치면 다음 실행에서 손대지 않은 크롭에 퍼지고(0 호출),
지우면 그 크롭들에서도 지워지며(렌더러 기본 문구), `""` 로 두면 화풍 초안과 전파를 모두 끕니다. **Draft 가 그래프에 있어야** 퍼집니다
(bypass 하면 전파가 멈춤 — 캐시 적중이면 0 호출이라 늘 두어도 됩니다). apply=targets 는 화풍을 호출·쓰지 않고, `_project.json` 의
style_note 가 사람이 고친 값일 때만 크롭에 퍼뜨립니다(노드가 쓴 화풍은 크롭에서 지움). `_project.json` 을 읽지 못하면(JSON 오류·잠김)
두 모드 모두 크롭 style_note 를 건드리지 않고 보고서에 경고합니다.

### 검토

1. **report**: 나쁜 것부터 `[실패]` > `[신뢰 낮음]` > `[경고]` > `[제안]` > `[정상]`. diff 기호 `+` 새로 씀, `~` 갱신, `=` 사람 값 유지,
   `-` 사람이 지운 필드 존중, `↓` 양보. 타깃별 신뢰도·검토 메모(full) 또는 렌더될 타깃 이름(targets — `[정상]` 도
   `003: A name badge, B red pen`), `[제안]` 의 채택할 값·제안 이유·bbox·adopt 안내(full 은 PS 에
   붙여 넣을 제안 레이어명도), 용어집 제안 JSON.
2. **sheet**: bbox 와 고른 레퍼런스(full)로 위치·좌우 혼동을 빠르게 봅니다.
3. **`vlm/review.md`**: 크롭별 병합된 스펙의 V1 지시문 전문과 diff(specs 밖이라 지문에 영향 없음). `vlm/last_report.txt` = 마지막 보고서.

고칠 때는 `specs/<crop_id>.json` 을 직접 편집합니다. 필드 옆 `_review` 를 근거로 보고, 다시 받고 싶으면 `_hint`, 확정하면 `_accepted: true`.

### 원장과 복구

```
<project>/vlm/<crop_id>/<키>/a<n>.req.json       호출 전 본문(이미지는 vlm/img/<sha12>.png 경로) · 추정 · 전송 단계
                            a<n>.inflight.json  POST 직전
                            a<n>.created.json   POST 응답 즉시(response_id) — 이후 끊겨도 무료 재폴링
                            a<n>.resp.json      폴링 완료(원문·usage·크레딧) / a<n>.done.json  JSON 추출 성공
                            a<n>.failed.json    과금됐을 수 있는 실패 / a<n>.rejected.json  과금 전 거부
<project>/vlm/_style/<키>/…                      화풍 호출
<base_dir>/bmk_design_patch/_vlm_caps.json       모델별 구조화 출력 단계·background·detail 결과, 출력 토큰 보정 기록
```

| 남은 상태 | 다음 실행 |
|---|---|
| done | 호출 없이 정규화·병합만(캐시) |
| created(폴링 중 끊김·401·마감 초과) | **무료 재폴링**(승인 불필요, only 무시) — 다시 큐에 넣기만 하면 됨 |
| resp(추출 전 끊김) | 무료 재파싱 |
| inflight(create 중 끊김 = orphan) | 다음 시도로 승인 목록에 "orphan 재시도(과금됐을 수 있음)" |
| failed(incomplete·refusal·5xx·파싱 실패·폴링 GET 404 등) | 다음 시도로 "재시도(이전: …)" — 새 승인 |
| rejected·req 만 | 같은 시도로 승인 목록에 |

- 원장 파일은 노드가 지우지 않습니다. runner 원장(`cands/`)과 겹치지 않습니다.
- **다시 큐 = 무료 재폴링**: 실행이 끝날 때 created 로 남은 단위가 있으면 `vlm/repoll_pending.json` 을 다시 씁니다. 이 파일과 원장은
  Project 지문에 들어가므로, 다음 큐에서 Draft 가 (ComfyUI 캐시에 걸리지 않고) 다시 실행되어 승인 없이 GET 으로 결과를 받아 병합합니다.
  로그인 전이거나 응답이 아직 끝나지 않았으면 큐마다 이 일이 되풀이됩니다(하류도 매번 다시 확인되지만 0 호출).
- **재폴링 상한**: 처음 호출한 때부터 effort 별 폴링 마감(low·medium 15분, high 20분, xhigh 40분, max 90분)까지는 남은 시간만큼,
  그 뒤에는 큐마다 60초만 기다립니다. 같은 상태에서 진척 없는 재폴링이 3번 이어지면 그 응답을 포기하고 failed(과금됐을 수 있음)로
  끝내며, 다음 승인 목록에 "재시도" 로 올립니다. 로그인 오류(401 등)로 끝난 재폴링은 세지 않습니다. 폴링 GET 이 404 같은 4xx 를
  받으면 그 자리에서 failed 입니다.
- **유료 초안 중에는 Cancel 하지 마세요.** comfy.org 프록시가 `background` 를 받지 않아(실측) create 요청이 생성이 끝날 때까지
  열려 있습니다(크롭당 30초–6.5분). 그 사이 취소하면 created 가 아니라 orphan(과금됐을 수 있음)이 되어, 다시 받으려면 새 승인과
  재과금이 필요합니다. 폴링 중(created 뒤)의 취소만 무료로 이어 받습니다.
  같은 큐의 다른 노드가 실패해도 프롬프트가 끝나며 진행 중인 초안이 취소됩니다 — 그래서 승인은 버튼(Draft 만 부분 실행)으로 하세요.
  그렇게 끊긴 orphan 은 콘솔에 "Spec Draft 실행이 취소됨 … orphan N건" 경고로 남고 다음 드라이런의 "orphan 재시도" 에 오릅니다.
- 구조화 출력은 strict json_schema(L2) → minItems/maxItems 없이(L1) → format 없이(L0) 순서로, 400 이면 같은 승인 안에서 한 단계씩
  내려갑니다(과금 전 거부). 받아들여진 단계만 caps 에 남깁니다(사다리를 내려가다 다른 400 으로 거부되면 caps 그대로).
  응답에 `schema_echo` 가 없으면 프록시가 format 을 버린 것으로 보고 caps 에 L0 을 기록합니다. 실측으로는 strict(L2)가 그대로
  통과하고, `background:true` 만 400 `Unsupported parameter` 로 거부되어 빼고 다시 보냅니다(caps `background: false`).

### 로컬 Qwen (local_qwen)

- 모델 폴더: `ComfyUI/models/LLM/Qwen-VL/Qwen3-VL-8B-Instruct`(bf16 · sdpa, local_files_only — 내려받지 않음).
- **VRAM 19.5 GiB** 가 필요합니다. 순서: 사전 검사(여유 + 내릴 수 있는 ComfyUI 모델 < 19.5 GiB 면 아무것도 내리지 않고 거절) →
  ComfyUI 모델을 VRAM 에서 내림(`free_memory`) → 재검사 → 로드. 모자라면 "VRAM 부족: 필요 19.5GiB, 여유 X + 내릴 수 있는 Y" 로
  보고하고 단위는 대기로 남습니다. CPU 오프로드·nf4 로 자동 전환하지 않습니다(품질 우선).
- 내린 이미지 모델은 다음 이미지 작업 때 다시 로드됩니다. 다른 ComfyUI 프로세스(예: 8189 에서 시험할 때 8188)의 모델은 내릴 수
  없고, Windows 는 OOM 대신 시스템 RAM 으로 넘쳐 둘 다 느려지므로 **시작 전에 nvidia-smi 로 여유 ≥ 20 GiB 를 확인**하세요.
- 실행이 끝나면(0 호출이어도) 내립니다. `keep_loaded` 면 남기며, 이때는 ComfyUI 의 Unload 버튼으로 내려가지 않습니다(끄고 한 번 실행 —
  vlm 을 openai·mock 으로 바꾼 뒤에도 내림). 켜 둔 채 다른 vlm 을 써도 Qwen 이 남아 있으면 보고서에 상주 경고가 붙습니다.
- **CUDA GPU 전용**: ComfyUI 가 `--cpu` 등으로 GPU 를 쓰지 않으면 원장을 쓰기 전에 "local_qwen 은 CUDA GPU 가 필요합니다" 로 멈춥니다.
- 로드·생성은 ComfyUI 실행 루프에서 차례로 합니다(다른 GPU 노드와 겹치지 않음). 그동안 큐의 다른 노드는 기다립니다.
- 파싱 실패, 4096 토큰에서 잘림, 닫히지 않은 JSON 이면 같은 시도 안에서 오류 문구를 붙여 1회 다시 묻습니다(무료). 다시 물어도 잘리면
  "실패" 로 남는데, greedy 라 같은 입력이면 다시 해도 같습니다 — `_hint` 로 줄이거나 openai 로 바꾸세요. 로컬 오류는 과금 없는 실패,
  취소는 같은 시도로 다시 승인 목록에 오릅니다. 승인 해시는 local_qwen 에도 필요합니다(모델 언로드 때문).
- 실측(2026-10-07, named 8 + 화풍): 9단위 생성 합 약 220초(단위 9–51초), 끝난 뒤 VRAM 해제. 품질은 아래 「실측 결과」.

### 비용

- 추정: `입력 = 3,000 + 사용자 텍스트 바이트/4 + Σ ceil(w/32)·ceil(h/32)` 토큰, `출력 = 원장 보정 중앙값(3건 이상) 또는 effort 기본값`,
  최대 = effort 별 출력 한도(low 16k · medium 24k · high 32k · xhigh 64k · max 100k). 크레딧 = USD × 211.
- 처음(보정 기록 없음) 추정 예: gpt-6-astra/high 1크롭 ≈ 165 크레딧, 최대 ≈ 500. 화풍 ≈ 99. sol/low ≈ 10, 최대 ≈ 50.
- **실측**(2026-10-07, 8189):

  | 호출 | 크레딧 | 시간 | 토큰 |
  |---|---|---|---|
  | gpt-6-sol/low 화풍 | 8.7 | 10–17초 | 입력 6.0k · 출력 0.2k |
  | gpt-6-astra/high 크롭 8개 | 43.6–121.5(평균 82.3, 합 658) | 34–390초 | 출력 1.4k–6.2k(reasoning 0.9k–4.9k) |
  | gpt-6-astra/high 화풍 | 49.2 | 20초 | 출력 0.6k |
  | local_qwen 크롭 8 + 화풍 | 0 | 합 220초 | 출력 0.2k–0.9k |

  실측은 처음 추정(보통)의 약 0.42–0.49배였습니다. 유료 done 이 3건 이상 쌓이면 추정이 원장 중앙값으로 보정되어(`_vlm_caps.json`)
  astra/high 크롭의 보통 추정은 약 70 크레딧으로 내려갑니다. "최대"(출력 한도 32k 기준 ≈ 500)는 그대로입니다.
- 크레딧은 comfy.org 의 `x-comfy-credits-used` 헤더(create 응답)에서 읽어 원장에 남깁니다.
- `image_detail=original` 은 받아들여지지만 이 크기(베이스 긴 변 1536)에서는 입력 토큰이 high 와 같아서 기본은 high 입니다.
- `incomplete`(출력 한도 소진)는 **과금된 실패**입니다. 보고서가 effort 를 낮추라고 안내합니다. 추론 토큰은 편차가 커서 "최대" 를 함께 봅니다.
- **credit_cap 쓰는 법**: 유료 호출 하나를 띄우기 직전 `이번 실행 실측 + 이 단위의 최대` 가 cap 을 넘는지 봅니다.
  - 넘으면 그 실행의 새 발사를 멈추고, 남은 단위는 다음 승인 목록에 남습니다. cap 이 단위 하나의 최대보다 작으면 **아무것도 보내지
    않습니다**(astra/high ≈ 500, astra/xhigh ≈ 980, sol/xhigh ≈ 200, sol/low ≈ 50). 이때 보고서가 "credit_cap 을 N 이상으로 올린 뒤
    같은 approve 로 다시 큐" 라고 알려 줍니다.
  - 진행 중인 호출의 "최대"(예약)까지 더해야 넘칠 때는 멈추지 않고, 진행 중인 호출이 끝나 실측으로 바뀔 때까지 기다렸다가 다시
    판정합니다. 그래서 동시 실행 수와 상관없이 **credit_cap ≈ 예상 지출(보통) × 1.6 + 단위 최대** 면 끝까지 돕니다.
  - `실측 + 진행 중 예약 ≤ cap` 이 늘 지켜지므로 cap 은 지출의 확실한 상한입니다(각 호출이 자기 "최대" 를 넘지 않는 한).
  - 크레딧이 보고되지 않은 호출은 0 이 아니라 최대로 셉니다(보고서 "크레딧 미보고 N건").
- 누적은 평가 도구 `budget` 으로 확인합니다. 크레딧 보고가 없는 유료 시도는 보통 추정으로 따로 표시해 더합니다.
- `ComfyUI/temp/api_logs` 에 요청 본문(base64 이미지 포함, 호출당 수 MB)이 그대로 남습니다(끌 수 없음). 디스크를 보고 직접 지우세요.
- 아트 원본은 `store: true` 로 OpenAI 에 갑니다(내장 OpenAI 노드와 같은 경로).

### 실측 결과와 백엔드 고르기 (2026-10-08 채점)

평가 프로젝트 named 8사례(E1–E8)를 지금의 정규화로 다시 채점한 결과입니다(점수 식은 아래 평가 도구).

| 백엔드 | 평균 S | 치명 게이트 통과 | 개수 틀림 | 위치(bbox) 적중 | 크레딧 |
|---|---|---|---|---|---|
| **gpt-6-sol / xhigh (기본)** | **85.1** | 7/8(E6 영수증 B 글자 정책 exact_ref) | E3·E7 | 100% | 219(크롭 8, ≈27/크롭) |
| gpt-6-astra / high (품질 우선) | 84.2 | 8/8 | E3·E4·E7 | 100% | 658(크롭 8, ≈80/크롭) |
| gpt-6-astra / xhigh | 89.1(같은 3사례 astra/high 89.5) | 3/3 | — | 100% | 470(크롭 3, ≈157/크롭) |
| local_qwen(Qwen3-VL-8B) | 71.9 | 7/8(E6 영수증 글자 정책 위반) | E3·E6·E7 | 50% | 0 |
| 레거시 1타깃(`|` 없음) astra/high | 62.7(2사례) | 2/2 | — | 50% | 138(크롭 2) |

- **기본은 openai/gpt-6-sol · xhigh** 입니다(2026-10-13 결정). 평균 점수는 astra/high 와 같은 수준이고 비용은 1/3 입니다.
  다만 E6 영수증에서 글자 정책을 `exact_ref`(레퍼런스 글자 복사)로 낸 일이 있으니 글자가 있는 부품은 `_review` 와 text 를 꼭 보세요.
- **품질을 더 올릴 때는 openai/gpt-6-astra · high** 로 바꿉니다(치명 0, 크롭당 ≈ 80). xhigh 는 같은 사례에서 high 와 점수가 같고 비용만 2배라 권하지 않습니다.
- 같은 입력을 다시 받아도(draft_round) 점수 차이는 1점 미만이었습니다. 레이어명에 타깃(`| A: …`)이 없으면 점수가 크게 떨어집니다(62.7).
- **local_qwen 은 무료 1차 초안·연습용**입니다. 이름·detail 은 쓸 만하지만 bbox·개수·글자 정책이 약하니 `_review` 를 꼭 보세요.
- 둘 다 **작은 부품의 개수**(단추·핀·고리)를 자주 틀립니다. 보고서의 count 플래그와 `_review.counts` 를 확인하고, 틀리면 detail 을 고치거나
  `_hint`("단추는 둘") 로 다시 받으세요. (이 결과로 v2 "작은 타깃 확대 2-pass" 가 후속 과제 후보가 됐습니다.)
- `image_detail` 은 high 로 두세요(original 은 이득 없음).
- **Spec Draft 는 참고용입니다**(D5, 2026-10-13 GPT Image A/B). 같은 크롭을 GPT Image 로 돌려 맹검 비교하면 레이어명 + 용어집만 쓴
  minimal 이 6크롭 중 5크롭에서 이겼습니다(쓸 수 있는 결과 minimal 16/21 · 초안 8/21 · 손으로 쓴 정답 스펙 0/11). 스펙 문장이 틀린
  판단을 굳히면 GPT Image 가 레퍼런스보다 문장을 따릅니다. 그래서 기본 apply 는 `targets`(이름·타깃 목록만, 「타깃만 + 채택」)이고,
  아래 확인 목록은 **apply = full 일 때만** 해당합니다(targets 는 이 필드를 쓰지 않음). full 초안을 Run 에 쓰기 전에 확인하세요(괄호 = 보고서 경고):
  - `shape: locked` — 모양이 틀린 부품이면 결함이 그대로 남습니다. 초안은 이제 locked 를 쓰지 않으니 사람이 쓴 locked 만 보면 됩니다.
  - `preserve` — 고쳐야 할 부분(예: 고리 안의 어두운 사각형)이 들어 있지 않은지(`preserve_in_target`). 들어 있으면 그 항목을 지우세요.
  - `ref_object` — 타깃이 여러 개(한 쌍)인데 하나만 가리키지 않는지(`ref_object_singular`). "the upper red pen" 이면 아래 핀은 안 바뀝니다.
  - `action: add` 와 location — 캔버스에 이미 있는 부품이면 `fix`(`add_but_visible`). 문장 위치가 틀리면 엉뚱한 자리에 그립니다.
  - `detail` — 레퍼런스에 없는 부품(클립 등)을 적지 않았는지(경고 없음 — 직접 보세요).
  - 10/13 전에 쓴 초안 파일은 fill 에서 그대로입니다. 새 규칙을 적용하려면 write_mode `preview` 로 diff 를 본 뒤 `refresh` 로 한 번
    돌리세요(키가 같은 크롭은 원장으로 다시 쓰므로 0 호출, 키가 바뀐 크롭은 재초안 승인 목록에 오릅니다).

### 평가 도구 `tools/eval_bmk_design_patch_vlm.py` (호출 0건)

`_proto\vlm_eval\cases.json`(E1–E8)을 기준으로 원장 done 을 **지금의 정규화로 다시 돌려** 채점합니다.
`S = 100·(0.35·게이트 + 0.25·키워드 + 0.15·개수 + 0.10·bbox + 0.15·형식)`, 치명 게이트(렌더 예외·CJK·텍스트 정책 위반)면 S ≤ 50.

```
eval_bmk_design_patch_vlm.py setup --named <root> --legacy <root>     평가 프로젝트 정리(크롭 거르기·03 PNG 캔버스·합성 레이어명)
eval_bmk_design_patch_vlm.py validate                                  정답 스펙 채점(≥90 이어야 채점기 정상)
eval_bmk_design_patch_vlm.py score --root <root> --select model=gpt-6-astra,effort=high,round=0 --tag iiia
eval_bmk_design_patch_vlm.py compare iiia iiic                         사례 대응 표·평균 차·SE
eval_bmk_design_patch_vlm.py determinism --root <root> --select backend=local_qwen [--rounds 0,1]
                                                                       draft_round 0 과 1 의 정규화 결과를 크롭별로 비교(다르면 exit 1)
eval_bmk_design_patch_vlm.py budget --roots <root> … [--stage 1355]    누적 크레딧·남은 예산·단계 게이트·credit_cap(옛 식 — 「비용」 참고)
eval_bmk_design_patch_vlm.py ab-setup --from <named root> --draft-tag iiia
eval_bmk_design_patch_vlm.py ab-sheet --roots <ab_minimal> <ab_draft> <ab_gold>
eval_bmk_design_patch_vlm.py ab-tally                                  ab/scores.csv(사람 순위) → Borda·승수·쓸 수 없음·D5
```

### 실측 체크리스트 (8189, 총 10,000 크레딧 상한)

공통 조건(단계마다):
- [ ] 8189 만 씁니다(로컬 단계 `comfy-test-8189-gpu`, 유료 단계 `comfy-test-8189`). 8188 은 쓰지 않습니다.
- [ ] 유료 단계: 사용자가 8189 프론트에서 comfy.org 에 로그인 → 보고서 첫 줄이 `[OpenAI … · 유료]` 이고 `BMK_DP_MOCK_API`·`_MOCK_API`
      가 없음을 확인 → **사용자가** 승인 버튼을 누릅니다.
- [ ] `budget --roots … --stage <단계 예상>` 으로 게이트 확인: **누적 실측 + 1.5 × 단계 예상 ≤ 9,700** 일 때만 시작. 넘으면 크롭 수를 줄이고,
      그래도 넘으면 멈춥니다. 노드 `credit_cap` = min(1.6 × 단계 예상 + 단위 최대, 9,800 − 누적)(「비용」의 credit_cap 규칙).
- [ ] 단계가 끝나면 실측 크레딧·결과를 보고합니다.

진행(2026-10-08): 누적 유료 ≈ 725 크레딧. 실측·판정은 위 「비용」·「실측 결과와 백엔드 고르기」와 M3_SPEC §11.4. 남은 단계의 credit_cap 은
ii-3 의 r ≈ 0.42 를 반영해 다시 계산한 값입니다(concurrency 4, iii-c 만 2).

| [ ] | 단계 | 내용 | 호출 | 예상 크레딧 | credit_cap |
|---|---|---|---|---|---|
| [x] | 0 | mock E2E: 합성 프로젝트에서 Draft(드라이런 → 승인 → 쓰기 → 재큐 0 호출) → Prepare → Run(mock) → Review, JS 버튼·확인창 수동 확인 | 0 | 0 | — |
| [~] | i | local_qwen: nvidia-smi 여유 ≥ 20 GiB 확인(필요하면 사용자가 8188 모델 Unload). named 8 + 화풍 1 + legacy 4, preview. draft_round=1 로 한 번 더(결정성 — `determinism`). `score` → **named 8 + 화풍만 함**(220초, S 71.9). legacy·round 1 남음 | 26(무료) | 0 | — |
| [x] | ii-1 | 스모크: gpt-6-sol/low, detail high, `only=style`, named, preview → background 400 거부(빼고 재전송), strict 통과, credits 헤더는 create 에. 8.70 · 17초 | 1 | ≈10 | (당시 50 — 지금 규칙이면 단위 최대 53 이라 ≥ 70) |
| [x] | ii-2 | 같은 호출을 detail original 로 → 받아들였지만 input_tokens 6,011 = high → D4 불충족, high 유지. 8.66 · 10초 | 1 | ≈15 | (당시 50) |
| [x] | ii-3 | astra/high, `only=002_388984`(E1 named), fill → 70.49 · 76초 · reasoning 1,552, create 가 완료까지 열림. **r = 70.5/167 ≈ 0.42** | 1 | ≈180 | 600 |
| [ ] | ii-4 | 복구 확인(재폴링 GET 과금). background 가 거부되어 정상 흐름에는 GET 이 없음 — 필요할 때만 | 0 | 0 | 0 |
| [x] | iii-a | astra/high, named 나머지 7 + 화풍, fill → 크롭 8 합 658.2 + 화풍 49.2, S 84.2 · 치명 8/8 | 8 | ≈1,355 | (당시 2,200) |
| [x] | iv-a | GPT Image A/B(quality high, V1, n4): {minimal, draft} × E1·E2·E4·E5·E6·E8 + gold(E1·E2·E5), Analyze 까지. `ab-sheet` 로 사용자 맹검 → 1,710, 크롭 승 minimal 5 · draft 1 → D5 참고용 | 15 | ≈2,500 | (Run, only) |
| [~] | iii-b | astra/high, legacy 4, preview → 2건 137.8(나머지 2건은 당시 credit_cap 예약 규칙으로 보류), S 62.7 | 4 | ≈300 | 1,000 |
| [~] | iii-c | astra/xhigh, named 다중 타깃 4, preview → 3건 469.6(1건 보류), S 89.1 = high 와 동점 → D1: high 유지 | 4 | ≈560 | 2,100 |
| [x] | iii-d | sol/xhigh, named 8, preview → 218.7, S 85.1 · 치명 7/8 → D2: 기본을 sol/xhigh 로(사용자 결정) | 8 | ≈220 | 900 |
| [ ] | iv-b | GPT Image quality max, n2: {minimal, draft} × E1·E5 | 4 | ≈1,180 | (Run) |
| [x] | iii-e | astra/high, draft_round=1, E1·E5 → 189.4, S 91.2(round 0: 91.7) — 반복 분산 작음 | 2 | ≈150 | 1,100 |
| | 합계(계획) | | | **≈8,170** → 실측 반영 남은 예상 ≈4,900 | 상한 10,000 |

- 순서: i → ii → iii-a → iv-a → iii-b → iii-c → iii-d → iv-b → iii-e. ii-3 뒤 r 로 다시 정합니다: r ≤ 1.3 표대로 /
  1.3 < r ≤ 2.0 iii-c 를 E1·E5 로 줄이고 iii-e 빼기 / r > 2.0 iii-c 빼기, iii-b 를 E1L·E5L 로, iv-b 빼기.
- 판정(유료 실행 전 고정): **D1** iii-c 4사례 평균 S(xhigh) − S(high) ≥ 3 이고 iii-e 반복 최대 |ΔS| 보다 크거나, 치명 게이트 실패가
  줄면 기본 effort 를 xhigh 로. **D2** 평균 S_sol ≥ S_astra(8사례)일 때만 sol 기본(비용은 기준 아님). **D3** iii-a 에서 개수 틀림이
  E3–E7 중 2건 이상이거나 found 타깃 bbox 적중률 < 70% 면 v2 "작은 타깃 확대 2-pass". **D4** ii-2 가 original 전달을 확인하고 400 이
  없으면 image_detail 기본 original(v1.1). **D5** draft 군이 Borda 로 6크롭 중 4 이상에서 minimal 을 이기고 "쓸 수 없음" 이
  minimal 보다 많지 않으면 이 가이드에 "초안 기본 사용", 아니면 "참고용" + 원인 조사.
- 합격선(iii-a): 치명 게이트 통과 ≥ 7/8, 평균 S ≥ 70.
- 판정 결과(2026-10-08): 합격선 통과(8/8, 84.2). D3 충족(개수 틀림 E3·E4·E7) → v2 "작은 타깃 확대 2-pass" 후보. D4 불충족 → high 유지.
  D1: xhigh 이득 없음 → high 유지. D2: 기본 sol/xhigh(10/13 사용자 결정). D5(10/13): 불충족 → Spec Draft 는 "참고용"
  (위 「실측 결과와 백엔드 고르기」, 원인은 M3_SPEC §11.4).

### 제한과 후속 과제

- 단일 패스만 있습니다. bbox 는 시트·검토용이고 렌더·cell_key 에 쓰이지 않습니다. 작은 타깃의 개수·좌우·가짜 글자 오류는
  counts·side·CJK 플래그와 `_review.note` 로 알려 줄 뿐 고치지 않습니다.
- 렌더러 `dp-prompt/1` 의 한계는 그대로입니다(모든 cell_key 가 바뀌는 dp-prompt/2 에서 함께 고칠 것): 레퍼런스 둘 + ref_view different 의
  `@image2 and @image3 shows`, style_note 마침표·대시, add·paste·self_restore 의 must_not 미렌더, ref_correct 의 occluded_by,
  ref_correct 아래 relocate/replace(fix 로 렌더 — `action_renders_as_fix` 플래그), 공유 레퍼런스의 소유자별 ref_ignore, location 빈 값 기본문.
- Prepare 는 깨진 스펙 JSON 을 크롭 단위로 건너뛰지 못합니다(Draft 는 그 크롭을 건너뛰고 보고).
- 보드에는 스펙 탭이 없습니다(specs 를 보드가 읽지 않음). 용어집 제안 병합 도구, suggested_mode 에 따른 자동 페이스트·가이드,
  bbox 를 SAM3·Analyze ROI 로 쓰기, Judge 노드, Qwen native 경로·nf4 비교는 후속입니다.
- 사라진 크롭의 스펙 파일은 지우지 않고 보고만 합니다. 파일이 잠겨 못 쓴 크롭은 다음 실행에서 원장으로 다시 씁니다(0 호출).
- comfy.org 프록시가 `background` 를 받지 않아 유료 초안은 create 하나가 끝까지 열려 있습니다(위 「원장과 복구」의 Cancel 주의).
- credit_cap 은 단위 "최대" 로 예약해 보수적입니다(예약 때문에 넘치면 기다렸다 실측으로 다시 판정).
- 탭을 바꾸거나 새로고침하면 Run·Spec Draft 버튼이 드라이런 보고를 잊고 "누르면 드라이런부터" 로 돌아갑니다. 그때 누르면 그 노드만
  드라이런으로 다시 큐에 넣고(모든 approve 를 비우므로 과금 없음), 라벨이 바뀌면 다시 누르면 됩니다.
- 정규화가 고치지 못하는 문장이 남을 수 있습니다(예: "on it" 같은 위치는 비워져 기본문, "X of <타깃>" 꼴 preserve 는 버려짐).
  `vlm/review.md` 의 V1 전문과 보고서의 lint 플래그로 확인하세요.
- 로그인하지 않은 채 created 가 남아 있으면 큐마다 Draft 와 하류가 다시 실행됩니다(0 호출). 로그인하면 이어 받습니다.
- 비공개 API 결합: `sync_op_raw` 키워드·`client._get_remembered_credits_used`·runner 의 `_CallHidden`·`_request_error`·인터럽트 보조.
  ComfyUI 업데이트 뒤에는 테스트의 M3-C1·C2 와 M2 C* 를 다시 돌리세요.

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

- `--quick`: 보조 모듈 단위 테스트(M1 + M2 store·runner(mock)·board + M3 vlm(V1–V25, mock·가짜 api·가짜 Qwen libs) + 평가 도구
  자기 시험 E0–E4 + JS 시나리오, 약 55초). comfy 를 import 하지 않습니다.
- 전체: 위 + runner·vlm 의 comfy 묶음(CPU, 가짜 sync_op / 내장 OpenAIChatNode 요청 모양) + 참조 폴더 `H:\BmkNodeDesign\MultiLayerCropEdit`
  의 03/05 PSD 회귀 + M1 노드 통합 + M2 노드(합성 프로젝트, mock) + M3 Spec Draft 노드(N1–N19) + M2 체인(ComfyUI 로더·실행기로
  Project → … → Export, mock, 서버 포트 없음). 2026-10-13(타깃만·채택 리뷰 수정 뒤): `--quick` PASS 2253, 전체 PASS 3037 · FAIL 0(약 4분 10초).
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
| M2-R | runner(mock): 승인 해시·재큐잉 0·재굴림·동시성·500·401·인터럽트·작업 취소(orphaned 경고 로그)·크래시·다운로드 복구·wave·drain·지운 타깃의 결과는 작업 tid 그대로(R15) | 전부 통과, 내장 노드와 sync_op 인자 동일(C2) |
| M2-B | 보드 라우트·경로 순회·pick/reject/reroll·썸네일 캐시·XSS | 전부 통과 |
| M2-N | 노드(합성, mock): 드라이런 0 호출 → 승인 → 재실행 0, n 변경·재시도는 새 해시, wave 이벤트, 인터럽트, rejects, 병합 충돌 보고, 채택 `_layer_targets`(Import 병합·캐시 경로 rev 불변·스펙 오류·Prepare 가드·Compose·Export 의 없는 타깃 제외(분석·skip_empty 전), N10) | 전부 통과 |
| M2-C | ComfyUI 실행기 체인(03, only 004·005·030): 드라이런 → 3 호출 → 0 호출 → 안정 → 보드 ★·탈락·재굴림 → Export | 통과. 실행 중 보드 쓰기 406회(Run 중 133, Analyze 중 112) 잃은 갱신 0·충돌 0 |
| M3-V | vlm: 키 민감도·프롬프트 지문·스키마·openai 본문·정규화(N0–N15)·3-way 병합 표×4모드·렌더 왕복 8크롭·원장 7행·승인 해시·400 사다리·Qwen VRAM 검사·credit_cap(미보고 = 단위 최대) + 리뷰 회귀(force 1회·타깃 변경·mock 화풍·폴링 404·caps 실패·잘린 출력·린트·드라이런 뒤 타깃 추가·force 의 only 보류·작업 취소 orphan 로그) + 타깃만·채택(V25: 양보 표·왕복 고정점·대기 판정·adopt 토큰·채택 값·거절·sticky·mock 채택 양보·보고서 [타깃만]/[제안] + 리뷰 회귀: 지운 타깃 안 살림·지운 글자 건너뜀·채택 값은 쓴 파일 기준) | V1–V25 |
| M3-E | 평가 도구: 정답 스펙 validate, setup(합성 매니페스트), mock 원장 재정규화 채점 결정성, 토큰 단위 일치(E0), determinism·입력 바뀜·stale, ab-sheet 라벨 고정·Borda(E4) | E1 97.6 · E2 97.1 · E5 96.0 · E6 100.0 |
| M3-N | Spec Draft 노드(mock): 드라이런 0 호출 → 승인 → 같은 위젯 0 호출(바이트·mtime·rev 불변), 사람 편집 보존, 인증 없음 0 호출, 인터럽트 → 무료 재폴링, Run 경고("N크롭 + 화풍"), 화풍 전파, local_qwen VramError·상주 언로드, force 1회(N11), fill 키 가드(N12), 실제 PromptExecutor 로 캐시·재폴링·재승인(N13), 타깃 추가 고정점(N14), 스펙 쓰기 실패 재시도·Run 경고(N15), only 보류·mock 초안 스펙 Run 경고(N16), VALIDATE_INPUTS·첫 await(N1), 얇은 bbox 시트·개요(N2), apply=targets 흐름(화풍 없음·렌더 = 초안 전과 같음·full → targets 양보, N17), 채택 흐름(새 제안은 다음 큐·adopt → Import → Prepare 가드·jobs·시트, N18), 채택 큐의 Run 은 드라이런(N19), JS adopt 1회용(J8) | N1–N19 |
