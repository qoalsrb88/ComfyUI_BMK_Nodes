# BMK XY Plot — 사용 가이드

`ComfyUI_BMK_Nodes` 노드팩에 통합되는 범용 XY Plot 노드. **Anima/Cosmos를 포함한 모든 모델 아키텍처와 호환**됩니다.

## 설치

1. `xy_plot.py`를 `ComfyUI/custom_nodes/ComfyUI_BMK_Nodes/` 폴더에 복사
2. `__init__.py`를 같은 폴더에 덮어쓰기 (`_NODE_MODULES`에 `"xy_plot"` 한 줄 추가됨)
3. ComfyUI 재시작

부팅 로그에 다음이 보이면 성공:
```
[ComfyUI_BMK_Nodes] Loaded 3 node(s): BMKNovelAIMetadata, BMKPromptSyntaxConverter, BMKXYPlot
```

(첫 번째는 추정값, 본인 환경에 따라 다를 수 있음)

## 노드 찾기

ComfyUI 더블클릭 검색에서 `bmk`, `xy`, `xyz`, `grid`, `compare` 어느 키워드로도 떠요. 카테고리는 `BMK Nodes/XY Plot`.

## Anima 워크플로우 적용법

기존 워크플로우에서 **KSampler + VAEDecode 두 노드를 BMK XY Plot 한 노드로 교체**하면 됩니다.

### 연결

| BMK XY Plot 입력 | 연결 출처 |
|---|---|
| model | UNETLoader(68).MODEL |
| positive | CLIPTextEncode+ (67).CONDITIONING |
| negative | CLIPTextEncode− (65).CONDITIONING |
| latent | EmptyLatentImage(64).LATENT |
| vae | VAELoader(62).VAE |
| positive_text (선택) | CLIPTextEncode+ 의 text 위젯에서 복사 (A1111 메타데이터용) |
| negative_text (선택) | CLIPTextEncode− 의 text 위젯에서 복사 |

출력 `grid` → SaveImage 에 연결.

## 위젯 설정

### 기본 sampler 파라미터
- seed, steps, cfg, sampler_name, scheduler, denoise
- **축으로 지정하지 않은 파라미터의 기본값**으로 쓰임

### XY 축 설정
- `x_axis`, `y_axis`: 드롭다운으로 어떤 파라미터를 축으로 쓸지 선택
  - 옵션: `none`, `cfg`, `steps`, `sampler_name`, `scheduler`, `seed`, `denoise`
  - 한 축을 `none`으로 두면 1차원 비교
- `x_values`, `y_values`: 쉼표로 구분된 값 리스트
  - 숫자: `3.5, 5.0, 7.0`
  - 콤보(sampler/scheduler): `euler, dpmpp_2m, er_sde`
  - 시드 랜덤화: `random` 또는 `-1` 포함하면 셀마다 새 랜덤 시드

### 그리드 출력
- `draw_grid_labels`: 행/열 끝에 라벨 그리기 (켜기 권장)
- `cell_gap`: 셀 간격 px
- `label_font_size`: 그리드 라벨 폰트 크기

### 개별 저장
- `save_clean_individuals`: 깨끗한 원본을 셀마다 저장 (학습 데이터로 활용 가능)
- `save_labeled_individuals`: 좌상단 텍스트 박힌 버전도 저장 (A1111 스타일)
- `overlay_position`: 오버레이 텍스트 위치 (top-left/top-right/bottom-left/bottom-right)
- `overlay_font_size`: 오버레이 폰트 크기
- `save_prefix`: 파일명 접두사. ComfyUI 기본 output 디렉토리에 저장됨
  - 결과 파일명: `{prefix}_{counter:05d}_clean.png`, `{prefix}_{counter:05d}_labeled.png`

### 메타데이터
- `embed_a1111_metadata`: A1111 호환 `parameters` 키 임베드 (`positive_text` 입력 시에만 유효)
- `embed_workflow_metadata`: ComfyUI workflow JSON 임베드 (PNG → ComfyUI 드래그 시 워크플로우 복원 가능)

## 사용 예시

### 예시 1: CFG × Sampler 비교 (가장 흔한 패턴)
```
x_axis     : cfg
x_values   : 3.5, 5.0, 7.0, 9.0
y_axis     : sampler_name
y_values   : euler, dpmpp_2m, dpmpp_3m_sde, er_sde
```
→ 4×4 = 16 셀 그리드.

### 예시 2: Steps 영향 단일 비교
```
x_axis     : steps
x_values   : 10, 20, 30, 40, 50
y_axis     : none
```
→ 1×5 가로 비교.

### 예시 3: 같은 프롬프트 변동성 체크
```
x_axis     : seed
x_values   : random, random, random, random
y_axis     : none
```
→ 4개 다른 랜덤 시드로 변동성 확인.

### 예시 4: Scheduler × CFG 매트릭스
```
x_axis     : scheduler
x_values   : simple, sgm_uniform, beta, karras
y_axis     : cfg
y_values   : 4, 6, 8
```
→ 4×3 = 12 셀.

## 동작 원리 (Anima 호환 핵심)

내부적으로 `nodes.common_ksampler`(ComfyUI 표준 KSampler 함수)를 그대로 호출합니다. 이 함수는 `comfy.sample.fix_empty_latent_channels(model, latent_image)`를 자동으로 적용해서 Cosmos/Anima 같은 5D latent를 요구하는 모델 아키텍처에도 올바른 텐서 shape을 보장해요. ttN의 자체 sampler 카피가 이 전처리를 누락해서 깨졌던 그 지점입니다.

같은 이유로 SDXL, Flux, SD3, SD1.5에서도 별도 분기 없이 그대로 동작합니다.

## 알려진 한계 (현재 버전)

- **축은 KSampler 파라미터 6종만 지원**: cfg, steps, sampler_name, scheduler, seed, denoise. 프롬프트 변경, LoRA 강도 변경, 체크포인트 교체는 미지원 (확장하려면 추가 입력 슬롯 필요).
- **단일 batch 가정**: latent의 batch_size=1 기준. 배치가 큰 경우 첫 이미지만 그리드 셀로 사용됨.
- **셀 크기 동일 가정**: 모든 셀은 같은 해상도 (latent 입력 하나만 받으므로 자연스럽게 보장됨).
- **A1111 메타데이터**: `positive_text`/`negative_text`를 명시적으로 넣어줘야 임베드됨. CONDITIONING은 이미 인코딩된 상태라 원본 텍스트 복원이 불가능해서 그렇습니다.

## 확장 계획 (요청 시)

- LoRA strength XY (LoRA loader 통합)
- 체크포인트/UNet XY (별도 입력 슬롯)
- 프롬프트 부분 치환 XY (CLIP 입력 추가, 재인코딩)
- Z 축 추가 (그리드 페이지 여러 장)
- 임의 노드 ID 타게팅 (별도 노드로, PromptServer queue 기반)
