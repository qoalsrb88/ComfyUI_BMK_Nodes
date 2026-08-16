# BMK PiD Tiled Upscale — 핵심 가이드

NVIDIA PiD(PixelDiT) 4배 업스케일을 **타일 루프**로 수행하여, 입력 해상도 제한 없이
4K 이상 이미지도 처리하는 노드입니다. 코어 ContextWindows 방식을 대체합니다.

---

## 왜 필요한가

PiD 체크포인트(`pid_*_1024_to_4096_*`)는 네이티브 4배 고정이고, 이름의 "1024"는
학습 시 입력 규모(≈1MP)를 뜻합니다. 이 규모를 벗어난 입력을 통째로 밀어 넣으면
두 가지가 동시에 터집니다.

1. **픽셀 공간 텐서 폭발** — 4096² 입력 → 16384×16384 = 268MP. 단일 패스 불가.
2. **분포 이탈로 인한 색 편이** — 코어 `ContextWindowsManual` 은 높이 축(dim=2)만
   자르고 폭은 통째로 전개합니다. 게다가 `split_conds_to_windows=False` 라
   각 창이 전체 `lq_latent` 를 그대로 받습니다. 창이 보는 출력 영역과 조건이
   어긋나고, 겹침 밴드에서 창끼리 서로 다른 색 판단이 누적되어 중앙부에
   보라 편이·과채도가 생깁니다.

이 노드는 캔버스를 나눠 먹이는 대신 **소스를 타일로 자릅니다.** 모든 타일이 예외 없이
정확히 `pid_input_size` 정사각으로 들어가므로 항상 학습 분포 정중앙이고, VRAM 피크는
타일 하나로 고정되어 입력 해상도와 무관해집니다.

## 타일 기하

사용자는 "PiD 가 실제로 보는 크기"를 지정하고, 코어는 거기서 역산됩니다.

    core   = pid_input_size − 2 × context_pixel
    stride = core − core_overlap

    ┌───────────────── pid_input_size (1024) ─────────────────┐
    │ context │              core (768)              │ context │
    │  (128)  │                                      │  (128)  │
    └─────────┴──────────────────────────────────────┴─────────┘
                ↑ 생성에만 쓰이고 페이스트에서 제외 ↑

**타일 크기를 코어 기준으로 잡지 않는 이유**: `TILE 1024 + CROP 128` 로 잡으면 PiD 실입력이
1280 이 되어 학습 크기에서 25% 벗어납니다. 역산 방식이면 실입력이 항상 1024 로 고정됩니다.

경계는 **가상 캔버스**(BMK Flexible Tile SEGS 의 virtual_canvas 와 같은 사고방식)로
처리합니다. 좌상단 기준 고정 스트라이드로 코어를 깔고, 모자란 부분은 이미지 밖으로
확장해 `padding_fill` 로 채웁니다. 마지막 타일을 가장자리에 스냅하지 않으므로 타일
크기가 끝까지 균일하고 숨은 겹침이 없습니다. 출력에서 패딩분은 ×4 비례로 잘라냅니다.

기본값(1024/128/64) 기준 타일 수:

| 입력 | 타일 | 출력 | 누적 캔버스 RAM(fp32) |
|---|---|---|---|
| 1248×1824 | 2×3 = 6 | 4992×7296 | ~0.6GB |
| 2048×2048 | 3×3 = 9 | 8192×8192 | 1.4GB |
| 3840×2160 | 6×3 = 18 | 15360×8640 | 2.4GB |
| 4096×4096 | 6×6 = 36 | 16384×16384 | 4.9GB |

## 과채도 억제 4층

| 층 | 수단 | 위치 |
|---|---|---|
| 1 | `degrade_sigma` 0.06 | 이 노드 (기본값) |
| 2 | 코어 페이스트 — 각 픽셀은 단 한 타일에서만 옴 | 이 노드 (내재) |
| 3 | per-tile color match | 이 노드 (`color_match`) |
| 4 | 전역 저주파 톤 이식 | **BMK Wavelet Tone Restore** (별도 연결) |

3층은 출력을 소스 해상도로 area 다운샘플한 뒤 통계를 비교합니다. PiD 가 새로 만든
고주파 디테일이 std 에 섞여 눌리는 것을 막기 위한 주파수 공정 비교입니다.
`mean` 은 채널 평균만 맞춰 색 편이를 잡고 대비는 건드리지 않으며, `mean_std` 는
Reinhard 전체 매칭으로 채도 폭주까지 억제하되 대비가 약간 눌릴 수 있습니다.

## 배선

```
BMK PiD Loader ──(pid_ctx)──┐
                            │
KSampler ──(LATENT)─────────┤
   또는                     ├─→ BMK PiD Tiled Upscale ─→ IMAGE (×4)
Load Image ──(IMAGE)────────┤                              │
VAE Loader ──(vae_encode)───┘                              │
                                                           ↓
              원본 ──(reference)──→ BMK Wavelet Tone Restore
                                                           ↓
                                            ImageScaleBy (lanczos, 0.5)
                                                           ↓
                                                      Save Image
```

- `latent` 와 `image` 중 하나만 연결하면 됩니다. 둘 다면 **latent 우선**입니다.
- `latent` 경로가 VAE 왕복이 없어 더 정확하고 빠릅니다. 단 `color_match` 를 쓰려면
  소스 타일 디코드용으로 `vae_encode` 도 함께 연결해야 합니다.
- `downscale_by` 는 노드에 없습니다. 외부 `ImageScaleBy` 를 쓰세요 — 축소 배율만
  바꿀 때 재샘플링을 피할 수 있습니다.

### VAE 두 개를 혼동하지 마세요

| 용도 | 값 | 연결 위치 |
|---|---|---|
| PiD 디코드 | `pixel_space` **고정** | BMK PiD Loader 의 `pid_vae` |
| 입력 인코딩 | 생성 백본과 같은 계열 (`qwen_image_vae` 등) | 업스케일 노드의 `vae_encode` |

`latent_format` 은 **생성 모델이 아니라 인코딩에 쓴 VAE** 를 따릅니다. Flux VAE(`ae.safetensors`)
로 인코딩해 놓고 `qwenimage` 를 고르면 채널 수가 둘 다 16 이라 에러 없이 통과한 뒤
결과만 무너집니다. 노드가 실행 시 `src ... (qwenimage, 16ch/8x)` 형태로 로그를 찍으니
확인하세요.

## 파라미터

| 파라미터 | 기본 | 설명 |
|---|---|---|
| `pid_input_size` | 1024 | PiD 실입력 크기. 체크포인트 학습 크기와 일치시킬 것 |
| `context_pixel` | 128 | 코어 밖 컨텍스트. 생성에는 쓰고 페이스트에서 제외 |
| `core_overlap` | 64 | 코어 겹침 = feather 밴드 폭. 0 이면 완전 분할 |
| `padding_fill` | reflect | 가상 캔버스 채움. 패딩이 이미지보다 크면 replicate 자동 폴백 |
| `degrade_sigma` | 0.06 | 낮을수록 원본 충실 = 색 편이 억제. 0.05~0.10 권장 |
| `latent_format` | qwenimage | flux2(Klein)만 16× 배율, 나머지 8×. 채널 수 교차 검증됨 |
| `seed_mode` | same | same 권장(타일 간 일관성). per_tile 은 반복 텍스처 회피용 |
| `color_match` | mean | mean / mean_std / off |
| `model_shift` | 0.0 | ModelSamplingSD3 shift. PiD v1 워크플로우는 1.5 를 씀 |
| `accum_dtype` | fp32 | 초대형 입력에서 RAM 이 빠듯하면 fp16 |

## 튜닝 순서

1. **기본값으로 한 번 돌립니다.** 타일 수와 출력 크기를 콘솔 로그로 확인.
2. **이음새가 보이면** → `core_overlap` 을 64 → 128 로. 겹침 밴드가 넓어져 전이가
   부드러워집니다. 대신 이중 처리 구간이 늘어나므로 128 이상은 권하지 않습니다.
3. **경계에 색 단차가 남으면** → `color_match` 를 `mean` → `mean_std` 로.
4. **디테일이 부족하면** → `degrade_sigma` 를 0.06 → 0.10 으로. 단 색 드리프트가
   커지므로 3번과 함께 조정하세요.
5. **전역 톤이 밍숭맹숭하면** → BMK Wavelet Tone Restore 를 붙이고 `delta_preview` 로
   `levels` 를 조정합니다(해당 가이드 참조).

## 트러블슈팅

- **타일 하나조차 OOM** — `pid_input_size` 를 768 또는 512 로 낮추세요. 학습 크기에서
  멀어질수록 품질이 떨어지므로 마지막 수단입니다. 노드가 1회 자동 재시도합니다.
- **결과가 원본과 색이 완전히 다름** — `latent_format` 과 인코딩 VAE 불일치입니다.
  콘솔 로그의 `(형식, N ch/M x)` 를 확인하세요.
- **`pid_vae` 에러** — `pixel_space` 이외를 고르면 로더가 즉시 막습니다.
- **짧은 면이 core(768)보다 작음** — 타일 1개로 처리되며 동작은 하지만, PiD 가
  1024급 입력을 기대하므로 디테일이 부족할 수 있습니다. 앞단에서
  BMK Upscale With Model (Tiled) 등으로 미리 확대한 뒤 넣으세요.
- **배치 입력** — 배치 항목마다 전체 캔버스를 만들어 마지막에 이어 붙입니다.
  초대형 입력에서는 batch 1 로 쓰세요.

## 설치

1. `bmk_pid_tiled_upscale.py` 를 `ComfyUI_BMK_Nodes/` 에 배치.
2. `__init__.py` 의 `_NODE_MODULES` 에 `"bmk_pid_tiled_upscale",` 추가
   (`bmk_load_image_crop` 과 `bmk_run_batch_grid` 사이, 알파벳 순).
3. ComfyUI 재시작 → `BMK/Image` 카테고리에서 두 노드 확인.

## 코어 ContextWindows 방식과의 차이

| | ContextWindows | 이 노드 |
|---|---|---|
| 분할 축 | 높이만 (dim=2) | 가로·세로 모두 |
| 조건(lq_latent) | 창별 분할 안 됨 | 타일별로 정확히 대응 |
| PiD 입력 형상 | 2048 × (전체 폭) — 분포 이탈 | 항상 정사각 1024 |
| VRAM | 입력 폭에 비례 | 타일 하나로 고정 |
| 4K 입력 | 불가 | 가능 |
| 겹침 처리 | 창별 fuse(pyramid 등) | 코어 페이스트 + 좁은 feather |

## 한계

- PiD 는 네이티브 4배 고정이라 배율은 선택할 수 없습니다. 순 2배가 목표여도 4배
  연산 비용을 그대로 지불하고 사후 축소해야 합니다.
- 타일 간 전역 일관성(예: 화면을 가로지르는 넓은 그라데이션)은 타일 방식의 구조적
  약점입니다. `color_match` 가 소스 통계로 되돌려 대부분 상쇄하지만, 완전한 복원은
  BMK Wavelet Tone Restore 의 저주파 이식에 의존합니다.
- `model_shift` 는 PiD v1 워크플로우에 있던 단계라 노출만 해두었습니다.
  1.5 체크포인트에서 필요한지는 실측으로 판단하세요.
