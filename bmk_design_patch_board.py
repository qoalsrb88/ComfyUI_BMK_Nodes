"""BMK Design Patch Board — Review Board(별도 웹 페이지)의 aiohttp 라우트와 보드 썸네일 생성(노드 없는 보조 모듈).

배경
----
Run 이 만든 후보가 타깃마다 여러 장이 되면 노드 미리보기로는 고르기 어렵다. Review Board 는 ComfyUI 탭이 연
별도 창(두 번째 모니터 등)에서 타깃별 모든 후보를 같은 초점 영역으로 확대해 보여 주고, 키보드로 ★·slice·탈락을
고르고 재굴림(+1 호출)을 요청한다. 보드는 매니페스트만 고친다(모든 쓰기는 store.update_manifest).
과금 큐잉은 ComfyUI 탭의 app.queuePrompt 로만 한다 — Studio 승인 메시지(opener postMessage·BroadcastChannel "bmk-dp")를
받은 탭이 이 모듈의 유료 템플릿(GET /pipeline/prompt run·draft·reroll — 해시 대조 뒤)을 제출하거나(S3b), 옛 확장의 opener 탭이
Review 노드를 부분 실행한다(대체 경로). 인증 토큰은 그 경로로만 붙으므로 라우트는 큐에 넣지도 유료 호출을 하지도 못한다.
명세: H:\\BmkNodeDesign\\MultiLayerCropEdit\\_proto\\M2_SPEC.md §4.

라우트 (register_routes — 모든 root 파라미터 = store 레지스트리 키(소문자 hex 12자), 경로 금지)
-------------------------------------------------------------------------------------------
- GET  /bmk/design_patch/board/            board.html (web_design_patch/ 의 board.html·board.js·viewer.js·layout_geom.js·
                                            layout_edit.js·ref_edit.js·checklist.js·pipeline.js·mask_edit.js·board.css 만)
- GET  /bmk/design_patch/state?root=&since_rev=
       보드 요약(JSON). since_rev 가 지금 rev 와 같으면 {"unchanged": true, rev, running, drain, layout_rev, refs_rev, refs_files_v,
       pipeline_rev, plan_v}.
- GET  /bmk/design_patch/thumb?root=&kind=&key=&size=[&v=]
       JPEG. kind = cand(후보 키) | before(crop_id/tid) | ref(crop_id/tid/번호) | delta(후보 키) | context(후보 키).
       size ∈ 128·192·256·384·512·768·1024, 또는 0 = 원해상도(cand·before 만).
       v 가 서버의 캐시 키와 같으면 오래 캐시해도 된다고 응답한다.
- GET  /bmk/design_patch/canvas?root=&kind=base|composite|region&v=[&crop=&key=]
       캔버스 미리보기 JPEG(긴 변 CANVAS_PREVIEW). composite = 현재 ★ 합성 — v 가 지금 서명과 다르면 409(낡은 요청은 계산 안 함).
       region = 크롭 rect 주변(변마다 64–256px)만 원해상도로 ★ 합성(crop= 크롭 ID). key= 후보면 "이 후보를 ★ 하면"(그 타깃의
       ★ 를 이 후보로 바꾼 가정). v = state 의 region_url·pick_url.
- GET  /bmk/design_patch/seams?root=&v=    ★ 합성의 이음매(JSON, composite 와 한 번에 계산·캐시). v 규칙은 composite 와 같음.
- GET  /bmk/design_patch/file?root=&rel=   프로젝트 안 cands/·inputs/·derived/·masks/ 의 이미지 파일만.
- POST /bmk/design_patch/pick    {root, crop_id, tid, key, mode: set|add|remove}
- POST /bmk/design_patch/reject  {root, key, value: bool}
- POST /bmk/design_patch/reroll  {root, cell_key | (crop_id, tid[, variant]), count}
- POST /bmk/design_patch/drain   {root}   진행 중인 Run 이 있을 때만 플래그를 건다(없으면 drain false 로 응답)
- GET  /bmk/design_patch/mask/unused?root=   masks/ 의 편집기 PNG(<key>_e_<sha>.png) 중 매니페스트 어디서도 가리키지 않는 것(편집 기록·이전 버전·
                                       후보 hand_mask 등 — ★ 아닌 후보 포함) {files: [{file, bytes, at}], bytes, recent}(배치 3-6 — 10분 안에 쓴 것은 recent 로만)
- POST /bmk/design_patch/mask/cleanup {root, files}   그 목록 중 지금도 안 쓰는 것만 지움(매니페스트 잠금 안에서 다시 판정) → {ok, deleted, bytes, kept}
- GET  /bmk/design_patch/mask/ps_list?root=   output 안의 이 프로젝트 Export PSD·사이드카 목록(최근 것부터)
- POST /bmk/design_patch/mask/ps_import {root, psd, dry_run, seen}   PS 그룹 마스크 미리보기·S4 새 판 저장(무료, seen은 미리보기 응답)
- POST /bmk/design_patch/mask/adjust {root, key, op, blur, invert, seen}   ★ 후보 보정 set·undo·reset(seen = 기록 at)
- POST /bmk/design_patch/open_folder {root}   Export 출력 폴더를 탐색기로(배치 3-5a — 마지막 Export PSD 를 고른 채, 없으면
                                       output/design_patch/<실행 설정 export.subfolder — 비면 프로젝트 폴더>(3-5b)). ComfyUI output 밖이거나
                                       폴더가 없으면 열지 않음 → {ok, path, selected}
- GET  /bmk/design_patch/layout?root=&since=           레이아웃 편집기 상태(STUDIO_S1_SPEC §4.1, layout_state)
- GET  /bmk/design_patch/layout/refs?root=&name=[&id=] 그 이름이면 고를 레퍼런스 {n, first[, fixed]} | null(refs_dir 없음) — 이름 바꾸기
                                                       미리보기. id 를 주면 고정된·이름 바꾸기가 고정할 크롭은 지금 레퍼런스(연결 유지)
- POST /bmk/design_patch/layout/start {root}           매니페스트 크롭 → layout.json(이관, 있으면 409)
- POST /bmk/design_patch/layout/crop  {root, rev, op: add|update|delete, …, restore_rev?, index?, restore_manifest?}
       update 가 이름을 바꾸면 먼저 refs.json 에 그 크롭을 바꾸기 전 이름으로 고정(S2 §2.6 — 실패하면 이름도 안 바꿈)
- POST /bmk/design_patch/layout/psd   {root, rev, op: take|ack, names?, snapshot?}   PSD 스냅숏과 갈라졌을 때(고른 이름만 기준점 갱신 —
                                                       남은 갈라짐은 계속 경고, S1-a)
  레이아웃 쓰기는 모두 store.update_layout(expect_rev = rev) — rev 가 다르면 409, 매니페스트는 건드리지 않는다.
- GET  /bmk/design_patch/refs?root=&since=             레퍼런스 편집기 상태(STUDIO_S2_SPEC §5.1, refs_state)
- GET  /bmk/design_patch/refs/sheets?root=             시트 후보 목록 [{path, size, kind, bytes, v, preview_url}] (§5.2)
- GET  /bmk/design_patch/refs/sheet?root=&sheet=&kind=preview|region&rect=&max=&v=   시트 JPEG(§5.3)
- GET  /bmk/design_patch/refs/img?root=&name=&where=refs|held|cut|cutmask&size=&bg=&v=   레퍼런스 썸네일·원본, 컷 미리보기(§5.4)
- POST /bmk/design_patch/refs/cut  {root, sheet, sheet_v?, rect, method: rect|sam3|poly, …}   컷 요청(rect·poly 즉시, sam3 = 요청 파일만,
                                                       sheet_v ≠ 지금 시트 서명 = 409 {sheet_changed: true})
- GET  /bmk/design_patch/refs/cut?root=&req=           컷 상태 waiting|done|error(§5.5)
- POST /bmk/design_patch/refs/save {root, rev, req, crop_id, role, desc?, transform, confirm?}   변환해 refs_dir 에 새 파일 + 연결(§5.6)
- POST /bmk/design_patch/refs/link {root, rev, op, crop_id, name?, to?, confirm?}   연결 고치기 op 10개(§5.7)
  레퍼런스 쓰기는 모두 drefs.update_refs(expect_rev = rev, null = 아직 없음 → 만들기) — rev 가 다르면 409, 매니페스트는 건드리지
  않는다. 이름으로 직접 가리키는 파일을 옮기거나 그 이름에 새 그림을 쓰면 409 {error, explicit} → confirm 에 이름을 넣어 다시.
  유료 경로 없음: SAM3 는 보드가 /api/prompt 로 큐에 넣는 무료 Ref Cut 노드가 돌린다(이 모듈은 요청·결과 파일만).
- GET  /bmk/design_patch/pipeline?root=                실행 설정(STUDIO_S3_SPEC §6 — pl.view: 파일·유도값·갈라짐·경로 경고·최근 Export·
                                                       설정 위젯 표·manifest_rev). 파일을 쓰지 않는다
- POST /bmk/design_patch/pipeline/settings {root, rev, section, values}   절 하나를 통째로(pl.update — rev 0 = 파일 생성, 깨진 파일
                                                       덮어쓰기 = null, 다르면 409)
- GET  /bmk/design_patch/pipeline/prompt?root=&stage=&approve=&only=&expect=
       템플릿 {stage, prompt, targets, hash, only, sig}(pipeline_prompt — S3b 계약 §1). apply·export = 무료(targets null, hash·only ""),
       run·draft = approve 가 지금 선택 계획의 해시와 같을 때만(only 는 서버 정규형으로 돌려줌), reroll = approve·only 없이(사전 승인분).
       인자 400, 409 {reason} = 설정 확인·경로·깨진 파일·expect('<pipeline rev>:<매니페스트 rev>') 다름·실행 중·반영 필요·계획 잠금
       15초·계획 오류·승인할 것 없음·해시 다름(stale — 지금 해시)
- GET  /bmk/design_patch/pipeline/plan?root=&only=&style=   Run·Draft 계획(pipeline_plan — 노드와 같은 함수의 승인 해시·비용·크롭별),
                                                       다른 계획을 계산 중이면 202 {busy: true}
- GET  /bmk/design_patch/pipeline/progress?root=       Run(원장)·Spec Draft(draft_active 동안의 원장) 진행
- GET  /bmk/design_patch/draft?root=&crop=             크롭의 초안 제안·채택 상태(vlm.draft_view)
- POST /bmk/design_patch/draft/adopt {root, crop_id, op: adopt|set|clear, text?, value?, seen, crop_sig, preview?}
                                                       스펙 _layer_targets 채택·수정·해제(vlm.adopt_write — 거절 400 {reason|rows},
                                                       seen·crop_sig·value 경합 409)
  라우트는 큐에 넣지 않는다: 무료 템플릿(Run·Spec Draft 없음)은 Studio 가 /api/prompt 로, 유료 템플릿(S3b)은 Studio 의 승인 메시지를
  받은 ComfyUI 탭이 검사한 뒤 app.queuePrompt 로 큐에 넣는다(인증 토큰은 그 경로로만 붙음 — 서버는 템플릿과 해시 대조만).
- GET  /bmk/design_patch/mask?root=&key=               ★ 후보의 마스크 편집 대상(STUDIO_S4_SPEC §4, mask_view — 편집 창·기준·편집·3층 URL·
                                                       이 크롭 이음매·z). ★ 아님 409 {reason: not_picked}. 파일을 쓰지 않는다
- GET  /bmk/design_patch/mask/img?root=&key=&kind=base|edit|under|patch|over&v=   PNG — base·edit = 지금 편집 창 L(edit = 기록 래스터를
                                                       그 창으로 옮김 = _plan), under = 영역 RGB, patch·over = 영역 RGBA(3층은 한 번에
                                                       계산·캐시). v ≠ 지금 서명 409 {reason: stale}
- POST /bmk/design_patch/mask/save?root=&key=&seen=&ops=&rect=   본문 image/png(≤ 4 MiB) = 편집 래스터(dmask.save_edit). PNG 크기
                                                       또는 rect(세션을 연 창 x0,y0,x1,y1) ≠ 지금 편집 창 409 {reason: rect_changed, rect},
                                                       seen ≠ 지금 기록 409 {reason: changed}
- POST /bmk/design_patch/mask/edit {root, key, seen, op: undo|reset}   [이전 판으로]·[편집 지우기](seen 다름 409 changed)
- GET  /bmk/design_patch/mask/alts?root=&key=          그 타깃의 다른 후보마다 "★ 로 바꾸면" 이음매(_compose_full(swap) — swap 서명별
                                                       캐시), 다른 대안 계산 중 202 busy
  응답 모양 정본 = tools/fixtures/mask_api.json. 마스크 편집 쓰기는 모두 dmask(store.update_manifest — seen 대조는 잠금 안).
- POST /bmk/design_patch/zorder {root, scope, item, op, seen}   범위 안 쌓는 순서(아래 → 위), seen = 매니페스트 rev
POST 는 Content-Type application/json 만 받는다(다른 사이트의 단순 폼 POST 차단 — /mask/save 는 image/png). 오류는 4xx + {"error": 한국어}
(레퍼런스: 목록 밖 시트·없는 파일 404, 같은 이름이 있음 409).
CPU·파일 작업은 asyncio.to_thread 로 돌려 aiohttp 루프를 막지 않는다. 시트 디코드·PSD 합성·컷·변환은 _REF_SLOTS 로 한 번에 하나.

썸네일 (derived/thumbs/ 캐시, 파일명 = 내용 서명: 후보 파일·quad·크롭 rect·분석 params_hash·초점 박스·kind·size)
--------------------------------------------------------------------------------------------------------
- 배치: Analyze 와 같은 방법 — 후보 원본 → 크롭 rect 크기로 LANCZOS(an.place) → 정합 행렬 적용(an.warp_affine)
  → 톤 보정장(있으면 an.apply_delta). 크롭과 IoU < 0.5 인 부분 패치는 quad 그대로 렌더해 크롭 소스 위에 얹는다.
  크롭 rect 를 옮기기 전 Run·폴더 후보도 quad 로(분석이 있으면 Compose 처럼 정합 quad·톤, 자동 마스크는 항목의
  rect 에서 크롭 창으로 옮김) — 노드 _gen_frame 과 같은 규칙.
  크롭 해상도(= 최종 PSD 에서 보이는 해상도) 결과를 placed_<서명>.png 로 캐시해 다른 kind 가 재사용한다.
- 초점 박스(크롭 로컬): 그 타깃 후보들의 자동 마스크(>= 0.5) bbox 합집합을 변마다 10%(합 +20%) 넓힌 것,
  최소 크롭 짧은 변의 1/4. 분석이 없으면 크롭 전체. before 와 cand 는 같은 초점·같은 크기라 Q 깜빡임이 픽셀 정렬된다.
- delta = 크롭 소스 대비 ΔE76(an.delta_e, blur 1.5) 히트맵(0 → 3·dE 를 inferno 로) + 자동 마스크 외곽(청록).
- context = 베이스 위에 최종 마스크로 합성한 크롭 rect 1.5배 영역 + 크롭 외곽선(주황). 마스크·정합·톤은 마지막 export 의
  Compose 설정(없으면 기본 hand_else_auto = 손 마스크가 있으면 손, 없으면 자동 마스크) — 바탕 ★ 합성과 같게.
- 원해상도(size 0): 초점 자르기·축소 없이 크롭 해상도 그대로(JPEG q95) — Studio 캔버스 뷰어의 1:1·2:1 겹침(Before·M 원본)용.
- 분석 항목 선택: prebuild_thumbs(analyze_params)·Analyze 노드가 넣은 설정(_PREF)과 params 가 같은 항목, 없으면 가장 최근 항목.

★ 합성·이음매 (Studio 캔버스 뷰어, derived/thumbs/composite_<서명>.jpg + seams_<서명>.json)
-------------------------------------------------------------------------------------------
- Compose 노드 함수 그대로(_plan → _cand_pixels → _render → _paste, 풀해상도) → 긴 변 CANVAS_PREVIEW 로 축소. 뷰어 겹침(region)도
  같은 계산을 크롭 주변 창에만(가정한 ★ 집합 = 그 타깃의 ★ 를 후보 하나로 바꿈 — 보드 Space 와 같음). 설정 = 마지막
  export 의 compose(없으면 Compose 기본값), 분석 항목 = 썸네일과 같은 선택. 보드는 분석하지 않는다 — 분석 없는 ★ 은
  Compose 가 분석 없이 하는 대로(손 마스크 아니면 마스크 없음) 합성하고 unanalyzed 로 알린다. 한 번에 하나만 계산한다.
- 이음매: ★ 항목마다 크롭 rect 네 변(바깥 이웃이 캔버스 안인 변)에서 안쪽 2줄 실효 알파 최대 >= 0.5 인 곳 = 마스크가 경계에 닿아
  잘림. 틈 2px 이하는 잇고 6px 이상 구간마다 새 불연속 |(합성 안 − 합성 밖) − (베이스 안 − 베이스 밖)|(경계 1px, RGB 평균)의
  중앙값·90% — 원래 그림의 윤곽선이 경계에 걸린 것은 0 에 가깝다. p90 < _SEAM_MIN_DIFF 는 버리고, >= _SEAM_BAD 면 "bad",
  아니면 "weak". (10/15 실전: 009 오른쪽 변 검은 띠 어긋남 p90 ≈ 99, 경계에서 베이스와 같은 패치 = 0)

공개 함수 (Review 노드·Run 노드용)
---------------------------------
- register_routes(routes, base_dir=None) -> bool   라우트 등록(이미 있으면 False). base_dir 를 주면 그 아래
                                                    _roots.json 을 store.load_root_registry 로 다시 읽는다(재시작 복구).
- build_state(root, since_rev=None) -> dict        state 라우트와 같은 요약(Review 보고서용).
- layout_state(root, since=None) -> dict           GET /layout 과 같은 레이아웃 편집기 상태(S1).
- refs_state(root, since=None) -> dict             GET /refs 와 같은 레퍼런스 편집기 상태(S2).
- set_sam3_ckpts(names) -> None                    SAM3 체크포인트 이름(노드 모듈이 import 때 — 보드는 folder_paths 를 import 하지 않음).
- set_output_dir(path) -> None                     ComfyUI output 폴더(노드 모듈이 import 때) — POST /open_folder 가 이 안만 연다.
- prebuild_thumbs(root, analyze_params=None, size=256) -> dict
                                                    분석 설정 기억 + 후보 그리드·Before 썸네일 미리 생성(캐시).
- board_url(key) -> str                            "/bmk/design_patch/board/?root=<key>"
- set_drain / drain_requested / clear_drain(root)  drain 플래그(프로세스 메모리, 키 = store.root_key). runner.run_calls 가
                                                    돌고 있으면 runner 레지스트리(request_drain)에 걸고, drain_requested 는
                                                    두 레지스트리를 합쳐 답한다(어느 쪽을 drain_flag 로 넘겨도 동작).
- run_active(root) / is_running(root)              Run 진행 표시(컨텍스트 관리자, runner.run_calls 밖에서 쓸 때). 처음 들어갈 때·
                                                    마지막으로 나갈 때 이 모듈 drain 플래그를 지운다. is_running 은 runner 것도 본다.
- set_qwen_dir(path)                               Studio Draft 계획의 local_qwen 모델 폴더(노드 모듈이 import 때 — 비면 local_qwen 계획 오류).
- draft_active(root) / draft_since(root)           Spec Draft 실행 표시(컨텍스트 관리자 — 노드 run() 전체를 감쌈, run_active 와 별개)·
                                                    가장 이른 시작 시각(없으면 None). 계획 라우트의 draft.running·진행 라우트의 since.
- pipeline_plan(root, settings=None, *, only_ids, style, confirm=()) -> dict
                                                    GET /pipeline/plan 본문(aiohttp 없음 — settings None = 실행 설정 파일(라우트),
                                                    노드 대조 시험 N25·N26 은 설정을 넘겨 직접 부름).

버전 이력
---------
v1 (2026-10, M2)
- 최초 구현: 라우트 8종(정적·state·thumb·file·pick·reject·reroll·drain), 썸네일 5종 + placed 중간 캐시,
  drain·run 레지스트리, pending 계산(runner.plan_calls 가 있으면 그것, 없으면 원장 파일 + calls 요약).
v2 (2026-10-15, Studio S0 — _proto/STUDIO_S0_SPEC.md)
- canvas(base·composite·region)·seams 라우트(★ 합성 미리보기·"이 후보를 ★ 하면" 원해상도 영역·이음매), 원해상도 썸네일
  size 0, 정적 viewer.js. context 는 export 의 Compose 설정(mask_source·정합·톤)을 따른다.
- state: crops(크롭별 크레딧·미상·호출·대기·중복 ★·옛 크롭 ★·region), canvas, cost, 후보 stale_rect·joint·full_url·pick_url,
  타깃 before_full_url.
v3 (2026-10-15, Studio S1 — _proto/STUDIO_S1_SPEC.md §4)
- layout 라우트 5종(상태·레퍼런스 미리보기·이관·크롭 add/update/delete·PSD take/ack), 정적 layout_geom.js·layout_edit.js.
  검사 = store.layout_geom_checks + R-none(레퍼런스 없음)·D-psd(PSD 갈라짐)·D-pname(사전 편집 그룹을 두 크롭이 씀)·
  H-orphan(새 크롭 ID 에 옛 스펙·후보). 초안 상자는 원장 req 의 canvas src_sha = 지금 jobs canvas sha 일 때만 믿는다(D5).
  PSD 스냅숏은 매니페스트 source(마지막 Import)와 PSD·지문·그룹 설정이 같을 때만 믿는다(노드와 같은 조건).
  실행 취소(restore_rev)는 그 판과 같은 값의 규칙 검사를 건너뛰고, 되살린 크롭은 원래 위치(index)로 — 매니페스트 순서 유지.
- state: layout_rev(unchanged 응답에도), 크롭 요약 layout_status.
v4 (2026-10-16, Studio S2 — _proto/STUDIO_S2_SPEC.md §5)
- refs 라우트 7종(상태·시트 목록·시트 JPEG·레퍼런스 그림·컷 POST/GET·저장·연결 op 10개), 정적 ref_edit.js. 연결·이름 규칙·시트 캐시·
  컷·변환·파일 동작은 bmk_design_patch_refs(drefs). 검사 R-none·R-main·R-missing·R-dir·R-unlinked·R-explicit·R-big·R-side·R-pending,
  고아 연결은 orphans, S2 가 만든 파일 행은 record(다시 열기). 컷 rect·poly·다듬기는 라우트에서 즉시, sam3 은 요청 파일만(보드가 무료 Ref Cut 노드를 큐).
  저장·보류·되살리기는 파일 먼저 → refs.json, 실패하면 파일을 되돌린다(지우지 않음). 고정 안 된 크롭을 고치면 그 전의 이름 매칭으로 고정.
  다른 크롭의 동작이 함께 정한 연결(같은 번호 형제 고정·이름 바꾸기 고정·다른 크롭 보류로 빈 연결)은 fixed.auto — 빈 files 여도 의도한
  '없음'이 아님(R-none·R-unlinked 경고, state none false·auto true), 그 크롭을 직접 고치면 지운다.
- S1 이름 바꾸기(/layout/crop update)가 고정 안 된 크롭을 바꾸기 전 이름으로 먼저 고정, /layout 레퍼런스·R-none 은 고정된 크롭이면
  연결 해석(/layout/refs 에 선택 id).
- state: refs_rev·refs_files_v(바깥 파일 변경 — unchanged 응답에도), 크롭 요약 refs {n, main, fixed, none, auto}. 오류 매핑 FileNotFoundError 404·
  FileExistsError 409·RefsConflict 409.
- 보류·대표 교체는 refs.json held 에 원래 이름·크롭을 남긴다 — 되살리기는 그 이름으로(이름에서 접미를 짐작하지 않음), 보류 목록은 번호 접두 밖에
  기록의 크롭으로도(S1 번호 바꾸기 뒤), 보류 행 restore_as(codex6).
v5 (2026-10-17, Studio S3a — _proto/STUDIO_S3_SPEC.md §2.4·§2.6·§3.5·§3.7·§6)
- pipeline 라우트 7종(실행 설정 GET·POST, 무료 템플릿, 계획, 진행, 초안 채택 GET·POST — 모두 25 → 32 RouteDef, 경로 31), 정적
  pipeline.js. 실행 설정·유도·템플릿은 bmk_design_patch_pipeline, 초안 계획·채택은 vlm(_vlm() 지연 접근자 하나 — state·layout·refs
  경로는 vlm 을 불러오지 않음). 계획(pipeline_plan)은 노드와 같은 식(Run = runner.plan_calls + split_approval, Draft = vlm.draft_plan)
  으로 only 없는 계획(크롭별) + 선택 계획(해시)을 계산하고 파일 stat 서명(설정보다 먼저 잡음) + 쓴 설정 지문으로 기억(계산 오류는
  다시 쓰지 않음) — 라우트 잠금을 못 잡으면 202 busy. runner 실패는 run {error}(원장 대체 계산 없음), Draft 오류·실행 중은 draft 만
  따로. '반영 필요' 판정 하나 = 계획 apply_pending(Import 미반영 + prepare_inputs 불일치 — 요청마다). Spec Draft 실행 표시
  draft_active(시작 시각)·local_qwen 모델 폴더 set_qwen_dir 실제 구현.
- state: pipeline_rev·plan_v(unchanged 응답에도), 크롭 요약 draft_chip·plan_stale(기억한 계획 — 계산하지 않음).
- _guard 409 에 PipelineConflict·vlm.SpecConflict, 채택 거절(AdoptRefused)은 400 {error, reason|rows}.
- S1-a: /layout/psd 가 고른 이름만 기준점을 스냅숏 값으로(이름 묶음 통째), ack 는 사라진 shape 도 고를 수 있음, names [] 400,
  take 에 사라진 shape 뿐인 이름 400, 메타는 갈라짐 0 일 때만 스냅숏 값.
v6 (2026-10-18, Studio S3b — _proto/s3b/S3B_CONTRACT.md §1·§2)
- /pipeline/prompt 유료 갈래(run·draft·reroll): 인자 → only 정규화 → 설정 게이트 → expect → 실행 중 → 반영 필요 → 계획 잠금(최대
  15초) 안에서 기억값 없이 선택 계획 → 해시 대조 순으로 판정하고 통과하면 템플릿. 응답 키는 stage 와 무관하게 하나(무료 hash·only "").
- /pipeline/plan draft 확인 창 필드(orphan_retries·est_usd·reasons·free · pending_style·mock_forced — 노드 Spec Draft ui·가지와 같은 식),
  GET /pipeline manifest_rev. local_qwen 모델 폴더 미설정은 _draft_cfg 의 오류 하나로(계획·prompt 같은 문구).
v7 (2026-10-19, Studio 사용성 — _proto/STUDIO_S3_SPEC.md §12)
- state 후보 done(원장 calls finished)·via(run·reroll·retry)·old_cell(지금 jobs 에 없는 셀), 셀 pre(그 셀 사전 승인 대기 — 키보드 R 거절),
  크롭 요약 layout_diff(바뀐 크롭의 레이아웃 name·rect·대상), 최상위 layout_added(레이아웃에만 있는 크롭).
- POST /layout/crop update 의 restore_manifest — 마지막 반영 값으로 되돌림(진행(G) 반영 칸의 [되돌리기]).
v8 (2026-10-19, Studio S4 — _proto/STUDIO_S4_SPEC.md §4·§6.2)
- mask 라우트 5종(편집 대상·래스터/3층 PNG·저장·되돌리기·대안 이음매 — 37 RouteDef, 경로 36), 정적 mask_edit.js. 기록·검사·쓰기는
  bmk_design_patch_mask(dmask), 대상·편집 창·기준·z·3층은 Compose 계획(_star_plan → 노드 _plan — 편집 적용도 그 한 곳).
  3층(§2.4 under·patch·over)과 대안 이음매는 _COMPOSE_LOCK 을 함께 쓰고, 대안은 swap 서명별 composite·seams 캐시(대안끼리만 _ALTS_LOCK 202).
- ★ 합성 서명·context 서명·context 썸네일 마스크에 마스크 편집(file·창 — 있을 때만, 편집 없는 프로젝트의 서명 그대로).
  보드는 형식이 맞는 기록만 읽는다(_edit_rec — 깨진 기록은 _plan 처럼 편집 없음).
- state: 후보 mask_edit {at, stale}, 크롭 요약 mask_edits·mask_stale. _guard 409 에 MaskConflict {error, reason}.
  stale = 기준 지문(dmask.is_stale) 또는 편집 창 ≠ 기록 창(_edit_stale — state·/mask/edit 은 계획 없이 _edit_window).
  state 의 stale 은 _plan 이 편집 파일을 쓰지 못할 때도(_edit_usable — 없음·깨짐·크기 다름).
v9 (2026-10-21, 다듬기 배치 3b — 3-3 크롭 잠금)
- layout.json 크롭 locked(bool, 없으면 false) — /layout 크롭 행·state 크롭 요약 locked. POST /layout/crop update 의 locked, 잠긴 크롭은
  locked 만 바꾸는 update 만(그 밖 update·delete 423). POST /layout/psd take 은 잠긴 크롭의 PSD 이름을 건너뛰고 locked 로 보고.
- 3-5b: POST /open_folder 가 Export 기록이 없을 때(PSD 가 없어졌을 때도) 여는 폴더 = 실행 설정 export.subfolder(비면 프로젝트 폴더).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import copy
import functools
import hashlib
import io
import json
import logging
import math
import os
import re
import subprocess
import sys
import threading
import time
import unicodedata
from typing import Callable
from urllib.parse import urlencode

import cv2
import numpy as np
from aiohttp import web
from PIL import Image

try:
    from . import bmk_design_patch_analysis as an
    from . import bmk_design_patch_mask as dmask
    from . import bmk_design_patch_pipeline as pl
    from . import bmk_design_patch_prompt as dprompt
    from . import bmk_design_patch_psd as dpsd
    from . import bmk_design_patch_refs as drefs
    from . import bmk_design_patch_store as store
except ImportError:  # 단독 실행/테스트
    import bmk_design_patch_analysis as an
    import bmk_design_patch_mask as dmask
    import bmk_design_patch_pipeline as pl
    import bmk_design_patch_prompt as dprompt
    import bmk_design_patch_psd as dpsd
    import bmk_design_patch_refs as drefs
    import bmk_design_patch_store as store

logger = logging.getLogger(__name__)

_TAG = "[ComfyUI_BMK_Nodes::DesignPatch]"

API = "/bmk/design_patch"
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web_design_patch")
_STATIC = {"board.html": "text/html", "board.js": "text/javascript", "viewer.js": "text/javascript",
           "layout_geom.js": "text/javascript", "layout_edit.js": "text/javascript", "ref_edit.js": "text/javascript",
           "checklist.js": "text/javascript", "pipeline.js": "text/javascript", "mask_edit.js": "text/javascript", "board.css": "text/css"}

THUMB_KINDS = ("cand", "before", "ref", "delta", "context")
THUMB_SIZES = (128, 192, 256, 384, 512, 768, 1024)
FULL_SIZE = 0              # 썸네일 size 0 = 크롭 해상도 그대로(초점 자르기·축소 없음)
_FULL_KINDS = ("cand", "before")
GRID_SIZE = 256
BIG_SIZE = 512
CANVAS_PREVIEW = 2048      # 캔버스 미리보기(베이스·★ 합성) 긴 변
_SEAM_GAP = 2              # 이음매 구간: 이만큼 이하 틈은 잇는다
_SEAM_MIN_LEN = 6          # 이보다 짧은 닿음은 버린다
_REGION_PAD_MIN, _REGION_PAD_MAX = 64, 256  # 뷰어 원해상도 ★ 합성 영역 = 크롭 rect + 변마다 이만큼
_SEAM_MIN_DIFF = 8.0       # 새 불연속 p90 이 이보다 작으면 보이지 않는 닿음 — 버린다
_SEAM_BAD = 32.0           # 새 불연속 p90 이 이 이상이면 "bad"
_THUMB_VERSION = "dp-thumb/2"  # 2: 옮기기 전 Run·폴더 후보는 quad 로(자기 quad 기준 정합·톤), 마스크는 항목 rect 에서
_THUMB_DIR = "derived/thumbs"
_FOCUS_PAD = 0.10          # 자동 마스크 bbox 합집합을 변마다 이만큼(합 +20%)
_CONTEXT_SCALE = 1.5
_MIN_CROP_IOU = 0.5        # bmk_design_patch._MIN_CROP_IOU 와 같음(부분 패치 판정)
_HEAT_DE_SPAN = 3.0        # ΔE 히트맵 상한 = 3·dE
_GRAY = 96                 # 크롭 소스의 투명(캔버스 밖) 부분 바탕

_FILE_DIRS = ("cands", "inputs", "derived", "masks")
_IMG_EXTS = (".png", ".jpg", ".jpeg", ".webp")
_MAX_BODY = 16 * 1024
_MAX_PNG_BODY = 4 * 1024 * 1024  # POST /mask/save 편집 래스터(L PNG — 실데이터 63–83KB)
_MAX_REROLL_COUNT = 4      # 요청 하나에 더할 수 있는 호출 수
_MAX_CELL_PENDING = 16     # 재굴림 후 셀의 대기 호출 상한(키 반복·버그로 인한 과금 폭주 방지, Run 이 소화하면 다시 가능)

_ROOT_HINT = "등록되지 않은 프로젝트 키입니다 — Review 노드를 실행한 뒤 노드의 Open Board 로 여세요"
_KEY_RE = re.compile(r"[A-Za-z0-9_\-]{1,96}")
_CROP_ID_RE = re.compile(r"[A-Za-z0-9_\-]{1,64}")
_TID_RE = re.compile(r"[A-Z]{1,3}")
_CELL_RE = re.compile(r"[0-9a-f]{24}")
_VARIANT_RE = re.compile(r"[A-Za-z0-9_\-]{1,48}")
_LEDGER_RE = re.compile(r"r(\d+)\.(inflight|done|failed)\.json")
_LAYOUT_TID_RE = re.compile(r"[A-Z]")
_DRAFT_KEY_RE = re.compile(r"[0-9a-f]{24}")   # Spec Draft 초안 키(vlm 원장 폴더 이름)
_SNAPSHOT_KEYS = ("psd", "fingerprint", "crop_group", "crop_pixels_group")  # 노드 _SNAPSHOT_KEYS 와 같음(스냅숏을 믿는 조건)
_SNAP_UNTRUSTED = "믿을 수 있는 PSD 스냅숏이 없습니다(마지막 Import 와 다름)"
_FILE_ORIGINS = ("folder", "psd_harvest")      # 파일·그룹 이름 앞부분 = 크롭 이름으로 찾는 후보 출처(STUDIO_S1_SPEC §5)
_MASK_V_RE = re.compile(r"[0-9a-f]{16}")                      # /mask/img v = 서명 16자(edit 도 — 기록 file·창·지금 창, _edit_sig)
_MASK_KINDS = ("base", "edit", "under", "patch", "over")
_LAYER_KINDS = ("under", "patch", "over")                     # 브러시 미리보기 3층(§2.4) — 한 번에 계산
_NOT_PICKED = "지금 ★ 후보가 아닙니다(★ 해제·제외·크롭에서 빠짐) — 다시 받기"  # dmask.save_edit 의 not_picked 와 같은 문구

_RENDER_SLOTS = threading.BoundedSemaphore(3)                 # 동시에 도는 썸네일 계산 수(라우트 + 미리 생성)
_COMPOSE_LOCK = threading.Lock()                              # ★ 합성(풀해상도 float32 캔버스)은 한 번에 하나
_ALTS_LOCK = threading.Lock()                                 # /mask/alts 계산은 한 번에 하나 — 못 잡으면 202 busy(캐시만이면 잠금 없이). _COMPOSE_LOCK 과
                                                              # 따로: 뷰어 ★ 합성·3층이 그 잠금을 잡고 있으면 202 가 아니라 기다림(후보마다 _composite 가 잡음)
_COMPOSE_DEFAULTS = {"mask_source": "hand_else_auto", "use_registration": True, "use_tone": True,
                     "zorder": "auto_small_on_top"}  # bmk_design_patch._COMPOSE_DEFAULTS 와 같음(노드 모듈은 torch 를 불러서 복사)
_PATH_LOCKS = [threading.Lock() for _ in range(32)]           # 같은 캐시 파일을 두 번 만들지 않게(중첩 금지)
_FOCUS_MEMO: dict[tuple, list[int]] = {}
_FOCUS_LOCK = threading.Lock()
_EDIT_OK: dict[tuple, bool] = {}   # (편집 파일 경로, mtime_ns, 크기, 기록 창) → _plan 이 그 래스터를 쓰나(_edit_usable)
_FLAGS_LOCK = threading.Lock()
_DRAIN: dict[str, float] = {}      # root_key → 요청 시각
_RUNNING: dict[str, int] = {}      # root_key → 진행 중인 Run 수
_DRAFTS: dict[str, list[float]] = {}  # root_key → 진행 중인 Spec Draft 시작 시각(draft_active)
_STARTS: dict[str, list[int]] = {}    # root_key → [Run 시작 수, Spec Draft 시작 수] — 계획 계산 중 시작한 실행(끝났어도)을 알아챔
_PREF: dict[str, dict] = {}        # root_key → 보드가 쓸 분석 설정(prebuild_thumbs·Analyze 노드가 넣고, 재시작 뒤 노드가 board_pref.json 에서 되살림)
_PLAN_WARNED: set[str] = set()
_REFS_MEMO: dict[str, tuple] = {}  # refs_dir → (파일 이름 목록, {(nnn, part): match_refs 결과}) — 이름 목록이 바뀌면 버림
_REFS_LOCK = threading.Lock()


class _Reject(Exception):
    """4xx 응답으로 바꿀 오류(한국어 메시지). extra 는 응답 JSON 에 함께 싣는다(레퍼런스 확인 409 의 explicit)."""

    def __init__(self, status: int, message: str, **extra):
        super().__init__(message)
        self.status = status
        self.message = message
        self.extra = extra


# ══════════════════════════════════════════════════════════════════════
# drain / run 레지스트리 (Run 노드·runner 가 조회)
# runner.run_calls 는 자기 레지스트리(is_running / request_drain / drain_requested)를 가진다. 그 Run 이 돌고 있으면
# 거기에 걸고(run_calls 의 기본 drain_flag 가 본다), 아니면 이 모듈 플래그(run_active 로 감싼 Run 용)에 건다.
# drain_requested / is_running 은 둘을 합쳐 답한다 → 노드가 어느 쪽을 drain_flag 로 넘겨도 보드 drain 이 먹는다.
# ══════════════════════════════════════════════════════════════════════
def set_drain(root: str) -> bool:
    """진행 중인 Run 에 '새 발사 중지'를 요청한다(진행 중인 호출은 끝까지 받는다). 반환: 진행 중인 Run 이 있는지.
    실행 중이 아닐 때 건 이 모듈 플래그는 다음 run_active 진입 때 지워진다(보드 라우트는 실행 중일 때만 부른다)."""
    runner = _runner()
    if runner is not None and runner.is_running(root):
        runner.request_drain(root)
        return True
    k = store.root_key(root)
    with _FLAGS_LOCK:
        _DRAIN[k] = time.time()
        return _RUNNING.get(k, 0) > 0


def drain_requested(root: str) -> bool:
    with _FLAGS_LOCK:
        if store.root_key(root) in _DRAIN:
            return True
    runner = _runner()
    return bool(runner is not None and runner.drain_requested(root))


def clear_drain(root: str) -> None:
    """이 모듈의 drain 플래그를 지운다(runner 플래그는 run_calls 가 시작할 때 스스로 지운다)."""
    with _FLAGS_LOCK:
        _DRAIN.pop(store.root_key(root), None)


def is_running(root: str) -> bool:
    with _FLAGS_LOCK:
        if _RUNNING.get(store.root_key(root), 0) > 0:
            return True
    runner = _runner()
    return bool(runner is not None and runner.is_running(root))


@contextlib.contextmanager
def run_active(root: str):
    """Run 이 호출을 발사하는 동안 감싼다: 보드의 running 표시 + drain 플래그 수명 관리.
    처음 들어가는 Run 은 지난 drain 플래그를 지우고, 마지막으로 나가는 Run 도 지운다."""
    k = store.root_key(root)
    with _FLAGS_LOCK:
        if _RUNNING.get(k, 0) == 0:
            _DRAIN.pop(k, None)
        _RUNNING[k] = _RUNNING.get(k, 0) + 1
        _STARTS.setdefault(k, [0, 0])[0] += 1
    try:
        yield
    finally:
        with _FLAGS_LOCK:
            n = _RUNNING.get(k, 1) - 1
            if n > 0:
                _RUNNING[k] = n
            else:
                _RUNNING.pop(k, None)
                _DRAIN.pop(k, None)


@contextlib.contextmanager
def draft_active(root: str):
    """Spec Draft 노드 run() 전체를 감싼다(STUDIO_S3_SPEC §2.4·§3.5): 그동안 계획 라우트는 draft {running: true} 만 답하고(진행 중
    단위의 inflight 를 'orphan 재시도(과금됐을 수 있음)'로 잘못 읽지 않게), 진행 라우트는 시작 시각(draft_since) 뒤 원장을 센다.
    run_active·drain 플래그와 별개. 예외·취소로 나가도 지운다."""
    k = store.root_key(root)
    t = time.time()
    with _FLAGS_LOCK:
        _DRAFTS.setdefault(k, []).append(t)
        _STARTS.setdefault(k, [0, 0])[1] += 1
    try:
        yield
    finally:
        with _FLAGS_LOCK:
            ts = _DRAFTS.get(k, [])
            ts.remove(t)
            if not ts:
                _DRAFTS.pop(k, None)


def draft_since(root: str) -> float | None:
    """진행 중인 Spec Draft 의 가장 이른 시작 시각(epoch 초) — 없으면 None."""
    with _FLAGS_LOCK:
        ts = _DRAFTS.get(store.root_key(root))
        return min(ts) if ts else None


def board_url(key: str) -> str:
    return f"{API}/board/?{urlencode({'root': key})}"


# ══════════════════════════════════════════════════════════════════════
# 매니페스트 조회 도우미
# ══════════════════════════════════════════════════════════════════════
def _rect_iou(a, b) -> float:
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _analyzable(crop: dict, cand: dict) -> bool:
    """크롭 rect 로 배치하는 후보 = 노드가 지금 크롭 기준으로 분석하는 후보: 크롭 전체를 덮는 수확 후보, 제자리
    Run·폴더 후보. 부분 패치 SO 와 rect 를 옮기기 전 Run·폴더 후보는 quad 그대로(노드 _gen_frame 과 같은 규칙)."""
    bb = dpsd.quad_bbox(cand["quad"])
    if cand.get("origin") in ("run", "folder"):
        return bb == crop["rect"]
    return _rect_iou(bb, crop["rect"]) >= _MIN_CROP_IOU


def _entry_for(m: dict, key: str, pref: dict | None) -> dict | None:
    """후보의 분석 항목: pref(분석 설정)와 params 가 같은 항목 중 seq 가 가장 큰(같으면 마지막) 것 = Analyze 가 그 설정으로
    지금 쓰는 항목(bmk_design_patch._ensure_analysis 가 seq 를 맞춤), 없으면 가장 최근 항목. pref 를 모르면(board_pref.json 이
    없는 옛 프로젝트·미등록 루트에서 Review·Analyze 전 — 재시작 뒤엔 노드 모듈이 board_pref.json 에서 되살림) 가장 최근 항목의 설정으로
    같은 규칙."""
    per = (m.get("analysis") or {}).get(key)
    if not isinstance(per, dict) or not per:
        return None
    if "params_hash" in per:  # dp-an/1 평평한 항목
        return per
    entries = [e for e in per.values() if isinstance(e, dict)]
    if not entries:
        return None
    if pref is None:
        pref = entries[-1].get("params")
    same = [e for e in entries if e.get("params") == pref]
    return max(reversed(same), key=lambda e: e.get("seq", 0)) if same else entries[-1]


def _usable(entry: dict | None) -> bool:
    return entry is not None and not entry.get("skipped") and isinstance(entry.get("reg"), dict)


def _target_members(m: dict, crop_id: str, tid: str) -> list[dict]:
    return [c for c in m["candidates"] if c.get("crop_id") == crop_id and c.get("tid") == tid]


def _crop(m: dict, crop_id: str) -> dict | None:
    return next((c for c in m["crops"] if c.get("id") == crop_id), None)


def _cand(m: dict, key: str) -> dict | None:
    return next((c for c in m["candidates"] if c.get("key") == key), None)


def _job_tids(job: dict) -> list[str]:
    return [t for t in str(job.get("tid") or "").split(",") if t]


def _target_jobs(m: dict, crop_id: str, tid: str) -> list[dict]:
    """그 타깃이 들어간 jobs(타깃 단독 작업 먼저)."""
    jobs = [j for j in m["jobs"] if j.get("crop_id") == crop_id and tid in _job_tids(j)]
    return sorted(jobs, key=lambda j: len(_job_tids(j)) > 1)


def _target_refs(m: dict, crop_id: str, tid: str) -> list[str]:
    """Prepare 가 평탄화해 inputs/ 에 둔 레퍼런스(그 타깃이 들어간 jobs 의 role=ref, 순서대로·중복 없이)."""
    out: list[str] = []
    for j in _target_jobs(m, crop_id, tid):
        for inp in j.get("inputs") or []:
            f = inp.get("file") if isinstance(inp, dict) else None
            if inp.get("role") == "ref" and isinstance(f, str) and f not in out:
                out.append(f)
    return out


# 원장 calls approved_by 의 앞부분("hash:<h>" | "board") → 후보 캡션의 실행 종류. 다시 과금한 재시도(kind retry_failed·retry_orphan)는 "retry" —
# 다운로드 복구(approved_by "retry")는 req.json 을 다시 쓰지 않아 calls 에 나오지 않는다(원래 종류·복구 시각으로 보임)
_VIA = {"hash": "run", "board": "reroll"}


def _call_via(call: dict | None) -> str | None:
    if not call:
        return None
    if str(call.get("kind") or "").startswith("retry"):
        return "retry"
    return _VIA.get(str(call.get("approved_by") or "").split(":")[0])


def _name_fold(name: str) -> str:
    """크롭 이름 비교 키(store.check_crop_name 의 중복 판정과 같은 NFC·casefold)."""
    return unicodedata.normalize("NFC", name).strip().casefold()


def _cell_of(cand: dict) -> tuple[str | None, str | None, int | None]:
    """run 후보의 (cell_key, call_id, rep). call_id = f"{cell_key}_r{rep}"."""
    info = cand.get("origin_info") or {}
    call_id = info.get("call_id")
    if not isinstance(call_id, str) or "_r" not in call_id:
        return info.get("cell_key"), None, None
    ck, rep = call_id.rsplit("_r", 1)
    return ck, call_id, int(rep) if rep.isdigit() else None


def _mock_active(root: str) -> bool:
    """Run 의 MockBackend 활성 조건(M2_SPEC §2.3): BMK_DP_MOCK_API=1 또는 <base>/bmk_design_patch/_MOCK_API.
    runner 가 있으면 Run 이 실제로 쓰는 판정(runner.mock_enabled — 1/true/yes/on)을 그대로 쓴다."""
    runner = _runner()
    if runner is not None:
        return runner.mock_enabled(root)
    return os.environ.get("BMK_DP_MOCK_API") == "1" or os.path.isfile(os.path.join(os.path.dirname(root), "_MOCK_API"))


# ══════════════════════════════════════════════════════════════════════
# 대기 호출 수
# ══════════════════════════════════════════════════════════════════════
@functools.lru_cache(maxsize=1)
def _runner():
    """bmk_design_patch_runner(plan_calls·실행 레지스트리) — 노드 모듈이 두 모듈을 함께 import 하고 runner 가 이 모듈의
    drain 함수를 쓸 수도 있으므로 순환 import 를 피해 처음 쓸 때 읽는다. 없으면 None(원장 파일로 계산)."""
    try:
        from . import bmk_design_patch_runner as runner
    except ImportError:
        try:
            import bmk_design_patch_runner as runner
        except ImportError:
            return None
    return runner if hasattr(runner, "plan_calls") else None


@functools.lru_cache(maxsize=1)
def _vlm():
    """bmk_design_patch_vlm — 보드의 vlm import 는 여기 하나(STUDIO_S3_SPEC §6). 초안 계획·채택 라우트만 처음 쓸 때 읽는다:
    state·layout·refs 경로는 부르지 않는다(S2 B0 — 새 프로세스 프로브가 sys.modules 에 vlm 이 없음을 본다)."""
    try:
        from . import bmk_design_patch_vlm as dvlm
    except ImportError:
        import bmk_design_patch_vlm as dvlm
    return dvlm


def _ledger_pending(root: str, m: dict) -> dict[str, int]:
    """runner 가 없을 때의 계산: 셀 목표(reps + rerolls) − 이미 발사된 rep 수. 보드 승인 여부를 보지 않으므로 재굴림이
    안 돈 기본 호출을 대신한 셀은 runner(max(reps, 보드 아닌 호출 + rerolls))보다 많게 센다 — 상한 409 쪽으로만 틀림.
    발사됨 = 원장 r<rep>.inflight/done/failed.json 이 있거나 calls 요약의 status 가 inflight/done/failed/orphaned."""
    fired: dict[str, set] = {}
    for c in m.get("calls") or []:
        if not isinstance(c, dict) or c.get("status") not in ("inflight", "done", "failed", "orphaned"):
            continue
        rep = c.get("rep")
        if rep is None and isinstance(c.get("call_id"), str) and "_r" in c["call_id"]:
            rep = c["call_id"].rsplit("_r", 1)[1]
        if str(rep).isdigit():
            fired.setdefault(str(c.get("cell_key")), set()).add(int(rep))
    out: dict[str, int] = {}
    rerolls = m.get("rerolls") or {}
    for j in m["jobs"]:
        ck = j.get("cell_key")
        if not isinstance(ck, str) or not _CELL_RE.fullmatch(ck):
            continue
        reps = set(fired.get(ck, ()))
        folder = os.path.join(root, "cands", ck)
        if os.path.isdir(folder):
            for fn in os.listdir(folder):
                mt = _LEDGER_RE.fullmatch(fn)
                if mt:
                    reps.add(int(mt.group(1)))
        target = int(j.get("reps") or 1) + int(rerolls.get(ck) or 0)
        out[ck] = max(0, target - len(reps))
    return out


def _pending_cells(root: str, m: dict) -> tuple[dict[str, int], dict[str, int]]:
    """({cell_key: 대기 호출 수}, {cell_key: 그중 보드 사전 승인 수}). runner.plan_calls 가 있으면 그것(Run 과 같은 판단:
    다운로드 복구 포함), 없으면 _ledger_pending(사전 승인 = 셀마다 min(rerolls, 대기))."""
    runner = _runner()
    if runner is not None:
        try:
            plan = runner.plan_calls(m, root)
        except Exception as e:  # 보드 폴링이 runner 오류로 죽지 않게 — 원장 계산으로 대신하고 한 번만 알림
            rk = store.root_key(root)
            if rk not in _PLAN_WARNED:
                _PLAN_WARNED.add(rk)
                logger.warning(f"{_TAG} Board: runner.plan_calls 실패 → 원장 파일로 대기 호출 계산 ({e})")
        else:
            out: dict[str, int] = {}
            pre: dict[str, int] = {}
            for it in plan.get("pending") or []:
                ck = str(it.get("cell_key") if isinstance(it, dict) else str(it).rsplit("_r", 1)[0])
                out[ck] = out.get(ck, 0) + 1
                if isinstance(it, dict) and it.get("preapproved"):
                    pre[ck] = pre.get(ck, 0) + 1
            return out, pre
    out = _ledger_pending(root, m)
    rerolls = m.get("rerolls") or {}
    return out, {ck: k for ck, n in out.items() if (k := min(int(rerolls.get(ck) or 0), n))}


def _pending(root: str, m: dict) -> tuple[dict[str, int], int]:
    """({cell_key: 대기 호출 수}, 그중 보드 사전 승인 수 합) — _pending_cells 의 합."""
    out, pre = _pending_cells(root, m)
    return out, sum(pre.values())


def _est_usd(job: dict):
    try:
        lo, hi = store.estimate_usd(job.get("model"), job.get("quality"), job.get("size"), int(job.get("n") or 1),
                                    len(job.get("inputs") or []))
    except (ValueError, TypeError):
        return None
    return [lo, hi]


# ══════════════════════════════════════════════════════════════════════
# 썸네일 — 파일·이미지 도우미
# ══════════════════════════════════════════════════════════════════════
def _sig(*parts) -> str:
    blob = json.dumps([_THUMB_VERSION, *parts], sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _thumb_path(root: str, name: str) -> str:
    return store.resolve_path(root, f"{_THUMB_DIR}/{name}")


def _lock_for(path: str) -> threading.Lock:
    return _PATH_LOCKS[int(hashlib.md5(path.encode("utf-8")).hexdigest()[:8], 16) % len(_PATH_LOCKS)]


def _read_bytes(path: str) -> bytes | None:
    try:
        with open(path, "rb") as f:
            return f.read()
    except FileNotFoundError:
        return None


def _open_rel(root: str, rel: str) -> str:
    path = store.resolve_path(root, rel)
    if not os.path.isfile(path):
        raise _Reject(404, f"파일이 없습니다: {rel}")
    return path


def _read_rgb(path: str) -> np.ndarray:
    with Image.open(path) as im:
        return np.asarray(im.convert("RGB"))


def _read_rgba(path: str) -> np.ndarray:
    with Image.open(path) as im:
        return np.asarray(im.convert("RGBA"))


def _read_l(path: str) -> np.ndarray:
    with Image.open(path) as im:
        return np.asarray(im.convert("L"))


def _decode(path: str) -> np.ndarray:
    """후보 원본 → RGB, 부분 투명이 있으면 RGBA (bmk_design_patch._decode 와 같은 규칙)."""
    with Image.open(path) as im:
        im.load()
        if "A" in im.getbands() or (im.mode == "P" and "transparency" in im.info):
            rgba = np.asarray(im.convert("RGBA"))
            return rgba if int(rgba[..., 3].min()) < 255 else np.ascontiguousarray(rgba[..., :3])
        return np.asarray(im.convert("RGB"))


def _over_gray(rgba: np.ndarray) -> np.ndarray:
    if rgba.shape[2] == 3:
        return rgba
    a = rgba[..., 3:4].astype(np.float32) / 255.0
    return np.clip(rgba[..., :3].astype(np.float32) * a + _GRAY * (1.0 - a) + 0.5, 0, 255).astype(np.uint8)


def _load_delta(root: str, tone: dict) -> np.ndarray:
    """톤 보정장 .npy(σ/4 격자 float16) → 크롭 크기 float32 (bmk_design_patch._load_delta 와 같음)."""
    with open(store.resolve_path(root, tone["delta"]), "rb") as f:
        small = np.load(io.BytesIO(f.read()), allow_pickle=False).astype(np.float32)
    w, h = tone["size"]
    if small.shape[:2] != (h, w):
        small = cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)
    return small


def _mask_region(mask: dict | None, x0: int, y0: int, x1: int, y1: int) -> np.ndarray | None:
    """{"arr","left","top","bg"} 캔버스 마스크 → 창 [x0,y0,x1,y1] float32 0..1 (bmk_design_patch._mask_region)."""
    if mask is None:
        return None
    out = np.full((y1 - y0, x1 - x0), int(mask["bg"]), np.uint8)
    arr = mask["arr"]
    ax0, ay0 = int(mask["left"]), int(mask["top"])
    ix0, iy0 = max(x0, ax0), max(y0, ay0)
    ix1, iy1 = min(x1, ax0 + arr.shape[1]), min(y1, ay0 + arr.shape[0])
    if ix1 > ix0 and iy1 > iy0:
        out[iy0 - y0:iy1 - y0, ix0 - x0:ix1 - x0] = arr[iy0 - ay0:iy1 - ay0, ix0 - ax0:ix1 - ax0]
    return out.astype(np.float32) / 255.0


def _fit(img: np.ndarray, size: int) -> Image.Image:
    pil = Image.fromarray(np.ascontiguousarray(img))
    s = size / max(pil.width, pil.height)
    return pil.resize((max(1, round(pil.width * s)), max(1, round(pil.height * s))), Image.LANCZOS)


def _jpeg(pil: Image.Image, quality: int = 90) -> bytes:
    buf = io.BytesIO()
    pil.convert("RGB").save(buf, "JPEG", quality=quality, subsampling=0)
    return buf.getvalue()


def _cached(root: str, name: str, make: Callable, prepare: Callable | None = None) -> bytes:
    """derived/thumbs/<name> 이 있으면 그 bytes, 없으면 make() → 원자 저장. 같은 파일은 한 스레드만 만든다.
    prepare 가 있으면 잠금 밖에서 먼저 불러 결과를 make(결과) 로 넘긴다 — prepare 는 자기 _cached(placed)를 쓰므로
    한 스레드가 파일 잠금·계산 슬롯을 겹쳐 잡지 않게(겹치면 미리 생성 스레드끼리 교착)."""
    path = _thumb_path(root, name)
    data = _read_bytes(path)
    if data is not None:
        return data
    pre = prepare() if prepare is not None else None
    with _lock_for(path):
        data = _read_bytes(path)
        if data is None:
            with _RENDER_SLOTS:
                data = make() if prepare is None else make(pre)
            store._atomic_write_bytes(path, data)
    return data


# ══════════════════════════════════════════════════════════════════════
# 썸네일 — 배치·초점·kind 별 렌더
# ══════════════════════════════════════════════════════════════════════
def _placed_parts(crop: dict, cand: dict, entry: dict | None, reg: bool = True, tone: bool = True) -> list:
    return [cand["file"], [float(v) for v in cand["quad"]], cand.get("src_size"), crop["rect"],
            entry.get("params_hash") if _usable(entry) else None, reg, tone]


def _mask_parts(cand: dict, entry: dict | None, edit: list | None = None) -> list:
    """마스크 서명 재료. edit = S4 마스크 편집의 서명 재료(_edit_part) — 있을 때만 덧붙인다(편집 없는 후보의 서명 그대로)."""
    hm = cand.get("hand_mask") or {}
    auto = entry.get("automask") if _usable(entry) else None
    parts = [hm.get("file"), hm.get("rect"), int(hm.get("bg", 0)) if hm else None, auto]
    return parts + [edit] if edit else parts


def _affine_quad(quad: list[float], src_size) -> list[float]:
    """원근(Distort) quad → Compose 와 같은 평행사변형 근사(노드 _plan — 쓰기·캐시 렌더는 affine 만)."""
    if dpsd.quad_kind(quad) != "perspective":
        return quad
    sw, sh = src_size
    return an.quad_from_matrix(an.matrix_from_quad(quad, [0, 0, sw, sh]), [0, 0, sw, sh])


def _make_placed(root: str, crop: dict, cand: dict, entry: dict | None, reg: bool = True,
                 tone: bool = True) -> tuple[np.ndarray, np.ndarray | None]:
    """크롭 로컬 (rgb HxWx3 uint8, cover HxW float32 | None). Analyze 의 배치·정합 + 톤 보정장, 부분 패치는 quad 렌더.
    reg·tone = Compose 의 use_registration·use_tone(context 가 export 설정을 따를 때 끔)."""
    x0, y0, x1, y1 = crop["rect"]
    w, h = x1 - x0, y1 - y0
    path = _open_rel(root, cand["file"])
    if _analyzable(crop, cand):
        G = an.place(_read_rgb(path), (w, h))
        if _usable(entry):
            applied = entry["reg"].get("applied")
            if reg and applied:
                G = an.warp_affine(G, entry["reg"]["matrix"])
            t = entry.get("tone") or {}
            if tone and t.get("delta") and t.get("size"):
                delta = _load_delta(root, t)
                if applied and not reg:  # 보정장은 정합된 좌표 — 정합을 안 쓰면 후보 좌표로 되돌림(Compose _cand_pixels 처럼)
                    delta = an.warp_affine(delta, entry["reg"]["matrix"], inverse=True)
                G = an.apply_delta(G, delta)
        return np.ascontiguousarray(G[..., :3]), None
    pix = _decode(path)
    quad = [float(v) for v in cand["quad"]]
    if _usable(entry) and entry.get("rect") == dpsd.quad_bbox(cand["quad"]):
        # 자기 quad 기준 분석(rect 를 옮기기 전 Run·폴더 후보): Compose 와 같은 정합 quad·톤(bmk_design_patch._cand_pixels).
        # 크롭 기준 분석이 남은 수확 부분 패치(옮겨 다시 수확, Analyze 전)는 quad 그대로
        applied = entry["reg"].get("applied")
        if reg and applied:
            quad = [float(v) for v in entry["quad_reg"]]
        t = entry.get("tone") or {}
        if tone and t.get("delta") and t.get("size"):
            delta = _load_delta(root, t)
            if applied:
                delta = an.warp_affine(delta, entry["reg"]["matrix"], inverse=True)
            pix = an.apply_delta(pix, delta, out_size=(pix.shape[1], pix.shape[0]))
    rgb, alpha, left, top = dpsd.render_so_cache(pix, _affine_quad(quad, cand["src_size"]))
    out = np.zeros((h, w, 3), np.uint8)
    cov = np.zeros((h, w), np.float32)
    a = np.ones(rgb.shape[:2], np.float32) if alpha is None else alpha.astype(np.float32) / 255.0
    ix0, iy0 = max(x0, left), max(y0, top)
    ix1, iy1 = min(x1, left + rgb.shape[1]), min(y1, top + rgb.shape[0])
    if ix1 > ix0 and iy1 > iy0:
        out[iy0 - y0:iy1 - y0, ix0 - x0:ix1 - x0] = rgb[iy0 - top:iy1 - top, ix0 - left:ix1 - left]
        cov[iy0 - y0:iy1 - y0, ix0 - x0:ix1 - x0] = a[iy0 - top:iy1 - top, ix0 - left:ix1 - left]
    return out, cov


def _placed(root: str, crop: dict, cand: dict, entry: dict | None, reg: bool = True,
            tone: bool = True) -> tuple[np.ndarray, np.ndarray | None]:
    """_make_placed 의 크롭 해상도 결과를 placed_<서명>.png(무손실)로 캐시해 다른 kind·크기가 재사용."""
    name = f"placed_{_sig('placed', _placed_parts(crop, cand, entry, reg, tone))}.png"

    def make() -> bytes:
        rgb, cov = _make_placed(root, crop, cand, entry, reg, tone)
        arr = rgb if cov is None else np.dstack([rgb, np.clip(cov * 255.0 + 0.5, 0, 255).astype(np.uint8)])
        buf = io.BytesIO()
        Image.fromarray(arr).save(buf, "PNG", compress_level=1)
        return buf.getvalue()

    with Image.open(io.BytesIO(_cached(root, name, make))) as im:
        arr = np.asarray(im.convert("RGBA" if "A" in im.getbands() else "RGB"))
    if arr.shape[2] == 4:
        return np.ascontiguousarray(arr[..., :3]), arr[..., 3].astype(np.float32) / 255.0
    return arr, None


def _grow_to(box: list[int], minimum: int, w: int, h: int) -> list[int]:
    """축마다 길이가 minimum(크롭보다 크면 크롭 길이)보다 짧으면 가운데를 유지한 채 넓힌다(크롭 안으로)."""
    out = list(box)
    for i0, i1, lim in ((0, 2, w), (1, 3, h)):
        need = min(minimum, lim)
        if out[i1] - out[i0] < need:
            lo = max(0, min(round((out[i0] + out[i1] - need) / 2), lim - need))
            out[i0], out[i1] = lo, lo + need
    return out


def focus_box(root: str, crop: dict, entries) -> list[int]:
    """타깃 초점 박스(크롭 로컬 [x0,y0,x1,y1]): 자동 마스크(>= 0.5) bbox 합집합을 변마다 10% 넓힘
    (최소 크롭 짧은 변의 1/4). 쓸 수 있는 자동 마스크가 없으면 크롭 전체. rect 를 옮기기 전 후보의 마스크는 뺀다."""
    x0, y0, x1, y1 = crop["rect"]
    w, h = x1 - x0, y1 - y0
    files = sorted({e["automask"] for e in entries if _usable(e) and e.get("automask")
                    and (e.get("rect") or crop["rect"]) == crop["rect"]})
    if not files:
        return [0, 0, w, h]
    memo_key = (store.root_key(root), w, h, tuple(files))
    with _FOCUS_LOCK:
        hit = _FOCUS_MEMO.get(memo_key)
    if hit is not None:
        return list(hit)
    bx = None
    for f in files:
        try:
            a = _read_l(store.resolve_path(root, f))
        except OSError:  # 없음, 또는 Analyze 가 밀려난 분석 항목의 derived 파일을 지우는 중(_drop_derived)
            continue
        if a.shape != (h, w):
            continue
        ys, xs = np.nonzero(a >= 128)
        if not len(xs):
            continue
        b = [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]
        bx = b if bx is None else [min(bx[0], b[0]), min(bx[1], b[1]), max(bx[2], b[2]), max(bx[3], b[3])]
    if bx is None:
        box = [0, 0, w, h]
    else:
        px, py = math.ceil((bx[2] - bx[0]) * _FOCUS_PAD), math.ceil((bx[3] - bx[1]) * _FOCUS_PAD)
        box = [max(0, bx[0] - px), max(0, bx[1] - py), min(w, bx[2] + px), min(h, bx[3] + py)]
        box = _grow_to(box, max(16, min(w, h) // 4), w, h)
    with _FOCUS_LOCK:
        if len(_FOCUS_MEMO) > 2048:
            _FOCUS_MEMO.clear()
        _FOCUS_MEMO[memo_key] = list(box)
    return box


def _source_local(root: str, crop: dict) -> np.ndarray:
    """크롭 소스 RGBA(크롭 로컬, 캔버스 밖 = 알파 0)."""
    return _read_rgba(_open_rel(root, crop["source"]))


def _composited(root: str, crop: dict, placed) -> tuple[np.ndarray, np.ndarray]:
    """(크롭 소스 RGBA, 크롭 소스 위에 놓은 후보 RGB) — 부분 패치는 덮은 곳만 후보. placed = _placed() 결과."""
    src = _source_local(root, crop)
    rgb, cov = placed
    if cov is None:
        return src, rgb
    bg = _over_gray(src).astype(np.float32)
    return src, np.clip(bg + (rgb.astype(np.float32) - bg) * cov[..., None] + 0.5, 0, 255).astype(np.uint8)


def _focus_crop(img: np.ndarray, box: list[int]) -> np.ndarray:
    x0, y0, x1, y1 = box
    return img[y0:y1, x0:x1]


def _automask_local(root: str, entry: dict | None, crop: dict) -> np.ndarray | None:
    """자동 마스크 → 크롭 로컬. 마스크는 분석 기준 rect(항목의 rect, 예전 항목은 크롭 rect)의 좌표 — rect 를 옮기기 전
    Run·폴더 후보는 지금 크롭 창으로 옮겨 자른다. 크기가 그 rect 와 다르면(옮기기 전에 만든 예전 항목) None."""
    if not _usable(entry) or not entry.get("automask"):
        return None
    path = store.resolve_path(root, entry["automask"])
    if not os.path.isfile(path):
        return None
    a = _read_l(path)
    ax0, ay0, ax1, ay1 = entry.get("rect") or crop["rect"]
    if a.shape != (ay1 - ay0, ax1 - ax0):
        return None
    return _mask_region({"arr": a, "left": ax0, "top": ay0, "bg": 0}, *crop["rect"])


def _final_mask_local(root: str, crop: dict, cand: dict, entry: dict | None,
                      mask_source: str = "hand_else_auto", edit: dict | None = None) -> np.ndarray | None:
    """Compose 의 최종 마스크(bmk_design_patch._final_mask 와 같은 규칙): hand = 손 마스크(없으면 None), hand_else_auto = 손 마스크가
    있으면 손, auto·hand_else_auto = 자동 마스크, 자동 마스크가 없으면 손 마스크. None = 마스크 없음(전체 보임).
    edit = S4 마스크 편집 기록 — 쓸 수 있으면 mask_source 와 상관없이 그 래스터(노드 _plan 처럼 기준 대신, 못 읽으면 기준). 노드는 ★ 에만
    적용하지만 context 는 ★ 가 아닌 후보의 기록도 적용한다 — "★ 하면" 미리보기(다시 ★ 하면 기록이 살아남, 명세 §3.1)."""
    if edit is not None:
        em, _why = dmask.edit_mask(root, edit)
        if em is not None:
            return _mask_region(em, *crop["rect"])
    hm = cand.get("hand_mask")
    hand = None
    if hm:
        arr = _read_l(_open_rel(root, hm["file"]))
        hand = _mask_region({"arr": arr, "left": int(hm["rect"][0]), "top": int(hm["rect"][1]),
                             "bg": int(hm.get("bg", 0))}, *crop["rect"])
    if mask_source == "hand" or (mask_source == "hand_else_auto" and hand is not None):
        return hand
    auto = _automask_local(root, entry, crop)
    return auto if auto is not None else hand


def _out(img: np.ndarray, focus, size: int) -> bytes:
    """size 0 = 크롭 해상도 그대로(JPEG q95, 초점 무시), 아니면 초점 박스를 잘라 size 에 맞춤."""
    if size == FULL_SIZE:
        return _jpeg(Image.fromarray(np.ascontiguousarray(img)), quality=95)
    return _jpeg(_fit(_focus_crop(img, focus), size))


def _render_cand(root, crop, placed, focus, size) -> bytes:
    _src, img = _composited(root, crop, placed)
    return _out(img, focus, size)


def _render_before(root, crop, focus, size) -> bytes:
    return _out(_over_gray(_source_local(root, crop)), focus, size)


def _render_ref(root, rel, size) -> bytes:
    path = _open_rel(root, rel)
    with Image.open(path) as im:
        rgba = np.asarray(im.convert("RGBA"))
    return _jpeg(_fit(_over_gray(rgba), size))


def _render_delta(root, crop, entry, placed, focus, size) -> bytes:
    src, img = _composited(root, crop, placed)
    d = an.delta_e(src, img, blur=1.5)
    dE = float(((entry or {}).get("params") or {}).get("dE") or 8.0)
    v = np.clip(d / (dE * _HEAT_DE_SPAN), 0.0, 1.0)
    heat = cv2.cvtColor(cv2.applyColorMap((v * 255.0 + 0.5).astype(np.uint8), cv2.COLORMAP_INFERNO), cv2.COLOR_BGR2RGB)
    am = _automask_local(root, entry, crop)
    if am is not None:
        fx0, fy0, fx1, fy1 = focus
        t = max(1, round(1.5 * max(fx1 - fx0, fy1 - fy0) / size))
        k = np.ones((2 * t + 1, 2 * t + 1), np.uint8)
        edge = cv2.morphologyEx((am >= 0.5).astype(np.uint8), cv2.MORPH_GRADIENT, k) > 0
        heat[edge] = (40, 230, 230)
    return _jpeg(_fit(_focus_crop(heat, focus), size))


def _canvas_wh(m: dict) -> tuple[int, int]:
    if not m.get("base") or not m.get("canvas"):
        raise _Reject(409, "베이스가 없습니다 — Import PSD 를 먼저 실행하세요")
    W, H = (int(v) for v in m["canvas"])
    return W, H


def _render_context(root, m, crop, cand, entry, placed, size) -> bytes:
    W, H = _canvas_wh(m)
    x0, y0, x1, y1 = crop["rect"]
    w, h = x1 - x0, y1 - y0
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    rx0, ry0 = max(0, math.floor(cx - w * _CONTEXT_SCALE / 2)), max(0, math.floor(cy - h * _CONTEXT_SCALE / 2))
    rx1, ry1 = min(W, math.ceil(cx + w * _CONTEXT_SCALE / 2)), min(H, math.ceil(cy + h * _CONTEXT_SCALE / 2))
    if rx1 <= rx0 or ry1 <= ry0:
        raise _Reject(409, "크롭이 캔버스 밖에 있습니다")
    base = _read_rgb(_open_rel(root, m["base"]))
    region = base[ry0:ry1, rx0:rx1].astype(np.float32)
    rgb, cov = placed
    mask = _final_mask_local(root, crop, cand, entry, _compose_settings(root, m)["mask_source"], _edit_rec(m, cand["key"]))
    eff = np.ones((h, w), np.float32) if cov is None else cov
    if mask is not None:
        eff = eff * mask
    ix0, iy0, ix1, iy1 = max(x0, rx0), max(y0, ry0), min(x1, rx1), min(y1, ry1)
    if ix1 > ix0 and iy1 > iy0:
        dst = region[iy0 - ry0:iy1 - ry0, ix0 - rx0:ix1 - rx0]
        src = rgb[iy0 - y0:iy1 - y0, ix0 - x0:ix1 - x0].astype(np.float32)
        a = eff[iy0 - y0:iy1 - y0, ix0 - x0:ix1 - x0, None]
        region[iy0 - ry0:iy1 - ry0, ix0 - rx0:ix1 - rx0] = dst + (src - dst) * a
    out = np.clip(region + 0.5, 0, 255).astype(np.uint8)
    t = max(1, round(max(rx1 - rx0, ry1 - ry0) / size))
    cv2.rectangle(out, (x0 - rx0, y0 - ry0), (x1 - rx0 - 1, y1 - ry0 - 1), (255, 170, 40), t)
    return _jpeg(_fit(out, size))


def _target_ctx(root: str, m: dict, crop: dict, tid: str, pref: dict | None) -> dict:
    cands = _target_members(m, crop["id"], tid)
    entries = {c["key"]: _entry_for(m, c["key"], pref) for c in cands}
    return {"cands": cands, "entries": entries, "focus": focus_box(root, crop, entries.values())}


def _view_flags(root: str, m: dict, kind: str) -> tuple[bool, bool]:
    """배치의 (정합, 톤): context 는 베이스 위 합성이라 export 의 Compose 설정, 나머지는 Analyze 와 같게 둘 다 켬."""
    if kind != "context":
        return True, True
    cs = _compose_settings(root, m)
    return bool(cs["use_registration"]), bool(cs["use_tone"])


def _cand_sig(root: str, kind: str, size: int, m: dict, crop: dict, cand: dict, entry: dict | None, focus: list[int]) -> str:
    parts = _placed_parts(crop, cand, entry, *_view_flags(root, m, kind))
    if size == FULL_SIZE:
        focus = None
    if kind == "cand":
        return _sig("cand", size, parts, crop["source"], focus)
    if kind == "delta":
        dE = ((entry or {}).get("params") or {}).get("dE") if _usable(entry) else None
        return _sig("delta", size, parts, crop["source"], focus, _mask_parts(cand, entry)[3], dE)
    ms = _compose_settings(root, m)["mask_source"]
    rec = _edit_rec(m, cand["key"])
    return _sig("context", size, parts, _mask_parts(cand, entry, _edit_part(root, rec) if rec else None), ms, m.get("base"), m.get("canvas"))


def _thumb_job(root: str, m: dict, kind: str, key: str, size: int,
               pref: dict | None) -> tuple[str, Callable, Callable | None]:
    """(서명, make, prepare) — 서명은 캐시 파일 이름과 URL 의 v. make([placed]) 는 JPEG bytes,
    prepare 는 후보 kind 의 배치(placed 캐시) — _cached 가 잠금 밖에서 먼저 부른다."""
    if kind in ("cand", "delta", "context"):
        if not _KEY_RE.fullmatch(key):
            raise _Reject(400, "후보 키 형식이 잘못되었습니다")
        cand = _cand(m, key)
        if cand is None:
            raise _Reject(404, f"후보가 없습니다: {key}")
        crop = _crop(m, cand["crop_id"])
        if crop is None:
            raise _Reject(404, f"후보의 크롭이 매니페스트에 없습니다: {cand['crop_id']}")
        ctx = _target_ctx(root, m, crop, cand["tid"], pref)
        entry, focus = ctx["entries"][key], ctx["focus"]
        sig = _cand_sig(root, kind, size, m, crop, cand, entry, focus)
        flags = _view_flags(root, m, kind)

        def prepare():
            return _placed(root, crop, cand, entry, *flags)

        if kind == "cand":
            return sig, lambda placed: _render_cand(root, crop, placed, focus, size), prepare
        if kind == "delta":
            return sig, lambda placed: _render_delta(root, crop, entry, placed, focus, size), prepare
        return sig, lambda placed: _render_context(root, m, crop, cand, entry, placed, size), prepare
    parts = key.split("/")
    if len(parts) != (2 if kind == "before" else 3) or not _CROP_ID_RE.fullmatch(parts[0]) \
            or not _TID_RE.fullmatch(parts[1]) or (kind == "ref" and not parts[2].isdigit()):
        raise _Reject(400, "key 형식이 잘못되었습니다(before: crop_id/tid, ref: crop_id/tid/번호)")
    crop = _crop(m, parts[0])
    if crop is None or parts[1] not in [t.get("tid") for t in crop.get("targets") or []]:
        raise _Reject(404, f"타깃이 없습니다: {parts[0]}/{parts[1]}")
    if kind == "before":
        focus = None if size == FULL_SIZE else _target_ctx(root, m, crop, parts[1], pref)["focus"]
        return _sig("before", size, crop["source"], crop["rect"], focus), \
            lambda: _render_before(root, crop, focus, size), None
    refs = _target_refs(m, crop["id"], parts[1])
    i = int(parts[2])
    if i >= len(refs):
        raise _Reject(404, f"레퍼런스 {i} 가 없습니다(이 타깃의 레퍼런스 {len(refs)}개)")
    return _sig("ref", size, refs[i]), lambda: _render_ref(root, refs[i], size), None


def render_thumb(root: str, kind: str, key: str, size: int = GRID_SIZE) -> tuple[bytes, str]:
    """썸네일 JPEG bytes 와 서명(캐시 키). derived/thumbs/<kind>_<서명>.jpg 가 있으면 그대로 읽는다."""
    if kind not in THUMB_KINDS:
        raise _Reject(400, f"kind 는 {', '.join(THUMB_KINDS)} 중 하나여야 합니다")
    if size not in THUMB_SIZES and not (size == FULL_SIZE and kind in _FULL_KINDS):
        raise _Reject(400, f"size 는 {', '.join(map(str, THUMB_SIZES))} 중 하나여야 합니다"
                           f"({', '.join(_FULL_KINDS)} 는 원해상도 {FULL_SIZE} 도)")
    m = store.load_manifest(root)
    sig, make, prepare = _thumb_job(root, m, kind, key, size, _PREF.get(store.root_key(root)))
    try:
        return _cached(root, f"{kind}_{sig}.jpg", make, prepare), sig
    except (OSError, ValueError) as e:  # 깨진 이미지·크기 불일치 등 — 보드에는 4xx 로
        raise _Reject(422, f"썸네일을 만들 수 없습니다({kind} {key}): {e}") from e


# ══════════════════════════════════════════════════════════════════════
# ★ 합성 미리보기·이음매 (Studio 캔버스 뷰어)
# ══════════════════════════════════════════════════════════════════════
@functools.lru_cache(maxsize=1)
def _node():
    """bmk_design_patch(Compose 의 _plan·_cand_pixels·_render·_paste) — 노드 모듈이 이 모듈을 import 하므로 처음 쓸 때
    읽는다. 합성 미리보기가 Compose 와 다른 계산을 하지 않게 노드 함수를 그대로 쓴다."""
    try:
        from . import bmk_design_patch as node
    except ImportError:
        import bmk_design_patch as node
    return node


def _preview_wh(w: int, h: int) -> tuple[int, int]:
    s = min(1.0, CANVAS_PREVIEW / max(w, h))
    return max(1, round(w * s)), max(1, round(h * s))


def _preview(img: np.ndarray) -> np.ndarray:
    h, w = img.shape[:2]
    pw, ph = _preview_wh(w, h)
    return img if (pw, ph) == (w, h) else cv2.resize(img, (pw, ph), interpolation=cv2.INTER_AREA)


def _base_sig(m: dict) -> str:
    return _sig("canvas_base", CANVAS_PREVIEW, m["base"], m["canvas"])


def _compose_settings(root: str, m: dict) -> dict:
    """★ 합성·context 가 쓰는 Compose 설정 = 마지막 Export 가 낸 PSD 의 compose(store.last_export — '변경 없음'으로 끝난 Export 포함,
    Export 와 같은 배치), 없으면 Compose 기본값."""
    comp = (store.last_export(root, m) or {}).get("compose")
    return dict(_COMPOSE_DEFAULTS, **comp) if isinstance(comp, dict) else dict(_COMPOSE_DEFAULTS)


def _composite_picks(m: dict, swap: tuple[str, str, str] | None = None) -> list[dict]:
    """Compose 가 합성하는 후보: ★·탈락 아님·크롭과 타깃이 지금 매니페스트에 있음(노드 _in_crop 과 같은 규칙).
    swap = (crop_id, tid, key): 그 타깃의 ★ 를 이 후보 하나로 바꾼 가정(보드 Space 와 같게 — ★ 하면 탈락도 풀림)."""
    crops = {c.get("id"): c for c in m["crops"]}
    rejects = m.get("rejects") or {}
    out = []
    for c in m["candidates"]:
        if swap is not None and (c.get("crop_id"), c.get("tid")) == swap[:2]:
            on = c["key"] == swap[2]
        else:
            on = c.get("picked") and c["key"] not in rejects
        if on and c.get("crop_id") in crops                 and any(t.get("tid") == c.get("tid") for t in crops[c["crop_id"]].get("targets") or []):
            out.append(c)
    return out


def _composite_sig(root: str, m: dict, pref: dict | None, swap: tuple[str, str, str] | None = None) -> str:
    """★ 합성이 읽는 것: 설정·베이스·★ 후보(크롭·타깃 = Compose 의 묶음, 파일·quad·손 마스크·z·크롭 rect·분석 항목)와
    S4 마스크 편집(_edit_part — 편집이 있는 ★ 만 덧붙여 편집 없는 프로젝트의 서명 그대로. _plan 이 ★ 에 적용하는 기록과 같음 — 형식이 깨진
    기록은 뺌, _edit_rec). 동률 기준인 크롭·타깃 목록 순서가 후보 첫 등장 순서와 다를 때만 덧붙인다."""
    crops = {c.get("id"): c for c in m["crops"]}
    parts = []
    adjustments = {}
    first = {}
    for c in _composite_picks(m, swap):
        tids = first.setdefault(c["crop_id"], [])
        if c["tid"] not in tids:
            tids.append(c["tid"])
        e = _entry_for(m, c["key"], pref)
        part = [c["key"], c["crop_id"], c["tid"], c["file"], c["quad"], c.get("hand_mask"), c.get("z"),
                crops[c["crop_id"]]["rect"], e.get("params_hash") if e else None]
        rec = _edit_rec(m, c["key"])
        parts.append(part + [_edit_part(root, rec)] if rec else part)
        adj = store.cand_adjust(m, c["key"])
        if adj:
            adjustments[c["key"]] = {k: adj[k] for k in ("blur", "invert")}
    manual = store.z_order(m)
    extra = [{"z_order": manual}] if manual else []
    if adjustments:
        extra.append({"cand_adjust": adjustments})
    tie_order = [[cid, [t["tid"] for t in crop["targets"] if t["tid"] in first[cid]]]
                 for cid, crop in crops.items() if cid in first]
    if tie_order != [[cid, tids] for cid, tids in first.items()]:
        extra.append({"tie_order": tie_order})
    return _sig("composite", CANVAS_PREVIEW, m.get("base"), m.get("canvas"), _compose_settings(root, m), parts,
                *extra)


def _runs(hit: np.ndarray) -> list[tuple[int, int]]:
    """True 구간 [(시작, 끝)] — _SEAM_GAP 이하 틈은 잇고 _SEAM_MIN_LEN 보다 짧은 구간은 버린다."""
    idx = np.flatnonzero(hit)
    if not len(idx):
        return []
    cuts = np.flatnonzero(np.diff(idx) > _SEAM_GAP + 1)
    starts = np.r_[idx[0], idx[cuts + 1]]
    ends = np.r_[idx[cuts], idx[-1]] + 1
    return [(int(a), int(b)) for a, b in zip(starts, ends) if b - a >= _SEAM_MIN_LEN]


def _edge_touches(rect: list[int], eff: np.ndarray, left: int, top: int, W: int, H: int) -> list[tuple[str, int, int]]:
    """[(변, 시작, 끝)] — rect(캔버스 안으로 자른 크롭 rect)의 변 중 바깥 이웃이 캔버스 안인 변에서, 안쪽 2줄 실효 알파
    최대 >= 0.5 인 구간(변 좌표). 캔버스 밖으로 나간 크롭은 자른 변이 캔버스 가장자리라 그 변은 보지 않는다."""
    x0, y0, x1, y1 = rect
    ix0, iy0 = max(x0, left), max(y0, top)
    ix1, iy1 = min(x1, left + eff.shape[1]), min(y1, top + eff.shape[0])
    if ix1 <= ix0 or iy1 <= iy0:
        return []
    win = np.zeros((y1 - y0, x1 - x0), np.float32)
    win[iy0 - y0:iy1 - y0, ix0 - x0:ix1 - x0] = eff[iy0 - top:iy1 - top, ix0 - left:ix1 - left]
    sides = (("top", y0 > 0, win[:2].max(axis=0)), ("bottom", y1 < H, win[-2:].max(axis=0)),
             ("left", x0 > 0, win[:, :2].max(axis=1)), ("right", x1 < W, win[:, -2:].max(axis=1)))
    return [(side, a, b) for side, inside, line in sides if inside for a, b in _runs(line >= 0.5)]


def _seam_segment(out: np.ndarray, base: np.ndarray, crop_id: str, rect: list[int], tid: str, key: str, side: str,
                  a: int, b: int) -> dict:
    """그 구간의 새 불연속(경계 1px 안·밖 차이가 베이스에서보다 얼마나 달라졌나, RGB 평균). rect = _edge_touches 와 같은
    캔버스 안 rect. 좌표 = 경계선(px 모서리)."""
    x0, y0, x1, y1 = rect
    if side in ("top", "bottom"):
        i, o, k = (y0, y0 - 1, y0) if side == "top" else (y1 - 1, y1, y1)
        inner, outer = np.s_[i, x0 + a:x0 + b], np.s_[o, x0 + a:x0 + b]
        line = [x0 + a, k, x0 + b, k]
    else:
        i, o, k = (x0, x0 - 1, x0) if side == "left" else (x1 - 1, x1, x1)
        inner, outer = np.s_[y0 + a:y0 + b, i], np.s_[y0 + a:y0 + b, o]
        line = [k, y0 + a, k, y0 + b]
    f = np.float32
    d = np.abs((out[inner].astype(f) - out[outer]) - (base[inner].astype(f) - base[outer])).mean(axis=1)
    p90 = float(np.percentile(d, 90))
    return {"crop_id": crop_id, "tid": tid, "key": key, "side": side, "x0": line[0], "y0": line[1], "x1": line[2],
            "y1": line[3], "length": b - a, "diff_med": round(float(np.median(d)), 1), "diff_p90": round(p90, 1),
            "severity": "bad" if p90 >= _SEAM_BAD else "weak"}


def _star_plan(root: str, m: dict, pref: dict | None,
               swap: tuple[str, str, str] | None = None) -> tuple[list[dict], list[dict], dict, dict, list[str]]:
    """Compose 의 합성 계획(노드 _plan — 마스크 편집 적용·z 순서 포함) → (plan, picks, entries, 설정, 경고). 분석은 하지 않는다
    (보드가 고르는 기존 항목만). swap = _composite_picks 의 ★ 교체 가정. ★ 합성·마스크 편집 라우트가 함께 쓴다."""
    cs = _compose_settings(root, m)
    picks = _composite_picks(m, swap)
    keys = {c["key"] for c in picks}
    # _plan 은 매니페스트의 picked·rejects 를 읽는다 — 가정한 ★ 집합으로 바꾼 사본을 준다(swap 이 없으면 지금과 같음)
    mv = dict(m, candidates=[dict(c, picked=c["key"] in keys) for c in m["candidates"]],
              rejects={k: v for k, v in (m.get("rejects") or {}).items() if k not in keys})
    entries = {c["key"]: e for c in picks if (e := _entry_for(m, c["key"], pref)) is not None}
    warns: list[str] = []
    plan = _node()._plan(root, mv, entries, cs["mask_source"], bool(cs["use_registration"]), cs["zorder"], False, warns)
    return plan, picks, entries, cs, warns


def _compose_full(root: str, m: dict, pref: dict | None, swap: tuple[str, str, str] | None = None,
                  box: list[int] | None = None) -> tuple[np.ndarray, dict]:
    """Compose 노드와 같은 계산(_plan → _cand_pixels → _render → _paste)으로 ★ 합성 uint8 과 요약(_star_plan).
    swap = _composite_picks 의 ★ 교체 가정, box = 그 창만 합성(뷰어 영역 — 이음매는 보지 않음)."""
    node = _node()
    plan, picks, entries, cs, warns = _star_plan(root, m, pref, swap)
    base = node._load_rgb(root, m["base"])
    H, W = base.shape[:2]
    bx0, by0, bx1, by1 = box or [0, 0, W, H]
    canvas = base[by0:by1, bx0:bx1].astype(np.float32)
    union = np.zeros(canvas.shape[:2], np.float32)
    touches = []
    for p, t, it in node._slices_in_order(plan):
        qx0, qy0, qx1, qy1 = dpsd.quad_bbox(it["quad"])
        if qx1 < bx0 - 2 or qy1 < by0 - 2 or qx0 > bx1 + 2 or qy0 > by1 + 2:
            continue  # 창 밖 패치는 읽지 않는다(겹치지 않으니 합성 결과 그대로)
        pix, _toned = node._cand_pixels(root, it, bool(cs["use_tone"]))
        rgb, _alpha, eff, left, top = node._render(pix, it["quad"], it["mask"])
        node._paste(canvas, union, rgb, eff, left - bx0, top - by0)
        x0, y0, x1, y1 = p["crop"]["rect"]
        rect = [max(0, x0), max(0, y0), min(W, x1), min(H, y1)]
        if box is None and rect[2] > rect[0] and rect[3] > rect[1]:
            touches += [(p["crop"]["id"], rect, t["tid"], it["cand"]["key"], *s)
                        for s in _edge_touches(rect, eff, left, top, W, H)]
    out = np.clip(np.rint(canvas), 0, 255).astype(np.uint8)
    segs = [s for s in (_seam_segment(out, base, *t) for t in touches) if s["diff_p90"] >= _SEAM_MIN_DIFF]
    segs.sort(key=lambda s: (s["severity"] != "bad", -s["diff_p90"]))
    return out, {"picks": len(picks), "unanalyzed": [c["key"] for c in picks if c["key"] not in entries],
                 "warnings": warns, "settings": cs, "segments": segs}


def _composite(root: str, m: dict, pref: dict | None, sig: str,
               swap: tuple[str, str, str] | None = None) -> tuple[bytes, dict]:
    """composite_<sig>.jpg + seams_<sig>.json — 둘 다 있으면 읽고, 없으면 한 번 계산해 둘 다 쓴다(JSON 먼저).
    swap = ★ 교체 가정(sig 는 그 가정의 서명 — /mask/alts. 그 후보를 실제로 ★ 하면 같은 서명이라 캐시를 그대로 쓴다)."""
    jpg = _thumb_path(root, f"composite_{sig}.jpg")
    js = _thumb_path(root, f"seams_{sig}.json")
    data, raw = _read_bytes(jpg), _read_bytes(js)
    if data is None or raw is None:
        with _COMPOSE_LOCK:
            data, raw = _read_bytes(jpg), _read_bytes(js)
            if data is None or raw is None:
                t0 = time.time()
                with _RENDER_SLOTS:
                    out, info = _compose_full(root, m, pref, swap)
                    data = _jpeg(Image.fromarray(_preview(out)))
                info.update(sig=sig, seconds=round(time.time() - t0, 2))
                raw = json.dumps(info, ensure_ascii=False).encode("utf-8")
                store._atomic_write_bytes(js, raw)
                store._atomic_write_bytes(jpg, data)
    return data, json.loads(raw)


def _region_rect(rect: list[int], W: int, H: int) -> list[int]:
    """크롭 rect 를 변마다 긴 변의 1/4(64–256px) 넓혀 캔버스 안으로 — 뷰어가 경계 이음매를 양쪽 다 원해상도로 보게."""
    x0, y0, x1, y1 = rect
    pad = int(min(_REGION_PAD_MAX, max(_REGION_PAD_MIN, 0.25 * max(x1 - x0, y1 - y0))))
    return [max(0, x0 - pad), max(0, y0 - pad), min(W, x1 + pad), min(H, y1 + pad)]


def _region_sig(csig: str, box: list[int]) -> str:
    return _sig("region", csig, box)


def render_region(root: str, crop_id: str, key: str | None = None, v: str | None = None) -> tuple[bytes, str]:
    """크롭 주변 영역(_region_rect)만 원해상도로 ★ 합성한 JPEG — 뷰어가 선택 크롭에 깐다. key 가 있으면 "이 후보를 ★ 하면"
    (그 타깃의 ★ 를 이 후보 하나로 바꾼 가정, Compose 와 같은 순서·마스크 — 같은 크롭·겹친 크롭의 다른 ★ 도 그대로).
    v 가 지금 서명(★·크롭 rect·가정)과 다르면 409."""
    if not isinstance(crop_id, str) or not _CROP_ID_RE.fullmatch(crop_id):
        raise _Reject(400, "crop 형식이 잘못되었습니다")
    m = store.load_manifest(root)
    W, H = _canvas_wh(m)
    crop = _crop(m, crop_id)
    if crop is None:
        raise _Reject(404, f"크롭이 없습니다: {crop_id}")
    box = _region_rect(crop["rect"], W, H)
    if box[2] <= box[0] or box[3] <= box[1]:
        raise _Reject(409, "크롭이 캔버스 밖에 있습니다")
    swap = None
    if key:
        if not _KEY_RE.fullmatch(key):
            raise _Reject(400, "후보 키 형식이 잘못되었습니다")
        cand = _cand(m, key)
        if cand is None or cand.get("crop_id") != crop_id:
            raise _Reject(404, f"이 크롭의 후보가 아닙니다: {key}")
        swap = (crop_id, cand["tid"], key)
    pref = _PREF.get(store.root_key(root))
    rsig = _region_sig(_composite_sig(root, m, pref, swap), box)
    if v not in (None, "", rsig):
        raise _Reject(409, "★ 나 크롭이 바뀌어 영역 서명이 달라졌습니다 — 새 상태로 다시 요청하세요")

    def make() -> bytes:
        return _jpeg(Image.fromarray(_compose_full(root, m, pref, swap, box)[0]), quality=95)

    try:
        return _cached(root, f"region_{rsig}.jpg", make), rsig
    except (OSError, ValueError) as e:
        raise _Reject(422, f"★ 합성 영역을 만들 수 없습니다: {e}") from e


def _composite_for(root: str, v: str | None) -> tuple[bytes, dict, str]:
    m = store.load_manifest(root)
    _canvas_wh(m)
    pref = _PREF.get(store.root_key(root))
    sig = _composite_sig(root, m, pref)
    if v not in (None, "", sig):
        raise _Reject(409, "★ 가 바뀌어 합성 서명이 달라졌습니다 — 새 상태로 다시 요청하세요")
    try:
        data, info = _composite(root, m, pref, sig)
    except (OSError, ValueError) as e:
        raise _Reject(422, f"★ 합성을 만들 수 없습니다: {e}") from e
    return data, info, sig


def render_canvas(root: str, kind: str, v: str | None = None) -> tuple[bytes, str]:
    """캔버스 미리보기 JPEG 와 서명. kind = base | composite(v 가 지금 서명과 다르면 409)."""
    if kind == "composite":
        data, _info, sig = _composite_for(root, v)
        return data, sig
    if kind != "base":
        raise _Reject(400, "kind 는 base, composite, region(crop=[&key=]) 중 하나여야 합니다")
    m = store.load_manifest(root)
    _canvas_wh(m)
    sig = _base_sig(m)
    try:
        return _cached(root, f"canvasbase_{sig}.jpg",
                       lambda: _jpeg(Image.fromarray(_preview(_read_rgb(_open_rel(root, m["base"])))))), sig
    except (OSError, ValueError) as e:
        raise _Reject(422, f"베이스 미리보기를 만들 수 없습니다: {e}") from e


def seams_state(root: str, v: str | None = None) -> dict:
    """★ 합성의 이음매 요약(composite 와 같은 계산·캐시)."""
    return _composite_for(root, v)[1]


# ══════════════════════════════════════════════════════════════════════
# 마스크 편집 (Studio S4 — _proto/STUDIO_S4_SPEC.md §4·§6.2, 응답 모양 정본 tools/fixtures/mask_api.json)
# 기록·검사·쓰기는 dmask. 대상·편집 창·기준·z·3층은 Compose 계획(_star_plan → 노드 _plan) 그대로 — 편집 창 = Export 그룹 마스크 창.
# ══════════════════════════════════════════════════════════════════════
def _mask_cand(m: dict, key) -> dict:
    """마스크 라우트의 대상: key 형식 400 · 후보 없음 404 · 지금 ★ 아님 409 not_picked(dmask.star_cand = _composite_picks 의 규칙)."""
    if not isinstance(key, str) or not _KEY_RE.fullmatch(key):
        raise _Reject(400, "key 형식이 잘못되었습니다")
    cand = _cand(m, key)
    if cand is None:
        raise _Reject(404, f"후보가 없습니다: {key}")
    if dmask.star_cand(m, key) is None:
        raise _Reject(409, _NOT_PICKED, reason="not_picked")
    return cand


def _seen(v) -> str:
    """마스크 편집 seen = 지금 기록 file(dmask 기록의 file 꼴) 또는 ""(기록 없음) — 아니면 400."""
    if not isinstance(v, str) or not (v == "" or dmask._FILE_RE.fullmatch(v)):
        raise _Reject(400, "seen 형식이 잘못되었습니다")
    return v


def _mask_target(root: str, m: dict, cand: dict, pref: dict | None) -> dict:
    """★ cand 의 계획 항목(Compose 순서 order[i])·편집 창(실효 quad bbox ∩ 캔버스)·기준 지문·기준 래스터 서명."""
    W, H = _canvas_wh(m)
    try:
        plan, _picks, _entries, cs, warns = _star_plan(root, m, pref)
    except (OSError, ValueError) as e:  # 다른 ★ 의 마스크 파일 문제도 여기서 — 404(없는 후보처럼)·날 errno 대신 ★ 합성과 같은 422
        raise _Reject(422, f"편집 대상을 계산할 수 없습니다: {e}") from e
    order = list(_node()._slices_in_order(plan))
    i = next(j for j, (_p, _t, it) in enumerate(order) if it["cand"]["key"] == cand["key"])
    it = order[i][2]
    rect = dmask.edit_rect(dpsd.quad_bbox(it["quad"]), (W, H))
    if rect is None:
        raise _Reject(422, "이 ★ 의 패치가 캔버스 밖에 있어 편집할 창이 없습니다")
    w, h = rect[2] - rect[0], rect[3] - rect[1]
    if w * h > dmask.MAX_PIXELS:  # 저장(validate_png)이 늘 거절할 창 — 칠하기 전에 알린다
        raise _Reject(422, f"편집 창 {w}x{h} 가 4096² 픽셀을 넘어 Studio 에서 편집할 수 없습니다 — PS 그룹 마스크로")
    fp = dmask.base_fp(cand, it["entry"], cs["mask_source"])
    bm = it["base_mask"]
    return {"order": order, "i": i, "it": it, "cs": cs, "warns": warns, "wh": (W, H), "rect": rect, "fp": fp,
            "base_sig": _sig("maskbase", rect, fp, None if bm is None else [bm["left"], bm["top"], bm["bg"]])}


def _window_l(mask: dict | None, rect: list[int]) -> np.ndarray:
    """노드 마스크 dict(캔버스 좌표)를 편집 창으로(L uint8, 마스크 없음 = 전부 255) — /mask/img base·edit."""
    x0, y0, x1, y1 = rect
    reg = _mask_region(mask, x0, y0, x1, y1)
    return np.full((y1 - y0, x1 - x0), 255, np.uint8) if reg is None else np.rint(reg * 255.0).astype(np.uint8)


def _edit_window(m: dict, cand: dict, entry: dict | None, use_registration: bool) -> list[int] | None:
    """노드 _plan 항목의 편집 창(실효 quad — _eff_quad·원근 근사 — bbox ∩ 캔버스)을 노드·마스크를 읽지 않고: state·/mask/edit 의 stale·
    edit url 용. GET /mask·save 는 계획 항목에서(_mask_target — 같은 값, mask_board_unit 이 대조)."""
    if not m.get("base") or not m.get("canvas"):
        return None
    reg = use_registration and _usable(entry) and entry["reg"].get("applied")
    quad = _affine_quad([float(v) for v in (entry["quad_reg"] if reg else cand["quad"])], cand.get("src_size"))
    return dmask.edit_rect(dpsd.quad_bbox(quad), [int(v) for v in m["canvas"]])


def _layers_box(crop_rect: list[int], win: list[int] | None, W: int, H: int) -> list[int]:
    """3층 영역 = 크롭 영역(_region_rect) ∪ 편집 창(캔버스 안) — 패치가 크롭 + 패드를 넘는 ★(수확·옮긴 SO·옛 크롭 ★)도 붓·페이드·페더가
    닿는 창 전체가 미리보기에 그려지게(D2). win None = 패치가 캔버스 밖(GET /mask 는 422)."""
    box = _region_rect(crop_rect, W, H)
    if win is None:
        return box
    return [min(box[0], win[0]), min(box[1], win[1]), max(box[2], win[2]), max(box[3], win[3])]


_LAYER_ORDER_MEMO: dict[tuple, tuple[float, list]] = {}  # 영역 안 순서 ID 만, 래스터·계획은 보관하지 않음(최대 16개·5초)
_LAYER_ORDER_LOCK = threading.Lock()                    # 같은 판의 3층 요청이 동시에 와도 순서 계산은 한 번


def _layers_sig(root: str, m: dict, pref: dict | None, key: str, box: list[int],
                order: list[tuple] | None = None, *, memo_order: bool = False) -> str:
    """3층 서명 = box·후보 + 3층이 읽는 ★ 만의 합성 서명(이 후보의 편집은 뺌 — 3층은 자기 마스크를 쓰지 않고 z 도 기준 넓이라(D5) [적용]이
    다시 계산시키지 않는다). 읽는 ★ = 편집 창이 box ±2(_mask_layers 가 건너뛰지 않는 범위)와 만나는 ★ 가 있는 크롭의 ★ 전부 — z 는 크롭·타깃
    넓이 합으로 정해져 같은 크롭의 떨어진 ★ 도 위아래를 바꾼다. 수동 순서가 있으면 전체 계획에서 near 의 실효 순서도 넣는다 —
    영역 밖 크롭의 자동 넓이가 수동 슬롯의 자리를 바꿀 수 있다. 수동 순서가 없으면 기존 서명을 유지한다.
    order 는 _mask_target 에서 이미 계산한 순서. /mask/img 는 memo_order 로 같은 rev·설정의 영역 안 순서를 잠깐 재사용한다."""
    cs = _compose_settings(root, m)
    use_reg = bool(cs["use_registration"])
    bx0, by0, bx1, by1 = box
    near = set()
    for c in _composite_picks(m):
        r = _edit_window(m, c, _entry_for(m, c["key"], pref), use_reg)
        if r is not None and r[2] >= bx0 - 2 and r[3] >= by0 - 2 and r[0] <= bx1 + 2 and r[1] <= by1 + 2:
            near.add(c["crop_id"])
    mv = dict(m, candidates=[c for c in m["candidates"] if c.get("crop_id") in near],
              mask_edits={k: v for k, v in (m.get("mask_edits") or {}).items() if k != key})
    order_args = []
    if store.z_order(m):
        def effective_order():
            items = order if order is not None else _node()._slices_in_order(_star_plan(root, m, pref)[0])
            return [[p["crop"]["id"], t["tid"], it["cand"]["key"]] for p, t, it in items if p["crop"]["id"] in near]

        if order is None and memo_order:
            memo_key = (store.root_key(root), m["rev"], store.board_pref_v(root), _sig(cs, pref), tuple(sorted(near)))
            with _LAYER_ORDER_LOCK:
                cached = _LAYER_ORDER_MEMO.get(memo_key)
                if cached is None or time.monotonic() - cached[0] >= 5:
                    effective = effective_order()
                    _LAYER_ORDER_MEMO.pop(memo_key, None)
                    while len(_LAYER_ORDER_MEMO) >= 16:
                        _LAYER_ORDER_MEMO.pop(next(iter(_LAYER_ORDER_MEMO)))
                    _LAYER_ORDER_MEMO[memo_key] = (time.monotonic(), effective)
                else:
                    effective = cached[1]
        else:
            effective = effective_order()
        order_args.append(effective)
    return _sig("masklayers", _composite_sig(root, mv, pref), box, key, *order_args)


def _murl(rk: str, key: str, kind: str, v: str) -> str:
    return "mask/img?" + urlencode({"root": rk, "key": key, "kind": kind, "v": v})


def _edit_sig(rec: dict, rect: list[int] | None) -> str:
    """/mask/img kind=edit 의 v — 기록 래스터(file 내용 주소·기록 창)를 지금 편집 창 rect 로 옮긴 PNG 의 서명."""
    return _sig("maskedit", rec["file"], rec["rect"], rect)


def _edit_stale(rec: dict, fp: dict, rect: list[int] | None) -> bool:
    """편집 기준 바뀜 = 기준 지문(dmask.is_stale) 또는 편집 창이 기록 창과 다름(정합·Compose use_registration·quad·캔버스가 바뀜 — 노드는
    기록 창 그대로 적용하므로 패치와 어긋났을 수 있음)."""
    return dmask.is_stale(rec, fp) or rec["rect"] != rect


def _edit_rec(m: dict, key: str) -> dict | None:
    """보드가 읽는 마스크 편집 기록 = dmask.check_record 를 통과한 것만. 형식이 깨진 기록(손으로 고친 매니페스트 — save_edit 은 쓰지 않음)은
    _plan 이 경고하고 기준을 쓰므로(dmask.edit_mask) 보드도 편집 없음으로 본다(서명·state·GET /mask — 날 KeyError 500 대신)."""
    rec = dmask.edit_record(m, key)
    if rec is None:
        return None
    try:
        return dmask.check_record(rec)
    except ValueError:
        return None


def _edit_part(root: str, rec: dict) -> list:
    """서명에 넣는 편집 재료 [file, 기록 창] — _plan 이 그 래스터를 쓰지 못하면(없음·깨짐·크기 다름 — _edit_usable) 표시를 덧붙여 기준으로 그린
    ★ 합성·이음매·context 캐시와 편집으로 그린 캐시가 갈리게(파일이 돌아오면 원래 서명 — 그때 캐시가 맞다)."""
    part = [rec["file"], rec["rect"]]
    return part if _edit_usable(root, rec) else part + ["unusable"]


def _edit_usable(root: str, rec: dict) -> bool:
    """_plan 이 이 기록의 래스터를 쓰나 — dmask.edit_mask 와 같은 판정(파일 없음·깨진 PNG·크기 ≠ 기록 창이면 기준). state 의 stale 용이라
    디코드는 파일(mtime·크기)·기록 창이 바뀔 때만. 잠김·권한(errno)은 _plan 이 기준으로 떨어뜨리지 않고 실패하므로 쓰는 것으로 본다."""
    path = store.resolve_path(root, rec["file"])
    try:
        s = os.stat(path)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    memo_key = (path, s.st_mtime_ns, s.st_size, tuple(rec["rect"]))
    ok = _EDIT_OK.get(memo_key)
    if ok is None:
        try:
            ok = dmask.edit_mask(root, rec)[0] is not None
        except OSError:
            return True
        if len(_EDIT_OK) > 2048:
            _EDIT_OK.clear()
        _EDIT_OK[memo_key] = ok
    return ok


def _edit_view(rk: str, key: str, rec: dict | None, fp: dict, rect: list[int] | None) -> dict | None:
    """GET /mask·save·edit 응답의 edit — 기록이 없으면 None. rect = 지금 편집 창, fp = 지금 기준 지문."""
    if rec is None:
        return None
    return {"file": rec["file"], "at": rec["at"], "ops": rec["ops"], "hist": len(rec["hist"]),
            "stale": _edit_stale(rec, fp, rect), "url": _murl(rk, key, "edit", _edit_sig(rec, rect))}


def mask_view(root: str, key) -> dict:
    """GET /mask — 편집 대상·기준·편집·3층 URL·이 크롭 이음매(지금 ★ 합성 캐시가 있을 때만, 없으면 null)·z(layers.box 와 겹치는 다른 ★,
    합성 순서 아래 → 위)·경고(이 후보의 _plan 경고 + 분석 없음). 파일을 쓰지 않는다. 편집 파일을 읽지 못하면(_plan 이 경고하고 기준을 씀)
    edit.url = 기준 url — Studio 가 _plan 이 쓰는 마스크로 열고 [적용]·[편집 지우기]로 나갈 수 있게. 형식이 깨진 기록은 edit null(_edit_rec)."""
    m = store.load_manifest(root)
    rk = store.root_key(root)
    pref = _PREF.get(rk)
    cand = _mask_cand(m, key)
    t = _mask_target(root, m, cand, pref)
    crop = t["order"][t["i"]][0]["crop"]
    box = _layers_box(crop["rect"], t["rect"], *t["wh"])
    csig, lsig = _composite_sig(root, m, pref), _layers_sig(root, m, pref, key, box, order=t["order"])
    z = {"below": [], "above": []}
    for j, (p, tg, it) in enumerate(t["order"]):
        qx0, qy0, qx1, qy1 = dpsd.quad_bbox(it["quad"])
        if j != t["i"] and qx0 < box[2] and qx1 > box[0] and qy0 < box[3] and qy1 > box[1]:
            z["below" if j < t["i"] else "above"].append(f"{p['crop']['name']} · {tg['tid']}")
    scopes, boxes = _zorder_scopes(t["order"])
    z["rev"] = m["rev"]
    z["controls"] = [_zorder_control(m, scopes, boxes, scope, item) for scope, item in (
        ("crops", crop["id"]), (f"t:{crop['id']}", cand["tid"]), (f"s:{crop['id']}/{cand['tid']}", key))]
    raw = _read_bytes(_thumb_path(root, f"seams_{csig}.json"))
    warns = [w for w in t["warns"] if w.startswith(f"{key} (")]
    if t["it"]["entry"] is None:
        warns.insert(0, "분석 없음 — 반영(Analyze) 뒤 편집 권장")
    base = {"src": t["it"]["base_msrc"], "sig": t["base_sig"], "url": _murl(rk, key, "base", t["base_sig"])}
    edit = _edit_view(rk, key, _edit_rec(m, key), t["fp"], t["rect"])
    if edit is not None and t["it"]["mask_edit"] is None:
        edit["url"] = base["url"]
    return {"key": key, "crop_id": crop["id"], "tid": cand["tid"],
            "c": next(n for n, c in enumerate(_target_members(m, crop["id"], cand["tid"]), 1) if c["key"] == key),
            "picked": True, "rect": t["rect"], "composite_sig": csig, "mask_source": t["cs"]["mask_source"],
            "base": base, "edit": edit, "adjust": store.cand_adjust(m, key),
            "layers": {"box": box, "sig": lsig, **{f"{k}_url": _murl(rk, key, k, lsig) for k in _LAYER_KINDS}},
            "seams": None if raw is None else [s for s in json.loads(raw)["segments"] if s["crop_id"] == crop["id"]],
            "z": z, "warnings": warns}


def _mask_layers(root: str, m: dict, pref: dict | None, key: str, box: list[int]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """브러시 미리보기 3층(§2.4, box 창): under = 베이스 + 이 ★ 보다 아래 ★ 합성(RGB), patch = 이 후보 RGB + SO 캐시 알파(마스크 없음),
    over = 위 ★ 들을 투명 위에 차례로(색 = 프리멀티 ÷ a, a = 1 − Π(1 − a)). Compose 와 같은 계획·렌더라
    over.rgb·over.a + (under + (patch.rgb − under)·patch.a·mask)·(1 − over.a) = 그 마스크로 합성한 ★ 합성(8비트 반올림 차)."""
    node = _node()
    plan, _picks, _entries, cs, _warns = _star_plan(root, m, pref)
    bx0, by0, bx1, by1 = box
    under = node._load_rgb(root, m["base"])[by0:by1, bx0:bx1].astype(np.float32)
    prem = np.zeros_like(under)
    keep = np.ones(under.shape[:2], np.float32)   # 위 ★ 들의 Π(1 − a)
    union = np.zeros(under.shape[:2], np.float32)
    patch = np.zeros((*under.shape[:2], 4), np.uint8)
    above = False
    for _p, _t, it in node._slices_in_order(plan):
        me = it["cand"]["key"] == key
        qx0, qy0, qx1, qy1 = dpsd.quad_bbox(it["quad"])
        if not me and (qx1 < bx0 - 2 or qy1 < by0 - 2 or qx0 > bx1 + 2 or qy0 > by1 + 2):
            continue  # _compose_full(box) 와 같이 창 밖 패치는 읽지 않는다
        pix, _toned = node._cand_pixels(root, it, bool(cs["use_tone"]))
        rgb, _alpha, eff, left, top = node._render(pix, it["quad"], None if me else it["mask"])
        x0, y0, x1, y1 = max(bx0, left), max(by0, top), min(bx1, left + eff.shape[1]), min(by1, top + eff.shape[0])
        win, src = np.s_[y0 - by0:y1 - by0, x0 - bx0:x1 - bx0], np.s_[y0 - top:y1 - top, x0 - left:x1 - left]
        if me:
            above = True
            if x1 > x0 and y1 > y0:
                patch[win] = np.dstack([rgb[src], np.rint(eff[src] * 255.0).astype(np.uint8)])
        elif not above:
            node._paste(under, union, rgb, eff, left - bx0, top - by0)
        else:
            node._paste(prem, union, rgb, eff, left - bx0, top - by0)
            if x1 > x0 and y1 > y0:
                keep[win] *= 1.0 - eff[src]
    a = 1.0 - keep
    color = np.divide(prem, a[..., None], out=np.zeros_like(prem), where=a[..., None] > 0)
    over = np.dstack([np.clip(np.rint(color), 0, 255), np.rint(a * 255.0)]).astype(np.uint8)
    return np.clip(np.rint(under), 0, 255).astype(np.uint8), patch, over


def _layer_png(root: str, m: dict, pref: dict | None, key: str, box: list[int], sig: str, kind: str) -> bytes:
    """3층 PNG(compress 1 — under 도 무손실이어야 공식이 Compose 와 1 이내) — 셋을 한 번에 계산해 derived/thumbs/mask<kind>_<sig>.png
    로 캐시. ★ 합성과 같은 _COMPOSE_LOCK(풀해상도 베이스·패치 렌더는 한 번에 하나)."""
    paths = {k: _thumb_path(root, f"mask{k}_{sig}.png") for k in _LAYER_KINDS}
    data = _read_bytes(paths[kind])
    if data is None:
        with _COMPOSE_LOCK:
            data = _read_bytes(paths[kind])
            if data is None:
                with _RENDER_SLOTS:
                    out = dict(zip(_LAYER_KINDS, (_png(a) for a in _mask_layers(root, m, pref, key, box))))
                for k in _LAYER_KINDS:
                    store._atomic_write_bytes(paths[k], out[k])
                data = out[kind]
    return data


def mask_image(root: str, key, kind, v) -> tuple[bytes, str]:
    """GET /mask/img → (PNG, 서명). v ≠ 지금 서명이면 409 stale(낡은 요청에 계산하지 않음). base = 기준 마스크(편집 창 L), edit = _plan 이
    쓰는 편집 래스터(기록 창의 캔버스 자리)를 지금 편집 창으로 옮긴 L(v = _edit_sig — 창이 그대로면 기록 파일과 같은 픽셀. 정합·
    use_registration 이 바뀌어 창이 달라져도 미리보기 = Compose·PSD), under·patch·over = 3층(v = layers.sig — 계획 없이 서명만 보고, 없을 때만 계산)."""
    if kind not in _MASK_KINDS:
        raise _Reject(400, f"kind 는 {' · '.join(_MASK_KINDS)} 중 하나여야 합니다")
    if not isinstance(v, str) or not _MASK_V_RE.fullmatch(v):
        raise _Reject(400, "v 형식이 잘못되었습니다")
    m = store.load_manifest(root)
    pref = _PREF.get(store.root_key(root))
    cand = _mask_cand(m, key)

    def fresh(sig: str) -> str:
        if v != sig:
            raise _Reject(409, "★ 합성·마스크가 그 사이 바뀌었습니다 — 다시 받기", reason="stale")
        return sig

    if kind == "edit":
        rec = _edit_rec(m, key)
        if rec is None:
            raise _Reject(404, "마스크 편집이 없습니다")
        t = _mask_target(root, m, cand, pref)
        sig = fresh(_edit_sig(rec, t["rect"]))
        if t["it"]["mask_edit"] is None:  # _plan 이 편집을 읽지 못해 기준을 씀(GET /mask 의 edit.url 은 그때 기준 url)
            raise _Reject(404, f"마스크 편집 파일을 읽지 못했습니다: {rec['file']}")
        return _png(_window_l(t["it"]["mask"], t["rect"])), sig
    if kind == "base":
        t = _mask_target(root, m, cand, pref)
        sig = fresh(t["base_sig"])
        return _cached(root, f"maskbase_{sig}.png", lambda: _png(_window_l(t["it"]["base_mask"], t["rect"]))), sig
    W, H = _canvas_wh(m)
    win = _edit_window(m, cand, _entry_for(m, key, pref), bool(_compose_settings(root, m)["use_registration"]))
    box = _layers_box(_crop(m, cand["crop_id"])["rect"], win, W, H)
    sig = fresh(_layers_sig(root, m, pref, key, box, memo_order=True))
    try:
        return _layer_png(root, m, pref, key, box, sig, kind), sig
    except (OSError, ValueError) as e:
        raise _Reject(422, f"브러시 미리보기 층을 만들 수 없습니다: {e}") from e


def mask_save(root: str, key, data: bytes, seen, ops, rect) -> dict:
    """POST /mask/save — 판정 순서(mask_api.json '_doc 열거 save 판정 순서'): key 400·404 → ★ 아님 409 not_picked → 본문·rect 형식 400
    (dmask.validate_png, rect 없음도) → PNG 크기 ≠ 지금 편집 창 또는 rect(Studio 가 세션을 연 창 "x0,y0,x1,y1") ≠ 지금 창 409 rect_changed {rect}
    (같은 크기로 옮겨진 창 — 순수 이동 정합·use_registration — 은 rect 로만 잡힌다. 그래서 rect 는 필수) →
    seen·ops 400 → seen ≠ 지금 기록 409 changed(dmask.save_edit 이 잠금 안에서). 편집 창·기준 지문은 지금 계획에서."""
    m = store.load_manifest(root)
    rk = store.root_key(root)
    pref = _PREF.get(rk)
    cand = _mask_cand(m, key)
    arr = dmask.validate_png(data)
    win = _int_list(rect, 4, "rect")
    t = _mask_target(root, m, cand, pref)
    x0, y0, x1, y1 = t["rect"]
    if arr.shape != (y1 - y0, x1 - x0) or win != t["rect"]:
        raise _Reject(409, "편집 창이 바뀌었습니다(정합·배치 변경) — 다시 받기", reason="rect_changed", rect=t["rect"])

    def placed(mm):  # 잠근 매니페스트로 창을 다시(_mask_target 뒤 잠그기 전에 Analyze 가 정합을 바꿨으면 409 rect_changed — 옛 좌표로 저장 안 함)
        return _edit_window(mm, _cand(mm, key), _entry_for(mm, key, _PREF.get(rk)), bool(_compose_settings(root, mm)["use_registration"]))

    rec, rev = dmask.save_edit(root, key, data, seen=_seen(seen), rect=t["rect"], base=t["fp"], ops=ops, placed=placed)
    return {"ok": True, "edit": _edit_view(rk, key, rec, t["fp"], t["rect"]), "rev": rev,
            "composite_sig": _composite_sig(root, store.load_manifest(root), pref)}


_MASK_CLEAN_AGE = 600  # 초 — 막 저장 중인 편집 PNG(save_edit 은 파일을 쓴 뒤 매니페스트에 기록)를 지우지 않게 이보다 새 파일은 남김


_MASK_EDIT_FILE_RE = re.compile(r"[A-Za-z0-9_\-]{0,10}_e_[0-9a-f]{12}\.png")  # 편집기 파일(dmask.save_edit 의 이름) — 손 마스크(_hand_) 등은 정리하지 않음


def _mask_refs(v, out: set) -> set:
    """매니페스트 안 어디든 'masks/…' 를 가리키는 글(편집 기록·이전 버전·후보 hand_mask.file 등 — Codex 3a 1차)."""
    if isinstance(v, str):
        if v.startswith(dmask.MASKS_DIR + "/"):
            out.add(v)
    elif isinstance(v, dict):
        for x in v.values():
            _mask_refs(x, out)
    elif isinstance(v, list):
        for x in v:
            _mask_refs(x, out)
    return out


def _mask_unused(root: str, m: dict, now: float) -> tuple[list[dict], int]:
    """masks/ 의 편집기 PNG 중 매니페스트 어디서도 가리키지 않는 것 [{file, bytes, at}](이름순)과 너무 새로워 뺀 수."""
    used = _mask_refs(m, set())
    d = os.path.join(root, dmask.MASKS_DIR)
    real_root = os.path.realpath(root)
    if not os.path.isdir(d) or os.path.commonpath([real_root, os.path.realpath(d)]) != real_root:
        return [], 0  # 없거나 프로젝트 밖을 가리키는 링크(심볼릭·junction — Codex 3a 2차)
    out, recent = [], 0
    for name in sorted(os.listdir(d)):
        rel = f"{dmask.MASKS_DIR}/{name}"
        p = os.path.join(d, name)
        if not _MASK_EDIT_FILE_RE.fullmatch(name) or rel in used or not os.path.isfile(p):
            continue
        st = os.stat(p)
        if now - st.st_mtime < _MASK_CLEAN_AGE:
            recent += 1
            continue
        out.append({"file": rel, "bytes": st.st_size, "at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime))})
    return out, recent


def mask_unused(root: str) -> dict:
    """GET /mask/unused(배치 3-6) — 지우지 않는 읽기."""
    files, recent = _mask_unused(root, store.load_manifest(root), time.time())
    return {"files": files, "bytes": sum(f["bytes"] for f in files), "recent": recent}


def mask_cleanup(root: str, data: dict) -> dict:
    """POST /mask/cleanup(배치 3-6): 보낸 목록 중 지금도 안 쓰는 파일만 지운다 — 매니페스트 잠금 안에서 다시 판정(그 사이 [이전 버전으로]·
    다른 창 저장이 다시 쓰게 된 파일은 남김 → kept)."""
    files = data.get("files")
    if not isinstance(files, list) or not files or len(files) > 500 or not all(isinstance(f, str) and dmask._FILE_RE.fullmatch(f) for f in files):
        raise _Reject(400, "files 는 masks/<이름>.png 목록이어야 합니다")
    with store.manifest_lock(root):
        unused = {f["file"]: f for f in _mask_unused(root, store.load_manifest(root), time.time())[0]}
        deleted, kept = [], []
        for f in dict.fromkeys(files):
            if f in unused:
                os.remove(os.path.join(root, *f.split("/")))  # _mask_unused 가 masks/ 실제 경로를 프로젝트 안으로 확인함
                deleted.append(f)
            else:
                kept.append(f)
    if deleted:
        logger.info(f"{_TAG} Board: masks/ 안 쓰는 편집 파일 {len(deleted)}개 지움 {os.path.basename(root)}")
    return {"ok": True, "deleted": len(deleted), "bytes": sum(unused[f]["bytes"] for f in deleted), "kept": kept}


def _ps_export_paths(path, original=None, *, require_psd=True) -> tuple[str, str]:
    """output 안의 PSD·원래 Export의 사이드카만. junction·심볼릭 링크도 실제 경로로 검사."""
    if not _OUTPUT_DIR:
        raise _Reject(409, "ComfyUI 출력 폴더를 알 수 없습니다 — ComfyUI 재시작 필요")
    if not isinstance(path, str) or not path or os.path.splitext(path)[1].lower() != ".psd":
        raise _Reject(400, "Export PSD 경로가 필요합니다")
    base = os.path.realpath(_OUTPUT_DIR[0])
    psd = os.path.abspath(path)
    side = os.path.splitext(os.path.abspath(original) if original else psd)[0] + ".json"
    for p in (psd, side):
        try:
            inside = os.path.normcase(os.path.commonpath([base, os.path.realpath(p)])) == os.path.normcase(base)
        except ValueError:
            inside = False
        if not inside:
            raise _Reject(400, "PSD·사이드카는 ComfyUI output 안이어야 합니다")
        if (p == side or require_psd) and not os.path.isfile(p):
            raise _Reject(404, "Export PSD 또는 옆 사이드카가 없습니다")
    return psd, side


def _ps_export_match(path, exports: list[dict]) -> tuple[str, str, str, dict]:
    """같은 폴더의 가장 긴 Export stem 접두사로 연결. 더 긴 기록의 사이드카가 없다고 짧은 것에 붙이지 않는다."""
    if not isinstance(path, str) or not path or os.path.splitext(path)[1].lower() != ".psd":
        raise _Reject(400, "Export PSD 경로가 필요합니다")
    psd = os.path.abspath(path)
    folder = os.path.normcase(os.path.dirname(psd))
    stem = os.path.splitext(os.path.basename(psd))[0].casefold()
    matches = []
    for index, rec in enumerate(exports):
        origin = rec.get("psd") if isinstance(rec, dict) else None
        if not isinstance(origin, str) or not origin or os.path.splitext(origin)[1].lower() != ".psd":
            continue
        origin = os.path.abspath(origin)
        prefix = os.path.splitext(os.path.basename(origin))[0].casefold()
        if (os.path.normcase(os.path.dirname(origin)) == folder and stem.startswith(prefix)
                and (len(stem) > len(prefix) or os.path.normcase(psd) == os.path.normcase(origin))):
            matches.append((len(prefix), index, origin, rec))
    if not matches:
        raise _Reject(400, "이 프로젝트 Export PSD와 같은 폴더·원래 이름 접두사인 파일만 가져올 수 있습니다")
    _length, _index, original, rec = max(matches, key=lambda x: x[:2])
    if os.path.normcase(psd) != os.path.normcase(original):
        suffix = stem[_length:]
        if suffix[0].isdigit() or re.search(r"^_\d+(?=\D|$)|_\d+$", suffix) or os.path.lexists(os.path.splitext(psd)[0] + ".json"):
            raise _Reject(400, "다른 Export 자동 이름 또는 자체 사이드카가 있는 PSD는 PS 사본으로 가져올 수 없습니다")
    psd, side = _ps_export_paths(psd, original)
    if (os.path.normcase(psd) != os.path.normcase(original)
            and os.path.normcase(os.path.dirname(os.path.realpath(psd))) != os.path.normcase(os.path.realpath(os.path.dirname(original)))):
        raise _Reject(400, "PS 사본은 원래 Export PSD와 같은 폴더 안이어야 합니다(다른 폴더 링크 거절)")
    return psd, side, original, rec


def mask_ps_list(root: str) -> list[dict]:
    if not _OUTPUT_DIR:
        raise _Reject(409, "ComfyUI 출력 폴더를 알 수 없습니다 — ComfyUI 재시작 필요")
    exports = store.load_manifest(root).get("exports") or []
    folders = {}
    for rec in exports:
        try:
            original, _side = _ps_export_paths(rec.get("psd"), require_psd=False)
            folder = os.path.dirname(original)
            folders.setdefault(os.path.normcase(folder), folder)
        except (_Reject, OSError, ValueError, AttributeError):
            continue
    groups = {}
    for folder in sorted(folders.values()):
        try:
            with os.scandir(folder) as entries:
                paths = [e.path for e in entries if os.path.splitext(e.name)[1].lower() == ".psd"]
        except OSError:
            continue
        for path in paths:
            try:
                psd, _side, original, rec = _ps_export_match(path, exports)
                stat = os.stat(psd)
                at = time.mktime(time.strptime(rec["time"], "%Y-%m-%d %H:%M:%S"))
                copy_of = original if os.path.normcase(psd) != os.path.normcase(original) else None
                row = {"psd": psd, "rev": rec.get("rev"), "time": rec["time"], "exported_at": at, "copy_of": copy_of,
                       "modified": int(stat.st_mtime) > int(at), "bytes": stat.st_size}
                groups.setdefault(os.path.normcase(original), []).append((row, stat.st_mtime))
            except (_Reject, OSError, ValueError, KeyError, TypeError):
                continue
    ordered = sorted(groups.values(), key=lambda g: (g[0][0]["exported_at"], g[0][0]["rev"] or 0), reverse=True)
    # Export는 최근 것부터, 그 원본 바로 아래에는 사본을 수정 시각 역순으로 둔다.
    return [row for group in ordered for row, _mtime in sorted(
        group, key=lambda pair: (pair[0]["copy_of"] is not None, -pair[1], pair[0]["psd"].casefold()))]


def mask_ps_import(root: str, data: dict) -> dict:
    m = store.load_manifest(root)
    psd, side, _original, export = _ps_export_match(data.get("psd"), m.get("exports") or [])
    dry = data.get("dry_run")
    if not isinstance(dry, bool):
        raise _Reject(400, "dry_run 은 true 또는 false 여야 합니다")

    def source():
        paths = _ps_export_match(psd, m.get("exports") or [])[:2]
        # ns는 JS 안전 정수 범위를 넘는다. 브라우저 JSON 왕복에서도 같은 불투명 문자열로 대조한다.
        return {p: f"{s.st_mtime_ns}:{s.st_size}:{s.st_ctime_ns}" for p in paths for s in [os.stat(p)]}

    stamp = source()
    with open(side, encoding="utf-8") as f:
        snapshot = json.load(f)
    if not isinstance(snapshot, dict):
        raise _Reject(400, "Export 사이드카가 객체가 아닙니다")
    rk = store.root_key(root)
    plans = []
    plan_error = None

    def target(cand, row):
        nonlocal plan_error
        if plan_error is not None:
            raise ValueError(plan_error)
        if not plans:
            try:
                plan, _picks, _entries, cs, _warns = _star_plan(root, m, _PREF.get(rk))
            except (ValueError, OSError, AttributeError, TypeError, KeyError, IndexError) as e:
                plan_error = str(e)  # 예외 traceback이 계획·배열을 붙잡지 않게 메시지만 기억한다.
                raise ValueError(plan_error) from None
            plans.append(({it["cand"]["key"]: (p, it, t["label"] if len(t["slices"]) == 1 else f"{t['label']} · slice {i}")
                           for p in plan for t in p["targets"] for i, it in enumerate(t["slices"], 1)}, cs))
        items, cs = plans[0]
        p, it, group = items[cand["key"]]
        rect = dmask.edit_rect(dpsd.quad_bbox(it["quad"]), _canvas_wh(m))
        dmask._check_rect(rect)
        old_mask = dmask.export_mask(root, snapshot, row, (export.get("options") or {}).get("mask_source"),
                                     use_registration=bool((export.get("compose") or {}).get("use_registration", True)))
        return {"rect": rect, "quad": it["quad"], "fp": dmask.base_fp(cand, it["entry"], cs["mask_source"]),
                "mask": _window_l(it["mask"], rect), "export_mask": _window_l(old_mask, rect),
                "crop": p["crop"]["name"], "group": group}

    def placed(mm, key, fp):
        cand = dmask.star_cand(mm, key)
        if cand is None:
            raise dmask.MaskConflict("지금 ★ 후보가 아닙니다 — 다시 미리 보기", "not_picked")
        entry, cs = _entry_for(mm, key, _PREF.get(rk)), _compose_settings(root, mm)
        try:
            current_source = source()
        except (_Reject, OSError) as e:
            raise dmask.MaskConflict(f"PSD·사이드카를 다시 확인할 수 없습니다 — 다시 미리 보기: {e}", "changed") from e
        if dmask.base_fp(cand, entry, cs["mask_source"]) != fp or current_source != stamp:
            raise dmask.MaskConflict("기준 마스크 또는 PSD가 바뀌었습니다 — 다시 미리 보기", "changed")
        return _edit_window(mm, cand, entry, bool(cs["use_registration"]))

    if source() != stamp:
        raise dmask.MaskConflict("사이드카가 바뀌었습니다 — 다시 미리 보기", "changed")
    try:
        result = dmask.import_ps_masks(root, psd, snapshot, manifest=m, target=target, placed=placed,
                                      source=source, dry_run=dry, seen=data.get("seen"))
    except dmask.PSDReadError as e:
        raise _Reject(422, str(e)) from e
    if any(f["conflict"] for f in result.get("failed", [])):
        raise _Reject(409, "일부 마스크가 다른 창에서 바뀌었습니다 — 결과 확인 후 다시 미리 보기", reason="changed", **result)
    return result


def mask_edit(root: str, data: dict) -> dict:
    """POST /mask/edit — undo = hist 첫 판으로(비면 기록 지움 = 기준), reset = 기록 지움(hist 도). ★ 여부는 보지 않는다(dmask).
    기록이 없을 때(seen "")는 바꾸지 않고 edit null(형식이 깨진 기록은 없음으로 보고 지움 — dmask._expect)."""
    key = _field(data, "key", _KEY_RE)
    seen = _seen(data.get("seen"))
    op = data.get("op")
    if op not in ("undo", "reset"):
        raise _Reject(400, "op 는 undo · reset 중 하나여야 합니다")
    if _cand(store.load_manifest(root), key) is None:
        raise _Reject(404, f"후보가 없습니다: {key}")
    rec, rev = (dmask.undo_edit if op == "undo" else dmask.reset_edit)(root, key, seen=seen)
    m = store.load_manifest(root)
    rk = store.root_key(root)
    pref = _PREF.get(rk)
    edit = None
    if rec is not None:  # 편집 창은 계획 없이(_edit_window — ★ 가 아닌 후보의 기록도 되돌릴 수 있음)
        c, e, cs = _cand(m, key), _entry_for(m, key, pref), _compose_settings(root, m)
        edit = _edit_view(rk, key, rec, dmask.base_fp(c, e, cs["mask_source"]), _edit_window(m, c, e, bool(cs["use_registration"])))
    return {"ok": True, "edit": edit, "rev": rev, "composite_sig": _composite_sig(root, m, pref)}


def mask_adjust(root: str, data: dict) -> dict:
    """후보 보정 쓰기. ★·seen 검사는 같은 매니페스트 잠금 안에서 하며 분석·마스크 기록은 건드리지 않는다."""
    key = _field(data, "key", _KEY_RE)
    op, seen = data.get("op"), data.get("seen")
    if op not in ("set", "undo", "reset"):
        raise _Reject(400, "op 는 set · undo · reset 중 하나여야 합니다")
    if not isinstance(seen, str) or not (seen == "" or re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(?:\.\d{1,9})?", seen)):
        raise _Reject(400, "seen 은 이 창에서 받은 보정 시각 또는 빈 문자열이어야 합니다")
    values = store.cand_adjust_values(data.get("blur"), data.get("invert")) if op == "set" else None

    def mutate(m):
        _mask_cand(m, key)
        old = store.cand_adjust(m, key)
        if seen != (old["at"] if old else ""):
            raise _Reject(409, "후보 보정이 다른 창에서 바뀌었습니다 — 다시 받기", reason="changed")
        hist = old["hist"] if old else []
        if op == "undo" and not hist:
            raise _Reject(400, "되돌릴 이전 보정이 없습니다")
        new = values if op == "set" else (hist[0] if op == "undo" and hist else None)
        if new is None or not (new["blur"] or new["invert"]):
            if isinstance(m.get("cand_adjust"), dict):
                m["cand_adjust"].pop(key, None)
            return None
        if op == "set" and old and all(new[k] == old[k] for k in ("blur", "invert")):
            return old
        if op == "set":
            hist = ([{k: old[k] for k in ("blur", "invert", "at")}] + hist)[:20] if old else []
        else:
            hist = hist[1:]
        # 초 안의 연속 저장·undo도 서로 다른 seen으로 구별한다(옛 at로 되돌리지 않음).
        ns = time.time_ns()
        at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ns // 1_000_000_000)) + f".{ns % 1_000_000_000:09d}"
        rec = {"blur": new["blur"], "invert": new["invert"], "at": at, "hist": hist}
        records = m.get("cand_adjust") if isinstance(m.get("cand_adjust"), dict) else {}
        m["cand_adjust"] = {**records, key: rec}
        return rec

    rec, rev = store.update_manifest(root, mutate)
    return {"ok": True, "adjust": rec, "rev": rev,
            "composite_sig": _composite_sig(root, store.load_manifest(root), _PREF.get(store.root_key(root)))}


def mask_alts(root: str, key) -> dict | None:
    """GET /mask/alts(§6.2) — 그 ★ 타깃의 탈락 아닌 다른 후보마다 "★ 로 바꾸면"(그 타깃 ★ 를 그 후보 하나로 — 보드 Space) 전체 ★ 합성의
    이음매. swap 서명별 composite·seams 캐시(_composite). 계산할 것이 남았는데 다른 대안 계산이 돌고 있으면 None(202 busy).
    changed_crops = 이 크롭 밖에서 이음매 집합이 지금과 달라지는 크롭(매니페스트 크롭 순서)."""
    m = store.load_manifest(root)
    cand = _mask_cand(m, key)
    _canvas_wh(m)
    pref = _PREF.get(store.root_key(root))
    cid, tid = cand["crop_id"], cand["tid"]
    rejects = m.get("rejects") or {}
    alts = [(n, c, (cid, tid, c["key"])) for n, c in enumerate(_target_members(m, cid, tid), 1)
            if c["key"] != key and c["key"] not in rejects]
    jobs = [(None, _composite_sig(root, m, pref))] + [(swap, _composite_sig(root, m, pref, swap)) for _n, _c, swap in alts]
    cached = all(os.path.isfile(_thumb_path(root, f"composite_{s}.jpg")) and os.path.isfile(_thumb_path(root, f"seams_{s}.json"))
                 for _swap, s in jobs)
    if not cached and not _ALTS_LOCK.acquire(blocking=False):
        return None
    try:
        segs = [_composite(root, m, pref, s, swap)[1]["segments"] for swap, s in jobs]
    except (OSError, ValueError) as e:
        raise _Reject(422, f"대안 이음매를 계산할 수 없습니다: {e}") from e
    finally:
        if not cached:
            _ALTS_LOCK.release()

    def rest(ss):  # 크롭별 이음매 집합(순서 무관 — 가정한 ★ 가 z 를 바꾸면 같은 등급·p90 끼리 순서가 바뀔 수 있음)
        out: dict[str, list] = {}
        for s in ss:
            if s["crop_id"] != cid:
                out.setdefault(s["crop_id"], []).append(json.dumps(s, sort_keys=True))
        return {k: sorted(v) for k, v in out.items()}

    now, now_rest = [s for s in segs[0] if s["crop_id"] == cid], rest(segs[0])
    calls = {x.get("call_id"): x for x in m.get("calls") or [] if isinstance(x, dict)}
    rows = []
    for (n, c, _swap), ss in zip(alts, segs[1:]):
        mine, other = [s for s in ss if s["crop_id"] == cid], rest(ss)
        call_id = _cell_of(c)[1]
        call = calls.get(call_id) if call_id else None
        rows.append({"key": c["key"], "c": n, "ran": {"done": call.get("finished"), "via": _call_via(call)} if call else None,
                     "bad": sum(s["severity"] == "bad" for s in mine), "weak": sum(s["severity"] == "weak" for s in mine),
                     "worst_p90": max((s["diff_p90"] for s in mine), default=None), "this_crop": mine,
                     "changed_crops": [x["id"] for x in m["crops"] if x["id"] != cid and now_rest.get(x["id"]) != other.get(x["id"])]})
    return {"sig": jobs[0][1], "now": {"bad": sum(s["severity"] == "bad" for s in now),
                                        "worst": max((s["diff_p90"] for s in now), default=None)}, "rows": rows}


def _zorder_scopes(order: list) -> tuple[dict, dict]:
    """지금 보이는 계획의 세 계층 목록과 크롭별 실효 패치 bbox(마스크 넓이 아님)."""
    scopes, boxes = {"crops": []}, {}
    for p, t, it in order:
        cid, tid = p["crop"]["id"], t["tid"]
        for scope, item in (("crops", cid), (f"t:{cid}", tid), (f"s:{cid}/{tid}", it["cand"]["key"])):
            ids = scopes.setdefault(scope, [])
            if item not in ids:
                ids.append(item)
        boxes.setdefault(cid, []).append(dpsd.quad_bbox(it["quad"]))
    return scopes, boxes


def _zorder_neighbor(ids: list[str], item: str, step: int, boxes: dict | None) -> int | None:
    i = ids.index(item)
    for j in range(i + step, len(ids) if step > 0 else -1, step):
        if boxes is None or any(a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]
                                for a in boxes[item] for b in boxes[ids[j]]):
            return j
    return None


def _zorder_control(m: dict, scopes: dict, boxes: dict, scope: str, item: str) -> dict:
    ids = scopes[scope]
    i = ids.index(item)
    crop_boxes = boxes if scope == "crops" else None
    blocked = {"top": "이미 맨 위입니다" if i == len(ids) - 1 else "",
               "bottom": "이미 맨 아래입니다" if i == 0 else ""}
    for op, step, label in (("up", 1, "위"), ("down", -1, "아래")):
        blocked[op] = (f"겹치는 {label} 크롭이 없습니다" if crop_boxes is not None else f"{label} 항목이 없습니다") if (
            _zorder_neighbor(ids, item, step, crop_boxes) is None) else ""
    return {"scope": scope, "item": item, "order": ids, "index": i,
            "manual": scope in store.z_order(m), "blocked": blocked}


def zorder_write(root: str, data: dict) -> dict:
    """POST /zorder — 계획은 잠금 밖, 저장 직전 rev 대조는 update_manifest 잠금 안에서."""
    scope, item, op, seen = (data.get(k) for k in ("scope", "item", "op", "seen"))
    if not isinstance(scope, str) or not re.fullmatch(r"crops|t:[^\s/:]+|s:[^\s/:]+/[^\s/:]+", scope):
        raise _Reject(400, "scope 는 crops · t:크롭 · s:크롭/타깃 형식이어야 합니다")
    if not isinstance(item, str) or not item:
        raise _Reject(400, "순서를 바꿀 항목이 잘못되었습니다")
    if op not in ("top", "up", "down", "bottom", "reset"):
        raise _Reject(400, "op 는 top · up · down · bottom · reset 중 하나여야 합니다")
    if type(seen) is not int or seen < 0:
        raise _Reject(400, "seen 은 보드가 읽은 매니페스트 rev 정수여야 합니다")

    def expect(m):
        if m["rev"] != seen:
            raise _Reject(409, "다른 창에서 순서나 프로젝트가 바뀌었습니다 — 다시 받기", reason="changed")

    m = store.load_manifest(root)
    expect(m)
    pref = _PREF.get(store.root_key(root))
    plan = _star_plan(root, m, pref)[0]
    scopes, boxes = _zorder_scopes(list(_node()._slices_in_order(plan)))
    if scope not in scopes or item not in scopes[scope]:
        raise _Reject(400, "이 범위에 보이는 항목이 없습니다 — 다시 받기")
    ids = list(scopes[scope])
    manual = store.z_order(m)
    if op == "reset":
        manual.pop(scope, None)
        auto = _star_plan(root, dict(m, z_order=manual), pref)[0]
        ids = _zorder_scopes(list(_node()._slices_in_order(auto)))[0][scope]
    else:
        i = ids.index(item)
        j = len(ids) - 1 if op == "top" else 0
        if op in ("up", "down"):
            j = _zorder_neighbor(ids, item, 1 if op == "up" else -1, boxes if scope == "crops" else None)
            if j is None:
                raise _Reject(400, _zorder_control(m, scopes, boxes, scope, item)["blocked"][op])
        ids.insert(j, ids.pop(i))
        manual[scope] = ids

    def mutate(mm):
        expect(mm)
        if manual:
            mm["z_order"] = manual
        else:
            mm.pop("z_order", None)

    _result, rev = store.update_manifest(root, mutate)
    return {"ok": True, "rev": rev, "scope": scope, "order": manual.get(scope, []), "effective": ids}


# ══════════════════════════════════════════════════════════════════════
# 상태 요약
# ══════════════════════════════════════════════════════════════════════
def _turl(rk: str, kind: str, key: str, size: int, sig: str) -> str:
    return "thumb?" + urlencode({"root": rk, "kind": kind, "key": key, "size": size, "v": sig})


def _furl(rk: str, rel: str) -> str:
    return "file?" + urlencode({"root": rk, "rel": rel})


def _gates_out(entry: dict | None):
    if not _usable(entry):
        return None
    g = entry.get("gates") or {}
    return {"fails": list(g.get("fails") or []), "outside_mad": g.get("outside_mad"),
            "outside_frac_gt24": g.get("outside_frac_gt24"), "black_band": bool(g.get("black_band"))}


def _reg_out(entry: dict | None):
    if not _usable(entry):
        return None
    r = entry["reg"]
    return {k: r.get(k) for k in ("applied", "kind", "disp", "dx", "dy", "sx", "sy", "rot_deg")}


def build_state(root: str, since_rev: int | None = None) -> dict:
    """state 라우트의 응답(JSON 기본형). URL 은 /bmk/design_patch/ 기준 상대 경로(thumb?…, file?…).
    문자열(이름·라벨)은 그대로 싣는다 — 보드는 textContent 로만 쓴다.
    layout_rev(unchanged 응답에도) = layout.json rev(없거나 깨졌으면 null) — 레이아웃 쓰기는 매니페스트 rev 를 올리지 않으므로
    보드는 이것이 바뀔 때 /layout 을 다시 받는다. 크롭 요약의 layout_status = same·changed·removed(레이아웃 없으면 null).
    refs_rev(unchanged 응답에도) = refs.json rev(없거나 깨졌으면 null) — 레퍼런스 쓰기도 매니페스트 rev 를 올리지 않는다(S2 §5.8).
    refs_files_v(unchanged 응답에도) = _refs_files_v — 바깥(탐색기·PS)에서 레퍼런스 파일을 넣고·고치고·지우면 바뀐다(refs_rev 는 그대로라
    보드는 이것이 바뀌면 GET /refs 를 since 없이 다시 받는다).
    크롭 요약의 refs = {n, main(Prepare 가 고를 대표), fixed, none(files [] 고정 — 연결 파일이 모두 사라진 고정은 false)} — 지금 연결
    해석(drefs.resolve_refs) 기준, refs_dir 폴더가 없으면 null.
    drafting(unchanged 응답에도, S3) = Spec Draft 실행 중(draft_since — running 은 Run 만, 진행 모드는 이것으로 폴링마다 실행 중을 알아
    낡은 계획·초안 다시 받기를 미룸). pipeline_rev(unchanged 응답에도, S3) = pipeline.json rev(없거나 깨졌으면 null), plan_v(unchanged
    응답에도) = 보드가 기억한 only 없는 계획(pipeline_plan)의 서명이 지금 서명(_plan_sigs — 파일 stat 만(스펙 JSON 은 specs/ 목록이 바뀔 때만 파싱), vlm 없음)과 같고 계산 오류가 없으면 그 서명, 다르거나 오류거나
    계획이 없으면 null — 보드는 둘 중 하나가 바뀌면 since_rev 없이 다시 받는다. 크롭 요약의 draft_chip = 기억한 계획의 {wait, refresh,
    adopt, run_need, running(지금 Run·Spec Draft 실행 중)}(계획이 없으면 null), plan_stale = 기억한 계획이 지금과 다르거나 오류(칩을
    흐리게 — §3.7). S4: 후보 mask_edit = 마스크 편집 기록이 있으면 {at, stale}(★ 가 아니어도 — stale 은 _plan 이 편집 파일을 쓰지 못할 때도,
    형식이 깨진 기록은 null), 크롭 요약 mask_edits·mask_stale = 지금 ★ 의 편집 수·그중 stale — 편집 쓰기는 매니페스트 rev 를 올리므로 보통
    폴링으로 반영된다. pref_v(unchanged 응답에도) = board_pref.json 지문(store.board_pref_v) — 분석·Export 설정이 바뀌면(편집 창·기준·★ 합성의
    재료, 매니페스트 rev 는 그대로) 보드는 since_rev 없이 다시 받는다.
    export_state(unchanged 응답에도) = 마지막 Export 뒤 사용자 입력 서명 same·changed·unknown(옛 기록)·null(Export 없음).
    export_last = 그 기록의 rev·time('변경 없음'으로 재사용한 옛 PSD 도 last_export 기준)."""
    m = store.load_manifest(root)
    rk = store.root_key(root)
    running, drafting = is_running(root), draft_since(root) is not None
    lay = _layout_or_none(root)
    doc = _refs_doc(root)[0]
    ex = store.last_export(root, m)
    export_state = None if ex is None else "unknown"
    if ex is not None and ex.get("state_sig"):
        try:
            export_state = "same" if ex["state_sig"] == store.export_state_sig(m) else "changed"
        except Exception:  # 점검표 서명 오류가 state 폴링을 중단시키지 않게
            pass
    flags = {"running": running, "drain": running and drain_requested(root), "drafting": drafting,
             "layout_rev": lay["rev"] if lay else None, "refs_rev": doc["rev"] if doc else None, "refs_files_v": _refs_files_v(_refs_dir(m)),
             "pipeline_rev": pl.pipeline_rev(root), "plan_v": None, "pref_v": store.board_pref_v(root),
             "export_state": export_state, "export_last": {k: ex.get(k) for k in ("rev", "time")} if ex is not None else None}
    plan = _plan_free(root)
    if plan is not None:
        r_sig, d_sig = _plan_sigs(root, m, (running, drafting), flags["refs_files_v"])
        flags["plan_v"] = plan["sig"] if plan["sig"] == f"r:{r_sig}|d:{d_sig}" and _plan_ok(plan) else None
    if since_rev is not None and since_rev == m["rev"]:
        return {"unchanged": True, "rev": m["rev"], **flags}
    pref = _PREF.get(rk)
    rejects = m.get("rejects") or {}
    picks = m.get("picks") if isinstance(m.get("picks"), dict) else {}
    rerolls = m.get("rerolls") or {}
    cs = _compose_settings(root, m)
    pending, pre_cells = _pending_cells(root, m)
    calls = [x for x in m.get("calls") or [] if isinstance(x, dict)]
    call_by_id = {x.get("call_id"): x for x in calls}
    crop_ids = {c.get("id") for c in m["crops"]}
    by_file = {(c.get("crop_id"), c.get("file"), c.get("tid")): c["key"] for c in m["candidates"]}
    wh = [int(v) for v in m["canvas"]] if m.get("base") and m.get("canvas") else None
    targets = []
    for crop in m["crops"]:
        x0, y0, x1, y1 = crop["rect"]
        box = _region_rect(crop["rect"], *wh) if wh else None
        for t in crop.get("targets") or []:
            tid = t["tid"]
            tkey = f"{crop['id']}/{tid}"
            ctx = _target_ctx(root, m, crop, tid, pref)
            focus = ctx["focus"]
            members = {c["key"] for c in ctx["cands"]}
            pick_keys = [k for k in picks.get(tkey) or [] if k in members]
            pick_keys += [c["key"] for c in ctx["cands"] if c.get("picked") and c["key"] not in pick_keys]
            jobs = _target_jobs(m, crop["id"], tid)
            cur_cells = {j.get("cell_key") for j in jobs}
            cands = []
            for n, c in enumerate(ctx["cands"], 1):
                e = ctx["entries"][c["key"]]
                info = c.get("origin_info") or {}
                ck, call_id, rep = _cell_of(c)
                call = call_by_id.get(call_id) if call_id else None
                gates = _gates_out(e)
                reg = _reg_out(e)
                joint = None  # 다중 타깃 작업의 결과 = 같은 이미지가 타깃마다 후보로 등록됨 → ★ 는 첫 타깃에만
                jt = [x for x in info.get("tids") or [] if isinstance(x, str)]
                if len(jt) > 1:
                    first = next((x["tid"] for x in crop.get("targets") or [] if x.get("tid") in jt), None)
                    joint = {"tids": jt, "first": first, "first_key": by_file.get((crop["id"], c["file"], first))}
                edit = _edit_rec(m, c["key"])
                cands.append({
                    "key": c["key"], "n": n, "origin": c.get("origin"), "backend": info.get("backend"),
                    "mock": info.get("backend") == "mock", "src_size": c.get("src_size"),
                    "label": info.get("layer") or info.get("filename") or c["key"],
                    "picked": c["key"] in pick_keys,
                    "slice_index": pick_keys.index(c["key"]) + 1 if c["key"] in pick_keys else None,
                    "rejected": bool(rejects.get(c["key"])),
                    "cell_key": ck, "call_id": call_id, "rep": rep, "variant": info.get("variant"),
                    "model": info.get("model"), "quality": info.get("quality"),
                    # 원장 calls 의 완료 시각·승인 종류(후보 캡션), 지금 jobs 에 없는 셀(설정·입력이 바뀌기 전 셀 — 옛 셀)
                    "done": call.get("finished") if call else None, "via": _call_via(call),
                    "old_cell": bool(ck and cur_cells and ck not in cur_cells),
                    "analysis": "ok" if _usable(e) else ("nosrc" if e and e.get("no_source")
                                                         else "skipped" if e and e.get("skipped") else "none"),
                    "gates": gates, "reg": reg, "reframed": bool(reg and reg.get("applied")),
                    "hand_mask": bool(c.get("hand_mask")),
                    "adjust": bool(store.cand_adjust(m, c["key"])),
                    # S4 마스크 편집(★ 가 아니어도 기록이 있으면 — 다시 ★ 하면 살아남): stale = 기록의 기준 ≠ 지금 기준 지문 또는 편집 창이 바뀜
                    # 또는 _plan 이 편집 파일을 쓰지 못함(없음·깨짐·크기 다름 — Export 는 기준 마스크, 까닭은 K 의 경고)
                    "mask_edit": None if edit is None else {"at": edit["at"], "stale": _edit_stale(
                        edit, dmask.base_fp(c, e, cs["mask_source"]), _edit_window(m, c, e, bool(cs["use_registration"])))
                        or not _edit_usable(root, edit)},
                    "stale_rect": c.get("origin") in ("run", "folder") and dpsd.quad_bbox(c["quad"]) != crop["rect"],
                    "joint": joint,
                    "thumb_url": _turl(rk, "cand", c["key"], GRID_SIZE,
                                       _cand_sig(root, "cand", GRID_SIZE, m, crop, c, e, focus)),
                    "big_url": _turl(rk, "cand", c["key"], BIG_SIZE, _cand_sig(root, "cand", BIG_SIZE, m, crop, c, e, focus)),
                    "delta_url": _turl(rk, "delta", c["key"], BIG_SIZE,
                                       _cand_sig(root, "delta", BIG_SIZE, m, crop, c, e, focus)),
                    "context_url": _turl(rk, "context", c["key"], BIG_SIZE,
                                         _cand_sig(root, "context", BIG_SIZE, m, crop, c, e, focus)),
                    "full_url": _turl(rk, "cand", c["key"], FULL_SIZE, _cand_sig(root, "cand", FULL_SIZE, m, crop, c, e, focus)),
                    # "이 후보를 ★ 하면": 그 타깃의 ★ 를 이 후보로 바꾼 가정의 원해상도 영역(뷰어 겹침)
                    "pick_url": None if box is None else "canvas?" + urlencode(
                        {"root": rk, "kind": "region", "crop": crop["id"], "key": c["key"],
                         "v": _region_sig(_composite_sig(root, m, pref, (crop["id"], tid, c["key"])), box)}),
                    "file_url": _furl(rk, c["file"]),
                })
            cells = []
            for j in jobs:
                ck = j["cell_key"]
                cells.append({"cell_key": ck, "variant": j.get("variant"), "tids": _job_tids(j),
                              "reps": int(j.get("reps") or 1), "rerolls": int(rerolls.get(ck) or 0),
                              "pending": pending.get(ck, 0), "pre": pre_cells.get(ck, 0), "model": j.get("model"), "quality": j.get("quality"),
                              "size": j.get("size"), "n": j.get("n"), "est_usd": _est_usd(j)})
            refs = [{"index": i, "file": r, "thumb_url": _turl(rk, "ref", f"{tkey}/{i}", BIG_SIZE, _sig("ref", BIG_SIZE, r)),
                     "file_url": _furl(rk, r)} for i, r in enumerate(_target_refs(m, crop["id"], tid))]
            live = [c for c in cands if not c["rejected"]]
            targets.append({
                "key": tkey, "crop_id": crop["id"], "tid": tid, "nnn": crop.get("nnn"), "crop_name": crop.get("name"),
                "part": crop.get("part"), "label": t.get("label"), "name_en": t.get("name_en"), "mode": t.get("mode"),
                "rect": crop["rect"], "size": [x1 - x0, y1 - y0], "focus_box": focus,
                "before_url": _turl(rk, "before", tkey, BIG_SIZE,
                                    _sig("before", BIG_SIZE, crop["source"], crop["rect"], focus)),
                "before_full_url": _turl(rk, "before", tkey, FULL_SIZE,
                                         _sig("before", FULL_SIZE, crop["source"], crop["rect"], None)),
                "refs": refs, "candidates": cands, "pick_keys": pick_keys, "cells": cells,
                "pending_calls": sum(c["pending"] for c in cells),
                "chips": {"n": len(cands), "live": len(live), "picked": len(pick_keys),
                          "gate_fail": sum(1 for c in live if c["gates"] and c["gates"]["fails"]),
                          "reframed": sum(1 for c in live if c["reframed"]),
                          "mock": sum(1 for c in cands if c["mock"]), "rejected": len(cands) - len(live)},
                "warnings": list(crop.get("warnings") or []),
            })
    crops = _crop_summaries(m, targets, calls)
    by_id = {c["id"]: c for c in lay["crops"]} if lay else {}
    refs_dir = _refs_dir(m)
    refs_ok = _refs_dir_ok(refs_dir)
    run_rows = (plan or {}).get("run", {}).get("by_crop") or {}
    draft_rows = (plan or {}).get("draft", {}).get("by_crop") or {}
    busy = running or drafting
    for c, mc in zip(crops, m["crops"]):  # 레이아웃 대 매니페스트(검토 모드 목록의 "미반영" 칩)
        status, changed = (None, []) if lay is None else ("removed", []) if c["id"] not in by_id else _layout_status(by_id[c["id"]], mc)
        c["layout_status"] = status
        # 바뀐 크롭의 레이아웃 값(요약의 name·rect 는 마지막 반영 = 매니페스트 값) — 진행(G) 반영 칸의 크롭별 줄·[되돌리기]
        lc = by_id.get(c["id"])
        c["layout_diff"] = None if status != "changed" else {
            "fields": changed, "name": lc["name"], "rect": lc["rect"],
            "targets": _targets_text(lc["targets"]) if "targets" in changed else None,
            "targets_was": _targets_text(store.parse_crop_layer_name(mc.get("layer_name") or "")["targets"]) if "targets" in changed else None}
        c["locked"] = bool(lc and lc.get("locked"))  # 잠긴 크롭(배치 3-3) — 반영 칸 [되돌리기]를 끔(서버도 423)
        d = draft_rows.get(c["id"]) or {}
        c["draft_chip"] = None if plan is None else {
            "wait": list(d.get("wait") or []), "refresh": bool(d.get("refresh")), "adopt": d.get("adopt"),
            "run_need": int((run_rows.get(c["id"]) or {}).get("need") or 0), "running": busy}
        c["plan_stale"] = plan is not None and flags["plan_v"] is None
        # 지금 ★ 의 마스크 편집 수·그중 기준 바뀜(S4 크롭 칩)
        edits = [x["mask_edit"] for t in targets if t["crop_id"] == c["id"] for x in t["candidates"]
                 if x["mask_edit"] and x["picked"] and not x["rejected"]]
        c["mask_edits"], c["mask_stale"] = len(edits), sum(1 for x in edits if x["stale"])
        c["cand_adjust"] = sum(1 for t in targets if t["crop_id"] == c["id"] for x in t["candidates"]
                               if x["adjust"] and x["picked"] and not x["rejected"])
        r = drefs.resolve_refs(refs_dir, mc, doc) if refs_ok else None
        # none = 의도한 '레퍼런스 없음' 고정(files [] — 부수 효과 아님) — 연결한 파일이 모두 사라진 고정(n 0)과 칩을 가른다.
        # auto = 다른 크롭의 동작이 함께 '없음'으로 고정함(files [] + fixed.auto — 칩 '레퍼런스 없음(자동 고정)')
        ln = doc["links"][mc["id"]] if r is not None and r["linked"] else None
        c["refs"] = None if r is None else {"n": len(r["refs_auto"]), "main": _pick(mc["name"], r), "fixed": r["linked"],
                                            "none": _none_fixed(ln), "auto": ln is not None and not ln["files"] and _auto_fixed(ln)}
    canvas = None
    if m.get("base") and m.get("canvas"):
        W, H = (int(v) for v in m["canvas"])
        csig = _composite_sig(root, m, pref)
        canvas = {"size": [W, H], "preview": list(_preview_wh(W, H)), "composite_sig": csig,
                  "picks": len(_composite_picks(m)),
                  "base_url": "canvas?" + urlencode({"root": rk, "kind": "base", "v": _base_sig(m)}),
                  "composite_url": "canvas?" + urlencode({"root": rk, "kind": "composite", "v": csig}),
                  "seams_url": "seams?" + urlencode({"root": rk, "v": csig})}
        for c in crops:  # 뷰어가 선택 크롭 아래에 까는 원해상도 ★ 합성 영역
            box = _region_rect(c["rect"], W, H)
            c["region"] = box
            c["region_url"] = "canvas?" + urlencode({"root": rk, "kind": "region", "crop": c["id"],
                                                     "v": _region_sig(csig, box)})
    return {
        "rev": m["rev"], "project": m.get("project"), "root_key": rk, "backend_mock": _mock_active(root),
        **flags, "time": time.strftime("%H:%M:%S"),
        "pending_calls": sum(pending.values()), "pending_preapproved": sum(pre_cells.values()), "rerolls_total": sum(int(v or 0) for v in rerolls.values()),
        "counts": {"targets": len(targets), "candidates": len(m["candidates"]), "rejected": len(rejects),
                   "picks": sum(len(t["pick_keys"]) for t in targets)},
        "orphan_candidates": sum(1 for c in m["candidates"] if c.get("crop_id") not in crop_ids),
        "cost": _cost(calls),
        "canvas": canvas,
        # 레이아웃에만 있는 크롭(다음 반영에서 새로 생김 — 크롭 요약에는 없음). 레이아웃이 없으면 null
        "layout_added": None if lay is None else [{"id": x["id"], "name": x["name"], "rect": x["rect"]}
                                                  for x in lay["crops"] if x["id"] not in crop_ids],
        "crops": crops,
        "targets": targets,
    }


def _cost(calls: list[dict]) -> dict:
    """원장 calls 의 크레딧 합(알려진 것만)·호출 수·완료 수·크레딧 미상 완료 수(크레딧 조회가 안 된 유료 호출 — 0 이 아님)."""
    done = [x for x in calls if x.get("status") == "done"]
    return {"credits": round(sum(float(x["credits"]) for x in calls if x.get("credits") is not None), 2),
            "calls": len(calls), "done": len(done), "unknown": sum(1 for x in done if x.get("credits") is None)}


def _crop_summaries(m: dict, targets: list[dict], calls: list[dict]) -> list[dict]:
    """크롭별 요약(Studio 왼쪽 목록·캔버스 사각형). 대기 = 셀 중복 없이(다중 타깃 작업은 타깃마다 같은 셀),
    중복 ★ = 공동 결과(다중 타깃 작업이 타깃마다 같은 이미지로 등록한 후보)를 앞 타깃에 이어 뒤 타깃에도 ★ — 같은 파일이라도
    수확 후보(SO 를 마스크·위치만 바꿔 타깃마다 둔 것)는 다른 패치라 보지 않는다. 옛 크롭 ★ = rect 를 옮기기 전 후보의 ★."""
    by_crop: dict[str, list[dict]] = {}
    for t in targets:
        by_crop.setdefault(t["crop_id"], []).append(t)
    files = {c["key"]: c.get("file") for c in m["candidates"]}
    out = []
    for crop in m["crops"]:
        ts = by_crop.get(crop["id"], [])
        seen: dict[str, str] = {}
        dup, stale = [], []
        for t in ts:
            joint = {c["key"] for c in t["candidates"] if c["joint"]}
            stale_keys = {c["key"] for c in t["candidates"] if c["stale_rect"]}
            for k in t["pick_keys"]:
                f = files.get(k)
                if k in joint and seen.setdefault(f, t["tid"]) != t["tid"]:
                    dup.append({"key": k, "tid": t["tid"], "same_as": seen[f]})
                if k in stale_keys:
                    stale.append(k)
        cost = _cost([x for x in calls if x.get("crop_id") == crop["id"]])
        cells = {x["cell_key"]: x["pending"] for t in ts for x in t["cells"]}
        warnings = list(crop.get("warnings") or [])
        out.append({
            "id": crop["id"], "name": crop.get("name"), "nnn": crop.get("nnn"), "rect": crop["rect"],
            "tids": [t["tid"] for t in ts], "warnings": warnings,
            "candidates": sum(len(t["candidates"]) for t in ts), "picked_targets": sum(1 for t in ts if t["pick_keys"]),
            "pending": sum(cells.values()), "credits": cost["credits"], "credits_unknown": cost["unknown"],
            "calls": cost["calls"], "calls_done": cost["done"],
            "dup_picks": dup, "stale_picked": stale, "problems": len(warnings) + len(dup) + len(stale),
        })
    return out


def prebuild_thumbs(root: str, analyze_params: dict | None = None, size: int = GRID_SIZE) -> dict:
    """Review 노드용: 보드가 쓸 분석 설정을 기억하고(None = 가장 최근 분석) 후보 그리드(size)·Before(BIG)·
    pick 후보 큰 썸네일을 미리 만든다(이미 있으면 건너뜀). 반환 {"targets","thumbs","built","failed":[...] ,"seconds"}."""
    if size not in THUMB_SIZES:
        raise ValueError(f"size 는 {', '.join(map(str, THUMB_SIZES))} 중 하나여야 합니다: {size!r}")
    t0 = time.time()
    rk = store.root_key(root)
    if analyze_params is None:
        _PREF.pop(rk, None)
    else:
        _PREF[rk] = json.loads(json.dumps(analyze_params))
    st = build_state(root)
    jobs = []
    for t in st["targets"]:
        jobs.append(("before", t["key"], BIG_SIZE))
        for c in t["candidates"]:
            jobs.append(("cand", c["key"], size))
            if c["picked"]:
                jobs.append(("cand", c["key"], BIG_SIZE))
    thumbs = os.path.join(root, *_THUMB_DIR.split("/"))
    before = set(os.listdir(thumbs)) if os.path.isdir(thumbs) else set()
    failed: list[str] = []

    def one(job):
        try:
            render_thumb(root, *job)
        except _Reject as e:
            failed.append(f"{job[0]} {job[1]}: {e.message}")
        except ValueError as e:  # 매니페스트 경로 형식 오류 등 — 한 장 실패가 Review 를 멈추지 않게
            failed.append(f"{job[0]} {job[1]}: {e}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        list(ex.map(one, jobs))
    after = set(os.listdir(thumbs)) if os.path.isdir(thumbs) else set()
    return {"targets": len(st["targets"]), "thumbs": len(jobs), "built": len(after - before), "failed": failed,
            "seconds": round(time.time() - t0, 2)}


# ══════════════════════════════════════════════════════════════════════
# 쓰기 (모두 store.update_manifest)
# ══════════════════════════════════════════════════════════════════════
def apply_pick(root: str, crop_id: str, tid: str, key: str, mode: str) -> tuple[dict, int]:
    """picks[crop_id/tid] 갱신 + 그 타깃 후보의 picked 플래그 동기화. set = ★ 교체(그 후보만), add = slice 추가(끝에),
    remove = 빼기(비면 키 삭제). set/add 는 그 후보의 탈락 표시를 지운다. → ({"pick_keys", "rejected_cleared"}, rev)"""
    if mode not in ("set", "add", "remove"):
        raise _Reject(400, "mode 는 set, add, remove 중 하나여야 합니다")

    def mutate(m):
        cand = _cand(m, key)
        if cand is None:
            raise _Reject(404, f"후보가 없습니다: {key}")
        if cand.get("crop_id") != crop_id or cand.get("tid") != tid:
            raise _Reject(409, f"후보 {key} 는 {cand.get('crop_id')}/{cand.get('tid')} 타깃입니다(보드 상태가 낡음 — 새로고침)")
        members = _target_members(m, crop_id, tid)
        keys = {c["key"] for c in members}
        tkey = f"{crop_id}/{tid}"
        cur = [k for k in m["picks"].get(tkey) or [] if k in keys]
        cur += [c["key"] for c in members if c.get("picked") and c["key"] not in cur]
        if mode == "set":
            new = [key]
        elif mode == "add":
            new = cur if key in cur else cur + [key]
        else:
            new = [k for k in cur if k != key]
        if new:
            m["picks"][tkey] = new
        else:
            m["picks"].pop(tkey, None)
        for c in members:
            c["picked"] = c["key"] in new
        cleared = mode != "remove" and m["rejects"].pop(key, None) is not None
        return {"pick_keys": new, "rejected_cleared": cleared}

    return store.update_manifest(root, mutate)


def apply_reject(root: str, key: str, value: bool) -> tuple[dict, int]:
    """rejects[key] = true / 삭제. 탈락시키면 pick 에서도 뺀다(내보내기에 탈락 후보가 남지 않게)."""

    def mutate(m):
        cand = _cand(m, key)
        if cand is None:
            raise _Reject(404, f"후보가 없습니다: {key}")
        unpicked = False
        if value:
            m["rejects"][key] = True
            tkey = f"{cand['crop_id']}/{cand['tid']}"
            lst = m["picks"].get(tkey) or []
            unpicked = bool(cand.get("picked")) or key in lst
            if key in lst:
                lst = [k for k in lst if k != key]
                if lst:
                    m["picks"][tkey] = lst
                else:
                    m["picks"].pop(tkey, None)
            cand["picked"] = False
        else:
            m["rejects"].pop(key, None)
        return {"rejected": bool(value), "unpicked": unpicked}

    return store.update_manifest(root, mutate)


def apply_reroll(root: str, count: int, cell_key: str | None = None, crop_id: str | None = None,
                 tid: str | None = None, variant: str | None = None) -> tuple[dict, int]:
    """rerolls[cell] += count (보드 사전 승인 호출). 셀은 cell_key, 아니면 (crop_id, tid[, variant]) 로 찾는다
    (variant 가 없으면 그 타깃의 작업이 하나일 때만). 더한 뒤 그 셀의 대기 호출이 _MAX_CELL_PENDING 을 넘으면 409
    (rerolls 는 셀 목표의 누적값이라 줄지 않으므로 상한은 아직 소화하지 않은 대기 호출로 본다)."""
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= _MAX_REROLL_COUNT:
        raise _Reject(400, f"count 는 1–{_MAX_REROLL_COUNT} 정수여야 합니다")

    def mutate(m):
        if cell_key is not None:
            job = next((j for j in m["jobs"] if j.get("cell_key") == cell_key), None)
            if job is None:
                raise _Reject(404, f"작업(셀)이 없습니다: {cell_key} — Prepare 를 다시 실행했다면 보드를 새로고침")
        else:
            jobs = _target_jobs(m, crop_id, tid)
            if variant is not None:
                jobs = [j for j in jobs if j.get("variant") == variant]
            if not jobs:
                raise _Reject(404, f"{crop_id}/{tid} 타깃의 작업이 없습니다" + (f"(변형 {variant})" if variant else "")
                              + " — Prepare 를 먼저 실행하세요")
            if len(jobs) > 1 and variant is None:
                raise _Reject(400, f"{crop_id}/{tid} 타깃에 작업이 {len(jobs)}개입니다 — variant 또는 cell_key 를 지정하세요 "
                                   f"({', '.join(str(j.get('variant')) for j in jobs)})")
            job = jobs[0]
        ck = job["cell_key"]
        cur = int(m["rerolls"].get(ck) or 0)
        waiting = _pending(root, m)[0].get(ck, 0)  # 원장 폴더 몇 개를 훑는 정도(잠금 안에서도 짧다)
        if waiting + count > _MAX_CELL_PENDING:
            raise _Reject(409, f"이 생성 조건에 대기 호출이 이미 {waiting}개입니다(상한 {_MAX_CELL_PENDING}) — Run 으로 소화한 뒤 다시")
        m["rerolls"][ck] = cur + count
        return {"cell_key": ck, "variant": job.get("variant"), "rerolls": cur + count, "est_usd": _est_usd(job)}

    return store.update_manifest(root, mutate)


# ══════════════════════════════════════════════════════════════════════
# 레이아웃 (S1 — STUDIO_S1_SPEC §4). 원천 = <root>/layout.json, 쓰기는 모두 store.update_layout(expect_rev = 요청 rev).
# 매니페스트는 읽기만 한다(반영은 다음 큐의 Import PSD).
# ══════════════════════════════════════════════════════════════════════
def _layout_or_none(root: str) -> dict | None:
    """layout.json(없거나 깨졌으면 None — 깨진 이유는 /layout 의 error 가 알린다)."""
    try:
        return store.load_layout(root)
    except ValueError:
        return None


def _targets_text(targets: list[dict]) -> list[str]:
    """대상 목록 → 크롭별 줄의 문구("A: 문구", 빈 문구는 tid 만)."""
    return [f"{t['tid']}: {t['text']}" if t["text"] else t["tid"] for t in targets]


def _layout_status(crop: dict, mc: dict | None) -> tuple[str, list[str]]:
    """레이아웃 크롭 대 같은 id 의 매니페스트 크롭: ("new", []) · ("same", []) · ("changed", 바뀐 것 name·rect·targets).
    대상은 레이어명 파싱 결과(tid·문구)로 비교한다 — 이관 직후 레이어명 끝 ';' 같은 표기 차이는 바뀜이 아니다."""
    if mc is None:
        return "new", []
    changed = [f for f, same in (("name", crop["name"] == mc.get("name")), ("rect", list(crop["rect"]) == list(mc["rect"])),
                                 ("targets", store.layout_parsed(crop)["targets"]
                                  == store.parse_crop_layer_name(mc["layer_name"])["targets"])) if not same]
    return ("changed" if changed else "same"), changed


def _refs_finder(refs_dir: str | None) -> Callable | None:
    """refs_dir 의 match_refs(nnn, part) — 폴더의 파일 이름 목록이 같으면 크롭마다 listdir 하지 않고 지난 결과를 쓴다.
    refs_dir 가 없거나 폴더가 없으면 None."""
    if not refs_dir or not os.path.isdir(refs_dir):
        return None
    names = tuple(sorted(os.listdir(refs_dir)))
    with _REFS_LOCK:
        hit = _REFS_MEMO.get(refs_dir)
        if hit is None or hit[0] != names:
            hit = _REFS_MEMO[refs_dir] = (names, {})
    memo = hit[1]

    def find(nnn: str, part: str) -> list[str]:
        if (nnn, part) not in memo:
            memo[(nnn, part)] = store.match_refs(refs_dir, nnn, part=part)
        return memo[(nnn, part)]

    return find


def _refs_row(refs: list[str], fixed: bool = False) -> dict:
    """/layout 크롭 행의 refs. fixed = refs.json 에 고정된 크롭(연결 유지 — 이름을 바꿔도 같음, S2 §2.6). refs.json 이 없으면 키도 없다."""
    return {"n": len(refs), "first": refs[0] if refs else None} | ({"fixed": True} if fixed else {})


def _linked_refs(refs_dir, doc: dict | None, crop: dict) -> dict | None:
    """crop 이 refs.json 에 고정돼 있고 그 연결이 지금 refs_dir 것이면 drefs.resolve_refs 결과, 아니면 None(이름 매칭)."""
    if doc is None or crop["id"] not in doc["links"] or not drefs.same_refs_dir(doc["refs_dir"], refs_dir):
        return None
    return drefs.resolve_refs(refs_dir, crop, doc)


def _spec(root: str, crop_id: str) -> dict:
    """specs/<crop_id>.json — 사람이 고치다 깨뜨린 스펙은 편집기에서는 없는 것으로(Import·Prepare 가 오류로 알린다)."""
    try:
        return store.load_spec_override(root, crop_id)
    except ValueError:
        return {}


def _adopted(spec: dict, crop: dict) -> dict[str, str]:
    """스펙 _layer_targets(Spec Draft 채택)의 {tid: 문구}. 앞머리 '<이름> |' 는 지금 이름·psd_name(옛 이름)·크롭 ID 의 처음 이름이면
    뗀다(이름을 바꾼 크롭의 스펙). 형식이 틀리면 {}(Import 가 알린다)."""
    try:
        merged, _w = store.merge_layer_targets({"name": crop["name"], "targets": []}, spec.get("_layer_targets"),
                                               crop_id=crop["id"], aliases=[crop["psd_name"]] if crop.get("psd_name") else [])
    except ValueError:
        return {}
    return {t["tid"]: t["text"] for t in merged["targets"]}


def _draft_canvas_sha(root: str, crop_id: str, draft: dict) -> str | None:
    """초안 원장 vlm/<crop_id>/<_draft.key>/a<attempt>.req.json 의 canvas src_sha. 본문은 OpenAI 꼴(input[].content[] 의
    input_image 에 role·src_sha) 또는 Qwen·mock 꼴(images = [[role, name, src_sha, w, h]]). 원장이 없거나 읽을 수 없는 꼴이면 None.
    키·시도 번호는 사람이 고칠 수 있는 스펙에서 오므로 경로로 쓰기 전에 형식을 본다."""
    key, attempt = draft.get("key"), draft.get("attempt")
    if (not isinstance(key, str) or not _DRAFT_KEY_RE.fullmatch(key) or isinstance(attempt, bool)
            or not isinstance(attempt, int) or attempt < 1):
        return None
    try:
        raw = _read_bytes(store.resolve_path(root, f"vlm/{crop_id}/{key}/a{attempt}.req.json"))
        if raw is None:
            return None
        body = json.loads(raw.decode("utf-8"))["body"]
        for im in body.get("images") or []:
            if im[0] == "canvas":
                return im[2]
        for msg in body.get("input") or []:
            for c in msg.get("content") or []:
                if c.get("role") == "canvas":
                    return c.get("src_sha")
    except (KeyError, TypeError, AttributeError, IndexError, ValueError, OSError):  # 손으로 고친·잘린 원장
        return None
    return None


def _bbox_ok(b) -> bool:
    """스펙 bbox = 실수 4개(bool 제외), 초안 캔버스 비율 0–1 근처(±0.5). 범위 비교라 NaN·inf·아주 큰 정수도 빠진다
    (math.isfinite 는 큰 정수에서 OverflowError)."""
    return (isinstance(b, list) and len(b) == 4
            and all(not isinstance(v, bool) and isinstance(v, (int, float)) and -0.5 <= v <= 1.5 for v in b))


def _draft_boxes(root: str, m: dict, crop_id: str, spec: dict) -> dict:
    """초안 상자(D5): 스펙 targets[].bbox(초안 캔버스에 대한 비율)를 원장 req 의 canvas src_sha 가 지금 매니페스트 jobs 의
    그 크롭 canvas sha 와 같고 그 job 의 canvas 소스가 지금 크롭 소스일 때만 믿고 캔버스 px(매니페스트 rect + 비율 × 크기)로. {"trusted", "boxes": {tid: px},
    "n": 스펙의 쓸 수 있는 bbox 수}. 다른 화면(크롭 크기·소스가 바뀐 뒤)에서 만든 초안은 trusted false·boxes {}.
    사람이 고칠 수 있는 스펙이라 targets 가 목록이 아니거나 bbox 가 _bbox_ok 가 아니면 없는 것으로 본다."""
    targets = spec.get("targets")
    bbs = {t["id"]: t["bbox"] for t in (targets if isinstance(targets, list) else [])
           if isinstance(t, dict) and isinstance(t.get("id"), str) and _bbox_ok(t.get("bbox"))}
    out = {"trusted": False, "boxes": {}, "n": len(bbs)}
    mc = _crop(m, crop_id)
    draft = spec.get("_draft")
    if not bbs or mc is None or not isinstance(draft, dict):
        return out
    # jobs 는 Prepare 가 만든 것 — 크롭을 옮긴 뒤 Import 만 돌았으면(Prepare 실패·꺼짐) 옛 캔버스라, 그 job 의 소스가 지금 크롭 소스일 때만
    shas = {inp.get("sha") for j in m["jobs"] if j.get("crop_id") == crop_id
            for inp in j.get("inputs") or []
            if isinstance(inp, dict) and inp.get("role") == "canvas" and inp.get("source") == mc.get("source")}
    sha = _draft_canvas_sha(root, crop_id, draft)
    if not isinstance(sha, str) or sha not in shas:  # 손으로 고친 원장의 src_sha 가 목록 등이면 set 비교가 TypeError
        return out
    x0, y0, x1, y1 = mc["rect"]
    w, h = x1 - x0, y1 - y0
    out.update(trusted=True, boxes={tid: [x0 + round(b[0] * w), y0 + round(b[1] * h), x0 + round(b[2] * w), y0 + round(b[3] * h)]
                                    for tid, b in bbs.items()})
    return out


def _read_snapshot(root: str, m: dict) -> dict | None:
    """PSD 스냅숏(derived/psd_crops.json)이 매니페스트 source(마지막 Import)의 PSD 경로·지문·crop_group·crop_pixels_group 으로 쓴
    것이면 그 dict, 아니면(없음·깨짐·다른 PSD 로 쓴 낡은 것) None — 노드 _read_psd_snapshot 과 같은 조건."""
    try:
        with open(os.path.join(root, store.PSD_SNAPSHOT), encoding="utf-8") as f:
            snap = json.load(f)
    except (OSError, ValueError):
        return None
    src = m.get("source") or {}
    return snap if all(snap.get(k) == src.get(k) for k in _SNAPSHOT_KEYS) else None


def _psd_checks(crop: dict, diff: dict | None) -> list[dict]:
    """D-psd: 이 크롭(psd_name, 없으면 name)의 PSD shape 가 기준점 뒤 바뀌었거나 사라짐."""
    if diff is None:
        return []
    name = crop.get("psd_name") or crop["name"]
    rows = []
    for ch in diff["changed"]:
        if ch["name"] == name:
            what = " · ".join(f"rect {ch['from']['rect']}→{ch['to']['rect']}" if f == "rect"
                              else f"레이어명 {ch['to']['layer_name']!r}" for f in ch["fields"])
            rows.append({"code": "D-psd", "level": "warn", "msg": f"PSD 사각형이 이관 뒤 바뀜(반영 안 함): {what}"})
    for r in diff["removed"]:
        if r["name"] == name:
            rows.append({"code": "D-psd", "level": "warn", "msg": "PSD 에서 shape 가 사라짐(레이아웃은 그대로)"})
    return rows


def _name_continues(crop: dict, others: list[dict]) -> bool:
    """지운 뒤 같은 이름으로 add 하면 같은 ID 가 되는가(add 와 같은 규칙: 이름 규칙·유일 통과 + new_layout_id == id).
    이름을 바꾼 크롭·규칙 밖 이름·다른 크롭과 같은 이름은 False."""
    try:
        store.check_crop_name(crop["name"], [o["name"] for o in others])
    except ValueError:
        return False
    return store.new_layout_id(crop["name"], [o["id"] for o in others]) == crop["id"]


def layout_state(root: str, since: int | None = None) -> dict:
    """GET /layout 응답(STUDIO_S1_SPEC §4.1). since 가 지금 rev 면 {"unchanged": true, "rev"}.
    레이아웃이 없으면 exists false(editable = 베이스·캔버스 있음, manifest_crops = 이관할 크롭 수, start_blocked = 이관이 막힌 이유
    — 레이아웃 모드 결과 매니페스트에 믿을 수 있는 스냅숏이 없음), 깨졌으면 error + editable false.
    applied = 매니페스트 source.layout.sig(Import 가 마지막에 읽은 판)가 지금 layout_sig 와 같음(메모·상자만 고친 것은 반영된 것).
    psd_diff = 믿을 수 있는 스냅숏(_read_snapshot)의 지문·at + 기준점과의 차이(없으면 null).
    크롭 행 = 레이아웃 크롭(locked — 없으면 false) + 대 매니페스트 상태·GPT 출력 크기·레퍼런스·기록(by_file = 폴더·PSD 수확 후보 수)·초안 상자·
    same_name_continues(_name_continues)·검사(store.layout_geom_checks + R-none·D-psd·D-pname·H-orphan).
    레퍼런스는 refs.json 에 고정된 크롭이면 연결 해석(_linked_refs — 이름을 바꿔도 같음, 행에 fixed), 아니면 이름 매칭(S2 §2.6).
    removed = 매니페스트에만 있는 크롭(레이아웃에서 지움, 미반영). 문자열은 그대로(보드는 textContent)."""
    m = store.load_manifest(root)
    try:
        lay, error = store.load_layout(root), None
    except ValueError as e:
        lay, error = None, str(e)
    if lay is not None and since == lay["rev"]:
        return {"unchanged": True, "rev": lay["rev"]}
    src = m.get("source") or {}
    find_refs = _refs_finder(src.get("refs_dir"))
    doc = _refs_doc(root)[0]
    out = {"exists": lay is not None or error is not None, "rev": lay["rev"] if lay else None,
           "editable": bool(m.get("base") and m.get("canvas")) and error is None, "error": error,
           "canvas": lay["canvas"] if lay else m.get("canvas"), "applied_rev": (src.get("layout") or {}).get("rev"),
           "applied": False, "start_blocked": None,
           "pending_rerolls": _pending(root, m)[1], "refs_dir": find_refs is not None, "manifest_crops": len(m["crops"]),
           "psd": None, "psd_diff": None, "crops": [], "removed": []}
    snap = _read_snapshot(root, m)
    if lay is None:
        if out["editable"]:
            try:  # 시작이 막히는가 = layout_start 와 같은 계산이 거절하는가(같은 안내 문구)
                store.layout_from_manifest(m, snap)
            except ValueError as e:
                out["start_blocked"] = str(e)
        return out
    out["applied"] = (src.get("layout") or {}).get("sig") == store.layout_sig(lay)
    diff = None if snap is None else store.psd_diff(lay["psd"], snap)
    out["psd"] = {"path": lay["psd"].get("path"), "at": lay["psd"].get("at")}
    out["psd_diff"] = None if diff is None else {"fingerprint": snap.get("fingerprint"), "at": snap.get("at"), "token": _snap_token(snap),
                                                  **diff}
    pixel_nodes = set(snap["pixel_nodes"]) if snap else set()
    geom = store.layout_geom_checks(lay)
    mcrops = {c.get("id"): c for c in m["crops"]}
    cands: dict[str, int] = {}
    picks: dict[str, int] = {}
    by_file: dict[str, int] = {}
    for c in m["candidates"]:
        cands[c.get("crop_id")] = cands.get(c.get("crop_id"), 0) + 1
        if c.get("picked"):
            picks[c.get("crop_id")] = picks.get(c.get("crop_id"), 0) + 1
        if c.get("origin") in _FILE_ORIGINS:
            by_file[c.get("crop_id")] = by_file.get(c.get("crop_id"), 0) + 1
    pnames: dict[str, list[dict]] = {}
    for crop in lay["crops"]:
        pnames.setdefault(crop.get("psd_name") or crop["name"], []).append(crop)
    for crop in lay["crops"]:
        cid = crop["id"]
        mc = mcrops.get(cid)
        status, changed = _layout_status(crop, mc)
        parsed = store.layout_parsed(crop)
        spec = _spec(root, cid)
        adopted = _adopted(spec, crop)
        x0, y0, x1, y1 = crop["rect"]
        gw, gh, ginfo = store.gpt_output_size(x1 - x0, y1 - y0)
        linked = None if find_refs is None else _linked_refs(src.get("refs_dir"), doc, crop)
        refs = None if find_refs is None else linked["refs_auto"] if linked else find_refs(parsed["nnn"], parsed["part"])
        history = {"candidates": cands.get(cid, 0), "picks": picks.get(cid, 0),
                   "spec": os.path.isfile(os.path.join(root, "specs", f"{cid}.json")), "by_file": by_file.get(cid, 0)}
        checks = list(geom[cid])
        if refs is not None and not refs:
            checks.append(_r_none(doc["links"][cid] if linked else None, parsed["nnn"]))
        checks += _psd_checks(crop, diff)
        pname = crop.get("psd_name") or crop["name"]
        mates = [o["name"] for o in pnames[pname] if o is not crop]
        if mates:  # 사전 편집 자식은 이름으로 찾음 → 두 크롭 소스에 같은 덧칠(스냅숏에 그 자식이 있으면 warn, 모르면 info)
            checks.append({"code": "D-pname", "level": "warn" if pname in pixel_nodes else "info",
                           "msg": f"사전 편집 그룹 {pname} 을 크롭 {', '.join(mates)} 도 씀 — 같은 덧칠이 두 소스에 들어감"})
        if status == "new" and (history["candidates"] or history["spec"]):
            checks.append({"code": "H-orphan", "level": "info",
                           "msg": f"같은 ID 의 옛 기록이 이어짐(후보 {history['candidates']} · ★ {history['picks']}"
                                  + (" · 스펙 있음)" if history["spec"] else ")")})
        tids = {t["tid"] for t in crop["targets"]}
        out["crops"].append({
            "id": cid, "name": crop["name"], "nnn": parsed["nnn"], "part": parsed["part"], "side": store._name_side(crop["name"]),
            "rect": crop["rect"],
            "targets": [{"tid": t["tid"], "text": t["text"], "box": t.get("box"), "adopted": adopted.get(t["tid"])}
                        for t in crop["targets"]],
            "adopted_only": [{"tid": tid, "text": x} for tid, x in adopted.items() if tid not in tids],
            "psd_name": crop.get("psd_name"), "note": crop.get("note", ""), "locked": crop.get("locked", False), "status": status, "changed": changed,
            "manifest": None if mc is None else {"name": mc.get("name"), "rect": mc["rect"]},
            "gpt": {"size": [gw, gh], "k": ginfo["k"]},
            "refs": None if refs is None else _refs_row(refs, linked is not None),
            "history": history, "draft_boxes": _draft_boxes(root, m, cid, spec),
            "same_name_continues": _name_continues(crop, [o for o in lay["crops"] if o is not crop]), "checks": checks,
        })
    ids = {c["id"] for c in lay["crops"]}
    out["removed"] = [{"id": c["id"], "name": c.get("name"), "rect": c["rect"], "candidates": cands.get(c["id"], 0),
                       "picks": picks.get(c["id"], 0)} for c in m["crops"] if c.get("id") not in ids]
    return out


def layout_start(root: str) -> dict:
    """POST /layout/start: 매니페스트 크롭 → layout.json(D1 이관, rev = 이력 최대 + 1 — 처음이면 0). 이미 있으면 LayoutConflict(409),
    베이스가 없으면 400.
    레이아웃 모드 결과 매니페스트(source.layout — layout.json 을 치운 뒤)는 크롭 값이 Studio 값이라 PSD 기준점을 믿을 수 있는
    스냅숏(_read_snapshot)으로 만든다 — 없으면 store.layout_from_manifest 의 ValueError(400).
    warnings = validate_layout 경고(레이어명 '|' 뒤가 비어 레거시로 바뀐 크롭 등 — 편집기가 안내)."""

    def fill(lay):
        m = store.load_manifest(root)
        if not m.get("base") or not m.get("canvas"):
            raise _Reject(400, "베이스가 없습니다 — Import PSD 를 먼저 실행하세요")
        lay.update(store.layout_from_manifest(m, _read_snapshot(root, m)))
        return len(lay["crops"]), store.validate_layout(lay)

    (n, warns), rev = store.update_layout(root, fill, expect_rev=None, create=True)
    return {"ok": True, "rev": rev, "crops": n, "warnings": warns}


def _targets_in(v) -> list[dict]:
    """요청의 targets → [{"tid", "text"(그대로 — 규칙 검사는 _target_text), "box"}]. 구조(목록·객체·tid)만 본다. 다른 키(adopted 등)는
    버린다. tid 중복·box 형식은 update_layout 의 validate_layout 이 막는다."""
    if not isinstance(v, list) or len(v) > 26:
        raise _Reject(400, "targets 는 26개 이하의 목록이어야 합니다")
    out = []
    for t in v:
        if not isinstance(t, dict):
            raise _Reject(400, "targets 항목은 {tid, text, box} 객체여야 합니다")
        out.append({"tid": _field(t, "tid", _LAYOUT_TID_RE), "text": t.get("text", ""), "box": t.get("box")})
    return out


def _target_text(t: dict, keep: list[dict]) -> str:
    """대상 문구: keep(update 의 지금 크롭·restore_rev 판의 크롭)의 같은 tid 에 똑같은 문구가 있으면 그대로 — 이관된 규칙 밖 문구
    (긴 문구·제어 문자)가 다른 대상의 상자·추가를 막지 않게(왕복은 validate_layout 이 지킴). 아니면 store.check_target_text(정규화)."""
    if any(o["tid"] == t["tid"] and o["text"] == t["text"] for c in keep for o in c["targets"]):
        return t["text"]
    try:
        return store.check_target_text(t["text"])
    except ValueError as e:
        raise _Reject(400, f"대상 {t['tid']}: {e}") from None


def _crop_in_layout(lay: dict, crop_id: str) -> dict:
    crop = next((c for c in lay["crops"] if c["id"] == crop_id), None)
    if crop is None:
        raise _Reject(404, f"레이아웃에 크롭이 없습니다: {crop_id}")
    return crop


def _restore_point(root: str, restore_rev: int | None, crop_id: str | None) -> tuple[int | None, dict | None]:
    """실행 취소가 되살리는 판(store.load_layout_version — 그 쓰기 직전 rev)에서 crop_id 크롭의 (위치, 크롭). 판·크롭이 없으면 (None, None)."""
    ver = store.load_layout_version(root, restore_rev) if restore_rev is not None and crop_id else None
    for i, c in enumerate(ver["crops"] if ver else []):
        if c["id"] == crop_id:
            return i, c
    return None, None


def _opt_count(data: dict, name: str) -> int | None:
    """선택 인자(없으면 None) = 0 이상 정수."""
    v = data.get(name)
    if v is not None and (isinstance(v, bool) or not isinstance(v, int) or v < 0):
        raise _Reject(400, f"{name} 는 0 이상의 정수여야 합니다")
    return v


def _fix_before_rename(root: str, rev: int, crop_id: str, name) -> bool:
    """S1 이름 바꾸기 앞의 레퍼런스 고정(STUDIO_S2_SPEC §2.6): refs_dir 폴더가 있고 이 크롭이 refs.json 에 고정 안 됐고 이름이 실제로
    바뀌면, 바꾸기 전 이름의 이름 매칭으로 why "rename" 고정(drefs.fix_link — refs.json 이 없으면 만듦, fixed.auto — 사용자가 레퍼런스를
    고르지 않은 부수 효과 고정이라 빈 seed 는 의도한 '없음'이 아님, _auto_fixed). update_refs 를 끝낸 뒤에
    update_layout 이 돈다(잠금 안에서 둘을 겹치지 않음). 고정 쓰기가 실패하면(깨진 refs.json 400) 예외가 올라가 이름도 바꾸지 않는다.
    레이아웃 rev 가 요청과 다르거나 새 이름이 규칙 밖·중복이면 아무것도 하지 않는다(update_layout 이 409·400 으로 거절).
    refs.json 의 refs_dir 가 지금 것과 다르면(연결을 무시하는 중) 고정하지 않는다. → 이 호출이 고정을 만들었으면 True — 뒤의 update_layout 이
    거절하면(rect·대상 규칙 400·rev 409) layout_crop 이 _unpin_rename 으로 되돌린다(codex12). 셋 다 layout_crop 이 _refs_write_lock 안에서(codex13)."""
    refs_dir = _refs_dir(store.load_manifest(root))
    lay = store.load_layout(root)
    if not _refs_dir_ok(refs_dir) or lay is None or lay["rev"] != rev:
        return False
    crop = next((c for c in lay["crops"] if c["id"] == crop_id), None)
    if crop is None or crop.get("locked"):  # 잠긴 크롭은 update_layout 이 423 으로 거절 — refs.json 을 건드리지 않게 먼저 뺀다
        return False
    try:
        new = store.check_crop_name(name, [c["name"] for c in lay["crops"] if c is not crop])["name"]
    except ValueError:
        return False
    doc = drefs.load_refs(root)
    if new == crop["name"] or (doc is not None and (crop_id in doc["links"] or not drefs.same_refs_dir(doc["refs_dir"], refs_dir))):
        return False
    old = {"id": crop_id, "name": crop["name"]}
    made = []

    def fix(d):
        made.clear()
        if crop_id not in d["links"]:  # 사용자가 레퍼런스를 고르지 않은 고정 — 빈 seed 면 의도한 '없음'이 아님(_auto_fixed)
            drefs.fix_link(d, refs_dir, old, "rename")["fixed"]["auto"] = True
            made.append(True)

    try:
        _write_refs(root, doc["rev"] if doc else None, refs_dir, fix)
    except drefs.RefsConflict:  # 그 사이 다른 프로세스의 refs.json 쓰기(만들기 경합 포함) — 레이아웃 쓰기라 레퍼런스 rev 를 모르므로 지금 판에 한 번 더
        now = drefs.load_refs(root)
        _write_refs(root, now["rev"] if now else None, refs_dir, fix)
    return bool(made)


def _unpin_rename(root: str, crop_id: str, name: str | None = None) -> None:
    """레이아웃 쓰기가 거절된 이름 바꾸기의 고정(_fix_before_rename 이 만든 것)을 되돌린다 — layout_crop 의 _refs_write_lock 안이라 그 사이 다른
    창의 레퍼런스 쓰기·이름 바꾸기는 끼지 않는다(why rename·auto 가 아니면 그대로). 만든 refs.json 은 남는다(빈 links = 연결 없음과 같음).
    name = restore_manifest 로 되돌린 이름 — 고정이 그 이름(바꾸기 전 이름)으로 만든 것일 때만 푼다.
    다른 프로세스의 refs.json 쓰기와 겹치면 지금 판에 한 번 더."""
    refs_dir = _refs_dir(store.load_manifest(root))

    def undo(d):
        fx = (d["links"].get(crop_id) or {}).get("fixed") or {}
        if fx.get("why") == "rename" and fx.get("auto") and (name is None or fx.get("name") == name):
            del d["links"][crop_id]

    for _ in range(2):
        doc = drefs.load_refs(root)
        if doc is None or crop_id not in doc["links"]:
            return
        try:
            _write_refs(root, doc["rev"], refs_dir, undo)
            return
        except drefs.RefsConflict:
            continue


def layout_crop(root: str, rev: int, op: str, data: dict) -> dict:
    """POST /layout/crop(§4.4): add {name, rect, targets?, note?, id?, psd_name?, index?} · update {id + 바꿀 것만(name·rect·targets·note·locked)} ·
    delete {id}. 바꾸는 필드만 검사한다 — 이관된 비정상 rect(캔버스 밖·비 > 3)·옛 이름을 가진 크롭도 다른 칸은 고칠 수 있다.
    이름 = store.check_crop_name(레이아웃 안에서 유일), rect = store.check_layout_rect, targets 는 통째로 바꾼다(문구는 _target_text).
    새 크롭 id = store.new_layout_id(이후 이름을 바꿔도 유지), psd_name 은 없음(= 이름으로 PSD 사전 편집 그룹을 찾음).
    add 의 id·psd_name = 편집기 실행 취소의 지운 크롭 되살리기: 레이아웃에서 쓰지 않는 id 면 그 id 로(스펙·후보·★ 가 이어짐),
    쓰는 중이면 new_layout_id. psd_name 은 그대로 둔다(이름을 바꾼 크롭도 사전 편집 그룹을 계속 찾음).
    restore_rev(선택, 실행 취소) = 되돌리는 쓰기 직전 rev: 그 판의 같은 id 크롭과 값이 같은 name·rect·대상 문구는 규칙 검사를
    건너뛴다 — 이관된 비정상 값(규칙 밖 이름·이름 중복·캔버스 밖·비 > 3·긴 문구)도 되돌릴 수 있게(구조는 validate_layout,
    이름 중복은 그 경고). add 의 위치 = index(선택, 넘으면 끝), 없으면 그 판의 위치, 그것도 없으면 끝 — crops 순서 = 매니페스트
    순서(§1.2)라 되살린 크롭이 화풍 초안·보고서 순서를 바꾸지 않게.
    restore_manifest(선택, update 만 — 진행(G) 반영 칸의 [되돌리기]) = restore_rev 대신 마지막 반영(매니페스트)의 같은 id 크롭을 되돌릴
    판으로: 보낸 name·rect 는 그 값과 같아야 하고(다르면 409 — 그 사이 반영으로 매니페스트가 바뀜) 값 규칙 검사를 건너뛴다. 이름 유일성은
    검사(이관 중복만 허용), 이름 되돌리기는 레퍼런스를 새로 고정하지 않고 그 이름의 자동 고정을 푼다(매니페스트에 그 크롭이 없으면 404).
    locked(선택, update 만 — 배치 3-3) = 크롭 잠금(true 면 layout.json 에 locked true, false 면 키를 뺌). 잠긴 크롭(디스크의 지금 값)에는
    locked 만 바꾸는 update 만 받는다 — 다른 필드·restore_rev·restore_manifest 가 섞인 update 와 delete 는 423(오래된 화면의 쓰기도).
    반환 {ok, rev, crop: 그 크롭의 /layout 행 | null(delete), checks: {crop_id: 검사}} — 겹침 등 다른 크롭의 검사도 바뀐다. delete 는
    + index(지운 위치). 쓰기 뒤 응답을 만들지 못하면(쓰기는 됨) crop null·checks {}·warning."""
    if op not in ("add", "update", "delete"):
        raise _Reject(400, "op 는 add, update, delete 중 하나여야 합니다")
    crop_id = _field(data, "id", _CROP_ID_RE, required=op != "add")
    fields = [f for f in ("name", "rect", "targets", "note") if f in data]
    if op == "add" and not {"name", "rect"} <= set(fields):
        raise _Reject(400, "add 에는 name 과 rect 가 있어야 합니다")
    locked = data.get("locked")
    if locked is not None and (not isinstance(locked, bool) or op != "update"):
        raise _Reject(400, "locked 는 true·false — update 에만")
    if op == "update" and not fields and locked is None:
        raise _Reject(400, "update 에 바꿀 것(name·rect·targets·note·locked)이 없습니다")
    restore_rev = _opt_count(data, "restore_rev")
    restore_manifest = data.get("restore_manifest", False)
    if not isinstance(restore_manifest, bool) or (restore_manifest and (op != "update" or restore_rev is not None)):
        raise _Reject(400, "restore_manifest 는 true·false — update 에만, restore_rev 와 함께 쓰지 않음")
    index = _opt_count(data, "index") if op == "add" else None
    targets = _targets_in(data["targets"]) if "targets" in data and op != "delete" else None
    rename = op == "update" and "name" in data
    restored: list[str] = []  # restore_manifest 로 되돌린 이름(쓰기가 커밋되면 이름 바꾸기 자동 고정을 푼다)

    def mutate(lay):
        crops = lay["crops"]
        # 잠긴 크롭(3-3)은 디스크의 지금 값으로 판정 — 저장 안 함(이름 바꾸기 고정은 아래 except 가 되돌림)
        cur = None if op == "add" else _crop_in_layout(lay, crop_id)
        if cur and cur.get("locked") and (op == "delete" or fields or restore_rev is not None or restore_manifest):
            raise _Reject(423, f"잠긴 크롭({cur['name']}) — 잠금을 풀고 다시")
        if op == "delete":
            at = crops.index(_crop_in_layout(lay, crop_id))
            del crops[at]
            return None, at
        pos, old = _restore_point(root, restore_rev, crop_id)
        mnames: dict = {}  # 매니페스트 crop id → 이름(되돌리는 이름이 지금 다른 크롭과 겹치는지)
        if restore_manifest:
            mcrops = store.load_manifest(root)["crops"]
            mc = next((c for c in mcrops if c.get("id") == crop_id), None)
            if mc is None:
                raise _Reject(404, f"마지막 반영(매니페스트)에 크롭이 없습니다: {crop_id}")
            old = {"name": mc.get("name"), "rect": list(mc["rect"]), "targets": []}
            mnames = {c.get("id"): c.get("name") or "" for c in mcrops}
            # 보낸 이름·사각형 = 지금 마지막 반영 값이어야 함 — 줄을 그린 뒤 다른 창의 반영이 매니페스트를 바꿨으면(layout_rev 는 그대로) 옛 값으로
            # 이미 반영된 크롭을 다시 바꾸지 않게(Codex 2차)
            moved = [f for f in ("name", "rect") if f in data and data[f] != old[f]]
            if moved:
                raise _Reject(409, f"마지막 반영 값이 바뀌었습니다({'·'.join(moved)}) — 다른 창의 반영 등. 다시 그린 줄에서 다시")
        crop = {"id": None, "name": None, "rect": None, "targets": [{"tid": "A", "text": "", "box": None}],
                "psd_name": data.get("psd_name"), "note": ""} if op == "add" else _crop_in_layout(lay, crop_id)
        if "name" in data:
            name = (data["name"] if old and data["name"] == old["name"]
                    else store.check_crop_name(data["name"], [c["name"] for c in crops if c is not crop])["name"])
            if restore_manifest and name == old["name"]:
                # 되돌리기는 값 규칙(이관된 규칙 밖 이름)만 건너뛴다 — 지금 다른 크롭이 그 이름이면 거절(그 크롭도 마지막 반영에서 같은 이름이던
                # 이관 중복은 그대로 둠). 이름을 맞바꾼 두 크롭은 다른 쪽을 먼저 되돌리면 됨
                key = _name_fold(name)
                clash = [c for c in crops if c is not crop and _name_fold(c["name"]) == key and _name_fold(mnames.get(c["id"], "")) != key]
                if clash:
                    o = clash[0]
                    raise _Reject(400, f"다른 크롭(ID {o['id']}" + (f", 마지막 반영 이름 {mnames[o['id']]}" if o["id"] in mnames else ", 새 크롭")
                                  + f")이 지금 {name} 이름을 씀 — 그 크롭을 먼저 되돌리거나 편집(E)에서 이름을 바꾼 뒤 다시")
                restored.append(name)
            if op == "update" and crop.get("psd_name") is None and name != crop["name"]:
                crop["psd_name"] = crop["name"]  # Studio 에서 만든 크롭(psd_name 없음 = 이름으로 찾음)도 이름을 바꾸면 사전 편집은 처음 이름으로(D3)
            crop["name"] = name
        if "rect" in data:
            crop["rect"] = data["rect"] if old and data["rect"] == old["rect"] else store.check_layout_rect(data["rect"], lay["canvas"])
        if targets is not None:
            keep = [c for c in (crop if op == "update" else None, old) if c]
            crop["targets"] = [dict(t, text=_target_text(t, keep)) for t in targets]
        if "note" in data:
            crop["note"] = data["note"]
        if locked:
            crop["locked"] = True
        elif locked is False:
            crop.pop("locked", None)
        if op == "add":
            taken = [c["id"] for c in crops]
            crop["id"] = crop_id if crop_id and crop_id not in taken else store.new_layout_id(crop["name"], taken)
            at = index if index is not None else pos if pos is not None else len(crops)
            crops.insert(min(at, len(crops)), crop)
        return crop["id"], None

    # 이름 바꾸기 = 고정 → 레이아웃 쓰기 → (거절이면) 되돌리기를 레퍼런스 쓰기 잠금 하나 안에서 — 두 창이 같은 크롭 이름을 같은 rev 에서 동시에
    # 바꾸면 실패한 쪽의 되돌리기가 성공한 쪽에 필요한 고정을 지울 수 있었다(codex13). 잠금 순서 = 이것 → manifest_lock(_write_refs 와 같음)
    with _refs_write_lock(root) if rename else contextlib.nullcontext():
        # 마지막 반영 이름으로 되돌리기는 고정하지 않는다 — 이름 매칭이 반영 때와 같아지는 것이 되돌리기
        pinned = rename and not restore_manifest and _fix_before_rename(root, rev, crop_id, data["name"])
        try:
            (cid, gone_at), new_rev = store.update_layout(root, mutate, expect_rev=rev)
        except Exception:
            if pinned:  # 이름은 그대로인데 고정만 남으면 같은 번호로 새로 넣는 파일이 말없이 빠진다(codex12)
                try:
                    _unpin_rename(root, crop_id)
                except Exception:  # 되돌리기 실패가 원래 거절(400·409)을 가리지 않게
                    logger.exception(f"{_TAG} Board: 거절된 이름 바꾸기의 레퍼런스 고정을 되돌리지 못함 {crop_id}")
            raise
        if restored:  # 이름을 마지막 반영 값으로 되돌림 — 실수 이름 바꾸기가 남긴 자동 고정(그 이름 기준)도 풀어 이름 매칭으로(되돌리기 전과 같게)
            try:
                _unpin_rename(root, crop_id, restored[0])
            except Exception:  # 레이아웃 쓰기는 커밋됨 — 고정이 남으면 '레퍼런스 미반영'으로 보일 뿐
                logger.exception(f"{_TAG} Board: 되돌린 이름의 레퍼런스 자동 고정을 풀지 못함 {crop_id}")
    out = {"ok": True, "rev": new_rev}
    if op == "delete":
        out["index"] = gone_at
    try:
        view = layout_state(root)
    except Exception as e:  # 쓰기는 이미 커밋됨 — 500 으로 알리면 편집기가 같은 쓰기를 낡은 rev 로 되풀이(409)한다
        logger.exception(f"{_TAG} Board: 레이아웃 쓰기(rev {new_rev}) 뒤 응답 구성 실패 {os.path.basename(root)}")
        return {**out, "crop": None, "checks": {}, "warning": f"응답 구성 실패: {e}"}
    return {**out, "crop": next((c for c in view["crops"] if c["id"] == cid), None),
            "checks": {c["id"]: c["checks"] for c in view["crops"]}}


def _snap_token(snap: dict) -> str:
    """스냅숏 표시(지문·at)를 문자열로 — 지문의 mtime_ns 는 JS 안전 정수(2^53)를 넘어, 숫자로 브라우저를 오가면 반올림돼 늘 달라 보인다."""
    return json.dumps([snap.get("fingerprint"), snap.get("at")])


def _shape(c: dict) -> dict:
    return {"name": c["name"], "layer_name": c["layer_name"], "rect": list(c["rect"])}


def layout_psd(root: str, rev: int, op: str, names: list[str] | None, seen: str | None = None) -> dict:
    """POST /layout/psd(§4.5, S1-a 이름별 갱신 — STUDIO_S3_SPEC §2.6): 믿을 수 있는 PSD 스냅숏(_read_snapshot)이 기준점(layout.psd)과
    갈라졌을 때. 고를 수 있는 이름: take = 바뀐·새 shape, ack = 바뀐·새·사라진 shape(사라진 shape 의 경고는 ack 로만 끈다).
    names 없음 = 그 op 의 전부(기준점은 사라진 shape 까지 스냅숏 통째 — 지금과 같음), names [] = 400, 범위 밖 이름 = 400, take 에
    사라진 shape 뿐인 이름 = 400(경고 끄기로).
    take = 고른 이름의 PSD 값을 가져온다: 바뀐 shape → psd_name(없으면 name)이 같은 레이아웃 크롭(여럿이면 기준점 rect 와 IoU 가 큰
    것)의 PSD 에서 바뀐 필드만 — rect 면 rect, layer_name 이면 대상(레이어명 파싱, 같은 tid 의 상자는 유지) — Studio 에서 고친 나머지는
    그대로. 새 shape → 크롭 추가(id 해시). 짝 크롭을 레이아웃에서 지운 바뀐 shape 는 skipped. ack = 크롭은 그대로 두고 경고만 끈다.
    기준점은 고른 이름만 스냅숏 값 — 그 이름의 항목을 모두 빼고 스냅숏의 같은 이름 항목 전부(스냅숏 순서)를 그 이름의 첫 항목
    자리에(없던 이름이면 끝에) 넣는다(skipped 이름도). psd_diff 는 이름 안에서만 짝지으므로 고른 이름의 차이는 0, 남은 갈라짐은
    계속 경고. 메타(path·fingerprint·crop_group·at)는 갱신 뒤 차이가 0 일 때만 기준점 = 스냅숏 통째, 아니면 그대로(노드 Import 가
    스냅숏을 잃었을 때 PSD 를 다시 열게).
    seen = 사용자가 본 스냅숏의 token(GET /layout 의 psd_diff.token) — 지금 스냅숏과 다르면 LayoutConflict(409, 새로고침). 기준점이
    아니라 스냅숏의 표시라 부분 갱신 뒤에도 같은 token 으로 다시 쓸 수 있다.
    take 은 잠긴 크롭(locked — 배치 3-3)이 쓰는 PSD 이름(psd_name, 없으면 name)을 고른 이름에서 뺀다: 크롭도 기준점도 그대로라 그 크롭의
    D-psd 경고가 남고, 뺀 이름은 locked(있을 때만)로 보고. ack 는 크롭을 바꾸지 않아 잠금과 무관.
    스냅숏이 없거나 믿을 수 없거나 차이가 없으면 400. → {ok, rev, taken: [{id, name, fields}], added, skipped, locked?}"""
    if op not in ("take", "ack"):
        raise _Reject(400, "op 는 take, ack 중 하나여야 합니다")
    if names is not None and not names:
        raise _Reject(400, "고른 shape 가 없습니다")

    def mutate(lay):
        snap = _read_snapshot(root, store.load_manifest(root))
        if snap is None:
            raise _Reject(400, f"{_SNAP_UNTRUSTED} — Import PSD 가 PSD 를 연 뒤에 쓸 수 있습니다")
        if seen is not None and seen != _snap_token(snap):
            raise store.LayoutConflict("PSD 스냅숏이 바뀌었습니다 — 새로고침")
        diff = store.psd_diff(lay["psd"], snap)
        if not any(diff.values()):
            raise _Reject(400, "PSD 사각형이 기준점과 같습니다(갈라짐 없음)")
        takeable = [x["name"] for x in diff["changed"] + diff["added"]]
        gone = [x["name"] for x in diff["removed"]]
        avail = takeable + gone if op == "ack" else takeable
        want = set(avail if names is None else names)
        if op == "take" and want & set(gone) - set(takeable):
            raise _Reject(400, f"사라진 shape 는 가져올 수 없습니다 — 경고 끄기로: {sorted(want & set(gone) - set(takeable))}")
        if not want <= set(avail):
            raise _Reject(400, f"PSD 차이에 없는 shape 입니다: {sorted(want - set(avail))}")
        if names is None:  # 전부 = 지금처럼 기준점 = 스냅숏 통째(take 도 사라진 shape 경고를 끔 — §0 S1-a), 크롭 쪽은 바뀐·새 shape 만
            want |= set(gone)
        # 잠긴 크롭(3-3)의 PSD 이름(psd_name, 없으면 name)은 가져오지 않는다 — 기준점 갱신 전에 빼서 그 크롭의 'PSD 와 다름' 경고가 남게
        held = sorted(want & {c.get("psd_name") or c["name"] for c in lay["crops"] if c.get("locked")}) if op == "take" else []
        want -= set(held)
        taken, added, skipped = [], [], []
        if op == "take":
            for ch in diff["changed"]:
                if ch["name"] not in want:
                    continue
                mates = [c for c in lay["crops"] if (c.get("psd_name") or c["name"]) == ch["name"]
                         and all(c["id"] != t["id"] for t in taken)]
                if not mates:
                    skipped.append(ch["name"])
                    continue
                crop = max(mates, key=lambda c: _rect_iou(c["rect"], ch["from"]["rect"]))
                if "rect" in ch["fields"]:
                    crop["rect"] = list(ch["to"]["rect"])
                if "layer_name" in ch["fields"]:
                    boxes = {t["tid"]: t.get("box") for t in crop["targets"]}
                    crop["targets"] = [{"tid": t["tid"], "text": t["text"], "box": boxes.get(t["tid"])}
                                       for t in store.parse_crop_layer_name(ch["to"]["layer_name"])["targets"]]
                taken.append({"id": crop["id"], "name": crop["name"], "fields": ch["fields"]})
            for a in diff["added"]:
                if a["name"] not in want:
                    continue
                cid = store.new_layout_id(a["name"], [c["id"] for c in lay["crops"]])
                lay["crops"].append({"id": cid, "name": a["name"], "rect": list(a["rect"]),
                                     "targets": [{"tid": t["tid"], "text": t["text"], "box": None}
                                                 for t in store.parse_crop_layer_name(a["layer_name"])["targets"]],
                                     "psd_name": a["name"], "note": ""})
                added.append(cid)
        new_shapes = {}
        for c in snap["crops"]:
            if c["name"] in want:
                new_shapes.setdefault(c["name"], []).append(_shape(c))
        base = []
        for c in lay["psd"]["crops"]:
            if c["name"] not in want:
                base.append(c)
            elif c["name"] in new_shapes:  # 그 이름의 첫 항목 자리에 스냅숏 항목 전부(사라진 이름은 빠짐)
                base += new_shapes.pop(c["name"])
        base += [s for rows in new_shapes.values() for s in rows]  # 기준점에 없던 이름(새 shape)은 끝에
        lay["psd"]["crops"] = base
        if not any(store.psd_diff(lay["psd"], snap).values()):
            lay["psd"] = {"path": snap.get("psd"), "fingerprint": snap.get("fingerprint"), "crop_group": snap.get("crop_group"),
                          "at": snap.get("at"), "crops": [_shape(c) for c in snap["crops"]]}
        return {"taken": taken, "added": added, "skipped": skipped, **({"locked": held} if held else {})}

    res, new_rev = store.update_layout(root, mutate, expect_rev=rev)
    return {"ok": True, "rev": new_rev, **res}


def layout_refs(root: str, name, crop_id=None) -> dict | None:
    """GET /layout/refs: 이름을 그렇게 바꾸면 고를 레퍼런스(속성 패널 '바꾼 뒤' 미리보기 — /layout 크롭 행 refs 와 같은 꼴).
    이름은 형식만 본다(유일성은 보지 않음, 오류는 store.check_crop_name ValueError → 400). refs_dir 가 없으면 None.
    crop_id(선택)가 refs.json 에 고정된 크롭이거나 고정 안 된 크롭(이름 바꾸기가 바꾸기 전 이름으로 먼저 고정 — S2 §2.6)이면
    이름과 상관없이 그 크롭의 지금 레퍼런스(fixed: true — 연결 유지)."""
    parsed = store.check_crop_name(name)
    src = store.load_manifest(root).get("source") or {}
    find_refs = _refs_finder(src.get("refs_dir"))
    if find_refs is None:
        return None
    if crop_id is not None:
        if not isinstance(crop_id, str) or not _CROP_ID_RE.fullmatch(crop_id):
            raise _Reject(400, "id 형식이 잘못되었습니다")
        lay = store.load_layout(root)
        crop = next((c for c in lay["crops"] if c["id"] == crop_id), None) if lay else None
        if crop is not None:
            doc = drefs.load_refs(root)
            linked = _linked_refs(src.get("refs_dir"), doc, crop)
            if linked is not None:
                return _refs_row(linked["refs_auto"], True)
            if doc is None or drefs.same_refs_dir(doc["refs_dir"], src.get("refs_dir")):
                old = store.parse_crop_layer_name(crop["name"])
                return _refs_row(find_refs(old["nnn"], old["part"]), True)
    return _refs_row(find_refs(parsed["nnn"], parsed["part"]))


# ══════════════════════════════════════════════════════════════════════
# 레퍼런스 (S2 — STUDIO_S2_SPEC §5). 원천 = <root>/refs.json(drefs), 그림 = refs_dir 바로 아래·_보류.
# 쓰기는 모두 drefs.update_refs(expect_rev = 요청 rev, null = 아직 없음 → 만들기). 매니페스트는 읽기만 한다(반영은 다음 큐의 Import PSD).
# 파일을 옮기는 쓰기(save·hold·restore)는 파일 먼저 → refs.json, refs.json 이 실패하면 파일을 되돌린다(지우지 않고 _보류 로).
# rev 검사부터 되돌리기까지 루트별 _refs_write_lock 안에서 한다(변환 계산은 밖).
# ══════════════════════════════════════════════════════════════════════
_REF_SLOTS = threading.BoundedSemaphore(1)   # 시트 디코드·PSD 합성·컷·변환은 한 번에 하나(시트 RGBA 119MB — 부록 A)
_REFS_WRITE_LOCKS: dict[str, threading.RLock] = {}  # root_key → 레퍼런스 쓰기 잠금(_refs_write_lock)
_SAM3_CKPTS: list[str] = []                  # set_sam3_ckpts — 노드 모듈이 import 때 넘긴 SAM3 체크포인트 이름
_QWEN_DIR: list[str] = []                    # set_qwen_dir — 노드 모듈이 import 때 넘긴 local_qwen 모델 폴더(비면 미설정)
_OUTPUT_DIR: list[str] = []                  # set_output_dir — 노드 모듈이 import 때 넘긴 ComfyUI output 폴더(비면 미설정)
_REF_IMG_WHERE = ("refs", "held", "cut", "cutmask")
_REF_IMG_SIZES = (0, 128, 256, 512)
_REF_BGS = {"gray": 128, "white": 255, "check": None}   # GPT 가 받는 매트 #808080 · 흰 · 체크(8px)
_REF_THUMB = 256
_REF_BIG_SIDE, _REF_BIG_BYTES = 4096, 25 * 1024 * 1024  # R-big(D4 — 축소 권장)
_REQ_RE = re.compile(r"[0-9a-f]{32}")
_LINK_OPS = ("fix", "none", "unfix", "add", "main", "move", "remove", "hold", "restore", "relink")
_SIDE_WORDS = {"right": "right", "오른쪽": "right", "left": "left", "왼쪽": "left"}  # vlm._SIDE_WORDS 와 같음
_SIDE_SPLIT_RE = re.compile(r"[^0-9a-z가-힣]+")
_SHA_MEMO: dict[tuple, str] = {}             # (경로, 지문) → pixel_sha — S2 가 저장한 파일이 바깥에서 바뀌었나
_SHA_LOCK = threading.Lock()


def set_sam3_ckpts(names) -> None:
    """GET /refs 의 sam3.ckpts(§5.1)와 POST /refs/cut sam3 의 체크포인트 검사. 노드 모듈이 import 때 부른다 — 보드는 B0 순도 때문에
    folder_paths 를 import 하지 않는다(재시작 전 새로 넣은 파일은 안 보임). 이름에 'sam3' 가 든 것만 둔다
    (bmk_sam3_mask_layers._sam3_checkpoints 는 하나도 없으면 전체 목록을 돌려줌)."""
    _SAM3_CKPTS[:] = [str(n) for n in names if "sam3" in str(n).lower()]


def set_qwen_dir(path) -> None:
    """local_qwen 모델 폴더(노드 모듈이 import 때 — 보드는 folder_paths 를 import 하지 않음). Studio Draft 계획의 local_qwen 키
    (config.json sha8)에 쓴다(STUDIO_S3_SPEC §2.4). 비어 있으면(재시작 전·노드 모듈 없음) local_qwen 계획은 오류 — 키를 'nodir' 로
    계산해 노드와 다른 해시를 보이지 않게."""
    _QWEN_DIR[:] = [str(path)] if path else []


def _qwen_dir() -> str:
    return _QWEN_DIR[0] if _QWEN_DIR else ""


def set_output_dir(path) -> None:
    """ComfyUI output 폴더(노드 모듈이 import 때 — 보드는 folder_paths 를 import 하지 않음). Export 는 그 아래
    design_patch/<하위 폴더 — 비면 프로젝트 폴더>에 쓰고(노드 _output_dir — 배치 3-5b), POST /open_folder 는 이 안의 경로만 연다."""
    _OUTPUT_DIR[:] = [os.path.abspath(str(path))] if path else []


def _reveal(path: str, select: bool) -> None:
    """탐색기로 열기 — select = 그 파일을 고른 채(Windows explorer /select). 기다리지 않음(explorer 는 성공해도 1 을 돌려줌).
    시험은 이 함수를 바꿔 끼운다."""
    if os.name == "nt":
        subprocess.Popen(f'explorer /select,"{path}"' if select else f'explorer "{path}"')
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", path] if select else ["open", path])
    else:
        subprocess.Popen(["xdg-open", os.path.dirname(path) if select else path])


def open_export_folder(root: str) -> dict:
    """POST /open_folder(배치 3-5a): 마지막 Export PSD 가 있으면 그것을 고른 채(기록 경로 그대로 — 그때의 하위 폴더), 없으면 다음
    Export 가 쓸 출력 폴더(output/design_patch/<실행 설정 export.subfolder — 비면 프로젝트 폴더 이름>, 배치 3-5b)를 연다.
    ComfyUI output 밖(손으로 고친 매니페스트의 다른 경로)은 열지 않는다."""
    if not _OUTPUT_DIR:
        raise _Reject(409, "ComfyUI 를 재시작해야 출력 폴더를 열 수 있습니다(서버가 출력 폴더를 모름)")
    base = os.path.realpath(_OUTPUT_DIR[0])

    def inside(p: str) -> bool:
        # 실제 경로로(junction·심볼릭이 output 밖을 가리키면 밖 — Codex 3a 3차)
        try:
            return os.path.normcase(os.path.commonpath([base, os.path.realpath(p)])) == os.path.normcase(base)
        except ValueError:  # 다른 드라이브
            return False

    m = store.load_manifest(root)
    last = (store.last_export(root, m) or {}).get("psd")  # 캐시 재사용 Export 도(exports 끝이 아닐 수 있음 — Codex 3a 2차)
    psd = os.path.abspath(last) if isinstance(last, str) and last else None
    if psd and inside(psd) and os.path.isfile(psd):
        _reveal(psd, True)
        return {"ok": True, "path": psd, "selected": True}
    settings = pl.current(root, m)["settings"]  # 파일이 없으면 유도값, 깨졌으면 None(기본 폴더) — 값은 pipeline 이 이름 검사를 마친 것
    sub = settings["export"]["subfolder"] if settings else ""
    folder = os.path.join(base, "design_patch", sub or os.path.basename(os.path.normpath(root)))
    if not os.path.isdir(folder):
        raise _Reject(404, f"출력 폴더가 아직 없습니다(Export 를 한 번 하면 생김): {folder}")
    if not inside(folder):
        raise _Reject(400, f"출력 폴더가 ComfyUI output 밖을 가리킵니다(링크) — 열지 않음: {folder}")
    _reveal(folder, False)
    return {"ok": True, "path": folder, "selected": False}


def _refs_doc(root: str) -> tuple[dict | None, str | None]:
    """(refs.json, 깨진 이유). 없으면 (None, None), 깨졌으면 (None, 메시지 — refs_history 복구 안내)."""
    try:
        return drefs.load_refs(root), None
    except ValueError as e:
        return None, str(e)


def _refs_dir(m: dict) -> str:
    return (m.get("source") or {}).get("refs_dir") or ""


def _refs_dir_ok(refs_dir: str) -> bool:
    return bool(refs_dir) and os.path.isdir(refs_dir)


def _refs_files_v(refs_dir: str) -> str | None:
    """state 의 refs_files_v: refs_dir(정규화 경로)와 그 바로 아래·_보류 의 파일 (이름, 크기, mtime_ns) 목록 해시 16자 | None(refs_dir 폴더
    없음). 2초 폴링마다 부르므로 scandir 두 번(Windows 는 목록 항목의 크기·시각 — 파일마다 stat 호출 없음)."""
    if not _refs_dir_ok(refs_dir):
        return None
    h = hashlib.sha256(drefs.norm_refs_dir(refs_dir).encode("utf-8"))
    for sub in ("", drefs.HELD_DIR):
        try:
            with os.scandir(os.path.join(refs_dir, sub)) as it:
                rows = sorted((e.name, e.stat()) for e in it if e.is_file())
        except OSError:  # _보류 없음 · 읽는 사이 지워짐(다음 폴링에서 다시)
            rows = []
        h.update(json.dumps([sub, [(n, s.st_size, s.st_mtime_ns) for n, s in rows]], ensure_ascii=False).encode("utf-8"))
    return h.hexdigest()[:16]


def _held_rec(doc: dict | None, name: str) -> dict | None:
    """_보류 파일 name 의 refs.json held 기록 {from(원래 이름), crop(보류한 크롭)} — S2 의 보류·대표 교체가 씀 | None(사람이 옮긴 파일 —
    이름 그대로 되살림)."""
    held = (doc or {}).get("held") or {}
    k = _record_key(held, name)
    return held[k] if k else None


def _pick(name: str, r: dict) -> str | None:
    """Prepare 가 고를 대표(§2.2): 고정된 대표가 refs_auto 안이면 그것, 아니면 prompt.auto_pick_ref(이름 규칙)."""
    return r["refs_main"] or (dprompt.auto_pick_ref(name, r["refs_auto"]) if r["refs_auto"] else None)


def _side_of(name: str) -> str | None:
    """vlm.side_of 와 같은 규칙(refs·state 경로라 vlm 을 불러오지 않는다 — S2 B0, vlm 은 계획·초안 라우트의 _vlm() 로만): 이름의 좌우
    토큰(보는 사람 기준), 둘 다 있으면 None."""
    stem = unicodedata.normalize("NFC", os.path.basename(name))
    root_, ext = os.path.splitext(stem)
    if ext.lower() in store.REF_EXTS:
        stem = root_
    found = {_SIDE_WORDS[t] for t in _SIDE_SPLIT_RE.split(stem.lower()) if t in _SIDE_WORDS}
    return found.pop() if len(found) == 1 else None


def _ref_crops(root: str, m: dict) -> list[dict]:
    """S2 의 크롭 목록 [{"id", "name"}]: 레이아웃이 있으면 레이아웃 크롭(지금 이름), 없으면 매니페스트 크롭."""
    lay = _layout_or_none(root)
    return [{"id": c["id"], "name": c["name"]} for c in (lay["crops"] if lay else m["crops"])]


def _crop_of(crops: list[dict], crop_id: str) -> dict:
    crop = next((c for c in crops if c["id"] == crop_id), None)
    if crop is None:
        raise _Reject(404, f"크롭이 없습니다: {crop_id}")
    return crop


def _img_url(rk: str, where: str, name: str, size: int, v: str) -> str:
    return "refs/img?" + urlencode({"root": rk, "where": where, "name": name, "size": size, "v": v})


def _pixel_sha(path: str, fp: list[int], im: Image.Image) -> str:
    k = (path, tuple(fp))
    with _SHA_LOCK:
        hit = _SHA_MEMO.get(k)
    if hit is None:
        hit = store.pixel_sha(im.convert("RGBA"))
        with _SHA_LOCK:
            if len(_SHA_MEMO) > 1024:
                _SHA_MEMO.clear()
            _SHA_MEMO[k] = hit
    return hit


def _ref_file(root: str, refs_dir: str, doc: dict | None, name: str, role: str, held: bool = False) -> dict:
    """GET /refs 의 파일 행 {name, role, size, bytes, origin, missing, outside_edit, thumb_url, record}. origin = "studio"(refs.json files 에
    기록 — 보류된 것은 "_보류/<이름>" 키) | "ps". outside_edit = 기록의 sha(저장한 RGBA 의 pixel_sha)와 지금 파일이 다름(바깥에서 바뀜).
    record = 다시 열기(§4.6)용 기록 {sheet{path, layer, size, changed}, rect, cut, transform} | null(PS 파일·기록이 모자람) — changed = 지금
    시트 파일의 지문이 기록과 다르거나 시트가 없음. 이미지가 아니면(보류의 PSD) 크기·썸네일 없음."""
    key = f"{drefs.HELD_DIR}/{name}" if held else name
    path = os.path.join(refs_dir, drefs.HELD_DIR, name) if held else os.path.join(refs_dir, name)
    files = (doc or {}).get("files") or {}
    rk = _record_key(files, key)  # 이름 매칭 행은 디스크 이름 — S2 가 쓴 기록 키와 대소문자·NFC/NFD 가 달라도 찾음
    rec = files.get(rk) if rk else None
    row = {"name": name, "role": role, "size": None, "bytes": None, "origin": "studio" if rec else "ps",
           "missing": not os.path.isfile(path), "outside_edit": False, "thumb_url": None, "record": None}
    if rec and isinstance(rec.get("sheet"), dict) and isinstance(rec.get("cut"), dict) and rec.get("rect") and rec.get("transform"):
        sh = rec["sheet"]  # 다시 열기(§4.6) — 시트 지문(mtime_ns 는 JS 안전 정수를 넘음)은 빼고 비교 결과만
        try:
            changed = store.file_fingerprint(store.resolve_path(root, sh.get("path"))) != sh.get("fingerprint")
        except ValueError:  # 시트가 없음·경로가 프로젝트 밖
            changed = True
        row["record"] = {"sheet": {"path": sh.get("path"), "layer": sh.get("layer"), "size": sh.get("size"), "changed": changed},
                         "rect": rec["rect"], "cut": rec["cut"], "transform": rec["transform"]}
    if row["missing"]:
        return row
    fp = store.file_fingerprint(path)
    row["bytes"] = fp[1]
    if os.path.splitext(name)[1].lower() not in store.REF_EXTS:
        return row
    try:
        with Image.open(path) as im:
            row["size"] = list(im.size)
            if rec and rec.get("sha"):
                row["outside_edit"] = _pixel_sha(path, fp, im) != rec["sha"]
    except (OSError, Image.DecompressionBombError):  # 깨진 그림 — 이름·용량만
        return row
    row["thumb_url"] = _img_url(store.root_key(root), "held" if held else "refs", name, _REF_THUMB, _sig("refimg", key, fp))
    return row


def _explicit_rows(ex: dict, name: str) -> list[dict]:
    """name 을 스펙·매니페스트 타깃이 이름으로 직접 가리키는 곳 [{file, crop_id, tid, where}] (drefs.explicit_refs 결과에서)."""
    return [{"file": name, "crop_id": cid, "tid": tid, "where": where} for cid, tid, where in ex.get(drefs.ref_key(name), [])]


def _name_users(refs_dir: str, doc: dict | None, crops: list[dict], name: str, skip: str) -> list[dict]:
    """name 을 지금 레퍼런스로 쓰는 다른 크롭 [{file, crop_id, tid: null, where: "link"(고정 연결) | "match"(이름 매칭)}] —
    그 파일을 옮기거나 그 이름에 새 그림을 쓰기 전에 확인한다(§5.7 hold)."""
    k = drefs.ref_key(name)
    out = []
    for c in crops:
        if c["id"] == skip:
            continue
        r = drefs.resolve_refs(refs_dir, c, doc)
        if any(drefs.ref_key(f) == k for f in r["refs_auto"]):
            out.append({"file": name, "crop_id": c["id"], "tid": None, "where": "link" if r["linked"] else "match"})
    return out


def _held_rows(ex: dict, refs_dir: str, name: str) -> list[dict]:
    """hold_file(refs_dir, name) 이 옮겨 갈 자리 "_보류/<name>" 을 스펙·매니페스트가 이름으로 가리키는 곳(그 타깃에 그림이 들어감). 자리가 차
    있으면(drefs.same_name — hold_file 과 같은 판정) hold_file 은 시각 접미 이름으로 옮겨 그 타깃은 그대로라 []."""
    held_dir = os.path.join(refs_dir, drefs.HELD_DIR)
    if os.path.isdir(held_dir) and drefs.same_name(held_dir, name) is not None:
        return []
    return _explicit_rows(ex, f"{drefs.HELD_DIR}/{name}")


def _match_seeds(refs_dir: str, doc: dict | None, crops: list[dict], name: str, skip: str) -> list[tuple[dict, dict]]:
    """refs_dir 바로 아래에 새 이름 name 이 생기면 이름 매칭(match_refs — 'NNN_' 접두)으로 그것을 받게 될 고정 안 된 다른 크롭과 그 seed.
    파일 동작 전에 잡아 대상 크롭과 같은 mutate 에서 why "edit" 로 고정한다 — 그 크롭들의 refs_auto·Prepare 선택·초안 키가 그대로(I5)."""
    nfn = unicodedata.normalize("NFC", name)
    out = []
    for c in crops:
        if c["id"] == skip or (doc is not None and c["id"] in doc["links"]):
            continue
        nnn = store.parse_crop_layer_name(c["name"])["nnn"]
        if nnn and nfn.startswith(store._norm_nnn(nnn) + "_"):
            out.append((c, drefs.seed_link(refs_dir, c)))
    return out


def _need_confirm(data: dict, rows: list[dict], what: str) -> None:
    """rows 의 파일이 모두 data.confirm(파일 이름 목록)에 있지 않으면 409 {error, explicit: rows} — 보드가 확인 창을 띄우고 confirm 에
    넣어 다시 보낸다(§5.6)."""
    confirm = data.get("confirm") or []
    if not isinstance(confirm, list) or not all(isinstance(c, str) for c in confirm):
        raise _Reject(400, "confirm 은 파일 이름 목록이어야 합니다")
    ok = {drefs.ref_key(c) for c in confirm}
    if rows and not all(drefs.ref_key(r["file"]) in ok for r in rows):
        where = ", ".join(f"{r['crop_id']}{'/' + r['tid'] if r['tid'] else ''}({r['where']})" for r in rows)
        raise _Reject(409, f"{what} — 이 파일을 쓰는 곳이 있어 확인이 필요합니다: {where}", explicit=rows)


def refs_state(root: str, since: int | None = None) -> dict:
    """GET /refs 응답(STUDIO_S2_SPEC §5.1). since 가 지금 rev 면 {"unchanged": true, "rev"}.
    최상위: exists·rev·error(깨진 refs.json — 쓰기는 400, 크롭은 이름 매칭으로 보임)·refs_dir(매니페스트 source 그대로)·refs_dir_ok(폴더 있음)·
    outside_project·doc_dir_match(refs.json 의 refs_dir = 지금 것, 없으면 true)·doc_refs_dir·suggest_dir(<project>/refs — 안내의 경로 복사)·
    pending_rerolls·sam3.ckpts·crops·orphans(크롭이 사라진 연결 — relink)·sheets(최근 연 시트 {path, layer, view, v(지금 서명 | null)}).
    크롭 행(레이아웃이 있으면 레이아웃 크롭): id·name·fixed(연결이 쓰이는 중)·auto(부수 효과로 정해진 연결 — _auto_fixed)·pick(Prepare 가 고를 대표)·status(same|pending|new — 매니페스트
    refs_auto·refs_main 대 지금 해석)·files(고정이면 연결 순서, 아니면 이름 매칭 — role main|aux)·unlinked·held(_보류 의 같은 번호 + refs.json
    held 기록의 crop·S2 기록의 for 가 이 크롭인 것 — S1 번호 바꾸기 뒤에도 보임, image, restore_as = 되살릴 이름: held 기록의 원래 이름, 없으면 그대로)·
    explicit·manifest·jobs_ref_sha(그 크롭 jobs 의 첫 ref 입력 sha — 평탄화 그림)·checks(§4.8 R-*) + 고정된 크롭만 match_files·match_pick(고정을
    풀면 쓰일 이름 매칭 결과·Prepare 가 고를 대표 | null — 고정 풀기 확인 창). 문자열은 그대로(보드는 textContent)."""
    m = store.load_manifest(root)
    doc, error = _refs_doc(root)
    if doc is not None and since == doc["rev"]:
        return {"unchanged": True, "rev": doc["rev"]}
    refs_dir = _refs_dir(m)
    ok = _refs_dir_ok(refs_dir)
    match = doc is None or drefs.same_refs_dir(doc["refs_dir"], refs_dir)
    base = os.path.normcase(os.path.abspath(root))
    try:
        outside = bool(refs_dir) and os.path.commonpath([base, os.path.normcase(os.path.abspath(refs_dir))]) != base
    except ValueError:  # 다른 드라이브
        outside = True
    crops = _ref_crops(root, m)
    mcrops = {c.get("id"): c for c in m["crops"]}
    ex = drefs.explicit_refs(root, m)
    held_dir = os.path.join(refs_dir, drefs.HELD_DIR)
    held_names = sorted(os.listdir(held_dir)) if ok and os.path.isdir(held_dir) else []
    display = {drefs.ref_key(n): n for n in (os.listdir(refs_dir) if ok else [])}
    display.update({drefs.ref_key(f"{drefs.HELD_DIR}/{n}"): f"{drefs.HELD_DIR}/{n}" for n in held_names})
    prov = doc if doc is not None and match else None
    pfiles = (prov or {}).get("files") or {}
    hinfo = {}  # 보류 파일 → (되살릴 이름, 그 파일의 크롭 ID — held 기록의 crop·S2 기록의 for): 번호를 바꾼 크롭에도 보이게(codex6)
    for n in held_names:
        hr = _held_rec(prov, n)
        fk = _record_key(pfiles, f"{drefs.HELD_DIR}/{n}")
        hinfo[n] = (hr["from"] if hr else n, {hr.get("crop") if hr else None, pfiles[fk].get("for") if fk else None})
    rows = []
    for crop in crops:
        cid, name = crop["id"], crop["name"]
        r = (drefs.resolve_refs(refs_dir, crop, doc) if ok else
             {"refs_auto": [], "refs_main": None, "linked": False, "missing": [], "unlinked": []})
        link = doc["links"][cid] if r["linked"] else None
        pick = _pick(name, r)
        files = [_ref_file(root, refs_dir, doc, f, "main" if f == (link["main"] if link else pick) else "aux")
                 for f in (link["files"] if link else r["refs_auto"])]
        nnn = store.parse_crop_layer_name(name)["nnn"]
        held = [dict(_ref_file(root, refs_dir, doc, n, "held", held=True), image=os.path.splitext(n)[1].lower() in store.REF_EXTS,
                     restore_as=hinfo[n][0])
                for n in held_names if (nnn and unicodedata.normalize("NFC", n).startswith(nnn + "_")) or cid in hinfo[n][1]]
        # 이 크롭의 파일·보류 파일을 이름으로 가리키는 곳(보류 파일도 스펙이 "_보류/<이름>" 으로 가리키면 Prepare 가 읽는다)
        mine = {drefs.ref_key(f["name"]) for f in files} | {drefs.ref_key(f"{drefs.HELD_DIR}/{h['name']}") for h in held}
        explicit = []
        for k, hits in ex.items():
            for hit_cid, tid, where in hits:
                row = {"file": display.get(k, k), "crop_id": hit_cid, "tid": tid, "where": where}
                if (hit_cid == cid or k in mine) and row not in explicit:
                    explicit.append(row)
        mc = mcrops.get(cid)
        status = ("new" if mc is None else
                  "same" if (list(mc.get("refs_auto") or []), mc.get("refs_main")) == (r["refs_auto"], r["refs_main"]) else "pending")
        checks = []
        if ok and not r["refs_auto"]:
            checks.append(_r_none(link, nnn))
        if link and link["main"] and r["refs_main"] is None:
            checks.append({"code": "R-main", "level": "warn",
                           "msg": f"대표 파일 {link['main']} 이 없음(사라짐·보류) — Prepare 가 이름 규칙으로 {pick or '없음'} 을 고름"})
        checks += [{"code": "R-missing", "level": "warn", "msg": f"연결된 파일이 사라짐: {f}"} for f in r["missing"]]
        if doc is not None and not match and cid in doc["links"]:
            checks.append({"code": "R-dir", "level": "warn",
                           "msg": f"refs.json 의 refs_dir({doc['refs_dir']})가 지금 refs_dir 와 달라 연결을 무시하고 이름으로 찾음"})
        if r["unlinked"]:  # 부수 효과로 고정된 크롭은 PS 로 넣은 파일을 그 크롭에서 고르지 않았는데 못 받음 → 경고
            auto = _auto_fixed(link)
            checks.append({"code": "R-unlinked", "level": "warn" if auto else "info",
                           "msg": f"같은 번호인데 연결 안 된 파일 {len(r['unlinked'])}개(Studio 에서 연결): {', '.join(r['unlinked'])}"
                                  + (" — 다른 크롭의 동작이 이 크롭을 자동 고정해 이름 매칭으로 받지 않음" if auto else "")})
        own = [e for e in explicit if e["crop_id"] == cid]
        if own:
            checks.append({"code": "R-explicit", "level": "info",
                           "msg": "스펙·매니페스트가 이름으로 직접 가리킴(그 타깃은 연결 대신 그것을 씀): "
                                  + ", ".join(f"{e['tid']} {e['where']} {e['file']}" for e in own)})
        checks += [{"code": "R-big", "level": "info", "msg": f"큰 레퍼런스(긴 변 > {_REF_BIG_SIDE} 또는 25MB 초과) — 축소 권장: {f['name']}"}
                   for f in files if (f["size"] and max(f["size"]) > _REF_BIG_SIDE) or (f["bytes"] or 0) > _REF_BIG_BYTES]
        side = _side_of(name)
        checks += [{"code": "R-side", "level": "info", "msg": f"레퍼런스 이름의 좌우가 크롭과 반대 — 초안에서 뒤로 밀림: {f['name']}"}
                   for f in files if side and _side_of(f["name"]) not in (None, side)]
        if status != "same":
            checks.append({"code": "R-pending", "level": "info", "msg": "미반영 — 반영(큐) 뒤 Import 가 이 레퍼런스를 씀"})
        row = {
            "id": cid, "name": name, "fixed": link is not None, "auto": _auto_fixed(link), "pick": pick, "status": status, "files": files,
            "unlinked": [_ref_file(root, refs_dir, doc, f, "unlinked") for f in r["unlinked"]], "held": held, "explicit": explicit,
            "manifest": None if mc is None else {"refs_auto": list(mc.get("refs_auto") or []), "refs_main": mc.get("refs_main")},
            "jobs_ref_sha": next((inp.get("sha") for j in m["jobs"] if j.get("crop_id") == cid for inp in j.get("inputs") or []
                                  if isinstance(inp, dict) and inp.get("role") == "ref"), None),
            "checks": checks,
        }
        if link is not None:  # 고정 풀기 확인 창(§5.7): 풀면 쓰일 이름 매칭 결과와 Prepare 가 고를 대표
            rm = drefs.resolve_refs(refs_dir, crop, None)
            row["match_files"], row["match_pick"] = rm["refs_auto"], _pick(name, rm)
        rows.append(row)
    ids = {c["id"] for c in crops}
    return {"exists": doc is not None or error is not None, "rev": doc["rev"] if doc else None, "error": error,
            "refs_dir": refs_dir, "refs_dir_ok": ok, "outside_project": outside, "doc_dir_match": match,
            "doc_refs_dir": doc["refs_dir"] if doc else None, "suggest_dir": os.path.join(root, "refs").replace("\\", "/"),
            "pending_rerolls": _pending(root, m)[1], "sam3": {"ckpts": list(_SAM3_CKPTS)}, "crops": rows,
            "orphans": [{"id": cid, "name": (ln.get("fixed") or {}).get("name"), "main": ln["main"], "files": list(ln["files"])}
                        for cid, ln in doc["links"].items() if cid not in ids] if doc else [],
            "sheets": [dict(s, v=_sheet_v(root, s)) for s in doc["sheets"]] if doc else []}


def _sheet_v(root: str, s: dict) -> str | None:
    """최근 시트 행의 v = 지금 시트 서명(GET /refs/sheets 행 v·컷 sheet_v 와 같은 값 — 화면은 열린 시트의 v 와 비교), 파일이 없으면 None."""
    try:
        return drefs.sheet_sig(root, s["path"], s.get("layer"))
    except ValueError:  # 최근 시트가 지워짐·옮겨짐
        return None


def ref_sheets(root: str) -> list[dict]:
    """GET /refs/sheets(§5.2): drefs.list_sheets 행 + v(시트 서명)·preview_url(GET /refs/sheet kind=preview)."""
    rk = store.root_key(root)
    out = drefs.list_sheets(root, store.load_manifest(root))
    for s in out:
        s["v"] = drefs.sheet_sig(root, s["path"])
        s["preview_url"] = "refs/sheet?" + urlencode({"root": rk, "sheet": s["path"], "kind": "preview", "v": s["v"]})
    return out


def _int_list(raw, n: int, what: str) -> list[int]:
    try:
        v = [int(x) for x in str(raw).split(",")]
    except ValueError:
        v = []
    if len(v) != n:
        raise _Reject(400, f"{what} 는 정수 {n}개(쉼표로 구분)여야 합니다")
    return v


def sheet_image(root: str, sheet, kind: str, rect=None, max_side=None) -> tuple[bytes, str]:
    """GET /refs/sheet(§5.3) → (JPEG, 그 그림을 만든 시트 캐시의 서명 — _REF_SLOTS 를 기다리거나 캐시를 만드는 사이 저장돼도 옛 v URL 에
    새 그림이 한 주 캐시되지 않게, codex9). sheet 는 목록 규칙(drefs.sheet_check — 절대·'..'·다른 드라이브 400, 목록 밖 404)을
    통과한 상대 경로만. preview = 캐시 미리보기(긴 변 2048), region = rect "x0,y0,x1,y1"(시트 안·≤ 8192²)를 원해상도로 잘라 긴 변 ≤ max(1–2048,
    기본 2048). 시트 캐시(디코드·PSD 합성)는 _REF_SLOTS 안에서."""
    if kind not in ("preview", "region"):
        raise _Reject(400, "kind 는 preview, region 중 하나여야 합니다")
    info = drefs.sheet_check(root, store.load_manifest(root), sheet)
    with _REF_SLOTS:
        if kind == "region":
            return drefs.sheet_region(root, info["path"], _int_list(rect, 4, "rect"),
                                      _int_list(max_side or drefs.SHEET_PREVIEW, 1, "max")[0])
        path, sig = drefs.sheet_preview(root, info["path"])
        data = _read_bytes(path)
    if data is None:  # Ref Cut 노드가 다른 시트를 만들며 캐시를 정리한 직후
        raise _Reject(409, "시트 미리보기 캐시가 방금 정리됐습니다 — 다시 시도하세요")
    return data, sig


def _on_bg(rgba: np.ndarray, bg: str) -> np.ndarray:
    """RGBA → 바탕(gray #808080·white·check 8px)에 합성한 RGB."""
    a = rgba[..., 3:4].astype(np.float32) / 255.0
    if _REF_BGS[bg] is None:
        yy, xx = np.indices(rgba.shape[:2])
        back = np.where(((yy // 8 + xx // 8) % 2 == 0)[..., None], 255.0, 204.0)
    else:
        back = float(_REF_BGS[bg])
    return np.clip(rgba[..., :3].astype(np.float32) * a + back * (1.0 - a) + 0.5, 0, 255).astype(np.uint8)


def _ref_thumb(rgba: np.ndarray, size: int, bg: str) -> bytes:
    """썸네일 JPEG: 긴 변이 size 보다 크면 줄이고(확대 없음) 바탕에 합성."""
    small = np.asarray(_fit(rgba, size)) if max(rgba.shape[:2]) > size else rgba
    return _jpeg(Image.fromarray(_on_bg(small, bg)))


def _png(arr: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(arr)).save(buf, "PNG", compress_level=1)
    return buf.getvalue()


def ref_image(root: str, name, where: str, size_raw, bg: str) -> tuple[bytes, str, str]:
    """GET /refs/img(§5.4) → (bytes, Content-Type, 서명). where = refs(refs_dir 바로 아래) | held(_보류) | cut(컷 RGBA — name = 요청 ID) |
    cutmask(컷의 실효 알파 L PNG = 시트 α × 선택 마스크 grow·feather — 저장될 그대로, size 0 만). size 128·256·512 = 바탕 bg 에 합성한
    JPEG(확대 없음), 0 = 원본(refs·held 는 파일 그대로, cut 은 투명 PNG). 이름은 drefs.check_ref_name(이미지만 — 보류의 PSD 는 안 됨)."""
    if where not in _REF_IMG_WHERE:
        raise _Reject(400, f"where 는 {', '.join(_REF_IMG_WHERE)} 중 하나여야 합니다")
    if not str(size_raw).isdigit() or int(size_raw) not in _REF_IMG_SIZES:
        raise _Reject(400, f"size 는 {', '.join(map(str, _REF_IMG_SIZES))} 중 하나여야 합니다")
    size = int(size_raw)
    if bg not in _REF_BGS:
        raise _Reject(400, f"bg 는 {', '.join(_REF_BGS)} 중 하나여야 합니다")
    if where in ("cut", "cutmask"):
        if not isinstance(name, str) or not _REQ_RE.fullmatch(name):
            raise _Reject(400, "컷 요청 ID 형식이 잘못되었습니다([0-9a-f] 32자)")
        if where == "cutmask" and size:
            raise _Reject(400, "cutmask 는 size 0 만 됩니다")
        req = drefs.load_cut_request(root, name)
        res = drefs.load_cut_result(root, name)
        if not res or not res.get("ok"):
            raise _Reject(404, f"끝난 컷이 없습니다: {name}")
        with _REF_SLOTS:
            rgba = drefs.apply_cut(root, req, res)
        sig = _sig("refcut", name, res.get("mask_sha"))
        if where == "cutmask":
            return _png(rgba[..., 3]), "image/png", sig
        return (_png(rgba), "image/png", sig) if size == 0 else (_ref_thumb(rgba, size, bg), "image/jpeg", sig)
    refs_dir = _refs_dir(store.load_manifest(root))
    if not _refs_dir_ok(refs_dir):
        raise _Reject(404, "레퍼런스 폴더가 없습니다")
    name = drefs.check_ref_name(name)
    key = f"{drefs.HELD_DIR}/{name}" if where == "held" else name
    path = os.path.join(refs_dir, *key.split("/"))
    if not os.path.isfile(path):
        raise _Reject(404, f"레퍼런스 파일이 없습니다: {key}")
    sig = _sig("refimg", key, store.file_fingerprint(path))
    if size == 0:
        ext = os.path.splitext(name)[1].lower()
        return _read_bytes(path), {".png": "image/png", ".webp": "image/webp"}.get(ext, "image/jpeg"), sig
    return _cached(root, f"refimg_{sig}_{size}_{bg}.jpg", lambda: _ref_thumb(_read_rgba(path), size, bg)), "image/jpeg", sig


def _cut_result(rk: str, res: dict) -> dict:
    """GET·POST /refs/cut 의 result: mask_url(실효 알파 L PNG — 보드는 쓰지 않고 진단·외부 도구용)·preview_url(컷 RGBA 원해상도 — 겹침 외곽선(알파)·변환 미리보기) + 통계.
    SAM3 결과는 auto_point(노드가 맨 앞에 넣은 자동 양점, 시트 좌표 | null = 넣지 않음 — 선택 안이 모두 투명·텍스트 감지 경로)도, 그것을
    base 로 다듬은 결과(grow·feather·다각형 더하기/빼기)는 base 의 auto_point 를 이어 받아 같은 값을 — 사각형·다각형에서 시작한 결과에는 없다."""
    v = _sig("refcut", res["req"], res.get("mask_sha"))  # ref_image 의 서명과 같게 — 같아야 끝난 컷 그림이 immutable 캐시(codex12)
    out = {"mask_url": _img_url(rk, "cutmask", res["req"], 0, v), "preview_url": _img_url(rk, "cut", res["req"], 0, v),
           "window": res["window"], "rect": res.get("rect"), "area_frac": res.get("area_frac"), "n_parts": res.get("n_parts"),
           "sec": res.get("sec"), "ckpt": res.get("ckpt"), "warnings": list(res.get("warnings") or [])}
    if "auto_point" in res:
        out["auto_point"] = res["auto_point"]
    return out


def ref_cut(root: str, body: dict) -> dict:
    """POST /refs/cut(§5.5): drefs.new_cut_request(검증 → derived/refcut/<req>.req.json) 뒤 오래된 요청 정리(refs.json 기록이 가리키는
    마스크와 그 다듬기 연쇄, 이 요청과 그 base 연쇄는 남김 — refs.json 이 깨졌으면 정리하지 않음). sam3 → {req, state: "client_queue"}(보드가 /api/prompt 로 Ref Cut 노드를 큐 — 체크포인트는
    set_sam3_ckpts 목록 안, 없으면 첫 항목을 요청에 적음). rect·poly(base 다듬기 포함) → 여기서 바로 drefs.finish_mask → save_cut_result →
    {req, state: "done", result}. base 다듬기는 base res.json 의 auto_point 를 결과에 이어 적는다(없으면 없음, null 은 null — 다듬은 SAM3
    결과의 회색 점·다시 열기가 그 자리를 잃지 않게). 계산이 실패하면 오류 결과 파일을 쓰고 다시 올린다(GET 이 error 로 보임).
    sheet_v(화면이 연 시트의 서명)가 지금 서명과 다르면(바깥에서 저장 — base 다듬기도) 409 {error, sheet_changed: true}, 요청 파일 없음."""
    if body.get("method") == "sam3":
        if not _SAM3_CKPTS:
            raise _Reject(400, "SAM3 체크포인트가 없습니다(models/checkpoints 에 이름에 sam3 가 든 파일 — ComfyUI 재시작 뒤 보임)")
        if body.get("ckpt") is not None and body["ckpt"] not in _SAM3_CKPTS:
            raise _Reject(400, f"없는 SAM3 체크포인트입니다: {body['ckpt']!r}")
        body = {**body, "ckpt": body.get("ckpt") or _SAM3_CKPTS[0]}
    try:
        req = drefs.new_cut_request(root, store.load_manifest(root), body)
    except drefs.SheetChanged as e:
        raise _Reject(409, str(e), sheet_changed=True) from None
    doc, error = _refs_doc(root)
    if error is None:  # 기록의 마스크와 그 연쇄(steps — 마지막 요청을 다시 읽으려면 base 요청이 있어야 함) + 이 요청과 그 연쇄(방금 검증한
        # base 를 아래 load_cut_mask 전에·저장의 steps 를 끊지 않게 — base 는 따로: 그 앞 컷이 이미 정리됐으면 _cut_chain 이 base 에서 끊김)
        cuts = [rec.get("cut") or {} for rec in (doc or {}).get("files", {}).values()]
        drefs.prune_cuts(root, keep=[c.get("mask") for c in cuts] + [s for c in cuts for s in c.get("steps") or []]
                         + [r["req"] for r in _cut_chain(root, req)] + [req["base"]])
    if req["method"] == "sam3":
        return {"req": req["req"], "state": "client_queue"}
    try:
        with _REF_SLOTS:
            t0 = time.perf_counter()
            arr = drefs.sheet_array(root, req["sheet"], req["layer"])
            _rgb, alpha, window = drefs.cut_window(arr, req["rect"], req["pad"])
            base = drefs.load_cut_mask(root, req["base"]) if req["base"] else None
            sel, stats = drefs.finish_mask(None, req, window, alpha, base)
            info = {"window": window, "rect": req["rect"], **stats, "sec": round(time.perf_counter() - t0, 3)}
            bres = drefs.load_cut_result(root, req["base"]) if req["base"] else None
            if bres and "auto_point" in bres:  # SAM3 연쇄의 자동 양점(다듬기는 SAM 을 다시 돌리지 않아 같은 점)
                info["auto_point"] = bres["auto_point"]
            res = drefs.save_cut_result(root, req["req"], sel, info)
    except Exception as e:
        drefs.save_cut_error(root, req["req"], e)
        raise
    return {"req": req["req"], "state": "done", "result": _cut_result(store.root_key(root), res)}


def ref_cut_state(root: str, req_id) -> dict:
    """GET /refs/cut(§5.5): {req, state: waiting(결과 파일 없음) | done(result) | error(error)}. 요청 파일이 없으면 404."""
    if not isinstance(req_id, str) or not _REQ_RE.fullmatch(req_id):
        raise _Reject(400, "req 형식이 잘못되었습니다([0-9a-f] 32자)")
    if not os.path.isfile(os.path.join(root, *drefs.CUT_DIR.split("/"), f"{req_id}.req.json")):
        raise _Reject(404, f"컷 요청이 없습니다(오래돼 정리됐을 수 있음): {req_id}")
    res = drefs.load_cut_result(root, req_id)
    if res is None:
        return {"req": req_id, "state": "waiting"}
    if not res.get("ok"):
        return {"req": req_id, "state": "error", "error": res.get("error")}
    return {"req": req_id, "state": "done", "result": _cut_result(store.root_key(root), res)}


_DIR_MISMATCH = ("refs.json 의 refs_dir({doc})가 지금 refs_dir({now})와 달라 연결을 무시하는 중입니다 — Import PSD 의 refs_dir 를 "
                 "되돌리거나 refs.json 을 치운 뒤 고치세요")


def _refs_dir_for_write(m: dict) -> str:
    """쓰기 가능한 refs_dir(§1.1): 비었거나 폴더가 없으면 400(보드는 Import 위젯을 바꿀 수 없음 — 안내)."""
    refs_dir = _refs_dir(m)
    if not refs_dir:
        raise _Reject(400, "refs_dir 가 비었습니다 — Import PSD 의 refs_dir 에 <project>\\refs 를 넣고 큐하세요")
    if not os.path.isdir(refs_dir):
        raise _Reject(400, f"레퍼런스 폴더가 없습니다: {refs_dir}")
    return refs_dir


@contextlib.contextmanager
def _refs_write_lock(root: str):
    """root 의 레퍼런스 쓰기를 한 번에 하나로: rev 검사 → 파일 동작(save·hold·restore) → refs.json → 실패하면 되돌리기 전체. 다른 창의
    쓰기가 그 사이에 커밋하면 이쪽의 되돌리기가 그 창이 쓴 파일을 옮겨 refs.json 과 디스크가 갈라진다. RLock(_write_refs 가 다시 잡음).
    잠금 순서 = 이것 → store.manifest_lock(update_refs 안)만."""
    with _REFS_WRITE_LOCKS.setdefault(store.root_key(root), threading.RLock()):
        yield


def _write_refs(root: str, rev: int | None, refs_dir: str, mutate: Callable[[dict], object]) -> tuple[object, int]:
    """refs.json 쓰기: rev None = 아직 없음(만들기 — 이미 있으면 RefsConflict), 아니면 expect_rev = rev. 문서의 refs_dir 가 지금 것과
    다르면 409(_DIR_MISMATCH). _refs_write_lock 안(파일을 옮기는 쓰기가 커밋 전인 동안 끼지 않게). → (mutate 반환, 새 rev)."""
    def guarded(d):
        if not d:
            d.update(drefs.new_refs(refs_dir))
        elif not drefs.same_refs_dir(d["refs_dir"], refs_dir):
            raise _Reject(409, _DIR_MISMATCH.format(doc=d["refs_dir"], now=refs_dir))
        return mutate(d)

    with _refs_write_lock(root):
        return drefs.update_refs(root, guarded, expect_rev=rev, create=rev is None)


def _refs_at(root: str, rev: int | None, refs_dir: str) -> dict | None:
    """파일을 옮기기 전에 낡은 창을 거른다: 지금 refs.json(없으면 None)의 rev 가 요청 rev 와 다르면 RefsConflict, refs_dir 가 다르면 409."""
    doc = drefs.load_refs(root)
    now = doc["rev"] if doc else None
    if now != rev:
        raise drefs.RefsConflict(f"레퍼런스가 다른 창에서 바뀌었습니다(rev {rev} → {now}) — 새로고침")
    if doc is not None and not drefs.same_refs_dir(doc["refs_dir"], refs_dir):
        raise _Reject(409, _DIR_MISMATCH.format(doc=doc["refs_dir"], now=refs_dir))
    return doc


def _seeded(d: dict, crop: dict, seed: dict | None, auto: bool = False) -> dict:
    """crop 의 연결. 없으면 파일을 옮기기 전에 잡은 seed(drefs.seed_link — 그 순간의 이름 매칭·자동 선택)로 why "edit" 고정한다
    (drefs.fix_link 와 같은 꼴 — 파일 동작 뒤에 seed 하면 방금 쓴·옮긴 파일이 섞이므로). auto = 다른 크롭의 동작이 함께 고정함(_match_seeds
    형제) → fixed.auto(_auto_fixed — 빈 seed 여도 의도한 '없음 고정'이 아님)."""
    ln = d["links"].get(crop["id"])
    if ln is None:
        ln = d["links"][crop["id"]] = {**seed, "fixed": {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "why": "edit", "name": crop["name"]}}
        if auto:
            ln["fixed"]["auto"] = True
    return ln


def _auto_fixed(ln: dict | None) -> bool:
    """부수 효과로 정해진 연결(fixed.auto): 그 크롭에서 고르지 않았는데 고정됐거나(같은 번호 형제 저장·되살리기, S1 이름 바꾸기) 다른 크롭의
    보류로 마지막 파일이 빠짐. 빈 files 여도 의도한 '레퍼런스 없음' 고정이 아니고(R-none 경고·state none false), 이후 PS 로 넣는 같은 번호
    파일을 받지 않으므로 R-unlinked 도 경고다. 그 크롭을 직접 고치면(저장·연결 동작) _touch 가 지운다."""
    return bool(ln is not None and (ln.get("fixed") or {}).get("auto"))


def _none_fixed(ln: dict | None) -> bool:
    """의도한 '레퍼런스 없음(자체 복원)' 고정: 연결이 있고 files 가 비었고 부수 효과가 아님(op none·마지막 파일 끊기/보류를 그 크롭에서)."""
    return ln is not None and not ln["files"] and not _auto_fixed(ln)


def _touch(ln: dict) -> dict:
    """사용자가 이 크롭에서 직접 고친 연결 — 부수 효과 표지(fixed.auto)를 지운다(이제 그 크롭의 선택)."""
    if isinstance(ln.get("fixed"), dict):
        ln["fixed"].pop("auto", None)
    return ln


def _r_none(ln: dict | None, nnn) -> dict:
    """검사 R-none(레퍼런스 없음, §4.8) — /refs 와 /layout 이 같이 씀. 의도한 없음 고정 = 정보, 부수 효과로 빈 고정 = 경고(이후 같은 번호
    파일은 연결해야 쓰임), 그 밖 = 경고(이름 매칭에 파일 없음 · 연결 파일이 모두 사라짐은 R-missing 도)."""
    if _none_fixed(ln):
        return {"code": "R-none", "level": "info", "msg": "레퍼런스 없음(자체 복원)으로 고정"}
    if ln is not None and not ln["files"]:
        return {"code": "R-none", "level": "warn",
                "msg": "레퍼런스 없음 — 다른 크롭의 저장·보류나 이름 바꾸기가 함께 '없음'으로 고정함(의도한 자체 복원 아님). "
                       f"refs_dir 에 넣는 '{nnn}_…' 파일은 Studio 에서 [연결]해야 쓰임"}
    return {"code": "R-none", "level": "warn", "msg": f"레퍼런스 없음(refs_dir 에 '{nnn}_…' 파일 없음)"}


def _drop_name(ln: dict, name: str, crop_name: str) -> None:
    """연결에서 name 을 뺀다(casefold). 대표였으면 남은 것 중 prompt.auto_pick_ref(크롭 이름) — 대표 파일이 사라졌을 때 Prepare 가
    고르는 것과 같다(R-main). 남은 것이 없으면 null(validate_refs 규칙)."""
    kept = [f for f in ln["files"] if f.casefold() != name.casefold()]
    if len(kept) != len(ln["files"]):
        ln["files"] = kept
        if ln["main"] is not None and ln["main"].casefold() == name.casefold():
            ln["main"] = dprompt.auto_pick_ref(crop_name, kept) if kept else None


def _rename_name(ln: dict, old: str, new: str) -> None:
    """연결의 old(디스크의 옛 이름 — NFD·대소문자만 다름 등, casefold)를 같은 자리에서 new 로(대표였으면 대표도). new 가 다른 자리에 이미
    있으면 old 만 뺀다(대표였으면 new 가 대표) — 옛 파일이 _보류 로 가고 루트에 같은 이름(ref_key)의 새 그림만 남을 때, 그 이름을 연결한
    다른 크롭이 '사라짐'이 되지 않게. 대소문자만 다르면 두 찾기가 같은 자리(i == j)라 제자리 바꾸기다."""
    i = next((i for i, f in enumerate(ln["files"]) if f.casefold() == old.casefold()), None)
    if i is None:
        return
    was_main = ln["main"] is not None and ln["main"].casefold() == old.casefold()
    j = next((j for j, f in enumerate(ln["files"]) if f.casefold() == new.casefold()), None)
    if j is None or j == i:
        ln["files"][i] = new
        if was_main:
            ln["main"] = new
    else:
        keep = ln["files"][j]
        del ln["files"][i]
        if was_main:
            ln["main"] = keep


def _missing_holders(refs_dir: str, doc: dict | None, crops: list[dict], name: str, skip: str) -> list[dict]:
    """name 을 연결해 두었는데 지금 refs_dir 에 그 파일이 없는(R-missing — 탐색기·PS 로 지움·바깥에서 _보류 로 옮김) 다른 고정 크롭
    [{file: name, crop_id, tid: null, where: "link"}]. name 에 새 그림이 생기면(새 이름 저장·되살리기) 그 크롭의 refs_auto·Prepare 선택이
    확인 없이 바뀌므로(다음 Run 새 셀일 수 있음 — I5) _need_confirm 에 올린다. 같은 이름 = casefold(NTFS 가 같은 파일로 여는 이름 — NFD 는 다름)."""
    if doc is None or not drefs.same_refs_dir(doc["refs_dir"], refs_dir):
        return []
    ids = {c["id"] for c in crops}
    k = name.casefold()
    return [{"file": name, "crop_id": cid, "tid": None, "where": "link"} for cid, ln in doc["links"].items()
            if cid != skip and cid in ids
            and any(f.casefold() == k and not os.path.isfile(os.path.join(refs_dir, f)) for f in ln["files"])]


def _add_name(ln: dict, name: str) -> None:
    """연결 끝에 name 을 보조로(이미 있으면 그대로). files 가 비었던 연결(레퍼런스 없음 고정)은 이것이 대표가 된다."""
    if all(f.casefold() != name.casefold() for f in ln["files"]):
        if len(ln["files"]) >= drefs.MAX_LINK_FILES:
            raise _Reject(400, f"크롭당 레퍼런스는 {drefs.MAX_LINK_FILES}장까지입니다")
        ln["files"].append(name)
    ln["main"] = ln["main"] or name


def _record_key(files: dict, name: str) -> str | None:
    """refs.json files 에서 name(디스크 이름 — "_보류/<이름>" 포함)의 기록 키 | None. 같은 키가 없으면 drefs.ref_key 로(S2 가 쓴 키와 디스크
    이름의 대소문자·NFC/NFD 가 달라도 옮기는 파일의 기록이 따라가게)."""
    if name in files:
        return name
    k = drefs.ref_key(name)
    return next((f for f in files if drefs.ref_key(f) == k), None)


def _undo(*steps) -> None:
    """파일 되돌리기(refs.json 쓰기가 실패한 뒤). 하나가 실패해도 나머지를 하고 로그만 남긴다 — 원래 오류를 덮지 않게."""
    for step in steps:
        try:
            step()
        except Exception:
            logger.exception(f"{_TAG} Board: 레퍼런스 파일 되돌리기 실패")


def _ref_reply(root: str, crop_id: str) -> dict:
    """쓰기 응답의 {crop: 그 크롭의 GET /refs 행, checks: {crop_id: 검사}}. 쓰기 뒤 응답을 만들지 못하면(쓰기는 됨 — 500 이면 보드가
    같은 쓰기를 낡은 rev 로 되풀이) crop null·checks {}·warning."""
    try:
        view = refs_state(root)
    except Exception as e:
        logger.exception(f"{_TAG} Board: 레퍼런스 쓰기 뒤 응답 구성 실패 {os.path.basename(root)}")
        return {"crop": None, "checks": {}, "warning": f"응답 구성 실패: {e}"}
    return {"crop": next((c for c in view["crops"] if c["id"] == crop_id), None),
            "checks": {c["id"]: c["checks"] for c in view["crops"]}}


def _cut_chain(root: str, req: dict) -> list[dict]:
    """req 와 그 앞 컷들(base 다듬기 연쇄) — 첫 컷부터. 앞 요청이 정리돼 읽을 수 없으면 거기서 끊는다."""
    chain = [req]
    while chain[0]["base"]:
        try:
            chain.insert(0, drefs.load_cut_request(root, chain[0]["base"]))
        except FileNotFoundError:
            break
    return chain


def ref_save(root: str, rev: int | None, data: dict) -> dict:
    """POST /refs/save(§5.6) {req, crop_id, role: main|aux, desc?, transform: {rotate, flip_h, scale, pad?}, confirm?}.
    끝난 컷(drefs.load_cut_request·load_cut_result) → drefs.apply_cut → transform_rgba → 이름 drefs.ref_file_name(지금 크롭 이름).
    대표 이름이 이미 있으면(drefs.same_name — NFD 이름 포함) 옛 파일을 _보류 로(hold_file — D5, held 기록), 보조 이름이 있으면 409(같은 설명). 그 이름이나
    옛 파일이 갈 "_보류/<이름>" 을 스펙·매니페스트가 이름으로 가리키거나(새 그림이 들어감) 옛 파일을 다른 크롭이 쓰거나 그 이름을 연결해
    두었는데 파일이 없던 다른 고정 크롭이 있으면(_missing_holders — 새 그림이 그 크롭에 들어감) confirm 에 그 이름이 없을 때 409
    {error, explicit}. 새 이름이면 그것을 이름 매칭으로 받게 될 고정 안 된 같은 번호 크롭도 지금 것으로 고정한다(_match_seeds — fixed.auto).
    옛 파일의 디스크 이름이 NFD 등으로 달랐으면 모든 연결의 그 이름을 같은 자리에서 새 이름으로 바꾼다(_rename_name — 확인한 다른 크롭이
    '사라짐'이 되지 않게). 순서 = 파일(hold → save_ref 배타) → refs.json(크롭 고정 — 없으면 파일 동작 전의 seed, 대표 = 맨 앞·main /
    보조 = 끝, files 기록, 최근 시트). files 기록의 cut = 다듬기 연쇄(base) 전체: 첫 컷의 method·points·text·ckpt·refine·pad·drop_small +
    모든 단계 polys(차례대로 이음 — 감사용, 16개를 넘을 수 있어 한 요청으로 다시 보낼 수는 없음) + 마지막 grow·feather·mask, steps = 연쇄
    요청 ID(첫 컷부터 — 오래된 컷 정리가 남김), step_polys = 단계마다의 polys(steps 와 같은 길이 — 다시 컷 = 첫 요청 + 단계마다 base·그
    polys, 단계마다 ≤ 16). refs.json 쓰기가 실패하면 방금 쓴 파일을 _보류 로 옮기고 옛 대표를 되살린다(refs 바로 아래 고아 0).
    변환 뒤의 rev 재검사·파일 동작·refs.json·되돌리기는 _refs_write_lock 안(다른 창의 쓰기가 사이에 끼지 않음).
    → {ok, rev, file, size, bytes, held(옛 파일의 보류 이름 | null), crop, checks}."""
    req_id = _field(data, "req", _REQ_RE)
    crop_id = _field(data, "crop_id", _CROP_ID_RE)
    role = data.get("role")
    if role not in ("main", "aux"):
        raise _Reject(400, "role 은 main(대표), aux(보조) 중 하나여야 합니다")
    t = drefs.check_transform(data.get("transform"))
    m = store.load_manifest(root)
    refs_dir = _refs_dir_for_write(m)
    crops = _ref_crops(root, m)
    crop = _crop_of(crops, crop_id)
    name = drefs.ref_file_name(crop["name"], role, data.get("desc"))
    req = drefs.load_cut_request(root, req_id)
    res = drefs.load_cut_result(root, req_id)
    if not res or not res.get("ok"):
        raise _Reject(400, f"끝나지 않았거나 실패한 컷입니다: {req_id}")
    doc = _refs_at(root, rev, refs_dir)
    link = doc["links"].get(crop_id) if doc else None
    path = os.path.join(refs_dir, name)
    old = drefs.same_name(refs_dir, name)
    if old is not None and role == "aux":
        raise FileExistsError(f"같은 설명의 보조 레퍼런스가 이미 있습니다(덮어쓰지 않음): {old}")
    if (link is not None and len(link["files"]) >= drefs.MAX_LINK_FILES
            and all(f.casefold() != name.casefold() for f in link["files"])):
        raise _Reject(400, f"크롭당 레퍼런스는 {drefs.MAX_LINK_FILES}장까지입니다")
    ex = drefs.explicit_refs(root, m)
    rows = _explicit_rows(ex, name)
    if old is not None:
        rows += _held_rows(ex, refs_dir, old) + _name_users(refs_dir, doc, crops, name, crop_id)
    rows += [r for r in _missing_holders(refs_dir, doc, crops, name, crop_id) if r not in rows]
    _need_confirm(data, rows, f"{name} 에 새 그림을 씁니다" + (" — 옛 파일은 _보류 로" if old is not None else ""))
    with _REF_SLOTS:
        rgba = drefs.transform_rgba(drefs.apply_cut(root, req, res), **t)
    h, w = rgba.shape[:2]
    chain = _cut_chain(root, req)
    first = chain[0]
    first_res = res if first is req else drefs.load_cut_result(root, first["req"]) or {}
    rec = {"for": crop_id, "at": time.strftime("%Y-%m-%d %H:%M:%S"), "size": [w, h], "sha": store.pixel_sha(rgba),
           "sheet": {"path": req["sheet"], "layer": req["layer"], "fingerprint": req["sheet_fp"], "size": req["sheet_size"]},
           "rect": req["rect"],
           "cut": {**{k: first[k] for k in ("method", "points", "text", "refine", "pad", "drop_small")},
                   "polys": [p for r in chain for p in r["polys"]], "grow": req["grow"], "feather": req["feather"],
                   "ckpt": first_res.get("ckpt") or first["ckpt"], "mask": res["mask"], "mask_sha": res["mask_sha"],
                   "steps": [r["req"] for r in chain], "step_polys": [r["polys"] for r in chain]},
           "transform": t}
    with _refs_write_lock(root):
        _refs_at(root, rev, refs_dir)  # 변환하는 동안 다른 창이 썼으면 409 — 같은 rev 면 위 판단(doc·확인)이 그대로
        seed = drefs.seed_link(refs_dir, crop) if link is None else None
        others = _match_seeds(refs_dir, doc, crops, name, crop_id) if old is None else []
        held = drefs.hold_file(refs_dir, old) if old is not None else None

        def unhold():
            if held:
                drefs.restore_file(refs_dir, held, old)

        try:
            drefs.save_ref(refs_dir, name, rgba)
        except BaseException:
            _undo(unhold)
            raise

        def mutate(d):
            ln = _touch(_seeded(d, crop, seed))
            for c, s in others:
                _seeded(d, c, s, auto=True)
            _drop_name(ln, name, crop["name"])  # 같은 이름의 옛 대표 · 연결돼 있는데 파일이 사라졌던 이름(R-missing)은 새 그림으로 다시
            if old is not None and old != name:  # 옛 파일의 디스크 이름이 NFD 였음(seed·연결에 그 이름으로 있음)
                _drop_name(ln, old, crop["name"])
                for oln in d["links"].values():  # 그 이름을 연결한(확인한 — where link) 다른 크롭도 새 그림(NFC 이름)으로, 같은 자리
                    if oln is not ln:
                        _rename_name(oln, old, name)
            if role == "main":
                ln["files"], ln["main"] = [name] + ln["files"], name
            else:
                _add_name(ln, name)
            okey = _record_key(d["files"], old) if held else None
            if okey is not None:
                d["files"][f"{drefs.HELD_DIR}/{held}"] = d["files"].pop(okey)
            if held:
                d.setdefault("held", {})[held] = {"from": old, "crop": crop_id}
            d["files"][name] = rec
            sheet = {"path": req["sheet"], "layer": req["layer"], "view": req["rect"]}
            d["sheets"] = [sheet] + [s for s in d["sheets"] if (s.get("path"), s.get("layer")) != (sheet["path"], sheet["layer"])]
            del d["sheets"][drefs.MAX_SHEETS:]

        try:
            _r, new_rev = _write_refs(root, rev, refs_dir, mutate)
        except BaseException:
            _undo(lambda: drefs.hold_file(refs_dir, name), unhold)
            raise
        nbytes = os.path.getsize(path)
    return {"ok": True, "rev": new_rev, "file": name, "size": [w, h], "bytes": nbytes, "held": held, **_ref_reply(root, crop_id)}


def ref_link(root: str, rev: int | None, op, data: dict) -> dict:
    """POST /refs/link(§5.7) {crop_id, name?, to?, confirm?}. op:
    fix(지금 이름 매칭으로 고정) · none(레퍼런스 없음으로 고정 — 고정된 크롭은 files 비움) · unfix(고정 풀기) · add(refs_dir 파일을 보조로) ·
    main(연결된 파일을 대표로 — 맨 앞) · move(to = 새 위치) · remove(연결 끊기, 파일은 그대로) ·
    hold(파일을 _보류 로, 모든 크롭 연결에서 뺌, refs.json held 에 {보류 이름: {from: 원래 이름, crop}} — 다른 크롭이 쓰거나 스펙·매니페스트가
    그 이름·옮겨 갈 "_보류/<이름>" 을 가리키면 confirm) ·
    (remove·hold 로 대표가 빠지면 남은 것 중 이름 규칙 auto_pick_ref — 대표 파일이 사라졌을 때 Prepare 가 고르는 것과 같음) ·
    restore(_보류 파일을 held 기록의 원래 이름(없으면 그대로)으로 되살려 보조로 — 같은 이름(drefs.same_name)이 있으면 409, 스펙·매니페스트가
    "_보류/<이름>"·되살릴 이름을 가리키면 confirm,
    되살린 이름을 이름 매칭으로 받게 될 고정 안 된 다른 크롭은 지금 것으로 고정 — _match_seeds) ·
    relink(크롭이 사라진 연결 crop_id 를 크롭 to 로 — to 가 이미 고정이면 409).
    고정 안 된 크롭을 고치는 op(add·main·move·remove·hold·restore)는 먼저 지금 이름 매칭으로 why "edit" 고정한다(D1).
    → {ok, rev, crop(그 크롭의 GET /refs 행 — relink 는 to), checks} + hold 는 held(보류 이름), restore 는 file(되살린 이름)."""
    if op not in _LINK_OPS:
        raise _Reject(400, f"op 는 {', '.join(_LINK_OPS)} 중 하나여야 합니다")
    crop_id = _field(data, "crop_id", _CROP_ID_RE)
    m = store.load_manifest(root)
    refs_dir = _refs_dir_for_write(m)
    crops = _ref_crops(root, m)
    extra = {}
    if op == "relink":
        to = _field(data, "to", _CROP_ID_RE)
        target = _crop_of(crops, to)
        if any(c["id"] == crop_id for c in crops):
            raise _Reject(400, f"크롭이 사라진 연결이 아닙니다(크롭 {crop_id} 가 있음)")

        def mutate(d):
            if crop_id not in d["links"]:
                raise _Reject(404, f"연결이 없습니다: {crop_id}")
            if to in d["links"]:
                raise drefs.RefsConflict(f"크롭 {target['name']} 은 이미 고정돼 있습니다 — 고정을 푼 뒤 다시 연결하세요")
            d["links"][to] = _touch(d["links"].pop(crop_id))  # 그 크롭에 직접 고른 연결
            for rec in d["files"].values():
                if rec.get("for") == crop_id:
                    rec["for"] = to
            for rec in (d.get("held") or {}).values():  # 그 크롭이 보류한 파일도 새 크롭의 보류 목록에
                if rec.get("crop") == crop_id:
                    rec["crop"] = to

        _r, new_rev = _write_refs(root, rev, refs_dir, mutate)
        return {"ok": True, "rev": new_rev, **_ref_reply(root, to)}
    crop = _crop_of(crops, crop_id)
    if op in ("hold", "restore"):
        name = drefs.check_ref_name(data.get("name"))
        with _refs_write_lock(root):  # rev 검사 → 파일 동작 → refs.json → 되돌리기 사이에 다른 창의 쓰기가 끼지 않게
            doc = _refs_at(root, rev, refs_dir)
            ex = drefs.explicit_refs(root, m)
            if op == "hold":
                if not os.path.isfile(os.path.join(refs_dir, name)):
                    raise FileNotFoundError(f"레퍼런스 파일이 없습니다: {name}")
                _need_confirm(data, _explicit_rows(ex, name) + _held_rows(ex, refs_dir, name) + _name_users(refs_dir, doc, crops, name, crop_id),
                              f"{name} 을 _보류 로 옮깁니다")
            else:
                if not os.path.isfile(os.path.join(refs_dir, drefs.HELD_DIR, name)):
                    raise FileNotFoundError(f"보류 파일이 없습니다: {name}")
                hr = _held_rec(doc, name)
                back = hr["from"] if hr else name
                if drefs.same_name(refs_dir, back) is not None:
                    raise FileExistsError(f"refs 에 같은 이름의 파일이 있어 되살릴 수 없습니다: {back}")
                _need_confirm(data, _explicit_rows(ex, f"{drefs.HELD_DIR}/{name}") + _explicit_rows(ex, back)
                              + _missing_holders(refs_dir, doc, crops, back, crop_id), f"{back} 을 되살립니다")
            seed = drefs.seed_link(refs_dir, crop) if doc is None or crop_id not in doc["links"] else None
            if op == "hold":
                held = extra["held"] = drefs.hold_file(refs_dir, name)
                names = {c["id"]: c["name"] for c in crops}

                def mutate(d):
                    _touch(_seeded(d, crop, seed))
                    for cid, ln in d["links"].items():  # 고아 연결은 고정 때의 이름으로
                        had = bool(ln["files"])
                        _drop_name(ln, name, names.get(cid) or (ln.get("fixed") or {}).get("name") or "")
                        if cid != crop_id and had and not ln["files"]:  # 다른 크롭의 보류로 마지막 파일이 빠짐 — 그 크롭이 고른 '없음'이 아님
                            ln["fixed"] = {**(ln.get("fixed") or {}), "auto": True}
                    okey = _record_key(d["files"], name)
                    if okey is not None:
                        d["files"][f"{drefs.HELD_DIR}/{held}"] = d["files"].pop(okey)
                    d.setdefault("held", {})[held] = {"from": name, "crop": crop_id}

                def undo():
                    drefs.restore_file(refs_dir, held, name)
            else:
                others = _match_seeds(refs_dir, doc, crops, back, crop_id)
                back = extra["file"] = drefs.restore_file(refs_dir, name, back)

                def mutate(d):
                    _add_name(_touch(_seeded(d, crop, seed)), back)
                    for c, s in others:
                        _seeded(d, c, s, auto=True)
                    okey = _record_key(d["files"], f"{drefs.HELD_DIR}/{name}")
                    if okey is not None:
                        d["files"][back] = d["files"].pop(okey)
                    hkey = _record_key(d.get("held") or {}, name)
                    if hkey is not None:
                        del d["held"][hkey]

                def undo():  # 원래 보류 이름으로(hold_file 은 새 시각 접미를 붙여 files 기록 키가 고아가 됨)
                    drefs._move_excl(os.path.join(refs_dir, back), os.path.join(refs_dir, drefs.HELD_DIR, name))
            try:
                _r, new_rev = _write_refs(root, rev, refs_dir, mutate)
            except BaseException:
                _undo(undo)
                raise
        return {"ok": True, "rev": new_rev, **extra, **_ref_reply(root, crop_id)}
    name = drefs.check_ref_name(data.get("name")) if op in ("add", "main", "move", "remove") else None
    if op == "add" and not os.path.isfile(os.path.join(refs_dir, name)):
        raise FileNotFoundError(f"레퍼런스 파일이 없습니다: {name}")
    to = data.get("to")
    if op == "move" and (isinstance(to, bool) or not isinstance(to, int) or to < 0):
        raise _Reject(400, "to 는 0 이상의 정수(새 위치)여야 합니다")

    def mutate(d):
        if op == "unfix":
            d["links"].pop(crop_id, None)
            return
        ln = _touch(drefs.fix_link(d, refs_dir, crop, {"fix": "fix", "none": "none"}.get(op, "edit")))
        if op == "none":
            ln["files"], ln["main"] = [], None
        elif op == "add":
            _add_name(ln, name)
        elif op in ("main", "move", "remove"):
            i = next((i for i, f in enumerate(ln["files"]) if f.casefold() == name.casefold()), None)
            if i is None:
                raise _Reject(400, f"이 크롭에 연결된 파일이 아닙니다: {name}")
            f = ln["files"][i]
            if op == "main":
                ln["files"], ln["main"] = [f] + ln["files"][:i] + ln["files"][i + 1:], f
            elif op == "move":
                if to >= len(ln["files"]):
                    raise _Reject(400, f"to 는 0–{len(ln['files']) - 1} 이어야 합니다")
                ln["files"].insert(to, ln["files"].pop(i))
            else:
                _drop_name(ln, f, crop["name"])

    _r, new_rev = _write_refs(root, rev, refs_dir, mutate)
    return {"ok": True, "rev": new_rev, **_ref_reply(root, crop_id)}


# ══════════════════════════════════════════════════════════════════════
# 파이프라인 (S3a — STUDIO_S3_SPEC §2.4·§3.5·§3.7·§6, S3b — _proto/s3b/S3B_CONTRACT.md §1·§2). 실행 설정·유도·템플릿은
# bmk_design_patch_pipeline(pl), 초안 계획·채택은 vlm(_vlm() 로만 — state·layout·refs 경로는 vlm 을 불러오지 않는다). 계획은 노드와
# 같은 함수(runner.plan_calls·split_approval, vlm.draft_plan)라 라우트 해시 = 노드가 승인 대조에 쓰는 실행 전 해시(N25·N26) — 유료
# 템플릿은 그 해시가 요청 approve 와 같을 때만 낸다. 계산은 요청 때만 — state 는 기억한 계획과 파일 stat 서명만 본다(plan_v·
# draft_chip). 응답 모양 정본 = tools/fixtures/pipeline_api.json.
# ══════════════════════════════════════════════════════════════════════
_PLAN_LOCK = threading.Lock()            # 계획 라우트는 한 번에 하나 — 못 잡으면 기다리지 않고 202 busy
_PROMPT_LOCK_WAIT = 15.0                 # 유료 prompt 는 계획 잠금을 이만큼 기다린다(못 잡으면 409 busy — 탭이 받는 답이라 202 없음)
_PLAN_MEMO: dict[str, dict] = {}         # root_key → {"free": only 없는 계획 {sig, settings, computed_at, run, draft},
                                         #             "sel": {(sig, settings, only): 선택 부분}} — settings = 계산에 쓴 설정의 지문
_PLAN_MEMO_LOCK = threading.Lock()
_PLAN_SEL_MAX = 8                        # 루트마다 기억하는 선택 계획 수
_DRAFT_MEMO: dict = {}                   # 보드 평탄화 memo(초안 계획·채택 — 노드 _DRAFT_MEMO 와 따로, 같은 상한)
_SPEC_PATHS: dict[str, tuple] = {}       # root_key → ((specs/ 목록, refs_dir), 스펙이 가리키는 파일 경로) — 계획 서명이 폴링마다 스펙 JSON 을
                                         # 파싱하지 않게(목록이 바뀔 때만 store._spec_paths)
_RUN_LEDGER_RE = re.compile(r"r\d+\.(req|inflight|resp|done|failed)\.json")  # runner 원장(plan_calls 가 읽는 파일)
_VLM_CAPS = "_vlm_caps.json"             # vlm.CAPS_FILE 와 같음(root 의 부모 폴더 — openai 예상 크레딧 보정, Draft est 에 들어감)
_SINCE_SLACK = 0.05                      # 원장 파일 시각은 time.time() 보다 거칠다(NTFS 갱신 틱·float 반올림) — Draft 시작 직후
                                         # 쓴 원장이 진행 수에서 빠지지 않게 시작 시각을 이만큼 당겨 센다
_RUN_ZERO = {"need": 0, "pre": 0, "free": 0, "done": 0, "failed": 0, "orphaned": 0, "inflight": 0, "est_usd": [0.0, 0.0],
             "selected": False}
_STALE = "_stale"                        # run.by_crop: crop_id 가 없거나 매니페스트에 없는 크롭의 옛 셀
_DRAFT_SEL_EMPTY = {"hash": "", "units": [], "refresh_available": [], "skipped": [], "cached": [], "accepted": [], "filtered": [],
                    "est_credits": [0, 0], "est_seconds": 0, "orphan_retries": 0, "est_usd": [0.0, 0.0], "reasons": [],
                    "free": 0}  # 선택 없는 Draft 계획의 선택 필드(정본 alt.no_selection)


def _now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _draft_memo() -> dict:
    """보드 평탄화 memo — 노드와 같은 상한(vlm.DRAFT_MEMO_MAX)을 넘으면 비운다(draft_view·adopt_write 는 스스로 비우지 않음)."""
    if len(_DRAFT_MEMO) > _vlm().DRAFT_MEMO_MAX:
        _DRAFT_MEMO.clear()
    return _DRAFT_MEMO


def _route_settings(root: str, m: dict | None = None) -> dict:
    """계획·채택 라우트의 실행 설정 = pl.current(deep=True)(파일이 없으면 GET /pipeline 과 같은 유도값). 깨졌으면 409 pipeline_broken
    (prompt_gate 와 같은 문구 — 덮어쓰기는 설정 패널)."""
    cur = pl.current(root, m, deep=True)
    if cur["error"] is not None:
        raise _Reject(409, f"실행 설정 파일이 깨졌습니다 — 설정 패널에서 다시 저장(덮어쓰기): {cur['error']}", reason="pipeline_broken")
    return cur


def _draft_family(root: str, settings: dict, mock: bool | None = None) -> str:
    """초안 백엔드 family(노드 Spec Draft 와 같은 판정): pipeline.draft.vlm 의 family, openai 이고 mock 스위치면 mock — None 은 없다
    (adopt_check 의 mock 초안 거절이 꺼지지 않게). mock = 이미 읽은 스위치(pipeline_plan), None 이면 지금 읽음."""
    fam, _model = _vlm().parse_vlm_choice(settings["draft"]["vlm"])
    return "mock" if fam == "openai" and (_mock_active(root) if mock is None else mock) else fam


def _scan(path: str, keep: re.Pattern | None = None, depth: int = 0, skip: tuple = ()) -> list:
    """폴더의 파일 [이름, 크기, mtime_ns](keep 이 있으면 그 이름만) + depth 단계 하위 폴더 [이름, …](skip 제외). scandir 이라 Windows 는
    파일마다 stat 을 부르지 않는다. 없거나 읽는 사이 사라진 것은 뺀다."""
    try:
        with os.scandir(path) as it:
            ents = sorted(it, key=lambda e: e.name)
    except OSError:
        return []
    rows = []
    for e in ents:
        try:
            if e.is_file():
                if keep is None or keep.fullmatch(e.name):
                    st = e.stat()
                    rows.append([e.name, st.st_size, st.st_mtime_ns])
            elif depth > 0 and e.is_dir() and e.name not in skip:
                rows.append([e.name, _scan(e.path, keep, depth - 1)])
        except OSError:
            continue
    return rows


def _busy(root: str) -> tuple[bool, bool]:
    """(Run 실행 중, Spec Draft 실행 중) — 계획 서명의 실행 상태 재료."""
    return is_running(root), draft_since(root) is not None


def _starts(root: str) -> tuple[int, int]:
    """(Run 시작 수, Spec Draft 시작 수) — 계획 계산 앞뒤로 읽어 그 사이 시작한 실행을 안다(파일을 되돌리고 끝난 실행 포함)."""
    with _FLAGS_LOCK:
        return tuple(_STARTS.get(store.root_key(root), (0, 0)))


def _spec_paths(root: str, specs: list, refs_dir: str) -> list[str]:
    """스펙 JSON 이 가리키는 파일 경로(store._spec_paths — guide·before_paste·스펙 refs). specs = _scan(specs/) — 그것과 refs_dir 가
    지난번과 같으면 기억한 경로(스펙을 다시 파싱하지 않음)."""
    rk, key = store.root_key(root), (specs, refs_dir)
    hit = _SPEC_PATHS.get(rk)
    if hit is None or hit[0] != key:
        try:
            hit = _SPEC_PATHS[rk] = (key, store._spec_paths(root, refs_dir))
        except OSError:  # 목록을 읽는 사이 specs/ 가 사라지거나 잠김(_scan 과 같게 state 를 500 으로 만들지 않음) — 기억하지 않고 다음에 다시
            return []
    return hit[1]


def _plan_sigs(root: str, m: dict, busy: tuple[bool, bool], refs_v: str | None, mock: bool | None = None) -> tuple[str, str]:
    """(Run 서명, Draft 서명) — 파일 stat 과 매니페스트 일부만(vlm·runner 계산 없음 — state 의 plan_v 가 폴링마다 부름, §2.4 서명).
    Run = jobs·rerolls + 마지막 Prepare 기록(prepare_settings·prepare_inputs — 같은 위젯·같은 프롬프트의 그래프 Prepare 는 jobs 를 같은
    바이트로 두고 이 둘만 처음 기록·갱신하는데, 그것이 apply_pending('지문 없음'·'입력이 바뀜')·notes(Prepare 갈라짐)를 바꾼다 — 빠지면
    plan_v 가 그대로라 Studio 가 낡은 '반영 필요'·해시 복사 끔을 다시 받지 않음, Codex 2차 c2-2) + 원장(cands/<셀>/ 원장 파일) + mock
    스위치 + pipeline.json 지문 + Run 실행 중.
    Draft = 크롭·source·베이스 + specs/·refs.json·glossary.json·layout.json·source.glossary·PSD·레퍼런스 파일(타깃 refs·refs_auto)·
    스펙이 가리키는 파일(guide·before_paste·스펙 refs — prepare_inputs_fp 의 재료라 그림만 바뀌어도 apply_pending '입력이 바뀜'이 되는데,
    specs/ 바로 아래·refs_dir 바로 아래가 아니면 다른 재료로는 잡히지 않음, Codex 3차 c3-1. 경로는 _spec_paths 기억값이라 폴링은 stat 만)·
    refs_dir 목록 + 초안 원장(vlm/ — img 제외) + mock 스위치 + pipeline.json 지문 + Draft 실행 중 + Qwen config.json + 예상 크레딧
    보정(_vlm_caps.json — 같은 기반 폴더의 다른 프로젝트 Draft 도 바꿈).
    실행 설정은 절 값 대신 pipeline.json 지문(stat — 파일이 없으면 유도값이라 위 재료로 정해짐). 매니페스트 rev 전체는 쓰지 않는다 —
    ★·탈락·후보 등록으로 칩이 낡지 않게. 내용 주소 크롭 소스(inputs/<sha>)는 지워지지 않는다고 보고 넣지 않는다(지우면 다음 서명
    변화까지 기억값). busy = 실행 상태(_busy — pipeline_plan 이 한 번 읽어 넘기고 계산 뒤 다시 읽은 값과 비교), refs_v = refs_dir 목록
    지문(_refs_files_v — build_state 는 flags 에서 이미 계산한 값을 넘긴다), mock = 스위치(pipeline_plan 이 한 번 읽어 서명과 계산에
    같이 넘김, None 이면 지금 읽음)."""
    src = m.get("source") or {}
    refs_dir = _refs_dir(m)
    if mock is None:
        mock = _mock_active(root)
    running, drafting = busy
    pfp = store._file_fp(os.path.join(root, pl.PIPELINE_NAME))
    run = [m.get("jobs"), m.get("rerolls"), m.get("prepare_settings"), m.get("prepare_inputs"),
           _scan(os.path.join(root, "cands"), _RUN_LEDGER_RE, 1), mock, pfp, running]
    refs = []
    for c in m["crops"]:
        for r in [x for t in c.get("targets") or [] for x in t.get("refs") or []] + list(c.get("refs_auto") or []):
            p = store._ref_path(r, refs_dir)
            refs.append([p, store._file_fp(p)])
    specs = _scan(os.path.join(root, "specs"))
    refs += [[p, store._file_fp(p)] for p in _spec_paths(root, specs, refs_dir)]
    qdir = _qwen_dir()
    draft = [m["crops"], src, m.get("base"), m.get("work_rect"), specs,
             [store._file_fp(os.path.join(root, n)) for n in (drefs.REFS_NAME, "glossary.json", store.LAYOUT_NAME)],
             store._file_fp(src["glossary"]) if src.get("glossary") else None,
             store._file_fp(src["psd"]) if src.get("psd") else None, refs, refs_v,
             _scan(os.path.join(root, "vlm"), depth=2, skip=("img",)), mock, pfp, drafting,
             store._file_fp(os.path.join(qdir, "config.json")) if qdir else None,
             store._file_fp(os.path.join(os.path.dirname(os.path.abspath(root)), _VLM_CAPS))]
    return _sig("plan-run", run), _sig("plan-draft", draft)


def _run_plans(root: str, m: dict, run_cfg: dict, only: str = "", mock: bool | None = None) -> tuple:
    """노드 _run_plan 과 같은 식: runner.plan_calls(m, root, retry_failed, retry_orphans, only) + split_approval(backend = mock 판정 —
    mock = 이미 읽은 스위치, None 이면 지금 읽음). → (runner, backend, plan, approval). runner 를 못 읽으면 ValueError."""
    runner = _runner()
    if runner is None:
        raise ValueError("runner 를 읽을 수 없음")
    backend = runner.BACKEND_MOCK if (runner.mock_enabled(root) if mock is None else mock) else runner.BACKEND_COMFY_ORG
    plan = runner.plan_calls(m, root, retry_failed=bool(run_cfg["retry_failed"]), retry_orphans=bool(run_cfg["retry_orphans"]),
                             only=only)
    return runner, backend, plan, runner.split_approval(plan, "", backend=backend)


def _run_free(root: str, m: dict, run_cfg: dict, mock: bool | None = None) -> dict:
    """only 없는 Run 계획: by_crop(크롭별 승인 필요·사전 승인·복구·완료·실패·orphaned·실행 중·예상 USD — 옛 셀은 _stale)·pre·free·
    est_usd_pre(재굴림은 approve·only 와 무관하게 Run 실행 때 함께 과금)·running + 비운 선택 필드(hash "" 등). 실패하면 {error}
    — 원장 대체 계산(_ledger_pending)으로 해시 없는 계획을 보이지 않는다."""
    try:
        runner, backend, plan, ap = _run_plans(root, m, run_cfg, mock=mock)
    except Exception as e:  # runner 없음·손상된 매니페스트·원장 — 계획을 보이지 않고 이유만
        return {"error": f"Run 계획을 계산하지 못함: {e}"}
    ids = {c["id"] for c in m["crops"]}
    rows = {c["id"]: copy.deepcopy(_RUN_ZERO) for c in m["crops"]}
    need: dict[str, list] = {}

    def row(e: dict) -> tuple[str, dict]:
        k = e.get("crop_id") if e.get("crop_id") in ids else _STALE
        return k, rows.setdefault(k, copy.deepcopy(_RUN_ZERO))

    for e in ap["needs_approval"]:
        k, r = row(e)
        r["need"] += 1
        need.setdefault(k, []).append(e)
    for key, entries in (("pre", ap["preapproved"]), ("free", ap["free"]), ("done", plan["done"]), ("failed", plan["failed"]),
                         ("orphaned", plan["orphaned"]), ("inflight", plan["inflight"])):
        for e in entries:
            row(e)[1][key] += 1
    for k, entries in need.items():
        rows[k]["est_usd"] = list(runner.estimate_calls(entries))
    return {"hash": "", "backend": backend, "need": 0, "pre": len(ap["preapproved"]), "free": len(ap["free"]), "filtered": 0,
            "est_usd": [0.0, 0.0], "est_usd_pre": list(runner.estimate_calls(ap["preapproved"])), "credits": [0, 0], "reasons": [],
            "by_crop": rows, "running": is_running(root)}


def _run_sel(root: str, m: dict, run_cfg: dict, only: str, mock: bool | None = None) -> dict:
    """선택(only) Run 계획: hash(노드 Run 이 같은 only 로 대조할 실행 전 해시)·need·filtered·est_usd·credits·reasons. only 의 style
    토큰은 뺀다(Run 위젯과 같은 문자열 — pl.strip_style)."""
    try:
        runner, _backend, plan, ap = _run_plans(root, m, run_cfg, pl.strip_style(only), mock)
    except Exception as e:  # _run_free 와 같은 이유
        return {"error": f"Run 계획을 계산하지 못함: {e}"}
    lo, hi = ap["est_usd"]
    reasons: dict[str, int] = {}
    for e in ap["needs_approval"]:
        reasons[e["reason"]] = reasons.get(e["reason"], 0) + 1
    return {"hash": ap["pending_hash"], "need": len(ap["needs_approval"]), "filtered": plan["filtered"], "est_usd": [lo, hi],
            "credits": [round(lo * runner.CREDITS_PER_USD), round(hi * runner.CREDITS_PER_USD)],
            "reasons": [{"reason": r, "n": n} for r, n in reasons.items()]}


def _draft_cfg(root: str, settings: dict, mock: bool | None = None) -> tuple[str, dict]:
    """노드 Spec Draft 와 같은 draft_plan 인자(only 제외) → (백엔드 이름, cfg). model 은 mock 으로 강제돼도 위젯의 모델(노드와 같음 —
    openai 키에 들어감, mock 키는 "mock" 이라 모델이 들어가지 않음). local_qwen 인데 모델 폴더를 모르면(set_qwen_dir 전) ValueError
    — 'nodir' 키로 계산하지 않는다. mock = _draft_family 로."""
    d = settings["draft"]
    dvlm = _vlm()
    _fam, model = dvlm.parse_vlm_choice(d["vlm"])
    family = _draft_family(root, settings, mock)
    if family == "local_qwen" and not _qwen_dir():
        raise ValueError("local_qwen 모델 경로 미설정 — ComfyUI 재시작")
    return dvlm.BACKEND_NAMES[family], {
        "family": family, "model": model, "effort": d["effort"], "image_detail": d["image_detail"],
        "draft_round": int(d["draft_round"]), "mode": d["write_mode"], "concurrency": int(d["concurrency"]),
        "model_dir": _qwen_dir(), "apply_mode": d["apply"]}


def _import_pending(root: str, m: dict, settings: dict) -> list[str]:
    """Draft 해시가 '반영 뒤 확정'인 이유(§2.4): 같은 큐의 Import 가 매니페스트를 바꾸면 초안 키가 바뀐다 — 레이아웃·refs.json 미반영,
    레이어명 + 스펙 _layer_targets 로 Import 가 만들 타깃 ≠ 매니페스트 타깃(store.adopt_stale — Prepare 가드와 같은 판정), 실행 설정
    import 절 ≠ 마지막 Import(source — pl.drift), 마지막 Import 뒤 PSD 파일이 바뀜. 깨진 layout.json·refs.json·스펙(Import 가 멈춤)도."""
    out = []
    src = m.get("source") or {}
    try:
        lay = store.load_layout(root)
    except ValueError:
        out.append("layout.json 이 깨짐 — 다음 Import 가 멈춤")
    else:
        if lay is not None and (src.get("layout") or {}).get("sig") != store.layout_sig(lay):
            out.append(f"레이아웃 미반영(rev {lay['rev']})")
        elif lay is None and src.get("layout"):
            out.append("레이아웃을 치움 — 다음 Import 가 PSD 로 다시 읽음")
    try:
        doc = drefs.load_refs(root)
    except ValueError:
        out.append("refs.json 이 깨짐 — 다음 Import 가 멈춤")
    else:
        refs_dir = _refs_dir(m)
        if _refs_dir_ok(refs_dir):
            n = 0
            for c in m["crops"]:
                r = drefs.resolve_refs(refs_dir, c, doc)
                n += (list(c.get("refs_auto") or []), c.get("refs_main")) != (r["refs_auto"], r["refs_main"])
            if n:
                out.append(f"레퍼런스 미반영({n}크롭)")
    stale, broken = [], []
    for c in m["crops"]:
        try:
            ov = store.load_spec_override(root, c["id"])
        except ValueError:
            broken.append(c.get("nnn") or c["id"])
            continue
        if store.adopt_stale(c, ov):
            stale.append(c.get("nnn") or c["id"])
    if broken:
        out.append(f"스펙 JSON 오류({', '.join(broken)}) — 다음 Import 가 멈춤")
    if stale:
        out.append(f"채택 미반영({', '.join(stale)})")
    imp = [d["field"] for d in pl.drift(settings, m) if d["kind"] == "import"]
    if imp:
        out.append(f"Import 설정이 마지막 Import 와 다름({', '.join(imp)})")
    if src.get("psd") and os.path.isfile(src["psd"]) and store._file_fp(src["psd"]) != src.get("fingerprint"):
        out.append("마지막 Import 뒤 PSD 파일이 바뀜")
    return out


def _draft_free(root: str, m: dict, settings: dict, mock: bool | None = None) -> dict:
    """only 없는 Draft 계획: backend·family·by_crop(대기 사유·갱신 가능·채택 가능 — 채택은 GET /draft 의 [채택] 단추와 같은 조건)·
    warnings(unwritten = 매니페스트 크롭·_project 의 못 쓴 스펙, mock_specs = family mock 일 때 mock 초안이 남은 스펙)·import_pending
    + 비운 선택 필드 + 확인 창 필드(S3b 계약 §2 — 노드 Spec Draft 와 같은 식): pending_style = 화풍 단위(_style) 대기 수(0|1 — 노드
    가지 draft.pending_style), mock_forced = draft.vlm 이 openai 인데 mock 스위치로 mock(노드 header.mock_forced).
    Spec Draft 실행 중이면 {running: true}, 계산 오류(용어집 형식·스펙 경로·local_qwen 모델 폴더 미설정 등)는 {error, hash: null}.
    채택 칩은 draft_plan 이 만든 크롭 문맥(ctx — draft_view 와 같은 인자의 build_crop_ctx)으로 GET /draft 와 같은 vlm.adopt_state
    판정(목록이 아닌 targets·스펙에 안 쓰인 도착 초안·⑥ 이 늘 거절할 노드 값 양보 거절 포함)을 한다 — 크롭마다 draft_view 를 부르면
    문맥을 다시 만들어 레퍼런스 경고가 같은 요청에서 두 번 로그된다."""
    if draft_since(root) is not None:
        return {"running": True}
    try:
        backend, cfg = _draft_cfg(root, settings, mock)
        dvlm = _vlm()
        memo = _draft_memo()
        ctxs, _reqs, _style, plan, _ap = dvlm.draft_plan(root, m, "", backend, only=[], memo=memo, **cfg)
        pending = _import_pending(root, m, settings)
        marked = dvlm.unwritten_specs(root)
        live = {c["id"] for c in m["crops"]} | {dvlm.PROJECT_SPEC_ID}
        unwritten = [s for s in marked if s in live]
        adopt = {}
        for ctx in ctxs:
            kind = why = None
            if ctx.spec_error is None and ctx.crop_id not in marked:
                _value, kind, why = dvlm.adopt_state(root, m, ctx, ctx.spec, family=cfg["family"], memo=memo)
            adopt[ctx.crop_id] = "add" if kind == "add" and not why else None
        mock_specs = dvlm.mock_drafted(root, ctxs, cfg["apply_mode"]) if cfg["family"] == "mock" else 0
        forced = cfg["family"] == "mock" and dvlm.parse_vlm_choice(settings["draft"]["vlm"])[0] == "openai"
    except (ValueError, OSError) as e:  # 용어집·스펙 경로 오류 — run 계획은 그대로 둔다
        return {"error": str(e), "hash": None}
    waits: dict[str, list] = {}
    for u in plan["units"]:
        waits.setdefault(u.crop_id, []).extend(u.reasons)
    refresh = {cid for cid, _est in plan["refresh_available"]}
    return {**copy.deepcopy(_DRAFT_SEL_EMPTY), "backend": backend, "family": cfg["family"],
            "by_crop": {c["id"]: {"wait": waits.get(c["id"], []), "refresh": c["id"] in refresh, "adopt": adopt[c["id"]]}
                        for c in m["crops"]},
            "warnings": {"unwritten": unwritten, "mock_specs": mock_specs}, "import_pending": pending,
            "pending_style": sum(1 for u in plan["units"] if u.crop_id == dvlm.STYLE_ID), "mock_forced": forced}


def _draft_sel(root: str, m: dict, settings: dict, only: str, mock: bool | None = None) -> dict:
    """선택(only) Draft 계획: hash(노드 Spec Draft 가 only = vlm.parse_only(같은 문자열)로 대조할 실행 전 해시)·units·refresh_available·
    skipped·cached·accepted·filtered·est_credits·est_seconds + 확인 창 필드(노드 ui bmk_dp_draft 와 같은 vlm.approval 값):
    orphan_retries·est_usd·reasons(run.reasons 꼴 [{reason, n}])·free(재파싱·재폴링 무료 단위 수). 오류는 {error, hash: null}."""
    try:
        backend, cfg = _draft_cfg(root, settings, mock)
        dvlm = _vlm()
        _c, _r, _s, plan, ap = dvlm.draft_plan(root, m, "", backend, only=dvlm.parse_only(only), memo=_draft_memo(), **cfg)
    except (ValueError, OSError) as e:
        return {"error": str(e), "hash": None}
    return {"hash": ap["pending_hash"],
            "units": [{"crop_id": u.crop_id, "state": u.state, "reasons": list(u.reasons), "est": list(u.est)} for u in plan["units"]],
            "refresh_available": [{"crop_id": cid, "est": list(est)} for cid, est in plan["refresh_available"]],
            "skipped": [{"crop_id": cid, "reason": why} for cid, why in plan["skipped"]],
            "cached": list(plan["cached"]), "accepted": list(plan["accepted"]), "filtered": list(plan["filtered"]),
            "est_credits": list(ap["est_credits"]), "est_seconds": ap["est_seconds"], "orphan_retries": ap["orphan_retries"],
            "est_usd": list(ap["est_usd"]), "reasons": [{"reason": r, "n": n} for r, n in ap["reasons"].items()],
            "free": len(plan["free"])}


# Prepare 설정 갈라짐(pl.drift kind) → 계획 notes 의 이유(앞이 무거운 것 — 한 종류면 문구만, 여럿이면 종류마다 필드)
_PREPARE_DRIFT_WHY = (("cell", "그래프와 Studio 를 번갈아 돌리면 생성 조건이 바뀌어 다시 과금 대상"),
                      ("calls", "생성 조건마다 호출을 늘리면 완료된 생성 조건마다 새 호출이 생겨 다시 과금 대상(생성 승인 필요)"),
                      ("cost", "생성 조건은 그대로, 승인 코드·비용이 바뀜"))


def _plan_notes(root: str, m: dict, settings: dict, confirm) -> list[str]:
    """계획 머리 줄 — Run·Draft 공통으로 '반영 뒤 다시 계산'(§3.3)인 설정 쪽 이유: 설정 미확인(confirm), Prepare 설정 갈라짐(셀/호출/비용 —
    옛 프로젝트는 jobs 와 비교), Import 설정 갈라짐. 파일 쪽 '반영 필요'는 _apply_pending. 매번 계산한다."""
    out = []
    if confirm:
        out.append(f"설정 미확인: {', '.join(confirm)} — 반영 전에 설정 패널에서 확인·저장")
    rows = pl.drift(settings, m)
    pre = [d for d in rows if d["section"] == "prepare"]
    if pre:
        last = "매니페스트" if pl.prepare_record(m)[0] == "prepare_settings" else "jobs 추정"  # 옛 프로젝트 = jobs 최다값
        what = ", ".join(f"{d['field']}({last} {d['manifest']} · Studio {d['settings']})" for d in pre)
        why = [(text, [d["field"] for d in pre if d["kind"] == kind]) for kind, text in _PREPARE_DRIFT_WHY]
        why = [(text, fs) for text, fs in why if fs]
        out.append(f"Prepare 설정 갈라짐: {what} — "
                   + (why[0][0] if len(why) == 1 else "; ".join(f"{text}({', '.join(fs)})" for text, fs in why)))
    imp = [d["field"] for d in rows if d["kind"] == "import"]
    if imp:
        out.append(f"Import 설정이 마지막 Import 와 다름({', '.join(imp)}) — 반영하면 크롭·타깃·레퍼런스가 바뀔 수 있음")
    return out


def _apply_pending(root: str, m: dict, settings: dict) -> list[str]:
    """'반영 필요'(§2.2·§3.3 — 무료 반영 apply 가 풀 차이) 사유 = 같은 큐의 Import 가 매니페스트를 바꿀 차이(_import_pending — 레이아웃·
    레퍼런스·채택·Import 설정 미반영, 깨진 파일) + prepare_inputs 불일치(마지막 Prepare 뒤 스펙·레이아웃·레퍼런스·용어집이 바뀜 —
    초안 쓰기·채택 포함). 지문만 보면 Import 뒤·Prepare 앞에 끼어든 layout.json·refs.json 쓰기(새 파일이 지문에 들어감)를 놓치고,
    Import 쪽만 보면 스펙 문장 수정을 놓친다 — 둘을 합친 이 하나가 Studio 머리줄·반영 칸의 판정이고 S3b run apply_needed 는 여기에 Prepare
    설정 갈라짐을 더한다(pipeline_prompt ⑦). 지문은 Prepare 를
    한 번이라도 돈 프로젝트(jobs·prepare_settings·prepare_inputs 중 하나 — 첫 Prepare 부터 채택 가드면 jobs·지문이 없음)만 본다.
    매번 계산한다(Draft 계획의 import_pending 은 기억값이고 Spec Draft 실행 중·오류면 없다)."""
    try:
        out = _import_pending(root, m, settings)
    except (ValueError, OSError) as e:  # 잠긴 파일 등 — 판정할 수 없으면 반영 필요로
        out = [f"반영 상태를 판정하지 못함({e})"]
    if m.get("jobs") or "prepare_inputs" in m or "prepare_settings" in m:
        want = m.get("prepare_inputs")
        try:
            now = store.prepare_inputs_fp(root, m)
        except (OSError, ValueError) as e:  # 잠긴 폴더 등
            out.append(f"Prepare 입력 지문을 계산하지 못함({e})")
        else:
            if not want:
                out.append("Prepare 입력 지문 없음(지문을 남기기 전 Prepare)")
            elif now != want:
                out.append("Prepare 입력이 바뀜(마지막 Prepare 뒤 스펙·레이아웃·레퍼런스·용어집)")
    return out


def _plan_ok(plan: dict) -> bool:
    """계산 오류(run·draft error)가 없는 계획 — 오류는 잠긴 원장 같은 일시 오류일 수 있어 다시 쓰지 않는다(다음 요청이 다시 계산,
    state 는 plan_v null 로 칩을 흐림)."""
    return not any("error" in (plan[k] or {}) for k in ("run", "draft"))


def pipeline_plan(root: str, settings: dict | None = None, *, only_ids, style: bool, confirm=()) -> dict:
    """GET /pipeline/plan 본문(STUDIO_S3_SPEC §2.4 — aiohttp 없음, 라우트는 asyncio.to_thread). settings 가 None 이면(라우트) 서명을
    잡은 뒤 pl.current(root, m, deep=True)(설정 패널과 같은 값)와 그 confirm(notes 의 '설정 미확인')을 읽는다 — 그 사이 저장된
    설정·스펙은 다음 서명에서 달라져 다시 계산된다(설정을 먼저 읽으면 옛 설정으로 계산한 결과가 새 서명으로 기억됨). 넘기면(노드 대조
    시험) 그 값과 confirm 으로 계산한다.
    Run = 노드 _run_plan 과 같은 식을 두 번: only 없는 계획(by_crop·pre·free·est_usd_pre·running) + 선택 계획(hash·need·filtered·
    est_usd·credits·reasons) — Σ by_crop[선택].need = need. runner 를 못 읽거나 계획이 실패하면 run {error}(대체 계산 없음).
    Draft = vlm.draft_plan(노드 Spec Draft 와 같은 인자 — family 는 openai + mock 스위치면 mock, model_dir = set_qwen_dir 값, only =
    vlm.parse_only(정규 only)) 도 두 번(by_crop·warnings·import_pending / 선택 해시·단위). Spec Draft 실행 중이면 draft {running: true},
    계산 오류는 draft {error, hash: null}(run 은 그대로).
    only_ids = 고른 crop_id(정규형 pl.only_string — 매니페스트 크롭만, style 토큰은 style 이고 apply=full 일 때만). 비면 선택 계획 없이
    선택 필드는 빈 값(hash "" — 빈 only 는 노드에서 '전부'라 그 해시를 보이지 않음, 정본 alt.no_selection).
    sig = "r:<Run 서명>|d:<Draft 서명>"(_plan_sigs) + 선택이 있으면 "|o:<정규 only>". 같은 서명·같은 설정(계산에 쓴 설정의 지문 —
    다른 settings 로 부르면 다시 계산)·같은 선택이면 기억한 결과(computed_at 그대로)를 계산 없이 — notes(설정 쪽 '반영 뒤 다시 계산')·
    apply_pending('반영 필요' — Import 미반영 + prepare_inputs 불일치, _apply_pending)은 매번 계산. 오류가 있는
    계획은 다시 쓰지 않는다(_plan_ok — 선택 부분은 only 없는 계획도 오류 없을 때만 기억). 계산 중 Run·Spec Draft 가 시작·끝나면
    (서명에 넣은 실행 상태 ≠ 계산 뒤 실행 상태·계산이 본 run.running·draft {running}, 또는 그 사이 시작 수(_starts)가 늚 — 시작해서
    파일을 되돌리고 끝난 실행 포함) 이번 계산은 기억하지 않는다 — 실행 중 값이 '실행 중 아님' 서명으로 남으면 그 실행이 파일을 바꾸지
    않고 끝났을 때(초안 형식 오류·로그아웃 401 되돌림) 다음 파일 변화까지 그대로 다시 쓰인다. 그 응답은 계산 뒤 실행 상태로 실행 중
    표시를 하고(Spec Draft 가 돌면 draft {running: true} 만, Run 이 돌면 run.running — §2.4, 계산이 그 실행을 못 봤어도) sig 는 계산 중
    돈 실행을 '실행 중'으로 넣은 서명이다 — 실행이 끝난 뒤의 state plan_v 와 달라 Studio 가 낡은 계획으로 본다(해시 복사 끔 — 진행
    모드는 같은 plan_v 에 한 번 더 받고, 그 밖의 모드는 머리줄 [다시 계산]).
    only 없는 계획은 state 의 plan_v·draft_chip 이 쓴다(plan_v 는 파일 서명만 본다). 파일은 apply=full 일 때
    화풍 입력 vlm/img(내용 주소)만 쓸 수 있다. 매니페스트는 한 번 읽고 manifest_lock 은 쥐지 않는다.
    mock 스위치도 한 번 읽어 서명·네 계산(run·draft × free·선택)에 같이 넘긴다 — 계산마다 다시 읽으면 계산 중 _MOCK_API 가 바뀔 때
    'backend mock + 실제 백엔드 해시' 계획이 mock 서명으로 기억돼 Studio 가 그 해시를 '[MOCK]' 페이지 상자로 승인할 수 있음(r1-07)."""
    m = store.load_manifest(root)
    starts, busy = _starts(root), _busy(root)
    mock = _mock_active(root)
    refs_v = _refs_files_v(_refs_dir(m))
    r_sig, d_sig = _plan_sigs(root, m, busy, refs_v, mock)
    if settings is None:
        cur = _route_settings(root, m)
        settings, confirm = cur["settings"], cur["confirm"]
    ids = [c["id"] for c in m["crops"]]
    only = pl.only_string(list(only_ids), bool(style) and settings["draft"]["apply"] == "full", known=ids) if only_ids else ""
    chosen = set(pl.strip_style(only).split(",")) if only else set()
    sig = f"r:{r_sig}|d:{d_sig}"
    skey = _sig("plan-settings", settings)
    rk = store.root_key(root)
    with _PLAN_MEMO_LOCK:
        memo = _PLAN_MEMO.setdefault(rk, {"free": None, "sel": {}})
        f = memo["free"]
        free = f if f is not None and (f["sig"], f["settings"]) == (sig, skey) and _plan_ok(f) else None
        sel = memo["sel"].get((sig, skey, only)) if only else None
    new_free, new_sel = free is None, bool(only) and sel is None
    if new_free:
        free = {"sig": sig, "settings": skey, "computed_at": _now_text(), "run": _run_free(root, m, settings["run"], mock),
                "draft": _draft_free(root, m, settings, mock)}
    if new_sel:
        sel = {"computed_at": _now_text(),
               "run": None if "error" in free["run"] else _run_sel(root, m, settings["run"], only, mock),
               "draft": None if "hash" not in free["draft"] or free["draft"]["hash"] is None
               else _draft_sel(root, m, settings, only, mock)}
    after, moved = _busy(root), tuple(a != b for a, b in zip(starts, _starts(root)))
    seen = (free["run"].get("running", busy[0]), "running" in free["draft"])
    steady = after == busy == seen and not any(moved)
    if steady:  # 계산 중 실행 상태가 그대로이고 시작한 실행이 없을 때만
        with _PLAN_MEMO_LOCK:
            if new_free:
                memo["free"] = free
                memo["sel"] = {k: v for k, v in memo["sel"].items() if k[:2] == (sig, skey)}
            if new_sel and _plan_ok(sel) and _plan_ok(free):  # free 오류로 비운 칸(None)을 기억하면 free 가 회복된 뒤에도 빈 선택 필드(hash "")
                memo["sel"][(sig, skey, only)] = sel
                while len(memo["sel"]) > _PLAN_SEL_MAX:
                    memo["sel"].pop(next(iter(memo["sel"])))
    run, draft = copy.deepcopy(free["run"]), copy.deepcopy(free["draft"])
    if sel is not None and sel["run"] is not None:
        run = copy.deepcopy(sel["run"]) if "error" in sel["run"] else {**run, **copy.deepcopy(sel["run"])}
    if sel is not None and sel["draft"] is not None:
        draft = copy.deepcopy(sel["draft"]) if "error" in sel["draft"] else {**draft, **copy.deepcopy(sel["draft"])}
    for cid, row in (run.get("by_crop") or {}).items():
        row["selected"] = cid in chosen
    if after[0] and "error" not in run:
        run["running"] = True
    if after[1]:
        draft = {"running": True}
    if not steady:
        ran = tuple(any(x) for x in zip(busy, after, seen, moved))
        if ran != busy:
            r_sig, d_sig = _plan_sigs(root, m, ran, refs_v, mock)
            sig = f"r:{r_sig}|d:{d_sig}"
    return {"sig": sig + (f"|o:{only}" if only else ""), "computed_at": (sel or free)["computed_at"],
            "notes": _plan_notes(root, m, settings, confirm), "apply_pending": _apply_pending(root, m, settings), "run": run, "draft": draft}


def _plan_route(root: str, ids: list[str], style: bool) -> dict | None:
    """GET /pipeline/plan(라우트 스레드): 계획 잠금을 기다리지 않는다 — 못 잡으면 None(202 busy, JS 는 0.5초 뒤 한 번 다시)."""
    if not _PLAN_LOCK.acquire(blocking=False):
        return None
    try:
        return pipeline_plan(root, only_ids=ids, style=style)
    finally:
        _PLAN_LOCK.release()


def _plan_free(root: str) -> dict | None:
    with _PLAN_MEMO_LOCK:
        memo = _PLAN_MEMO.get(store.root_key(root))
        return memo["free"] if memo else None


def pipeline_prompt(root: str, stage, approve=None, only=None, expect=None) -> dict:
    """GET /pipeline/prompt(S3b 계약 §1) → {stage, prompt, targets, hash, only, sig: "<pipeline rev>:<매니페스트 rev>"}. 앞에서 걸리면
    거기서 끝난다(파일을 쓰지 않음 — 계획 라우트처럼 apply=full 인 draft 의 ⑧ 계획만 화풍 입력 vlm/img 내용 주소 파일을 쓸 수 있음,
    manifest_lock 을 쥐지 않음):
    ① 모르는 stage 400 ② 인자 400 — run·draft 는 approve(12자 소문자 hex)와 비지 않은 only(빈 only 는 '전부'라 승인 집합이 계획과
    다름), reroll·무료 stage 는 approve·only 없음 ③ only 정규화(run·draft — 쉼표·공백 토큰, style 토큰은 draft.apply full 일 때만
    남음, 매니페스트 밖 크롭 400) = 응답 only(탭이 요청과 대조) ④ pl.prompt_gate 409 본문 그대로 ⑤ expect(선택, 모든 stage)가
    지금 sig 와 다르면 409 stale_settings ⑥ 실행 중 409 running — run 은 Run 또는 Spec Draft, draft 는 Spec Draft(실행 중 계획의
    해시는 일시값) ⑦ 반영 필요 409 apply_needed — run 은 계획 apply_pending 과 같은 판정 + Prepare 설정 갈라짐(계획 notes 의 그 줄 —
    템플릿 Prepare 가 jobs 를 바꿔 해시가 달라짐), draft 는 같은 큐의 Import 가 초안 키를 바꿀 때만(_import_pending — prepare_inputs
    불일치·Prepare 설정은 초안 키와 무관, 판정 실패는 run 처럼 '판정하지 못함' 사유) ⑧ 계획 잠금을 _PROMPT_LOCK_WAIT 초까지 기다려(못 잡으면 409
    busy) 기억값 없이 지금 상태의 선택 계획(_run_sel·_draft_sel) — 계산 뒤 ⑥ 을 다시 본다: 잠금을 기다리고 계산하는 사이 ⑥ 의
    실행이 돌거나 시작 수(_starts)가 늘었으면(시작해서 파일을 되돌리고 끝난 실행 포함 — 계획 라우트 steady 와 같은 재료) 그 해시는
    일시값이라 409 running, 그다음 오류 409 plan_error, 해시 "" 409 nothing_to_approve, 해시 ≠ approve 409 stale(지금 해시)
    ⑨ pl.build_prompt.
    reroll 은 ⑥–⑧ 을 보지 않는다(사전 승인분만 과금하고 템플릿의 Import·Prepare 가 반영을 겸함 — R 경로와 같음). 매니페스트·
    pipeline.json 은 한 번 읽은 같은 값으로 판정·해시·템플릿까지(두 번 읽으면 그 사이 다른 창·그래프가 바꾼 값이 판정 없이 템플릿에
    실림 — r1-21). targets = 무료 null, 유료 [출력 노드 키], hash = 대조한 approve(reroll·무료 ""), only = 정규 only(reroll·무료 "")."""
    if stage not in pl.STAGES:
        raise _Reject(400, f"모르는 stage 입니다: {stage!r} (stage: {', '.join(pl.STAGES)})")
    approve, only = approve or "", only or ""
    paid = stage in ("run", "draft")
    if paid and not pl._HASH_RE.fullmatch(approve):
        raise _Reject(400, f"{stage} 는 승인 해시(12자 소문자 hex)가 필요합니다: {approve!r}")
    if paid and not only.strip():
        raise _Reject(400, f"{stage} 는 only(고른 크롭 명시 목록)가 필요합니다 — 빈 only 는 '전부'라 승인 집합이 계획과 다름")
    if not paid and (approve or only):
        raise _Reject(400, f"reroll 은 approve·only 없이(사전 승인 재굴림 전체 — only 무관): approve={approve!r} only={only!r}"
                      if stage == "reroll" else f"무료 stage({stage})에는 approve·only 를 싣지 않습니다")
    m = store.load_manifest(root)
    cur = pl.current(root, m, deep=True)
    settings = cur["settings"]
    if paid:  # 깨진 설정은 ④ 가 409 — 그 전에도 크롭 형식·매니페스트 밖 크롭은 400
        toks = pl._only_tokens(only)
        style = any(pl._is_style(t) for t in toks) and settings is not None and settings["draft"]["apply"] == "full"
        only = pl.only_string([t for t in toks if not pl._is_style(t)], style, known=[c["id"] for c in m["crops"]])
    gate = pl.prompt_gate(root, stage, m, cur=cur)
    if gate is not None:
        raise _Reject(409, gate.pop("error"), **gate)
    sig = f"{cur['rev']}:{m['rev']}"
    if expect and expect != sig:
        raise _Reject(409, f"실행 설정·매니페스트가 바뀜(요청 {expect} · 지금 {sig}) — 계획을 다시 받아 확인", reason="stale_settings",
                      stale=True, sig=sig)
    if paid:
        starts, (running, drafting) = _starts(root), _busy(root)  # 시작 수가 먼저 — 그 뒤 시작한 실행은 _busy 나 재확인의 시작 수가 봄
        if drafting or (stage == "run" and running):
            raise _Reject(409, f"{'Spec Draft' if drafting else 'Run'} 실행 중 — 끝난 뒤 계획을 다시 받아 승인", reason="running", stale=True)
        if stage == "run":
            pending = _apply_pending(root, m, settings)
            pre = [d["field"] for d in pl.drift(settings, m) if d["section"] == "prepare"]
            if pre:  # 템플릿 Prepare 가 이 설정으로 jobs 를 다시 써 노드 해시 ≠ approve(늘 드라이런) — Studio 가 흐리는 기준과 같음(r1-06)
                pending.append(f"Prepare 설정 갈라짐({', '.join(pre)}) — 템플릿의 Prepare 가 jobs 를 바꿔 승인 해시가 달라짐")
        else:
            try:
                pending = _import_pending(root, m, settings)
            except (ValueError, OSError) as e:  # _apply_pending 과 같게 — 잠긴 레퍼런스 폴더 등(500 이면 Studio 가 계획을 다시 받지 않음)
                pending = [f"반영 상태를 판정하지 못함({e})"]
        if pending:
            raise _Reject(409, f"반영 필요 — 먼저 [반영]: {'; '.join(pending)}", reason="apply_needed", stale=True, pending=pending)
        if not _PLAN_LOCK.acquire(timeout=_PROMPT_LOCK_WAIT):
            raise _Reject(409, f"계획 계산이 밀려 있습니다({_PROMPT_LOCK_WAIT:g}초) — 잠시 뒤 다시", reason="busy")
        try:
            sel = _run_sel(root, m, settings["run"], only) if stage == "run" else _draft_sel(root, m, settings, only)
        finally:
            _PLAN_LOCK.release()
        moved, (running, drafting) = [a != b for a, b in zip(starts, _starts(root))], _busy(root)
        if drafting or moved[1] or (stage == "run" and (running or moved[0])):
            raise _Reject(409, f"계획 계산 중 {'Spec Draft' if drafting or moved[1] else 'Run'} 실행이 시작됨 — 끝난 뒤 계획을 다시 받아 승인",
                          reason="running", stale=True)
        if "error" in sel:
            raise _Reject(409, sel["error"], reason="plan_error")
        if not sel["hash"]:
            raise _Reject(409, "승인할 것이 없습니다 — 지금 계획의 승인 대상 0건(사전 승인 다시 생성은 R)", reason="nothing_to_approve")
        if sel["hash"] != approve:
            raise _Reject(409, f"승인한 계획이 지금과 다릅니다 — 계획을 다시 받아 승인(지금 승인 코드 {sel['hash']})", reason="stale",
                          stale=True, hash=sel["hash"])
    return {"stage": stage, "prompt": pl.build_prompt(stage, settings, root, approve=approve, only=only),
            "targets": None if stage in pl.FREE_STAGES else [pl.OUTPUT_KEY[stage]], "hash": approve, "only": only, "sig": sig}


def pipeline_progress(root: str) -> dict:
    """GET /pipeline/progress(§3.5): Run = 원장(runner.plan_calls 기본 옵션 — 어느 탭·그래프가 큐에 넣었든 같음)의 완료·실행 중·실패·
    orphaned·남은 승인분·사전 승인 + 실행 중·drain. Draft = draft_active 가 실행 중일 때만 vlm.ledger_progress(시작 시각 뒤 바뀐 원장
    키의 상태 수 — 파일 시각 해상도만큼 _SINCE_SLACK 당겨 셈) + since(시작 시각), 아니면 null(vlm 을 불러오지 않음)."""
    runner = _runner()
    if runner is None:
        raise _Reject(500, "runner 를 읽을 수 없어 진행을 셀 수 없습니다")
    running = is_running(root)
    plan = runner.plan_calls(store.load_manifest(root), root)
    ap = runner.split_approval(plan, "", backend=runner.BACKEND_MOCK if runner.mock_enabled(root) else runner.BACKEND_COMFY_ORG)
    since = draft_since(root)
    return {"running": running, "drain": running and drain_requested(root),
            "run": {"done": len(plan["done"]), "inflight": len(plan["inflight"]), "failed": len(plan["failed"]),
                    "orphaned": len(plan["orphaned"]), "need": len(ap["needs_approval"]), "pre": len(ap["preapproved"])},
            "draft": None if since is None else {**_vlm().ledger_progress(root, since - _SINCE_SLACK), "since": since}}


def _adopt_crop_id(m: dict, crop_id) -> str:
    """채택 라우트의 크롭: 형식이 틀리면 400, 매니페스트에 없으면 404(_project 는 vlm 이 400 — 매니페스트 크롭만 채택 대상)."""
    if not isinstance(crop_id, str) or not _CROP_ID_RE.fullmatch(crop_id):
        raise _Reject(400, "crop 형식이 잘못되었습니다")
    if crop_id != _vlm().PROJECT_SPEC_ID and _crop(m, crop_id) is None:
        raise _Reject(404, f"크롭이 없습니다: {crop_id}")
    return crop_id


def draft_get(root: str, crop_id) -> dict:
    """GET /draft(§3.4·§6): 크롭의 초안 제안·채택 상태(vlm.draft_view — family = 지금 초안 백엔드, 보드 평탄화 memo). 쓰지 않는다."""
    m = store.load_manifest(root)
    cid = _adopt_crop_id(m, crop_id)
    cur = _route_settings(root, m)
    return _vlm().draft_view(root, m, cid, family=_draft_family(root, cur["settings"]), memo=_draft_memo())


def draft_adopt(root: str, data: dict) -> dict:
    """POST /draft/adopt(§2.5 D8): 스펙 _layer_targets 채택·수정·해제(vlm.adopt_write — family = 지금 초안 백엔드(None 아님),
    apply_mode = pipeline.draft.apply). 채택하지 않는 이유(AdoptRefused)는 400 {error, reason|rows}, seen·crop_sig·value 경합
    (SpecConflict)은 _guard 의 409. preview = 같은 검사, 쓰지 않음. 반영은 다음 무료 반영(Import).
    draft.apply 가 아직 확인 전(confirm — 스펙에 기록되지 않아 유도값은 노드 기본값)이면 warnings 맨 앞에 '초안 대기는 추정값' 한 줄
    (확인 창은 초안 대기 줄 바로 뒤에 warnings 를 보인다 — full 로 돌리는 프로젝트의 유료 재초안 대기를 '없음'으로 믿지 않게)."""
    preview = data.get("preview", False)
    if not isinstance(preview, bool):
        raise _Reject(400, "preview 는 true/false 여야 합니다")
    m = store.load_manifest(root)
    cid = _adopt_crop_id(m, data.get("crop_id"))
    cur = _route_settings(root, m)
    apply_mode = cur["settings"]["draft"]["apply"]
    dvlm = _vlm()
    try:
        out = dvlm.adopt_write(root, m, cid, data.get("op"), data.get("text"), seen=data.get("seen"),
                               crop_sig=data.get("crop_sig"), value=data.get("value"), family=_draft_family(root, cur["settings"]),
                               apply_mode=apply_mode, preview=preview, memo=_draft_memo())
    except dvlm.AdoptRefused as e:
        raise _Reject(400, str(e), **e.extra) from None
    if "draft.apply" in cur["confirm"]:
        out["warnings"].insert(0, f"초안 apply 미확인 — 초안 대기는 {apply_mode}(추정값) 기준, 설정 패널에서 확인·저장")
    return out


# ══════════════════════════════════════════════════════════════════════
# HTTP 핸들러
# ══════════════════════════════════════════════════════════════════════
def _dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _json(data: dict, status: int = 200) -> web.Response:
    return web.json_response(data, status=status, dumps=_dumps,
                             headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


def _conflicts() -> tuple:
    """_guard 의 409(새로고침·다시 읽기) 예외: 레이아웃·레퍼런스·실행 설정 rev 경합(LayoutConflict·RefsConflict·PipelineConflict)과
    채택 쓰기 경합(vlm.SpecConflict — _vlm() 이 vlm 을 읽은 뒤에만 넣는다: 그 전에는 날 수 없고, state·layout·refs 경로의 오류
    처리가 vlm 을 불러오지 않게)."""
    out = (store.LayoutConflict, drefs.RefsConflict, pl.PipelineConflict)
    return out + (_vlm().SpecConflict,) if _vlm.cache_info().currsize else out


def _guard(fn):
    @functools.wraps(fn)
    async def wrapped(request: web.Request) -> web.StreamResponse:
        try:
            return await fn(request)
        except _Reject as e:
            return _json({"error": e.message, **e.extra}, status=e.status)
        except dmask.MaskConflict as e:  # 마스크 편집 seen·★ 경합 — 409 본문에 reason(changed·not_picked)을 싣는다(mask_api.json)
            return _json({"error": str(e), **e.extra}, status=409)
        except _conflicts() as e:  # rev·지문이 요청과 다름(다른 창·노드에서 바뀜)·이미 있음·없음 — 새로고침
            return _json({"error": str(e)}, status=409)
        except ValueError as e:  # store·refs 검증(매니페스트가 사라짐·형식 오류 등)
            return _json({"error": str(e)}, status=400)
        except FileNotFoundError as e:  # refs: 시트 목록 밖·없는 컷 요청·레퍼런스 파일
            return _json({"error": str(e)}, status=404)
        except FileExistsError as e:  # refs: 같은 이름이 있음(덮어쓰지 않음 — D5)
            return _json({"error": str(e)}, status=409)
        except OSError as e:  # 백신·동기화·탐색기 미리보기가 파일을 잡음 — aiohttp 의 텍스트 500 대신 보드가 읽는 JSON
            logger.warning(f"{_TAG} Board: {request.path} 파일 오류: {e}")
            return _json({"error": f"파일을 읽거나 쓰지 못했습니다(다른 프로그램이 잠갔을 수 있음): {e}"}, status=500)
    return wrapped


def _root_param(value) -> str:
    root = store.resolve_board_root(value)
    if root is None:
        raise _Reject(404, _ROOT_HINT)
    return root


async def _body(request: web.Request) -> dict:
    if request.content_type != "application/json":
        raise _Reject(415, "Content-Type 은 application/json 이어야 합니다")
    if request.content_length is not None and request.content_length > _MAX_BODY:
        raise _Reject(413, "요청 본문이 너무 큽니다")
    raw = await request.read()
    if len(raw) > _MAX_BODY:
        raise _Reject(413, "요청 본문이 너무 큽니다")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise _Reject(400, "JSON 본문을 읽을 수 없습니다") from None
    if not isinstance(data, dict):
        raise _Reject(400, "JSON 본문은 객체여야 합니다")
    return data


async def _png_body(request: web.Request) -> bytes:
    """POST /mask/save 본문 — image/png 만(415), _MAX_PNG_BODY 이하(content_length 먼저, 다 읽은 뒤 다시 — 413)."""
    if request.content_type != "image/png":
        raise _Reject(415, "Content-Type 은 image/png 이어야 합니다")
    if request.content_length is not None and request.content_length > _MAX_PNG_BODY:
        raise _Reject(413, "요청 본문이 너무 큽니다")
    raw = await request.read()
    if len(raw) > _MAX_PNG_BODY:
        raise _Reject(413, "요청 본문이 너무 큽니다")
    return raw


def _field(data: dict, name: str, rx: re.Pattern, required: bool = True) -> str | None:
    v = data.get(name)
    if v is None and not required:
        return None
    if not isinstance(v, str) or not rx.fullmatch(v):
        raise _Reject(400, f"{name} 형식이 잘못되었습니다")
    return v


@_guard
async def board_redirect(request: web.Request) -> web.StreamResponse:
    qs = request.query_string
    raise web.HTTPFound(request.path + "/" + (f"?{qs}" if qs else ""))


@_guard
async def board_static(request: web.Request) -> web.StreamResponse:
    name = request.match_info.get("name") or "board.html"
    ctype = _STATIC.get(name)
    if ctype is None:
        raise _Reject(404, "보드 파일이 아닙니다")
    data = await asyncio.to_thread(_read_bytes, os.path.join(WEB_DIR, name))
    if data is None:
        raise _Reject(404, f"보드 파일이 없습니다: web_design_patch/{name}")
    return web.Response(body=data, content_type=ctype, charset="utf-8",
                        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@_guard
async def state_handler(request: web.Request) -> web.StreamResponse:
    root = _root_param(request.query.get("root"))
    raw = request.query.get("since_rev")
    since = None
    if raw not in (None, ""):
        if not re.fullmatch(r"\d{1,12}", raw):
            raise _Reject(400, "since_rev 는 0 이상의 정수여야 합니다")
        since = int(raw)
    return _json(await asyncio.to_thread(build_state, root, since))


@_guard
async def thumb_handler(request: web.Request) -> web.StreamResponse:
    q = request.query
    root = _root_param(q.get("root"))
    raw = q.get("size") or str(GRID_SIZE)
    if not raw.isdigit():
        raise _Reject(400, "size 는 정수여야 합니다")
    data, sig = await asyncio.to_thread(render_thumb, root, q.get("kind") or "", q.get("key") or "", int(raw))
    cache = "private, max-age=604800, immutable" if q.get("v") == sig else "no-cache"
    return web.Response(body=data, content_type="image/jpeg",
                        headers={"Cache-Control": cache, "ETag": f'"{sig}"', "X-Content-Type-Options": "nosniff"})


@_guard
async def canvas_handler(request: web.Request) -> web.StreamResponse:
    q = request.query
    root = _root_param(q.get("root"))
    if q.get("kind") == "region":
        data, sig = await asyncio.to_thread(render_region, root, q.get("crop"), q.get("key"), q.get("v"))
    else:
        data, sig = await asyncio.to_thread(render_canvas, root, q.get("kind") or "", q.get("v"))
    cache = "private, max-age=604800, immutable" if q.get("v") == sig else "no-cache"
    return web.Response(body=data, content_type="image/jpeg",
                        headers={"Cache-Control": cache, "ETag": f'"{sig}"', "X-Content-Type-Options": "nosniff"})


@_guard
async def seams_handler(request: web.Request) -> web.StreamResponse:
    root = _root_param(request.query.get("root"))
    return _json(await asyncio.to_thread(seams_state, root, request.query.get("v")))


def _file_path(root: str, rel) -> str:
    if not isinstance(rel, str) or not 0 < len(rel) <= 300 or any(ch in rel for ch in "\\:\x00"):
        raise _Reject(400, "rel 형식이 잘못되었습니다")
    try:
        path = store.resolve_path(root, rel)
    except ValueError:
        raise _Reject(400, "프로젝트 폴더 밖이거나 잘못된 경로입니다") from None
    parts = rel.replace("\\", "/").strip("/").split("/")
    if parts[0] not in _FILE_DIRS or os.path.splitext(path)[1].lower() not in _IMG_EXTS:
        raise _Reject(400, f"이미지 파일({'/'.join(_FILE_DIRS)} 아래 {', '.join(_IMG_EXTS)})만 열 수 있습니다")
    real_root = os.path.realpath(root)
    real = os.path.realpath(path)
    if os.path.commonpath([real_root, real]) != real_root:
        raise _Reject(400, "프로젝트 폴더 밖 경로입니다")
    if not os.path.isfile(real):
        raise _Reject(404, f"파일이 없습니다: {rel}")
    return real


@_guard
async def file_handler(request: web.Request) -> web.StreamResponse:
    root = _root_param(request.query.get("root"))
    path = await asyncio.to_thread(_file_path, root, request.query.get("rel"))
    ext = os.path.splitext(path)[1].lower()
    ctype = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}[ext]
    data = await asyncio.to_thread(_read_bytes, path)
    if data is None:
        raise _Reject(404, "파일이 없습니다")
    return web.Response(body=data, content_type=ctype,
                        headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"})


@_guard
async def pick_handler(request: web.Request) -> web.StreamResponse:
    data = await _body(request)
    root = _root_param(data.get("root"))
    crop_id = _field(data, "crop_id", _CROP_ID_RE)
    tid = _field(data, "tid", _TID_RE)
    key = _field(data, "key", _KEY_RE)
    mode = data.get("mode")
    if mode not in ("set", "add", "remove"):
        raise _Reject(400, "mode 는 set, add, remove 중 하나여야 합니다")
    res, rev = await asyncio.to_thread(apply_pick, root, crop_id, tid, key, mode)
    return _json({"ok": True, "rev": rev, **res})


@_guard
async def reject_handler(request: web.Request) -> web.StreamResponse:
    data = await _body(request)
    root = _root_param(data.get("root"))
    key = _field(data, "key", _KEY_RE)
    value = data.get("value")
    if not isinstance(value, bool):
        raise _Reject(400, "value 는 true/false 여야 합니다")
    res, rev = await asyncio.to_thread(apply_reject, root, key, value)
    return _json({"ok": True, "rev": rev, "key": key, **res})


def _reroll_and_count(root: str, count: int, sel: dict) -> dict:
    res, rev = apply_reroll(root, count, **sel)
    pending, preapproved = _pending(root, store.load_manifest(root))
    return {"ok": True, "rev": rev, **res, "cell_pending": pending.get(res["cell_key"], 0),
            "pending_calls": sum(pending.values()), "pending_preapproved": preapproved}


@_guard
async def reroll_handler(request: web.Request) -> web.StreamResponse:
    data = await _body(request)
    root = _root_param(data.get("root"))
    count = data.get("count", 1)
    if data.get("cell_key") is not None:
        sel = {"cell_key": _field(data, "cell_key", _CELL_RE)}
    else:
        sel = {"crop_id": _field(data, "crop_id", _CROP_ID_RE), "tid": _field(data, "tid", _TID_RE),
               "variant": _field(data, "variant", _VARIANT_RE, required=False)}
    return _json(await asyncio.to_thread(_reroll_and_count, root, count, sel))


@_guard
async def drain_handler(request: web.Request) -> web.StreamResponse:
    data = await _body(request)
    root = _root_param(data.get("root"))
    if not is_running(root):  # 실행 밖에서 건 플래그가 다음 Run 을 막지 않게 아무것도 하지 않는다
        return _json({"ok": True, "drain": False, "running": False})
    set_drain(root)
    logger.info(f"{_TAG} Board: drain 요청 {os.path.basename(root)}")
    return _json({"ok": True, "drain": True, "running": True})


@_guard
async def open_folder_handler(request: web.Request) -> web.StreamResponse:
    data = await _body(request)
    root = _root_param(data.get("root"))
    return _json(await asyncio.to_thread(open_export_folder, root))


def _rev(data: dict) -> int:
    rev = data.get("rev")
    if isinstance(rev, bool) or not isinstance(rev, int) or rev < 0:
        raise _Reject(400, "rev 는 0 이상의 정수여야 합니다(지금 레이아웃 rev)")
    return rev


@_guard
async def layout_handler(request: web.Request) -> web.StreamResponse:
    root = _root_param(request.query.get("root"))
    raw = request.query.get("since")
    if raw not in (None, "") and not re.fullmatch(r"\d{1,12}", raw):
        raise _Reject(400, "since 는 0 이상의 정수여야 합니다")
    return _json(await asyncio.to_thread(layout_state, root, int(raw) if raw else None))


@_guard
async def layout_refs_handler(request: web.Request) -> web.StreamResponse:
    root = _root_param(request.query.get("root"))
    return _json(await asyncio.to_thread(layout_refs, root, request.query.get("name"), request.query.get("id")))


@_guard
async def layout_start_handler(request: web.Request) -> web.StreamResponse:
    data = await _body(request)
    root = _root_param(data.get("root"))
    return _json(await asyncio.to_thread(layout_start, root))


@_guard
async def layout_crop_handler(request: web.Request) -> web.StreamResponse:
    data = await _body(request)
    root = _root_param(data.get("root"))
    return _json(await asyncio.to_thread(layout_crop, root, _rev(data), data.get("op"), data))


@_guard
async def layout_psd_handler(request: web.Request) -> web.StreamResponse:
    data = await _body(request)
    root = _root_param(data.get("root"))
    names = data.get("names")
    if names is not None and (not isinstance(names, list) or not all(isinstance(n, str) for n in names)):
        raise _Reject(400, "names 는 shape 이름(문자열) 목록이어야 합니다")
    seen = data.get("snapshot")
    if seen is not None and (not isinstance(seen, dict) or not isinstance(seen.get("token"), str)):
        raise _Reject(400, "snapshot 은 GET /layout 의 psd_diff 에서 받은 {token} 이어야 합니다")
    return _json(await asyncio.to_thread(layout_psd, root, _rev(data), data.get("op"), names, seen and seen["token"]))


def _refs_rev(data: dict) -> int | None:
    """레퍼런스 쓰기의 rev = GET /refs 의 rev(0 이상 정수) 또는 null(refs.json 이 아직 없음 — 이 쓰기가 만듦). 키는 있어야 한다."""
    if "rev" not in data:
        raise _Reject(400, "rev 가 없습니다(GET /refs 의 rev, refs.json 이 없으면 null)")
    rev = data["rev"]
    if rev is not None and (isinstance(rev, bool) or not isinstance(rev, int) or rev < 0):
        raise _Reject(400, "rev 는 0 이상의 정수 또는 null 이어야 합니다(지금 레퍼런스 rev)")
    return rev


def _image_response(data: bytes, ctype: str, sig: str, v) -> web.Response:
    cache = "private, max-age=604800, immutable" if v == sig else "no-cache"
    return web.Response(body=data, content_type=ctype,
                        headers={"Cache-Control": cache, "ETag": f'"{sig}"', "X-Content-Type-Options": "nosniff"})


@_guard
async def refs_handler(request: web.Request) -> web.StreamResponse:
    root = _root_param(request.query.get("root"))
    raw = request.query.get("since")
    if raw not in (None, "") and not re.fullmatch(r"\d{1,12}", raw):
        raise _Reject(400, "since 는 0 이상의 정수여야 합니다")
    return _json(await asyncio.to_thread(refs_state, root, int(raw) if raw else None))


@_guard
async def refs_sheets_handler(request: web.Request) -> web.StreamResponse:
    root = _root_param(request.query.get("root"))
    return _json(await asyncio.to_thread(ref_sheets, root))


@_guard
async def refs_sheet_handler(request: web.Request) -> web.StreamResponse:
    q = request.query
    root = _root_param(q.get("root"))
    data, sig = await asyncio.to_thread(sheet_image, root, q.get("sheet"), q.get("kind") or "preview", q.get("rect"), q.get("max"))
    return _image_response(data, "image/jpeg", sig, q.get("v"))


@_guard
async def refs_img_handler(request: web.Request) -> web.StreamResponse:
    q = request.query
    root = _root_param(q.get("root"))
    data, ctype, sig = await asyncio.to_thread(ref_image, root, q.get("name"), q.get("where") or "refs",
                                               q.get("size") or str(_REF_THUMB), q.get("bg") or "gray")
    return _image_response(data, ctype, sig, q.get("v"))


@_guard
async def refs_cut_handler(request: web.Request) -> web.StreamResponse:
    data = await _body(request)
    root = _root_param(data.get("root"))
    return _json(await asyncio.to_thread(ref_cut, root, data))


@_guard
async def refs_cut_state_handler(request: web.Request) -> web.StreamResponse:
    root = _root_param(request.query.get("root"))
    return _json(await asyncio.to_thread(ref_cut_state, root, request.query.get("req")))


@_guard
async def refs_save_handler(request: web.Request) -> web.StreamResponse:
    data = await _body(request)
    root = _root_param(data.get("root"))
    return _json(await asyncio.to_thread(ref_save, root, _refs_rev(data), data))


@_guard
async def refs_link_handler(request: web.Request) -> web.StreamResponse:
    data = await _body(request)
    root = _root_param(data.get("root"))
    return _json(await asyncio.to_thread(ref_link, root, _refs_rev(data), data.get("op"), data))


@_guard
async def pipeline_handler(request: web.Request) -> web.StreamResponse:
    root = _root_param(request.query.get("root"))
    return _json(await asyncio.to_thread(pl.view, root))


@_guard
async def pipeline_settings_handler(request: web.Request) -> web.StreamResponse:
    data = await _body(request)
    root = _root_param(data.get("root"))
    if "rev" not in data:
        raise _Reject(400, "rev 가 없습니다(GET /pipeline 의 rev — 파일이 없으면 0, 깨진 파일 덮어쓰기는 null)")
    return _json(await asyncio.to_thread(pl.update, root, data["rev"], data.get("section"), data.get("values")))


@_guard
async def pipeline_prompt_handler(request: web.Request) -> web.StreamResponse:
    q = request.query
    root = _root_param(q.get("root"))
    return _json(await asyncio.to_thread(pipeline_prompt, root, q.get("stage"), q.get("approve"), q.get("only"), q.get("expect")))


@_guard
async def pipeline_plan_handler(request: web.Request) -> web.StreamResponse:
    q = request.query
    root = _root_param(q.get("root"))
    style = q.get("style") or "0"
    if style not in ("0", "1"):
        raise _Reject(400, "style 은 0 또는 1 이어야 합니다")
    ids = [t.strip() for t in (q.get("only") or "").split(",") if t.strip()]
    res = await asyncio.to_thread(_plan_route, root, ids, style == "1")
    return _json({"busy": True}, status=202) if res is None else _json(res)


@_guard
async def pipeline_progress_handler(request: web.Request) -> web.StreamResponse:
    root = _root_param(request.query.get("root"))
    return _json(await asyncio.to_thread(pipeline_progress, root))


@_guard
async def draft_handler(request: web.Request) -> web.StreamResponse:
    root = _root_param(request.query.get("root"))
    return _json(await asyncio.to_thread(draft_get, root, request.query.get("crop")))


@_guard
async def draft_adopt_handler(request: web.Request) -> web.StreamResponse:
    data = await _body(request)
    root = _root_param(data.get("root"))
    return _json(await asyncio.to_thread(draft_adopt, root, data))


@_guard
async def mask_handler(request: web.Request) -> web.StreamResponse:
    root = _root_param(request.query.get("root"))
    return _json(await asyncio.to_thread(mask_view, root, request.query.get("key")))


@_guard
async def mask_img_handler(request: web.Request) -> web.StreamResponse:
    q = request.query
    root = _root_param(q.get("root"))
    data, sig = await asyncio.to_thread(mask_image, root, q.get("key"), q.get("kind"), q.get("v"))
    return _image_response(data, "image/png", sig, q.get("v"))


@_guard
async def mask_save_handler(request: web.Request) -> web.StreamResponse:
    data = await _png_body(request)
    q = request.query
    root = _root_param(q.get("root"))
    return _json(await asyncio.to_thread(mask_save, root, q.get("key"), data, q.get("seen"), q.get("ops"), q.get("rect")))


@_guard
async def mask_edit_handler(request: web.Request) -> web.StreamResponse:
    data = await _body(request)
    root = _root_param(data.get("root"))
    return _json(await asyncio.to_thread(mask_edit, root, data))


@_guard
async def mask_unused_handler(request: web.Request) -> web.StreamResponse:
    root = _root_param(request.query.get("root"))
    return _json(await asyncio.to_thread(mask_unused, root))


@_guard
async def mask_cleanup_handler(request: web.Request) -> web.StreamResponse:
    data = await _body(request)
    root = _root_param(data.get("root"))
    return _json(await asyncio.to_thread(mask_cleanup, root, data))


@_guard
async def mask_alts_handler(request: web.Request) -> web.StreamResponse:
    root = _root_param(request.query.get("root"))
    res = await asyncio.to_thread(mask_alts, root, request.query.get("key"))
    return _json({"busy": True}, status=202) if res is None else _json(res)


@_guard
async def mask_adjust_handler(request: web.Request) -> web.StreamResponse:
    data = await _body(request)
    root = _root_param(data.get("root"))
    return _json(await asyncio.to_thread(mask_adjust, root, data))


@_guard
async def zorder_handler(request: web.Request) -> web.StreamResponse:
    data = await _body(request)
    root = _root_param(data.get("root"))
    return _json(await asyncio.to_thread(zorder_write, root, data))


@_guard
async def mask_ps_list_handler(request: web.Request) -> web.StreamResponse:
    root = _root_param(request.query.get("root"))
    return _json(await asyncio.to_thread(mask_ps_list, root))


@_guard
async def mask_ps_import_handler(request: web.Request) -> web.StreamResponse:
    data = await _body(request)
    root = _root_param(data.get("root"))
    return _json(await asyncio.to_thread(mask_ps_import, root, data))


_ROUTES = (
    ("GET", f"{API}/board", board_redirect),
    ("GET", f"{API}/board/", board_static),
    ("GET", f"{API}/board/{{name}}", board_static),
    ("GET", f"{API}/state", state_handler),
    ("GET", f"{API}/thumb", thumb_handler),
    ("GET", f"{API}/canvas", canvas_handler),
    ("GET", f"{API}/seams", seams_handler),
    ("GET", f"{API}/file", file_handler),
    ("POST", f"{API}/pick", pick_handler),
    ("POST", f"{API}/reject", reject_handler),
    ("POST", f"{API}/reroll", reroll_handler),
    ("POST", f"{API}/drain", drain_handler),
    ("POST", f"{API}/open_folder", open_folder_handler),
    ("GET", f"{API}/layout", layout_handler),
    ("GET", f"{API}/layout/refs", layout_refs_handler),
    ("POST", f"{API}/layout/start", layout_start_handler),
    ("POST", f"{API}/layout/crop", layout_crop_handler),
    ("POST", f"{API}/layout/psd", layout_psd_handler),
    ("GET", f"{API}/refs", refs_handler),
    ("GET", f"{API}/refs/sheets", refs_sheets_handler),
    ("GET", f"{API}/refs/sheet", refs_sheet_handler),
    ("GET", f"{API}/refs/img", refs_img_handler),
    ("POST", f"{API}/refs/cut", refs_cut_handler),
    ("GET", f"{API}/refs/cut", refs_cut_state_handler),
    ("POST", f"{API}/refs/save", refs_save_handler),
    ("POST", f"{API}/refs/link", refs_link_handler),
    ("GET", f"{API}/pipeline", pipeline_handler),
    ("POST", f"{API}/pipeline/settings", pipeline_settings_handler),
    ("GET", f"{API}/pipeline/prompt", pipeline_prompt_handler),
    ("GET", f"{API}/pipeline/plan", pipeline_plan_handler),
    ("GET", f"{API}/pipeline/progress", pipeline_progress_handler),
    ("GET", f"{API}/draft", draft_handler),
    ("POST", f"{API}/draft/adopt", draft_adopt_handler),
    ("GET", f"{API}/mask", mask_handler),
    ("GET", f"{API}/mask/img", mask_img_handler),
    ("POST", f"{API}/mask/save", mask_save_handler),
    ("POST", f"{API}/mask/edit", mask_edit_handler),
    ("GET", f"{API}/mask/alts", mask_alts_handler),
    ("GET", f"{API}/mask/unused", mask_unused_handler),
    ("POST", f"{API}/mask/cleanup", mask_cleanup_handler),
    ("POST", f"{API}/zorder", zorder_handler),
    ("GET", f"{API}/mask/ps_list", mask_ps_list_handler),
    ("POST", f"{API}/mask/ps_import", mask_ps_import_handler),
    ("POST", f"{API}/mask/adjust", mask_adjust_handler),
)


def register_routes(routes: web.RouteTableDef, base_dir: str | None = None) -> bool:
    """보드 라우트를 routes(PromptServer.instance.routes)에 더한다. 이미 있으면 아무것도 더하지 않고 False.
    PromptServer.add_routes() 전에(노드 모듈 import 때) 불러야 /api 접두 사본도 생긴다.
    base_dir(ComfyUI input 디렉터리)를 주면 <base_dir>/bmk_design_patch/_roots.json 을 다시 읽는다(재시작 뒤 보드 링크 복구)."""
    if base_dir:
        n = store.load_root_registry(base_dir)
        logger.debug(f"{_TAG} Board: 루트 레지스트리 {n}개 복구 ({base_dir})")
    if any(isinstance(r, web.RouteDef) and r.path == f"{API}/state" for r in routes):
        return False
    for method, path, handler in _ROUTES:
        routes.route(method, path)(handler)
    return True
