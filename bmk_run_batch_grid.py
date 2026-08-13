# bmk_run_batch_grid.py  (v4)
# Run 배치 누적 그리드 노드 (BMKCyclicSeed와 짝으로 사용)
#
# v4 변경점:
#   - 그리드가 한 사이클(run)을 완성할 때 공유 인덱스(BMKRunCycle)를 0으로
#     리셋한다. 이로써 run 연동 cycle 시드와 사이클 경계가 정확히 일치한다.
#     (시드는 매 실행 인덱스를 올리기만 하고, 경계 리셋은 그리드가 소유)
#
# v3 기능 유지:
#   - link_to_run=ON: 컨트롤러 값을 읽지 않고, "서버 큐에 남은 작업이
#     없을 때(=이번이 마지막)"를 완성 신호로 사용. 그리드는 맨 뒤에서
#     실행되므로 큐 잔량 판정이 신뢰 가능하다.
#   - link_to_run=OFF: 기존처럼 batch_count장 모이면 완성.
#
# 레이아웃 공식(A1111 동일): rows = round(sqrt(n)), cols = ceil(n / rows)

import json
import math
import os

import numpy as np
import torch
from PIL import Image
from PIL.PngImagePlugin import PngInfo

import comfy.utils
import folder_paths

try:
    from .bmk_run_cycle import BMKRunCycle
except Exception:
    try:
        from bmk_run_cycle import BMKRunCycle
    except Exception:
        class BMKRunCycle:
            _index = 0
            @classmethod
            def peek(cls): return cls._index
            @classmethod
            def advance(cls): cls._index += 1; return cls._index
            @classmethod
            def reset(cls): cls._index = 0


def _tasks_remaining():
    """현재 서버 큐에 남은 총 작업 수(실행 중 + 대기). 실패 시 1로 간주."""
    try:
        from server import PromptServer
        return PromptServer.instance.prompt_queue.get_tasks_remaining()
    except Exception as e:
        print(f"[BMKRunBatchGrid] queue check failed ({e}); treating as last")
        return 1


class BMKRunBatchGrid:
    # 노드 인스턴스(unique_id)별 누적 버퍼. 서버가 살아있는 동안 유지됨.
    _state = {}

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE",),
                "link_to_run": ("BOOLEAN", {
                    "default": True,
                    "label_on": "Run 연동(큐 종료 시 완성)",
                    "label_off": "수동(batch_count 사용)",
                }),
                "batch_count": ("INT", {"default": 4, "min": 1, "max": 256}),
                "save_final_grid": ("BOOLEAN", {"default": True}),
                "filename_prefix": ("STRING", {"default": "grid/BMK_grid"}),
            },
            "hidden": {
                "unique_id": "UNIQUE_ID",
                "prompt": "PROMPT",
                "extra_pnginfo": "EXTRA_PNGINFO",
            },
        }

    RETURN_TYPES = ("IMAGE", "BOOLEAN")
    RETURN_NAMES = ("grid", "is_complete")
    FUNCTION = "accumulate"
    OUTPUT_NODE = True
    CATEGORY = "BMK/utils"
    DESCRIPTION = (
        "한 run(배치) 동안 들어온 이미지를 누적해 A1111과 같은 레이아웃"
        "(rows=round(sqrt(n)), cols=ceil(n/rows))의 그리드 한 장으로 합칩니다. "
        "BMK Cyclic Seed와 짝으로 쓰면 이 노드가 사이클 경계를 리셋합니다."
    )
    SEARCH_ALIASES = [
        "run batch grid", "grid", "batch grid", "contact sheet",
        "그리드", "배치 그리드", "이미지 모음",
    ]

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        # 누적 동작을 위해 매 실행마다 강제 재실행
        return float("NaN")

    # ── A1111 그리드 레이아웃 공식 ─────────────────────────────
    @staticmethod
    def _layout(n):
        rows = max(1, round(math.sqrt(n)))
        cols = max(1, math.ceil(n / rows))
        return rows, cols

    # ── 누적된 이미지들을 한 장의 그리드로 합성 ────────────────
    def _compose(self, imgs):
        h, w, c = imgs[0].shape
        norm = []
        for im in imgs:
            if im.shape != (h, w, c):
                t = im.permute(2, 0, 1).unsqueeze(0)
                t = comfy.utils.common_upscale(t, w, h, "lanczos", "disabled")
                im = t.squeeze(0).permute(1, 2, 0)
            norm.append(im)

        rows, cols = self._layout(len(norm))
        grid = torch.zeros((rows * h, cols * w, c), dtype=norm[0].dtype)
        for i, im in enumerate(norm):
            r, col = divmod(i, cols)
            grid[r * h:(r + 1) * h, col * w:(col + 1) * w, :] = im
        return grid.unsqueeze(0)  # [1, H, W, C]

    # ── 완성된 그리드 저장 (SaveImage와 동일한 방식) ───────────
    def _save(self, grid, filename_prefix, prompt, extra_pnginfo):
        full_folder, filename, counter, subfolder, _ = \
            folder_paths.get_save_image_path(
                filename_prefix, folder_paths.get_output_directory(),
                grid.shape[2], grid.shape[1])

        arr = np.clip(255.0 * grid[0].cpu().numpy(), 0, 255).astype(np.uint8)
        img = Image.fromarray(arr)

        meta = PngInfo()
        if prompt is not None:
            meta.add_text("prompt", json.dumps(prompt))
        if extra_pnginfo:
            for k, v in extra_pnginfo.items():
                meta.add_text(k, json.dumps(v))

        file = f"{filename}_{counter:05}_.png"
        img.save(os.path.join(full_folder, file),
                 pnginfo=meta, compress_level=4)
        return [{"filename": file, "subfolder": subfolder, "type": "output"}]

    def accumulate(self, images, link_to_run, batch_count, save_final_grid,
                   filename_prefix, unique_id, prompt=None, extra_pnginfo=None):
        key = str(unique_id)
        st = BMKRunBatchGrid._state.get(key)

        if link_to_run:
            # ── Run 연동: 큐가 빌 때(이번이 마지막)까지 누적 ──────
            if st is None or st.get("mode") != "run":
                st = {"mode": "run", "imgs": []}
                BMKRunBatchGrid._state[key] = st
            for i in range(images.shape[0]):
                st["imgs"].append(images[i].detach().cpu().clone())
            complete = _tasks_remaining() <= 1   # 1 == 지금 실행 중인 이거 하나뿐
            imgs_for_grid = st["imgs"]
        else:
            # ── 수동: batch_count장 모이면 완성 (기존 동작) ──────
            if (st is None or st.get("mode") != "manual"
                    or st.get("batch_count") != batch_count):
                st = {"mode": "manual", "batch_count": batch_count, "imgs": []}
                BMKRunBatchGrid._state[key] = st
            for i in range(images.shape[0]):
                st["imgs"].append(images[i].detach().cpu().clone())
            complete = len(st["imgs"]) >= batch_count
            imgs_for_grid = st["imgs"][:batch_count] if complete else st["imgs"]

        grid = self._compose(imgs_for_grid)

        ui_images = []
        if complete:
            if save_final_grid:
                ui_images = self._save(grid, filename_prefix,
                                       prompt, extra_pnginfo)
            n = len(imgs_for_grid)
            st["imgs"] = []           # 다음 사이클을 위해 자동 리셋
            BMKRunCycle.reset()       # 짝 시드의 사이클 인덱스도 함께 리셋
            print(f"[BMKRunBatchGrid] grid complete "
                  f"({n} imgs, layout {self._layout(n)}, "
                  f"mode={'run' if link_to_run else 'manual'})")
        else:
            tail = _tasks_remaining() if link_to_run else "-"
            print(f"[BMKRunBatchGrid] accumulated {len(st['imgs'])} "
                  f"(mode={'run' if link_to_run else 'manual'}, remaining={tail})")

        return {"ui": {"images": ui_images}, "result": (grid, complete)}


NODE_CLASS_MAPPINGS = {
    "BMKRunBatchGrid": BMKRunBatchGrid,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "BMKRunBatchGrid": "Run Batch Grid (WebUI style)",
}
