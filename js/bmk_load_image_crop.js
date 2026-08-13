// BMK Load Image (Crop) — 프론트엔드 확장 v2
//
// BMKLoadImageCrop 노드에 다음을 추가한다:
//   - 노드 내 프리뷰 위젯: 원본(크롭 영역 오버레이 표시) / 크롭(편집 결과) 토글
//   - "✂ Crop Editor" 버튼: 종횡비 고정 드래그 크롭 + 90° 회전 다이얼로그
//   - "⟲ Reset Crop" 버튼: 에디터를 열지 않고 노드에서 바로 크롭 해제
//   - 위젯 순서 정렬: [image ▸ upload ▸ 버튼들 ▸ 파라미터 ▸ 프리뷰]
//     (기본 LoadImage 처럼 업로드 버튼이 파일 콤보 바로 아래)
//   - 에디터: 스냅(divide-by) 드래그, 종횡비/스냅 선택 유지(properties 저장),
//     X/Y/W/H 수치 미세 입력
//   - Free(종횡비 미고정) 모드 극단 AR 가드: w/h 를 1:4 ~ 4:1 로 클램프
//     (하류 해상도 정규화에서 한 축이 폭주하는 것을 에디터 단계에서 차단)
//
// 좌표 규약: rotation(시계 방향 90° 단위) 적용 "이후" 이미지 좌표계의 픽셀 값.
// Python 측(bmk_load_image_crop.py)과 동일. 편집은 위젯 값에만 기록(비파괴적)
// 되므로 큐 실행 중에도 자유롭게 조작 가능하다.

import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const NODE_NAME = "BMKLoadImageCrop";

const AR_PRESETS = [
    ["Free", 0],
    ["1:1", 1],
    ["4:3", 4 / 3],
    ["3:4", 3 / 4],
    ["3:2", 3 / 2],
    ["2:3", 2 / 3],
    ["16:9", 16 / 9],
    ["9:16", 9 / 16],
    ["21:9", 21 / 9],
];

// Free(종횡비 미고정) 모드 극단 AR 가드.
// 하류에서 크롭을 목표 화소수(1MP 등)로 정규화할 때 AR 이 보존되므로,
// 3000×100 같은 극단 크롭은 한 축이 폭주한다(→ 9808×320). 모델이 구조를
// 잡지 못하고 attention 비용도 터지므로 에디터에서 미리 막는다.
// 프리셋 AR 은 전부 이 범위 안이라(최대 21:9 ≈ 2.33) 영향받지 않는다.
const AR_FREE_LIMIT = 4; // 허용 범위: 1:4 ~ 4:1

const SNAP_PRESETS = [1, 8, 16, 32, 64];

const HANDLE_HIT_RADIUS = 10; // px (화면 좌표)

// 프리뷰 위젯 치수
const PV_CHIP_H = 22;
const PV_MARGIN = 10;
const PV_MIN_IMG_H = 40;
const PV_MAX_IMG_H = 360;
const PV_CAPTION_H = 16; // 하단 픽셀 캡션(LoadImage 스타일) 높이

// properties 키 (워크플로우에 함께 저장됨)
const PROP_PREVIEW_MODE = "bmk_preview_mode"; // "original" | "crop"
const PROP_EDITOR_ASPECT = "bmk_editor_aspect"; // AR_PRESETS 의 value 문자열
const PROP_EDITOR_SNAP = "bmk_editor_snap"; // SNAP_PRESETS 중 하나

// ─── 공통 유틸 ───────────────────────────────────────────────────

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

const dirty = () => {
    app.graph?.setDirtyCanvas(true, true);
};

function getWidget(node, name) {
    return node.widgets?.find((w) => w.name === name);
}

function getWidgetValue(node, name, fallback = 0) {
    const w = getWidget(node, name);
    return w ? w.value : fallback;
}

function setWidgetValue(node, name, value) {
    const w = getWidget(node, name);
    if (!w) return;
    w.value = value;
    w.callback?.(value, app.canvas, node, null, null);
}

function ensureProps(node) {
    if (!node.properties) node.properties = {};
    return node.properties;
}

// LoadImage 계열 위젯 값("name.png", "sub/name.png", "name.png [input]")을
// /view 엔드포인트 파라미터로 분해
function parseImageValue(value) {
    let filename = String(value ?? "");
    let type = "input";
    const annotated = filename.match(/^(.*) \[(input|output|temp)\]$/);
    if (annotated) {
        filename = annotated[1];
        type = annotated[2];
    }
    let subfolder = "";
    const slash = filename.lastIndexOf("/");
    if (slash >= 0) {
        subfolder = filename.slice(0, slash);
        filename = filename.slice(slash + 1);
    }
    return { filename, subfolder, type };
}

function imageURL(value) {
    const { filename, subfolder, type } = parseImageValue(value);
    if (!filename) return null;
    const params = new URLSearchParams({ filename, subfolder, type });
    return api.apiURL(`/view?${params.toString()}`);
}

// Python _clamp_crop_box 와 동일한 규칙 (없으면 null)
function clampCropBox(iw, ih, x, y, w, h) {
    if (w <= 0 || h <= 0) return null;
    const x0 = Math.max(0, Math.min(x, iw - 1));
    const y0 = Math.max(0, Math.min(y, ih - 1));
    const x1 = Math.max(x0 + 1, Math.min(x + w, iw));
    const y1 = Math.max(y0 + 1, Math.min(y + h, ih));
    if (x1 - x0 < 1 || y1 - y0 < 1) return null;
    return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
}

// Free 모드 비율 가드: w/h 를 [1/AR_FREE_LIMIT, AR_FREE_LIMIT] 안으로 보정.
// 부족한 축을 먼저 "늘려서" 맞추고(가용 공간 내), 늘릴 수 없으면 넘치는 축을
// "줄여서" 맞춘다. 늘리기를 우선하므로 세로로만 드래그해도 사각형이
// 0 으로 붕괴하지 않고 1:4 비율을 유지하며 자란다.
// availW/availH = 앵커 기준으로 그 방향에 남은 여유 픽셀.
// 반환: { w, h, limited } — limited 는 가드가 실제로 개입했는지 여부.
function limitFreeRatio(w, h, availW, availH) {
    const lim = AR_FREE_LIMIT;
    if (h * lim < w) {
        // 너무 가로로 김 → h 확장, 불가하면 w 축소
        const need = w / lim;
        if (need <= availH) {
            h = need;
        } else {
            h = availH;
            w = h * lim;
        }
        return { w, h, limited: true };
    }
    if (w * lim < h) {
        // 너무 세로로 김 → w 확장, 불가하면 h 축소
        const need = h / lim;
        if (need <= availW) {
            w = need;
        } else {
            w = availW;
            h = w * lim;
        }
        return { w, h, limited: true };
    }
    return { w, h, limited: false };
}

// 시계 방향 rot 적용 이미지를 담는 캔버스 생성
function makeRotatedCanvas(img, rot) {
    const iw = img.naturalWidth ?? img.width;
    const ih = img.naturalHeight ?? img.height;
    const c = document.createElement("canvas");
    if (rot % 180 === 0) {
        c.width = iw;
        c.height = ih;
    } else {
        c.width = ih;
        c.height = iw;
    }
    const ctx = c.getContext("2d");
    ctx.translate(c.width / 2, c.height / 2);
    ctx.rotate((rot * Math.PI) / 180);
    ctx.drawImage(img, -iw / 2, -ih / 2);
    return c;
}

// 노드 단위 회전 캔버스 캐시 (프리뷰용)
function getRotatedCanvas(node, img, rot) {
    const key = `${img.src}|${rot}`;
    if (node._bmkRotCache?.key !== key) {
        node._bmkRotCache = { key, canvas: makeRotatedCanvas(img, rot) };
    }
    return node._bmkRotCache.canvas;
}

// 회전 후 좌표계의 사각형 → 원본(비회전) 좌표계 사각형.
// W, H = 원본 이미지 크기. rot = 시계 방향 회전각.
function rectRotatedToOriginal(r, W, H, rot) {
    switch (((rot % 360) + 360) % 360) {
        case 90:
            return { x: r.y, y: H - r.x - r.w, w: r.h, h: r.w };
        case 180:
            return { x: W - r.x - r.w, y: H - r.y - r.h, w: r.w, h: r.h };
        case 270:
            return { x: W - r.y - r.h, y: r.x, w: r.h, h: r.w };
        default:
            return { x: r.x, y: r.y, w: r.w, h: r.h };
    }
}

// 최소 크기 미달일 때만 키운다(절대 축소하지 않음) — 사용자가 늘린 크기 보존.
function fitNode(node) {
    const cs = node.computeSize();
    node.setSize([
        Math.max(node.size[0], cs[0]),
        Math.max(node.size[1], cs[1]),
    ]);
    dirty();
}

// 이미지 로드 시 1회: 프리뷰가 종횡비 기준 적정 높이가 되도록 키움(축소 없음).
// 이후의 세로 여유 공간 채우기는 프리뷰 위젯 draw() 가 담당한다.
function growNodeForImage(node, dims) {
    const cs = node.computeSize();
    const availW = Math.max(60, node.size[0] - PV_MARGIN * 2);
    const desired = clamp(availW * (dims.h / dims.w), PV_MIN_IMG_H, PV_MAX_IMG_H);
    const target = cs[1] + (desired - PV_MIN_IMG_H);
    node.setSize([
        Math.max(node.size[0], cs[0]),
        Math.max(node.size[1], target),
    ]);
    dirty();
}

function resetCrop(node) {
    setWidgetValue(node, "crop_x", 0);
    setWidgetValue(node, "crop_y", 0);
    setWidgetValue(node, "crop_width", 0);
    setWidgetValue(node, "crop_height", 0);
    dirty();
}

// ─── 위젯 순서 정렬 (1-1) ────────────────────────────────────────
// 목표: [image, upload, ✂editor, ⟲reset, rotation, crop_x, crop_y,
//        crop_width, crop_height, preview]
// onNodeCreated 안에서 "동기적으로" 실행되어 configure(값 복원)보다
// 항상 먼저 적용된다 → 저장/복원 시 widgets_values 인덱스가 일치.

function findUploadWidget(node) {
    return node.widgets?.find(
        (w) =>
            w.name !== "image" &&
            (w.name === "upload" ||
                /choose file/i.test(String(w.name ?? "")) ||
                /choose file/i.test(String(w.label ?? "")))
    );
}

function arrangeWidgets(node) {
    const ws = node.widgets ?? [];
    const pick = (name) => ws.find((w) => w.name === name);
    const ordered = [
        pick("image"),
        findUploadWidget(node),
        pick("✂ Crop Editor"),
        pick("⟲ Reset Crop"),
        pick("rotation"),
        pick("crop_x"),
        pick("crop_y"),
        pick("crop_width"),
        pick("crop_height"),
    ].filter(Boolean);
    const preview = pick("bmk_preview");
    const rest = ws.filter((w) => !ordered.includes(w) && w !== preview);
    node.widgets = [...ordered, ...rest, ...(preview ? [preview] : [])];
}

// ─── 기본 이미지 프리뷰 억제 ─────────────────────────────────────
// image_upload 콤보가 있으면 프론트엔드가 node.imgs 로 기본 프리뷰를
// 그린다. 자체 프리뷰 위젯과 중복되므로 imgs 를 항상 빈 배열로 노출.

function suppressDefaultPreview(node) {
    if (node._bmkImgsTrapped) return;
    node._bmkImgsTrapped = true;
    try {
        Object.defineProperty(node, "imgs", {
            configurable: true,
            get() {
                return [];
            },
            set() {
                dirty();
            },
        });
    } catch (e) {
        console.warn("[BMK] default preview suppression failed:", e);
    }
}

// ─── 노드 내 프리뷰 위젯 (1-2, 1-3) ──────────────────────────────

function previewMode(node) {
    return ensureProps(node)[PROP_PREVIEW_MODE] === "crop" ? "crop" : "original";
}

function readCropWidgets(node) {
    return {
        rotation: (((getWidgetValue(node, "rotation", 0) % 360) + 360) % 360) | 0,
        x: getWidgetValue(node, "crop_x", 0) | 0,
        y: getWidgetValue(node, "crop_y", 0) | 0,
        w: getWidgetValue(node, "crop_width", 0) | 0,
        h: getWidgetValue(node, "crop_height", 0) | 0,
    };
}

function makePreviewWidget(node) {
    const widget = {
        type: "BMK_PREVIEW",
        name: "bmk_preview",
        // serializeValue 를 두면 프론트엔드가 "직렬화 대상"으로 판단해
        // options.serialize:false 가 무시된다. 옵션만 남긴다.
        options: { serialize: false },
        _url: null,
        _img: null,
        _pending: null, // 로딩 중인 Image 강참조 (탈락/GC 방지)
        _state: "idle", // idle | loading | loaded | error
        _tries: 0,
        _chips: [],

        // 처음부터 다시 로드 (같은 파일명 재선택/업로드 완료 시에도 확실히 갱신)
        forceReload() {
            this._url = null;
            this._img = null;
            this._pending = null;
            this._tries = 0;
            this._state = "idle";
            node._bmkRotCache = null;
            dirty();
        },

        _startLoad(url, bust) {
            this._state = "loading";
            const im = new Image();
            this._pending = im;

            im.onload = () => {
                if (this._url !== url || this._pending !== im) return;
                this._pending = null;
                this._img = im;
                this._state = "loaded";
                this._tries = 0;
                node._bmkRotCache = null;
                growNodeForImage(node, {
                    w: im.naturalWidth,
                    h: im.naturalHeight,
                });
            };

            im.onerror = () => {
                if (this._url !== url || this._pending !== im) return;
                this._pending = null;
                // 업로드 직후 서버에 파일이 아직 준비되지 않은 일시적 404 등을
                // 대비해 백오프 재시도한다 (재시도는 캐시 우회 파라미터 사용).
                if (this._tries < 8) {
                    const delay = Math.min(300 * ++this._tries, 2000);
                    setTimeout(() => {
                        if (
                            this._url === url &&
                            !this._pending &&
                            this._state !== "loaded"
                        ) {
                            this._startLoad(url, true);
                        }
                    }, delay);
                } else {
                    this._state = "error";
                }
                dirty();
            };

            // 워치독: onload/onerror 가 장시간 오지 않는 고착 상태 → 강제 재시작
            setTimeout(() => {
                if (this._url === url && this._pending === im) {
                    im.src = "";
                    this._pending = null;
                    if (this._tries < 8) {
                        this._tries += 1;
                        this._startLoad(url, true);
                    } else {
                        this._state = "error";
                        dirty();
                    }
                }
            }, 15000);

            im.src = bust
                ? url + (url.includes("?") ? "&" : "?") + "r=" + Date.now()
                : url;
        },

        _ensureImage() {
            const url = imageURL(getWidgetValue(node, "image", ""));
            if (url !== this._url) {
                this._url = url;
                this._img = null;
                this._pending = null;
                this._tries = 0;
                this._state = "idle";
                node._bmkRotCache = null;
            }
            if (url && this._state === "idle") {
                this._startLoad(url, false);
            }
        },

        // 현재 모드에서 표시할 소스의 (w, h)
        _displayDims() {
            const img = this._img;
            if (!img) return null;
            const c = readCropWidgets(node);
            if (previewMode(node) === "original") {
                return { w: img.naturalWidth, h: img.naturalHeight };
            }
            const rw = c.rotation % 180 === 0 ? img.naturalWidth : img.naturalHeight;
            const rh = c.rotation % 180 === 0 ? img.naturalHeight : img.naturalWidth;
            const box = clampCropBox(rw, rh, c.x, c.y, c.w, c.h);
            return box ? { w: box.w, h: box.h } : { w: rw, h: rh };
        },

        computeSize(width) {
            const w = width ?? node.size?.[0] ?? 220;
            // 최소 높이만 보고한다. 실제 이미지 영역은 draw() 가 노드의
            // 남은 세로 공간을 전부 사용해 채운다(기본 LoadImage 와 동일 감각).
            return [w, PV_CHIP_H + 8 + PV_MIN_IMG_H + PV_CAPTION_H + 6];
        },

        draw(ctx, node, width, y) {
            this._ensureImage();
            const c = readCropWidgets(node);
            const mode = previewMode(node);
            const left = PV_MARGIN;
            const availW = width - PV_MARGIN * 2;

            // ── 토글 칩 (원본 / 크롭) ──
            ctx.save();
            ctx.font = "11px sans-serif";
            ctx.textBaseline = "middle";
            this._chips = [];
            let cx = left;
            for (const [label, m] of [
                ["원본", "original"],
                ["크롭", "crop"],
            ]) {
                const tw = ctx.measureText(label).width;
                const cw = tw + 18;
                const rect = { x: cx, y: y + 2, w: cw, h: PV_CHIP_H - 4, mode: m };
                const active = mode === m;
                ctx.fillStyle = active ? "#4af" : "rgba(255,255,255,0.10)";
                ctx.beginPath();
                ctx.roundRect(rect.x, rect.y, rect.w, rect.h, 4);
                ctx.fill();
                ctx.fillStyle = active ? "#102030" : "#cccccc";
                ctx.fillText(label, rect.x + 9, rect.y + rect.h / 2 + 0.5);
                this._chips.push(rect);
                cx += cw + 6;
            }

            // (수치 정보는 하단 캡션으로 일원화 — 상단은 모드 칩 전용)

            // ── 이미지 영역: 노드의 남은 세로 공간(캡션 제외)을 전부 사용 ──
            const areaY = y + PV_CHIP_H + 6;
            const availH = Math.max(
                PV_MIN_IMG_H,
                (node.size?.[1] ?? 0) - areaY - PV_CAPTION_H - 6
            );
            const d = this._displayDims();
            const img = this._img;

            if (!img || !d) {
                ctx.fillStyle = "rgba(255,255,255,0.06)";
                ctx.fillRect(left, areaY, availW, Math.min(availH, 60));
                ctx.fillStyle = "rgba(255,255,255,0.4)";
                ctx.font = "11px sans-serif";
                const msg =
                    this._state === "error"
                        ? "이미지 로드 실패 — 클릭하여 재시도"
                        : this._url
                          ? "이미지 로딩 중…"
                          : "이미지 없음";
                ctx.fillText(msg, left + 8, areaY + 30);
                ctx.restore();
                return;
            }

            const iw = img.naturalWidth;
            const ih = img.naturalHeight;
            const rw = c.rotation % 180 === 0 ? iw : ih;
            const rh = c.rotation % 180 === 0 ? ih : iw;
            const box = clampCropBox(rw, rh, c.x, c.y, c.w, c.h);

            const scale = Math.min(availW / d.w, availH / d.h);
            const dw = d.w * scale;
            const dh = d.h * scale;
            const dx = left + (availW - dw) / 2;
            const dy = areaY + (availH - dh) / 2;

            // 노드 경계 밖으로 절대 그리지 않도록 클립 (돌출 방지)
            ctx.save();
            ctx.beginPath();
            ctx.rect(left, areaY, availW, availH);
            ctx.clip();

            if (mode === "crop") {
                // 편집 결과 뷰: 회전 캔버스에서 크롭 박스만 그린다
                const rc = getRotatedCanvas(node, img, c.rotation);
                if (box) {
                    ctx.drawImage(rc, box.x, box.y, box.w, box.h, dx, dy, dw, dh);
                } else {
                    ctx.drawImage(rc, dx, dy, dw, dh);
                }
            } else {
                // 원본 뷰: 원본 이미지 + 크롭 영역 오버레이(원본 좌표계로 역매핑)
                ctx.drawImage(img, dx, dy, dw, dh);
                if (box) {
                    const o = rectRotatedToOriginal(box, iw, ih, c.rotation);
                    const r = {
                        x: dx + o.x * scale,
                        y: dy + o.y * scale,
                        w: o.w * scale,
                        h: o.h * scale,
                    };
                    ctx.save();
                    ctx.fillStyle = "rgba(0,0,0,0.5)";
                    ctx.beginPath();
                    ctx.rect(dx, dy, dw, dh);
                    ctx.rect(r.x, r.y, r.w, r.h);
                    ctx.fill("evenodd");
                    ctx.restore();
                    ctx.strokeStyle = "#4af";
                    ctx.lineWidth = 1.5;
                    ctx.strokeRect(r.x + 0.5, r.y + 0.5, r.w, r.h);
                }
            }
            ctx.restore(); // 클립 해제

            // ── 하단 픽셀 캡션 (한 곳으로 일원화) ──
            // 크롭 없음:            1024 × 1024
            // 회전만(치수 변화):    1024×768 → 768×1024 · ⟳90°
            // 크롭 있음:            1024×1024 → 448×448 @ (320,256)
            // box 는 클램프된 실효값이므로 실제 image_crop 출력 치수와 일치한다.
            const rotMark = c.rotation ? ` · ⟳${c.rotation}°` : "";
            let caption;
            if (box) {
                caption =
                    `${iw}×${ih} → ${box.w}×${box.h}` +
                    ` @ (${box.x},${box.y})` +
                    rotMark;
            } else if (rw !== iw || rh !== ih) {
                caption = `${iw}×${ih} → ${rw}×${rh}${rotMark}`;
            } else {
                caption = `${iw} × ${ih}${rotMark}`;
            }
            ctx.fillStyle = "rgba(255,255,255,0.55)";
            ctx.font = "10px sans-serif";
            ctx.textAlign = "center";
            ctx.fillText(caption, left + availW / 2, areaY + availH + 11, availW);
            ctx.textAlign = "left";

            ctx.restore();
        },

        mouse(event, pos, node) {
            const t = event?.type ?? "";
            if (t !== "pointerdown" && t !== "mousedown") return false;
            for (const chip of this._chips) {
                if (
                    pos[0] >= chip.x &&
                    pos[0] <= chip.x + chip.w &&
                    pos[1] >= chip.y &&
                    pos[1] <= chip.y + chip.h
                ) {
                    ensureProps(node)[PROP_PREVIEW_MODE] = chip.mode;
                    dirty(); // 크기 변경 없음 — draw() 가 현재 공간에 맞춰 그림
                    return true;
                }
            }
            // 미로드 상태에서 프리뷰 영역 클릭 → 처음부터 강제 재시도
            if (this._url && this._state !== "loaded") {
                this.forceReload();
                return true;
            }
            return false;
        },
    };
    return widget;
}

// ─── 크롭 에디터 다이얼로그 (2-1 ~ 2-3) ─────────────────────────

class BMKCropEditor {
    constructor(node) {
        this.node = node;
        this.rotation = ((getWidgetValue(node, "rotation", 0) % 360) + 360) % 360;
        if (this.rotation % 90 !== 0) this.rotation = 0;

        this.crop = null; // {x, y, w, h} — 회전 후 이미지 픽셀 좌표
        const cw = getWidgetValue(node, "crop_width", 0);
        const ch = getWidgetValue(node, "crop_height", 0);
        if (cw > 0 && ch > 0) {
            this.crop = {
                x: getWidgetValue(node, "crop_x", 0),
                y: getWidgetValue(node, "crop_y", 0),
                w: cw,
                h: ch,
            };
        }

        // 종횡비/스냅: 노드 properties 에서 복원 (2-2)
        const props = ensureProps(node);
        this.aspect = parseFloat(props[PROP_EDITOR_ASPECT]) || 0;
        this.snap = SNAP_PRESETS.includes(props[PROP_EDITOR_SNAP])
            ? props[PROP_EDITOR_SNAP]
            : 1;

        this.scale = 1;
        this.source = null; // 회전 적용된 오프스크린 캔버스
        this.drag = null;
        this.numInputs = {};
        this._arLimited = false; // 직전 조작에서 Free 비율 가드가 개입했는지

        this._onKeyDown = (e) => {
            if (e.key === "Escape") {
                e.preventDefault();
                this.close();
            }
        };
    }

    _snapVal(v) {
        return this.snap > 1 ? Math.round(v / this.snap) * this.snap : v;
    }

    _snapPoint(p) {
        return {
            x: clamp(this._snapVal(p.x), 0, this.source.width),
            y: clamp(this._snapVal(p.y), 0, this.source.height),
        };
    }

    // ── DOM 구성 ──
    open() {
        const url = imageURL(getWidgetValue(this.node, "image", ""));
        if (!url) {
            alert("[BMK] 이미지 위젯이 비어 있습니다. 먼저 이미지를 선택하세요.");
            return;
        }

        this.overlay = document.createElement("div");
        Object.assign(this.overlay.style, {
            position: "fixed",
            inset: "0",
            zIndex: "10000",
            background: "rgba(0,0,0,0.65)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
        });
        this.overlay.addEventListener("pointerdown", (e) => {
            if (e.target === this.overlay) this.close();
        });

        this.panel = document.createElement("div");
        Object.assign(this.panel.style, {
            background: "var(--comfy-menu-bg, #202020)",
            color: "var(--fg-color, #ddd)",
            border: "1px solid var(--border-color, #444)",
            borderRadius: "8px",
            padding: "12px",
            display: "flex",
            flexDirection: "column",
            gap: "8px",
            maxWidth: "92vw",
            maxHeight: "92vh",
            boxShadow: "0 8px 32px rgba(0,0,0,0.5)",
            fontFamily: "sans-serif",
            fontSize: "13px",
        });

        const inputStyle = {
            background: "var(--comfy-input-bg, #333)",
            color: "inherit",
            border: "1px solid var(--border-color, #555)",
            borderRadius: "4px",
            padding: "3px 6px",
        };

        const mkButton = (label, onClick, title = "") => {
            const b = document.createElement("button");
            b.textContent = label;
            b.title = title;
            Object.assign(b.style, { ...inputStyle, padding: "4px 10px", cursor: "pointer" });
            b.addEventListener("click", onClick);
            return b;
        };

        const mkSelect = (entries, current, onChange) => {
            const s = document.createElement("select");
            Object.assign(s.style, inputStyle);
            for (const [label, value] of entries) {
                const opt = document.createElement("option");
                opt.textContent = label;
                opt.value = String(value);
                s.appendChild(opt);
            }
            s.value = String(current);
            if (s.selectedIndex < 0) s.selectedIndex = 0;
            s.addEventListener("change", onChange);
            return s;
        };

        // ── 툴바 ──
        const toolbar = document.createElement("div");
        Object.assign(toolbar.style, {
            display: "flex",
            alignItems: "center",
            gap: "8px",
            flexWrap: "wrap",
        });

        toolbar.appendChild(mkButton("⟲ 90°", () => this.rotate(-90), "반시계 방향 회전"));
        toolbar.appendChild(mkButton("⟳ 90°", () => this.rotate(90), "시계 방향 회전"));

        const arLabel = document.createElement("span");
        arLabel.textContent = "종횡비:";
        arLabel.style.marginLeft = "6px";
        toolbar.appendChild(arLabel);

        this.arSelect = mkSelect(AR_PRESETS, this.aspect, () => {
            this.aspect = parseFloat(this.arSelect.value) || 0;
            ensureProps(this.node)[PROP_EDITOR_ASPECT] = this.arSelect.value; // 2-2
            if (this.aspect > 0) {
                this._arLimited = false;
                this._reshapeToAspect();
            } else {
                this._enforceFreeRatio(); // Free 전환 시에도 제한 유지
            }
            this.draw();
        });
        this.arSelect.title =
            `종횡비 고정. Free 는 제한 없음이 아니라 ` +
            `1:${AR_FREE_LIMIT} ~ ${AR_FREE_LIMIT}:1 범위로 자동 클램프됩니다.`;
        toolbar.appendChild(this.arSelect);

        const snapLabel = document.createElement("span");
        snapLabel.textContent = "스냅(px):";
        snapLabel.style.marginLeft = "6px";
        toolbar.appendChild(snapLabel);

        this.snapSelect = mkSelect(
            SNAP_PRESETS.map((n) => [n === 1 ? "1 (off)" : String(n), n]),
            this.snap,
            () => {
                this.snap = parseInt(this.snapSelect.value, 10) || 1;
                ensureProps(this.node)[PROP_EDITOR_SNAP] = this.snap; // 2-2
                for (const inp of Object.values(this.numInputs)) {
                    inp.step = String(this.snap);
                }
            }
        );
        toolbar.appendChild(this.snapSelect);

        toolbar.appendChild(
            mkButton(
                "크롭 해제",
                () => {
                    this.crop = null;
                    this.draw();
                },
                "크롭 없이 전체 이미지 사용"
            )
        );

        this.infoLabel = document.createElement("span");
        this.infoLabel.style.marginLeft = "auto";
        this.infoLabel.style.opacity = "0.8";
        toolbar.appendChild(this.infoLabel);

        // ── 캔버스 ──
        this.canvas = document.createElement("canvas");
        Object.assign(this.canvas.style, {
            borderRadius: "4px",
            cursor: "crosshair",
            touchAction: "none",
            background: "#111",
        });
        this.ctx = this.canvas.getContext("2d");
        this.canvas.addEventListener("pointerdown", (e) => this.onPointerDown(e));
        this.canvas.addEventListener("pointermove", (e) => this.onPointerMove(e));
        this.canvas.addEventListener("pointerup", (e) => this.onPointerUp(e));
        this.canvas.addEventListener("pointercancel", (e) => this.onPointerUp(e));

        // ── 수치 미세 입력 (2-3) ──
        const numRow = document.createElement("div");
        Object.assign(numRow.style, {
            display: "flex",
            alignItems: "center",
            gap: "6px",
            flexWrap: "wrap",
        });
        for (const key of ["x", "y", "w", "h"]) {
            const lab = document.createElement("span");
            lab.textContent = key.toUpperCase() + ":";
            lab.style.opacity = "0.8";
            numRow.appendChild(lab);
            const inp = document.createElement("input");
            inp.type = "number";
            inp.min = "0";
            inp.step = String(this.snap);
            Object.assign(inp.style, { ...inputStyle, width: "76px" });
            inp.addEventListener("change", () => this._commitNumInput(key));
            inp.addEventListener("keydown", (e) => {
                if (e.key === "Enter") this._commitNumInput(key);
            });
            this.numInputs[key] = inp;
            numRow.appendChild(inp);
        }
        const numHint = document.createElement("span");
        numHint.textContent =
            `· 스냅 적용 시 근사값으로 이동, 종횡비 고정 시 W↔H 자동 보정` +
            ` · Free 도 1:${AR_FREE_LIMIT} ~ ${AR_FREE_LIMIT}:1 범위로 제한`;
        numHint.style.opacity = "0.55";
        numRow.appendChild(numHint);

        // ── 하단 버튼 ──
        const footer = document.createElement("div");
        Object.assign(footer.style, {
            display: "flex",
            justifyContent: "flex-end",
            gap: "8px",
        });
        footer.appendChild(mkButton("취소", () => this.close()));
        const applyBtn = mkButton("적용", () => this.apply());
        applyBtn.style.background = "var(--p-button-primary-background, #3a5)";
        footer.appendChild(applyBtn);

        this.panel.appendChild(toolbar);
        this.panel.appendChild(this.canvas);
        this.panel.appendChild(numRow);
        this.panel.appendChild(footer);
        this.overlay.appendChild(this.panel);
        document.body.appendChild(this.overlay);
        document.addEventListener("keydown", this._onKeyDown);

        // 이미지 로드
        this.img = new Image();
        this.img.onload = () => {
            this.rebuildSource();
            this._enforceFreeRatio(); // 복원한 크롭이 제한을 벗어난 경우 보정
            this.draw();
        };
        this.img.onerror = () => {
            alert("[BMK] 이미지를 불러오지 못했습니다.");
            this.close();
        };
        this.img.src = url;
    }

    close() {
        document.removeEventListener("keydown", this._onKeyDown);
        this.overlay?.remove();
        this.overlay = null;
    }

    // ── 회전 소스 캔버스 ──
    rebuildSource() {
        this.source = makeRotatedCanvas(this.img, this.rotation);
        const c = this.source;
        // 표시 스케일: 뷰포트에 맞춤 (종횡비 유지 → 찌그러짐 없음)
        const maxW = Math.min(window.innerWidth * 0.85, 1400);
        const maxH = window.innerHeight * 0.66;
        this.scale = Math.min(maxW / c.width, maxH / c.height, 1);
        this.canvas.width = Math.max(1, Math.round(c.width * this.scale));
        this.canvas.height = Math.max(1, Math.round(c.height * this.scale));
    }

    rotate(deltaDeg) {
        this.rotation = (((this.rotation + deltaDeg) % 360) + 360) % 360;
        // 좌표계가 바뀌므로 기존 크롭은 초기화 (혼동 방지)
        this.crop = null;
        this.rebuildSource();
        this.draw();
    }

    // 기존 크롭을 중심 유지한 채 현재 종횡비로 재구성
    _reshapeToAspect() {
        if (!(this.aspect > 0) || !this.crop || !this.source) return;
        const c = this.crop;
        const iw = this.source.width;
        const ih = this.source.height;
        const cx = c.x + c.w / 2;
        const cy = c.y + c.h / 2;
        let w = c.w;
        let h = w / this.aspect;
        if (h > ih) {
            h = ih;
            w = h * this.aspect;
        }
        if (w > iw) {
            w = iw;
            h = w / this.aspect;
        }
        c.w = w;
        c.h = h;
        c.x = clamp(cx - w / 2, 0, iw - w);
        c.y = clamp(cy - h / 2, 0, ih - h);
    }

    // 현재 크롭을 Free 모드 비율 제한 안으로 보정 (Free 모드에서만 동작).
    // 호출 지점: 에디터 열기 직후(위젯에서 복원한 값 / 구 워크플로우 /
    // crop_* 위젯 직접 편집 대응), Free 로 전환할 때, 적용 직전 최종 방어.
    // 위젯에는 "적용" 을 눌러야 기록되므로 이 보정 자체는 비파괴적이다.
    _enforceFreeRatio() {
        if (!this.crop || !this.source || this.aspect > 0) return false;
        const iw = this.source.width;
        const ih = this.source.height;
        const c = this.crop;
        const g = limitFreeRatio(c.w, c.h, iw - c.x, ih - c.y);
        if (!g.limited) return false;
        c.w = clamp(g.w, 1, iw);
        c.h = clamp(g.h, 1, ih);
        c.x = clamp(c.x, 0, iw - c.w);
        c.y = clamp(c.y, 0, ih - c.h);
        this._arLimited = true;
        console.warn(
            `[BMK] 크롭 종횡비가 허용 범위(1:${AR_FREE_LIMIT} ~ ${AR_FREE_LIMIT}:1)를 ` +
                `벗어나 ${Math.round(c.w)}×${Math.round(c.h)} 로 보정했습니다.`
        );
        return true;
    }

    // ── 수치 입력 커밋 (2-3) ──
    _commitNumInput(changedKey) {
        if (!this.source) return;
        const iw = this.source.width;
        const ih = this.source.height;
        if (!this.crop) this.crop = { x: 0, y: 0, w: iw, h: ih };
        const c = this.crop;

        const read = (key, fallback) => {
            const v = parseFloat(this.numInputs[key].value);
            return Number.isFinite(v) ? v : fallback;
        };
        let x = read("x", c.x);
        let y = read("y", c.y);
        let w = read("w", c.w);
        let h = read("h", c.h);

        // 스냅 적용: 변경한 필드를 근사값(배수)으로 이동
        if (changedKey === "x") x = this._snapVal(x);
        if (changedKey === "y") y = this._snapVal(y);
        if (changedKey === "w") w = this._snapVal(w);
        if (changedKey === "h") h = this._snapVal(h);

        // 종횡비 고정: W 변경 → H 보정, H 변경 → W 보정 (파생값은 스냅보다 AR 우선)
        if (this.aspect > 0) {
            if (changedKey === "w") h = w / this.aspect;
            else if (changedKey === "h") w = h * this.aspect;
        }

        w = clamp(w, 1, iw);
        h = clamp(h, 1, ih);
        if (this.aspect > 0) {
            // 클램프로 AR 이 깨졌으면 한 번 더 정합
            if (w / h > this.aspect + 1e-6) w = h * this.aspect;
            else if (w / h < this.aspect - 1e-6) h = w / this.aspect;
        } else {
            // Free 모드 극단 AR 가드 — 방금 입력한 축의 값을 존중하고 반대
            // 축을 허용 범위 안으로 보정한다. 보정값이 이미지를 벗어나면
            // 그때 입력한 축을 대신 줄인다.
            const lim = AR_FREE_LIMIT;
            this._arLimited = false;
            if (changedKey === "h") {
                if (w < h / lim || w > h * lim) {
                    w = clamp(clamp(w, h / lim, h * lim), 1, iw);
                    h = clamp(h, w / lim, w * lim);
                    this._arLimited = true;
                }
            } else if (h < w / lim || h > w * lim) {
                h = clamp(clamp(h, w / lim, w * lim), 1, ih);
                w = clamp(w, h / lim, h * lim);
                this._arLimited = true;
            }
        }
        x = clamp(x, 0, iw - w);
        y = clamp(y, 0, ih - h);

        this.crop = { x, y, w, h };
        this.draw();
    }

    _syncNumInputs() {
        const active = document.activeElement;
        const setIfIdle = (key, v) => {
            const inp = this.numInputs[key];
            if (inp && inp !== active) inp.value = String(Math.round(v));
        };
        if (this.crop) {
            setIfIdle("x", this.crop.x);
            setIfIdle("y", this.crop.y);
            setIfIdle("w", this.crop.w);
            setIfIdle("h", this.crop.h);
        } else {
            for (const key of ["x", "y", "w", "h"]) {
                const inp = this.numInputs[key];
                if (inp && inp !== active) inp.value = "";
            }
        }
    }

    // ── 렌더링 ──
    draw() {
        if (!this.source) return;
        const { ctx, canvas } = this;
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        ctx.imageSmoothingQuality = "high";
        ctx.drawImage(this.source, 0, 0, canvas.width, canvas.height);

        if (this.crop) {
            const s = this.scale;
            const r = {
                x: this.crop.x * s,
                y: this.crop.y * s,
                w: this.crop.w * s,
                h: this.crop.h * s,
            };

            // 크롭 외부 어둡게
            ctx.save();
            ctx.fillStyle = "rgba(0,0,0,0.55)";
            ctx.beginPath();
            ctx.rect(0, 0, canvas.width, canvas.height);
            ctx.rect(r.x, r.y, r.w, r.h);
            ctx.fill("evenodd");
            ctx.restore();

            // 테두리 + 삼분할선
            ctx.strokeStyle = "#4af";
            ctx.lineWidth = 1.5;
            ctx.strokeRect(r.x + 0.5, r.y + 0.5, r.w, r.h);
            ctx.strokeStyle = "rgba(255,255,255,0.35)";
            ctx.lineWidth = 1;
            ctx.beginPath();
            for (let i = 1; i <= 2; i++) {
                ctx.moveTo(r.x + (r.w * i) / 3, r.y);
                ctx.lineTo(r.x + (r.w * i) / 3, r.y + r.h);
                ctx.moveTo(r.x, r.y + (r.h * i) / 3);
                ctx.lineTo(r.x + r.w, r.y + (r.h * i) / 3);
            }
            ctx.stroke();

            // 코너 핸들
            ctx.fillStyle = "#4af";
            for (const [hx, hy] of this.handlePositions(r)) {
                ctx.fillRect(hx - 4, hy - 4, 8, 8);
            }

            const ar = this.crop.w / Math.max(1e-6, this.crop.h);
            const arText =
                ar >= 1 ? `${ar.toFixed(2)}:1` : `1:${(1 / ar).toFixed(2)}`;
            this.infoLabel.textContent =
                `${Math.round(this.crop.w)} × ${Math.round(this.crop.h)} px` +
                `  (x:${Math.round(this.crop.x)}, y:${Math.round(this.crop.y)})` +
                `  · ${arText}` +
                (this._arLimited ? `  ⚠ 비율 제한 적용` : "");
            this.infoLabel.style.color = this._arLimited ? "#fc6" : "";
        } else {
            this.infoLabel.textContent =
                `${this.source.width} × ${this.source.height} px (크롭 없음 — 드래그로 지정)`;
            this.infoLabel.style.color = "";
        }
        this._syncNumInputs();
    }

    handlePositions(r) {
        return [
            [r.x, r.y], // nw
            [r.x + r.w, r.y], // ne
            [r.x, r.y + r.h], // sw
            [r.x + r.w, r.y + r.h], // se
        ];
    }

    // ── 마우스 상호작용 ──
    toImageCoords(e) {
        const rect = this.canvas.getBoundingClientRect();
        const x = ((e.clientX - rect.left) / rect.width) * this.source.width;
        const y = ((e.clientY - rect.top) / rect.height) * this.source.height;
        return {
            x: clamp(x, 0, this.source.width),
            y: clamp(y, 0, this.source.height),
        };
    }

    hitTest(e) {
        if (!this.crop) return null;
        const rect = this.canvas.getBoundingClientRect();
        const sx = e.clientX - rect.left;
        const sy = e.clientY - rect.top;
        const s = this.scale;
        const r = {
            x: this.crop.x * s,
            y: this.crop.y * s,
            w: this.crop.w * s,
            h: this.crop.h * s,
        };
        const names = ["nw", "ne", "sw", "se"];
        const positions = this.handlePositions(r);
        for (let i = 0; i < positions.length; i++) {
            const [hx, hy] = positions[i];
            if (Math.hypot(sx - hx, sy - hy) <= HANDLE_HIT_RADIUS) {
                return names[i];
            }
        }
        if (sx >= r.x && sx <= r.x + r.w && sy >= r.y && sy <= r.y + r.h) {
            return "move";
        }
        return null;
    }

    onPointerDown(e) {
        if (!this.source) return;
        e.preventDefault();
        this.canvas.setPointerCapture(e.pointerId);
        const p = this.toImageCoords(e);
        const hit = this.hitTest(e);

        if (hit === "move") {
            this.drag = {
                mode: "move",
                offX: p.x - this.crop.x,
                offY: p.y - this.crop.y,
            };
        } else if (hit) {
            // 코너 리사이즈: 반대편 코너를 앵커로 고정
            const c = this.crop;
            const anchor = {
                nw: { x: c.x + c.w, y: c.y + c.h },
                ne: { x: c.x, y: c.y + c.h },
                sw: { x: c.x + c.w, y: c.y },
                se: { x: c.x, y: c.y },
            }[hit];
            this.drag = { mode: "resize", anchor };
        } else {
            // 새 크롭 시작 — 앵커를 스냅 격자에 정렬 (2-1)
            const a = this._snapPoint(p);
            this.drag = { mode: "new", anchor: a };
            this.crop = { x: a.x, y: a.y, w: 1, h: 1 };
        }
        this.draw();
    }

    onPointerMove(e) {
        if (!this.source) return;
        if (!this.drag) {
            const hit = this.hitTest(e);
            this.canvas.style.cursor =
                hit === "move"
                    ? "move"
                    : hit === "nw" || hit === "se"
                      ? "nwse-resize"
                      : hit === "ne" || hit === "sw"
                        ? "nesw-resize"
                        : "crosshair";
            return;
        }
        const p = this.toImageCoords(e);

        if (this.drag.mode === "move") {
            const c = this.crop;
            // 이동도 스냅 격자에 정렬 후 경계 클램프 (경계에서는 클램프 우선)
            c.x = clamp(this._snapVal(p.x - this.drag.offX), 0, this.source.width - c.w);
            c.y = clamp(this._snapVal(p.y - this.drag.offY), 0, this.source.height - c.h);
        } else {
            // new / resize: 포인터를 스냅 격자에 정렬해 사각형 계산 (2-1)
            this.crop = this.rectFromAnchor(this.drag.anchor, this._snapPoint(p));
        }
        this.draw();
    }

    onPointerUp(e) {
        if (this.drag) {
            this.canvas.releasePointerCapture?.(e.pointerId);
            // 극소 크롭(오클릭)은 취소로 간주
            if (this.crop && (this.crop.w < 4 || this.crop.h < 4)) {
                if (this.drag.mode === "new") this.crop = null;
            }
            this.drag = null;
            this.draw();
        }
    }

    // 앵커(고정점)와 현재 포인터로 크롭 사각형 계산 (종횡비 고정 포함)
    rectFromAnchor(anchor, p) {
        const iw = this.source.width;
        const ih = this.source.height;
        const dx = p.x - anchor.x;
        const dy = p.y - anchor.y;
        const sx = dx < 0 ? -1 : 1;
        const sy = dy < 0 ? -1 : 1;
        let w = Math.abs(dx);
        let h = Math.abs(dy);

        if (this.aspect > 0) {
            // 지배축 기준으로 다른 축을 종횡비에 맞춤 (파생축은 스냅보다 AR 우선)
            if (w / this.aspect >= h) h = w / this.aspect;
            else w = h * this.aspect;
            const availW = sx > 0 ? iw - anchor.x : anchor.x;
            const availH = sy > 0 ? ih - anchor.y : anchor.y;
            if (w > availW) {
                w = availW;
                h = w / this.aspect;
            }
            if (h > availH) {
                h = availH;
                w = h * this.aspect;
            }
            this._arLimited = false; // 프리셋 AR 은 항상 허용 범위 안
        } else {
            const availW = sx > 0 ? iw - anchor.x : anchor.x;
            const availH = sy > 0 ? ih - anchor.y : anchor.y;
            w = Math.min(w, availW);
            h = Math.min(h, availH);
            // Free 모드 극단 AR 가드 (파생축은 스냅보다 가드 우선)
            const g = limitFreeRatio(w, h, availW, availH);
            w = g.w;
            h = g.h;
            this._arLimited = g.limited;
        }

        return {
            x: sx > 0 ? anchor.x : anchor.x - w,
            y: sy > 0 ? anchor.y : anchor.y - h,
            w,
            h,
        };
    }

    // ── 위젯 반영 ──
    apply() {
        this._enforceFreeRatio(); // 위젯 기록 직전 최종 방어
        setWidgetValue(this.node, "rotation", this.rotation);
        if (this.crop && this.crop.w >= 1 && this.crop.h >= 1) {
            setWidgetValue(this.node, "crop_x", Math.round(this.crop.x));
            setWidgetValue(this.node, "crop_y", Math.round(this.crop.y));
            setWidgetValue(this.node, "crop_width", Math.round(this.crop.w));
            setWidgetValue(this.node, "crop_height", Math.round(this.crop.h));
        } else {
            setWidgetValue(this.node, "crop_x", 0);
            setWidgetValue(this.node, "crop_y", 0);
            setWidgetValue(this.node, "crop_width", 0);
            setWidgetValue(this.node, "crop_height", 0);
        }
        fitNode(this.node); // 최소 크기 보장(축소 없음) + 프리뷰 갱신
        this.close();
    }
}

// ─── 확장 등록 ───────────────────────────────────────────────────

app.registerExtension({
    name: "BMK.LoadImageCrop",
    beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== NODE_NAME) return;

        const origOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            origOnNodeCreated?.apply(this, arguments);

            // serialize:false — 버튼이 widgets_values 슬롯을 차지하지 않게 한다.
            this.addWidget(
                "button",
                "✂ Crop Editor",
                null,
                () => {
                    new BMKCropEditor(this).open();
                },
                { serialize: false }
            );
            this.addWidget(
                "button",
                "⟲ Reset Crop",
                null,
                () => {
                    resetCrop(this); // 1-4: 에디터를 열지 않고 크롭 해제
                },
                { serialize: false }
            );

            const preview = makePreviewWidget(this);
            if (this.addCustomWidget) this.addCustomWidget(preview);
            else this.widgets.push(preview);

            // 업로드 완료·동일 파일 재선택 등 콤보 콜백이 불릴 때마다
            // 프리뷰를 처음부터 다시 로드한다 (값이 같아도 갱신 보장).
            const imageWidget = getWidget(this, "image");
            if (imageWidget) {
                const origCb = imageWidget.callback;
                imageWidget.callback = function (...args) {
                    const r = origCb?.apply(this, args);
                    preview.forceReload();
                    return r;
                };
            }

            suppressDefaultPreview(this);
            arrangeWidgets(this); // 1-1: configure 이전에 순서 확정
            fitNode(this);
        };

        // ─── widgets_values 정규화 ──────────────────────────────────
        // 저장: 항상 "JS 미적용 환경의 위젯 순서"로 강제한다.
        //   정규 순서 = [image, rotation, crop_x, crop_y, crop_width,
        //                crop_height, upload]
        //   (Python 이 image 에 image_upload:true 를 주면 프론트엔드가
        //    upload 위젯을 맨 뒤에 붙이므로 이 순서가 기준이 된다)
        // 로드: 위치·길이에 의존하지 않고 값을 이름으로 다시 주입한다.
        //   v1  (8): [image, rot, x, y, w, h, upload, editor]
        //   v2 (10): [image, upload, btn, btn, rot, x, y, w, h, preview]
        //   v3  (7): [image, rot, x, y, w, h, upload]   ← 정규
        const PARAM_NAMES = [
            "rotation",
            "crop_x",
            "crop_y",
            "crop_width",
            "crop_height",
        ];

        // 역직렬화 중에는 콜백 부작용을 피하기 위해 값만 대입한다.
        const assign = (node, name, value) => {
            const w = getWidget(node, name);
            if (w) w.value = value;
        };

        const origOnSerialize = nodeType.prototype.onSerialize;
        nodeType.prototype.onSerialize = function (info) {
            origOnSerialize?.apply(this, arguments);
            info.widgets_values = [
                getWidgetValue(this, "image", ""),
                ...PARAM_NAMES.map((n) => getWidgetValue(this, n, 0)),
                "image", // upload 위젯 값 (core 규약: 대상 위젯 이름)
            ];
        };

        const origConfigure = nodeType.prototype.configure;
        nodeType.prototype.configure = function (info) {
            // 숫자 5개가 연속으로 나오는 첫 지점을 파라미터 블록으로 본다.
            const v = info?.widgets_values;
            let picked = null;
            if (Array.isArray(v) && typeof v[0] === "string") {
                for (let i = 1; i + 4 < v.length; i++) {
                    if (
                        v
                            .slice(i, i + 5)
                            .every((n) => typeof n === "number" && isFinite(n))
                    ) {
                        picked = { image: v[0], nums: v.slice(i, i + 5) };
                        break;
                    }
                }
            }

            const r = origConfigure?.apply(this, arguments);

            // 위치 기반 복원이 어떻게 됐든 이름 기준으로 덮어쓴다.
            if (picked) {
                assign(this, "image", picked.image);
                PARAM_NAMES.forEach((n, i) => assign(this, n, picked.nums[i]));
            }
            arrangeWidgets(this); // 값 주입 후 시각적 순서 재확정
            return r;
        };
    },
});
