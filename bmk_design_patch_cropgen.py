"""서버 모드의 패치별 생성 설정 일괄 조회·저장."""

import asyncio

from aiohttp import web

try:
    from . import bmk_design_patch_board as board
    from . import bmk_design_patch_models as models
    from . import bmk_design_patch_prompt as dprompt
    from . import bmk_design_patch_store as store
except ImportError:
    import bmk_design_patch_board as board
    import bmk_design_patch_models as models
    import bmk_design_patch_prompt as dprompt
    import bmk_design_patch_store as store


MODEL_ROWS = web.AppKey("bmk_dp_crop_model_rows", object)
_MODEL_FIELDS = {"quality", "opts", "complexity"}


def _validate(data):
    extra = data.keys() - {"root", "seen", "changes"}
    if extra:
        raise board._Reject(400, "모르는 키: " + ", ".join(sorted(extra)))
    if not isinstance(data.get("root"), str):
        raise board._Reject(400, "root는 필수 문자열입니다")
    if type(data.get("seen")) is not int or data["seen"] < 0:
        raise board._Reject(400, "seen은 0 이상 정수여야 합니다")
    changes = data.get("changes")
    if not isinstance(changes, list) or not 1 <= len(changes) <= 20:
        raise board._Reject(400, "changes는 1–20개 항목의 목록이어야 합니다")
    used = set()
    for change in changes:
        if not isinstance(change, dict):
            raise board._Reject(400, "changes 항목에는 crop_ids와 patch 두 키가 필요합니다")
        wrong = change.keys() ^ {"crop_ids", "patch"}
        if wrong:
            raise board._Reject(400, "changes 항목에서 빠지거나 모르는 키: " + ", ".join(sorted(wrong)))
        ids = change["crop_ids"]
        if not isinstance(ids, list) or not ids:
            raise board._Reject(400, "crop_ids는 비어 있지 않은 목록이어야 합니다")
        if len(used) + len(ids) > 200:
            raise board._Reject(400, "crop_ids는 전체 200개까지입니다")
        for cid in ids:
            if not isinstance(cid, str) or not board._CROP_ID_RE.fullmatch(cid):
                raise board._Reject(400, "crop_ids의 크롭 id 형식이 잘못되었습니다")
            if cid in used:
                raise board._Reject(400, f"crop_ids에 같은 크롭이 두 번 있습니다: {cid}")
            used.add(cid)
        patch = change["patch"]
        if not isinstance(patch, dict) or not patch:
            raise board._Reject(400, "patch는 비어 있지 않은 객체여야 합니다")
        extra = patch.keys() - {"inherit_model", "model_id", "count", *_MODEL_FIELDS}
        if extra:
            raise board._Reject(400, "patch에 모르는 키: " + ", ".join(sorted(extra)))
        if "inherit_model" in patch and patch["inherit_model"] is not True:
            raise board._Reject(400, "inherit_model은 true여야 합니다")
        if "inherit_model" in patch and "model_id" in patch:
            raise board._Reject(400, "inherit_model과 model_id를 함께 줄 수 없습니다")
        fields = patch.keys() & _MODEL_FIELDS
        if fields and "model_id" not in patch:
            raise board._Reject(400, ", ".join(sorted(fields)) + "에는 model_id가 필요합니다")
        if patch.get("count") is not None and type(patch["count"]) is not int:
            raise board._Reject(400, "count는 정수 또는 null이어야 합니다")


def _apply(entries, changes, rects, project_model, rows):
    for change in changes:
        for cid in change["crop_ids"]:
            if cid not in rects:
                raise board._Reject(404, "크롭이 없습니다", crop_id=cid)

    rejected = []
    unavailable = False
    for change in changes:
        patch, ids = change["patch"], change["crop_ids"]
        bundle = {}
        if "model_id" in patch:
            model = patch["model_id"]
            try:
                bundle = models.gen_bundle(model, patch.get("quality"), patch.get("opts"), patch.get("complexity"))
            except models.GenError as exc:
                rejected.extend({"crop_id": cid, "key": exc.key, "error": str(exc)} for cid in ids)
            if models.get(model) and not rows[model]["available"]:
                unavailable = True
                rejected.extend({"crop_id": cid, "key": "model_id", "error": rows[model]["why"]} for cid in ids)
        for cid in ids:
            old = entries.get(cid)
            if models.gen_entry_problem(old):
                old = {}
            entry = dict(old)
            if "model_id" in patch or "inherit_model" in patch:
                entry = dict(bundle)
                if "n" in old:
                    entry["n"] = old["n"]
            if "count" in patch:
                count = patch["count"]
                if count is None:
                    entry.pop("n", None)
                else:
                    model = patch.get("model_id", entry.get("model", project_model))
                    problem = models.count_problem(model, count)
                    if problem:
                        rejected.append({"crop_id": cid, "key": "count", "error": problem})
                    entry["n"] = count
            if entry:
                entries[cid] = {key: entry[key] for key in models.GEN_KEYS if key in entry}
            else:
                entries.pop(cid, None)
    if rejected:
        reason = {"reason": "model_unavailable"} if unavailable else {}
        raise board._Reject(400, "패치별 생성 설정을 저장할 수 없습니다", rejected=rejected, **reason)
    return entries


def _prompt_inputs(root, m):
    """매니페스트 잠금 안에서 AI 작성본의 재료를 한 판으로 읽는다."""
    vlm = board._vlm()
    inputs = {}
    for crop in m["crops"]:
        ov, error = vlm._load_spec(root, crop["id"])
        if not ov or error or vlm._targets_error(ov):
            continue
        v2 = dprompt.v2_of(ov)
        if v2 and (v2.get("prompt") or {}).get("source") == "assist":
            inputs[crop["id"]] = (crop, ov, v2)
    try:
        glossary = store.project_glossary(root, m) if inputs else {}
    except ValueError:
        return {}, {}  # 용어집을 읽을 수 없으면 작성본의 상태도 판정할 수 없다.
    return inputs, glossary


def _prompt_stale(root, refs_dir, glossary, prompt_input, model):
    crop, ov, v2 = prompt_input
    try:
        spec = dprompt.spec_from_manifest(crop, None, refs_dir, glossary, ov)
        roles = dprompt.v2_roles(spec, root)
        return dprompt.v2_state(v2, model, roles, len(dprompt.render(spec)["images"]))["state"] == "blocked"
    except (ValueError, TypeError, AttributeError, KeyError):
        return False  # 손으로 편집한 스펙의 손상은 다른 크롭의 조회를 막지 않는다.


def _job_matches(job, effective, ratio):
    if any(job.get(k) != effective[k] for k in ("model", "quality", "n")):
        return False
    if "complexity" in effective and job.get("complexity") != effective["complexity"]:
        return False
    opts = job.get("opts")
    if isinstance(opts, dict):
        job_ratio = opts.get(models.RATIO_OPT)
        opts = {k: v for k, v in opts.items() if k != models.RATIO_OPT} or None
    else:
        job_ratio = None
    return opts == effective.get("opts") and (ratio is None or job_ratio == ratio["matched"])


def _crop_row(rect, cid, entry, prepare, jobs, inputs_applied, rows):
    try:
        effective = models.effective_gen(prepare, entry)
    except models.GenError as exc:
        error = board.public_error(str(exc))
        return {"crop_id": cid, "applied": False, "gen": entry, "effective": None, "ratio": None,
                "available": False, "why": error, "prompt_stale": False, "error": error}
    model = effective["model"]
    row = rows[model]
    if not row["complexities"]:
        del effective["complexity"]
    for k in ("opts", "clamped"):
        if effective[k] is None:
            del effective[k]
    ratio = None
    if row["ratios"]:
        x0, y0, x1, y1 = rect
        w, h = x1 - x0, y1 - y0
        matched = models.match_ratio(model, w, h)
        ratio = {"ok": matched is not None, "matched": matched, "nearest": models.nearest_ratio(model, w, h)}
    applied = jobs is not None and (all(_job_matches(job, effective, ratio) for job in jobs) if jobs else inputs_applied)
    return {"crop_id": cid, "applied": applied, "gen": entry, "effective": effective, "ratio": ratio,
            "available": row["available"], "why": row["why"], "prompt_stale": False, "error": None}


def _read_or_update(root, data, rects, project_model, rows):
    conflict = None
    if data is not None:
        def mutate(entries):
            return _apply(entries, data["changes"], rects, project_model, rows)

        try:
            entries, rev, changed = store.update_crop_gen(root, mutate, expect_rev=data["seen"])
            return entries, rev, changed, None
        except store.CropGenConflict as exc:
            conflict = str(exc)
    doc = store.load_crop_gen(root)
    return doc["crops"], doc["rev"], None, conflict


def _request(root_key, model_rows, data=None):
    root = board._root_param(root_key)
    rows, _ = model_rows(board.current_scope())
    with store.manifest_lock(root):
        m = store.load_manifest(root)
        settings = board._route_settings(root, m)["settings"]
        prepare = settings["prepare"]
        layout = board._layout_or_none(root) or {}
        rects = {c["id"]: c["rect"] for c in layout.get("crops", [])}
        for crop in m["crops"]:
            rects.setdefault(crop["id"], crop["rect"])

        try:
            entries, rev, changed, conflict = _read_or_update(root, data, rects, prepare["model"], rows)
        except ValueError as exc:
            raise board._Reject(422, str(exc)) from None
        jobs = {c["id"]: [] for c in m["crops"]}
        for job in m["jobs"]:
            if isinstance(job, dict) and isinstance(job.get("crop_id"), str) and job["crop_id"] in jobs:
                jobs[job["crop_id"]].append(job)
        same_inputs = any(not group for group in jobs.values()) and m.get("prepare_inputs") == store.prepare_inputs_fp(root, m)
        prompts, glossary = _prompt_inputs(root, m)

    inputs_applied = same_inputs and not any(d["section"] == "prepare" for d in board.pl.drift(settings, m))
    project = {k: v for k, v in models.effective_gen(prepare).items() if k in models.GEN_KEYS}
    if not rows[project["model"]]["complexities"]:
        project["complexity"] = None
    refs_dir = (m.get("source") or {}).get("refs_dir") or ""
    crops = []
    for cid, rect in rects.items():
        row = _crop_row(rect, cid, entries.get(cid), prepare, jobs.get(cid), inputs_applied, rows)
        if row["effective"] is not None and cid in prompts:
            row["prompt_stale"] = _prompt_stale(root, refs_dir, glossary, prompts[cid], row["effective"]["model"])
        crops.append(row)
    result = {"ok": conflict is None, "seen": rev, "project": project, "crops": crops}
    if conflict is not None:
        raise board._Reject(409, conflict, reason="stale", **result)
    if changed is not None:
        result["changed"] = changed
    return result


def routes() -> tuple:
    @board._guard
    async def get(request):
        return board._json(await asyncio.to_thread(_request, request.query.get("root"), request.app[MODEL_ROWS]))

    @board._guard
    async def post(request):
        data = await board._body(request)
        _validate(data)
        return board._json(await asyncio.to_thread(_request, data["root"], request.app[MODEL_ROWS], data))

    path = board.API + "/crop_gen"
    return (("GET", path, get), ("POST", path, post))
