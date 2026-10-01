"""BMK Persistent Bridge (Image / String)
중간 결과(이미지·텍스트)를 input/bmk_bridge/ 에 영구 저장하고, 캐시가 사라진 뒤에도
(재시작·Free memory·상위 bypass) 상위 프로세스를 다시 돌리지 않고 저장본을
하위 프로세스에 바로 투입할 수 있는 브릿지 노드. String 버전은 아래 "String 버전" 참고.

배경
----
Impact Pack 의 Preview Bridge 는 미리보기를 temp/ 에 쓰고 파일 매핑을 서버
메모리에만 두기 때문에 재시작하면 `0 × 0` 이 되고, `images` 가 required 라서
상위를 bypass 하면 실행 전 검증(Required input missing)에서 막힌다.
이 노드는 그 두 제약을 없앤다.

  * images 입력이 optional + lazy: 상위가 bypass 돼 링크가 사라져도 실행되고,
    saved/custom 모드에서는 상위 노드를 "요청하지 않으므로" 실행 자체가
    일어나지 않는다(상위는 프롬프트에 남아 있어 캐시도 정리되지 않는다).
  * 저장 위치가 input/bmk_bridge/ 이므로 재시작 후에도 남고 /view 로 서빙된다.
  * 저장본 이름(slot)은 프롬프트 입력(위젯)이므로 캐시 키에 포함된다. 실행마다
    바뀌는 값이 아니라서 캐시를 흔들지 않고, 이름이 곧 파일명이라 IS_CHANGED 가
    정확히 그 파일만 지문으로 삼을 수 있다.

모드 (mode)
-----------
  passthrough  입력 이미지를 그대로 내보내면서 저장본을 갱신한다(기본).
               입력 링크가 비어 있으면(상위 bypass/mute) 저장본으로 대체한다.
               저장본도 없으면(아직 한 번도 생성하지 않은 단계) 오류 대신 하류만
               조용히 건너뛴다(ExecutionBlocker).
  saved        상위를 실행하지 않고 저장본만 내보낸다.
  custom       상위를 실행하지 않고 image 위젯의 파일(input 폴더/업로드/마스크
               에디터 결과)을 내보낸다. Load Image 규약: RGB + MASK(1 - alpha).

mask 입력 (v2) — Join Image with Alpha 통합
-------------------------------------------
  passthrough 에서 mask 가 연결되면 코어 Join Image with Alpha 와 동일하게
  alpha = 1 - mask 를 붙인 RGBA 이미지를 내보내고, PNG 도 RGBA 로 저장한다.
  mask 출력은 이미지 크기로 리사이즈된 입력 마스크. 배치는 큰 쪽에 맞춰 반복.
  mask 없이 4채널 이미지가 들어오면 그대로 통과시키고 mask 출력 = 1 - alpha.

  saved 모드는 "이 노드가 마지막에 내보낸 것"을 재현하는 것이 목적이므로,
  저장 PNG 에 알파가 있으면 RGBA 이미지 + (1 - alpha) 마스크를 그대로 돌려준다.
  → passthrough 때와 같은 채널 수/마스크가 나와 하위 노드 입장에서 차이가 없다.
  custom 모드만 Load Image 규약(RGB + MASK)을 따른다.

slot (저장본 이름)
------------------
  * 프론트엔드 JS 가 노드 생성 시 `auto-xxxxxxxx` 를 자동으로 채운다(노드별 고유).
    이미지/String 노드는 확장자가 달라 같은 이름을 써도 충돌하지 않으며, 중복 검사도
    같은 종류의 노드끼리만 한다.
  * 복사/붙이기·복제로 같은 워크플로우에 같은 이름이 생기면 새 노드 쪽을 자동으로
    바꾼다: auto 이름은 재발급, 직접 적은 이름은 `이름_2`, `이름_3`… 접미.
    한 번의 붙이기에 포함된 노드들은 같은 원본 이름 → 같은 새 이름으로 매핑되어
    붙여넣은 묶음 안의 의도적 공유는 유지된다.
  * 워크플로우 로드(재시작·undo 포함)에서는 이름을 건드리지 않으므로, 직접 같은
    이름을 적어 여러 노드가 저장본을 공유하는 구성은 그대로 보존된다.
  * 비어 있으면(API 전용 사용 등) `node_<노드ID>` 를 쓴다.
  * 파일: input/bmk_bridge/<slot>_b<배치인덱스>.png

캐시 동작 메모
--------------
  * passthrough(링크 있음): IS_CHANGED 는 상수 → 상위 서명만 캐시 키를 결정.
  * saved / custom / passthrough(링크 없음): 사용할 파일의 (이름, mtime, size)
    지문을 IS_CHANGED 로 반환 → 파일이 바뀌면 재실행, 아니면 캐시 유지.
  * saved 모드에서도 상위 링크는 프롬프트에 남아 있으므로, 상위 파라미터를 바꾸면
    캐시 서명이 달라져 이 노드와 하위가 한 번 재실행된다(출력은 동일).

배치
----
  [B,H,W,C] 입력은 `<slot>_b0.png, _b1.png ...` 로 프레임별 저장하고, 로드 시
  같은 slot 의 프레임을 인덱스순으로 다시 쌓는다. 이전 배치의 잔여 프레임은 삭제.

프론트엔드 (./js/bmk_persistent_bridge.js)
-----------------------------------------
  * slot 자동 발급 / 붙이기·복제 시 중복 방지.
  * 실행 결과 ui.bmk_bridge 의 저장본 목록을 node.properties 에 기록 → 재로드 후
    프리뷰 복원. mode 에 맞는 프리뷰 표시. custom 이 아닐 때 image 위젯 회색 처리.
  * image 위젯이 사용자/마스크 에디터에 의해 바뀌면 mode 를 custom 으로 자동 전환.

String 버전 — BMK Persistent Bridge (String)  (v3)
--------------------------------------------------
  LLM/API 프롬프트 생성 노드처럼 다시 돌리기 비싼 텍스트 결과를 보존한다.
  모드·slot·lazy 입력·IS_CHANGED 규칙은 이미지 버전과 같고, 차이는 아래뿐이다.

  * 저장: input/bmk_bridge/<slot>.txt (UTF-8, 줄바꿈 변환 없음). 외부 편집기로 고쳐도
    (이름, mtime, size) 지문이 바뀌어 saved 모드가 다시 읽는다. BOM 은 읽을 때 무시.
  * passthrough 에서 내용이 저장본과 같으면 파일을 다시 쓰지 않는다
    → 같은 slot 을 saved 로 쓰는 다른 노드의 캐시가 흔들리지 않는다.
  * custom: custom_text 위젯 값을 내보낸다(빈 문자열 허용). 위젯 값이 곧 프롬프트
    입력이라 IS_CHANGED 는 상수.
  * 실행 결과가 입력 위젯에 다시 써지지 않는다(저장본 표시는 ui 로만) → 텍스트를
    잡은 다음 실행에서 하류 캐시가 무효화되지 않는다.
  * 리스트 입력은 항목마다 실행되어 마지막 항목이 저장본에 남는다(단일 문자열 기준).

  프론트엔드(같은 JS 파일):
  * 저장본 미리보기(읽기 전용). 로드 시 /view 로 서버 파일을 직접 읽고, 파일이 없으면
    node.properties 에 남긴 사본(워크플로우와 함께 저장됨)을 "사본"으로 표시한다.
  * "저장본 → 편집칸" 버튼: 저장본(없으면 사본)을 custom_text 로 복사하고 mode=custom.
    custom_text 에 이미 다른 내용이 있으면 덮어쓰기 전에 확인한다.
  * custom_text 에 직접 입력하면 mode 를 custom 으로 자동 전환.
  * 탐색 모드 토글: 결과를 여러 번 뽑아 보는 동안 passthrough 를 유지한다. 켜져 있으면
    custom_text 입력·복사 버튼이 mode 를 바꾸지 않아 편집칸을 후보 메모장으로 쓸 수 있다.
    mode 를 직접 바꾸면 꺼진다. 상태는 node.properties 에만 있어 프롬프트·캐시와 무관.
  * 미리보기 하단 손잡이로 미리보기 : custom_text 높이 비율 조절(더블클릭 = 50%).
  * "편집칸 → 저장본(확정)" 버튼: custom_text 를 저장본 파일에 쓴다(POST
    /bmk/bridge/save_text, 파일명은 slot 규칙으로 다시 정리). custom 모드 없이도 고친
    텍스트가 고정 단계에서 재생된다. 편집칸 내용은 그대로 남는다.

  주의: 상위 출력에 Show Text 같은 출력 노드가 붙어 있으면 그 노드가 상위를 필요로
  하므로 saved 모드에서도 상위가 돈다 → 보기용 노드는 브릿지 뒤에 연결할 것.

단계 제어 — 상위 bypass = 고정 (v4)
------------------------------------
  여러 브릿지를 단계(A-00, A-01 …)로 이어 쓸 때, 브릿지는 전부 passthrough 로 두고
  각 단계의 API 그룹을 켜고 끄는 것만으로 "탐색 중 / 고정"을 정한다.
    상위가 켜져 있음  → 매번 새로 생성해 통과 + 저장본 갱신 (탐색)
    상위가 꺼져 있음  → 저장본 재생 (고정)
  rgthree Fast Groups Bypasser 의 toggleRestriction 을 "max one" 으로 두면 켜진
  그룹 하나가 곧 현재 단계라서, 단계 이동이 토글 한 번이 된다.

  * mute 는 프론트엔드가 링크를 지워 원래도 저장본으로 대체됐지만, bypass 는 그 노드의
    같은 타입 입력(지시 프롬프트, 캐릭터 이미지 등)을 브릿지로 흘려보내 passthrough 가
    그 값으로 저장본을 덮어썼다. JS 가 graphToPrompt 결과에서, 브릿지 입력의 실제 출처
    노드(Reroute·Get 같은 가상 노드는 따라 올라감)가 bypass 상태면 그 입력을 빼서
    mute 와 같게 만든다. 상위를 bypass 해서 그 입력을 그대로 받던 구 동작이 필요하면
    노드 우클릭 메뉴에서 노드별로 끌 수 있다(properties.bmk_bridge_bypass_through).
  * 한 번도 생성하지 않은 단계는 저장본이 없으므로 위 passthrough 규칙대로 하류만
    건너뛴다 → 첫 탐색 때 뒤 단계를 꺼 둬도 실행이 오류로 멈추지 않는다.

v4 (2026-10)
------------
- 상위 bypass 를 mute 와 같게 처리(프론트엔드) → 그룹 토글만으로 단계 제어.
- passthrough 에서 입력·저장본이 모두 없으면 오류 대신 하류를 조용히 건너뜀.
- String: "편집칸 → 저장본(확정)" 버튼과 로컬 저장 라우트.

v3 (2026-10)
------------
- BMK Persistent Bridge (String) 추가.

v2 (2026-09)
------------
- mask 입력 추가 (Join Image with Alpha 통합). saved 모드는 알파를 보존해 재현.
- 붙이기/복제 시 slot 중복 방지를 직접 적은 이름까지 확대 (프론트엔드).

v1 (2026-09)
------------
- 최초 구현.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import time

import numpy as np
import torch
from PIL import Image, ImageOps, ImageSequence
from PIL.PngImagePlugin import PngInfo

import folder_paths
import node_helpers
from comfy_execution.graph_utils import ExecutionBlocker

try:
    from comfy.cli_args import args as _comfy_args
except Exception:  # pragma: no cover - ComfyUI 밖에서 import 된 경우
    _comfy_args = None

logger = logging.getLogger(__name__)

_TAG = "[ComfyUI_BMK_Nodes::PersistentBridge]"

SUBFOLDER = "bmk_bridge"

MODE_PASSTHROUGH = "passthrough"
MODE_SAVED = "saved"
MODE_CUSTOM = "custom"
MODES = [MODE_PASSTHROUGH, MODE_SAVED, MODE_CUSTOM]

_FRAME_SUFFIX = "_b"  # <base>_b<index>.png


# ─── 경로/이름 유틸 ─────────────────────────────────────────────


def _bridge_dir() -> str:
    return os.path.join(folder_paths.get_input_directory(), SUBFOLDER)


def _sanitize(value) -> str:
    """파일명 조각으로 안전한 문자열로 정리 (유니코드 문자·숫자·_·- 만 허용)."""
    text = re.sub(r"[^\w\-]+", "_", str(value), flags=re.UNICODE).strip("_")
    return text or "x"


def _resolve_base(slot, unique_id) -> str:
    """저장본 기본 이름. slot 이 비어 있으면 node_<노드ID>."""
    slot = (slot or "").strip()
    if slot:
        return _sanitize(slot)
    return f"node_{_sanitize(unique_id)}"


def _frames_of(base: str) -> list[str]:
    """base 에 속한 프레임 파일(절대경로)을 인덱스순으로 반환."""
    folder = _bridge_dir()
    if not os.path.isdir(folder):
        return []
    rx = re.compile(rf"^{re.escape(base)}{_FRAME_SUFFIX}(\d+)\.png$")
    found = []
    for name in os.listdir(folder):
        m = rx.match(name)
        if m:
            found.append((int(m.group(1)), os.path.join(folder, name)))
    found.sort()
    return [path for _, path in found]


def _fingerprint(paths: list[str]) -> str:
    """(파일명, mtime_ns, size) 기반 지문. 내용 해시보다 싸고 변경 감지엔 충분."""
    if not paths:
        return "none"
    h = hashlib.sha256()
    for path in paths:
        try:
            st = os.stat(path)
            h.update(
                f"{os.path.basename(path)}|{st.st_mtime_ns}|{st.st_size};".encode()
            )
        except OSError:
            h.update(f"{os.path.basename(path)}|missing;".encode())
    return h.hexdigest()


def _ui_entry(path: str) -> dict:
    return {"filename": os.path.basename(path), "subfolder": SUBFOLDER, "type": "input"}


def _ui_entry_for_widget(image_value: str) -> dict:
    """LoadImage 계열 위젯 값('a.png', 'sub/a.png', 'a.png [output]') → ui 항목."""
    name, base_dir = folder_paths.annotated_filepath(image_value)
    kind = "input"
    if base_dir is not None:
        if base_dir == folder_paths.get_output_directory():
            kind = "output"
        elif base_dir == folder_paths.get_temp_directory():
            kind = "temp"
    name = name.replace("\\", "/")
    subfolder, _, filename = name.rpartition("/")
    return {"filename": filename, "subfolder": subfolder, "type": kind}


# ─── 마스크/알파 유틸 ───────────────────────────────────────────


def _repeat_to_batch(t: torch.Tensor, batch: int) -> torch.Tensor:
    """코어 comfy.utils.repeat_to_batch_size 와 동일한 동작."""
    if t.shape[0] == batch:
        return t
    if t.shape[0] > batch:
        return t[:batch]
    reps = (batch + t.shape[0] - 1) // t.shape[0]
    return t.repeat((reps,) + (1,) * (t.ndim - 1))[:batch]


def _resize_mask(mask: torch.Tensor, height: int, width: int) -> torch.Tensor:
    """[B,h,w] 마스크를 [B,H,W] 로 bilinear 리사이즈 (코어 resize_mask 와 동일)."""
    if mask.ndim == 2:
        mask = mask.unsqueeze(0)
    m = mask.reshape((-1, 1, mask.shape[-2], mask.shape[-1])).float()
    m = torch.nn.functional.interpolate(m, size=(height, width), mode="bilinear")
    return m.squeeze(1)


def _join_with_alpha(images: torch.Tensor, mask: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
    """코어 Join Image with Alpha 와 동일: alpha = 1 - mask 를 붙인 RGBA 와,
    이미지 크기로 맞춘 mask [B,H,W] 를 반환."""
    height, width = int(images.shape[1]), int(images.shape[2])
    mask_r = _resize_mask(mask.to(images.device), height, width).clamp(0.0, 1.0)
    batch = max(int(images.shape[0]), int(mask_r.shape[0]))
    images = _repeat_to_batch(images, batch)
    mask_r = _repeat_to_batch(mask_r, batch).to(images.dtype)
    alpha = 1.0 - mask_r
    rgba = torch.cat((images[..., :3], alpha.unsqueeze(-1)), dim=-1)
    return rgba, mask_r


def _empty_mask(batch: int) -> torch.Tensor:
    return torch.zeros((batch, 64, 64), dtype=torch.float32)


# ─── 이미지 입출력 ─────────────────────────────────────────────


def _load_files(paths: list[str], keep_alpha: bool) -> tuple[torch.Tensor, torch.Tensor]:
    """파일들을 [B,H,W,C] float32 텐서 + [B,H,W] 마스크로 로드.

    * keep_alpha=False (Load Image 규약): RGB, MASK = 1 - alpha (없으면 64×64 영).
    * keep_alpha=True  (saved 재현):     알파가 있으면 RGBA 로 돌려주고 MASK = 1 - alpha.
    첫 프레임과 크기가 다른 프레임은 건너뛴다.
    """
    frames: list[Image.Image] = []
    size = None

    for path in paths:
        img = node_helpers.pillow(Image.open, path)
        for frame in ImageSequence.Iterator(img):
            frame = node_helpers.pillow(ImageOps.exif_transpose, frame)
            if frame.mode == "I":
                frame = frame.point(lambda i: i * (1 / 255))
            if size is None:
                size = frame.size
            if frame.size != size:
                continue
            frames.append(frame.copy())
            if img.format == "MPO":  # LoadImage 와 동일: MPO 는 첫 프레임만
                break

    if not frames:
        raise RuntimeError(f"{_TAG} 이미지를 읽을 수 없습니다: {paths}")

    any_alpha = any("A" in f.getbands() for f in frames)
    w, h = size
    images: list[torch.Tensor] = []
    masks: list[torch.Tensor] = []

    for frame in frames:
        has_alpha = "A" in frame.getbands()
        if keep_alpha and any_alpha:
            rgba = np.asarray(frame.convert("RGBA"), dtype=np.float32) / 255.0
            images.append(torch.from_numpy(rgba)[None,])
        else:
            rgb = np.asarray(frame.convert("RGB"), dtype=np.float32) / 255.0
            images.append(torch.from_numpy(rgb)[None,])

        if has_alpha:
            alpha = np.asarray(frame.getchannel("A"), dtype=np.float32) / 255.0
            masks.append((1.0 - torch.from_numpy(alpha))[None,])
        elif any_alpha:
            masks.append(torch.zeros((1, h, w), dtype=torch.float32))
        else:
            masks.append(torch.zeros((1, 64, 64), dtype=torch.float32))

    return torch.cat(images, dim=0), torch.cat(masks, dim=0)


def _save_frames(images: torch.Tensor, base: str, prompt, extra_pnginfo, unique_id) -> list[str]:
    """배치를 <base>_b<i>.png 로 저장(3채널 RGB / 4채널 RGBA). 이전 배치의 잔여 프레임은 삭제."""
    folder = _bridge_dir()
    os.makedirs(folder, exist_ok=True)

    disable_metadata = bool(getattr(_comfy_args, "disable_metadata", False))
    saved: list[str] = []

    for index in range(images.shape[0]):
        arr = 255.0 * images[index].detach().cpu().float().numpy()
        pil = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))

        metadata = None
        if not disable_metadata:
            metadata = PngInfo()
            if prompt is not None:
                metadata.add_text("prompt", json.dumps(prompt))
            if extra_pnginfo:
                for key, value in extra_pnginfo.items():
                    metadata.add_text(key, json.dumps(value))
            metadata.add_text(
                "bmk_bridge",
                json.dumps(
                    {
                        "node": str(unique_id),
                        "base": base,
                        "index": index,
                        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    }
                ),
            )

        path = os.path.join(folder, f"{base}{_FRAME_SUFFIX}{index}.png")
        tmp = path + ".tmp"
        pil.save(tmp, format="PNG", pnginfo=metadata, compress_level=4)
        os.replace(tmp, path)
        saved.append(path)

    for stale in _frames_of(base):
        if stale not in saved:
            try:
                os.remove(stale)
            except OSError:
                logger.warning(f"{_TAG} 잔여 프레임 삭제 실패: {stale}")

    return saved


# ─── 텍스트 입출력 (String 버전) ────────────────────────────────


def _text_path(base: str) -> str:
    return os.path.join(_bridge_dir(), f"{base}.txt")


def _read_text(path: str) -> str:
    # newline="": 저장한 줄바꿈을 그대로 재현.  utf-8-sig: 외부 편집기가 붙인 BOM 무시.
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return f.read()


def _write_text(path: str, text: str) -> None:
    """원자적 저장. 내용이 같으면 쓰지 않아 mtime(= saved 쪽 지문)을 유지한다."""
    if os.path.isfile(path) and _read_text(path) == text:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    os.replace(tmp, path)


def _blocked_result(base: str, ui: dict) -> dict:
    """passthrough 인데 입력도 저장본도 없음(아직 한 번도 생성하지 않은 단계).
    오류로 실행 전체를 멈추는 대신 이 브릿지의 하류만 조용히 건너뛴다."""
    logger.info(f"{_TAG} 입력과 저장본이 모두 없어 하류를 건너뜁니다: {base}")
    return {"ui": ui, "result": ExecutionBlocker(None)}


def _saved_text_entry(path: str) -> dict | None:
    if not os.path.isfile(path):
        return None
    # 올림: 프론트가 /view 의 Last-Modified(aiohttp 가 초 단위 올림)로 같은 값을 표시한다
    mtime = math.ceil(os.path.getmtime(path))
    return {
        "text": _read_text(path),
        "mtime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime)),
    }


# ─── HTTP API (String 버전 "편집칸 → 저장본" 확정 버튼) ─────────────


def _register_routes() -> None:
    try:
        from aiohttp import web
        from server import PromptServer
    except Exception:
        return  # 서버 환경이 아님

    server = getattr(PromptServer, "instance", None)
    if server is None or getattr(server, "_bmk_bridge_routes_registered", False):
        return
    server._bmk_bridge_routes_registered = True

    @server.routes.post("/bmk/bridge/save_text")
    async def bmk_bridge_save_text(request):
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"error": "잘못된 요청 본문"}, status=400)
        base = payload.get("base") if isinstance(payload, dict) else None
        text = payload.get("text") if isinstance(payload, dict) else None
        if not isinstance(base, str) or not base.strip() or not isinstance(text, str):
            return web.json_response({"error": "base 와 text 가 필요합니다"}, status=400)
        # slot 규칙으로 다시 정리 → 경로 구분자·점이 남지 않아 bmk_bridge 폴더 밖으로 못 나간다
        base = _sanitize(base.strip())
        path = _text_path(base)
        _write_text(path, text)
        return web.json_response({"base": base, "saved": _saved_text_entry(path)})


_register_routes()


# ─── 노드 ─────────────────────────────────────────────────────


class BMKPersistentBridge:
    """영구 저장 브릿지: 저장본/입력/커스텀 이미지 중 하나를 골라 내보낸다."""

    @classmethod
    def _list_custom_files(cls) -> list[str]:
        input_dir = folder_paths.get_input_directory()
        try:
            files = [
                f
                for f in os.listdir(input_dir)
                if os.path.isfile(os.path.join(input_dir, f))
            ]
            files = folder_paths.filter_files_content_types(files, ["image"])
        except Exception:
            files = []

        bridge_files: list[str] = []
        folder = _bridge_dir()
        if os.path.isdir(folder):
            bridge_files = [
                f"{SUBFOLDER}/{f}"
                for f in os.listdir(folder)
                if f.lower().endswith(".png")
            ]

        out = sorted(files) + sorted(bridge_files)
        return out or [""]

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "mode": (
                    MODES,
                    {
                        "default": MODE_PASSTHROUGH,
                        "tooltip": (
                            "passthrough: 입력을 통과시키며 저장본 갱신 "
                            "(입력이 비어 있으면 저장본 사용)\n"
                            "saved: 상위를 실행하지 않고 저장본 사용\n"
                            "custom: 상위를 실행하지 않고 image 위젯 파일 사용"
                        ),
                    },
                ),
                "slot": (
                    "STRING",
                    {
                        "default": "",
                        "tooltip": (
                            "저장본 이름 (input/bmk_bridge/<slot>_b0.png).\n"
                            "자동 생성값(auto-…)을 그대로 두면 노드별로 고유하게 저장됩니다.\n"
                            "복사/붙이기로 이름이 겹치면 새 노드 쪽이 자동으로 바뀝니다.\n"
                            "여러 노드가 하나의 저장본을 공유하려면 같은 이름을 직접 입력하세요."
                        ),
                    },
                ),
            },
            "optional": {
                "images": (
                    "IMAGE",
                    {
                        "lazy": True,
                        "tooltip": (
                            "상위 프로세스 출력. passthrough 에서만 요청되며, "
                            "링크가 비어 있어도(상위 bypass) 실행됩니다."
                        ),
                    },
                ),
                "mask": (
                    "MASK",
                    {
                        "lazy": True,
                        "tooltip": (
                            "선택. passthrough 에서 연결하면 Join Image with Alpha 처럼 "
                            "alpha = 1 - mask 를 붙인 RGBA 이미지를 내보내고 저장합니다.\n"
                            "mask 출력은 이미지 크기로 맞춘 입력 마스크입니다."
                        ),
                    },
                ),
                "image": (
                    cls._list_custom_files(),
                    {
                        "image_upload": True,
                        "tooltip": (
                            "custom 모드에서 내보낼 파일. input 폴더 파일, 업로드, "
                            "마스크 에디터 결과(clipspace) 모두 가능."
                        ),
                    },
                ),
            },
            "hidden": {
                "unique_id": "UNIQUE_ID",
                "prompt": "PROMPT",
                "extra_pnginfo": "EXTRA_PNGINFO",
            },
        }

    RETURN_TYPES = ("IMAGE", "MASK")
    RETURN_NAMES = ("image", "mask")
    FUNCTION = "bridge"
    OUTPUT_NODE = True
    CATEGORY = "BMK/Image"
    DESCRIPTION = (
        "중간 결과 이미지를 input/bmk_bridge/ 에 영구 저장하는 브릿지. "
        "상위가 bypass 되거나 캐시가 사라져도(재시작 포함) 저장본을 바로 하위로 "
        "넘길 수 있고, saved/custom 모드에서는 상위 프로세스를 실행하지 않습니다. "
        "mask 를 연결하면 Join Image with Alpha 처럼 RGBA 로 합쳐 내보내고, "
        "custom 모드는 Load Image 처럼 파일/업로드/마스크 에디터 결과를 내보냅니다."
    )
    SEARCH_ALIASES = [
        "preview bridge",
        "persistent bridge",
        "checkpoint image",
        "resume",
        "skip upstream",
        "join image with alpha",
        "브릿지",
        "중간 저장",
        "저장본",
        "캐시 복원",
        "이어서 실행",
    ]

    # ── 지연 평가: passthrough 이고 링크가 있을 때만 상위 실행을 요청 ──
    def check_lazy_status(self, mode, **kwargs):
        if mode != MODE_PASSTHROUGH:
            return []
        return [name for name in ("images", "mask") if name in kwargs and kwargs[name] is None]

    def bridge(
        self,
        mode,
        slot,
        images=None,
        mask=None,
        image=None,
        unique_id=None,
        prompt=None,
        extra_pnginfo=None,
    ):
        base = _resolve_base(slot, unique_id)

        if mode == MODE_CUSTOM:
            if not image:
                raise ValueError(f"{_TAG} custom 모드: image 가 선택되지 않았습니다.")
            path = folder_paths.get_annotated_filepath(image)
            if not os.path.isfile(path):
                raise FileNotFoundError(f"{_TAG} custom 모드: 파일이 없습니다: {image}")
            out_image, out_mask = _load_files([path], keep_alpha=False)
            ui_images = [_ui_entry_for_widget(image)]
            used = "custom"

        elif mode == MODE_SAVED or images is None:
            frames = _frames_of(base)
            if not frames and mode == MODE_PASSTHROUGH:
                return _blocked_result(base, {"images": [], "bmk_bridge": [
                    {"mode": mode, "used": "blocked", "base": base, "alpha": False, "saved": []}
                ]})
            if not frames:
                raise RuntimeError(
                    f"{_TAG} 저장본이 없습니다 (slot='{base}'). "
                    "mode=passthrough 로 상위 프로세스를 한 번 실행해 저장본을 만든 뒤 "
                    "다시 시도하세요."
                )
            if mode == MODE_PASSTHROUGH:
                logger.info(
                    f"{_TAG} 입력이 비어 있어 저장본을 사용합니다: "
                    f"{base} ({len(frames)} frame)"
                )
            out_image, out_mask = _load_files(frames, keep_alpha=True)
            ui_images = [_ui_entry(p) for p in frames]
            used = "saved"

        else:
            if mask is not None:
                # Join Image with Alpha 통합: RGBA 로 합쳐 내보내고 저장
                out_image, out_mask = _join_with_alpha(images, mask)
            elif images.shape[-1] == 4:
                out_image = images
                out_mask = (1.0 - images[..., 3]).float()
            else:
                out_image = images
                out_mask = _empty_mask(int(images.shape[0]))
            frames = _save_frames(out_image, base, prompt, extra_pnginfo, unique_id)
            ui_images = [_ui_entry(p) for p in frames]
            used = "input"

        info = {
            "mode": mode,
            "used": used,
            "base": base,
            "alpha": bool(out_image.shape[-1] == 4),
            "saved": [_ui_entry(p) for p in _frames_of(base)],
        }
        return {
            "ui": {"images": ui_images, "bmk_bridge": [info]},
            "result": (out_image, out_mask),
        }

    @classmethod
    def IS_CHANGED(cls, mode, slot, image=None, unique_id=None, **kwargs):
        # kwargs 에 "images" 키가 있으면 링크가 존재(값은 아직 None), 없으면 링크 없음.
        linked = "images" in kwargs

        if mode == MODE_CUSTOM:
            try:
                return _fingerprint([folder_paths.get_annotated_filepath(image)])
            except Exception:
                return "invalid"

        if mode == MODE_PASSTHROUGH and linked:
            return ""  # 상위 서명이 캐시 키를 결정

        return _fingerprint(_frames_of(_resolve_base(slot, unique_id)))

    @classmethod
    def VALIDATE_INPUTS(cls, mode, image=None):
        if mode not in MODES:
            return f"Invalid mode: {mode}"
        if mode == MODE_CUSTOM:
            if not image or not folder_paths.exists_annotated_filepath(image):
                return f"Invalid image file: {image}"
        return True


class BMKPersistentBridgeString:
    """영구 저장 브릿지 (텍스트): 저장본/입력/편집 텍스트 중 하나를 골라 내보낸다."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "mode": (
                    MODES,
                    {
                        "default": MODE_PASSTHROUGH,
                        "tooltip": (
                            "passthrough: 입력을 통과시키며 저장본 갱신 "
                            "(입력이 비어 있으면 저장본 사용)\n"
                            "saved: 상위를 실행하지 않고 저장본 사용\n"
                            "custom: 상위를 실행하지 않고 custom_text 사용"
                        ),
                    },
                ),
                "slot": (
                    "STRING",
                    {
                        "default": "",
                        "tooltip": (
                            "저장본 이름 (input/bmk_bridge/<slot>.txt).\n"
                            "자동 생성값(auto-…)을 그대로 두면 노드별로 고유하게 저장됩니다.\n"
                            "복사/붙이기로 이름이 겹치면 새 노드 쪽이 자동으로 바뀝니다.\n"
                            "여러 노드(다른 워크플로우 포함)가 저장본을 공유하려면 같은 이름을 직접 입력하세요."
                        ),
                    },
                ),
            },
            "optional": {
                "text": (
                    "STRING",
                    {
                        "lazy": True,
                        "forceInput": True,
                        "tooltip": (
                            "상위 프로세스 출력. passthrough 에서만 요청되며, "
                            "링크가 비어 있어도(상위 mute/삭제) 실행됩니다."
                        ),
                    },
                ),
                "custom_text": (
                    "STRING",
                    {
                        "multiline": True,
                        "default": "",
                        "tooltip": (
                            "custom 모드에서 내보낼 텍스트. 입력하면 mode 가 custom 으로 바뀝니다.\n"
                            "'저장본 → 편집칸' 버튼으로 저장본을 가져와 고칠 수 있습니다."
                        ),
                    },
                ),
            },
            "hidden": {
                "unique_id": "UNIQUE_ID",
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("text",)
    FUNCTION = "bridge"
    OUTPUT_NODE = True
    CATEGORY = "BMK/Text"
    DESCRIPTION = (
        "중간 결과 텍스트(LLM/API 프롬프트 등)를 input/bmk_bridge/<slot>.txt 에 영구 저장하는 "
        "브릿지. saved/custom 모드에서는 상위 프로세스를 실행하지 않고 저장본이나 직접 고친 "
        "텍스트를 하위로 넘기며, 재시작이나 캐시 비우기 뒤에도 그대로 이어서 쓸 수 있습니다."
    )
    SEARCH_ALIASES = [
        "persistent bridge",
        "catch edit text",
        "catch and edit text",
        "text checkpoint",
        "llm output",
        "skip upstream",
        "resume",
        "브릿지",
        "텍스트 저장본",
        "프롬프트 보존",
        "중간 저장",
        "이어서 실행",
    ]

    def check_lazy_status(self, mode, **kwargs):
        if mode != MODE_PASSTHROUGH:
            return []
        return ["text"] if "text" in kwargs and kwargs["text"] is None else []

    def bridge(self, mode, slot, text=None, custom_text="", unique_id=None):
        base = _resolve_base(slot, unique_id)
        path = _text_path(base)

        if mode == MODE_CUSTOM:
            out = custom_text
            used = "custom"

        elif mode == MODE_SAVED or text is None:
            if not os.path.isfile(path) and mode == MODE_PASSTHROUGH:
                return _blocked_result(base, {"bmk_bridge_text": [
                    {"mode": mode, "used": "blocked", "base": base, "saved": None}
                ]})
            if not os.path.isfile(path):
                raise RuntimeError(
                    f"{_TAG} 저장본이 없습니다 (slot='{base}'). "
                    "mode=passthrough 로 상위 프로세스를 한 번 실행해 저장본을 만들거나, "
                    "노드 미리보기에 사본이 있으면 '저장본 → 편집칸' 후 custom 모드로 쓰세요."
                )
            if mode == MODE_PASSTHROUGH:
                logger.info(f"{_TAG} 입력이 비어 있어 텍스트 저장본을 사용합니다: {base}")
            out = _read_text(path)
            used = "saved"

        else:
            out = text
            _write_text(path, out)
            used = "input"

        info = {
            "mode": mode,
            "used": used,
            "base": base,
            "saved": _saved_text_entry(path),
        }
        return {
            "ui": {"bmk_bridge_text": [info]},
            "result": (out,),
        }

    @classmethod
    def IS_CHANGED(cls, mode, slot, unique_id=None, **kwargs):
        # custom_text 는 프롬프트 입력이라 이미 캐시 키에 들어 있다.
        if mode == MODE_CUSTOM or (mode == MODE_PASSTHROUGH and "text" in kwargs):
            return ""
        path = _text_path(_resolve_base(slot, unique_id))
        return _fingerprint([path] if os.path.isfile(path) else [])


NODE_CLASS_MAPPINGS = {
    "BMKPersistentBridge": BMKPersistentBridge,
    "BMKPersistentBridgeString": BMKPersistentBridgeString,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "BMKPersistentBridge": "BMK Persistent Bridge (Image)",
    "BMKPersistentBridgeString": "BMK Persistent Bridge (String)",
}
