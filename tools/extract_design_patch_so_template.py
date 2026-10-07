# -*- coding: utf-8 -*-
r"""Design Patch SO 템플릿 추출기 — bmk_design_patch_psd.py 의 SO_TEMPLATE_* 상수를 재현한다.

Photoshop 2026 이 쓴 05 PSD 의 템플릿 SO 레이어 '2026-09-11_16-59-23_edit_2' 에서
  - SoLd(SMART_OBJECT_LAYER_DATA1) 디스크립터, PlLd(PLACED_LAYER2), lnk2 항목의 open_file 디스크립터,
  - lnk2 v8 항목 꼬리(psd_tools 가 읽지 않는 117B: 디스크립터 v16 {contentID})
를 뽑아 uuid(Idnt / placed / PlLd uuid / contentID)를 0 으로 정규화한 뒤 base64 상수로 만든다.

검사(항상 수행):
  1) psd_tools 재직렬화 멱등(frombytes(tobytes(x)).tobytes() == tobytes(x)).
  2) 정규화 상수 + 원래 uuid/파일명/데이터로 만든 lnk2 항목 == 05 원본 lnk2 항목 bytes (꼬리 포함, 완전 일치).
  3) 정규화 상수 + 원래 uuid 로 만든 SoLd/PlLd == psd_tools 가 템플릿을 직렬화한 bytes
     (so_synth_v2 가 쓴 것과 같은 경로. Photoshop 원본 raw bytes 와의 일치 여부는 참고로만 출력).
  4) 상수에 경로·파일명·개인정보 흔적이 없는지(드라이브 경로, file://, Users, @, 확장자 등) 검사.

실행 (ComfyUI-Easy-Install 루트에서):
    python_embeded\python.exe -X utf8 ComfyUI\custom_nodes\ComfyUI_BMK_Nodes\tools\extract_design_patch_so_template.py [psd] [--write|--check]
  (인자 없음)  상수 블록을 출력하고 모듈 상수와 비교 결과를 보여 준다.
  --check      모듈 상수와 다르면 종료 코드 1.
  --write      bmk_design_patch_psd.py 의 '# >>> SO_TEMPLATE_BEGIN' ~ '# <<< SO_TEMPLATE_END' 사이를 교체.
"""
from __future__ import annotations

import base64
import copy
import io
import os
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
PKG = _HERE.parents[1]
MODULE_PATH = PKG / "bmk_design_patch_psd.py"
sys.path.insert(0, str(PKG))

import bmk_design_patch_psd as dpsd  # noqa: E402
from psd_tools import PSDImage  # noqa: E402
from psd_tools.constants import LinkedLayerType, Tag  # noqa: E402
from psd_tools.psd.descriptor import DescriptorBlock  # noqa: E402
from psd_tools.psd.tagged_blocks import PlacedLayerData, SmartObjectLayerData  # noqa: E402

DEFAULT_PSD = r"H:\BmkNodeDesign\MultiLayerCropEdit\05.디자인보정 결과.psd"
TEMPLATE_LAYER = "2026-09-11_16-59-23_edit_2"
ZERO = dpsd._ZERO_UUID
SOURCE_TEXT = "05 디자인보정 결과 PSD (Photoshop 2026), SO layer 2026-09-11_16-59-23_edit_2, lnk2 v8"

# 상수에 있으면 안 되는 흔적 (latin-1 / utf-16-be 로 디코드한 텍스트에서 검색)
_SUSPECT = [
    (re.compile(r"[A-Za-z]:[\\/]"), "드라이브 경로"),
    (re.compile(r"file:", re.IGNORECASE), "file URL"),
    (re.compile(r"[\\/](Users|home|AppData)[\\/]", re.IGNORECASE), "사용자 폴더"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "이메일"),
    (re.compile(r"\.(png|psd|psb|jpe?g|webp|tiff?)\b", re.IGNORECASE), "파일 확장자"),
    (re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE), "uuid(0 정규화 안 됨)"),
]


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _zero_uuid_text(v: str) -> str:
    core = v.rstrip("\x00")
    if len(core) != 36:
        raise ValueError(f"uuid 길이 이상: {v!r}")
    return ZERO + v[len(core):]


def extract(psd_path: str = DEFAULT_PSD) -> dict:
    """05 PSD 에서 정규화된 템플릿 상수와 검사 결과를 돌려준다."""
    psd = PSDImage.open(psd_path)
    tmpl = [x for x in psd.descendants() if x.name == TEMPLATE_LAYER and x.kind == "smartobject"]
    if len(tmpl) != 1:
        raise ValueError(f"템플릿 레이어 {TEMPLATE_LAYER!r} 가 {len(tmpl)}개")
    t = tmpl[0]
    tb = t._record.tagged_blocks
    sold0 = tb.get_data(Tag.SMART_OBJECT_LAYER_DATA1)
    plld0 = tb.get_data(Tag.PLACED_LAYER2)
    item0 = t.smart_object._data
    if item0 is None or item0.kind != LinkedLayerType.DATA:
        raise ValueError("템플릿 SO 가 embedded(liFD) 가 아님")
    report: list[str] = []

    # ── 1. SoLd / PlLd 정규화 ──
    orig_idnt = sold0.data[b"Idnt"].value
    orig_placed = sold0.data[b"placed"].value
    sold = copy.deepcopy(sold0)
    sold.data[b"Idnt"].value = _zero_uuid_text(orig_idnt)
    sold.data[b"placed"].value = _zero_uuid_text(orig_placed)
    sold_b = sold.tobytes()
    plld = copy.deepcopy(plld0)
    orig_pl_uuid = plld0.uuid
    plld.uuid = ZERO.encode("ascii")
    plld_b = plld.tobytes()
    assert SmartObjectLayerData.frombytes(sold_b).tobytes() == sold_b, "SoLd 재직렬화 멱등 실패"
    assert PlacedLayerData.frombytes(plld_b).tobytes() == plld_b, "PlLd 재직렬화 멱등 실패"
    report.append(f"SoLd {len(sold_b)}B / PlLd {len(plld_b)}B 재직렬화 멱등 OK")

    # 정규화 상수 + 원래 uuid → psd_tools 의 템플릿 직렬화와 같아야 함
    s2 = SmartObjectLayerData.frombytes(sold_b)
    s2.data[b"Idnt"].value = orig_idnt
    s2.data[b"placed"].value = orig_placed
    p2 = PlacedLayerData.frombytes(plld_b)
    p2.uuid = orig_pl_uuid
    ok_sold = s2.tobytes() == sold0.tobytes()
    ok_plld = p2.tobytes() == plld0.tobytes()
    report.append(f"SoLd == psd_tools(템플릿) {ok_sold}, PlLd == psd_tools(템플릿) {ok_plld}")

    # ── 2. open_file / 항목 필드 ──
    of_b = item0.open_file.tobytes(padding=1)
    assert DescriptorBlock.frombytes(of_b, padding=1).tobytes(padding=1) == of_b, "open_file 재직렬화 멱등 실패"
    fields = {
        "version": int(item0.version),
        "filetype": item0.filetype.decode("latin-1"),
        "creator_hex": item0.creator.hex(),
        "child_id": item0.child_id,
        "mod_time": float(item0.mod_time),
        "lock_state": int(item0.lock_state),
    }
    if fields["version"] != 8 or fields["filetype"] != "png " or fields["child_id"].strip("\x00") != "":
        raise ValueError(f"템플릿 항목 필드가 예상과 다름: {fields}")

    # ── 3. raw lnk2 에서 꼬리 ──
    w = dpsd.raw_walk(psd_path, want_raw=(b"lnk2", b"SoLd", b"PlLd"))
    gl = {b["key"]: b for b in w["global"]}
    items, consumed = dpsd.lnk2_items(gl[b"lnk2"]["raw"])
    if consumed != gl[b"lnk2"]["len"]:
        raise ValueError("05 lnk2 분할 소비량 불일치")
    body = [it for it in items if it["uuid"].decode("latin-1") == item0.uuid]
    if len(body) != 1:
        raise ValueError("템플릿 lnk2 항목을 raw 에서 찾지 못함")
    body = body[0]["body"]
    tail = dpsd.lnk2_item_tail(body)
    if len(tail) != dpsd.LNK2_TAIL_LEN or b"contentID" not in tail:
        raise ValueError(f"꼬리 이상 {len(tail)}B")
    orig_cid = tail[-74:-2].decode("utf-16-be")
    tail_n = tail.replace(orig_cid.encode("utf-16-be"), ZERO.encode("utf-16-be"))
    assert len(tail_n) == len(tail) and tail_n.count(ZERO.encode("utf-16-be")) == 1
    vers = sorted({it["ver"] for it in items})
    tails_len = sorted({len(dpsd.lnk2_item_tail(it["body"])) for it in items})
    report.append(f"05 lnk2 항목 {len(items)}개 버전 {vers} 꼬리 길이 {tails_len}")

    # 정규화 상수 + 원래 값으로 항목 재구성 == 원본 raw 항목
    rebuilt = dpsd._LinkedLayerV8(
        kind=LinkedLayerType.DATA, version=fields["version"], uuid=item0.uuid, filename=item0.filename,
        filetype=fields["filetype"].encode("latin-1"), creator=bytes.fromhex(fields["creator_hex"]), filesize=None,
        open_file=DescriptorBlock.frombytes(of_b, padding=1), linked_file=None, timestamp=None, data=item0.data,
        child_id=fields["child_id"], mod_time=fields["mod_time"], lock_state=fields["lock_state"],
        tail=tail_n.replace(ZERO.encode("utf-16-be"), orig_cid.encode("utf-16-be")))
    buf = io.BytesIO()
    rebuilt.write(buf)
    ok_item = buf.getvalue() == body
    report.append(f"lnk2 항목 재구성 == 05 원본 bytes(꼬리 포함) {ok_item} ({len(body)}B)")

    # 참고: Photoshop raw SoLd/PlLd 와 psd_tools 직렬화 비교
    idnt16 = orig_idnt.rstrip("\x00").encode("utf-16-be")
    rec = [r for r in w["layers"] if any(b["key"] == b"SoLd" and "raw" in b and idnt16 in b["raw"] for b in r["blocks"])]
    if rec:
        bb = {b["key"]: b["raw"] for b in rec[0]["blocks"] if "raw" in b}
        report.append("참고: PS raw SoLd == psd_tools 직렬화 %s, PS raw PlLd == psd_tools 직렬화 %s" % (
            bb.get(b"SoLd") == sold0.tobytes(), bb.get(b"PlLd") == plld0.tobytes()))

    consts = {
        "SO_TEMPLATE_SOURCE": SOURCE_TEXT,
        "SO_TEMPLATE_SOLD_B64": _b64(sold_b),
        "SO_TEMPLATE_PLLD_B64": _b64(plld_b),
        "SO_TEMPLATE_OPEN_FILE_B64": _b64(of_b),
        "SO_TEMPLATE_TAIL_B64": _b64(tail_n),
        "SO_TEMPLATE_ITEM_FIELDS": fields,
    }
    ok = ok_sold and ok_plld and ok_item
    return {"consts": consts, "report": report, "ok": ok}


def check_clean(consts: dict) -> list[str]:
    """상수 bytes 를 latin-1 / utf-16-be 로 디코드해 경로·개인정보 흔적을 찾는다. 문제 목록 반환."""
    issues = []
    for key in ("SO_TEMPLATE_SOLD_B64", "SO_TEMPLATE_PLLD_B64", "SO_TEMPLATE_OPEN_FILE_B64", "SO_TEMPLATE_TAIL_B64"):
        raw = base64.b64decode(consts[key])
        texts = [raw.decode("latin-1")]
        for off in (0, 1):
            seg = raw[off:]
            seg = seg[:len(seg) // 2 * 2]
            texts.append(seg.decode("utf-16-be", "replace"))
        for txt in texts:
            for rx, what in _SUSPECT:
                for m in rx.finditer(txt):
                    if m.group(0).lower() == ZERO:
                        continue  # 0 으로 정규화된 uuid 는 허용
                    issues.append(f"{key}: {what} 의심 {m.group(0)!r}")
    return sorted(set(issues))


def _wrap(s: str, width: int = 96, indent: str = "    ") -> str:
    parts = [s[i:i + width] for i in range(0, len(s), width)] or [""]
    return "(\n" + "\n".join(f'{indent}"{p}"' for p in parts) + "\n)"


def render_block(consts: dict) -> str:
    lines = [f"SO_TEMPLATE_SOURCE = {consts['SO_TEMPLATE_SOURCE']!r}"]
    for key in ("SO_TEMPLATE_SOLD_B64", "SO_TEMPLATE_PLLD_B64", "SO_TEMPLATE_OPEN_FILE_B64", "SO_TEMPLATE_TAIL_B64"):
        lines.append(f"{key} = {_wrap(consts[key])}")
    lines.append(f"SO_TEMPLATE_ITEM_FIELDS = {consts['SO_TEMPLATE_ITEM_FIELDS']!r}")
    return "\n".join(lines)


def compare_with_module(consts: dict) -> list[str]:
    diffs = []
    for key, val in consts.items():
        if key == "SO_TEMPLATE_SOURCE":
            continue
        if getattr(dpsd, key) != val:
            diffs.append(key)
    return diffs


def write_module(consts: dict) -> None:
    text = MODULE_PATH.read_text(encoding="utf-8")
    begin, end = "# >>> SO_TEMPLATE_BEGIN\n", "# <<< SO_TEMPLATE_END"
    i0, i1 = text.index(begin) + len(begin), text.index(end)
    new = text[:i0] + render_block(consts) + "\n" + text[i1:]
    MODULE_PATH.write_text(new, encoding="utf-8", newline="\n")


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    flags = {a for a in argv if a.startswith("--")}
    psd_path = args[0] if args else DEFAULT_PSD
    res = extract(psd_path)
    for line in res["report"]:
        print(" -", line)
    issues = check_clean(res["consts"])
    print(" - 개인정보/경로 검사:", "깨끗함" if not issues else issues)
    if not res["ok"] or issues:
        print("추출 검사 실패")
        return 1
    if "--write" in flags:
        write_module(res["consts"])
        print("모듈 상수 갱신:", MODULE_PATH)
        return 0
    diffs = compare_with_module(res["consts"])
    if "--check" in flags:
        print("모듈 상수 일치" if not diffs else f"모듈 상수 불일치: {diffs}")
        return 0 if not diffs else 1
    print()
    print(render_block(res["consts"]))
    print()
    print("모듈 상수와 비교:", "일치" if not diffs else f"불일치 {diffs}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
