"""BMK Persistent Bridge (Image)
중간 결과 이미지를 input/bmk_bridge/ 에 영구 저장하고, 캐시가 사라진 뒤에도
(재시작·Free memory·상위 bypass) 상위 프로세스를 다시 돌리지 않고 저장본을
하위 프로세스에 바로 투입할 수 있는 브릿지 노드.

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
  saved        상위를 실행하지 않고 저장본만 내보낸다.
  custom       상위를 실행하지 않고 image 위젯의 파일(input 폴더/업로드/마스크
               에디터 결과)을 내보낸다. 알파 채널이 있으면 MASK 로 변환.

slot (저장본 이름)
------------------
  * 프론트엔드 JS 가 노드 생성 시 `auto-xxxxxxxx` 를 자동으로 채운다(노드별 고유).
    복사/붙이기로 같은 그래프 안에 auto 이름이 중복되면 새 노드 쪽을 재발급한다.
  * 이름을 직접 적으면 그 이름으로 저장·로드한다 → 여러 노드/워크플로우가 하나의
    저장본을 공유하거나, 저장본을 여러 개 구분해 둘 때 사용. (직접 적은 이름은
    중복 검사에서 제외 — 공유가 의도일 수 있으므로.)
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
  * slot 자동 발급 / 중복 재발급.
  * 실행 결과 ui.bmk_bridge 의 저장본 목록을 node.properties 에 기록 → 재로드 후
    프리뷰 복원. mode 에 맞는 프리뷰 표시. custom 이 아닐 때 image 위젯 회색 처리.
  * image 위젯이 사용자/마스크 에디터에 의해 바뀌면 mode 를 custom 으로 자동 전환.

v1 (2026-09)
------------
- 최초 구현.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time

import numpy as np
import torch
from PIL import Image, ImageOps, ImageSequence
from PIL.PngImagePlugin import PngInfo

import folder_paths
import node_helpers

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


# ─── 이미지 입출력 ─────────────────────────────────────────────


def _load_files(paths: list[str]) -> tuple[torch.Tensor, torch.Tensor]:
    """파일들을 [B,H,W,3] float32 텐서 + [B,H,W] 마스크로 로드 (LoadImage 규약).

    알파 채널이 있으면 MASK = 1 - alpha, 없으면 64×64 영 마스크.
    첫 프레임과 크기가 다른 프레임은 건너뛴다.
    """
    images: list[torch.Tensor] = []
    masks: list[torch.Tensor] = []
    size = None
    any_alpha = False

    for path in paths:
        img = node_helpers.pillow(Image.open, path)
        for frame in ImageSequence.Iterator(img):
            frame = node_helpers.pillow(ImageOps.exif_transpose, frame)
            if frame.mode == "I":
                frame = frame.point(lambda i: i * (1 / 255))
            rgb = frame.convert("RGB")
            if size is None:
                size = rgb.size
            if rgb.size != size:
                continue
            images.append(
                torch.from_numpy(np.asarray(rgb, dtype=np.float32) / 255.0)[None,]
            )
            if "A" in frame.getbands():
                alpha = np.asarray(frame.getchannel("A"), dtype=np.float32) / 255.0
                masks.append((1.0 - torch.from_numpy(alpha))[None,])
                any_alpha = True
            else:
                masks.append(torch.zeros((1, 64, 64), dtype=torch.float32))
            if img.format == "MPO":  # LoadImage 와 동일: MPO 는 첫 프레임만
                break

    if not images:
        raise RuntimeError(f"{_TAG} 이미지를 읽을 수 없습니다: {paths}")

    if any_alpha:
        # 일부 프레임만 알파가 있으면 나머지를 실제 크기의 영 마스크로 맞춰 cat 가능하게
        w, h = size
        masks = [
            m if m.shape[1:] == (h, w) else torch.zeros((1, h, w), dtype=torch.float32)
            for m in masks
        ]

    return torch.cat(images, dim=0), torch.cat(masks, dim=0)


def _save_frames(images: torch.Tensor, base: str, prompt, extra_pnginfo, unique_id) -> list[str]:
    """배치를 <base>_b<i>.png 로 저장. 이전 배치의 잔여 프레임은 삭제."""
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
                            "이름을 직접 적으면 그 이름으로 저장/로드 → 여러 노드·워크플로우가 "
                            "하나의 저장본을 공유하거나 저장본을 여러 개 구분할 수 있습니다."
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
        "custom 모드는 Load Image 처럼 파일/업로드/마스크 에디터 결과를 내보냅니다."
    )
    SEARCH_ALIASES = [
        "preview bridge",
        "persistent bridge",
        "checkpoint image",
        "resume",
        "skip upstream",
        "브릿지",
        "중간 저장",
        "저장본",
        "캐시 복원",
        "이어서 실행",
    ]

    # ── 지연 평가: passthrough 이고 링크가 있을 때만 상위 실행을 요청 ──
    def check_lazy_status(self, mode, **kwargs):
        if mode == MODE_PASSTHROUGH and "images" in kwargs and kwargs["images"] is None:
            return ["images"]
        return []

    def bridge(
        self,
        mode,
        slot,
        images=None,
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
            out_image, out_mask = _load_files([path])
            ui_images = [_ui_entry_for_widget(image)]
            used = "custom"

        elif mode == MODE_SAVED or images is None:
            frames = _frames_of(base)
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
            out_image, out_mask = _load_files(frames)
            ui_images = [_ui_entry(p) for p in frames]
            used = "saved"

        else:
            frames = _save_frames(images, base, prompt, extra_pnginfo, unique_id)
            out_image = images
            out_mask = torch.zeros((images.shape[0], 64, 64), dtype=torch.float32)
            ui_images = [_ui_entry(p) for p in frames]
            used = "input"

        info = {
            "mode": mode,
            "used": used,
            "base": base,
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


NODE_CLASS_MAPPINGS = {
    "BMKPersistentBridge": BMKPersistentBridge,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "BMKPersistentBridge": "BMK Persistent Bridge (Image)",
}
