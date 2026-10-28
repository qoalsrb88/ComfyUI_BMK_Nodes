// bmk_native_file_dialog.js — 워크플로 저장 이름창·열기를 윈도우 기본 파일 창으로.
//
// 배경
// ────
// ComfyUI 의 저장은 사용자 폴더 workflows/ 라이브러리에 쓰는 구조라 파일명만 받는 입력창을 띄우고,
// 열기(Ctrl+O)는 브라우저 파일 선택이라 시작 폴더를 정할 수 없고 연 워크플로가 라이브러리와 연결되지 않는다.
// 같은 PC에서 접속했을 때 서버(bmk_native_file_dialog.py)가 윈도우 파일 창을 띄우고, 이 확장이
// 그 결과를 프론트엔드의 원래 저장·열기 흐름에 넘긴다.
//
// 동작
// ────
// - 저장: 워크플로의 promptSave(파일명 입력창)를 바꾼다. 저장·다른 이름으로 저장·탭 닫을 때 저장이 모두
//   이 함수를 거치므로 한 곳만 바꾸면 된다. 고른 폴더·이름을 원래 흐름에 돌려주므로 덮어쓰기 확인,
//   라이브러리 목록, 탭 상태는 원래대로 동작한다. 라이브러리 밖을 고르면 서버가 안내하고 다시 연다.
// - 열기: app.ui.loadFile(Open 명령·Ctrl+O)을 바꾼다. 라이브러리 안 .json 은 사이드바에서 연 것처럼
//   라이브러리 탭으로 열고, 이미지·영상·바깥 파일은 원래 열기와 같이 처리한다.
// - 시작 폴더: 저장된 워크플로면 그 폴더, 아니면 이 브라우저에서 마지막으로 쓴 라이브러리 폴더.
// - 다른 PC에서 접속했거나(창이 서버 화면에 뜨므로) 윈도우가 아니면 원래 입력창·파일 선택을 쓴다.
//
// 설정 (Settings → BMK → Native File Dialog)
//   - 윈도우 파일 창 사용 : on/off (끄면 원래 동작)
//
// 주의
// ────
// - 프론트엔드 내부(ComfyWorkflow.promptSave, saveWorkflowAs 의 경로 계산 순서)에 기대는 패치다.
//   프론트엔드 1.57 기준. 구조가 바뀌면 원래 입력창으로 돌아가거나 저장 폴더가 현재 폴더로 바뀔 수 있다.
//
// 버전 이력:
//   v1 (2026-10): 저장 이름창·열기를 윈도우 기본 파일 창으로.

import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const _TAG = "[ComfyUI_BMK_Nodes::NativeFileDialog]";

const API = "/bmk/native_dialog";
const SETTING_ENABLED = "BMK.NativeFileDialog.Enabled";
const LAST_DIRECTORY_KEY = "BMK.NativeFileDialog.LastDirectory";
const WORKFLOWS_PREFIX = "workflows/";

// 서버가 이 PC의 창을 쓸 수 없다고 답하면(원격 접속 등) 페이지를 새로 열 때까지 원래 동작만 쓴다.
let _unsupported = false;

function enabled() {
    if (_unsupported) return false;
    const v = app.extensionManager?.setting?.get?.(SETTING_ENABLED);
    return v === undefined || v === null ? true : v;
}

function toast(severity, summary, detail) {
    try {
        app.extensionManager?.toast?.add?.({ severity, summary, detail, life: 4000 });
    } catch (err) {
        console.debug(`${_TAG} toast unavailable`, err);
    }
}

function lastDirectory() {
    try {
        return localStorage.getItem(LAST_DIRECTORY_KEY);
    } catch {
        return null;
    }
}

function rememberDirectory(directory) {
    try {
        localStorage.setItem(LAST_DIRECTORY_KEY, directory);
    } catch {
        // 저장소를 못 쓰면 다음에 라이브러리 루트에서 열 뿐이다.
    }
}

function directoryOf(path) {
    return path.slice(0, path.lastIndexOf("/"));
}

async function requestDialog(kind, body) {
    const res = await api.fetchApi(`${API}/${kind}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status} ${res.statusText}`);
    return res;
}

// 서버 답 가운데 결과가 아닌 것을 처리한다. 원래 동작으로 넘어가야 하면 true.
function handleStatus(data) {
    if (data.status === "unsupported") {
        _unsupported = true;
        return true;
    }
    if (data.status === "error") {
        console.error(`${_TAG} 파일 창 실패`, data.message);
        toast("error", "윈도우 파일 창을 띄우지 못했습니다", data.message);
        return true;
    }
    if (data.status === "busy") {
        toast("warn", "파일 창이 이미 열려 있습니다", "작업 표시줄에서 열린 창을 찾아 닫거나 마저 진행하세요.");
    }
    return false;
}

// ─── 저장: 파일명 입력창 ─────────────────────────────────────────

// ComfyWorkflow 클래스는 확장에 노출되지 않아 열린 워크플로 객체에서 프로토타입을 찾는다. 바꿨으면 true.
function patchPromptSave() {
    const sample = app.extensionManager.workflow.activeWorkflow;
    let proto = sample && Object.getPrototypeOf(sample);
    while (proto && !Object.hasOwn(proto, "promptSave")) proto = Object.getPrototypeOf(proto);
    if (!proto) return false;
    if (proto.promptSave.bmkNativeDialog) return true;

    const original = proto.promptSave;
    async function promptSave() {
        // 서브그래프 블루프린트(subgraphs/) 등 라이브러리 워크플로가 아닌 것은 원래 입력창.
        if (!enabled() || !this.path.startsWith(WORKFLOWS_PREFIX)) return original.call(this);
        let data;
        try {
            const res = await requestDialog("save", {
                directory: this.isTemporary ? lastDirectory() : this.directory,
                filename: this.filename,
            });
            data = await res.json();
        } catch (err) {
            console.error(`${_TAG} 저장 창 요청 실패`, err);
            toast("error", "윈도우 파일 창을 띄우지 못했습니다", String(err));
            return original.call(this);
        }
        if (data.status !== "ok") return handleStatus(data) ? original.call(this) : null;

        rememberDirectory(data.directory);
        if (data.directory !== this.directory) {
            // saveWorkflowAs 는 promptSave 가 끝난 직후 다른 await 없이 workflow.directory + "/" + 이름으로
            // 새 경로를 만든다. 그 순간에 고른 폴더가 보이도록 바꿔 두고, 마이크로태스크가 다 돈 뒤(setTimeout)
            // 경로가 그대로면 원래 폴더로 되돌린다(임시 워크플로는 이름 바꾸기로 경로·폴더가 이미 새 값이다).
            const { path, directory } = this;
            this.directory = data.directory;
            setTimeout(() => {
                if (this.path === path) this.directory = directory;
            });
        }
        return data.filename;
    }
    promptSave.bmkNativeDialog = true;
    proto.promptSave = promptSave;
    return true;
}

// ─── 열기 ───────────────────────────────────────────────────────

async function openLibraryWorkflow(path) {
    const store = app.extensionManager.workflow;
    let workflow = store.getWorkflowByPath(path);
    if (!workflow) {
        // 다른 곳에서 막 만든 파일이면 목록에 아직 없다.
        await store.syncWorkflows();
        workflow = store.getWorkflowByPath(path);
    }
    if (!workflow) throw new Error(`워크플로 목록에서 찾지 못함: ${path}`);
    if (store.isActive(workflow)) return;
    // 사이드바에서 열 때(workflowService.openWorkflow)와 같은 순서: 내용 불러오기 → 그래프에 올리며 탭 연결.
    if (!workflow.isLoaded) await workflow.load();
    await app.loadGraphData(workflow.activeState, true, true, workflow);
}

// 창을 띄웠으면(취소 포함) true, 원래 열기를 써야 하면 false.
async function openWithNativeDialog() {
    const active = app.extensionManager.workflow.activeWorkflow;
    const directory =
        active && !active.isTemporary && active.path.startsWith(WORKFLOWS_PREFIX) ? active.directory : lastDirectory();
    const accept = document.getElementById("comfy-file-input")?.accept ?? "";
    let res;
    try {
        res = await requestDialog("open", {
            directory,
            extensions: accept.split(",").filter((e) => e.startsWith(".")),
        });
    } catch (err) {
        console.error(`${_TAG} 열기 창 요청 실패`, err);
        toast("error", "윈도우 파일 창을 띄우지 못했습니다", String(err));
        return false;
    }
    const filename = res.headers.get("X-BMK-Filename");
    if (filename) {
        const blob = await res.blob();
        await app.handleFile(new File([blob], decodeURIComponent(filename), { type: blob.type }), "file_button");
        return true;
    }
    const data = await res.json();
    if (data.status !== "ok") return !handleStatus(data);
    rememberDirectory(directoryOf(data.path));
    await openLibraryWorkflow(data.path);
    return true;
}

function patchLoadFile() {
    const original = app.ui.loadFile;
    app.ui.loadFile = async () => {
        if (!enabled()) return original();
        try {
            if (!(await openWithNativeDialog())) original();
        } catch (err) {
            console.error(`${_TAG} 열기 실패`, err);
            toast("error", "워크플로를 열지 못했습니다", String(err));
        }
    };
}

app.registerExtension({
    name: "BMK.NativeFileDialog",

    settings: [
        {
            id: SETTING_ENABLED,
            name: "워크플로 저장·열기에 윈도우 파일 창 사용",
            category: ["BMK", "Native File Dialog", "Enabled"],
            tooltip:
                "저장할 때 파일명 입력창 대신, 열기(Ctrl+O)에 브라우저 파일 선택 대신 윈도우 파일 창을 띄웁니다. " +
                "ComfyUI 를 같은 PC의 브라우저로 열었을 때만 동작하고, 끄면 원래 동작입니다.",
            type: "boolean",
            defaultValue: true,
        },
    ],

    setup() {
        patchLoadFile();
        if (patchPromptSave()) return;
        // setup 때는 아직 열린 워크플로가 없을 수 있다. 워크플로 스토어(Pinia)가 바뀔 때 다시 시도한다.
        const stop = app.extensionManager.workflow.$subscribe(() => {
            if (patchPromptSave()) stop();
        });
    },
});
