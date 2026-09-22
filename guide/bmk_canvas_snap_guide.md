# BMK Canvas Snap 가이드

임의 해상도 원본을 **잘라내지 않고** GPT Image 2.5 의 출력 제약(16px 배수, 최대 3,840px, 종횡비 1:3~3:1,
총화소 예산)에 맞는 캔버스로 만든 뒤, 편집 결과에서 **추가한 여백만** 되돌리는 노드 묶음입니다.
모듈: `bmk_canvas_snap.py`. 설계 문서: `260922_GPTImage_CanvasSnap/ComfyUI_GPTImage_CanvasSnap_Handoff_2026-09-22.md`.

## 노드

| 노드 | 역할 |
|---|---|
| BMK Canvas Snap Prepare | 캔버스 계산 → 원본 전체 리샘플 → 여백 추가. `width`/`height` 를 INT 링크로 GPT 노드에 넘김 |
| BMK Canvas Snap Restore | `canvas_plan` 좌표로 여백만 크롭. 결과 크기가 계획과 다르면 중단 |
| BMK Canvas Plan From JSON | `plan_json` 문자열 → `canvas_plan`. 저장해 둔 결과를 다른 세션에서 크롭할 때 |

## 기본 배선 (예제: `guide/bmk_canvas_snap_example_workflow.json`)

```
Load Image ─IMAGE─▶ Prepare ─canvas_image──▶ OpenAI GPT Image 2.5 (size=Custom) ─IMAGE─▶ Restore ─image─▶ Save Image
                       │  width  ──────────▶   model.custom_width                          ▲
                       │  height ──────────▶   model.custom_height                         │
                       └─ canvas_plan ─────────────────────────────────────────────────────┘
                       └─ report ──▶ Preview Any (계산 요약 확인)
```

- GPT 노드의 `size` 는 **Custom** 으로 두고, `custom_width`/`custom_height` 는 위젯 값을 복사하지 말고 **링크**로 받습니다.
- 처음에는 GPT 노드를 빼고 Prepare → Restore 를 직접 연결해 기하 왕복이 맞는지 먼저 확인하세요.
- 편집 프롬프트에는 "캔버스 전체 크기 유지, 원본 사각형의 위치·크기 유지, 추가 여백 유지, 줌·재구도 금지" 를 포함하는 것을 권장합니다. 정렬을 보증하는 것은 아닙니다.

## 계산 규칙

1. 후보 캔버스 W×H: 변이 `snap` 의 배수, `min_edge`~`max_edge`, `min_pixels` ≤ W×H ≤ `max_pixels`, 장단비 ≤ `max_aspect_ratio`.
2. 공통 배율 `s = min(W/w, H/h)` 가 **최대**인 후보 → 같으면 면적 최소 → W 작은 것 → H 작은 것.
3. 그림 크기 `Cw = round_half_up(w·s)`, `Ch = round_half_up(h·s)`. 캔버스 중앙 배치, 여백 홀수면 오른쪽/아래에 1px 더.
4. `max_padding_px` > 0 이면 여백 총량(가로+세로) ≤ N 인 후보 중 배율 최대를 고르고, 없으면 2 로 폴백.

| 원본 | 캔버스 | 그림 | 여백 L/T/R/B | 비고 |
|---|---|---|---|---|
| 546×764 | 1616×2272 | 1616×2261 | 0/5/0/6 | 배율 808/273 ≈ 2.9597 |
| 546×764, max_padding_px=2 | 1600×2240 | 1600×2239 | 0/0/0/1 | 배율 1.0% 손실, 여백 1px |
| 1200×1800 | 1568×2336 | 1557×2336 | 5/0/6/0 | |
| 1200×1800, max_padding_px=2 | 1536×2304 | 1536×2304 | 0/0/0/0 | 배율 1.4% 손실, 여백 0 |
| 1920×1080 | 2560×1440 | 2560×1440 | 0/0/0/0 | 16:9 |
| 600×3000 | 1104×3312 | 662×3312 | 221/0/221/0 | 비율 한계 밖 → 큰 레터박스 |
| 3000×4000 | 1664×2208 | 1656×2208 | 4/0/4/0 | `allow_downscale` 켜야 실행 |

## Prepare 옵션

| 위젯 | 기본 | 의미 |
|---|---|---|
| profile | gpt-image-2.5 standard | standard ≤3,686,400px / experimental ≤8,294,400px / custom(아래 6개 위젯 사용) |
| padding_mode | white | white·black = 3채널 불투명(알파는 그 색 위에 합성), transparent = 4채널 RGBA(여백 알파 0) |
| max_padding_px | 0 | 0 = 끔. N>0 = 여백 총량 상한(위 규칙 4) |
| allow_downscale | False | 축소가 필요한 큰 원본을 허용. False 면 계산 크기를 보여주고 중단 |
| resample | bicubic | bicubic / bilinear / lanczos(채널별 float) / area / nearest-exact |
| use_transparency_mask | True | False 면 마스크 입력·이미지 알파를 무시하고 불투명 처리 |
| snap … max_aspect_ratio | 16 / 480 / 3840 / 655,360 / 3,686,400 / 3.0 | **custom 프로파일에서만** 사용 |
| transparency_mask (선택) | – | 1=투명 / 0=불투명 (Load Image 의 MASK 와 같은 의미) |

다른 모델용 예: NovelAI 계열이면 custom + snap 64, max_pixels 1,048,576 처럼 지정합니다.
custom 결과가 내장 GPT 노드의 Custom 제약(16배수·480~3840·비율≤3·655,360~8,294,400px)을 벗어나면 report 에 경고가 붙습니다.

## Prepare 출력

| 출력 | 의미 |
|---|---|
| canvas_image | 여백 포함 캔버스. white/black 3채널, transparent 4채널 |
| canvas_transparency_mask | 1=투명. **GPT 노드 `mask` 에 그대로 꽂으면 여백만 재생성됩니다** |
| content_region_mask | 1=그림, 0=여백. GPT 노드 `mask`(1=편집 영역) 에 꽂으면 여백을 보호(이미지 1장일 때만) |
| width / height / size_string | 생성 요청 크기 |
| canvas_plan / plan_json | Restore 용 좌표 계약(텐서 없음) |
| report | 한 줄 요약 + 상세 + `주의:` 경고 |

## Restore

- 입력 `image` 는 3채널·4채널 모두 받습니다. 내장 GPT 노드의 결과는 **항상 4채널 RGBA** 텐서입니다.
- `rgba_output`: `split_rgb_mask`(기본) = RGB 3채널 + 투명도 MASK 분리, `keep_rgba` = 4채널 유지.
- `transparency_mask` 입력을 연결하면 이미지 알파 채널보다 우선합니다. 원본 마스크를 결과 알파처럼 넣지 마세요.
- 결과 크기가 `canvas_plan` 의 캔버스와 다르면 예상/실제 크기를 표시하고 **중단**합니다. 비례 보정을 하지 않습니다.

## 내장 GPT Image 2.5 노드와 맞물리는 사실 (ComfyUI 0.37.0 기준)

- `size=Custom` 이면 `model.custom_width`/`model.custom_height`(480~3840, step 16) 링크를 받습니다. 백엔드 검증은 이 노드의 standard/experimental 제약과 같습니다.
- 3채널 IMAGE + MASK 조합으로는 입력 알파가 전송되지 않습니다. 4채널 IMAGE(transparent 모드)는 RGBA PNG 로 전송됩니다. 모델이 입력 알파를 어떻게 다루는지는 실험으로 확인해야 합니다.
- 전송 전 입력을 총 4,194,304px 이하로 축소합니다. experimental 프로파일에서 그 이상이면 report 에 경고가 붙고, 출력만 요청 크기로 나옵니다.
- `mask` 입력은 1=재생성 영역입니다.

## 오류 처리

| 상황 | 동작 |
|---|---|
| 축소 필요 + allow_downscale=False | 계산된 배율·캔버스·그림 크기를 표시하고 중단 |
| 그림 한 변이 0px 로 반올림(극단 비율) | 중단. 1px 로 강제하지 않음 |
| 제약을 만족하는 캔버스 없음(custom) | 중단 |
| 마스크 크기 불일치(64×64 전부 0 기본 마스크 제외) | 중단. 임의 리사이즈하지 않음 |
| 마스크 배치가 1도, 이미지 배치도 아님 | 중단 |
| Restore 결과 크기 ≠ 계획 | 중단 |
| plan 스키마·좌표 규약·일관성 위반 | 중단 |
| transparent 계획인데 결과에 알파 없음 | 크롭은 수행, report 에 `주의:` 표시 |

## 검증 (2026-09-22)

- `tools/test_bmk_canvas_snap.py`: 473 검사 통과. 설계 문서 11.1 기준값 9행 일치, 브루트포스(모든 W×H) 와 후보 탐색 결과 동일(기본 규칙·여백 상한·custom 프로파일), half-up 경계, Prepare→Restore 왕복이 직접 리샘플과 동일, 가장자리 1px 마커 정합, 알파 3모드, 배치·마스크 검증, plan JSON 왕복.
- 격리 인스턴스(8189)에서 Load Image → Prepare → Restore → Save Image 실행: 캔버스 1616×2272 PNG(위 5/아래 6행 흰색), 복원 1616×2261 PNG(가장자리 마커 유지), transparent 모드 RGBA PNG(여백 알파 0, 반투명 128 보존).
- GPT 노드 실제 호출(과금)은 하지 않았습니다. `custom_width/height` 링크가 API 프롬프트에 `["노드id", 슬롯]` 로 직렬화되는 것까지 확인했습니다.
