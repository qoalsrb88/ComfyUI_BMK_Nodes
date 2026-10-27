"""BMK Design Patch — PSD 크롭 영역에 GPT 편집 후보를 준비·실행·정합·톤 보정·마스크 합성해 풀해상도 Smart Object PSD 로 되돌리는 노드 묶음(M1·M2·M3).

배경
----
캐릭터 일러스트의 틀린 디자인(파츠)을 고칠 때 Photoshop 에서 하던 일은 이렇다.
  크롭 Rectangle 그리기 → 크롭 PNG 내보내기 → 크롭마다 GPT 프롬프트 작성·반복 실행 → 결과를 rect 에
  배치·축소 → 어긋난 결과 재변형 → 톤 보정(_2tone) → 마스크 → 그룹 정리.
Design Patch 는 그 사이의 반복 계산을 노드로 옮긴다. PSD 를 psd_tools 로 직접 읽고(PS 스크립트 불필요),
프로젝트 폴더의 매니페스트 JSON 하나를 유일한 원본(원장)으로 삼는다. 모든 노드는
"매니페스트 읽기 → 일 → 바뀐 것이 있을 때만 원자 저장 → 같은 핸들(rev 갱신) 출력" 의 멱등 노드다.
M1 은 유료 GPT 호출 없이 기존 결과(PSD 수확·폴더)로 전체 흐름을 재현한다. M2 는 Run(유료 호출)·Review(보드)를 더한다.
M3 는 Spec Draft(VLM 이 specs/<crop_id>.json 초안을 씀)를 더한다.
명세: H:\\BmkNodeDesign\\MultiLayerCropEdit\\_proto\\M1_SPEC.md · M2_SPEC.md · M3_SPEC.md, 근거: _proto\\reports\\final.md.

모듈 구성 (규약 §1 의 노드 없는 보조 모듈 bmk_design_patch_<역할>.py)
- bmk_design_patch_store     매니페스트·원자 저장·트랜잭션(3-way 병합)·루트 레지스트리·가격표·ID·레이어명 파싱·
                             용어집·레퍼런스 매칭·출력 크기·cell_key
- bmk_design_patch_psd       PSD 읽기(rect·베이스·크롭 소스·수확) / PSDWriter(픽셀·그룹·마스크·SO)
- bmk_design_patch_analysis  배치·가드 정합·톤 보정장·ΔE 자동 마스크·게이트·z순서
- bmk_design_patch_prompt    결정적 프롬프트 렌더러·변형·평탄화·매니페스트 → 스펙
- bmk_design_patch_runner    실행 계획·승인 해시·호출 원장·API 어댑터(comfy.org / mock)·wave·동시성·인터럽트·drain
- bmk_design_patch_board     Review Board aiohttp 라우트(web_design_patch/ 페이지)·보드 썸네일
- bmk_design_patch_vlm       Spec Draft 의 VLM 초안기: 문맥·키·프롬프트·스키마·원장(vlm/)·계획/승인·백엔드(openai /
                             local_qwen / mock)·JSON 추출·정규화·3-way 스펙 병합·렌더 점검·보고서
- bmk_design_patch_refs      Studio S2 레퍼런스 연결(refs.json)·이름 규칙·시트 캐시·컷 요청/결과·변환·파일 동작
                             (SAM3 자체는 bmk_sam3_mask_layers.segment — 불러온 모델 한 벌을 그 노드와 같이 씀)
- bmk_design_patch_mask      Studio S4 마스크 편집: 매니페스트 mask_edits(★ 후보의 최종 마스크 래스터)·기준 지문·PNG 검사·
                             저장/되돌리기(보드 라우트)·_plan 이 쓰는 적용 규칙
이 파일은 노드와 노드 경계 변환(텐서 ↔ numpy, MASK 의미 반전), 보드 라우트 등록만 담는다.

동시 쓰기 (M2)
- 보드 라우트(aiohttp 스레드)와 비동기 Run 이 노드 실행 중에도 매니페스트를 고친다. 노드 저장(_commit)은
  store.commit_merge(연 시점 스냅샷 ↔ 노드 결과 ↔ 디스크 최신본 3-way)로 하므로 그 사이의 pick·reject·재굴림·호출 결과를
  덮지 않는다. 같은 항목을 양쪽이 다르게 바꾸면 노드 값으로 저장하고 보고서 끝에 "병합 충돌" 경고를 붙인다.
- 잠금은 프로세스 안에서만 유효하다(ComfyUI 하나가 프로젝트 폴더를 쓴다고 가정).

노드 (CATEGORY BMK/Image, 공용 핸들 BMK_DP_PROJECT = {"schema","root","project","rev"} + 가지 설정)
---------------------------------------------------------------------------------------------------
- 핸들의 가지 설정: Analyze 는 "analyze"(분석 설정), Compose 는 "compose"(정합 사용·z순서 등)를 핸들에 실어 보낸다.
  하류 노드는 매니페스트가 아니라 **자기 상류** 의 설정을 쓰므로, 한 프로젝트에 Analyze/Compose 가지가 여럿이어도
  서로의 결과를 덮어쓰지 않는다(명세 §5 의 핸들 4키에 더한 확장). Spec Draft 는 "draft"(초안 대기 요약)를 실어
  Prepare 를 지나 Run 까지 보낸다(Run 의 초안 대기 경고).
- Project      : <base_dir>/bmk_design_patch/<project>/ 를 열거나 만든다. base_dir "" = ComfyUI input 폴더.
                 IS_CHANGED = 매니페스트·layout.json·refs.json·specs/·glossary.json 과 specs 가 가리키는 파일(guide·before_paste·refs) 지문
                 + Spec Draft 원장(vlm/<crop_id>/<key>/a*.json 이름·크기, 재폴링 표시) + mock 스위치
                 → 다른 노드가 원장을 바꾸면 다음 큐에서 하류 전체가 다시 확인된다(각 노드는 디스크 캐시로 즉시 끝남).
                 매니페스트의 project 이름은 폴더 이름에 맞춘다(복사한 폴더).
- Import PSD   : 크롭 영역 그룹의 shape rect, 베이스(I2I_base + 클린플레이트), 크롭 이미지 그룹 합성을 읽어
                 crops[] 를 만든다. 레이어명 "NNN_부위 | A: …; B: …" 에서 타깃 골격, refs_dir 에서 NNN_ 레퍼런스.
                 PSD 지문·그룹 설정이 같으면 다시 읽지 않는다. overview = 번호 박스 오버뷰.
                 타깃 = 레이어명 + specs/<crop_id>.json 의 _layer_targets(Spec Draft 채택, store.merge_layer_targets —
                 PSD 문구가 있는 tid 는 PSD 우선, 채택 타깃에 origin "adopt"). 캐시 경로에서도 _layer_targets·채택 타깃이 있는
                 크롭은 매 실행 다시 계산한다(스펙을 고치면 다음 큐에 반영). crop.layer_name 은 PSD 원본 그대로.
                 레이아웃 모드(S1, 프로젝트 폴더에 layout.json — Studio '레이아웃 편집 시작'이 만든다): 크롭 rect·이름·대상·
                 ID 의 원천은 layout.json(layer_name = 합성 레이어명, 대상 문구가 PSD 문구 자리), PSD 는 베이스·work_rect·사전
                 편집 그룹(psd_name 으로 찾음)만. 소스는 바뀐 크롭만 다시 만들고, 사전 편집 자식이 아니면 PSD 를 열지 않는다.
                 PSD 를 열 때마다 derived/psd_crops.json 스냅숏을 쓰고 PSD 사각형이 기준점과 달라지면 경고만 한다(크롭 영역
                 그룹을 못 읽으면 경고 한 줄). 기준점이 이 PSD 가 아닌데 믿을 스냅숏이 없으면 바뀐 크롭이 없어도 한 번 연다.
                 PSD 모드 Import 는 스냅숏을 지운다. 깨진 layout.json·캔버스 크기 다름은 오류. 빈 시작(D6): layout.json 없음 +
                 crop_group=auto 인데 그룹이 없거나 shape 0 이면, 지울 크롭이 없거나(첫 Import) layout.json 을 치운 매니페스트일
                 때만 크롭 0(베이스만) + 안내 — PSD 모드로 크롭이 있던 프로젝트는 오류(매니페스트 그대로).
                 Candidate In·Prepare 는 크롭 0 이면 예외 대신 경고(Review 까지 돌게).
                 레퍼런스 연결(S2, 프로젝트 폴더에 refs.json — Studio 레퍼런스 편집기가 만든다): 크롭마다 refs.resolve_refs —
                 고정된 크롭(크롭 ID 로 연결)은 refs_auto = 연결 파일 중 refs_dir 에 있는 것, refs_main = 대표(Prepare 가 고름),
                 고정 안 된 크롭·refs.json 없음·refs_dir 다름(경고)은 이름 매칭 그대로. 깨진 refs.json 은 오류(refs_history 안내).
- Candidate In : 후보 등록. psd_harvest = 최종 PSD 의 결과 그룹 SO(원본 bytes·quad·손 마스크·보임 여부),
                 folder = 폴더 이미지(파일명 앞부분 = 크롭 이름, quad = crop rect). 원본 bytes 는 cands/ 에
                 디코드 없이 저장(내용 주소). mapping_json 으로 크롭/타깃 지정·건너뛰기.
- Spec Draft (M3): 크롭마다 VLM(openai 유료 / local_qwen 무료·ComfyUI 모델 언로드 / mock)으로 specs/<crop_id>.json 초안을
                 쓴다(크롭 캔버스 + NNN_ 레퍼런스 + 한국어 라벨 + 용어집 → JSON → 정규화 → 필드 단위 3-way 병합 → 렌더 점검).
                 화풍(style_note)은 프로젝트당 1회 → specs/_project.json. 승인은 Run 과 같은 1회용 해시(모든 백엔드),
                 원장은 vlm/<crop_id>/<key>/. 사람이 고친 값은 덮지 않고(fill 기본), 같은 입력이면 재큐 0 호출.
                 apply = targets(기본, 타깃 영문 이름·bbox 만 — 나머지 노드 값은 양보, 화풍 요청 없음) | full(전부, 참고용).
                 adopt = 채택할 크롭(1회용, JS 가 큐 직후 비움): 지난 실행까지 스펙에 저장된 제안 레이어명의 새 타깃을
                 스펙 _layer_targets 에 씀 → 다음 큐의 Import PSD 가 타깃으로 더함(그 큐의 Prepare 는 채택 가드).
                 매니페스트는 쓰지 않는다(rev 그대로). 출력 노드(버튼 = 이 노드만 부분 실행).
                 IS_CHANGED = approve 가 있으면 NaN(승인 큐는 매번 실행), 없으면 상수 — 원장에만 남은 무료 복구(재폴링·
                 도착분 병합)는 Project 지문이 잡는다.
- Prepare      : 무료. 크롭마다 스펙(specs/<crop_id>.json 오버라이드 + 용어집) → 평탄화한 입력 이미지 →
                 변형별 결정적 프롬프트 → GPT 출력 크기 → cell_key 로 jobs[] 를 만든다(호출은 M2 Run).
                 채택 가드: 레이어명 + _layer_targets 로 Import 가 만들 타깃이 매니페스트와 다르면(같은 큐에서 Draft 가
                 Import 뒤에 채택을 씀) 그 크롭은 jobs 를 만들지 않는다 → Run 해시가 바뀌어 드라이런.
                 jobs 와 같은 커밋에 쓴 위젯 값(prepare_settings)·입력 지문(prepare_inputs)을 남긴다(Studio S3). 가드가
                 'Import 가 반영할 차이'로 건너뛴 크롭이 있으면 지문은 그대로 둔다(문법 오류는 제외).
- Analyze      : 후보마다 배치(LANCZOS) → 가드 정합(손 마스크가 있으면 정합 제외 영역) → ΔE 자동 마스크 →
                 저주파 톤 보정장 → 게이트. 결과는 analysis[key][params_hash] (같은 설정이면 건너뜀, 후보마다 최근
                 설정 4개까지 보관하고 밀려난 항목의 derived 파일은 지움). 보드가 고르는 분석 설정도 이 설정으로 바꾼다
                 (Review 와 같게 — Review 없는 Studio Export 템플릿에서도 보드 ★ 합성·편집 stale 이 Export 와 같은 항목).
- Compose      : pick 을 z순으로 베이스 위에 합성한 평탄화 미리보기 + 마스크 합집합. 설정(정합 사용, z순서)은
                 핸들 compose 로 하류 Export 에 넘어가 같은 배치로 쓰인다(미리보기 = PSD). 상류 Analyze 설정의 분석을 쓴다.
- Export PSD   : 00.Base / 02.크롭영역(외곽선, 숨김) / 03.수정 > 크롭 그룹 > 타깃 그룹(그룹 마스크 = 최종 마스크)
                 > 후보 SO(pick 보임·녹색, 대안 숨김·빨강) 구조의 PSD 를 output/design_patch/<subfolder>/ 에 쓴다(subfolder =
                 optional 맨 뒤 위젯 — 폴더 이름 하나(store.check_export_subfolder), 비우거나 project 폴더 이름이면 project 폴더 이름 —
                 그때는 export 해시·기록 options 에 subfolder 를 넣지 않아 그 전과 같음).
                 정합은 SO quad 에 affine 으로(비파괴), color=toned 면 톤 보정한 풀해상도 PNG 를 임베드.
                 이미 있는 파일은 덮어쓰지 않는다(<prefix>_r<rev>_2 …). 병합 프리뷰는 레이어와 같은 순서로 직접 합성.
                 정합·z순서는 상류 Compose 설정(핸들), 없으면 기본값. ui bmk_dp_export = 결과 하나
                 {outcome: new(새 파일) | unchanged(같은 내용의 기존 파일) | empty(넣을 후보 없음 — 쓰지 않음), psd, rev}.
                 new·unchanged 는 그 PSD 의 export 해시를 board_pref.json 에 남긴다(store.save_board_export — unchanged 는 exports[] 에
                 아무것도 더하지 않으므로 보드 ★ 합성·마스크 편집 창과 Studio 처음 값이 store.last_export 로 최신 PSD 의 설정을 따름).
                 ★ 의 마스크 편집(S4)은 그룹 마스크로 들어가고 exports[] 기록 mask_groups·mask_edits(그 가운데 편집 수)·보고서
                 '그룹 마스크 n (편집 k)'·사이드카 export_layers[].mask_src "edit" + mask_edit 로 남는다(실제로 적용한 편집이 있을 때만
                 해시에 들어감 — 파일이 없어 기준으로 떨어진 기록·★ 아닌 후보의 기록은 빠짐).
- Run (M2)     : jobs 를 comfy.org GPT Image(/proxy/openai/images/edits)로 실행(async, 출력 노드). 과금 대상 =
                 보드 재굴림(사전 승인) + approve == 현재 pending_hash 일 때 나머지. 승인이 없으면 과금 없이 드라이런
                 보고와 ui bmk_dp_run(해시·개수·예상 USD)만 낸다. 원장(cands/<cell_key>/)에 먼저 쓰고 결과는 도착하는 대로
                 저장. wave(시간 상한)로 멈추면 bmk.dp.wave 이벤트 → JS 가 새 토큰으로 이어서 큐. 보드 Drain = 새 발사 중지.
                 mock(BMK_DP_MOCK_API=1 또는 <base_dir>/bmk_design_patch/_MOCK_API)이면 과금 없는 가짜 결과.
                 핸들의 draft 에 초안 대기가 있으면 보고서·ui(draft_pending)로 경고한다(초안 없는 프롬프트로 과금).
- Review (M2)  : 프로젝트를 보드 레지스트리에 등록하고 보드 썸네일을 미리 만든다(출력 노드). ui bmk_dp_board 로
                 JS 의 Open Board 가 별도 창 Review Board(키보드 ★·slice·탈락·재굴림)를 연다.
- Ref Cut (S2) : Studio 레퍼런스 편집기가 ComfyUI /api/prompt 로 이 노드 하나만 큐에 넣는다(그래프에 둘 일 없음, 출력 없음, 무료).
                 입력 = 보드 레지스트리 키 + 컷 요청 ID. 요청 파일(derived/refcut/<req>.req.json)을 다시 검증 → 시트 창(선택 +
                 변마다 12%) → 상자 + 자동 양점(늘 맨 앞, 사람 점은 뒤에) → bmk_sam3_mask_layers.segment(멀티마스크 늘 켬)
                 → refs.finish_mask → <req>_mask.png·<req>.res.json. res.json 의 auto_point = 맨 앞에 넣은 자동 양점(시트 좌표 =
                 창 좌표 + 창 원점), null = 넣지 않음(선택 안이 모두 투명 · 텍스트 감지 경로 — 점을 안 씀, 경고 한 줄). 보드 다듬기(base)
                 결과는 base 의 auto_point 를 이어 받는다. 예외·취소면 res.json {ok: false, error} 를 쓰고 다시 올린다.
                 실행 스레드에서 돌아 SAM3 가 ComfyUI 모델 관리·VRAM 을 다른 노드와 순서대로 쓴다(STUDIO_S2_SPEC D2).
- Analyze / Compose / Export 는 보드의 탈락(rejects) 후보를 분석·합성·대안에서 뺀다. Compose / Export 는 크롭 타깃에
  없는 tid 의 후보(레이어명·채택에서 지운 타깃)도 경고하고 뺀다(보드와 같게).

옵션 메모
---------
- 정합 행렬은 cand → base 정방향(크롭 로컬, 픽셀 중심). SO quad = analysis.quad_from_matrix(M, rect).
- 손 마스크(hand) = 수확한 PSD 의 레이어/부모 그룹 마스크. auto = Analyze 의 ΔE 자동 마스크(grow·feather 굽기).
  hand_else_auto = 손 마스크가 있으면 손, 없으면 auto. hand 인데 손 마스크가 없으면 마스크 없음(SO 전체 보임).
- 마스크 편집(S4, 매니페스트 mask_edits — Studio 마스크 모드가 씀)이 있는 ★ 는 mask_source 와 상관없이 그 래스터가 최종 마스크
  (보고서·사이드카 msrc "edit"). z 순서 넓이는 편집 전(기준) 마스크로, 분석은 편집을 모른다(bmk_design_patch_mask).
  편집은 기록한 창 그대로 쓰므로 이번 Compose·Export 의 패치 창(정합 사용·재분석)이나 분석 항목이 편집할 때와 다르면 보고서에 경고한다.
- 크롭과 맞지 않는 부분 패치 SO(quad 와 crop rect IoU < 0.5, 예: 생성형 채우기, 손으로 옮긴 조각)는 분석하지
  않고 수확한 quad·마스크 그대로 쓴다.
- Run·폴더 후보는 만들 때의 크롭 기준으로 분석한다(_gen_frame): PS 에서 크롭 rect 를 옮긴 뒤에도 자기 quad +
  그때의 소스(Run = 원장 req 의 canvas 입력에 Prepare 가 남긴 평탄화 전 크롭 소스 — 예전 원장은 평탄화 canvas +
  유효 영역, 폴더 = 등록 때의 크롭 소스)로 자동 마스크를 만들고, 항목의 rect 가 마스크 좌표 기준이 된다. 그 소스가 없으면
  no_source 항목(마스크 없음)을 남기고 Analyze 보고서·보드가 경고한다. 같은 설정의 옛 기준 항목으로 돌아오면(소스가
  돌아옴 등) 그 항목의 seq 를 올려 보드(_entry_for)도 Analyze 가 지금 쓰는 항목을 본다.
- PS 에서 원근/왜곡(Distort) 변형한 SO 의 quad 는 Compose/Export 에서 가장 가까운 평행사변형으로 근사한다(경고).
- Compose 의 mask 출력은 ComfyUI MASK 규약(LoadImage 알파 마스크와 같음): 1 = 패치 없음(투명), 0 = 패치 적용.
  JoinImageWithAlpha 에 넣으면 패치만 보이는 RGBA 가 된다. 패치 영역 = 1 이 필요하면 InvertMask.

매니페스트 추가 키(명세 §1 스키마 밖, "추가 키 허용")
- source.refs_dir / refs_fingerprint / glossary / glossary_fingerprint, crops[].layer_name / rect_source
- 레이아웃 모드(S1): source.layout = {rev, sig}(Import 가 이번에 읽은 layout.json 판 — 보드의 '미반영' 표시),
  crops[].psd_name(사전 편집 그룹에서 찾은 이름), rect_source "layout"
- 레퍼런스 연결(S2): source.refs_links(refs.refs_sig — refs.json 이 있을 때만 키), crops[].refs_main(고정된 크롭의 대표만 키)
- crops[].targets[].origin ("adopt" = specs 의 _layer_targets 에서 온 타깃, 그 밖은 키 없음)
- candidates[].hand_mask.bg / origin_info.crop_source(폴더), jobs[].inputs[].source(canvas), 분석 항목의 params /
  skipped / no_source / rect / seq / quad_reg / mask_area / thumb / tone.full
- exports[].hash / options / compose / bytes / smart_objects / problems
- Studio S3: prepare_settings(Prepare 가 실제로 쓴 위젯 값 7개 {model, quality, size_rule, variants(받은 문자열 그대로), n, ref_style,
  calls_per_cell} — n·calls_per_cell 은 int) / prepare_inputs(store.prepare_inputs_fp — Prepare 입력 지문). 둘 다 Prepare 가 jobs 와 같은
  커밋에 쓴다(Studio 처음 값·그래프와의 설정 갈라짐·'반영 필요' 판정). 키 없음 = 그 전의 프로젝트(store 골격에 넣지 않음).
  단 채택 가드가 'Import 가 반영할 차이'로 건너뛴 크롭이 있으면 prepare_inputs 는 쓰지 않는다(채택이 반영되지 않았으므로 '반영 필요' 유지)
명세와 다른 모양: analysis 는 {key: {params_hash: 항목}}(명세는 {key: 항목}) — Analyze 가지가 여럿이어도 서로
밀어내지 않게. 핸들은 4키 + analyze / compose / draft(위).

버전 이력
---------
v1 (2026-10, M1)
- 노드 7개(Project, Import PSD, Candidate In, Prepare, Analyze, Compose, Export PSD). 유료 API 호출 없음.
- 회귀(tools/test_bmk_design_patch.py): 03 PSD 가져오기 → 05 PSD 수확 → 분석 → 합성(05 렌더 대비 MAE)
  → SO PSD 출력 재열기 검사.
- 리뷰 수정: 같은 이름 크롭은 위치/ID 로 짝지음, 가지 설정(analyze/compose)을 핸들로, 분석 해시에 분석 대상 여부·
  손 마스크 위치, export 덮어쓰기 금지·폴더 이름 출력, 병합 프리뷰 직접 합성(RLE), 원근 quad 근사, 링크 입력 IS_CHANGED.
v2 (2026-10, M2)
- Run(유료 호출·승인 게이트·원장·wave·drain·mock), Review(보드 등록·썸네일) 노드. 보드 라우트는 모듈 import 때 한 번 등록.
- 모든 노드 저장을 store.commit_merge(3-way)로. Prepare 에 calls_per_cell(→ jobs[].reps, 위젯 맨 뒤)과 Run 과 같은
  승인 해시·예상 비용 보고. Analyze/Compose/Export 는 rejects 제외.
v3 (2026-10, M3)
- Spec Draft 노드(매핑은 Candidate In 다음·Prepare 앞, 출력 노드, async, Run 과 같은 hidden). 로직은 bmk_design_patch_vlm,
  이 파일은 §3.3 순서(문맥 → 계획·승인 → 실행 → 화풍·크롭 병합·쓰기 → 보고서·review.md·시트)와 시트 그리기만.
- 핸들 가지 키 draft({pending, pending_hash, refresh_available, pending_style[, unwritten][, held, held_style][, mock_specs]})
  → Run 보고서·ui 의 초안 대기 경고(draft_pending, draft_style, draft_unwritten, draft_held, draft_held_style, draft_mock).
- 리뷰 수정(M3): Draft IS_CHANGED(approve 가 있으면 NaN — 로그인·VRAM 복구 뒤 같은 해시 재승인이 캐시에 걸리지 않게),
  Project 지문에 VLM 원장·mock 스위치(Cancel·폴링 실패·401 뒤 재큐 = 무료 재폴링·병합, _MOCK_API 를 끄면 'mock 초안 교체'),
  created 가 남으면 vlm/repoll_pending.json 을 다시 써 다음 큐도 재폴링, keep_loaded 를 끄면 vlm 이 무엇이든 남은 Qwen 을
  내림(켜져 있고 Qwen 이 올라 있으면 상주 경고), 가지 키 draft.pending_style → Run 경고가 "N크롭 + 화풍",
  mock 초안의 _draft.model = "mock"(키는 그대로). credit_cap 툴팁을 구현 규칙(실측·미보고 + 이 단위 최대 > cap → 그 실행의
  새 발사 중지, 진행 중 예약까지 더해 넘치면 대기 후 재판정)으로, write_mode 툴팁에 force 1회(키마다)를 적음. 명세 M3_SPEC §11.
- 커밋 전 리뷰(10/13): Draft VALIDATE_INPUTS(vlm, effort)(조합·목록 오류는 큐 단계에서), Draft·Run 의 run 은 첫 줄부터 await +
  **_stale(옛 워크플로의 없는 입력) — 그 뒤 예외는 ComfyUI 가 hidden 토큰을 입력값으로 기록하지 않는 경로로 보고. 스펙 쓰기
  실패 → 보고서 [실패] + vlm/write_pending.json(지문이 바뀌어 다음 큐가 다시 씀, 못 한 크롭은 이어 둠), 가지 키
  draft.unwritten → Run 보고서·확인 창 경고(draft_unwritten). 시트는 1px 미만 bbox 도 그림.
- 커밋 뒤 후속(리뷰 B 묶음 일부): 가지 키 draft.held·held_style(only 로 보류된 초안 대기 — force 는 only 밖에서 fill 이면
  대기였을 것)·mock_specs(vlm=mock 으로 쓴 초안이 남은 스펙) → Run 보고서·확인 창 경고(mock 은 유료 Run 일 때만).
  Spec Draft·Run 실행이 프롬프트 종료로 취소되면(같은 큐의 다른 노드 실패) orphan 을 경고 로그로 남김.
  Import PSD 개요도 축소로 1px 보다 얇아진 사각형을 그림.
- 타깃만 채택(10/13, TARGETS_ONLY_SPEC §9): Import PSD 가 레이어명과 스펙 _layer_targets 를 먼저 합친 뒤 한 번만
  _new_targets(old = 이번 실행 전 크롭, origin 이음) — 캐시 경로는 채택이 걸린 크롭만 다시 계산, 스펙 오류는 Import 를 막지
  않음(캐시 = 지금 타깃 유지, 다시 가져오기 = 레이어명만), 채택 경고·빠진 채택 타깃의 후보·의미가 바뀐 타깃의 후보는 보고서
  줄로만. Prepare 채택 가드(채택·채택 해제 어느 쪽이든), Compose/Export 는 크롭·tid 가 없는 후보를 분석·빈 PSD 판단 전에
  뺌(_plan 과 같은 _in_crop).
  Spec Draft 위젯 apply(targets 기본 | full)·adopt(keep_loaded 뒤, run 에도 기본값 — 첫 await 전 바인딩): targets 는 화풍 문맥·
  요청 없음(_project.json 안 씀, 사람이 쓴 style_note 만 전파), plan·merge_spec·mock_drafted·review.md 에 apply, 채택은
  adopt_select 로 고른 크롭만 merge_spec(adopt), 보고서 [타깃만]·[제안]·adopt 토큰 경고. 시트는 초안 없는 실행에서도 채택 타깃을
  제안 bbox 로 그리고, targets 면 고른 레퍼런스 테두리 없음.
- 크롭 rect 이동(10/15, 261014_Test 009 실측): 옮기기 전 Run 후보가 부분 패치로 분석 생략돼 Export 가 GPT 출력 전체를
  사각형으로 합성하던 문제 — Run·폴더 후보는 만들 때의 크롭(_gen_frame) 기준 분석, 항목 rect, 소스 없음 경고.
  jobs[].inputs 의 canvas 에 source(평탄화 전 크롭 소스, 원장 req 에도), 폴더 후보 origin_info.crop_source.
  제자리 후보의 분석 해시는 그대로(다시 계산하지 않음).
v4 (2026-10, Studio S1 — STUDIO_S1_SPEC §2·D6)
- Import PSD 레이아웃 모드(layout.json 이 크롭 원천, 바뀐 크롭만 소스 계산, PSD 안 여는 경로, 스냅숏·갈라짐 경고, 보고서
  '레이아웃 rev N · 크롭 n · 바뀜 k(소스 다시 만듦 s, PSD 엶|안 엶)'). layout.json 이 없으면 지금 그대로(만들지 않음).
  이관(Studio '레이아웃 편집 시작')만으로는 id·rect·source·targets·cell_key·Run 승인 해시·초안 키가 바뀌지 않는다.
- Project 지문에 layout.json. 빈 시작(D6): Import 크롭 0, Candidate In·Prepare 경고.
v5 (2026-10, Studio S2 — STUDIO_S2_SPEC §2·§3)
- Import PSD 가 refs.json(크롭 ID 연결)을 해석(refs_auto·refs_main·source.refs_links, 보고서 'refs N(연결)'·레퍼런스 줄),
  Prepare 는 refs_main 을 고름(고정 때 refs_main = 지금 자동 선택 → refs_auto·cell_key·승인 해시·초안 키 그대로).
  refs.json 이 없으면 지금 그대로(키를 만들지 않음). Project 지문에 refs.json.
- Ref Cut 노드(SAM3 컷, 무료 출력 노드). 모듈 import 때 board.set_sam3_ckpts(이름에 sam3 가 든 체크포인트).
v6 (2026-10, Studio S3a — STUDIO_S3_SPEC §2.4·§2.5)
- Studio 계획·채택 라우트가 노드와 같은 함수를 쓰도록 옮김(동작·보고서·파일 바이트 그대로 — 단 store._dir_fp 는 목록 뒤 사라진 파일을
  건너뜀, 옛 _dir_fp 는 FileNotFoundError): Spec Draft 계획 본문 → dvlm.draft_plan
  (_draft_plan 은 _DRAFT_MEMO 를 넘기는 얇은 함수), _merge_crop·_owned_paths·_drop_paths·화풍 전파 규칙 → dvlm.merge_crop·
  project_style_for, 미기록 표식 → dvlm.WRITE_MARK·unwritten_specs, _merge_adopted·_new_targets·_adopt_stale·_glossary·
  _file_fp·_dir_fp·_spec_files_fp → store(_new_targets·_glossary 는 얇은 함수).
- Prepare 가 jobs 와 같은 커밋에 매니페스트 prepare_settings·prepare_inputs(§2.2 — 입력 지문은 스펙을 읽기 전에 계산, 같은 설정·입력의
  재실행은 바이트·rev 그대로, 채택 가드가 Import 미반영 크롭을 건너뛰면 지문은 그대로). Export ui 에 bmk_dp_export(§3.6 — 보고서
  문구는 그대로). Spec Draft run 의 _open 뒤 전체(예외·인터럽트·취소·finally 포함 — 핸들을 못 열면 루트를 몰라 표시하지 않음)를
  board.draft_active(root) 로 감쌈(§2.4). 모듈 import 때 board.set_qwen_dir(Spec Draft 와 같은 Qwen 모델 폴더).
v7 (2026-10, Studio 다듬기 배치 3 — _proto/STUDIO_POLISH_PLAN_2610.md §11)
- 모듈 import 때 board.set_output_dir(Studio [출력 폴더 열기] — 3-5a). Export PSD optional 맨 뒤 subfolder(3-5b — 출력
  output/design_patch/<subfolder>, 비우면 지금처럼 project 폴더 이름. 기본값이면 export 해시·기록 그대로).
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import math
import os
import re
import sys
import time
import unicodedata
from functools import lru_cache, partial

import cv2
import numpy as np
import torch
from PIL import Image, ImageDraw, ImageFont

import comfy.model_management
import comfy.utils
import folder_paths

try:
    from . import bmk_design_patch_analysis as an
    from . import bmk_design_patch_board as board
    from . import bmk_design_patch_mask as dmask
    from . import bmk_design_patch_prompt as dprompt
    from . import bmk_design_patch_psd as dpsd
    from . import bmk_design_patch_refs as refs
    from . import bmk_design_patch_runner as runner
    from . import bmk_design_patch_store as store
    from . import bmk_design_patch_vlm as dvlm
    from . import bmk_sam3_mask_layers as sam3
except ImportError:  # 단독 실행/테스트
    import bmk_design_patch_analysis as an
    import bmk_design_patch_board as board
    import bmk_design_patch_mask as dmask
    import bmk_design_patch_prompt as dprompt
    import bmk_design_patch_psd as dpsd
    import bmk_design_patch_refs as refs
    import bmk_design_patch_runner as runner
    import bmk_design_patch_store as store
    import bmk_design_patch_vlm as dvlm
    import bmk_sam3_mask_layers as sam3

logger = logging.getLogger(__name__)

_TAG = "[ComfyUI_BMK_Nodes::DesignPatch]"

PROJECT_TYPE = "BMK_DP_PROJECT"
_CATEGORY = "BMK/Image"
_ALIASES = ["multi layer crop edit", "design patch", "디자인 패치", "디자인 수정", "디자인 보정", "파츠 보정",
            "크롭 수정", "레퍼런스 보정", "psd"]

_MODELS = ["gpt-image-2.5-sunburst", "gpt-image-2.5-flare"]
_QUALITIES = ["max", "xhigh", "high", "medium", "low"]  # gpt-image-2.5 가 받는 품질(medium·low 는 시험·저비용용)
_REF_STYLES = ["at", "plain", "both"]
_REGISTER_MODES = ["guarded_affine", "translation", "off"]
_TONE_MODES = ["field", "global", "off"]
_ROI_PRIORS = ["none", "hand_bbox"]
_MASK_SOURCES = ["auto", "hand", "hand_else_auto"]
_EXPORT_MASK_SOURCES = ["hand_else_auto", "auto", "hand"]
_ZORDERS = ["auto_small_on_top", "harvest"]
_LAYER_MODES = ["smart_object", "pixel"]
_ALTERNATES = ["hidden_all", "none"]
_COLORS = ["toned", "raw"]
_SOURCES = ["psd_harvest", "folder"]

_ANALYSIS_VERSION = "dp-an/2"
_ANALYSIS_KEEP = 4  # 후보마다 남기는 분석 설정 수(Analyze 가지 여럿·파라미터 조정). 넘으면 가장 오래된 것을 지운다
_ANALYZE_DEFAULTS = {"register": "guarded_affine", "tone": "field", "tone_sigma": 16, "dE": 8.0, "grow": 9,
                     "feather_sigma": 3.5, "roi_prior": "none"}
_COMPOSE_DEFAULTS = {"mask_source": "hand_else_auto", "use_registration": True, "use_tone": True,
                     "zorder": "auto_small_on_top"}
_TONE_CLAMP = 12.0
_BACKGROUND = "opaque"  # cell_key 의 background (GPT 편집 결과는 불투명)
_EXPORT_VERSION = "dp-export/3"
_EXPORT_UI = "bmk_dp_export"  # Export ui 결과 [{outcome: new|unchanged|empty, psd, rev}] — Studio 가 /api/history 로 판정(exports 만으로는 못 앎)
_MIN_CROP_IOU = 0.5  # 이보다 크롭과 덜 겹치는 SO 는 부분 패치로 보고 분석하지 않음
_THUMB = 160
_SAFE_PREFIX_RE = re.compile(r"^[\w\-.가-힣 ]+$")


# ══════════════════════════════════════════════════════════════════════
# 공통 — 핸들 / 매니페스트 / 경로
# ══════════════════════════════════════════════════════════════════════
def _strip_path(p) -> str:
    return str(p or "").strip().strip('"').strip("'").strip()


def _project_root(project: str, base_dir: str) -> str:
    base = _strip_path(base_dir) or folder_paths.get_input_directory()
    return store.project_root(base, str(project or "").strip())


_BRANCH_KEYS = ("analyze", "compose", "draft")


def _handle(root: str, m: dict, src: dict | None = None, **branch) -> dict:
    """핸들 = {schema, root, project, rev} + 가지 설정(analyze / compose / draft). src(입력 핸들)의 가지 설정을 이어받고
    branch 로 덮는다. 가지 설정은 ComfyUI 그래프를 따라 흐르므로 하류 노드는 자기 상류의 설정을 쓴다."""
    h = {"schema": store.SCHEMA, "root": root, "project": m["project"], "rev": int(m["rev"])}
    for k in _BRANCH_KEYS:
        v = branch[k] if k in branch else (src or {}).get(k)
        if v is not None:
            h[k] = v
    return h


def _open(project) -> tuple[str, dict]:
    if not isinstance(project, dict) or project.get("schema") != store.SCHEMA or not project.get("root"):
        raise ValueError(f"{_TAG} project 입력이 BMK Design Patch Project 핸들이 아닙니다")
    root = project["root"]
    if not os.path.isfile(os.path.join(root, store.MANIFEST_NAME)):
        raise ValueError(f"{_TAG} 매니페스트가 없습니다: {root} (BMK Design Patch Project 를 먼저 실행하세요)")
    return root, store.load_manifest(root)


def _json_default(o):
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(f"JSON 으로 바꿀 수 없는 값: {type(o).__name__}")


def _plain(obj):
    """매니페스트에 넣을 값을 JSON 기본형으로(numpy·튜플 제거) — 저장 전후 비교가 흔들리지 않게."""
    return json.loads(json.dumps(obj, ensure_ascii=False, default=_json_default))


def _snapshot(m: dict) -> str:
    return json.dumps(m, sort_keys=True, ensure_ascii=False, default=_json_default)


def _commit(root: str, m: dict, before: str, src: dict | None = None, **branch) -> tuple[dict, list[str]]:
    """3-way 병합 저장(store.commit_merge): 이 노드가 연 뒤에 보드·Run 이 쓴 변경을 덮지 않는다. 합친 결과가 디스크와 같으면
    저장하지 않는다(같은 입력의 재실행은 rev 그대로). m 은 저장된 내용(rev 포함)으로 바뀐다.
    반환 (핸들, 병합 충돌 경고 — 보고서 끝에 붙인다)."""
    merged, _rev, conflicts = store.commit_merge(root, before, m)
    m.clear()
    m.update(merged)
    return _handle(root, m, src, **branch), [f"매니페스트 병합 충돌(이 노드의 값으로 저장): {c}" for c in conflicts]


def _abs(root: str, rel: str) -> str:
    return store.resolve_path(root, rel)


_LEDGER_FILE_RE = re.compile(r"a\d+\.[a-z]+\.json")
_REPOLL_MARK = "repoll_pending.json"  # vlm/ 바로 아래. Spec Draft 가 created(무료 재폴링 대기)를 남기고 끝날 때마다 다시 쓴다


def _vlm_fp(root: str):
    """Spec Draft 원장 지문: vlm/<crop_id>/<key>/a<n>.<kind>.json(화풍 _style 포함)의 이름·크기 + 재폴링·미기록 표시 파일.
    vlm/img/·review.md·last_report.txt 는 실행마다 다시 쓰여 뺀다. Cancel·폴링 실패·401 뒤 원장에만 남은 일(재폴링·
    도착분 병합)이 있으면 다음 큐에서 Draft 가 다시 실행되고, 바뀐 것이 없으면 같은 값이다(캐시 유지). vlm/ 가 없을 때와
    원장 없이 review.md 만 생겼을 때도 같은 값 — 첫 드라이런이 다음 큐를 다시 실행시키지 않게."""
    base = os.path.join(root, dvlm.LEDGER_DIR)
    rows = []
    for cid in sorted(os.listdir(base)) if os.path.isdir(base) else []:
        cdir = os.path.join(base, cid)
        if cid == "img" or not os.path.isdir(cdir):
            continue
        for key in sorted(os.listdir(cdir)):
            kdir = os.path.join(cdir, key)
            if os.path.isdir(kdir):
                rows += [f"{cid}/{key}/{fn}|{os.path.getsize(os.path.join(kdir, fn))}"
                         for fn in sorted(os.listdir(kdir)) if _LEDGER_FILE_RE.fullmatch(fn)]
    rows += [str(store._file_fp(os.path.join(base, mark))) for mark in (_REPOLL_MARK, dvlm.WRITE_MARK)]
    return hashlib.sha1("\n".join(rows).encode("utf-8")).hexdigest()[:16]


def _project_fp(root: str) -> str:
    """Project IS_CHANGED. mock 스위치(환경변수·_MOCK_API 파일)도 넣는다 — 켜고 끄면 Draft·Prepare·Run 이 다시 실행돼
    백엔드에 묶인 승인 해시·'mock 초안 교체' 대기·Run 의 초안 대기 경고가 지금 백엔드를 따른다.
    layout.json(Studio 레이아웃 편집, S1)·refs.json(레퍼런스 연결, S2)도 넣는다 — Import 의 IS_CHANGED 는 루트를 모르므로(링크 입력)
    하류 전체를 여기서 깨운다."""
    return "|".join(str(v) for v in (store._file_fp(os.path.join(root, store.MANIFEST_NAME)),
                                     store._file_fp(os.path.join(root, store.LAYOUT_NAME)),
                                     store._file_fp(os.path.join(root, refs.REFS_NAME)),
                                     store._dir_fp(os.path.join(root, "specs")),
                                     store._file_fp(os.path.join(root, "glossary.json")),
                                     store._spec_files_fp(root),
                                     _vlm_fp(root),
                                     runner.mock_enabled(root),
                                     store._file_fp(os.path.join(os.path.dirname(os.path.abspath(root)), runner.MOCK_FILE))))


def _rect_iou(a, b) -> float:
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _sync_picks(m: dict) -> None:
    """picks 맵을 candidates[].picked 에서 다시 만든다(원본은 picked 플래그)."""
    picks: dict[str, list[str]] = {}
    for c in m["candidates"]:
        if c.get("picked"):
            picks.setdefault(f"{c['crop_id']}/{c['tid']}", []).append(c["key"])
    m["picks"] = picks


# ══════════════════════════════════════════════════════════════════════
# 공통 — 이미지
# ══════════════════════════════════════════════════════════════════════
def _to_image(rgb: np.ndarray) -> torch.Tensor:
    return torch.from_numpy(np.ascontiguousarray(rgb[..., :3]).astype(np.float32) / 255.0)[None]


def _read(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def _load_rgba(root: str, rel: str) -> np.ndarray:
    with Image.open(_abs(root, rel)) as im:
        return np.asarray(im.convert("RGBA"))


def _load_rgb(root: str, rel: str) -> np.ndarray:
    with Image.open(_abs(root, rel)) as im:
        return np.asarray(im.convert("RGB"))


def _load_l(root: str, rel: str) -> np.ndarray:
    with Image.open(_abs(root, rel)) as im:
        return np.asarray(im.convert("L"))


def _decode(data: bytes) -> np.ndarray:
    """후보 원본 bytes → RGB, 부분 투명이 있으면 RGBA (uint8)."""
    with Image.open(io.BytesIO(data)) as im:
        im.load()
        if "A" in im.getbands() or (im.mode == "P" and "transparency" in im.info):
            rgba = np.asarray(im.convert("RGBA"))
            return rgba if int(rgba[..., 3].min()) < 255 else np.ascontiguousarray(rgba[..., :3])
        return np.asarray(im.convert("RGB"))


@lru_cache(maxsize=4)
def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def _fit(img: np.ndarray, box: int, bg=(40, 40, 40)) -> Image.Image:
    """box×box 칸 가운데에 비율 유지 축소. RGBA 는 bg 위에 합성."""
    pil = Image.fromarray(img)
    if pil.mode == "RGBA":
        under = Image.new("RGBA", pil.size, bg + (255,))
        pil = Image.alpha_composite(under, pil).convert("RGB")
    elif pil.mode != "RGB":
        pil = pil.convert("RGB")
    s = min(box / pil.width, box / pil.height)
    pil = pil.resize((max(1, round(pil.width * s)), max(1, round(pil.height * s))), Image.LANCZOS)
    cell = Image.new("RGB", (box, box), bg)
    cell.paste(pil, ((box - pil.width) // 2, (box - pil.height) // 2))
    return cell


def _grid(tiles: list[Image.Image], cols: int, bg=(24, 24, 24)) -> np.ndarray:
    if not tiles:
        im = Image.new("RGB", (320, 48), bg)
        ImageDraw.Draw(im).text((8, 14), "(empty)", fill=(200, 200, 200), font=_font(16))
        return np.asarray(im)
    tw = max(t.width for t in tiles)
    th = max(t.height for t in tiles)
    cols = max(1, min(cols, len(tiles)))
    rows = math.ceil(len(tiles) / cols)
    sheet = Image.new("RGB", (cols * tw, rows * th), bg)
    for i, t in enumerate(tiles):
        sheet.paste(t, ((i % cols) * tw, (i // cols) * th))
    return np.asarray(sheet)


def _label_tile(cells: list[Image.Image], lines: list[str], color=(220, 220, 220)) -> Image.Image:
    pad, lh = 4, 15
    w = sum(c.width for c in cells) + pad * (len(cells) + 1)
    h = max(c.height for c in cells) + pad * 2 + lh * len(lines)
    tile = Image.new("RGB", (w, h), (24, 24, 24))
    x = pad
    for c in cells:
        tile.paste(c, (x, pad))
        x += c.width + pad
    d = ImageDraw.Draw(tile)
    y = pad + max(c.height for c in cells) + 1
    for ln in lines:
        d.text((pad, y), ln, fill=color, font=_font(12))
        y += lh
    return tile


def _ascii(s: str) -> str:
    """기본 폰트에 한글 글리프가 없어 시트 라벨은 ASCII 만 쓴다."""
    return "".join(ch if 32 <= ord(ch) < 127 else "?" for ch in str(s))


# ══════════════════════════════════════════════════════════════════════
# 마스크 / 크롭 도우미
# ══════════════════════════════════════════════════════════════════════
def _mask_region(mask: dict | None, x0: int, y0: int, x1: int, y1: int) -> np.ndarray | None:
    """{"arr" uint8, "left", "top", "bg"} 마스크를 캔버스 창 [x0,y0,x1,y1] 로 잘라 float32 0..1. None 이면 None."""
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


def _hand_mask(root: str, cand: dict) -> dict | None:
    hm = cand.get("hand_mask")
    if not hm:
        return None
    return {"arr": _load_l(root, hm["file"]), "left": int(hm["rect"][0]), "top": int(hm["rect"][1]),
            "bg": int(hm.get("bg", 0))}


def _hand_local(root: str, cand: dict, rect) -> np.ndarray | None:
    """손 마스크를 crop 로컬 HxW float32 로(창 밖 = 마스크 배경값)."""
    return _mask_region(_hand_mask(root, cand), *rect)


def _hand_roi(hand: np.ndarray | None):
    if hand is None:
        return None
    ys, xs = np.nonzero(hand >= 0.5)
    if not len(xs):
        return None
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()) + 1, int(ys.min()), int(ys.max()) + 1
    px, py = 0.1 * (x1 - x0), 0.1 * (y1 - y0)
    return [x0 - px, y0 - py, x1 + px, y1 + py]


def _crop_map(m: dict) -> dict:
    return {c["id"]: c for c in m["crops"]}


def _analyzable(cand: dict, rect) -> bool:
    """분석 기준 rect 전체를 덮는 후보(GPT 결과)만 분석한다. 부분 패치 SO 는 수확한 quad·마스크 그대로."""
    return _rect_iou(dpsd.quad_bbox(cand["quad"]), rect) >= _MIN_CROP_IOU


def _gen_frame(root: str, m: dict, crop: dict, cand: dict) -> tuple[list[int], str | None, list[int] | None]:
    """분석 기준 (rect, 크롭 소스, 유효 영역). 수확 후보와 제자리 후보는 지금 크롭. Run·폴더 후보의 quad 가 지금 크롭
    rect 와 다르면(PS 에서 rect 를 옮긴 뒤) 만들 때의 크롭 = 자기 quad + 그때의 소스(Run = 원장 req 의 canvas 입력에 남은
    평탄화 전 크롭 소스, 폴더 = 등록 때의 크롭 소스). 그 소스 파일이 없으면 None(분석 안 함 — 보고서·보드가 경고).
    유효 영역은 소스 알파를 쓰면 None. source 가 없는 예전 원장은 평탄화한 canvas(RGB)로 분석하고 유효 영역 = 캔버스
    (base_crop 크롭이면 ∩ work_rect — Import 가 크롭 소스 알파를 만든 규칙) — 그 밖은 평탄화로 채운 여백."""
    if cand.get("origin") not in ("run", "folder"):
        return crop["rect"], crop["source"], None
    rect = dpsd.quad_bbox(cand["quad"])
    if rect == crop["rect"]:
        return rect, crop["source"], None
    info = cand.get("origin_info") or {}
    if cand["origin"] == "folder":
        src = info.get("crop_source")
        return rect, (src if src and os.path.isfile(_abs(root, src)) else None), None
    req = runner.read_ledger(root, info["cell_key"]).get(int(info["rep"]), {}).get("req") or {}
    inp = next((i for i in req.get("inputs") or [] if i.get("role") == "canvas"), {})
    if inp.get("source") and os.path.isfile(_abs(root, inp["source"])):
        return rect, inp["source"], None
    src = inp.get("file")
    W, H = m["canvas"]
    wx0, wy0, wx1, wy1 = (m.get("work_rect") or [0, 0, W, H]) if crop.get("source_kind") == "base_crop" else [0, 0, W, H]
    return rect, (src if src and os.path.isfile(_abs(root, src)) else None), [max(0, wx0), max(0, wy0), min(W, wx1), min(H, wy1)]


# ══════════════════════════════════════════════════════════════════════
# 분석 (Analyze 노드와 Compose/Export 의 누락분 보충이 공유)
# ══════════════════════════════════════════════════════════════════════
def _params_hash(cand: dict, frame: tuple, prm: dict) -> str:
    """분석이 실제로 읽는 것: 후보·분석 기준(_gen_frame 의 소스·rect·유효 영역), 분석 대상 여부(quad 가 rect 를 덮는지),
    손 마스크(픽셀·위치·배경), 설정."""
    rect, src, valid = frame
    hm = cand.get("hand_mask") or {}
    payload = {"v": _ANALYSIS_VERSION, "cand": cand["file"], "src": src, "rect": rect,
               "analyzable": _analyzable(cand, rect),
               "hand": [hm.get("file"), hm.get("rect"), int(hm.get("bg", 0))] if hm else None, "prm": prm}
    if valid is not None:  # Run canvas 로 분석할 때만 — 지금 크롭 소스로 분석하는 항목의 해시는 그대로
        payload["valid"] = valid
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def _entry_valid(root: str, entry: dict | None, ph: str) -> bool:
    if not entry or entry.get("params_hash") != ph:
        return False
    if entry.get("skipped"):
        return True
    files = [entry.get("automask"), entry.get("thumb"), (entry.get("tone") or {}).get("delta")]
    return all(f is None or os.path.isfile(_abs(root, f)) for f in files)


def _save_delta(root: str, delta: np.ndarray, sigma: int, prefix: str) -> tuple[str, list[int]]:
    """톤 보정장은 저주파라 σ/4 격자로 줄여 float16 .npy 로 저장한다(읽을 때 선형 보간으로 복원)."""
    h, w = delta.shape[:2]
    f = max(1, int(sigma) // 4)
    small = cv2.resize(delta, (max(1, round(w / f)), max(1, round(h / f))), interpolation=cv2.INTER_AREA) if f > 1 else delta
    buf = io.BytesIO()
    np.save(buf, np.ascontiguousarray(small, dtype=np.float16), allow_pickle=False)
    return store.save_bytes_ca(root, "derived", buf.getvalue(), "npy", prefix=prefix), [w, h]


def _load_delta(root: str, tone: dict) -> np.ndarray:
    small = np.load(io.BytesIO(_read(_abs(root, tone["delta"]))), allow_pickle=False).astype(np.float32)
    w, h = tone["size"]
    if small.shape[:2] != (h, w):
        small = cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)
    return small


def _derived_prefix(key: str, ph: str) -> str:
    """derived/ 파일 접두 "<key>_<phash6>_". store 접두 한도(32자)에 "toned_"·"thumb_" 까지 들어가도록, 긴 후보 키
    (다중 타깃 Run 후보 r_<cell12>_<rep>_<slot>_<tid> 등)는 앞 10자 + 키 해시 6자로 줄인다(결정적)."""
    if len(key) > 18:
        key = key[:10] + hashlib.blake2b(key.encode(), digest_size=3).hexdigest()
    return f"{key}_{ph[:6]}_"


def _analyze_one(root: str, crop: dict, cand: dict, prm: dict, ph: str, frame: tuple) -> dict:
    """후보 1개 분석 → analysis 항목(JSON 기본형). frame = _gen_frame(자동 마스크·정합은 그 rect 의 로컬 좌표, 항목의
    rect 로 남김). 산출 파일은 derived/<key>_<phash6>_* (내용 주소)."""
    rect, src, valid = frame
    head = {"params_hash": ph, "params": dict(prm), "crop_id": crop["id"], "tid": cand["tid"]}
    if not _analyzable(cand, rect):
        return dict(head, skipped="quad 가 크롭과 맞지 않는 부분 패치(IoU < 0.5) - 수확한 quad·마스크 그대로 사용")
    x0, y0, x1, y1 = rect
    w, h = x1 - x0, y1 - y0
    base = _load_rgba(root, src) if src else None
    if base is None or base.shape[:2] != (h, w):
        return dict(head, no_source=True,
                    skipped=f"크롭 rect 를 옮기기 전 후보인데 만들 때의 크롭 소스({w}x{h} at {x0},{y0})를 찾지 못함 - "
                            "자동 마스크 없음: 손 마스크가 없으면 Compose/Export 가 SO 전체를 합성")
    if valid is not None:  # 평탄화로 채운 캔버스·작업 영역 밖은 Import 의 크롭 소스처럼 알파 0
        base = base.copy()
        base[..., 3] = 0
        vx0, vy0, vx1, vy1 = max(valid[0], x0) - x0, max(valid[1], y0) - y0, min(valid[2], x1) - x0, min(valid[3], y1) - y0
        if vx1 > vx0 and vy1 > vy0:
            base[vy0:vy1, vx0:vx1, 3] = 255
    full = _decode(_read(_abs(root, cand["file"])))
    G = an.place(np.ascontiguousarray(full[..., :3]), (w, h))
    hand = _hand_local(root, cand, rect)
    reg = an.register(base, G, mode=prm["register"], exclude_mask=hand)
    Ga = an.warp_affine(G, reg["matrix"]) if reg["applied"] else G
    roi = _hand_roi(hand) if prm["roi_prior"] == "hand_bbox" else None
    am = an.auto_mask(base, Ga, dE=float(prm["dE"]), grow=int(prm["grow"]), feather_sigma=float(prm["feather_sigma"]),
                      roi=roi, align=False)
    pre = _derived_prefix(cand["key"], ph)
    tone = {"mode": prm["tone"], "delta": None}
    delta = None
    if prm["tone"] != "off":
        excl = am["mask"] > 0.01
        if hand is not None:
            excl |= hand >= 0.01
        tf = an.tone_field(base, Ga, excl.astype(np.float32), sigma=int(prm["tone_sigma"]), clamp=_TONE_CLAMP)
        tone["stats"] = tf["stats"]
        if "note" not in tf["stats"]:
            if prm["tone"] == "field":
                delta = tf["delta"]
            else:
                g = np.clip(np.asarray(tf["stats"]["mean_rgb"], np.float32), -_TONE_CLAMP, _TONE_CLAMP)
                delta = np.broadcast_to(g, (h, w, 3)).astype(np.float32)
            tone["delta"], tone["size"] = _save_delta(root, delta, int(prm["tone_sigma"]), pre + "tone_")
    Gt = an.apply_delta(Ga, delta) if delta is not None else Ga
    gates = an.gates(base, Gt, am["mask"], reg)
    am_rel = store.save_png_ca(root, "derived", am["mask"], prefix=pre + "am_")

    # 접촉시트 칸: 크롭 소스 | 정렬·톤 보정 후보 | 자동 마스크 오버레이(빨강) [+ 손 마스크 외곽 초록]
    mk = am["mask"][..., None]
    ov = (Gt.astype(np.float32) * (1 - 0.45 * mk) + np.float32([255, 40, 40]) * 0.45 * mk)
    if hand is not None:
        edge = cv2.morphologyEx((hand >= 0.5).astype(np.uint8), cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)) > 0
        ov[edge] = (40, 255, 80)
    cells = [_fit(base, _THUMB), _fit(Gt, _THUMB), _fit(np.clip(ov, 0, 255).astype(np.uint8), _THUMB)]
    shift = an.quad_from_matrix(reg["matrix"], rect)
    disp = max(abs(a - b) for a, b in zip(shift, dpsd.quad_from_rect(rect)))
    lines = [_ascii(f"{crop['id']} {cand['tid']} {cand['key']}" + ("" if rect == crop["rect"] else f" @{x0},{y0} {w}x{h}")),
             _ascii(f"reg {reg['kind']} {disp:.1f}px  s=({reg['sx']:.3f},{reg['sy']:.3f}) rot {reg['rot_deg']:+.2f}"),
             _ascii(f"mad {gates['outside_mad']:.1f} >24 {gates['outside_frac_gt24']:.2f} "
                    + ("FAIL " + ",".join(gates["fails"]) if gates["fails"] else "ok"))]
    tile = _label_tile(cells, lines, (255, 140, 120) if gates["fails"] else (220, 220, 220))
    thumb_rel = store.save_png_ca(root, "derived", np.asarray(tile), prefix=pre + "thumb_")

    reg_out = {k: reg[k] for k in ("matrix", "applied", "kind", "dx", "dy", "sx", "sy", "rot_deg", "shear",
                                   "med_before", "med_after", "improve", "disp", "masked")}
    return _plain({
        **head, "rect": rect, "reg": reg_out, "quad_reg": an.quad_from_matrix(reg["matrix"], rect),
        "tone": tone, "automask": am_rel, "components": am["components"][:16],
        "mask_area": round(float(am["mask"].sum()), 1), "gates": gates, "thumb": thumb_rel,
    })


def _derived_files(entry: dict) -> set:
    tone = entry.get("tone") or {}
    return {entry.get("automask"), entry.get("thumb"), tone.get("delta"), tone.get("full")} - {None}


def _drop_derived(root: str, old: dict, keep) -> None:
    """밀려난 분석 항목의 derived/ 파일을 지운다(남은 항목이 쓰는 파일은 둠). derived 는 다시 만들 수 있다."""
    used = set().union(*(_derived_files(e) for e in keep))
    for rel in _derived_files(old) - used:
        if rel.startswith("derived/"):
            try:
                os.remove(_abs(root, rel))
            except OSError:  # 이미 없음 / Windows 에서 뷰어가 잡고 있음
                pass


def _ensure_analysis(root: str, m: dict, cands: list[dict], prm: dict | None = None,
                     pbar=None) -> tuple[dict, int, int, list[str]]:
    """cands 의 분석 항목을 prm 설정(None = 기본값)으로 보장한다. prm = Analyze 노드 설정, Compose/Export 는 핸들의
    상류 Analyze 설정(없으면 기본값). analysis[key] = {params_hash: 항목}(삽입 순 = 오래된 것 먼저) — 같은 해시의
    항목이 있고 산출 파일이 있으면 건너뛰고, 후보마다 _ANALYSIS_KEEP 개를 넘으면 가장 오래된 항목과 그 파일을 지운다.
    반환 ({key: 이번 설정의 항목}, 계산 수, 캐시 적중 수, 경고)."""
    use = dict(prm or _ANALYZE_DEFAULTS)
    crops = _crop_map(m)
    entries: dict[str, dict] = {}
    done = hit = 0
    warns: list[str] = []
    for c in cands:
        crop = crops.get(c["crop_id"])
        if crop is None:
            warns.append(f"{c['key']}: 크롭 {c['crop_id']} 없음 → 분석 건너뜀")
        else:
            frame = _gen_frame(root, m, crop, c)
            ph = _params_hash(c, frame, use)
            per = m["analysis"].get(c["key"])
            if not per or "params_hash" in per:  # 없음 / dp-an/1 의 평평한 항목(설정이 하나뿐이던 모양)
                per = m["analysis"][c["key"]] = {}
            if _entry_valid(root, per.get(ph), ph):
                hit += 1
                if not per[ph].get("skipped"):
                    per[ph].setdefault("rect", frame[0])  # rect 를 남기기 전 항목: 해시에 rect 가 있어 이 rect 에서 계산한 것
            else:
                per[ph] = _analyze_one(root, crop, c, use, ph, frame)
                done += 1
                while len(per) > _ANALYSIS_KEEP:
                    _drop_derived(root, per.pop(next(iter(per))), per.values())
            same = [o.get("seq", 0) for k, o in per.items() if k != ph and o.get("params") == use]
            if same and per[ph].get("seq", 0) <= max(same):
                # 같은 설정의 다른 기준 항목(소스 없음·canvas 대체·옛 손 마스크 등)이 있으면 지금 항목의 seq 를 가장 크게 —
                # 보드(_entry_for)도 이 항목을 본다. 순서만 바뀐 것은 매니페스트 병합이 저장하지 않아 seq 로 남긴다
                per[ph]["seq"] = max(o.get("seq", 0) for o in per.values()) + 1
            entries[c["key"]] = per[ph]
        if pbar is not None:
            pbar.update(1)
    return entries, done, hit, warns


# ══════════════════════════════════════════════════════════════════════
# 합성 계획 (Compose 와 Export 가 같은 순서·quad·마스크를 쓰도록 공유)
# ══════════════════════════════════════════════════════════════════════
def _auto_mask_dict(root: str, entry: dict | None, crop: dict) -> dict | None:
    """자동 마스크는 분석 기준 rect(항목의 rect, 예전 항목은 크롭 rect)의 로컬 좌표."""
    if not entry or entry.get("skipped") or not entry.get("automask"):
        return None
    x0, y0 = (entry.get("rect") or crop["rect"])[:2]
    return {"arr": _load_l(root, entry["automask"]), "left": x0, "top": y0, "bg": 0}


def _final_mask(root: str, cand: dict, entry: dict | None, crop: dict, mask_source: str) -> tuple[dict | None, str]:
    """편집 전(기준) 최종 마스크. 고르는 규칙은 dmask.base_source 하나(S4 편집의 기준 지문과 같게)."""
    src, _file = dmask.base_source(cand, entry, mask_source)
    if src == "hand":
        return _hand_mask(root, cand), "hand"
    if src == "auto":
        return _auto_mask_dict(root, entry, crop), "auto"
    return None, "none"


def _eff_quad(cand: dict, entry: dict | None, use_registration: bool) -> list[float]:
    if use_registration and entry and not entry.get("skipped") and entry["reg"]["applied"]:
        return [float(v) for v in entry["quad_reg"]]
    return [float(v) for v in cand["quad"]]


def _in_crop(crop: dict | None, tid: str) -> bool:
    """후보의 크롭·타깃(tid)이 지금 매니페스트에 있음(보드와 같은 규칙). _plan 은 경고하고 빼고, Compose·Export 는 분석·
    빈 PSD 판단 전에 뺀다."""
    return crop is not None and any(t["tid"] == tid for t in crop["targets"])


def _target_label(crop: dict, tid: str) -> str:
    """레이어명의 타깃 조각: 라벨이 크롭 부위명과 같으면(레거시) tid, 아니면 라벨."""
    for t in crop.get("targets") or []:
        if t["tid"] == tid:
            lab = (t.get("label") or "").strip()
            if lab and lab != crop.get("part"):
                return lab
    return tid


def _plan(root: str, m: dict, entries: dict, mask_source: str, use_registration: bool, zorder: str, alternates: bool,
          warns: list[str], area_mask_source: str | None = None) -> list[dict]:
    """[{crop, targets: [{tid, label, slices: [{cand, entry, quad, mask, msrc, area, base_mask, base_msrc, mask_edit}], alts: [...]}]}]
    (아래 → 위). entries = _ensure_analysis 가 돌려준 {key: 분석 항목}(분석이 필요 없는 설정이면 {}). 보드에서 탈락시킨 후보(rejects)는
    pick 이든 대안이든 넣지 않는다. 크롭이나 타깃(tid)이 매니페스트에서 사라진 후보도 경고하고 뺀다(보드와 같게).
    ★ 에 마스크 편집(S4 mask_edits)이 있으면 mask = 그 래스터·msrc "edit"·mask_edit = 파일(mask_source 와 상관없이 — D4),
    base_mask·base_msrc = 편집 전 기준(지금 편집 창·분석 항목 ph 가 기록과 다르면 '마스크 편집 기준 바뀜' 경고). area(z 순서)는 기준 마스크로
    잰다 — 편집이 다른 패치와의 위아래를 바꾸지 않게(D5)."""
    crops = _crop_map(m)
    rejects = m.get("rejects") or {}
    groups: dict[tuple[str, str], dict] = {}
    order_idx = {c["key"]: i for i, c in enumerate(m["candidates"])}
    for c in m["candidates"]:
        if c["key"] in rejects:
            continue
        crop = crops.get(c["crop_id"])
        if not _in_crop(crop, c["tid"]):
            warns.append(f"{c['key']}: 매니페스트에 크롭 {c['crop_id']} 가 없음(다시 가져온 PSD 에서 사라짐) → 제외" if crop is None
                         else f"{c['key']}: 크롭 {crop['id']} 에 타깃 {c['tid']} 가 없음(레이어명·채택에서 빠짐) → 제외")
            continue
        if not c.get("picked") and not alternates:
            continue
        entry = entries.get(c["key"])
        mask, msrc = _final_mask(root, c, entry, crop, mask_source)
        base_mask, base_msrc, mask_edit = mask, msrc, None
        rec = dmask.edit_record(m, c["key"]) if c.get("picked") else None
        if rec is not None:
            em, why = dmask.edit_mask(root, rec)
            if em is None:
                warns.append(f"{c['key']} ({crop['name']}): {why}")
            else:
                mask, msrc, mask_edit = em, "edit", rec["file"]
        if mask_source != "hand" and msrc == "none":
            warns.append(f"{c['key']} ({crop['name']}): 손 마스크도 자동 마스크도 없음"
                         + (" (만들 때의 크롭 소스를 찾지 못함)" if (entry or {}).get("no_source") else "")
                         + " → 마스크 없이(SO 전체 보임)")
        quad = _eff_quad(c, entry, use_registration)
        if dpsd.quad_kind(quad) == "perspective":  # PS Distort/Perspective — 쓰기·캐시 렌더는 affine 만
            sw, sh = c["src_size"]
            quad = an.quad_from_matrix(an.matrix_from_quad(quad, [0, 0, sw, sh]), [0, 0, sw, sh])
            warns.append(f"{c['key']} ({(c.get('origin_info') or {}).get('layer') or crop['name']}): 원근(Distort) quad 는 "
                         f"지원하지 않아 가장 가까운 평행사변형으로 근사")
        bb = dpsd.quad_bbox(quad)
        if mask_edit is not None:
            # 편집은 기록 창 그대로 쓴다(D4) — 이번 계획의 패치 창·분석 항목이 편집할 때와 다르면 알린다. 보드 stale 은 마지막 Export 의
            # compose 로 보므로 이번 Compose·Export 의 정합 사용이 바뀐 것은 여기서만 보인다. mask_source 는 Export 가 따로 가져 보지 않는다
            why = []
            now = dmask.edit_rect(bb, m["canvas"])
            if now != rec["rect"]:
                why.append(f"창 {rec['rect']} → {now}")
            if entry is not None and dmask.base_fp(c, entry, mask_source)["ph"] != rec["base"]["ph"]:
                why.append("분석 항목")
            if why:
                warns.append(f"{c['key']} ({crop['name']}): 마스크 편집 기준 바뀜({' · '.join(why)}) → 편집 그대로 씀"
                             " — 어긋났으면 마스크 편집 [기준으로 다시 시작]")
        # Export 의 출력 마스크는 별도여도 쌓는 순서의 넓이는 Compose 기준(편집 전)으로 잰다.
        area_mask = base_mask if area_mask_source is None or area_mask_source == mask_source else _final_mask(
            root, c, entry, crop, area_mask_source)[0]
        reg_m = _mask_region(area_mask, *bb)
        area = float(reg_m.sum()) if reg_m is not None else float((bb[2] - bb[0]) * (bb[3] - bb[1]))
        z = c.get("z")
        item = {"cand": c, "entry": entry, "quad": quad, "mask": mask, "msrc": msrc, "area": area,
                "base_mask": base_mask, "base_msrc": base_msrc, "mask_edit": mask_edit,
                "z": (0, int(z)) if z is not None else (1, order_idx[c["key"]])}
        g = groups.setdefault((c["crop_id"], c["tid"]), {"slices": [], "alts": []})
        g["slices" if c.get("picked") else "alts"].append(item)

    manual = store.z_order(m)

    def order(items, area_of, z_of, scope, key_of):
        auto = sorted(items, key=z_of if zorder == "harvest" else lambda it: -area_of(it))
        keyed = {key_of(it): it for it in auto}
        return [keyed[k] for k in an.zorder_manual(keyed, manual.get(scope))]

    by_crop: dict[str, list[dict]] = {}
    for (cid, tid), g in groups.items():
        g["slices"] = order(g["slices"], lambda it: it["area"], lambda it: it["z"],
                            f"s:{cid}/{tid}", lambda it: it["cand"]["key"])
        g["alts"] = sorted(g["alts"], key=lambda it: it["z"])
        members = g["slices"] or g["alts"]
        by_crop.setdefault(cid, []).append({
            "tid": tid, "label": _target_label(crops[cid], tid), "slices": g["slices"], "alts": g["alts"],
            "area": sum(it["area"] for it in g["slices"]), "z": min(it["z"] for it in members)})
    plan = []
    for cid in crops:  # 같은 넓이 크롭 = 매니페스트 크롭 순서(후보가 들어온 순서가 아님)
        if cid not in by_crop:
            continue
        target_idx = {t["tid"]: i for i, t in enumerate(crops[cid]["targets"])}
        targets = sorted(by_crop[cid], key=lambda t: target_idx[t["tid"]])
        targets = order(targets, lambda t: t["area"], lambda t: t["z"], f"t:{cid}", lambda t: t["tid"])
        shown = [t for t in targets if t["slices"]] or targets  # 숨김 대안만 있는 타깃이 크롭 순서를 바꾸지 않게(Compose = Export)
        plan.append({"crop": crops[cid], "targets": targets, "area": sum(t["area"] for t in targets),
                     "z": min(t["z"] for t in shown)})
    return order(plan, lambda p: p["area"], lambda p: p["z"], "crops", lambda p: p["crop"]["id"])


def _cand_pixels(root: str, item: dict, toned: bool) -> tuple[np.ndarray, bool]:
    """후보 풀해상도 픽셀(RGB 또는 RGBA uint8). toned 이고 톤 보정장이 있으면 적용(원본 좌표로 되돌려서)."""
    c, entry = item["cand"], item["entry"]
    tone = (entry or {}).get("tone") or {}
    if toned and entry is not None and not entry.get("skipped") and tone.get("full") \
            and os.path.isfile(_abs(root, tone["full"])):
        return _decode(_read(_abs(root, tone["full"]))), True
    full = _decode(_read(_abs(root, c["file"])))
    if not toned or entry is None or entry.get("skipped") or not tone.get("delta"):
        return full, False
    delta = _load_delta(root, tone)
    if entry["reg"]["applied"]:
        delta = an.warp_affine(delta, entry["reg"]["matrix"], inverse=True)
    return an.apply_delta(full, delta, out_size=(full.shape[1], full.shape[0])), True


def _render(pix: np.ndarray, quad, mask: dict | None):
    """SO 캐시(PS 가 다시 그리기 전 보이는 픽셀) + 실효 알파(캐시 알파 × 마스크) → (rgb, alpha8|None, eff, left, top)."""
    rgb, alpha, left, top = dpsd.render_so_cache(pix, quad)
    h, w = rgb.shape[:2]
    eff = np.ones((h, w), np.float32) if alpha is None else alpha.astype(np.float32) / 255.0
    mk = _mask_region(mask, left, top, left + w, top + h)
    if mk is not None:
        eff *= mk
    return rgb, alpha, eff, left, top


def _clip_box(shape, eff: np.ndarray, left: int, top: int):
    H, W = shape[:2]
    h, w = eff.shape
    return max(0, left), max(0, top), min(W, left + w), min(H, top + h)


def _union_add(union: np.ndarray, eff: np.ndarray, left: int, top: int) -> None:
    x0, y0, x1, y1 = _clip_box(union.shape, eff, left, top)
    if x1 > x0 and y1 > y0:
        union[y0:y1, x0:x1] = np.maximum(union[y0:y1, x0:x1], eff[y0 - top:y1 - top, x0 - left:x1 - left])


def _paste(canvas: np.ndarray, union: np.ndarray, rgb: np.ndarray, eff: np.ndarray, left: int, top: int) -> None:
    """normal blend: canvas += (src − canvas)·eff, union = max(union, eff). 캔버스 밖은 잘라낸다."""
    x0, y0, x1, y1 = _clip_box(canvas.shape, eff, left, top)
    if x1 <= x0 or y1 <= y0:
        return
    a = eff[y0 - top:y1 - top, x0 - left:x1 - left, None]
    src = rgb[y0 - top:y1 - top, x0 - left:x1 - left].astype(np.float32)
    dst = canvas[y0:y1, x0:x1]
    canvas[y0:y1, x0:x1] = dst + (src - dst) * a
    _union_add(union, eff, left, top)


def _slices_in_order(plan: list[dict]):
    for p in plan:
        for t in p["targets"]:
            for it in t["slices"]:
                yield p, t, it


def _needs_analysis(mask_source: str, use_registration: bool, toned: bool) -> bool:
    return mask_source != "hand" or use_registration or toned


# ══════════════════════════════════════════════════════════════════════
# 노드 1 — Project
# ══════════════════════════════════════════════════════════════════════
def _summary(root: str, m: dict) -> str:
    n_pick = sum(1 for c in m["candidates"] if c.get("picked"))
    src = (m.get("source") or {}).get("psd") or "-"
    last = m["exports"][-1]["psd"] if m["exports"] else "-"
    calls = m.get("calls") or []  # 새 프로젝트는 new_manifest(M1 키)라 M2 키가 없다
    n_done = sum(1 for c in calls if c.get("status") == "done")
    return (f"{m['project']} rev {m['rev']} | 크롭 {len(m['crops'])} · 후보 {len(m['candidates'])} (pick {n_pick}, "
            f"탈락 {len(m.get('rejects') or {})}) · 작업 {len(m['jobs'])} · 호출 {n_done}/{len(calls)} · "
            f"분석 {len(m['analysis'])} · export {len(m['exports'])}\n"
            f"폴더: {root}\nPSD: {src}\n마지막 export: {last}")


class BMKDesignPatchProject:
    """Design Patch 프로젝트 폴더(매니페스트)를 열거나 만든다."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "project": ("STRING", {
                    "default": "design_patch",
                    "tooltip": "프로젝트 이름(영문·숫자·한글·_ - .). <base_dir>/bmk_design_patch/<이름>/ 폴더가 원장입니다."}),
                "base_dir": ("STRING", {
                    "default": "",
                    "tooltip": "프로젝트들을 둘 폴더. 비우면 ComfyUI input 디렉터리."}),
            },
        }

    RETURN_TYPES = (PROJECT_TYPE, "STRING")
    RETURN_NAMES = ("project", "summary")
    OUTPUT_TOOLTIPS = ("다른 Design Patch 노드에 연결하는 핸들(텐서 없음).", "매니페스트 요약.")
    FUNCTION = "open"
    CATEGORY = _CATEGORY
    DESCRIPTION = (
        "Design Patch 프로젝트(크롭·후보·분석·작업을 기록하는 매니페스트 폴더)를 열거나 만듭니다. "
        "다른 노드가 매니페스트를 바꾸면 다음 실행에서 하류가 다시 확인되며, 각 노드는 디스크 캐시로 바로 끝납니다."
    )
    SEARCH_ALIASES = _ALIASES + ["project", "manifest", "프로젝트", "원장"]

    def open(self, project, base_dir):
        root = _project_root(project, base_dir)
        os.makedirs(root, exist_ok=True)
        m = store.load_manifest(root)
        name = os.path.basename(root)
        if m["project"] != name or not os.path.isfile(os.path.join(root, store.MANIFEST_NAME)):
            m["project"] = name  # 복사·이름을 바꾼 폴더: 핸들·요약은 폴더 이름을 따른다
            store.save_manifest(root, m)
        logger.info("%s Project %s rev %d (%s)", _TAG, m["project"], m["rev"], root)
        return (_handle(root, m), _summary(root, m))

    @classmethod
    def IS_CHANGED(cls, project="", base_dir="", **kwargs):
        if project is None or base_dir is None:  # 링크된 입력: 값이 보이지 않으니 매번 다시 확인
            return float("nan")
        root = _project_root(project, base_dir)
        return f"{root}|{_project_fp(root)}"


# ══════════════════════════════════════════════════════════════════════
# 노드 2 — Import PSD
# ══════════════════════════════════════════════════════════════════════
def _new_targets(parsed: dict, old_crop: dict | None) -> list[dict]:
    """store.new_targets(레이어명 타깃 → 매니페스트 타깃 — Import 와 평가 도구 대조 M3-N9 가 부름)."""
    return store.new_targets(parsed, old_crop)


def _adopt_changes(m: dict, crop: dict, prev: list[dict]) -> list[str]:
    """채택으로 바뀐 타깃에 이미 후보가 있으면 보고서 줄: 빠진 채택 타깃(보드·Compose·Export 에서 빠짐), 라벨이 바뀐 타깃
    (레거시 A 의 '부위 전체' → 채택 이름, 채택 문구 수정 등 — 그 tid 의 기존 후보는 옛 의미로 만든 것), 새로 채택한 tid 에
    이미 있는 후보(PS 에서 같은 글자를 썼다 지운 타깃의 결과가 붙음). prev = 이번 실행 전 타깃."""
    new = {t["tid"]: t for t in crop["targets"]}
    old = {t["tid"] for t in prev}
    out = []
    for t in prev + [n for n in crop["targets"] if n["tid"] not in old and n.get("origin") == "adopt"]:
        n = new.get(t["tid"])
        if t["tid"] not in old:
            what = f"새로 채택한 타깃 {t['tid']}({t['label']}) 에 같은 글자의 옛 후보가 있음 — 보드에서 확인"
        elif n is None and t.get("origin") == "adopt":
            what = f"채택 타깃 {t['tid']}({t['label']}) 가 빠짐 → 보드·Compose·Export 에서 제외"
        elif n is not None and n["label"] != t["label"] and "adopt" in (t.get("origin"), n.get("origin")):
            what = f"타깃 {t['tid']} 의미 바뀜({t['label']} → {n['label']}) — 기존 후보 확인"
        else:
            continue
        cands = [c for c in m["candidates"] if c["crop_id"] == crop["id"] and c["tid"] == t["tid"]]
        if cands:
            out.append(f"{what}: 후보 {len(cands)} · pick {sum(1 for c in cands if c.get('picked'))}")
    return out


_SAME_RECT = "다른 크롭과 rect 가 같음(타깃을 한 크롭의 '| A: …; B: …' 로 합치는 것을 권장)"
_SAME_NAME = "같은 이름의 크롭이 여러 개(ID 에 -2.. 접미, 폴더 후보·mapping_json 은 ID 로 지정)"
_LAYOUT_WARN = "레이아웃: "  # 레이아웃 모드에서 크롭 정의(layout.json)에서 나온 경고 머리 — 소스 경고와 가른다
_SNAPSHOT_KEYS = ("psd", "fingerprint", "crop_group", "crop_pixels_group")  # 스냅숏을 믿는 조건 = 지금 Import 설정과 같음


def _source_warnings(s: dict) -> list[str]:
    """crop_sources 항목에서 나온 경고(합성 경고·캔버스 밖) + 투명 여백 안내."""
    return list(s["warnings"]) + ([f"투명 여백 {s['alpha_frac'] * 100:.1f}% → Prepare 가 가장자리 복제로 평탄화"]
                                  if s["alpha_frac"] > 0 else [])


def _kept_source_warnings(old: dict) -> list[str]:
    """소스를 다시 쓰는 크롭의 소스 경고 = 옛 경고에서 크롭 정의에서 나온 것(레이아웃 머리·같은 rect·같은 이름·레이어명 파싱)을
    뺀 것. 이관 직후(PSD 모드 크롭)의 shape 읽기 경고는 남는다(그 rect 를 이관했으므로)."""
    drop = {_SAME_RECT, _SAME_NAME, *store.parse_crop_layer_name(old["layer_name"])["warnings"]}
    return [w for w in old["warnings"] if not w.startswith(_LAYOUT_WARN) and w not in drop]


_CROP_GROUP_HINT = "PSD 의 02.크롭영역 그룹을 확인하거나, Studio 로만 쓰려면 먼저 레이아웃 편집 시작"
_LATE_LAYOUT = ("주의: 이 Import 중 layout.json 이 생김 — 이관 기준점이 이 Import 전 매니페스트일 수 있음"
                "(다음 큐에서 PSD 를 열어 갈라짐 확인)")


def _auto_group(crop_group: str) -> bool:
    return crop_group.strip().lower() in ("", "auto")


def _psd_rects(psd, crop_group: str) -> list[dict]:
    """dpsd.read_crop_rects. crop_group 이 auto 인데 크롭 영역 그룹이 없으면 [](shape 0 과 같음 — 빈 시작 판정은 _empty_start),
    이름을 직접 적었는데 없으면 지금처럼 오류(오타 방지)."""
    try:
        return dpsd.read_crop_rects(psd, crop_group)
    except ValueError:
        if not _auto_group(crop_group) or dpsd.find_crop_group(psd) is not None:
            raise
        return []


def _empty_start(root: str, m: dict, crop_group: str) -> str:
    """PSD 모드 Import 의 크롭이 0(크롭 영역 그룹 없음·shape 0)일 때. 빈 시작(D6)은 crop_group auto 이고, 지울 크롭이 없거나
    (첫 Import·빈 프로젝트) layout.json 을 치워 PSD 원천으로 돌아온 매니페스트(source.layout)일 때만 — 반환 = 보고서 줄(되돌림 안내,
    없으면 ""). 그 밖(PSD 모드로 크롭이 있던 프로젝트, 이름을 적은 그룹이 빔)은 ValueError — 매니페스트는 그대로."""
    if not _auto_group(crop_group):
        raise ValueError(f"{_TAG} 크롭 영역 그룹 {crop_group!r} 에 shape 레이어가 없습니다 — {_CROP_GROUP_HINT}")
    if m["source"].get("layout"):
        revs = store._layout_history_revs(os.path.join(root, store.LAYOUT_HISTORY_DIR))
        return (f"{store.LAYOUT_NAME} 가 없어 크롭 원천이 PSD 로 돌아옴(크롭 영역 그룹 없음·shape 0) — "
                + (f"{store.LAYOUT_HISTORY_DIR}/r{revs[-1]}.json 을 {store.LAYOUT_NAME} 으로 복사하면 되돌림"
                   "(이력은 쓰기 직전 판 — 마지막 편집 1회는 빠짐)" if revs
                   else f"되돌릴 {store.LAYOUT_HISTORY_DIR} 없음"))
    if m["crops"]:
        raise ValueError(f"{_TAG} 크롭 영역 그룹을 찾을 수 없습니다(또는 shape 0) — 기존 크롭 {len(m['crops'])}개를 지우지 않으려고 "
                         f"멈춥니다. {_CROP_GROUP_HINT}")
    return ""


def _base_lines(binfo: dict, work_rect: list[int], sec: float) -> list[str]:
    return ([f"PSD 읽기 {sec:.1f}s · 베이스 {binfo['method']} ({binfo.get('base_layer')} + {binfo.get('clean_plate_layer')}) · "
             f"work_rect {work_rect}"] + [f"  - 베이스: {w}" for w in binfo["warnings"]])


def _write_psd_snapshot(root: str, heavy: dict, rects: list[dict], nodes: list[str]) -> dict:
    """PSD 크롭 스냅숏 derived/psd_crops.json = {psd, fingerprint, crop_group, crop_pixels_group, at,
    crops: [{name, layer_name, rect, rect_source}], pixel_nodes: [크롭 이미지 그룹 자식 이름]}. 갈라짐 경고·보드 'PSD 값 가져오기'·
    PSD 를 열지 않는 소스 경로(pixel_nodes)의 원천."""
    snap = {**{k: heavy[k] for k in _SNAPSHOT_KEYS}, "at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "crops": [{"name": store.parse_crop_layer_name(r["name"])["name"], "layer_name": r["name"],
                       "rect": [int(v) for v in r["rect"]], "rect_source": r["rect_source"]} for r in rects],
            "pixel_nodes": nodes}
    store.write_json_atomic(os.path.join(root, store.PSD_SNAPSHOT), snap)
    return snap


def _read_psd_snapshot(root: str, heavy: dict) -> dict | None:
    """스냅숏이 지금 Import 설정(PSD 경로·지문·crop_group·crop_pixels_group)으로 쓴 것이면 그 dict, 아니면(없음·깨짐 포함) None."""
    try:
        with open(os.path.join(root, store.PSD_SNAPSHOT), encoding="utf-8") as f:
            snap = json.load(f)
    except (OSError, ValueError):
        return None
    return snap if all(snap.get(k) == heavy[k] for k in _SNAPSHOT_KEYS) else None


def _psd_drift(lay: dict, snap: dict) -> str:
    """PSD 기준점(layout.psd) 대 스냅숏의 shape 차이(store.psd_diff)를 보고서 한 줄로. 차이가 없으면 ""."""
    d = store.psd_diff(lay["psd"], snap)
    parts = [f"{c['name']} " + ", ".join(f"rect {c['from']['rect']}→{c['to']['rect']}" if f == "rect"
                                          else f"레이어명 {c['from']['layer_name']!r}→{c['to']['layer_name']!r}"
                                          for f in c["fields"]) for c in d["changed"]]
    parts += [f"새 shape {c['name']}" for c in d["added"]] + [f"사라진 shape {c['name']}" for c in d["removed"]]
    return ("주의: PSD 사각형이 이관 뒤 바뀜(반영 안 함 — Studio 의 'PSD 값 가져오기'로): " + " · ".join(parts)) if parts else ""


def _overview(base: np.ndarray, crops: list[dict], max_side: int = 1600) -> np.ndarray:
    H, W = base.shape[:2]
    s = min(1.0, max_side / max(W, H))
    im = Image.fromarray(base[..., :3]).resize((max(1, round(W * s)), max(1, round(H * s))), Image.LANCZOS)
    d = ImageDraw.Draw(im)
    font = _font(16)
    for i, c in enumerate(crops):
        x0, y0, x1, y1 = (round(v * s) for v in c["rect"])
        col = (255, 150, 0) if c.get("alpha_frac", 0) > 0 else ((255, 60, 60) if c.get("warnings") else (40, 230, 90))
        # 축소하면 1px 보다 얇아지는 사각형도 그린다(Pillow 는 x1 < x0 · y1 < y0 이면 예외 — Import PSD 전체가 실패함)
        d.rectangle([x0, y0, max(x0, x1 - 1), max(y0, y1 - 1)], outline=col, width=3)
        tag = f"{i + 1}:{c.get('nnn') or '?'}"
        tw = d.textlength(tag, font=font)
        d.rectangle([x0, y0, x0 + tw + 6, y0 + 19], fill=col)
        d.text((x0 + 3, y0 + 1), tag, fill=(0, 0, 0), font=font)
    return np.asarray(im)


class BMKDesignPatchImportPSD:
    """PSD 의 크롭 rect·베이스·크롭 소스를 읽어 매니페스트 crops[] 를 만든다."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "project": (PROJECT_TYPE, {"tooltip": "BMK Design Patch Project 출력."}),
                "psd_path": ("STRING", {"default": "", "tooltip": "크롭 영역(Rectangle)을 그린 PSD 경로. 따옴표는 무시."}),
                "crop_group": ("STRING", {"default": "auto",
                                          "tooltip": "크롭 shape 레이어 그룹. auto = '02.크롭영역' 또는 '크롭영역'."}),
                "clean_plate_layer": ("STRING", {"default": "auto",
                                                 "tooltip": "클린플레이트 픽셀 레이어. auto = 'non-gpt 1차수정' / '00.Base_마스크용', none = 안 씀."}),
                "base_layer": ("STRING", {"default": "auto",
                                          "tooltip": "베이스 레이어. auto = 'I2I_base'(없으면 아래와 같음), "
                                                     "none = 보이는 레이어 합성(shape·크롭 그룹 제외, 여백도 불투명)."}),
                "crop_pixels_group": ("STRING", {"default": "auto",
                                                 "tooltip": "크롭별 사전 편집 그룹(같은 이름 하위 그룹/레이어를 크롭 소스에 합성). "
                                                            "auto = '크롭이미지' / '01.크롭', none = 베이스만."}),
                "refs_dir": ("STRING", {"default": "", "tooltip": "레퍼런스 폴더(NNN_ 로 시작하는 이미지를 크롭에 자동 매칭). 비우면 안 씀."}),
                "glossary_path": ("STRING", {"default": "",
                                             "tooltip": "한→영 용어집 JSON(선택). 프로젝트 폴더의 glossary.json 위에 덮어씁니다."}),
            },
        }

    RETURN_TYPES = (PROJECT_TYPE, "IMAGE", "STRING")
    RETURN_NAMES = ("project", "overview", "report")
    OUTPUT_TOOLTIPS = ("갱신된 프로젝트 핸들.", "캔버스 + 번호 박스(초록 정상, 주황 투명 여백, 빨강 경고).", "크롭 목록과 경고.")
    FUNCTION = "run"
    CATEGORY = _CATEGORY
    DESCRIPTION = (
        "PSD 를 psd_tools 로 직접 읽어 크롭 rect(shape 벡터), 베이스(I2I_base + 클린플레이트), 크롭 그룹 사전 편집을 합성한 "
        "크롭 소스를 매니페스트에 기록합니다. 레이어명 'NNN_부위 | A: …; B: …' 로 타깃을, refs_dir 에서 레퍼런스를 매칭합니다. "
        "specs/<crop_id>.json 의 _layer_targets(Spec Draft 채택)는 레이어명에 적은 것처럼 타깃에 더합니다(PSD 문구가 있으면 PSD 우선). "
        "프로젝트 폴더에 layout.json(Studio 레이아웃 편집)이 있으면 크롭 rect·이름·대상·ID 는 그것을 쓰고, PSD 는 베이스·사전 편집에만 "
        "씁니다(PSD 사각형이 달라지면 경고만)."
    )
    SEARCH_ALIASES = _ALIASES + ["import psd", "crop rect", "psd 가져오기", "크롭 영역", "클린플레이트"]

    def run(self, project, psd_path, crop_group, clean_plate_layer, base_layer, crop_pixels_group, refs_dir,
            glossary_path):
        t0 = time.time()
        root, m = _open(project)
        before = _snapshot(m)
        old = {c["id"]: c for c in m["crops"]}  # 이번 실행 전 크롭(_import 가 crops 를 바꾸기 전)
        psd_path = _strip_path(psd_path)
        refs_dir = _strip_path(refs_dir)
        glossary_path = _strip_path(glossary_path)
        if not os.path.isfile(psd_path):
            raise ValueError(f"{_TAG} PSD 파일을 찾을 수 없습니다: {psd_path!r}")
        if refs_dir and not os.path.isdir(refs_dir):
            raise ValueError(f"{_TAG} 레퍼런스 폴더가 없습니다: {refs_dir!r}")
        if glossary_path and not os.path.isfile(glossary_path):
            raise ValueError(f"{_TAG} 용어집 파일이 없습니다: {glossary_path!r}")
        try:
            lay = store.load_layout(root)  # 깨진 layout.json 은 오류(조용히 PSD 로 돌아가면 크롭 원천이 뒤집힘)
            rdoc = refs.load_refs(root)  # 깨진 refs.json 도 오류(조용히 이름 매칭으로 돌아가면 레퍼런스 원천이 뒤집힘)
        except ValueError as e:
            raise ValueError(f"{_TAG} {e}") from e
        heavy = {"psd": psd_path.replace("\\", "/"), "fingerprint": store.file_fingerprint(psd_path),
                 "crop_group": crop_group, "clean_plate_layer": clean_plate_layer, "base_layer": base_layer,
                 "crop_pixels_group": crop_pixels_group}
        src = m["source"]
        if lay is None:  # 크롭 원천 = PSD shape. 레이아웃 모드였던 매니페스트(source.layout)는 다시 읽는다
            cached = (all(src.get(k) == v for k, v in heavy.items()) and not src.get("layout") and m["base"]
                      and os.path.isfile(_abs(root, m["base"]))
                      and all(os.path.isfile(_abs(root, c["source"])) for c in m["crops"]))
            info_line = "캐시 적중(PSD·그룹 설정 같음, 다시 읽지 않음)"
            if not cached:
                info_line = self._import(root, m, psd_path, heavy)
        else:
            info_line, cached = self._import_layout(root, m, psd_path, heavy, lay, old)
        notes = self._targets(root, m, old, cached)
        light = {"refs_dir": refs_dir.replace("\\", "/"), "refs_fingerprint": store._dir_fp(refs_dir),
                 "glossary": glossary_path.replace("\\", "/"), "glossary_fingerprint": store._file_fp(glossary_path)}
        if rdoc is not None:  # refs.json 이 없으면 키를 만들지 않는다(기존 매니페스트 바이트 그대로)
            light["refs_links"] = refs.refs_sig(rdoc, refs_dir)
        again = (not cached or ("refs_links" in src) != ("refs_links" in light)
                 or any(src.get(k) != v for k, v in light.items()))
        resolved = {c["id"]: refs.resolve_refs(refs_dir, c, rdoc) for c in m["crops"]} if again or rdoc is not None else {}
        if again:
            for c in m["crops"]:
                r = resolved[c["id"]]
                c["refs_auto"] = r["refs_auto"]
                if r["refs_main"] is None:
                    c.pop("refs_main", None)
                else:
                    c["refs_main"] = r["refs_main"]
            m["source"].update(light)
            if rdoc is None:
                m["source"].pop("refs_links", None)
        if rdoc is not None:
            info_line += "\n" + self._refs_line(rdoc, refs_dir, m, resolved)
        linked = {cid for cid, r in resolved.items() if r["linked"]}
        ref_notes = {cid: resolved[cid]["notes"] for cid in linked if resolved[cid]["notes"]}
        late = lay is None and os.path.isfile(os.path.join(root, store.LAYOUT_NAME))  # PSD 모드 Import 중 보드 '편집 시작'
        handle, conflicts = _commit(root, m, before, project)
        base = _load_rgb(root, m["base"])
        report = "\n".join([self._report(m, psd_path, info_line, time.time() - t0, notes, linked, ref_notes)]
                           + ([_LATE_LAYOUT] if late else []) + [f"  - {w}" for w in conflicts])
        logger.info("%s %s", _TAG, report.splitlines()[0])
        return (handle, _to_image(_overview(base, m["crops"])), report)

    @staticmethod
    def _import(root: str, m: dict, psd_path: str, heavy: dict) -> str:
        t0 = time.time()
        psd = dpsd.open_psd(psd_path)
        rects = _psd_rects(psd, heavy["crop_group"])
        empty_note = "" if rects else _empty_start(root, m, heavy["crop_group"])  # 빈 시작(D6): 크롭 0, 베이스만
        pbar = comfy.utils.ProgressBar(len(rects) + 2)
        base_rgb, binfo = dpsd.read_base(psd, heavy["clean_plate_layer"], heavy["base_layer"])
        pbar.update(1)
        parsed = [store.parse_crop_layer_name(r["name"]) for r in rects]
        named = [{"name": p["name"], "rect": r["rect"]} for p, r in zip(parsed, rects)]
        srcs = dpsd.crop_sources(psd, named, base_rgb, heavy["crop_pixels_group"], work_rect=binfo["work_rect"])
        pbar.update(1)
        ids = store.assign_crop_ids([p["name"] for p in parsed])
        rect_count: dict[tuple, int] = {}
        for r in rects:
            rect_count[tuple(r["rect"])] = rect_count.get(tuple(r["rect"]), 0) + 1
        name_count: dict[str, int] = {}
        for p in parsed:
            name_count[p["name"]] = name_count.get(p["name"], 0) + 1
        crops = []
        for cid, p, r, s in zip(ids, parsed, rects, srcs):
            warns = list(r["warnings"]) + list(p["warnings"]) + _source_warnings(s)
            if rect_count[tuple(r["rect"])] > 1:
                warns.append(_SAME_RECT)
            if name_count[p["name"]] > 1:
                warns.append(_SAME_NAME)
            crops.append({
                "id": cid, "name": p["name"], "nnn": p["nnn"], "part": p["part"], "layer_name": r["name"],
                "rect": [int(v) for v in r["rect"]], "rect_source": r["rect_source"],
                "source": store.save_png_ca(root, "inputs", s["rgba"]), "source_kind": s["kind"],
                "alpha_frac": round(float(s["alpha_frac"]), 6),
                "targets": [], "refs_auto": [], "warnings": warns,  # targets 는 _targets(채택과 합쳐 한 번)
            })
            pbar.update(1)
        m["crops"] = _plain(crops)
        m["canvas"] = list(psd.size)
        m["work_rect"] = [int(v) for v in binfo["work_rect"]]
        m["source"].update(heavy)
        m["source"].pop("layout", None)  # layout.json 을 치운 프로젝트: 크롭 원천이 PSD 로 돌아옴
        m["base"] = store.save_png_ca(root, "inputs", base_rgb)
        snap_path = os.path.join(root, store.PSD_SNAPSHOT)
        if os.path.isfile(snap_path):  # 레이아웃 모드 때 쓴 스냅숏 — PSD 모드 결과와 맞지 않는 옛 기준을 남기지 않는다
            os.remove(snap_path)
        return "\n".join(_base_lines(binfo, m["work_rect"], time.time() - t0) + ([empty_note] if empty_note else []))

    @staticmethod
    def _import_layout(root: str, m: dict, psd_path: str, heavy: dict, lay: dict, old: dict) -> tuple[str, bool]:
        """레이아웃 모드(layout.json 있음, S1 §2.2): 크롭의 rect·이름·대상·ID = layout.json(assign_crop_ids 를 부르지 않음),
        PSD = 베이스·work_rect·사전 편집 그룹(찾는 이름 = psd_name, 없으면 name). 매니페스트 크롭에 psd_name(찾은 이름)을 남긴다.
        소스는 바뀐 크롭(새 크롭·rect·psd_name·소스 파일 없음)만 다시 만든다: PSD 지문·무거운 설정이 같고 믿을 수 있는 스냅숏이
        그 이름을 사전 편집 자식(pixel_nodes)으로 보지 않으면 PSD 를 열지 않고 매니페스트 베이스 PNG + work_rect 로
        (dpsd.base_crop_sources — crop_sources 와 같은 바이트), 아니면 PSD 를 한 번 열어 바뀐 크롭 전부. PSD 지문·무거운 설정이
        바뀌었으면 전부(베이스 포함). PSD 를 열면 스냅숏(derived/psd_crops.json)을 쓰고, 믿을 수 있는 스냅숏이 PSD 기준점
        (layout.psd)과 다르면 경고 줄만 낸다(크롭은 레이아웃 그대로). 기준점의 경로·지문이 지금 PSD 와 다른데 믿을 스냅숏이 없으면
        바뀐 크롭이 없어도 한 번 연다(갈라짐 확인). 크롭 영역 그룹을 못 읽으면 스냅숏을 쓰지 않고 경고 한 줄만.
        사전 편집 자식 이름(psd_name)을 여러 크롭이 쓰면 그 크롭들에 경고. 매니페스트 source.layout = {rev, sig}(이번에 읽은 판).
        반환 (보고서 둘째 줄부터, 캐시 적중 = 무거운 설정·source.layout.sig 같음·파일 있음 — _targets·refs 재계산 여부)."""
        t0 = time.time()
        src = m["source"]
        sig = store.layout_sig(lay)
        same_psd = (all(src.get(k) == v for k, v in heavy.items()) and bool(m["base"])
                    and os.path.isfile(_abs(root, m["base"])))
        rows = []  # (레이아웃 크롭, 파싱, 사전 편집에서 찾을 이름, 이번 실행 전 매니페스트 크롭, 소스를 그대로 씀)
        for lc in lay["crops"]:
            pname = lc.get("psd_name") or lc["name"]
            o = old.get(lc["id"])
            keep = (same_psd and o is not None and o["rect"] == lc["rect"] and o.get("psd_name", o["name"]) == pname
                    and os.path.isfile(_abs(root, o["source"])))
            rows.append((lc, store.layout_parsed(lc), pname, o, keep))
        n_changed = len(old.keys() - {lc["id"] for lc in lay["crops"]}) + sum(
            1 for lc, p, pname, o, _k in rows
            if o is None or (o["name"], o["rect"], o.get("psd_name", o["name"])) != (p["name"], lc["rect"], pname)
            or store.parse_crop_layer_name(o["layer_name"])["targets"] != p["targets"])
        cached = (same_psd and (src.get("layout") or {}).get("sig") == sig and all(r[4] for r in rows)
                  and [c["id"] for c in m["crops"]] == [lc["id"] for lc in lay["crops"]])
        need = [] if cached else [r for r in rows if not r[4]]
        snap = _read_psd_snapshot(root, heavy) if same_psd else None
        pixel_nodes = snap["pixel_nodes"] if snap else []
        # 이관 기준점이 이 PSD 가 아님(PSD 모드 Import 중 '편집 시작'·낡은 기준점)인데 믿을 스냅숏이 없음 → 한 번 열어 갈라짐을 본다
        unseen = snap is None and (lay["psd"].get("path"), lay["psd"].get("fingerprint")) != (heavy["psd"], heavy["fingerprint"])
        opened = not same_psd or unseen or (bool(need) and (snap is None or any(r[2] in pixel_nodes for r in need)))
        psd = dpsd.open_psd(psd_path) if opened else None
        canvas = list(psd.size) if opened else m["canvas"]
        if lay["canvas"] != canvas:
            raise ValueError(f"{_TAG} {store.LAYOUT_NAME} 캔버스 {lay['canvas'][0]}x{lay['canvas'][1]} 가 PSD {canvas[0]}x{canvas[1]} 와 "
                             f"다릅니다 — 같은 캔버스의 PSD 를 고르거나 {store.LAYOUT_NAME} 를 치우세요(그러면 PSD 크롭으로 가져옴)")
        drift = ""
        if opened:
            pbar = comfy.utils.ProgressBar(2)
            if same_psd:  # 베이스는 그대로 — 사전 편집 자식인 바뀐 크롭·갈라짐 확인 때문에 연다
                base_rgb, work_rect = _load_rgb(root, m["base"]), m["work_rect"]
            else:
                base_rgb, binfo = dpsd.read_base(psd, heavy["clean_plate_layer"], heavy["base_layer"])
                work_rect = [int(v) for v in binfo["work_rect"]]
                need = rows
            pbar.update(1)
            pixel_nodes = dpsd.crop_pixel_nodes(psd, heavy["crop_pixels_group"])
            try:
                snap = _write_psd_snapshot(root, heavy, _psd_rects(psd, heavy["crop_group"]), pixel_nodes)
            except ValueError as e:  # 크롭 정의에 더는 쓰지 않는 그룹 — 갈라짐만 못 본다. 옛 스냅숏은 그대로(지문이 다르면 믿지 않음)
                snap, drift = None, f"갈라짐 확인 못 함(크롭 영역 그룹): {e}"
            srcs = dpsd.crop_sources(psd, [{"name": r[2], "rect": r[0]["rect"]} for r in need], base_rgb,
                                     heavy["crop_pixels_group"], work_rect=work_rect)
            pbar.update(1)
            if same_psd:
                psd_lines = [f"PSD 열기 {time.time() - t0:.1f}s("
                             + ("이관 기준점과 다른 PSD 의 갈라짐 확인" if unseen else "바뀐 크롭의 사전 편집 합성")
                             + " — 베이스는 다시 읽지 않음)"]
            else:
                m["canvas"], m["work_rect"] = canvas, work_rect
                m["base"] = store.save_png_ca(root, "inputs", base_rgb)
                src.update(heavy)
                psd_lines = _base_lines(binfo, work_rect, time.time() - t0)
        else:
            srcs = dpsd.base_crop_sources(_load_rgb(root, m["base"]), [{"name": r[2], "rect": r[0]["rect"]} for r in need],
                                          m["work_rect"]) if need else []
            psd_lines = ["캐시 적중(PSD·그룹 설정·레이아웃 같음, 다시 읽지 않음)" if cached
                         else "PSD 안 엶(지문·그룹 설정 같음" + (" — 바뀐 크롭은 베이스 PNG 에서 자름)" if need else ")")]
        if not cached:
            made = {r[0]["id"]: s for r, s in zip(need, srcs)}
            rects = [tuple(lc["rect"]) for lc in lay["crops"]]
            names = [p["name"].casefold() for _lc, p, *_r in rows]
            by_pname: dict[str, list[str]] = {}
            for _lc, p, pname, *_r in rows:
                by_pname.setdefault(pname, []).append(p["name"])
            crops = []
            for lc, p, pname, o, _k in rows:
                lw = list(p["warnings"])
                if rects.count(tuple(lc["rect"])) > 1:
                    lw.append("다른 크롭과 rect 가 같음(대상을 한 크롭에 합치는 것을 권장)")
                if names.count(p["name"].casefold()) > 1:
                    lw.append("같은 이름의 크롭이 여러 개(Studio 에서 이름을 바꾸세요)")
                if len(by_pname[pname]) > 1 and pname in pixel_nodes:  # 이름 바꾼 크롭의 psd_name 과 같은 이름의 새 크롭 등
                    lw.append(f"사전 편집 그룹 {pname} 을 크롭 {'·'.join(by_pname[pname])} 가 함께 씀")
                s = made.get(lc["id"])
                if s is None:
                    source, kind, frac, sw = o["source"], o["source_kind"], o["alpha_frac"], _kept_source_warnings(o)
                else:
                    source, kind, frac = store.save_png_ca(root, "inputs", s["rgba"]), s["kind"], round(float(s["alpha_frac"]), 6)
                    sw = _source_warnings(s)
                crops.append({
                    "id": lc["id"], "name": p["name"], "nnn": p["nnn"], "part": p["part"],
                    "layer_name": store.layout_layer_name(lc), "psd_name": pname, "rect": list(lc["rect"]),
                    "rect_source": "layout", "source": source, "source_kind": kind, "alpha_frac": frac,
                    "targets": [], "refs_auto": [], "warnings": [_LAYOUT_WARN + w for w in lw] + sw,  # targets 는 _targets
                })
            m["crops"] = _plain(crops)
        src["layout"] = {"rev": lay["rev"], "sig": sig}
        lines = [f"레이아웃 rev {lay['rev']} · 크롭 {len(rows)} · 바뀜 {n_changed}"
                 f"(소스 다시 만듦 {len(need)}, {'PSD 엶' if opened else 'PSD 안 엶'})"] + psd_lines
        renamed = [lc for lc in lay["crops"] if lc["id"].split("-")[0] != store.crop_id_for(lc["name"])]
        if renamed:
            lines.append("ID 는 처음 이름 기준(이름을 바꿔도 유지): " + ", ".join(f"{lc['id']} = {lc['name']}" for lc in renamed))
            by_file = sorted({lc["name"] for lc in renamed for c in m["candidates"]
                              if c["crop_id"] == lc["id"] and c.get("origin") in ("folder", "psd_harvest")})
            if by_file:  # 폴더·PSD 수확은 파일·그룹 이름 앞부분 = 크롭 이름으로 찾는다(STUDIO_S1_SPEC §5)
                lines.append(f"주의: 이름을 바꾼 크롭에 폴더·PSD 수확 후보가 있음({', '.join(by_file)}) — 옛 이름 파일은 "
                             "mapping_json 에 ID 로 지정")
        if snap is not None:
            drift = _psd_drift(lay, snap)
        if drift:
            lines.append(drift)
        return "\n".join(lines), cached

    @staticmethod
    def _targets(root: str, m: dict, old: dict, cached: bool) -> dict[str, list[str]]:
        """크롭 타깃 = 레이어명 + specs/<crop_id>.json 의 _layer_targets(채택)를 먼저 합친 뒤 한 번만 _new_targets
        (old = 이번 실행 전 크롭 — 다시 가져와도 채택 타깃의 사람 name_en·mode·refs 를 잇는다). 캐시 경로는 _layer_targets 가
        있거나 채택 타깃이 있는 크롭만 다시 계산(그 밖은 손대지 않음 — rev·cell_key 불변). 스펙을 못 읽거나 문법 오류면 캐시
        경로는 지금 타깃 유지, 다시 가져오기는 레이어명 타깃만 — Import 는 실패하지 않는다.
        반환 {crop_id: 보고서 줄}. crop.warnings 에 넣지 않는다(매 실행 다시 계산 — 저장하면 rev 가 흔들림)."""
        notes: dict[str, list[str]] = {}
        for c in m["crops"]:
            prev = (old.get(c["id"]) or {}).get("targets") or []  # 캐시 경로에서는 c 자신 — 바꾸기 전 목록을 잡아 둔다
            p = store.parse_crop_layer_name(c["layer_name"])
            try:
                ov = store.load_spec_override(root, c["id"])
                if cached and "_layer_targets" not in ov and not any(t.get("origin") == "adopt" for t in prev):
                    continue
                merged, msgs = store.merge_adopted(c, p, ov)
            except ValueError as e:
                if cached:
                    notes[c["id"]] = [f"지금 타깃 유지 — {e}"]
                    continue
                merged, msgs = p, [f"레이어명 타깃만 — {e}"]
            c["targets"] = _new_targets(merged, old.get(c["id"]))
            msgs += _adopt_changes(m, c, prev)
            if msgs:
                notes[c["id"]] = msgs
        return notes

    @staticmethod
    def _refs_line(rdoc: dict, refs_dir: str, m: dict, resolved: dict[str, dict]) -> str:
        """refs.json 이 있을 때의 보고서 줄: 연결 rev·고정 크롭 수·크롭이 사라진 연결(R-orphan), refs_dir 가 다르면 '연결 무시' 주의."""
        if not refs.same_refs_dir(rdoc["refs_dir"], refs_dir):
            return (f"주의: {refs.REFS_NAME} 의 refs_dir({rdoc['refs_dir']})가 지금 refs_dir({refs_dir or '비어 있음'})와 달라 "
                    "레퍼런스 연결을 무시하고 이름으로 찾음")
        known = {c["id"] for c in m["crops"]}
        orphans = sorted(cid for cid in rdoc["links"] if cid not in known)
        return (f"레퍼런스 연결 rev {rdoc['rev']} · 고정 {sum(r['linked'] for r in resolved.values())}/{len(m['crops'])} 크롭"
                + (f" · 크롭이 사라진 연결 {len(orphans)}({', '.join(orphans)}) — Studio 에서 다른 크롭에 다시 연결" if orphans else ""))

    @staticmethod
    def _report(m: dict, psd_path: str, info_line: str, sec: float, notes: dict[str, list[str]], linked: set[str],
                ref_notes: dict[str, list[str]]) -> str:
        n_warn = sum(1 for c in m["crops"] if c["warnings"])
        n_note = sum(len(v) for v in notes.values())
        lines = [f"Import PSD: {os.path.basename(psd_path)} {m['canvas'][0]}x{m['canvas'][1]} | 크롭 {len(m['crops'])}"
                 f" (경고 {n_warn}{f' · 채택 {n_note}' if n_note else ''}) | rev {m['rev']} | {sec:.1f}s", info_line]
        layout = bool(m["source"].get("layout"))
        if not m["crops"]:
            lines.append("크롭 없음 — Studio 레이아웃 편집에서 크롭을 그리세요" if layout
                         else "크롭 없음(크롭 영역 그룹 없음) — Studio 에서 '레이아웃 편집 시작'")
        known = {c["id"] for c in m["crops"]}
        orphans = sorted({c["crop_id"] for c in m["candidates"] if c["crop_id"] not in known})
        if orphans:
            lines.append(f"주의: 매니페스트 후보가 가리키는 크롭이 {'레이아웃에서' if layout else 'PSD 에서'} 사라짐: {', '.join(orphans)}")
        for i, c in enumerate(m["crops"]):
            x0, y0, x1, y1 = c["rect"]
            tg = "; ".join(f"{t['tid']}: {t['name_en'] or t['label']}" + (" (채택)" if t.get("origin") == "adopt" else "")
                           for t in c["targets"])
            lines.append(f"{i + 1:2d}. {c['id']} {c['name']} {x1 - x0}x{y1 - y0} [{c['source_kind']}] "
                         f"refs {len(c['refs_auto'])}{'(연결)' if c['id'] in linked else ''} | {tg}")
            for wmsg in c["warnings"]:
                lines.append(f"      - {wmsg}")
            for wmsg in notes.get(c["id"], []):
                lines.append(f"      - 채택: {wmsg}")
            for wmsg in ref_notes.get(c["id"], []):
                lines.append(f"      - 레퍼런스: {wmsg}")
        return "\n".join(lines)

    @classmethod
    def IS_CHANGED(cls, psd_path="", refs_dir="", glossary_path="", **kwargs):
        if psd_path is None or refs_dir is None or glossary_path is None:  # 링크된 경로: 파일 지문을 볼 수 없음
            return float("nan")
        psd_path, refs_dir, glossary_path = (_strip_path(v) for v in (psd_path, refs_dir, glossary_path))
        vals = {k: kwargs.get(k) for k in ("crop_group", "clean_plate_layer", "base_layer", "crop_pixels_group")}
        return json.dumps([psd_path, store._file_fp(psd_path), refs_dir, store._dir_fp(refs_dir), glossary_path,
                           store._file_fp(glossary_path), vals], ensure_ascii=False)


# ══════════════════════════════════════════════════════════════════════
# 노드 3 — Candidate In
# ══════════════════════════════════════════════════════════════════════
def _parse_mapping(text: str) -> dict:
    """mapping_json: {"<레이어명/파일명>": {"crop": "<크롭 이름|ID>", "tid": "B", "skip": true}} 또는 {"이름": "B"}."""
    text = (text or "").strip()
    if not text:
        return {}
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"{_TAG} mapping_json 형식 오류: {e}") from e
    if not isinstance(raw, dict):
        raise ValueError(f"{_TAG} mapping_json 은 객체여야 합니다")
    out = {}
    for k, v in raw.items():
        if isinstance(v, str):
            v = {"tid": v}
        if not isinstance(v, dict):
            raise ValueError(f"{_TAG} mapping_json 값은 객체 또는 타깃 문자열이어야 합니다: {k!r}")
        out[unicodedata.normalize("NFC", str(k))] = v
    return out


def _resolve_crop(m: dict, ref: str) -> dict | None:
    for c in m["crops"]:
        if ref in (c["id"], c["name"]):
            return c
    return None


def _crop_for_filename(stem: str, crops: list[dict]) -> tuple[dict | None, str]:
    """파일명 앞부분 = 크롭 이름(가장 긴 일치) → 아니면 NNN 이 유일한 크롭. 같은 이름의 크롭이 여럿이면 모호함."""
    s = unicodedata.normalize("NFC", stem).casefold()
    best = None
    for c in crops:
        nm = unicodedata.normalize("NFC", c["name"]).casefold()
        if s.startswith(nm) and (best is None or len(nm) > len(best[1])):
            best = (c, nm)
    if best:
        n_same = sum(1 for c in crops if unicodedata.normalize("NFC", c["name"]).casefold() == best[1])
        if n_same > 1:
            return None, f"크롭 이름 {best[0]['name']} 이 {n_same}개라 모호함 - mapping_json 에 크롭 ID 로 지정"
        return best[0], "name"
    mt = re.match(r"^(\d+)_", stem)
    if mt:
        same = [c for c in crops if c["nnn"] == mt.group(1)]
        if len(same) == 1:
            return same[0], "nnn"
        if len(same) > 1:
            return None, f"NNN {mt.group(1)} 크롭이 {len(same)}개라 모호함"
    return None, "파일명이 크롭 이름/NNN 으로 시작하지 않음"


class BMKDesignPatchCandidateIn:
    """후보 등록: PSD 수확(SO 원본 bytes·quad·손 마스크) 또는 폴더 이미지."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "project": (PROJECT_TYPE, {"tooltip": "BMK Design Patch Project 출력(Import PSD 이후)."}),
                "source": (_SOURCES, {"default": "psd_harvest",
                                      "tooltip": "psd_harvest = 마감한 PSD 의 결과 그룹 SO 를 수확. folder = 폴더의 이미지."}),
                "path": ("STRING", {"default": "", "tooltip": "psd_harvest 면 PSD 경로, folder 면 폴더 경로."}),
                "group": ("STRING", {"default": "03.수정", "tooltip": "psd_harvest: 결과 그룹 이름(비우면 문서 전체)."}),
                "mapping_json": ("STRING", {"default": "", "multiline": True,
                                            "tooltip": '선택. {"레이어명/파일명": {"crop": "크롭 이름|ID", "tid": "B", "skip": true}} '
                                                       '또는 {"이름": "B"}(타깃만). 자동 매칭을 덮어씁니다.'}),
                "visible_is_pick": ("BOOLEAN", {"default": True,
                                                "tooltip": "psd_harvest: 보이는 SO 를 pick, 숨김을 대안으로. 끄면 기존 pick 을 유지."}),
                "keep_hand_masks": ("BOOLEAN", {"default": True,
                                                "tooltip": "psd_harvest: 레이어(또는 부모 그룹) 마스크를 손 마스크로 저장."}),
            },
        }

    RETURN_TYPES = (PROJECT_TYPE, "STRING")
    RETURN_NAMES = ("project", "report")
    FUNCTION = "run"
    CATEGORY = _CATEGORY
    DESCRIPTION = (
        "후보를 매니페스트에 등록합니다. psd_harvest 는 마감한 PSD 의 결과 그룹 SO 에서 원본 bytes·배치(quad)·마스크·보임 여부를, "
        "folder 는 폴더 이미지(파일명 = 크롭 이름으로 시작)를 읽어 원본 그대로 cands/ 에 저장합니다."
    )
    SEARCH_ALIASES = _ALIASES + ["candidate in", "harvest", "후보 등록", "수확", "결과 가져오기"]

    def run(self, project, source, path, group, mapping_json, visible_is_pick, keep_hand_masks):
        t0 = time.time()
        root, m = _open(project)
        before = _snapshot(m)
        if not m["base"]:
            raise ValueError(f"{_TAG} 크롭이 없습니다 - BMK Design Patch Import PSD 를 먼저 실행하세요")
        if not m["crops"]:  # 빈 시작(D6): 예외 대신 경고 — Review 까지 돌아 Studio 키가 생기게
            report = "Candidate In: 크롭 없음 — 등록하지 않음(Studio 레이아웃 편집에서 크롭을 그린 뒤 다시 큐)"
            logger.info("%s %s", _TAG, report)
            return (_handle(root, m, project), report)
        if source not in _SOURCES:
            raise ValueError(f"{_TAG} source 는 {_SOURCES} 중 하나여야 합니다: {source!r}")
        path = _strip_path(path)
        mapping = _parse_mapping(mapping_json)
        warns: list[str] = []
        if source == "psd_harvest":
            new = self._harvest(root, m, path, group, mapping, bool(keep_hand_masks), warns)
        else:
            new = self._folder(root, m, path, mapping, warns)
        old = {c["key"]: c for c in m["candidates"]}
        seen = {c["key"] for c in new}
        origin_key = "psd" if source == "psd_harvest" else "folder"
        removed = [k for k, c in old.items() if k not in seen and c.get("origin") == source
                   and (c.get("origin_info") or {}).get(origin_key) == path]  # 같은 출처에서 사라진 후보
        added = updated = 0
        merged = [c for c in m["candidates"] if c["key"] not in seen and c["key"] not in removed]
        for c in new:
            prev = old.get(c["key"])
            if source == "folder" or not visible_is_pick:
                c["picked"] = bool(prev.get("picked")) if prev else False
            if prev is None:
                added += 1
            elif _snapshot(prev) != _snapshot(c):
                updated += 1
            merged.append(c)
        m["candidates"] = _plain(merged)
        for k in removed:
            m["analysis"].pop(k, None)
            (m.get("mask_edits") or {}).pop(k, None)  # S4 마스크 편집도(탈락·★ 해제는 기록을 둠 — 사라진 후보만)
        if source == "folder":
            self._autopick(m, {c["key"] for c in new}, warns)
        _sync_picks(m)
        handle, conflicts = _commit(root, m, before, project)
        per: dict[str, list[int]] = {}
        for c in m["candidates"]:
            row = per.setdefault(c["crop_id"], [0, 0])
            row[0] += 1
            row[1] += bool(c.get("picked"))
        names = {c["id"]: c["name"] for c in m["crops"]}
        lines = [f"Candidate In ({source}): {os.path.basename(path)} 항목 {len(new)} → 신규 {added} · 갱신 {updated} · "
                 f"제거 {len(removed)} | 전체 후보 {len(m['candidates'])}, pick {sum(len(v) for v in m['picks'].values())} "
                 f"| rev {m['rev']} | {time.time() - t0:.1f}s"]
        for cid, (n, p) in per.items():
            lines.append(f"  {cid} {names.get(cid, '(크롭 없음)')}: 후보 {n}, pick {p}")
        lines += [f"  - {w}" for w in warns + conflicts]
        report = "\n".join(lines)
        logger.info("%s %s", _TAG, lines[0])
        return (handle, report)

    @staticmethod
    def _harvest(root, m, path, group, mapping, keep_masks, warns) -> list[dict]:
        if not os.path.isfile(path):
            raise ValueError(f"{_TAG} PSD 파일을 찾을 수 없습니다: {path!r}")
        psd = dpsd.open_psd(path)
        crops = [{"name": c["name"], "rect": c["rect"]} for c in m["crops"]]  # crop_index = m["crops"] 위치
        hv = dpsd.harvest_results(psd, (group or "").strip() or None, crops, warnings=warns)
        pbar = comfy.utils.ProgressBar(max(1, len(hv)))
        out, keys = [], set()
        for h in hv:
            pbar.update(1)
            mp = mapping.get(unicodedata.normalize("NFC", h["layer"]), {})
            if mp.get("skip"):
                continue
            if mp.get("crop"):
                crop = _resolve_crop(m, mp["crop"])
            else:
                crop = None if h["crop_index"] is None else m["crops"][h["crop_index"]]
            if crop is None:
                warns.append(f"{h['layer']}: 크롭을 찾지 못해 건너뜀 (mapping_json 으로 지정)")
                continue
            tids = [t["tid"] for t in crop["targets"]]
            tid = str(mp.get("tid") or tids[0]).upper()
            if tid not in tids:
                warns.append(f"{h['layer']}: 타깃 {tid} 가 {crop['name']} 에 없음 → {tids[0]}")
                tid = tids[0]
            rel = store.save_bytes_ca(root, "cands", h["data"], h["filetype"], prefix="h_")
            key = os.path.splitext(os.path.basename(rel))[0]
            n = 2
            while key in keys:  # 같은 임베드 원본을 쓰는 SO 레이어가 둘 이상
                key = f"{os.path.splitext(os.path.basename(rel))[0]}-{n}"
                n += 1
            keys.add(key)
            hand = None
            if keep_masks and h["mask"] is not None:
                hand = {"file": store.save_png_ca(root, "masks", h["mask"]["arr"], prefix=f"{key}_hand_"),
                        "rect": h["mask"]["rect"], "owner": h["mask"]["owner"], "bg": h["mask"]["bg"]}
            for wmsg in h["warnings"]:
                if "crop 매칭" not in wmsg:
                    warns.append(f"{h['layer']}: {wmsg}")
            out.append({
                "key": key, "crop_id": crop["id"], "tid": tid, "file": rel, "src_size": h["src_size"],
                "origin": "psd_harvest",
                "origin_info": {"psd": path, "layer": h["layer"], "group_path": h["group_path"],
                                "filetype": h["filetype_orig"], "category": h["category"], "z_index": h["z_index"],
                                "visible": h["visible_effective"], "crop_match": h["crop_match"]},
                "quad": h["quad"], "picked": bool(h["visible_effective"]), "z": h["z_index"], "hand_mask": hand,
            })
        return out

    @staticmethod
    def _folder(root, m, path, mapping, warns) -> list[dict]:
        if not os.path.isdir(path):
            raise ValueError(f"{_TAG} 폴더를 찾을 수 없습니다: {path!r}")
        files = sorted(f for f in os.listdir(path)
                       if os.path.isfile(os.path.join(path, f)) and os.path.splitext(f)[1].lower() in store.REF_EXTS)
        pbar = comfy.utils.ProgressBar(max(1, len(files)))
        out, keys = [], set()
        for fn in files:
            pbar.update(1)
            nfn = unicodedata.normalize("NFC", fn)
            stem = os.path.splitext(nfn)[0]
            mp = mapping.get(nfn) or mapping.get(stem) or {}
            if mp.get("skip"):
                continue
            if mp.get("crop"):
                crop, why = _resolve_crop(m, mp["crop"]), "mapping"
            else:
                crop, why = _crop_for_filename(stem, m["crops"])
            if crop is None:
                warns.append(f"{fn}: 건너뜀 - {why if why != 'mapping' else '매핑한 크롭 없음'}")
                continue
            data = _read(os.path.join(path, fn))
            try:
                pix = _decode(data)
            except OSError as e:
                warns.append(f"{fn}: 이미지를 열 수 없음 ({e})")
                continue
            x0, y0, x1, y1 = crop["rect"]
            if pix.shape[:2] == (y1 - y0, x1 - x0):
                src = _load_rgba(root, crop["source"])
                if np.array_equal(pix[..., :3], src[..., :3]):
                    warns.append(f"{fn}: 입력 크롭과 픽셀이 같아 거부(bypass 통과 의심)")
                    continue
            tids = [t["tid"] for t in crop["targets"]]
            tid = str(mp.get("tid") or tids[0]).upper()
            if tid not in tids:
                warns.append(f"{fn}: 타깃 {tid} 가 {crop['name']} 에 없음 → {tids[0]}")
                tid = tids[0]
            ext = os.path.splitext(fn)[1].lower().lstrip(".")
            rel = store.save_bytes_ca(root, "cands", data, ext, prefix="f_")
            key = os.path.splitext(os.path.basename(rel))[0]
            if key in keys:
                warns.append(f"{fn}: 같은 내용의 파일이 이미 등록됨 → 건너뜀")
                continue
            keys.add(key)
            out.append({
                "key": key, "crop_id": crop["id"], "tid": tid, "file": rel,
                "src_size": [int(pix.shape[1]), int(pix.shape[0])], "origin": "folder",
                "origin_info": {"folder": path, "filename": fn, "match": why, "crop_source": crop["source"]},
                "quad": dpsd.quad_from_rect(crop["rect"]), "picked": False, "z": None, "hand_mask": None,
            })
        return out

    @staticmethod
    def _autopick(m: dict, keys: set[str], warns: list[str]) -> None:
        """이번 폴더 실행으로 들어온 후보(keys) 중, pick 이 하나도 없는 타깃의 첫 후보를 pick.
        수확 후보(PS 에서 숨겨 거절한 것)나 이전 실행 후보는 건드리지 않는다."""
        picked = {(c["crop_id"], c["tid"]) for c in m["candidates"] if c.get("picked")}
        for c in m["candidates"]:
            k = (c["crop_id"], c["tid"])
            if c["key"] in keys and k not in picked:
                c["picked"] = True
                picked.add(k)
                warns.append(f"{c['crop_id']}/{c['tid']}: pick 없음 → 첫 후보 {c['key']} 자동 선택")

    @classmethod
    def IS_CHANGED(cls, source="", path="", **kwargs):
        if path is None:  # 링크된 경로: 파일 지문을 볼 수 없음
            return float("nan")
        path = _strip_path(path)
        fp = store._file_fp(path) if os.path.isfile(path) else store._dir_fp(path)
        vals = {k: kwargs.get(k) for k in ("group", "mapping_json", "visible_is_pick", "keep_hand_masks")}
        return json.dumps([source, path, fp, vals], ensure_ascii=False)


# ══════════════════════════════════════════════════════════════════════
# 노드 4 — Prepare (무료)
# ══════════════════════════════════════════════════════════════════════
def _glossary(root: str, m: dict) -> dict:
    """store.project_glossary(프로젝트 glossary.json + Import 의 glossary_path — Prepare·Spec Draft·Studio 가 같은 값)."""
    return store.project_glossary(root, m)


class BMKDesignPatchPrepare:
    """크롭마다 스펙 → 평탄화 입력 → 변형별 결정적 프롬프트 → cell_key 로 GPT 작업 목록(jobs)을 만든다(호출 없음)."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "project": (PROJECT_TYPE, {"tooltip": "BMK Design Patch Project 출력(Import PSD 이후)."}),
                "model": (_MODELS, {"default": _MODELS[0], "tooltip": "GPT Image 모델(cell_key 에 들어감)."}),
                "quality": (_QUALITIES, {"default": "max",
                                         "tooltip": "품질(cell_key 에 들어감). low·medium 은 시험용(싸고 빠름, 결과 품질 낮음)."}),
                "size_rule": (list(store.GPT_SIZE_RULES), {
                    "default": "user_k",
                    "tooltip": "출력 크기 규칙. user_k = 정사각 2048², 비정사각 k=max(2,1024/짧은변)(정수 아니면 정수 k). "
                               "int_k_2560 = 긴 변 ≤2560 최대 정수 k."}),
                "variants": ("STRING", {"default": "V1,V4",
                                        "tooltip": "프롬프트 변형. V1 표준, V2 타깃 단독(다중 타깃), V3 형태 자유도 +1, V4 체크리스트."}),
                "n": ("INT", {"default": 4, "min": 1, "max": 10, "tooltip": "호출당 장 수(cell_key 에 넣지 않음)."}),
                "ref_style": (_REF_STYLES, {"default": "at",
                                            "tooltip": "이미지 토큰. at = @image1, plain = Image 1, both = 범례에 둘 다. "
                                                       "specs/<crop_id>.json 의 ref_style 이 있으면 그것이 우선."}),
                "calls_per_cell": ("INT", {"default": 1, "min": 1, "max": 8,
                                           "tooltip": "작업(셀)마다 기본 호출 수(jobs[].reps). 호출마다 후보 n장. 늘린 만큼의 새 호출은 "
                                                      "Run 에서 다시 승인해야 과금됩니다(보드 재굴림 결과도 이 수에 셈). "
                                                      "줄여도 받은 결과는 그대로."}),
            },
        }

    RETURN_TYPES = (PROJECT_TYPE, "IMAGE", "STRING")
    RETURN_NAMES = ("project", "job_sheet", "report")
    OUTPUT_TOOLTIPS = ("갱신된 프로젝트 핸들.", "크롭별 입력 시트(평탄화 canvas + 레퍼런스).", "작업 요약·승인 해시·예상 비용·경고.")
    FUNCTION = "run"
    CATEGORY = _CATEGORY
    DESCRIPTION = (
        "무료 단계. 크롭마다 레이어명 골격·용어집·specs/<crop_id>.json 으로 스펙을 만들고, 투명 여백을 평탄화한 입력과 "
        "결정적 프롬프트(변형별), GPT 출력 크기, cell_key 를 jobs 에 기록합니다. 유료 호출은 하지 않고, Run 이 쓸 "
        "승인 해시(pending_hash)와 예상 비용을 보고합니다."
    )
    SEARCH_ALIASES = _ALIASES + ["prepare", "prompt", "gpt prompt", "프롬프트", "작업 준비", "평탄화"]

    def run(self, project, model, quality, size_rule, variants, n, ref_style, calls_per_cell=1):
        t0 = time.time()
        root, m = _open(project)
        before = _snapshot(m)
        if not m["base"]:
            raise ValueError(f"{_TAG} 크롭이 없습니다 - BMK Design Patch Import PSD 를 먼저 실행하세요")
        if ref_style not in _REF_STYLES:
            raise ValueError(f"{_TAG} ref_style 은 {_REF_STYLES} 중 하나여야 합니다: {ref_style!r}")
        dprompt.select_variants({}, variants)  # 형식 검사(잘못된 토큰이면 ValueError)
        # 입력 지문은 스펙·용어집을 읽기 전에 — 실행 도중 바뀐 입력은 지금 값과 달라 Studio 에 '반영 필요'로 남는다
        inputs_fp = store.prepare_inputs_fp(root, m)
        refs_dir = (m.get("source") or {}).get("refs_dir") or ""
        glossary = _glossary(root, m)
        old_jobs = {j["cell_key"]: j for j in m["jobs"]}
        jobs, tiles, warn_lines, dup_lines = [], [], [], []
        adopt_pending = False  # 채택 가드가 'Import 가 반영할 차이'로 건너뛴 크롭이 있음 → 입력 지문을 올리지 않는다
        if not m["crops"]:  # 빈 시작(D6): 예외 대신 경고
            warn_lines.append("  크롭 없음 — 작업 없음(Studio 레이아웃 편집에서 크롭을 그린 뒤 다시 큐)")
        n_new = n_dup = 0
        pbar = comfy.utils.ProgressBar(len(m["crops"]))
        for crop in m["crops"]:
            pbar.update(1)
            ov = store.load_spec_override(root, crop["id"])
            spec = dprompt.spec_from_manifest(crop, None, refs_dir, glossary, ov)
            if "ref_style" not in ov:
                spec["ref_style"] = ref_style
            sha_of: dict[str, str] = {}
            rel_of: dict[str, str] = {}
            # canvas: 크롭 소스 RGBA → edge-replicate 평탄화
            try:
                canvas_rgb, finfo = dprompt.flatten_canvas(_load_rgba(root, crop["source"]))
            except ValueError as e:  # 크롭 전체가 캔버스/작업영역 밖
                warn_lines.append(f"  {crop['id']} {crop['name']}: 작업 없음 - {e}")
                continue
            crel = store.save_png_ca(root, "inputs", canvas_rgb)
            cpath = _abs(root, crel)
            sha_of[cpath], rel_of[cpath] = store.pixel_sha(canvas_rgb), crel
            spec["canvas"] = cpath
            if "_layer_targets" in ov or any(t.get("origin") == "adopt" for t in crop["targets"]):
                stale = store.adopt_stale(crop, ov)
                if stale:
                    # 문법 오류('채택 확인 못 함')는 Import 로도 풀리지 않는다 — 지문까지 막으면 '반영 필요'가 영영 남음
                    adopt_pending = adopt_pending or not stale.startswith("채택 확인 못 함")
                    warn_lines.append(f"  {crop['id']} {crop['name']}: 작업 없음 - {stale}")
                    tiles.append(_label_tile([_fit(canvas_rgb, _THUMB)], [_ascii(crop["id"]), "0 jobs: targets out of sync"],
                                             (255, 140, 120)))
                    continue
            for key in ("guide", "before_paste"):
                if spec.get(key):
                    p = spec[key] if os.path.isabs(spec[key]) else os.path.join(root, spec[key])
                    if not os.path.isfile(p):
                        spec["warnings"].append(f"{key} 파일 없음 → 사용 안 함: {spec[key]}")
                        spec[key] = None
                        continue
                    with Image.open(p) as im:
                        rgb, _ = dprompt.flatten_canvas(im)
                    rel = store.save_png_ca(root, "inputs", rgb)
                    sha_of[_abs(root, rel)], rel_of[_abs(root, rel)] = store.pixel_sha(rgb), rel
                    spec[key] = _abs(root, rel)
            ref_thumbs = []
            for t in spec["targets"]:
                flat = []
                for r in t.get("refs") or []:
                    if not os.path.isfile(r):
                        continue  # spec_from_manifest 가 이미 경고함
                    with Image.open(r) as im:
                        rgb = dprompt.flatten_ref(im)
                    rel = store.save_png_ca(root, "inputs", rgb)
                    ap = _abs(root, rel)
                    sha_of[ap], rel_of[ap] = store.pixel_sha(rgb), rel
                    flat.append(ap)
                    if ap not in [x[0] for x in ref_thumbs]:
                        ref_thumbs.append((ap, rgb))
                t["refs"] = flat
                if t["mode"] in ("ref_correct", "sketch_guide") and not flat:
                    spec["warnings"].append(f"{t['id']}: 레퍼런스 파일을 읽지 못해 self_restore 로 바꿈")
                    t["mode"] = "self_restore"
            probs = dprompt.validate_spec(spec)
            x0, y0, x1, y1 = crop["rect"]
            W, H, sinfo = store.gpt_output_size(x1 - x0, y1 - y0, size_rule)
            chosen = dprompt.select_variants(dprompt.variants(spec), variants)
            crop_jobs, seen_prompts = [], {}
            for vname, v in chosen.items():
                same = seen_prompts.get((v["prompt"], tuple(v["images"])))
                if same:  # 예: detail 에 ';' 가 없으면 V4 체크리스트 = V1 → 같은 요청을 두 번 과금하지 않게
                    n_dup += 1
                    dup_lines.append(f"  {crop['id']}: {vname} = {same} (같은 프롬프트·이미지) → 건너뜀")
                    continue
                seen_prompts[(v["prompt"], tuple(v["images"]))] = vname
                shas = [sha_of[p] for p in v["images"]]
                ck = store.cell_key(model, quality, [W, H], _BACKGROUND, v["prompt"], shas, dprompt.TEMPLATE_VERSION, vname)
                roles = []
                for p in v["images"]:
                    role = ("canvas" if p == spec["canvas"] else "guide" if p == spec.get("guide")
                            else "before_paste" if p == spec.get("before_paste") else "ref")
                    roles.append({"role": role, "file": rel_of[p], "sha": sha_of[p]}
                                 | ({"source": crop["source"]} if role == "canvas" else {}))  # 평탄화 전 RGBA(Analyze _gen_frame)
                job = dict(old_jobs.get(ck) or {})
                if not job:
                    n_new += 1
                job.update({"cell_key": ck, "crop_id": crop["id"], "tid": ",".join(t["id"] for t in v["targets"]),
                            "variant": vname, "model": model, "quality": quality, "size": [W, H], "n": int(n),
                            "reps": int(calls_per_cell), "background": _BACKGROUND, "prompt": v["prompt"],
                            "inputs": roles, "template_version": dprompt.TEMPLATE_VERSION, "size_info": sinfo})
                crop_jobs.append(job)
            jobs += crop_jobs
            for wmsg in spec["warnings"] + [f"스펙 점검: {p}" for p in probs]:
                warn_lines.append(f"  {crop['id']} {crop['name']}: {wmsg}")
            cells = [_fit(canvas_rgb, _THUMB)] + [_fit(rgb, _THUMB) for _, rgb in ref_thumbs[:4]]
            lab = [_ascii(f"{crop['id']}  {x1 - x0}x{y1 - y0} -> {W}x{H} ({sinfo['method']})"),
                   _ascii(f"{len(crop_jobs)} jobs: {','.join(j['variant'] for j in crop_jobs)}  n{int(n)}  refs {len(ref_thumbs)}"
                          + (f"  fill {finfo['alpha_frac'] * 100:.0f}%" if finfo["method"] != "none" else ""))]
            tiles.append(_label_tile(cells, lab, (255, 140, 120) if (spec["warnings"] or probs) else (220, 220, 220)))
        m["jobs"] = _plain(jobs)
        # jobs 와 같은 커밋에(STUDIO_S3_SPEC §2.2): 실제로 쓴 위젯 값 — Studio 처음 값·그래프와의 갈라짐 판정, 같은 설정이면 바이트 그대로
        m["prepare_settings"] = {"model": model, "quality": quality, "size_rule": size_rule, "variants": variants,
                                 "n": int(n), "ref_style": ref_style, "calls_per_cell": int(calls_per_cell)}
        if not adopt_pending:  # 채택 미반영 크롭이 있으면 열 때 값 그대로(없으면 키 없음) — Studio '반영 필요'가 다음 반영까지 남게
            m["prepare_inputs"] = inputs_fp
        handle, conflicts = _commit(root, m, before, project)
        plan = runner.plan_calls(m, root)
        mock = runner.mock_enabled(root)  # 해시는 백엔드에 묶인다 — Run 이 지금 쓸 백엔드와 같게
        ap = runner.split_approval(plan, "", backend=runner.BACKEND_MOCK if mock else runner.BACKEND_COMFY_ORG)
        lines = [f"Prepare: 작업 {len(jobs)} (신규 {n_new} · 기존 {len(jobs) - n_new}) · 셀당 호출 {int(calls_per_cell)} · "
                 f"예상 후보 {len(jobs) * int(n) * int(calls_per_cell)}장 · {model} {quality} {size_rule} · "
                 f"템플릿 {dprompt.TEMPLATE_VERSION} | 승인 해시 {ap['pending_hash'] or '-'}{' (MOCK)' if mock else ''} | "
                 f"rev {m['rev']} | {time.time() - t0:.1f}s",
                 "유료 호출 없음. Run 이 approve == pending_hash 일 때만 과금합니다"
                 "(Run 의 retry_failed·retry_orphans·only 가 기본값이면 Run 드라이런의 해시와 같음)."]
        lines += runner.format_plan(plan, ap).splitlines()
        if n_dup:
            lines.append(f"같은 요청이 되는 변형 {n_dup}개 건너뜀:")
            lines += dup_lines
        if warn_lines:
            lines.append(f"경고 {len(warn_lines)}:")
            lines += warn_lines
        lines += [f"  - {w}" for w in conflicts]
        logger.info("%s %s", _TAG, lines[0])
        return (handle, _to_image(_grid(tiles, 2)), "\n".join(lines))


# ══════════════════════════════════════════════════════════════════════
# 노드 5 — Analyze (무료)
# ══════════════════════════════════════════════════════════════════════
def _area(rect) -> int:
    x0, y0, x1, y1 = rect
    return (x1 - x0) * (y1 - y0)


class BMKDesignPatchAnalyze:
    """후보마다 배치 → 가드 정합 → ΔE 자동 마스크 → 톤 보정장 → 게이트."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "project": (PROJECT_TYPE, {"tooltip": "BMK Design Patch Project 출력(Candidate In 이후)."}),
                "register": (_REGISTER_MODES, {"default": "guarded_affine",
                                               "tooltip": "가드 정합. 중앙 |차이| 가 2% 이상 줄 때만 적용. "
                                                          "손 마스크가 있으면 그 영역은 정합 추정에서 뺀다."}),
                "tone": (_TONE_MODES, {"default": "field",
                                       "tooltip": "field = 마스크 밖 저주파 보정장(σ), global = 전역 평균, off = 없음."}),
                "tone_sigma": ("INT", {"default": 16, "min": 2, "max": 128, "tooltip": "보정장 σ(px). 실측 최적 16."}),
                "dE": ("FLOAT", {"default": 8.0, "min": 1.0, "max": 50.0, "step": 0.5, "tooltip": "자동 마스크 ΔE76 문턱."}),
                "grow": ("INT", {"default": 9, "min": 0, "max": 64, "tooltip": "자동 마스크 확장(px)."}),
                "feather_sigma": ("FLOAT", {"default": 3.5, "min": 0.0, "max": 32.0, "step": 0.5,
                                            "tooltip": "자동 마스크 가우시안 페더 σ(px, 마스크에 굽기)."}),
                "roi_prior": (_ROI_PRIORS, {"default": "none",
                                            "tooltip": "hand_bbox = 손 마스크 bbox+10% 밖은 자동 마스크 0."}),
                "only_picked": ("BOOLEAN", {"default": False, "tooltip": "pick 된 후보만 분석."}),
            },
        }

    RETURN_TYPES = (PROJECT_TYPE, "IMAGE", "STRING")
    RETURN_NAMES = ("project", "contact_sheet", "report")
    OUTPUT_TOOLTIPS = ("갱신된 프로젝트 핸들(이 분석 설정을 실어 하류 Compose/Export 가 같은 분석을 씀).",
                       "후보별: 크롭 소스 | 정렬·톤 보정 후보 | 자동 마스크(빨강)·손 마스크 외곽(초록).",
                       "정합·게이트 요약.")
    FUNCTION = "run"
    CATEGORY = _CATEGORY
    DESCRIPTION = (
        "무료 단계. 후보를 크롭 크기로 배치하고 가드 정합(어긋난 결과만 보정), ΔE 자동 마스크 초안, 마스크 밖 저주파 톤 보정장, "
        "게이트(마스크 밖 드리프트, 검은 띠)를 계산합니다. 같은 입력·설정이면 다시 계산하지 않습니다."
    )
    SEARCH_ALIASES = _ALIASES + ["analyze", "registration", "auto mask", "tone", "정합", "자동 마스크", "톤 보정", "게이트"]

    def run(self, project, register, tone, tone_sigma, dE, grow, feather_sigma, roi_prior, only_picked):
        t0 = time.time()
        root, m = _open(project)
        before = _snapshot(m)
        for v, allowed, nm in ((register, _REGISTER_MODES, "register"), (tone, _TONE_MODES, "tone"),
                               (roi_prior, _ROI_PRIORS, "roi_prior")):
            if v not in allowed:
                raise ValueError(f"{_TAG} {nm} 는 {allowed} 중 하나여야 합니다: {v!r}")
        prm = {"register": register, "tone": tone, "tone_sigma": int(tone_sigma), "dE": float(dE), "grow": int(grow),
               "feather_sigma": float(feather_sigma), "roi_prior": roi_prior}
        rejects = m["rejects"]
        cands = [c for c in m["candidates"] if (c.get("picked") or not only_picked) and c["key"] not in rejects]
        n_rej = sum(1 for c in m["candidates"] if c["key"] in rejects)
        pbar = comfy.utils.ProgressBar(max(1, len(cands)))
        entries, done, hit, warns = _ensure_analysis(root, m, cands, prm, pbar=pbar)
        handle, conflicts = _commit(root, m, before, project, analyze=prm)
        # 보드가 고르는 분석 항목(★ 합성·편집 기준 stale)도 이 설정으로 — Review(prebuild_thumbs)와 같은 일. Review 없는 Studio Export
        # 템플릿(Analyze → Compose → Export)으로 설정을 바꿔 재분석해도 보드가 옛 항목을 보며 '편집 기준 바뀜'을 놓치지 않게.
        # board_pref.json 에도 남겨 재시작 뒤 되살린다(_restore_board_prefs)
        board._PREF[store.root_key(root)] = dict(prm)
        store.save_board_pref(root, prm)
        crops = _crop_map(m)
        tiles, lines = [], []
        n_reg = n_fail = n_skip = n_nosrc = 0
        for c in cands:
            e = entries.get(c["key"])
            if e is None:
                continue
            if e.get("no_source"):
                n_nosrc += 1
                warns.append(f"{c['crop_id']} {c['tid']} {c['key']}{' ★' if c.get('picked') else ''}: {e['skipped']}")
                continue
            if e.get("skipped"):
                n_skip += 1
                lines.append(f"  {c['crop_id']} {c['tid']} {c['key']}: 분석 생략 - {e['skipped']}")
                continue
            r, g = e["reg"], e["gates"]
            n_reg += r["applied"]
            n_fail += bool(g["fails"])
            tiles.append(Image.open(_abs(root, e["thumb"])).convert("RGB"))
            crop_rect = crops[c["crop_id"]]["rect"]
            rect = e.get("rect") or crop_rect
            lines.append(f"  {c['crop_id']} {c['tid']} {c['key']} {'★' if c.get('picked') else ' '} "
                         f"reg {r['kind']}" + (f" (s {r['sx']:.3f},{r['sy']:.3f} rot {r['rot_deg']:+.2f} disp {r['disp']:.1f}px)"
                                               if r["applied"] else "")
                         + f" | mad {g['outside_mad']:.1f} | mask {e['mask_area'] / max(1, _area(rect)):.0%}"
                         + (f" | FAIL {','.join(g['fails'])}" if g["fails"] else "")
                         + ("" if rect == crop_rect else f" | 옮기기 전 rect {rect} 기준(만들 때의 크롭)"))
        head = (f"Analyze: 후보 {len(cands)} (계산 {done} · 캐시 {hit} · 생략 {n_skip}"
                + (f" · 소스 없음 {n_nosrc}" if n_nosrc else "")
                + (f" · 보드 탈락 {n_rej} 제외" if n_rej else "") + f") | 정합 적용 {n_reg} | 게이트 실패 {n_fail} | "
                f"{register}/{tone} σ{int(tone_sigma)} dE {float(dE):g} grow {int(grow)} feather {float(feather_sigma):g} "
                f"roi {roi_prior} | rev {m['rev']} | {time.time() - t0:.1f}s")
        report = "\n".join([head] + lines + [f"  - {w}" for w in warns + conflicts])
        logger.info("%s %s", _TAG, head)
        return (handle, _to_image(_grid(tiles, 2 if len(tiles) > 4 else 1)), report)


# ══════════════════════════════════════════════════════════════════════
# 노드 6 — Compose
# ══════════════════════════════════════════════════════════════════════
class BMKDesignPatchCompose:
    """pick 을 z순으로 베이스 위에 합성한 평탄화 미리보기 + 마스크 합집합."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "project": (PROJECT_TYPE, {"tooltip": "BMK Design Patch Project 출력."}),
                "mask_source": (_MASK_SOURCES, {"default": "hand_else_auto",
                                                "tooltip": "auto = 자동 마스크, hand = 수확한 손 마스크(없으면 마스크 없음), "
                                                           "hand_else_auto = 손 마스크가 있으면 손, 없으면 자동."}),
                "use_registration": ("BOOLEAN", {"default": True,
                                                  "tooltip": "켜면 Analyze 정합을 quad 에 반영. 끄면 후보 quad 그대로"
                                                             "(수확 후보 = PSD 의 사용자 배치, 폴더 후보 = crop rect)."}),
                "use_tone": ("BOOLEAN", {"default": True, "tooltip": "Analyze 톤 보정장을 적용."}),
                "zorder": (_ZORDERS, {"default": "auto_small_on_top",
                                      "tooltip": "auto_small_on_top = 실효 마스크가 작은 것을 위로(실측 97% 일치). "
                                                 "harvest = 수확한 PSD 의 레이어 순서."}),
            },
        }

    RETURN_TYPES = (PROJECT_TYPE, "IMAGE", "MASK", "STRING")
    RETURN_NAMES = ("project", "image", "mask", "report")
    OUTPUT_TOOLTIPS = ("갱신된 프로젝트 핸들(이 합성 설정을 실어 하류 Export PSD 가 같은 배치로 씀).",
                       "베이스 위에 pick 을 합성한 풀캔버스 미리보기.",
                       "마스크 합집합(ComfyUI MASK 규약: 1 = 패치 없음/투명, 0 = 패치 적용).", "합성 요약.")
    FUNCTION = "run"
    CATEGORY = _CATEGORY
    DESCRIPTION = (
        "pick 된 후보를 정합 quad·톤 보정·최종 마스크로 베이스 위에 z순 합성한 평탄화 미리보기와 마스크 합집합을 냅니다. "
        "분석은 상류 Analyze 설정(없으면 기본값)을 쓰고, 설정(정합 사용, z순서)은 핸들에 실려 하류 Export PSD 가 같은 배치로 씁니다."
    )
    SEARCH_ALIASES = _ALIASES + ["compose", "flatten", "preview", "합성", "미리보기"]

    def run(self, project, mask_source, use_registration, use_tone, zorder):
        t0 = time.time()
        root, m = _open(project)
        before = _snapshot(m)
        if mask_source not in _MASK_SOURCES or zorder not in _ZORDERS:
            raise ValueError(f"{_TAG} mask_source/zorder 값이 잘못되었습니다: {mask_source!r} {zorder!r}")
        if not m["base"]:
            raise ValueError(f"{_TAG} 베이스가 없습니다 - Import PSD 를 먼저 실행하세요")
        warns: list[str] = []
        crops = _crop_map(m)
        picks = [c for c in m["candidates"] if c.get("picked") and c["key"] not in m["rejects"]
                 and _in_crop(crops.get(c["crop_id"]), c["tid"])]
        entries: dict = {}
        if _needs_analysis(mask_source, bool(use_registration), bool(use_tone)):
            entries, done, _hit, w2 = _ensure_analysis(root, m, picks, project.get("analyze"))
            warns += w2
            if done:
                warns.append(f"상류 Analyze 설정(없으면 기본값)의 분석이 없던 pick {done}개를 분석함")
        comp = {"mask_source": mask_source, "use_registration": bool(use_registration), "use_tone": bool(use_tone),
                "zorder": zorder}
        plan = _plan(root, m, entries, mask_source, bool(use_registration), zorder, False, warns)
        base = _load_rgb(root, m["base"])
        canvas = base.astype(np.float32)
        union = np.zeros(base.shape[:2], np.float32)
        items = list(_slices_in_order(plan))
        pbar = comfy.utils.ProgressBar(max(1, len(items)))
        lines, n_toned, msrc_count = [], 0, {}
        for p, t, it in items:
            pix, toned = _cand_pixels(root, it, bool(use_tone))
            n_toned += toned
            rgb, _alpha, eff, left, top = _render(pix, it["quad"], it["mask"])
            _paste(canvas, union, rgb, eff, left, top)
            msrc_count[it["msrc"]] = msrc_count.get(it["msrc"], 0) + 1
            q = it["quad"]
            lines.append(f"  {p['crop']['id']} {t['tid']} {it['cand']['key']} mask {it['msrc']} "
                         f"quad ({q[0]:.1f},{q[1]:.1f})-({q[4]:.1f},{q[5]:.1f})" + (" toned" if toned else ""))
            pbar.update(1)
        handle, conflicts = _commit(root, m, before, project, compose=comp)
        out = np.clip(np.rint(canvas), 0, 255).astype(np.uint8)
        head = (f"Compose: pick {len(items)} (크롭 {len(plan)}) · 마스크 {msrc_count} · 톤 적용 {n_toned} · "
                f"정합 {'사용' if use_registration else '안 씀'} · z {zorder} | 패치 면적 {float((union > 0.5).mean()) * 100:.2f}% "
                f"| rev {m['rev']} | {time.time() - t0:.1f}s")
        report = "\n".join([head] + lines + [f"  - {w}" for w in warns + conflicts])
        logger.info("%s %s", _TAG, head)
        return (handle, _to_image(out), torch.from_numpy(1.0 - union)[None], report)


# ══════════════════════════════════════════════════════════════════════
# 노드 7 — Export PSD
# ══════════════════════════════════════════════════════════════════════
def _outline(w: int, h: int, px: int = 4, color=(255, 64, 64)) -> np.ndarray:
    o = np.zeros((h, w, 4), np.uint8)
    o[..., :3] = color
    o[:px, :, 3] = 255
    o[-px:, :, 3] = 255
    o[:, :px, 3] = 255
    o[:, -px:, 3] = 255
    return o


def _export_hash(m: dict, entries: dict, comp: dict, opts: dict, edits: dict | None = None) -> str:
    """edits = {★ 후보 key: [편집 file, rect]} — _plan 이 그룹 마스크로 적용한 ★ 의 마스크 편집(S4)."""
    payload = {"v": _EXPORT_VERSION, "opts": opts, "base": m["base"], "canvas": m["canvas"], "work_rect": m["work_rect"],
               "crops": [[c["id"], c["name"], c["rect"], c["targets"]] for c in m["crops"]],
               "cands": m["candidates"], "compose": comp,
               "analysis": {k: e.get("params_hash") for k, e in entries.items()}}
    if m["rejects"]:  # 탈락 후보는 PSD 에서 빠진다(없을 때는 키를 넣지 않아 M1 export 해시 그대로)
        payload["rejects"] = sorted(m["rejects"])
    if edits:  # 그룹 마스크가 바뀐다(없을 때는 키를 넣지 않아 S3 export 해시 그대로 — '변경 없음' 판정 유지)
        payload["mask_edits"] = edits
    if manual := store.z_order(m):
        payload["z_order"] = manual
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def _output_dir(name: str) -> str:
    """output/design_patch/<name> — name = Export 하위 폴더(배치 3-5b), 비우면 프로젝트 폴더 이름."""
    return os.path.join(folder_paths.get_output_directory(), "design_patch", name)


def _sidecar_hash(psd_path: str):
    """export 옆 스냅샷(<stem>.json)의 마지막 export 해시 — 그 경로를 마지막으로 쓴 export. 읽을 수 없으면 None."""
    try:
        with open(os.path.splitext(psd_path)[0] + ".json", encoding="utf-8") as f:
            return (json.load(f).get("exports") or [{}])[-1].get("hash")
    except (OSError, ValueError, AttributeError):
        return None


class BMKDesignPatchExportPSD:
    """pick·대안 후보를 풀해상도 Smart Object(또는 픽셀) 레이어로 넣은 PSD 를 쓴다."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "project": (PROJECT_TYPE, {"tooltip": "BMK Design Patch Project 출력."}),
                "filename_prefix": ("STRING", {"default": "design_patch",
                                               "tooltip": "파일 이름 앞부분. output/design_patch/<subfolder — 비우면 project 폴더>/<prefix>_r<rev>.psd "
                                                          "(이미 있으면 _r<rev>_2 … — 덮어쓰지 않음)"}),
                "layer_mode": (_LAYER_MODES, {"default": "smart_object",
                                              "tooltip": "smart_object = 풀해상도 원본 임베드(PS 에서 재변형 가능), pixel = 배치된 픽셀."}),
                "alternates": (_ALTERNATES, {"default": "hidden_all",
                                             "tooltip": "hidden_all = pick 아닌 후보도 숨김·빨강 라벨로 함께, none = pick 만."}),
                "color": (_COLORS, {"default": "toned", "tooltip": "toned = 톤 보정한 풀해상도 PNG 임베드, raw = 원본 그대로."}),
                "mask_source": (_EXPORT_MASK_SOURCES, {"default": "hand_else_auto",
                                                       "tooltip": "타깃(슬라이스) 그룹 마스크로 쓸 최종 마스크."}),
                "include_base": ("BOOLEAN", {"default": True, "tooltip": "00.Base(클린플레이트 합성) 픽셀 레이어를 넣음."}),
                "include_crop_outlines": ("BOOLEAN", {"default": True,
                                                      "tooltip": "02.크롭영역 그룹(숨김)에 크롭 외곽선 픽셀 레이어를 넣음."}),
            },
            # optional: 예전 워크플로·API 프롬프트(이 입력 없음)도 그대로 검증을 통과하고 기본값을 쓴다.
            "optional": {
                "skip_empty": ("BOOLEAN", {"default": True,
                                           "tooltip": "넣을 후보가 하나도 없으면(pick 0, hidden_all 이면 후보 0) PSD 를 쓰지 않음 — "
                                                      "Run 드라이런 직후의 전체 큐가 베이스만 든 빈 PSD(수십 MB)를 남기지 않게."}),
                # 맨 뒤(배치 3-5b) — 저장된 워크플로 widgets_values(skip_empty 전 7값·뒤 8값)가 밀리지 않게
                "subfolder": ("STRING", {"default": "",
                                         "tooltip": "ComfyUI output/design_patch/ 아래 하위 폴더 이름 — 비우면 프로젝트 폴더 이름(지금과 같음). "
                                                    "이름만(경로 구분자·.. 불가)"}),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("psd_path",)
    OUTPUT_TOOLTIPS = ("저장한 PSD 경로.",)
    FUNCTION = "run"
    OUTPUT_NODE = True
    CATEGORY = _CATEGORY
    DESCRIPTION = (
        "00.Base / 02.크롭영역(외곽선, 숨김) / 03.수정 > 크롭 > 타깃(그룹 마스크 = 최종 마스크) > 후보 구조의 PSD 를 씁니다. "
        "후보는 풀해상도 Smart Object 로 임베드하고 정합은 SO 변형(affine)에 넣습니다(비파괴). pick 은 보임·녹색, 대안은 숨김·빨강. "
        "정합 사용·z순서는 상류 Compose 설정(없으면 기본값)을 따르고, 이미 있는 파일은 덮어쓰지 않습니다."
    )
    SEARCH_ALIASES = _ALIASES + ["export psd", "smart object", "psd 출력", "스마트 오브젝트", "레이어 출력"]

    def run(self, project, filename_prefix, layer_mode, alternates, color, mask_source, include_base,
            include_crop_outlines, skip_empty=True, subfolder=""):
        t0 = time.time()
        root, m = _open(project)
        before = _snapshot(m)
        prefix = str(filename_prefix or "").strip() or "design_patch"
        if not _SAFE_PREFIX_RE.match(prefix) or ".." in prefix:
            raise ValueError(f"{_TAG} filename_prefix 에는 경로 구분자 없이 영문·숫자·한글·_ - . 만 쓸 수 있습니다: {prefix!r}")
        folder = os.path.basename(os.path.normpath(root))  # 폴더 이름(복사한 매니페스트의 project 값 아님)
        try:
            sub = store.check_export_subfolder(subfolder)
        except ValueError as e:
            raise ValueError(f"{_TAG} {e}") from e
        if os.path.normcase(sub) == os.path.normcase(folder):
            sub = ""  # 프로젝트 폴더 이름 = 기본 — 해시·기록이 비운 것과 같게
        for v, allowed, nm in ((layer_mode, _LAYER_MODES, "layer_mode"), (alternates, _ALTERNATES, "alternates"),
                               (color, _COLORS, "color"), (mask_source, _EXPORT_MASK_SOURCES, "mask_source")):
            if v not in allowed:
                raise ValueError(f"{_TAG} {nm} 는 {allowed} 중 하나여야 합니다: {v!r}")
        if not m["base"] or not m["canvas"]:
            raise ValueError(f"{_TAG} 베이스가 없습니다 - Import PSD 를 먼저 실행하세요")
        comp = dict(_COMPOSE_DEFAULTS, **(project.get("compose") or {}))  # 상류 Compose 설정(없으면 기본값)
        use_reg, zorder = bool(comp["use_registration"]), comp["zorder"]
        toned = color == "toned"
        opts = {"layer_mode": layer_mode, "alternates": alternates, "color": color, "mask_source": mask_source,
                "include_base": bool(include_base), "include_crop_outlines": bool(include_crop_outlines),
                "prefix": prefix}
        if sub:  # 하위 폴더(배치 3-5b)를 바꾸면 새 폴더에 새로 씀 — 기본(빈 값)은 키를 넣지 않아 그 전 export 해시·'변경 없음' 그대로
            opts["subfolder"] = sub
        warns: list[str] = []
        crops = _crop_map(m)
        want = [c for c in m["candidates"] if (c.get("picked") or alternates == "hidden_all") and c["key"] not in m["rejects"]]
        inc = [c for c in want if _in_crop(crops.get(c["crop_id"]), c["tid"])]
        if not inc and skip_empty:  # 넣을 후보가 없으면 빈 PSD(베이스만, 수십 MB)를 쓰지 않는다 — Run 드라이런 직후의 전체 큐 등
            _h, conflicts = _commit(root, m, before)
            msg = ("내보낼 후보 없음(pick 0" + ("" if alternates == "hidden_all" else " · 대안 미포함")
                   + (f" · 크롭·타깃이 빠진 후보 {len(want)} 제외" if want else "") + ") → PSD 를 쓰지 않음")
            logger.info("%s Export: %s", _TAG, msg)
            return {"ui": {"text": [msg] + conflicts, _EXPORT_UI: [{"outcome": "empty", "psd": None, "rev": None}]},
                    "result": ("",)}
        entries: dict = {}
        if _needs_analysis(mask_source, use_reg, toned) or _needs_analysis(comp["mask_source"], use_reg, toned):
            entries, done, _hit, w2 = _ensure_analysis(root, m, inc, project.get("analyze"))
            warns += w2
            if done:
                warns.append(f"상류 Analyze 설정(없으면 기본값)의 분석이 없던 후보 {done}개를 분석함")
        plan = _plan(root, m, entries, mask_source, use_reg, zorder, alternates == "hidden_all", warns,
                     area_mask_source=comp["mask_source"])
        # 해시의 편집 = _plan 이 실제로 그룹 마스크로 쓴 것(★ 만, 파일을 못 읽어 기준으로 떨어진 기록은 빠짐 — 보이는 것 = PSD)
        edits = {it["cand"]["key"]: [it["mask_edit"], dmask.edit_record(m, it["cand"]["key"])["rect"]]
                 for _p, _t, it in _slices_in_order(plan) if it["mask_edit"]}
        # 해시의 하위 폴더 = 이 OS 의 폴더 같음(Windows 는 대소문자 무시 — 외주_A·외주_a 는 같은 폴더라 '변경 없음'), 기록 options 는 입력 그대로
        eh = _export_hash(m, entries, comp, dict(opts, subfolder=os.path.normcase(sub)) if sub else opts, edits)
        try:
            state_sig = store.export_state_sig(m)
        except Exception:  # 점검표 서명 실패는 PSD 출력·재사용을 막지 않는다
            state_sig = None
            warns.append("Export 변경 여부를 계산하지 못함 — 서명 없이 기록")
        last = next((e for e in reversed(m["exports"]) if e.get("hash") == eh), None)
        if last and os.path.isfile(last["psd"]) and _sidecar_hash(last["psd"]) == eh:  # 다른 export 가 덮어쓴 파일은 재사용 안 함
            if state_sig is None:
                last.pop("state_sig", None)  # 계산하지 못했으면 옛 서명으로 완료 표시하지 않음
            else:
                last["state_sig"] = state_sig  # 옛 기록도 보완 — 출력 파일·사이드카는 그대로
            _h, conflicts = _commit(root, m, before)
            store.save_board_export(root, eh)  # 새 항목은 더하지 않음 — 보드·Studio 가 이 PSD 의 설정을 따르게(store.last_export)
            logger.info("%s Export: 바뀐 것 없음 → 기존 파일 %s", _TAG, last["psd"])
            for wmsg in warns:  # 편집 파일을 못 읽어 기준으로 떨어진 경고 등 — 기존 PSD 를 돌려줄 때도 알린다
                logger.warning("%s Export: %s", _TAG, wmsg)
            return {"ui": {"text": [f"변경 없음: {last['psd']}"] + warns + conflicts,
                           _EXPORT_UI: [{"outcome": "unchanged", "psd": last["psd"], "rev": last["rev"]}]},
                    "result": (last["psd"],)}

        rev = int(m["rev"])
        out_dir = _output_dir(sub or folder)
        os.makedirs(out_dir, exist_ok=True)
        stem, k = f"{prefix}_r{rev}", 1
        while any(os.path.exists(os.path.join(out_dir, stem + ext)) for ext in (".psd", "_mask.png", ".json")):
            k += 1  # 같은 이름(다른 base_dir·다시 만든·복사한) 프로젝트의 파일이나 PS 에서 고친 파일을 덮어쓰지 않는다
            stem = f"{prefix}_r{rev}_{k}"
        psd_path = os.path.join(out_dir, stem + ".psd")
        W, H = m["canvas"]
        wr = dpsd.PSDWriter((W, H))
        base = _load_rgb(root, m["base"])
        flat = np.full((H, W, 3), 255.0, np.float32)  # 병합 프리뷰 = 흰 문서 위 보이는 레이어(아래와 같은 순서로 합성)
        if include_base:
            x0, y0, x1, y1 = m["work_rect"] or [0, 0, W, H]
            x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
            if x1 > x0 and y1 > y0:
                wr.add_pixel(None, "00.Base", np.ascontiguousarray(base[y0:y1, x0:x1]), x0, y0)
                flat[y0:y1, x0:x1] = base[y0:y1, x0:x1]
        if include_crop_outlines:
            g_out = wr.add_group(None, "02.크롭영역", visible=False, open_folder=False)
            for c in m["crops"]:
                cx0, cy0, cx1, cy1 = (max(0, c["rect"][0]), max(0, c["rect"][1]), min(W, c["rect"][2]), min(H, c["rect"][3]))
                if cx1 > cx0 and cy1 > cy0:
                    wr.add_pixel(g_out, c["name"], _outline(cx1 - cx0, cy1 - cy0), cx0, cy0)
        g_fix = wr.add_group(None, "03.수정")
        union = np.zeros((H, W), np.float32)
        total = sum(len(t["slices"]) + len(t["alts"]) for p in plan for t in p["targets"])
        pbar = comfy.utils.ProgressBar(max(1, total) + 1)
        # c<번호> = 타깃 안 등록 순서, 탈락 후보도 센다 — Studio 후보 번호(board._target_members)와 같게(PSD 번호에 빈칸이 생길 수 있음)
        num: dict[str, int] = {}
        seen_tid: dict[tuple, int] = {}
        for c in m["candidates"]:
            k = (c.get("crop_id"), c.get("tid"))
            seen_tid[k] = num[c["key"]] = seen_tid.get(k, 0) + 1
        written: list[dict] = []
        n_toned = n_groups = n_edit = 0

        def add_cand(parent, crop, it, num, visible):
            nonlocal n_toned
            c = it["cand"]
            pix, is_toned = _cand_pixels(root, it, toned)
            n_toned += is_toned
            rgb, alpha, eff, left, top = _render(pix, it["quad"], it["mask"])
            cache = rgb if alpha is None else np.dstack([rgb, alpha])
            name = f"{crop['name']} · {_target_label(crop, c['tid'])} · c{num:02d}" + (" ★" if c.get("picked") else "")
            label = "green" if c.get("picked") else "red"
            if layer_mode == "smart_object":
                data, ft = self._so_bytes(root, it, pix, is_toned)
                wr.add_smart_object(parent, name, data, filetype=ft, src_size=(pix.shape[1], pix.shape[0]),
                                    quad=it["quad"], visible=visible, label=label, cache_rgb=cache,
                                    filename=f"{c['key']}.{ft}")
            else:
                wr.add_pixel(parent, name, cache, left, top, visible=visible, label=label)
            if visible:
                _paste(flat, union, rgb, eff, left, top)
            mk = it["mask"]
            written.append({"name": name, "key": c["key"], "quad": [float(v) for v in it["quad"]],
                            "src_size": [int(pix.shape[1]), int(pix.shape[0])], "visible": visible,
                            "toned": bool(is_toned), "mask_src": it["msrc"], "mask_rect": None if mk is None else
                            [mk["left"], mk["top"], mk["left"] + mk["arr"].shape[1], mk["top"] + mk["arr"].shape[0]]})
            if it["mask_edit"]:
                written[-1]["mask_edit"] = it["mask_edit"]
            pbar.update(1)

        for p in plan:
            crop = p["crop"]
            cg = wr.add_group(g_fix, crop["name"])
            for t in p["targets"]:
                if len(t["slices"]) <= 1:
                    first = t["slices"][0] if t["slices"] else None
                    tg = wr.add_group(cg, t["label"], mask=None if first is None else first["mask"])
                    n_groups += first is not None and first["mask"] is not None
                    n_edit += first is not None and first["msrc"] == "edit"
                    for it in t["slices"]:
                        add_cand(tg, crop, it, num[it["cand"]["key"]], True)
                    for it in t["alts"]:
                        add_cand(tg, crop, it, num[it["cand"]["key"]], False)
                else:
                    tg = wr.add_group(cg, t["label"])
                    for k, it in enumerate(t["slices"], 1):
                        sg = wr.add_group(tg, f"{t['label']} · slice {k}", mask=it["mask"])
                        n_groups += it["mask"] is not None
                        n_edit += it["msrc"] == "edit"
                        add_cand(sg, crop, it, num[it["cand"]["key"]], True)
                        if k == 1:
                            for alt in t["alts"]:
                                add_cand(sg, crop, alt, num[alt["cand"]["key"]], False)
        res = wr.save(psd_path, verify=True, composite=np.clip(np.rint(flat), 0, 255).astype(np.uint8))
        pbar.update(1)
        mask_png = os.path.join(out_dir, stem + "_mask.png")
        Image.fromarray(np.clip(np.rint(union * 255.0), 0, 255).astype(np.uint8), "L").save(mask_png)
        snap = os.path.join(out_dir, stem + ".json")
        rec = {"rev": rev, "psd": psd_path.replace("\\", "/"), "time": time.strftime("%Y-%m-%d %H:%M:%S"), "hash": eh,
               "options": opts, "compose": comp, "bytes": res["bytes"], "smart_objects": res["smart_objects"],
               "layers": len(written), "problems": len(res["problems"]), "mask": mask_png.replace("\\", "/"),
               "mask_groups": n_groups, "mask_edits": n_edit}
        if state_sig is not None:
            rec["state_sig"] = state_sig
        m["exports"].append(_plain(rec))
        store.write_json_atomic(snap, dict(m, export_layers=written))
        _h, conflicts = _commit(root, m, before)
        store.save_board_export(root, eh)
        warns += conflicts
        head = (f"Export PSD: {psd_path} | 레이어 {len(written)} (pick {sum(w['visible'] for w in written)}) · "
                f"그룹 마스크 {n_groups} (편집 {n_edit}) · 톤 {n_toned} · {layer_mode} · {res['bytes'] / 1e6:.1f}MB · 검사 문제 {len(res['problems'])} "
                f"| 정합 {'사용' if use_reg else '안 씀'} · z {zorder} | rev {m['rev']} | {time.time() - t0:.1f}s")
        for p_ in res["problems"]:
            warns.append(f"검사: {p_}")
        warns += res.get("warnings") or []
        logger.info("%s %s", _TAG, head)
        for wmsg in warns:
            logger.warning("%s Export: %s", _TAG, wmsg)
        return {"ui": {"text": [head] + warns, _EXPORT_UI: [{"outcome": "new", "psd": rec["psd"], "rev": rev}]},
                "result": (rec["psd"],)}

    @staticmethod
    def _so_bytes(root: str, it: dict, pix: np.ndarray, is_toned: bool) -> tuple[bytes, str]:
        """SO 임베드 bytes: 원본 파일 그대로, 톤 보정이면 풀해상도 PNG(derived 에 캐시)."""
        c, entry = it["cand"], it["entry"]
        if not is_toned:
            ext = os.path.splitext(c["file"])[1].lower().lstrip(".")
            return _read(_abs(root, c["file"])), ("jpg" if ext == "jpeg" else ext)
        tone = entry["tone"]
        if not (tone.get("full") and os.path.isfile(_abs(root, tone["full"]))):
            tone["full"] = store.save_png_ca(root, "derived", pix, prefix=_derived_prefix(c["key"], entry["params_hash"]) + "toned_")
        return _read(_abs(root, tone["full"])), "png"


# ══════════════════════════════════════════════════════════════════════
# 노드 8 — Run (유료, M2)
# ══════════════════════════════════════════════════════════════════════
def _server():
    """실행 중인 ComfyUI 의 PromptServer. 단독 실행·테스트면 None(server 모듈을 새로 import 하지 않는다)."""
    return getattr(getattr(sys.modules.get("server"), "PromptServer", None), "instance", None)


def _board_key(root: str) -> str:
    """보드·Drain 이 쓰는 레지스트리 키. 아직 등록되지 않았으면 등록한다(Review 없이 Run 만 돌린 세션에서도 Drain 이 되게)."""
    key = store.root_key(root)
    if store.resolve_board_root(key) is None:
        key = store.register_root(root)
    return key


def _progress_cb(total: int, unique_id):
    """실행기의 progress_cb(done, total, text) → ComfyUI 진행 막대 + 노드 진행 문구(Run·Spec Draft 공용).
    Cancel 이면 update_absolute 가 InterruptProcessingException 을 내고 실행기가 새 발사를 멈춘다."""
    pbar = comfy.utils.ProgressBar(total)
    srv = _server()

    def progress(done, total, text):
        if srv is not None and unique_id is not None:
            srv.send_progress_text(text, unique_id)
        pbar.update_absolute(done, total)

    return progress


def _run_plan(root: str, approve: str, opts: dict, backend_name: str) -> tuple[dict, dict]:
    """디스크 최신 매니페스트 + 원장으로 실행 계획과 승인 판정(runner.plan_calls / split_approval, 해시는 백엔드에 묶임)."""
    plan = runner.plan_calls(store.load_manifest(root), root, **opts)
    return plan, runner.split_approval(plan, approve, backend=backend_name)


def _cand_grid(root: str, keys: list[str], limit: int = 48) -> torch.Tensor:
    """이번 실행에 도착한 후보의 썸네일 격자(IMAGE). 없으면 1x1."""
    by_key = {c["key"]: c for c in store.load_manifest(root)["candidates"]}
    tiles = []
    for k in keys[:limit]:
        c = by_key.get(k)
        if c is None:
            continue
        img = _decode(_read(_abs(root, c["file"])))
        tiles.append(_label_tile([_fit(img, _THUMB)], [_ascii(f"{c['crop_id']} {c['tid']}"), _ascii(k)]))
    if not tiles:
        return torch.zeros((1, 1, 1, 3), dtype=torch.float32)
    return _to_image(_grid(tiles, min(6, len(tiles))))


class BMKDesignPatchRun:
    """Prepare 의 jobs 를 comfy.org GPT Image(유료)로 실행한다. 승인 해시가 맞을 때만 과금하고, 결과는 원장에 먼저 저장한다."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "project": (PROJECT_TYPE, {"tooltip": "BMK Design Patch Project 출력(Prepare 이후)."}),
                "approve": ("STRING", {
                    "default": "",
                    "tooltip": "드라이런 보고의 pending_hash. 지금 대기 중인 호출 목록의 해시와 같을 때만 과금합니다. 비우거나 "
                               "다르면 과금 없이 드라이런(개수·예상 USD·해시)만 합니다. 노드의 '승인 후 실행' 버튼이 채웁니다."}),
                "max_new_calls": ("INT", {"default": 64, "min": 1, "max": 1000,
                                          "tooltip": "이번 실행에서 새로 과금할 호출 수 상한. 나머지는 대기로 남습니다."}),
                "concurrency": ("INT", {"default": 4, "min": 1, "max": 8, "tooltip": "동시에 보내는 호출 수."}),
                "wave_minutes": ("INT", {"default": 25, "min": 1, "max": 60,
                                         "tooltip": "실행 시작 후 이 시간(분)이 지나면 새 호출을 멈춥니다(진행 중인 호출은 받음). "
                                                    "로그인 토큰 만료 대비 — '자동 이어가기' 를 켜면 새 토큰으로 이어서 큐에 넣습니다."}),
                "retry_failed": ("BOOLEAN", {"default": False,
                                             "tooltip": "실패한 호출을 다시 호출합니다(과금, 새 승인 필요)."}),
                "retry_orphans": ("BOOLEAN", {"default": False,
                                              "tooltip": "결과 없이 끊긴 호출(orphaned, 과금됐을 수 있음)을 다시 호출합니다(과금, 새 승인 "
                                                         "필요). 응답을 받아 둔 호출은 이것 없이도 다운로드만 다시 합니다."}),
                "only": ("STRING", {"default": "",
                                    "tooltip": "승인이 필요한 호출만 거르는 쉼표 필터: crop_id, NNN, cell_key 앞 6자 이상, call_id. "
                                               "비우면 전부. 보드 재굴림·다운로드 복구는 항상 포함."}),
            },
            "hidden": {"auth_token": "AUTH_TOKEN_COMFY_ORG", "api_key": "API_KEY_COMFY_ORG",
                       "usage_source": "COMFY_USAGE_SOURCE", "unique_id": "UNIQUE_ID"},
        }

    RETURN_TYPES = (PROJECT_TYPE, "STRING", "IMAGE")
    RETURN_NAMES = ("project", "report", "new_candidates")
    OUTPUT_TOOLTIPS = ("갱신된 프로젝트 핸들(새 후보·호출 기록).", "드라이런/실행 보고(호출별 결과·크레딧·orphaned 처리법).",
                       "이번 실행에 도착한 후보 썸네일 격자(없으면 1x1).")
    FUNCTION = "run"
    OUTPUT_NODE = True
    CATEGORY = _CATEGORY
    DESCRIPTION = (
        "유료 단계. Prepare 의 작업을 comfy.org GPT Image 로 실행합니다. approve 가 비어 있거나 지금 대기 목록의 해시와 다르면 "
        "과금 없이 드라이런(개수·예상 USD·pending_hash)만 보고하고, 노드의 '승인 후 실행' 버튼으로 그 해시를 승인하면 과금합니다. "
        "보드 재굴림은 사전 승인입니다. 결과는 원장(cands/<cell_key>/)에 먼저 저장하므로 끊겨도 다시 과금하지 않습니다."
    )
    SEARCH_ALIASES = _ALIASES + ["run", "gpt image", "api", "실행", "과금", "승인"]

    async def run(self, project, approve, max_new_calls, concurrency, wave_minutes, retry_failed, retry_orphans, only,
                  auth_token=None, api_key=None, usage_source=None, unique_id=None, **_stale):
        # **_stale: 옛 워크플로가 지금은 없는 입력을 링크해 와도 인자 바인딩(첫 await 전)에서 실패하지 않게(hidden 토큰 기록 방지)
        t0 = time.time()
        # 첫 줄부터 await: 이후 예외는 ComfyUI 의 대기 노드 경로로 보고되어 입력값(hidden 토큰)이 기록되지 않는다
        root, _m = await asyncio.to_thread(_open, project)
        key = _board_key(root)
        opts = {"retry_failed": bool(retry_failed), "retry_orphans": bool(retry_orphans), "only": str(only or "")}
        approve = str(approve or "").strip()
        rec = await asyncio.to_thread(runner.reconcile_ledger, root)
        # 백엔드를 먼저 정한다: 승인 해시·실행·ui backend 를 한 백엔드가 정해야 MOCK 드라이런 해시가 유료 실행을 승인하지 않는다
        backend = runner.make_backend(root, {"auth_token": auth_token, "api_key": api_key, "usage_source": usage_source,
                                             "unique_id": unique_id})
        plan, ap = await asyncio.to_thread(_run_plan, root, approve, opts, backend.name)
        report = None
        if ap["to_run"]:
            report = await self._execute(root, ap["to_run"], backend, int(concurrency), wave_minutes, int(max_new_calls),
                                         unique_id)
            plan_after, ap_after = await asyncio.to_thread(_run_plan, root, "", opts, backend.name)
        else:
            plan_after, ap_after = plan, runner.split_approval(plan, "", backend=backend.name)
        m = store.load_manifest(root)
        if report is not None and report.stopped_reason == "wave" and report.remaining:
            srv = _server()
            if srv is not None:  # JS 가 이 프롬프트가 끝나면 새 해시로 이어서 큐에 넣는다(자동 이어가기)
                srv.send_sync("bmk.dp.wave", {"node": unique_id, "root_key": key, "remaining": len(report.remaining)},
                              srv.client_id)
        new_keys = list(dict.fromkeys(rec["candidates"] + (report.new_candidates if report is not None else [])))
        grid = await asyncio.to_thread(_cand_grid, root, new_keys)
        draft = (project or {}).get("draft") or {}  # 상류 Spec Draft 의 승인 대기(pending 은 화풍 단위 포함)
        draft_pending, draft_style = int(draft.get("pending") or 0), int(draft.get("pending_style") or 0)
        draft_held, draft_held_style = int(draft.get("held") or 0), int(draft.get("held_style") or 0)
        draft_unwritten = int(draft.get("unwritten") or 0)
        draft_mock = 0 if backend.name == runner.BACKEND_MOCK else int(draft.get("mock_specs") or 0)
        text = self._report(ap, plan_after, ap_after, report, rec, backend, approve, bool(auth_token or api_key),
                            int(concurrency), new_keys, m["rev"], time.time() - t0, draft_pending, draft_style,
                            draft_unwritten, draft_held, draft_held_style, draft_mock)
        logger.info("%s %s", _TAG, text.splitlines()[0])
        # continuation_hash = 이번 실행이 승인받고 남긴 호출의 해시. JS 자동 이어가기는 pending_hash 가 이것과 같을 때만
        # 승인한다(개수가 아니라 집합 비교 — 승인하지 않은 호출이 섞이면 멈춤). stopped_reason = 실행이 멈춘 까닭(runner.STOP_REASONS —
        # auth = 로그인 없음·401/402/403 부터 보내지 않음: Studio 끝 줄이 '승인분 실행' 대신 로그인 필요)
        entry = {"pending_hash": ap_after["pending_hash"], "pending": len(ap_after["needs_approval"]),
                 "est_usd": list(ap_after["est_usd"]), "approved_now": ap["approved_now"], "root_key": key,
                 "stopped_reason": report.stopped_reason if report is not None else "none",
                 "backend": backend.name, "continuation_hash": report.continuation_hash if report is not None else "",
                 "draft_pending": draft_pending, "draft_style": draft_style, "draft_unwritten": draft_unwritten,
                 "draft_held": draft_held, "draft_held_style": draft_held_style, "draft_mock": draft_mock}
        return {"ui": {"bmk_dp_run": [entry], "text": [text]}, "result": (_handle(root, m, project), text, grid)}

    @staticmethod
    async def _execute(root, calls, backend, concurrency, wave_minutes, max_new_calls, unique_id):
        """runner.run_calls + 진행 표시 + 보드 Drain(board 레지스트리). 취소는 InterruptProcessingException 으로 다시 올린다."""
        try:
            with board.run_active(root):
                return await runner.run_calls(root, calls, backend, concurrency=concurrency, wave_minutes=wave_minutes,
                                              max_new_calls=max_new_calls, progress_cb=_progress_cb(len(calls), unique_id),
                                              drain_flag=lambda: board.drain_requested(root))
        except (Exception, comfy.model_management.InterruptProcessingException) as e:  # IPE 는 BaseException
            rep = getattr(e, "bmk_dp_report", None)
            if rep is None:
                raise
            logger.warning("%s Run 취소 — 도착한 결과는 저장함: %s", _TAG, " | ".join(rep.text().splitlines()))
            if isinstance(e, comfy.model_management.InterruptProcessingException):
                raise
            raise comfy.model_management.InterruptProcessingException() from e

    @staticmethod
    def _report(ap, plan_after, ap_after, report, rec, backend, approve, has_auth, concurrency, new_keys, rev,
                sec, draft_pending=0, draft_style=0, draft_unwritten=0, draft_held=0, draft_held_style=0,
                draft_mock=0) -> str:
        mock = backend.name == runner.BACKEND_MOCK
        be = "MOCK(과금 없음)" if mock else "comfy.org(유료)"
        if report is not None:
            state = f"실행: 발사 {report.launched} · 완료 {report.done} · 실패 {report.failed} · 남음 {len(report.remaining)}"
        elif ap["needs_approval"]:
            state = f"드라이런(과금 없음): 승인 필요 {len(ap['needs_approval'])}건"
        else:
            state = "할 일 없음(대기 호출 0)"
        lines = [f"Run [{be}] {state} | 새 후보 {len(new_keys)} | rev {rev} | {sec:.1f}s"]
        if draft_pending > 0:
            lines.append(f"주의: 스펙 초안 대기 {_draft_pending_text(draft_pending, draft_style)}(Spec Draft 미승인) — "
                         f"지금 Run 을 승인하면 초안 없는 프롬프트로 과금")
        if draft_held > 0:
            lines.append(f"주의: Spec Draft 의 only 로 보류된 초안 대기 {_draft_pending_text(draft_held, draft_held_style)} — "
                         f"지금 Run 을 승인하면 초안 없는 프롬프트로 과금(only 를 비우면 승인 목록에 오름)")
        if draft_mock > 0:
            lines.append(f"주의: 스펙 {draft_mock}개가 mock 초안(자리표시 문구, Spec Draft vlm=mock) — 지금 승인하면 그 문구로 "
                         f"과금. 실제 vlm 으로 바꾸면 'mock 초안 교체' 로 다시 초안할 수 있음")
        if draft_unwritten > 0:
            lines.append(f"주의: 스펙 파일 {draft_unwritten}개에 초안·화풍 변경을 아직 못 씀(편집기·백신 잠금) — 잠금을 풀고 "
                         f"다시 큐하면 Spec Draft 가 원장으로 다시 씀(0 호출). 지금 승인하면 그 변경 없이 과금")
        if report is not None:
            lines += report.text().splitlines()
        if ap["blocked"]:
            why = ("approve 가 비어 있음" if not approve else
                   f"approve {approve} ≠ 지금 pending_hash {ap['pending_hash']}(드라이런 이후 대기 목록·n 이 바뀜)")
            lines.append(f"승인 안 된 {len(ap['blocked'])}건은 과금하지 않음 — {why}")
        lines.append("현재 상태: " + runner.format_plan(plan_after, ap_after, concurrency=concurrency).replace("\n", "\n  "))
        if ap_after["needs_approval"]:
            lines.append(f"→ 승인: Run 노드의 '승인 후 실행' 버튼, 또는 approve 에 {ap_after['pending_hash']} 를 넣고 큐")
            if not mock and not has_auth:
                lines.append("주의: comfy.org 로그인(또는 API 키) 정보가 없습니다 — ComfyUI 에 로그인한 뒤 큐에 넣으세요")
        if rec["candidates"]:
            lines.append(f"원장 재조정: 매니페스트에 없던 결과 후보 {len(rec['candidates'])}개 등록")
        lines += [f"  - {w}" for w in rec["warnings"]]
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════
# 노드 9 — Review (M2)
# ══════════════════════════════════════════════════════════════════════
class BMKDesignPatchReview:
    """프로젝트를 Review Board 에 등록하고 보드 썸네일을 미리 만든다(Open Board 버튼은 JS)."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "project": (PROJECT_TYPE, {"tooltip": "BMK Design Patch Project 출력(Run → Analyze 이후 권장)."}),
            },
        }

    RETURN_TYPES = (PROJECT_TYPE, "STRING")
    RETURN_NAMES = ("project", "report")
    OUTPUT_TOOLTIPS = ("프로젝트 핸들(그대로).", "타깃별 후보·★·탈락·대기 호출 요약과 보드 주소.")
    FUNCTION = "run"
    OUTPUT_NODE = True
    CATEGORY = _CATEGORY
    DESCRIPTION = (
        "Review Board(별도 창)를 엽니다. 실행하면 프로젝트를 보드에 등록하고 후보 썸네일을 미리 만든 뒤, 노드의 Open Board 버튼으로 "
        "타깃별 모든 후보를 같은 초점으로 확대해 키보드로 ★·slice·탈락을 고르고 재굴림(+1 호출, 사전 승인)을 요청합니다. "
        "보드의 큐 요청은 이 노드만 부분 실행합니다(Run 이 재굴림만 과금, Export 는 돌지 않음)."
    )
    SEARCH_ALIASES = _ALIASES + ["review", "board", "review board", "pick", "보드", "검토", "후보 선택"]

    async def run(self, project):
        t0 = time.time()
        root, m = _open(project)
        key = store.register_root(root)
        prm = project.get("analyze") or dict(_ANALYZE_DEFAULTS)  # Compose 와 같은 분석 설정의 항목을 보드가 보여 준다
        thumbs = await asyncio.to_thread(board.prebuild_thumbs, root, prm)
        store.save_board_pref(root, prm)  # prebuild_thumbs 가 넣은 보드 분석 설정을 재시작 너머로(Analyze 와 같게)
        st = await asyncio.to_thread(board.build_state, root)
        url = board.board_url(key)
        c = st["counts"]
        lines = [f"Review: 타깃 {c['targets']} · 후보 {c['candidates']} (탈락 {c['rejected']}) · ★ {c['picks']} · "
                 f"대기 호출 {st['pending_calls']} (재굴림 사전 승인 {st['pending_preapproved']})"
                 + (" · MOCK" if st["backend_mock"] else "")
                 + f" | 썸네일 {thumbs['thumbs']} (새로 {thumbs['built']}) | rev {st['rev']} | {time.time() - t0:.1f}s",
                 f"보드: {url}  (노드의 Open Board 버튼)"]
        for t in st["targets"]:
            ch = t["chips"]
            lines.append(f"  {t['key']} {t['crop_name']}: 후보 {ch['n']} · ★ {ch['picked']} · 탈락 {ch['rejected']} · "
                         f"게이트 실패 {ch['gate_fail']} · reframed {ch['reframed']}"
                         + (f" · MOCK {ch['mock']}" if ch["mock"] else "")
                         + (f" · 대기 {t['pending_calls']}" if t["pending_calls"] else ""))
        if st["orphan_candidates"]:
            lines.append(f"  - 크롭이 사라진 후보 {st['orphan_candidates']}개는 보드에 나오지 않음")
        nosrc = [f"{c['key']}{' ★' if c['picked'] else ''}" for t in st["targets"] for c in t["candidates"]
                 if c["analysis"] == "nosrc" and not c["hand_mask"] and not c["rejected"]]
        if nosrc:
            lines.append("  - 자동 마스크 없음(크롭 rect 를 옮기기 전 후보인데 만들 때의 크롭 소스를 찾지 못함) → "
                         f"Compose/Export 가 SO 전체를 합성: {', '.join(nosrc)}")
        lines += [f"  - 썸네일 실패: {f}" for f in thumbs["failed"]]
        report = "\n".join(lines)
        logger.info("%s %s", _TAG, lines[0])
        return {"ui": {"bmk_dp_board": [{"root_key": key, "url": url}], "text": [report]},
                "result": (_handle(root, m, project), report)}


# ══════════════════════════════════════════════════════════════════════
# 노드 10 — Spec Draft (M3, VLM 스펙 초안)
# ══════════════════════════════════════════════════════════════════════
_DRAFT_MEMO: dict = {}  # 평탄화 캐시 {(역할 태그…, abs path, size, mtime_ns): (src_sha, wh, finfo|저장 경로)} — 보드 큐마다 다시 실행됨
_QWEN_DIR = ("LLM", "Qwen-VL", "Qwen3-VL-8B-Instruct")  # models/ 아래(folder_paths 에 LLM 키가 없어 직접 잇는다)
_SHEET_CANVAS = 320
_BOX_COLORS = {"A": (255, 60, 60), "B": (40, 220, 90), "C": (70, 130, 255), "D": (255, 160, 40)}
_SUGGEST_COLOR = (170, 170, 170)
_PICK_COLOR = (40, 220, 90)


def _qwen_resident() -> bool:
    """vlm 모듈 캐시(dvlm._QWEN)에 Qwen 이 올라 있는지(ComfyUI 의 Unload 버튼은 못 봄). keep_loaded 상주 경고와
    _unload_resident_qwen 이 쓴다."""
    return (getattr(dvlm, "_QWEN", None) or {}).get("model") is not None


def _unload_resident_qwen(model_dir: str) -> None:
    """keep_loaded 로 남은 Qwen 을 지금 vlm 이 무엇이든 내린다(openai·mock 으로 바꾼 뒤에도). 올라 있지 않으면 아무것도 안 함.
    dvlm.unload_cached(모듈 캐시를 비우고 torch.cuda.empty_cache)를 부른다 — 백엔드 인스턴스가 필요 없다."""
    if not _qwen_resident():
        return
    unload = getattr(dvlm, "unload_cached", None)
    (unload or dvlm.QwenVLM(model_dir, keep_loaded=False).unload)()


def _draft_pending_text(n: int, style: int) -> str:
    """Run 의 초안 대기 경고: 크롭과 화풍(프로젝트당 1)을 따로 센다 — "22크롭 + 화풍" / "3크롭" / "화풍"."""
    crops = max(0, int(n) - int(style))
    return " + ".join(([f"{crops}크롭"] if crops else []) + (["화풍"] if style else []))


def _draft_plan(root: str, m: dict, approve: str, backend_name: str, *, family: str, model: str, effort: str,
                image_detail: str, draft_round: int, mode: str, only: list[str], concurrency: int, model_dir: str,
                apply_mode: str):
    """dvlm.draft_plan(평탄화 memo = _DRAFT_MEMO) — Studio 계획 라우트와 같은 함수(STUDIO_S3_SPEC §2.4).
    반환 (ctxs, reqs{crop_id | STYLE_ID: Request}, style_req, plan, approval)."""
    return dvlm.draft_plan(root, m, approve, backend_name, family=family, model=model, effort=effort,
                           image_detail=image_detail, draft_round=draft_round, mode=mode, only=only,
                           concurrency=concurrency, model_dir=model_dir, apply_mode=apply_mode, memo=_DRAFT_MEMO)


def _draft_provenance(req, done: dict, **extra) -> dict:
    """스펙 _draft 머리(§5.1) = 그 원장 done 을 만든 요청. drafted 는 done 의 기록 시각이다(벽시계가 아니라서 같은 입력이면
    같은 dict → 파일을 다시 쓰지 않는다). transport = done 의 전송 단계(schema_level·background·schema_enforced).
    mock(_MOCK_API 로 바뀐 openai 포함)의 model 은 "mock" — 요청의 model(키에 들어감)은 그대로 두고 기록만 사실대로."""
    return {"version": dvlm.DRAFT_VERSION, "normalizer": dvlm.NORM_VERSION, "pass_plan": dvlm.PASS_PLAN, "key": req.key,
            "attempt": done["attempt"], "backend": dvlm.BACKEND_NAMES[req.backend],
            "model": "mock" if req.backend == "mock" else req.model,
            "effort": req.effort, "image_detail": req.image_detail, "round": req.draft_round,
            "prompt": dvlm.prompt_fingerprint(), "drafted": done.get("at"), "credits": done.get("credits"),
            "transport": done.get("transport") or {}, **extra}


def _merge_mode(mode: str, only: list[str], spec_id: str) -> str:
    """force 는 only 에 든 크롭(화풍은 "style")만. 나머지의 원장 캐시 병합은 fill 규칙(사람 값을 덮지 않음)."""
    return "fill" if mode == "force" and not dvlm.only_match(only, spec_id) else mode


def _draft_write(root: str, m: dict, ctxs: list, reqs: dict, style_req, *, mode: str, only: list[str], apply_mode: str,
                 adopt: list[str]):
    """§3.3 5단계(순서가 중요): (a) 화풍 done → normalize_style → _project.json, 그다음 크롭마다 지금 키의 done(없으면
    화풍 전파만)을 dvlm.merge_crop → write_spec(preview 는 쓰지 않음). 반환 (merges, checks, drafts, 경고).
    targets 는 style_req 가 None 이라 _project.json 을 쓰지 않고, 그 style_note 가 사람 값일 때만 크롭에 전파한다(§9.2) —
    _project.json 을 읽지 못하면 full 과 같이 크롭 style_note 를 건드리지 않는다("").
    adopt = 채택할 crop_id(dvlm.adopt_select). 쓰지 못한 파일은 vlm/<dvlm.WRITE_MARK> 에 남겨(Project 지문이 바뀜) 다음 큐에서
    원장으로 다시 쓴다(0 호출)."""
    refs_dir = (m.get("source") or {}).get("refs_dir") or ""
    glossary = _glossary(root, m)
    ledger = dvlm.Ledger(root)
    crops = _crop_map(m)
    preview = mode == "preview"
    merges, checks, drafts, warns = {}, {}, {}, []
    failed: list[str] = []      # 쓰기를 시도했지만 못 쓴 스펙(잠김) → vlm/<dvlm.WRITE_MARK>
    attempted: set[str] = set()

    def unwritten(sid: str, e: OSError) -> dvlm.MergeResult:  # "쓰지 않음" 메모 = 보고서 [실패]·review.md 메모
        failed.append(sid)
        return dvlm.MergeResult(None, False, [], False, False, [f"쓰지 않음(파일 잠김) — 다음 큐에서 원장으로 다시 씀(0 호출): {e}"])

    attempted.add(dvlm.PROJECT_SPEC_ID)  # _project 는 실행마다 끝까지 판단한다(쓸 화풍 초안이 없으면 표식에서 빠짐)
    try:
        proj = store.load_spec_override(root, dvlm.PROJECT_SPEC_ID)
    except ValueError as e:  # plan 도 화풍을 skipped 로 보고한다. 전파는 끈다("" = 끔)
        proj = None
        warns.append(f"{dvlm.PROJECT_SPEC_ID}.json 을 읽지 못해 화풍 초안·전파를 건너뜀: {e}")
        # 잠김(읽기 OSError)인데 받은 화풍 초안이 있으면 '못 씀' — JSON 오류는 사람이 고쳐야 하니 큐마다 다시 돌리지 않는다
        if isinstance(e.__cause__, OSError) and style_req is not None and ledger.done(dvlm.STYLE_ID, style_req.key):
            failed.append(dvlm.PROJECT_SPEC_ID)
    done = ledger.done(dvlm.STYLE_ID, style_req.key) if style_req is not None and proj is not None else None
    if done is not None:
        style, sflags = dvlm.normalize_style(done["parsed"], done.get("repairs"))
        obs = done["parsed"].get("observations")
        prov = _draft_provenance(style_req, done, observations=obs if isinstance(obs, list) else [])
        if preview:
            merges[dvlm.PROJECT_SPEC_ID] = dvlm.merge_project(proj, style, mode="preview", provenance=prov)
            proj = dvlm.merge_project(proj, style, mode="refresh", provenance=prov).spec or proj
        else:
            smode = _merge_mode(mode, only, dvlm.STYLE_ID)
            try:
                res, backup = dvlm.write_spec(
                    root, dvlm.PROJECT_SPEC_ID, lambda cur: dvlm.merge_project(cur, style, mode=smode, provenance=prov))
                if backup:
                    res.notes.append(f"백업 {backup}")
                merges[dvlm.PROJECT_SPEC_ID] = res
                proj = store.load_spec_override(root, dvlm.PROJECT_SPEC_ID)
            except OSError as e:
                merges[dvlm.PROJECT_SPEC_ID] = unwritten(dvlm.PROJECT_SPEC_ID, e)
                warns.append(f"{dvlm.PROJECT_SPEC_ID}.json 을 쓰지 못함(다음 큐에서 원장으로 다시 씀, 0 호출) — {e}")
        warns += [f"화풍 초안: {f}" for f in sflags]
    project_style = dvlm.project_style_for(root, apply_mode, proj=proj)  # preview 는 병합한 화풍, 못 읽음(None) = 전파 끔

    for ctx in ctxs:
        cid = ctx.crop_id
        req, crop = reqs.get(cid), crops.get(cid)
        if req is None or crop is None:  # 스펙 JSON 오류·평탄화 실패 — plan 이 skipped 로 보고
            continue
        done = ledger.done(cid, req.key)
        prov = _draft_provenance(req, done, hint=ctx.hint, refs_shown=list(req.shown_refs)) if done is not None else {}
        out: dict = {}
        merge = partial(dvlm.merge_crop, ctx=ctx, req=req, crop=crop, done=done, prov=prov, project_style=project_style,
                        apply_mode=apply_mode, adopt=cid in adopt, backend=req.backend, refs_dir=refs_dir, glossary=glossary,
                        out=out)
        cmode = _merge_mode(mode, only, cid)
        try:
            if preview:
                merges[cid] = merge(ctx.spec, "preview")
            else:
                attempted.add(cid)
                merges[cid], backup = dvlm.write_spec(root, cid, lambda cur: merge(cur, cmode))
                if backup:
                    merges[cid].notes.append(f"백업 {backup}")
        except OSError as e:  # 편집기·백신이 파일을 잡고 있음
            merges[cid] = unwritten(cid, e)
            warns.append(f"{cid}: 스펙 파일을 쓰지 못함(다음 큐에서 원장으로 다시 씀, 0 호출) — {e}")
        except Exception as e:  # 한 크롭의 결함이 나머지 크롭의 쓰기를 막지 않게 — 보고서에 "버그" 로
            logger.exception("%s Spec Draft %s 정규화·병합 실패", _TAG, cid)
            out["check"] = dvlm.RenderCheck(ok=False, problems=[f"버그: 초안 정규화·병합 실패 — {e!r}"], flags=[],
                                            dropped=[], v1=None)
        if out.get("draft") is not None:
            drafts[cid] = out["draft"]
        if "check" in out:
            checks[cid] = out["check"]
    if not preview:  # 이번에 시도하지 못한(건너뛴) 크롭의 지난 표식은 그 크롭이 매니페스트에 남아 있는 동안 이어 둔다
        keep = [s for s in dvlm.unwritten_specs(root) if s not in attempted and (s in crops or s == dvlm.PROJECT_SPEC_ID)]
        _mark_unwritten(root, list(dict.fromkeys(failed + keep)))
    return merges, checks, drafts, warns


def _mark_unwritten(root: str, failed: list[str]) -> None:
    """스펙 쓰기가 실패한(편집기·백신 잠금) 크롭을 vlm/<dvlm.WRITE_MARK> 에 남긴다 → Project 지문이 바뀌어 다음 큐에서 Draft 가
    다시 실행돼 원장으로 다시 쓴다(0 호출). 실패가 없으면 지운다(그 다음 큐 한 번은 지문이 바뀌어 다시 확인만 함)."""
    path = os.path.join(root, dvlm.LEDGER_DIR, dvlm.WRITE_MARK)
    try:
        if failed:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            store.write_json_atomic(path, {"at": time.time_ns(), "specs": failed})
        elif os.path.exists(path):
            os.remove(path)
    except OSError as e:
        logger.warning("%s %s 를 갱신하지 못함(다음 큐가 스펙을 다시 쓰지 않을 수 있음): %s", _TAG, dvlm.WRITE_MARK, e)


def _mark_repoll(root: str, units: list) -> None:
    """이번 실행 뒤에도 created(무료 재폴링 대기)로 남은 단위가 있으면 vlm/<_REPOLL_MARK> 를 다시 쓴다 → Project 지문이
    바뀌어 다음 큐에서 Draft 가 다시 실행(재폴링)된다. 재폴링이 또 실패해 원장이 그대로여도 '다시 큐 = 무료 재폴링' 이 된다."""
    ledger = dvlm.Ledger(root)
    left = [dvlm.approval_id(u) for u in units if ledger.state(u.crop_id, u.key)["status"] == "created"]
    if not left:
        return
    try:
        store.write_json_atomic(os.path.join(root, dvlm.LEDGER_DIR, _REPOLL_MARK), {"at": time.time_ns(), "units": left})
    except OSError as e:
        logger.warning("%s %s 를 쓰지 못함(다음 큐가 재폴링하지 않을 수 있음): %s", _TAG, _REPOLL_MARK, e)


def _draft_files(root: str, review: str, report: str) -> None:
    """vlm/review.md(크롭별 V1 전문·diff·플래그)와 vlm/last_report.txt — 마지막 실행만. specs 밖이라 Project 지문과 무관."""
    d = os.path.join(root, dvlm.LEDGER_DIR)
    os.makedirs(d, exist_ok=True)
    for name, body in (("review.md", review), ("last_report.txt", report)):
        try:
            with open(os.path.join(d, name), "w", encoding="utf-8", newline="\n") as f:
                f.write(body.rstrip("\n") + "\n")
        except OSError as e:  # 뷰어가 잡고 있음 — 내용은 노드 출력에도 있다
            logger.warning("%s %s 를 쓰지 못함: %s", _TAG, name, e)


def _frac_box(b) -> list[float] | None:
    """정규화 bbox(분율 [x0, y0, x1, y1]). 형식이 틀리거나 퇴화했으면 None(그리지 않음)."""
    if not (isinstance(b, (list, tuple)) and len(b) == 4
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in b)):
        return None
    x0, y0, x1, y1 = (float(v) for v in b)
    return [x0, y0, x1, y1] if 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1 else None


def _dashed_rect(d: ImageDraw.ImageDraw, box, color, dash: int = 6) -> None:
    x0, y0, x1, y1 = box
    for a, b, horiz, at in ((x0, x1, True, y0), (x0, x1, True, y1), (y0, y1, False, x0), (y0, y1, False, x1)):
        for s in range(int(a), int(b), dash * 2):
            e = min(s + dash, b)
            d.line([(s, at), (e, at)] if horiz else [(at, s), (at, e)], fill=color, width=2)


def _confidence(rv) -> str:
    c = rv.get("confidence") if isinstance(rv, dict) else None
    return str(c)[:1] if c else "?"


def _draft_sheet(root: str, m: dict, ctxs: list, reqs: dict, pl: dict, drafts: dict, checks: dict,
                 apply_mode: str) -> torch.Tensor:
    """크롭 하나 = 타일 하나: 캔버스 320px + 정규화 bbox(A 빨강 B 초록 C 파랑 D 주황, 제안 타깃 회색 점선) | 보여 준
    레퍼런스 최대 4장 160px(고른 것 초록 테두리 — targets 는 Prepare 가 VLM 이 고른 레퍼런스를 쓰지 않아 생략) + ASCII 두 줄
    (crop_id 상태 타깃별 신뢰도 / 플래그). 크롭이 없으면 1x1. 이번 실행의 Draft(원장 done 정규화)가 없는 크롭은 스펙 파일의
    bbox·_review 를 그리고, 스펙에 bbox 가 없는 채택 타깃(origin adopt)은 _draft.suggestions 에서 tid·문구가 같은 제안의 bbox
    (사람이 _layer_targets 문구를 바꾼 타깃은 그리지 않음)."""
    crops = _crop_map(m)
    ledger = dvlm.Ledger(root)
    pending = {u.crop_id: u.state for u in pl["units"]}
    skipped = {cid for cid, _why in pl["skipped"]}
    tiles = []
    for ctx in ctxs:
        cid = ctx.crop_id
        crop = crops.get(cid)
        if crop is None:
            continue
        draft, chk, spec = drafts.get(cid), checks.get(cid), ctx.spec or {}
        if draft is not None:
            boxes = {tid: t.get("bbox") for tid, t in draft.targets.items()}
            review = draft.review
            sugg = [s.get("bbox") for s in draft.suggestions.get("targets") or [] if isinstance(s, dict)]
            picked = {r for t in draft.targets.values() for r in t.get("refs") or []}
            picked |= {r for rs in (draft.suggestions.get("refs") or {}).values() for r in rs or []}
            flags = list(draft.flags) + [f"{tid}:{f}" for tid, rv in review.items() for f in rv.get("flags") or []]
        else:
            ts = [t for t in spec.get("targets") or [] if isinstance(t, dict)]
            boxes = {str(t.get("id")): t.get("bbox") for t in ts}
            review = {str(t.get("id")): t.get("_review") for t in ts}
            sugg, flags = [], []
            picked = {r for t in ts for r in t.get("refs") or [] if isinstance(r, str)}
            sd = spec.get("_draft") if isinstance(spec.get("_draft"), dict) else {}
            adopted = {t.tid: t.label for t in ctx.targets if t.origin == "adopt"}
            for e in dvlm.suggestion_targets(sd.get("suggestions")):
                if e["suggestion"] is not None and adopted.get(e["tid"]) == e["text"] and boxes.get(e["tid"]) is None:
                    boxes[e["tid"]] = e["suggestion"].get("bbox")
        if chk is not None:
            flags += list(chk.flags) + [f"!{p}" for p in chk.problems]
        if cid in skipped:
            state = "skip"
        elif cid in pending:
            state = "pending" if pending[cid] == "new" else pending[cid]
        elif cid in pl["accepted"]:
            state = "accepted"
        else:
            st = ledger.state(cid, reqs[cid].key)["status"] if cid in reqs else "none"
            state = st if st != "none" else ("kept" if spec.get("_draft") else "-")

        try:
            rgba = _load_rgba(root, crop["source"])
        except (OSError, ValueError):  # 소스가 사라짐 — 이미 쓴 결과·보고서는 그대로 낸다
            rgba = np.zeros((8, 8, 4), np.uint8)
        h, w = rgba.shape[:2]
        cell = _fit(rgba, _SHEET_CANVAS)  # _fit 과 같은 배치로 분율 → 칸 좌표
        s = min(_SHEET_CANVAS / w, _SHEET_CANVAS / h)
        pw, ph = max(1, round(w * s)), max(1, round(h * s))
        ox, oy = (_SHEET_CANVAS - pw) // 2, (_SHEET_CANVAS - ph) // 2
        d = ImageDraw.Draw(cell)

        def to_px(fb):  # 1px 보다 얇은 상자도 그린다(Pillow 는 x1 < x0 · y1 < y0 이면 예외 — 노드 전체가 실패함)
            x0, y0 = ox + fb[0] * pw, oy + fb[1] * ph
            return [x0, y0, max(x0, ox + fb[2] * pw - 1), max(y0, oy + fb[3] * ph - 1)]

        for fb in filter(None, map(_frac_box, sugg)):
            _dashed_rect(d, to_px(fb), _SUGGEST_COLOR)
        palette = list(_BOX_COLORS.values())
        for i, (tid, b) in enumerate(boxes.items()):
            fb = _frac_box(b)
            if fb is None:
                continue
            col = _BOX_COLORS.get(tid, palette[i % len(palette)])
            box = to_px(fb)
            d.rectangle(box, outline=col, width=3)
            d.text((box[0] + 4, box[1] + 2), _ascii(tid), fill=col, font=_font(14))
        cells = [cell]
        for im in ctx.refs[:4]:
            try:
                with Image.open(im.src_path) as pim:
                    rc = _fit(dprompt.flatten_ref(pim), _THUMB)
            except OSError:
                continue
            if apply_mode != "targets" and im.name in picked:
                ImageDraw.Draw(rc).rectangle([0, 0, _THUMB - 1, _THUMB - 1], outline=_PICK_COLOR, width=4)
            cells.append(rc)
        conf = " ".join(f"{tid}:{_confidence(rv)}" for tid, rv in review.items())
        lines = [_ascii(f"{cid} {state}" + (f" conf {conf}" if conf else "")), _ascii(" ".join(flags)[:120] or "-")]
        bad = state in ("failed", "rejected", "retry", "orphan") or (chk is not None and not chk.ok)
        tiles.append(_label_tile(cells, lines, (255, 140, 120) if bad else (220, 220, 220)))
    if not tiles:
        return torch.zeros((1, 1, 1, 3), dtype=torch.float32)
    return _to_image(_grid(tiles, 2))


class BMKDesignPatchSpecDraft:
    """크롭마다 VLM 으로 specs/<crop_id>.json 초안을 쓴다. 승인 해시가 맞을 때만 새로 호출하고, 사람이 고친 값은 덮지 않는다."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "project": (PROJECT_TYPE, {"tooltip": "BMK Design Patch Project 출력(Import PSD 이후)."}),
                "approve": ("STRING", {
                    "default": "",
                    "tooltip": "드라이런 보고의 pending_hash. 지금 대기 중인 초안 목록의 해시와 같을 때만 새로 호출합니다. 비우거나 "
                               "다르면 새 호출 없이 드라이런(건수·예상 크레딧·해시) — 원장에 이미 받은 초안은 write_mode 대로 "
                               "병합·쓰기(force 는 바로 적용). 노드의 'VLM 초안 승인' 버튼이 채우고 "
                               "큐 직후 비웁니다. 값이 있으면 캐시와 무관하게 매번 실행합니다(로그인·VRAM 확보 뒤 같은 해시로 "
                               "다시 승인 가능)."}),
                "vlm": (list(dvlm.VLM_CHOICES), {
                    "default": dvlm.DEFAULT_VLM,
                    "tooltip": "openai = comfy.org 유료(_MOCK_API·BMK_DP_MOCK_API 가 있으면 mock). 기본 gpt-6-sol(effort xhigh, "
                               "크롭당 ≈ 27 크레딧), 품질을 더 올리려면 gpt-6-astra(effort high, ≈ 80). local_qwen = 무료·CUDA "
                               "GPU·VRAM 19.5GiB·ComfyUI 모델을 VRAM 에서 내림(품질 낮음), mock = 시험용(과금 없음)."}),
                "effort": (list(dvlm.EFFORTS), {
                    "default": dvlm.DEFAULT_EFFORT,
                    "tooltip": "추론 effort(openai 만, 캐시 키에 들어감). 모델이 받지 않는 값이면 호출 전에 오류."}),
                "image_detail": (list(dvlm.IMAGE_DETAILS), {
                    "default": "high",
                    "tooltip": "이미지 detail(openai 만, 캐시 키에 들어감). original 이 거부되면 high 로 바꾸세요."}),
                "write_mode": (list(dvlm.WRITE_MODES), {
                    "default": "fill",
                    "tooltip": "fill = 새 크롭·새 타깃만 초안(노드가 쓴 값도 유지). refresh = 입력이 바뀐 크롭을 다시 초안하고 "
                               "손대지 않은 필드를 갱신. preview = refresh 처럼 호출하되 쓰지 않고 diff 만. force = only 필수, "
                               "specs/_backup/ 에 백업 뒤 노드 필드를 새 값으로 — 초안 키마다 1회(같은 키로 다시 실행되면 "
                               "fill 처럼 사람 편집·_accepted 유지, 다시 하려면 draft_round 를 올림). 원장에 이 키의 초안이 "
                               "이미 있으면 approve 없이 첫 큐에서 바로 적용(먼저 보려면 preview). 사람이 고친 값은 force "
                               "외에는 유지."}),
                "only": ("STRING", {
                    "default": "",
                    "tooltip": "승인이 필요한 초안만 거르는 쉼표 필터: crop_id, NNN, style(화풍). 비우면 전부. 무료 복구·원장 "
                               "캐시 병합에는 적용하지 않습니다. force 는 필수."}),
                "draft_round": ("INT", {"default": 0, "min": 0, "max": 99,
                                        "tooltip": "같은 입력으로 다시 초안을 받을 때 올립니다(캐시 키에 들어감, seed 아님)."}),
                "concurrency": ("INT", {"default": 4, "min": 1, "max": 8, "tooltip": "openai 동시 호출 수."}),
                "credit_cap": ("INT", {"default": 0, "min": 0, "max": 100000,
                                       "tooltip": "openai 유료 호출을 띄우기 직전, 이번 실행의 실측 크레딧(보고 안 된 단위는 단위 "
                                                  "최대로 셈) + 이 단위의 최대 추정이 이 값을 넘으면 그 실행의 새 발사를 멈춥니다(나머지는 "
                                                  "대기, 다음 승인). 진행 중 단위의 최대까지 더해야 넘치면 그것들이 끝날 때까지 기다렸다 "
                                                  "다시 판정합니다. 단위 하나의 최대(astra/high ≈ 500)보다 작으면 아무것도 보내지 않습니다. "
                                                  "권장 ≈ 예상 지출 × 1.6 + 단위 최대. 0 = 상한 없음."}),
                "keep_loaded": ("BOOLEAN", {"default": False,
                                            "tooltip": "local_qwen 을 실행 뒤에도 VRAM 에 남깁니다(ComfyUI 의 Unload 버튼으로는 "
                                                       "내려가지 않음 — 끄고 한 번 실행하면 vlm 이 무엇이든 내림)."}),
                "apply": (list(dvlm.APPLY_MODES), {
                    "default": dvlm.DEFAULT_APPLY,
                    "tooltip": "스펙에 쓰는 범위. targets = 타깃 영문 이름(레거시 A 제외 — adopt 로)·bbox 만 쓰고 mode·refs·detail·"
                               "preserve·style_note 등은 쓰지 않음(노드가 전에 쓴 값은 지움, 사람 값은 유지), 화풍 호출 없음 → "
                               "렌더는 레이어명 + 용어집에 VLM 이름만 더함(용어집 없는 한글 라벨 타깃 — 보고서에서 확인). "
                               "full = 모든 필드(참고용 — 가이드의 확인 목록을 볼 것). 캐시 키 밖이라 full → targets 는 0 호출, "
                               "targets → full 은 같은 키 크롭 0 호출이지만 원장에 없는 화풍(화풍 신규)과 채택 등으로 타깃이 "
                               "바뀐 크롭(타깃 추가)은 재초안 승인 대기."}),
                "adopt": ("STRING", {
                    "default": "",
                    "tooltip": "채택할 크롭(쉼표: crop_id, NNN, all). 보고서 [제안] 에 보인 제안 타깃(PSD 레이어명에 문구가 없는 "
                               "타깃)을 specs/<crop_id>.json 의 _layer_targets 에 씀(0 호출) → 다음 큐에서 Import PSD 가 타깃으로 "
                               "더함. 이번 실행에 처음 받은 제안은 채택하지 않음(보고서를 먼저 봄). 큐 직후 비워지는 1회 명령이고, "
                               "쓴 _layer_targets 는 남음(되돌리기 = 스펙에서 지움)."}),
            },
            "hidden": {"auth_token": "AUTH_TOKEN_COMFY_ORG", "api_key": "API_KEY_COMFY_ORG",
                       "usage_source": "COMFY_USAGE_SOURCE", "unique_id": "UNIQUE_ID"},
        }

    RETURN_TYPES = (PROJECT_TYPE, "STRING", "IMAGE")
    RETURN_NAMES = ("project", "report", "sheet")
    OUTPUT_TOOLTIPS = ("프로젝트 핸들(rev 그대로 + 초안 대기 요약 draft — 하류 Run 이 경고).",
                       "드라이런/실행 보고(나쁜 것부터: 실패 > 신뢰 낮음 > 경고 > [제안] > 정상, diff·채택 안내(adopt 에 넣을 값)·"
                       "용어집 제안). apply=targets 면 첫 줄에 [타깃만].",
                       "크롭별 캔버스 + 타깃 bbox(A 빨강 B 초록 C 파랑 D 주황, 제안 회색 점선, 채택 타깃은 그 제안의 bbox) + "
                       "레퍼런스(고른 것 초록 테두리 — full 만).")
    FUNCTION = "run"
    OUTPUT_NODE = True
    CATEGORY = _CATEGORY
    DESCRIPTION = (
        "크롭마다 VLM 으로 specs/<crop_id>.json 초안을 씁니다. 입력은 크롭 캔버스, NNN_ 레퍼런스, 한국어 레이어 라벨, 용어집. "
        "apply=targets(기본)는 타깃 영문 이름·bbox 만 써서 문장 필드 없이 렌더하고(용어집 없는 한글 라벨 타깃만 VLM 이름, 화풍 "
        "호출 없음), full 은 모드·레퍼런스·"
        "detail·must_not·preserve 와 화풍(specs/_project.json, 프로젝트당 1회)까지 씁니다(참고용). VLM 이 제안한 타깃은 adopt 에 "
        "크롭을 적으면 스펙 _layer_targets 에 쓰고(0 호출) 다음 큐의 Import PSD 가 타깃으로 더합니다. "
        "approve 가 지금 대기 목록의 해시와 같을 때만 새로 호출하고(openai 유료, local_qwen 은 ComfyUI 모델을 내림), 아니면 "
        "새 호출 없이 드라이런을 보고합니다(원장에 이미 받은 초안은 write_mode 대로 병합·쓰기 — force 는 바로 적용). "
        "사람이 고친 값은 force 외에는 덮지 않고, 같은 입력이면 다시 큐에 넣어도 호출 0건입니다. "
        "매니페스트는 바꾸지 않습니다(rev 그대로)."
    )
    SEARCH_ALIASES = _ALIASES + ["spec draft", "vlm", "qwen", "openai", "스펙 초안", "초안", "자동 스펙"]

    @classmethod
    def IS_CHANGED(cls, approve="", **kwargs):
        # 승인 큐는 1회용 — 로그인·VRAM 확보 뒤 같은 해시를 다시 눌러도 캐시된 출력이 아니라 실제로 실행되게 매번 다시 실행.
        # 링크된 approve(None)는 값이 보이지 않으니 매번. 빈 approve 는 상수(원장에만 남은 무료 복구는 Project 지문이 잡는다)
        if approve is None or str(approve).strip():
            return float("nan")
        return ""

    @classmethod
    def VALIDATE_INPUTS(cls, vlm=None, effort=None):
        # vlm·effort 조합은 큐 단계에서 거절한다 — run() 이 첫 await 전에 예외를 내면 ComfyUI 가 hidden(comfy.org 토큰)까지
        # 입력값을 execution_error 에 적어 /history 에 남긴다. 링크된 값(None)은 run 이 다시 검사한다. 이 두 입력은 코어의
        # 콤보 검사를 건너뛰므로 목록 검사도 여기서(image_detail·write_mode 는 코어가 검사)
        try:
            dvlm.check_vlm_effort(vlm, effort)
        except ValueError as e:
            return str(e)
        return True

    async def run(self, project, approve, vlm, effort, image_detail, write_mode, only, draft_round, concurrency,
                  credit_cap, keep_loaded, apply=dvlm.DEFAULT_APPLY, adopt="", auth_token=None, api_key=None,
                  usage_source=None, unique_id=None, **_stale):
        # **_stale: 옛 워크플로가 지금은 없는 입력을 링크해 와도 인자 바인딩(첫 await 전)에서 실패하지 않게(hidden 토큰 기록 방지).
        # apply·adopt 의 기본값도 같은 이유 — 검증을 거치지 않고 실행기에 들어온 프롬프트에 그 입력이 없을 때
        t0 = time.time()
        # 첫 줄부터 await: 이후 예외는 ComfyUI 의 대기 노드 경로로 보고되어 입력값(hidden 토큰)이 기록되지 않는다
        # (코어가 run 전에 입력 목록을 자르다 나는 예외는 남은 한계 — M3_SPEC §11.6 F1)
        root, m = await asyncio.to_thread(_open, project)
        # _open 뒤 run 전체(예외·인터럽트·취소·finally 포함 — 핸들을 못 열면 루트를 몰라 표시하지 않음)를 보드 실행 표시로
        # — Studio 계획은 실행 중 Draft 의 해시를 내지 않고 진행 표시는 시작 시각 뒤 원장만 센다
        with board.draft_active(root):
            dvlm.check_widgets(vlm, effort, image_detail, write_mode)
            if apply not in dvlm.APPLY_MODES:
                raise ValueError(f"{_TAG} apply 값이 잘못되었습니다: {apply!r} (선택지: {', '.join(dvlm.APPLY_MODES)})")
            adopt_ids, adopt_notes = dvlm.adopt_select(adopt, [c["id"] for c in m["crops"]])
            family, model = dvlm.parse_vlm_choice(vlm)
            mock_forced = family == "openai" and runner.mock_enabled(root)
            if mock_forced:
                family = "mock"
            tokens = dvlm.parse_only(only)
            idle = write_mode == "force" and not tokens  # force 는 only 필수 — 호출·쓰기 없이 보고만
            approve = str(approve or "").strip()
            key = _board_key(root)
            # 백엔드를 먼저 정한다: 승인 해시·실행·ui backend 를 한 백엔드가 정해야 MOCK 드라이런 해시가 유료 실행을 승인하지 않는다
            model_dir = os.path.join(folder_paths.models_dir, *_QWEN_DIR)
            backend = dvlm.make_vlm(family, root, {"auth_token": auth_token, "api_key": api_key, "usage_source": usage_source,
                                                   "unique_id": unique_id}, model_dir=model_dir, keep_loaded=bool(keep_loaded))
            cfg = {"family": family, "model": model, "effort": effort, "image_detail": image_detail,
                   "draft_round": int(draft_round), "mode": write_mode, "only": tokens, "concurrency": int(concurrency),
                   "model_dir": model_dir, "apply_mode": apply}
            watch = []  # 네트워크 단위(재폴링·새 호출) — 끝난 뒤 created 로 남으면 재폴링 표시(_mark_repoll)
            try:
                ctxs, reqs, style_req, pl, ap = await asyncio.to_thread(_draft_plan, root, m, approve, backend.name, **cfg)
                watch = [] if idle else [u for u in ap["to_run"] if u.state != "reparse"]
                has_auth = family != "openai" or backend.has_auth()
                # 인증 없이 보내면 401 — 401 로 처리되기 전에 막는다(원장 재파싱만 로컬이라 남김)
                to_run = [] if idle else [u for u in ap["to_run"] if has_auth or u.state == "reparse"]
                auth_held = 0 if idle else len(ap["to_run"]) - len(to_run)
                run_rep = None
                if to_run:
                    run_rep = await self._execute(root, to_run, reqs, backend, model=model, effort=effort,
                                                  image_detail=image_detail, concurrency=int(concurrency),
                                                  credit_cap=int(credit_cap), unique_id=unique_id)
                merges, checks, drafts, warns = ({}, {}, {}, []) if idle else await asyncio.to_thread(
                    _draft_write, root, m, ctxs, reqs, style_req, mode=write_mode, only=tokens, apply_mode=apply,
                    adopt=adopt_ids)
                if to_run or (write_mode != "preview" and any(r.spec is not None for r in merges.values())):
                    # 원장·스펙 _draft 가 바뀜 → 지금 상태로 다시 계획(ui·보고서의 해시 = 다음 큐가 계산할 해시)
                    ctxs, reqs, style_req, pl_after, ap_after = await asyncio.to_thread(
                        _draft_plan, root, m, "", backend.name, **cfg)
                else:
                    pl_after, ap_after = pl, dvlm.approval(pl, "", backend_name=backend.name, concurrency=int(concurrency))
                m = store.load_manifest(root)
                shown_model = "mock" if family == "mock" else model
                shown_effort = effort if family == "openai" else None
                header = {"backend": backend.name, "family": family, "model": shown_model, "effort": shown_effort,
                          "image_detail": image_detail if family == "openai" else None, "write_mode": write_mode,
                          "only": tokens, "approve": approve, "approved_now": ap["approved_now"],
                          "pending_hash_before": ap["pending_hash"], "blocked_before": len(ap["blocked"]),
                          "mock_forced": mock_forced, "draft_round": int(draft_round), "concurrency": int(concurrency),
                          "credit_cap": int(credit_cap), "rev": m["rev"], "seconds": round(time.time() - t0, 1),
                          "apply": apply}
                lines = dvlm.format_report(header=header, plan=pl_after, approval=ap_after, run=run_rep, merges=merges,
                                           checks=checks, drafts=drafts, ctxs=ctxs).splitlines()
                if keep_loaded and (family == "local_qwen" or _qwen_resident()):  # 다른 vlm 이어도 남아 있는 Qwen 은 알린다
                    lines.insert(1, dvlm.QWEN_RESIDENT_WARNING)
                if idle:
                    lines.append("force 는 only(crop_id·NNN·style)가 필요합니다 — 호출·쓰기 없이 보고만 함"
                                 + (" (adopt 도 쓰지 않음)" if adopt_ids else ""))
                lines += adopt_notes
                if not has_auth and (pl_after["units"] or auth_held):
                    lines.append("주의: comfy.org 로그인 필요 — 로그인(또는 API 키) 정보가 없어 호출하지 않았습니다. "
                                 "ComfyUI 에 로그인한 뒤 큐에 넣으세요")
                lines += [f"  - {w}" for w in warns]
                text = "\n".join(lines)
                await asyncio.to_thread(_draft_files, root, dvlm.review_markdown(ctxs, merges, checks, drafts, apply), text)
                sheet = await asyncio.to_thread(_draft_sheet, root, m, ctxs, reqs, pl_after, drafts, checks, apply)
                logger.info("%s %s", _TAG, lines[0] if lines else "Spec Draft")
                entry = {"pending_hash": ap_after["pending_hash"], "pending": len(pl_after["units"]),
                         "reasons": ap_after["reasons"], "orphan_retries": ap_after["orphan_retries"],
                         "est_credits": list(ap_after["est_credits"]), "est_usd": list(ap_after["est_usd"]),
                         "est_seconds": ap_after["est_seconds"], "backend": backend.name, "model": shown_model,
                         "effort": shown_effort, "approved_now": ap["approved_now"], "root_key": key,
                         "refresh_available": len(pl_after["refresh_available"]),
                         # 인증 중지(Studio 끝 줄 — 로그인 필요): auth_held = 로그인 정보가 없어 보내지 않은 단위 수, stopped_reason auth =
                         # 실행 중 401/402/403 부터 보내지 않음
                         "auth_held": auth_held, "stopped_reason": run_rep.stopped_reason if run_rep is not None else "none"}
                # pending_style = 대기 중 화풍 단위(0/1) — Run 경고가 크롭과 화풍을 따로 센다. unwritten = 스펙 파일에 아직 못 쓴
                # 변경(잠김, vlm/<dvlm.WRITE_MARK>, 지금 매니페스트의 크롭·_project 만) — 초안 대기와 따로 Run 이 경고한다.
                # held = only 로 보류된 대기(승인 목록 밖이라 pending 에 없음), mock_specs = vlm=mock 으로 쓴 초안이 남은 스펙
                # (실제 백엔드면 'mock 초안 교체' 대기라 pending 이 센다)
                live = {c["id"] for c in m["crops"]} | {dvlm.PROJECT_SPEC_ID}
                unwritten = sum(1 for s in await asyncio.to_thread(dvlm.unwritten_specs, root) if s in live)
                held = pl_after["filtered"]
                mock_specs = await asyncio.to_thread(dvlm.mock_drafted, root, ctxs, apply) if family == "mock" else 0
                branch = {"pending": entry["pending"], "pending_hash": entry["pending_hash"],
                          "refresh_available": entry["refresh_available"],
                          "pending_style": sum(1 for u in pl_after["units"] if u.crop_id == dvlm.STYLE_ID),
                          **({"unwritten": unwritten} if unwritten else {}),
                          **({"held": len(held), "held_style": int(dvlm.STYLE_ID in held)} if held else {}),
                          **({"mock_specs": mock_specs} if mock_specs else {})}
                return {"ui": {dvlm.UI_KEY: [entry], "text": [text]},
                        "result": (_handle(root, m, project, draft=branch), text, sheet)}
            finally:
                if watch:  # 취소·폴링 실패·로그인 없음 뒤에도 — 다음 큐가 Draft 를 다시 실행해 무료 재폴링하게
                    await asyncio.to_thread(_mark_repoll, root, watch)
                if not keep_loaded:  # 0 호출 실행이어도, vlm 을 바꿨어도 — 지난 실행이 keep_loaded 로 남긴 모델까지 내린다
                    if family == "local_qwen":
                        backend.unload()
                    else:
                        _unload_resident_qwen(model_dir)

    @staticmethod
    async def _execute(root, units, reqs, backend, *, model, effort, image_detail, concurrency, credit_cap, unique_id):
        """dvlm.run_units + 진행 표시. 취소는 InterruptProcessingException 으로 다시 올린다 — 도착분은 원장(vlm/)에 있어
        다음 실행에서 0 호출로 병합되고, 폴링 중이던 것은 created 로 남아 무료 재폴링된다."""
        try:
            return await dvlm.run_units(root, units, reqs, backend, model=model, effort=effort, image_detail=image_detail,
                                        concurrency=concurrency, credit_cap=credit_cap,
                                        progress_cb=_progress_cb(len(units), unique_id))
        except (Exception, comfy.model_management.InterruptProcessingException) as e:  # IPE 는 BaseException
            if not isinstance(e, runner._interrupt_types()):
                raise
            logger.warning("%s Spec Draft 취소 — 도착한 결과는 원장(vlm/)에 있음(다음 실행에서 0 호출로 병합)", _TAG)
            if isinstance(e, comfy.model_management.InterruptProcessingException):
                raise
            raise comfy.model_management.InterruptProcessingException() from e


# ══════════════════════════════════════════════════════════════════════
# 노드 11 — Ref Cut (Studio S2, 무료 — 보드가 /api/prompt 로 이 노드 하나만 큐에 넣는다)
# ══════════════════════════════════════════════════════════════════════
def _sam3_ckpt_names() -> list[str]:
    """checkpoints 중 이름에 'sam3' 가 든 것(sam3._sam3_checkpoints 는 하나도 없으면 전체를 돌려주므로 쓰지 않는다)."""
    return [n for n in folder_paths.get_filename_list("checkpoints") if "sam3" in n.lower()]


class BMKDesignPatchRefCut:
    """Studio 레퍼런스 편집기의 SAM3 컷: 요청 파일(derived/refcut/<req>.req.json) → 시트 창 → SAM3 → 결과 파일."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "root": ("STRING", {"default": "", "tooltip": "보드 레지스트리 키(소문자 16진 12자 — Review 노드가 등록한 프로젝트)."}),
                "req": ("STRING", {"default": "", "tooltip": "컷 요청 ID(소문자 16진 32자) — 보드 POST /refs/cut 이 만든 요청 파일."}),
            },
        }

    RETURN_TYPES = ()
    FUNCTION = "run"
    OUTPUT_NODE = True
    CATEGORY = _CATEGORY
    DESCRIPTION = (
        "Studio 가 큐에 넣는 노드 — 그래프에 둘 일 없음. 보드 레퍼런스 편집기의 SAM3 컷 요청 하나를 로컬 SAM3(BMK SAM3 Mask Layers 와 "
        "같은 모델 한 벌)로 실행해 프로젝트의 derived/refcut/ 에 마스크·결과 파일을 씁니다. 유료 호출 없음."
    )
    SEARCH_ALIASES = _ALIASES + ["ref cut", "reference cutout", "sam3", "레퍼런스 컷", "컷아웃"]

    def run(self, root, req):
        path = store.resolve_board_root(root)
        if path is None:
            raise ValueError(f"{_TAG} 보드에 등록되지 않은 프로젝트 키입니다(Review 노드를 한 번 실행): {root!r}")
        if not isinstance(req, str) or not refs._REQ_RE.fullmatch(req):
            raise ValueError(f"{_TAG} 컷 요청 ID 형식이 잘못되었습니다([0-9a-f] 32자): {req!r}")
        t0 = time.time()
        try:
            r = refs.load_cut_request(path, req)
            if r["method"] != "sam3":
                raise ValueError(f"Ref Cut 노드는 sam3 요청만 실행합니다(사각형·다각형은 보드가 바로 계산): {r['method']}")
            names = _sam3_ckpt_names()
            ckpt = r["ckpt"] or (names[0] if names else None)
            if ckpt is None:
                raise ValueError("checkpoints 폴더에 이름에 sam3 가 든 체크포인트가 없습니다")
            rgb, alpha, window = refs.cut_window(refs.sheet_array(path, r["sheet"], r["layer"]), r["rect"], r["pad"])
            wx, wy = window[:2]
            box = [r["rect"][0] - wx, r["rect"][1] - wy, r["rect"][2] - wx, r["rect"][3] - wy]
            points = [(x - wx, y - wy, lab) for x, y, lab in r["points"]]
            warnings = []
            if r["text"]:
                # 텍스트 = 감지 경로(sam3.segment 의 _run_sam3 — 점을 보지 않음) → 자동 양점을 넣지 않고 auto_point null(보드가 회색 점을 그리지 않게)
                auto = None
                warnings.append("텍스트가 있으면 점(자동 양점 포함)은 쓰지 않음(감지 경로)")
            else:
                # 상자만은 SAM3.1 이 못 맞춤 → 상자 가운데에 가장 가까운 불투명 픽셀을 사람 점이 있어도 늘 맨 앞에(§3.2 — U0 고침 클릭 실측이 이 조건)
                auto = refs.auto_point(alpha > 127, box)
                if auto is None:
                    warnings.append("선택 안이 모두 투명해 자동 양점 없음")
                else:
                    points.insert(0, auto)
            raw = sam3.segment(ckpt, torch.from_numpy(rgb).float() / 255.0, box=box, points=points, text=r["text"] or None,
                               refine_iterations=r["refine"], multimask=r["multimask"] == "always")
            sel, stats = refs.finish_mask(raw, r, window, alpha)
            res = refs.save_cut_result(path, req, sel, {"window": window, "rect": r["rect"], **stats,
                                                        "sec": round(time.time() - t0, 2), "ckpt": ckpt, "warnings": warnings,
                                                        "auto_point": None if auto is None else [auto[0] + wx, auto[1] + wy]})
        except (Exception, comfy.model_management.InterruptProcessingException) as e:  # IPE 는 BaseException — 취소도 보드가 기다리지 않게
            refs.save_cut_error(path, req, "취소됨" if isinstance(e, comfy.model_management.InterruptProcessingException) else str(e))
            raise
        logger.info("%s Ref Cut %s: 면적 %.3f · 조각 %d · %.2fs", _TAG, req[:8], res["area_frac"], res["n_parts"], res["sec"])
        return {}

    @classmethod
    def IS_CHANGED(cls, root="", req="", **kwargs):
        return req


def _register_board_routes() -> None:
    """보드 라우트를 ComfyUI 서버에 한 번 등록한다(서버의 add_routes 전, 노드 모듈 import 때). 서버가 없으면(테스트) 건너뜀."""
    srv = _server()
    if srv is not None:
        board.register_routes(srv.routes, base_dir=folder_paths.get_input_directory())


def _restore_board_prefs() -> None:
    """재시작 뒤 등록된 프로젝트마다 보드 분석 설정(board._PREF)을 board_pref.json(마지막 Analyze·Review)에서 되살린다 — 비어 있으면
    보드는 후보의 마지막 삽입 항목을 보는데, 설정을 A→B→A 로 되돌린 프로젝트에서는 그것이 B 라 Analyze·Review 전에 연 Studio 가
    '마스크 편집 기준 바뀜'을 잘못 띄우고 편집을 B 기준으로 기록했다(리뷰 analysis-r2-01). 이미 들어 있으면 두지 않는다."""
    for r in store.known_roots():
        prm = store.load_board_pref(r["root"])
        if prm is not None:
            board._PREF.setdefault(r["key"], prm)


_register_board_routes()
_restore_board_prefs()
board.set_sam3_ckpts(_sam3_ckpt_names())  # 보드는 folder_paths 를 import 하지 않는다(B0) — 재시작 전 새로 넣은 파일은 안 보임
board.set_qwen_dir(os.path.join(folder_paths.models_dir, *_QWEN_DIR))  # Studio Draft 계획의 local_qwen 키(Spec Draft 와 같은 경로)
board.set_output_dir(folder_paths.get_output_directory())  # Studio [출력 폴더 열기](배치 3-5a)가 이 안만 연다


# ══════════════════════════════════════════════════════════════════════
NODE_CLASS_MAPPINGS = {
    "BMKDesignPatchProject": BMKDesignPatchProject,
    "BMKDesignPatchImportPSD": BMKDesignPatchImportPSD,
    "BMKDesignPatchCandidateIn": BMKDesignPatchCandidateIn,
    "BMKDesignPatchSpecDraft": BMKDesignPatchSpecDraft,
    "BMKDesignPatchPrepare": BMKDesignPatchPrepare,
    "BMKDesignPatchRun": BMKDesignPatchRun,
    "BMKDesignPatchAnalyze": BMKDesignPatchAnalyze,
    "BMKDesignPatchReview": BMKDesignPatchReview,
    "BMKDesignPatchCompose": BMKDesignPatchCompose,
    "BMKDesignPatchExportPSD": BMKDesignPatchExportPSD,
    "BMKDesignPatchRefCut": BMKDesignPatchRefCut,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "BMKDesignPatchProject": "BMK Design Patch Project",
    "BMKDesignPatchImportPSD": "BMK Design Patch Import PSD",
    "BMKDesignPatchCandidateIn": "BMK Design Patch Candidate In",
    "BMKDesignPatchSpecDraft": "BMK Design Patch Spec Draft",
    "BMKDesignPatchPrepare": "BMK Design Patch Prepare",
    "BMKDesignPatchRun": "BMK Design Patch Run",
    "BMKDesignPatchAnalyze": "BMK Design Patch Analyze",
    "BMKDesignPatchReview": "BMK Design Patch Review",
    "BMKDesignPatchCompose": "BMK Design Patch Compose",
    "BMKDesignPatchExportPSD": "BMK Design Patch Export PSD",
    "BMKDesignPatchRefCut": "BMK Design Patch Ref Cut (Studio)",
}
