"""서버 모드의 크롭별 지시문 편집·프롬프트 미리보기."""

import asyncio
import json
import ntpath
import os
from datetime import datetime, timezone

try:
    from . import bmk_design_patch_board as board
    from . import bmk_design_patch_prompt as dprompt
    from . import bmk_design_patch_store as store
except ImportError:
    import bmk_design_patch_board as board
    import bmk_design_patch_prompt as dprompt
    import bmk_design_patch_store as store


_REQUIRED = ("root", "crop_id", "seen", "crop_sig")
_FIELDS = ("purpose", "user_instruction_ko", "ai_auto")


def _crop(root, m, crop_id):
    if not isinstance(crop_id, str) or not board._CROP_ID_RE.fullmatch(crop_id):
        raise board._Reject(400, "crop_id 형식이 잘못되었습니다")
    crop = board._crop(m, crop_id)
    if crop is None:
        layout = board._layout_or_none(root)
        if layout and board._crop(layout, crop_id):
            raise board._Reject(409, "먼저 반영이 필요합니다", reason="apply_needed")
        raise board._Reject(404, "크롭이 없습니다")
    return crop


def _inputs(root, m, crop, ov):
    prepare = board._route_settings(root, m)["settings"]["prepare"]
    models = board.pl._models()
    try:
        model = models.effective_gen(prepare, store.crop_gen_entries(root).get(crop["id"]))["model"]
    except ValueError as exc:
        raise board._Reject(422, str(exc)) from exc
    refs_dir = (m.get("source") or {}).get("refs_dir") or ""
    spec = dprompt.spec_from_manifest(crop, None, refs_dir, store.project_glossary(root, m), ov)
    roles = dprompt.v2_roles(spec, root)  # Prepare 처럼 표기(ref_style)를 채우기 전의 스펙으로
    if "ref_style" not in (ov or {}):  # Prepare 와 같게: 스펙에 없으면 실행 설정의 표기
        spec["ref_style"] = prepare["ref_style"]
    return model, spec, roles, dprompt.render(spec)


def _linked(root, crop_id):
    """레퍼런스 연결 파일(refs.json)에서 이 크롭의 연결만 뽑은 글. 다른 크롭의 연결 편집은 넣지 않는다 — 넣으면 남의 편집 때문에
    이 크롭에서 쓰던 글·AI 작성 결과가 낡은 판으로 버려진다. 파일이 없는 것과 이 크롭의 연결이 없는 것은 같은 값이다(다른 크롭의
    첫 연결로 파일이 생겨도 그대로). 파일이 있는데 읽지 못하면 파일 지문 전체."""
    path = os.path.join(root, store._REFS_FILE)
    if not os.path.exists(path):
        return "null"
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        return json.dumps((doc.get("links") or {}).get(crop_id), ensure_ascii=False, sort_keys=True)
    except (OSError, ValueError, AttributeError):
        return json.dumps(store._file_fp(path))


def _token(vlm, root, crop_id, roles):
    """화면이 본 판의 표지 = 스펙 파일 지문 + 레퍼런스 구성 지문. 스펙 파일이 그대로여도 연결된 그림이 바뀌면(다른 창의 레퍼런스 편집·
    반영) 화면이 보던 @imageN 이 다른 그림이 된다 — 그 판에서 쓴 글을 저장하지 않게 표지에 함께 넣는다."""
    # 이 크롭의 레퍼런스 연결(refs.json)도 넣는다 — 연결을 바꿨지만 아직 반영 전이면 매니페스트의 구성은 그대로라 roles 만으로는 모른다.
    return (vlm._spec_seen(vlm._spec_path(root, crop_id)) + "."
            + dprompt.v2_input_hash({}, "", [*roles, ["refs_link", _linked(root, crop_id)]], manual=True)[:12])


def spec_view(root: str, crop_id: str) -> dict:
    """계정 범위 검사가 끝난 프로젝트에서 저장 시점의 토큰과 편집 미리보기를 읽는다."""
    vlm = board._vlm()
    with store.manifest_lock(root):
        m = store.load_manifest(root)
        crop = _crop(root, m, crop_id)
        ov, error = vlm._load_spec(root, crop_id)
        error = error or vlm._targets_error(ov)
        if error:
            raise board._Reject(422, error)
        model, spec, roles, rendered = _inputs(root, m, crop, ov)
        seen = _token(vlm, root, crop_id, roles)
    v2 = dprompt.v2_of(ov)
    state = dprompt.v2_state(v2, model, roles, len(rendered["images"]))
    prompt = rendered["prompt"]
    if state["state"] == "body":
        try:
            prompt = dprompt.render_body(spec, state["body"])["prompt"]
        except ValueError as exc:
            state.update(state="blocked", why=str(exc))
    images = []
    for i, (path, role) in enumerate(dprompt.v2_images(spec), 1):
        images.append({"token": f"@image{i}", "role": role,
                       "label": crop["name"] if role == "canvas" else ntpath.basename(path)})
    return {"ok": True, "crop_id": crop_id, "seen": seen, "crop_sig": vlm.crop_signature(crop),
            "v2": None if v2 and "invalid" in v2 else v2, "images": images,
            "state": state["state"], "why": state["why"], "prompt_en": "" if state["state"] == "blocked" else prompt,
            "input_hash": state["input_hash"], "model": model}


def _validate(data):
    unknown = data.keys() - {*_REQUIRED, *_FIELDS, "raw_prompt_override"}
    if unknown:
        raise board._Reject(400, "모르는 키: " + ", ".join(sorted(unknown)))
    for key in _REQUIRED:
        if not isinstance(data.get(key), str):
            raise board._Reject(400, f"{key}는 필수 문자열입니다")
    if "purpose" in data and data["purpose"] not in dprompt.PURPOSES:
        raise board._Reject(400, "purpose 값이 잘못되었습니다")
    if "user_instruction_ko" in data:
        problem = dprompt.text_problem(data["user_instruction_ko"], dprompt.INSTRUCTION_MAX, "지시문")
        if problem:
            raise board._Reject(400, problem)
    if "ai_auto" in data and type(data["ai_auto"]) is not bool:
        raise board._Reject(400, "ai_auto는 true/false여야 합니다")
    if data.get("raw_prompt_override") is not None and not isinstance(data["raw_prompt_override"], str):
        raise board._Reject(400, "raw_prompt_override는 문자열 또는 null이어야 합니다")


def _manual_prompt(raw, v2, model, roles, image_count):
    if raw is None or not raw.strip():
        return None
    problem = dprompt.body_problem(raw, image_count)
    if problem:
        raise board._Reject(400, problem)
    body = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    fingerprint = dprompt.v2_input_hash(v2, model, roles, manual=True)
    prompt = {"source": "manual", "body_en": body, "summary_ko": "", "input_hash": fingerprint}
    previous = v2.get("prompt") or {}
    if all(previous.get(k) == v for k, v in prompt.items()) and previous.get("written_at"):
        return previous
    return {**prompt, "written_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def _save(root, data):
    vlm = board._vlm()
    crop_id = data["crop_id"]

    def decide(cur):
        if vlm.crop_signature(crop) != data["crop_sig"]:
            raise vlm.SpecConflict("크롭이 바뀌었습니다 — 다시 읽기")
        error = vlm._targets_error(cur)
        if error:
            raise board._Reject(422, error)
        model, _, roles, rendered = _inputs(root, m, crop, cur)
        if _token(vlm, root, crop_id, roles) != data["seen"]:
            raise vlm.SpecConflict("스펙이나 레퍼런스 연결이 다른 곳에서 바뀌었습니다 — 다시 읽기")
        fields = {key: data[key] for key in _FIELDS if key in data}
        new = dprompt.v2_update(cur, **fields) if fields else cur
        if "raw_prompt_override" in data:
            v2 = dprompt.v2_of(new) or {}
            prompt = _manual_prompt(data["raw_prompt_override"], v2, model, roles, len(rendered["images"]))
            new = dprompt.v2_update(new, prompt=prompt)
        return vlm.MergeResult(new, new != cur, [], False, False, [])

    with store.manifest_lock(root):
        m = store.load_manifest(root)
        crop = _crop(root, m, crop_id)
        try:
            vlm.write_spec(root, crop_id, decide)
        except vlm.SpecConflict as exc:
            raise board._Reject(409, str(exc), reason="stale") from None
        except ValueError as exc:
            raise board._Reject(422, str(exc)) from None
        return spec_view(root, crop_id)


def routes() -> tuple:
    @board._guard
    async def get(request):
        root = await asyncio.to_thread(board._root_param, request.query.get("root"))
        return board._json(await asyncio.to_thread(spec_view, root, request.query.get("crop_id")))

    @board._guard
    async def post(request):
        data = await board._body(request)
        _validate(data)
        root = await asyncio.to_thread(board._root_param, data["root"])
        return board._json(await asyncio.to_thread(_save, root, data))

    path = board.API + "/spec"
    return (("GET", path, get), ("POST", path, post))
