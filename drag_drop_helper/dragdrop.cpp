#define UNICODE
#define _UNICODE
#include <windows.h>
#include <windowsx.h>
#include <shlobj.h>
#include <string>
#include <thread>
#include <atomic>
#include <mutex>
#include <sstream>

// ── Default wav path ──────────────────────────────────────
static std::wstring WAV_PATH = L"D:\\projects\\audio-browser\\audio\\clap-808.wav";
static std::mutex   gPathMutex;

static const int WND_W    = 200;
static const int WND_H    = 60;
static const int BORDER_PX = 2;

// ── Named pipe name ───────────────────────────────────────
static const wchar_t* PIPE_NAME = L"\\\\.\\pipe\\DragDropCtrl";

// ── IDropSource ───────────────────────────────────────────
class DropSource : public IDropSource {
    ULONG mRef = 1;
public:
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID riid, void** ppv) override {
        if (riid == IID_IUnknown || riid == IID_IDropSource) {
            *ppv = this; AddRef(); return S_OK;
        }
        *ppv = nullptr; return E_NOINTERFACE;
    }
    ULONG STDMETHODCALLTYPE AddRef()  override { return ++mRef; }
    ULONG STDMETHODCALLTYPE Release() override { return --mRef; }

    HRESULT STDMETHODCALLTYPE QueryContinueDrag(BOOL esc, DWORD keys) override {
        if (esc) return DRAGDROP_S_CANCEL;
        if (!(keys & MK_LBUTTON)) return DRAGDROP_S_DROP;
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE GiveFeedback(DWORD) override {
        return DRAGDROP_S_USEDEFAULTCURSORS;
    }
};

// ── Drag ─────────────────────────────────────────────────
void StartDrag(HWND hwnd, const std::wstring& path) {
    size_t pathBytes = (path.size() + 2) * sizeof(wchar_t);
    size_t total     = sizeof(DROPFILES) + pathBytes;

    HGLOBAL hGlobal = GlobalAlloc(GHND, total);
    if (!hGlobal) return;

    auto* df   = (DROPFILES*)GlobalLock(hGlobal);
    df->pFiles = sizeof(DROPFILES);
    df->fWide  = TRUE;
    df->pt     = { 0, 0 };
    df->fNC    = FALSE;

    wchar_t* dst = (wchar_t*)((BYTE*)df + sizeof(DROPFILES));
    wcscpy_s(dst, path.size() + 1, path.c_str());
    GlobalUnlock(hGlobal);

    IDataObject* dataObj = nullptr;
    if (FAILED(SHCreateDataObject(nullptr, 0, nullptr, nullptr,
                                  IID_IDataObject, (void**)&dataObj))) {
        GlobalFree(hGlobal);
        return;
    }

    FORMATETC fmt = { CF_HDROP, nullptr, DVASPECT_CONTENT, -1, TYMED_HGLOBAL };
    STGMEDIUM med = {};
    med.tymed   = TYMED_HGLOBAL;
    med.hGlobal = hGlobal;

    dataObj->SetData(&fmt, &med, TRUE);

    DropSource src;
    DWORD effect = 0;
    DoDragDrop(dataObj, &src, DROPEFFECT_COPY, &effect);

    dataObj->Release();
}

// ── Layered paint ─────────────────────────────────────────
void PaintLayered(HWND hwnd) {
    int w = WND_W, h = WND_H;

    HDC hdcScreen = GetDC(nullptr);
    HDC hdcMem    = CreateCompatibleDC(hdcScreen);

    BITMAPINFO bi = {};
    bi.bmiHeader.biSize        = sizeof(BITMAPINFOHEADER);
    bi.bmiHeader.biWidth       = w;
    bi.bmiHeader.biHeight      = -h;
    bi.bmiHeader.biPlanes      = 1;
    bi.bmiHeader.biBitCount    = 32;
    bi.bmiHeader.biCompression = BI_RGB;

    BYTE* bits = nullptr;
    HBITMAP hbm    = CreateDIBSection(hdcMem, &bi, DIB_RGB_COLORS, (void**)&bits, nullptr, 0);
    HBITMAP oldBmp = (HBITMAP)SelectObject(hdcMem, hbm);

    for (int y = 0; y < h; y++) {
        for (int x = 0; x < w; x++) {
            int  i      = (y * w + x) * 4;
            bool border = (x < BORDER_PX || x >= w - BORDER_PX ||
                           y < BORDER_PX || y >= h - BORDER_PX);
            BYTE a = border ? 255 : 1;
            bits[i + 0] = (100 * a) / 255;
            bits[i + 1] = (180 * a) / 255;
            bits[i + 2] = (255 * a) / 255;
            bits[i + 3] = a;
        }
    }

    POINT ptSrc = { 0, 0 };
    SIZE  size  = { w, h };
    BLENDFUNCTION bf = { AC_SRC_OVER, 0, 255, AC_SRC_ALPHA };
    UpdateLayeredWindow(hwnd, hdcScreen, nullptr, &size, hdcMem, &ptSrc, 0, &bf, ULW_ALPHA);

    SelectObject(hdcMem, oldBmp);
    DeleteObject(hbm);
    DeleteDC(hdcMem);
    ReleaseDC(nullptr, hdcScreen);
}

// ── Custom WM for pipe commands ───────────────────────────
// WM_APP+1 : MOVE  — wParam=x, lParam=y
// WM_APP+2 : FILE  — lParam=(LPARAM)new wstring* (heap, caller transfers ownership)
#define WM_PIPE_MOVE  (WM_APP + 1)
#define WM_PIPE_FILE  (WM_APP + 2)
#define WM_PIPE_QUIT  (WM_APP + 3)

// ── Named pipe server thread ──────────────────────────────
// Runs a persistent loop: create pipe → wait for client → read lines → repeat.
static HWND gHwnd = nullptr;

void PipeServerThread() {
    // Buffer for one command line (UTF-8 from Go client)
    char buf[4096];

    while (true) {
        HANDLE hPipe = CreateNamedPipeW(
            PIPE_NAME,
            PIPE_ACCESS_INBOUND,
            PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT,
            PIPE_UNLIMITED_INSTANCES,
            0, sizeof(buf), 0, nullptr
        );
        if (hPipe == INVALID_HANDLE_VALUE) {
            Sleep(500);
            continue;
        }

        // Block until a client connects
        if (!ConnectNamedPipe(hPipe, nullptr) &&
            GetLastError() != ERROR_PIPE_CONNECTED) {
            CloseHandle(hPipe);
            continue;
        }

        // Read bytes until client disconnects
        std::string lineBuf;
        DWORD read = 0;
        char ch;
        while (ReadFile(hPipe, &ch, 1, &read, nullptr) && read == 1) {
            if (ch == '\n') {
                // Trim \r
                if (!lineBuf.empty() && lineBuf.back() == '\r')
                    lineBuf.pop_back();

                // Parse command
                if (lineBuf.substr(0, 5) == "MOVE ") {
                    std::istringstream ss(lineBuf.substr(5));
                    int x = 0, y = 0;
                    ss >> x >> y;
                    PostMessageW(gHwnd, WM_PIPE_MOVE, (WPARAM)x, (LPARAM)y);
                }
                else if (lineBuf.substr(0, 5) == "FILE ") {
                    std::string  utf8path = lineBuf.substr(5);
                    // Convert UTF-8 → UTF-16
                    int wlen = MultiByteToWideChar(CP_UTF8, 0,
                        utf8path.c_str(), (int)utf8path.size(),
                        nullptr, 0);
                    auto* ws = new std::wstring(wlen, L'\0');
                    MultiByteToWideChar(CP_UTF8, 0,
                        utf8path.c_str(), (int)utf8path.size(),
                        &(*ws)[0], wlen);
                    // Pass heap pointer; WndProc takes ownership
                    PostMessageW(gHwnd, WM_PIPE_FILE, 0, (LPARAM)ws);
                }
                else if (lineBuf == "QUIT") {
                    PostMessageW(gHwnd, WM_PIPE_QUIT, 0, 0);
                }

                lineBuf.clear();
            } else {
                lineBuf += ch;
            }
        }

        DisconnectNamedPipe(hPipe);
        CloseHandle(hPipe);
    }
}

// ── State ─────────────────────────────────────────────────
static POINT gDragStart = {};
static bool  gMouseDown = false;

static bool ExceedsDragThreshold(POINT cur) {
    return abs(cur.x - gDragStart.x) > GetSystemMetrics(SM_CXDRAG) ||
           abs(cur.y - gDragStart.y) > GetSystemMetrics(SM_CYDRAG);
}

// ── WndProc ───────────────────────────────────────────────
LRESULT CALLBACK WndProc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp) {
    switch (msg) {

    case WM_PAINT: {
        PAINTSTRUCT ps;
        BeginPaint(hwnd, &ps);
        EndPaint(hwnd, &ps);
        return 0;
    }

    case WM_LBUTTONDOWN:
        gMouseDown = true;
        gDragStart = { GET_X_LPARAM(lp), GET_Y_LPARAM(lp) };
        SetCapture(hwnd);
        return 0;

    case WM_MOUSEMOVE: {
        if (!gMouseDown) return 0;
        if (!ExceedsDragThreshold({ GET_X_LPARAM(lp), GET_Y_LPARAM(lp) })) return 0;
        gMouseDown = false;
        ReleaseCapture();
        std::wstring pathCopy;
        {
            std::lock_guard<std::mutex> lk(gPathMutex);
            pathCopy = WAV_PATH;
        }
        StartDrag(hwnd, pathCopy);
        return 0;
    }

    case WM_LBUTTONUP:
        gMouseDown = false;
        ReleaseCapture();
        return 0;

    case WM_RBUTTONDOWN:
        DestroyWindow(hwnd);
        return 0;

    case WM_DESTROY:
        PostQuitMessage(0);
        return 0;

    // ── Pipe commands (posted from pipe thread) ───────────
    case WM_PIPE_MOVE: {
        int x = (int)(INT_PTR)wp;
        int y = (int)(INT_PTR)lp;
        SetWindowPos(hwnd, nullptr, x, y, 0, 0,
                     SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE);
        return 0;
    }

    case WM_PIPE_FILE: {
        auto* ws = reinterpret_cast<std::wstring*>(lp);
        if (ws) {
            std::lock_guard<std::mutex> lk(gPathMutex);
            WAV_PATH = *ws;
            delete ws;
        }
        return 0;
    }

    case WM_PIPE_QUIT:
        DestroyWindow(hwnd);
        return 0;
    }
    return DefWindowProc(hwnd, msg, wp, lp);
}

// ── Entry ─────────────────────────────────────────────────
int WINAPI WinMain(HINSTANCE hInst, HINSTANCE, LPSTR, int) {
    OleInitialize(nullptr);

    const wchar_t* CLASS = L"DragDropWnd";
    WNDCLASSEX wc = {};
    wc.cbSize        = sizeof(wc);
    wc.lpfnWndProc   = WndProc;
    wc.hInstance     = hInst;
    wc.hCursor       = LoadCursor(nullptr, IDC_ARROW);
    wc.lpszClassName = CLASS;
    RegisterClassEx(&wc);

    HWND hwnd = CreateWindowEx(
        WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_LAYERED,
        CLASS, L"DragDrop",
        WS_POPUP,
        100, 100, WND_W, WND_H,
        nullptr, nullptr, hInst, nullptr
    );

    gHwnd = hwnd;

    // Start pipe server on a background thread
    std::thread(PipeServerThread).detach();

    PaintLayered(hwnd);
    ShowWindow(hwnd, SW_SHOW);

    MSG msg;
    while (GetMessage(&msg, nullptr, 0, 0)) {
        TranslateMessage(&msg);
        DispatchMessage(&msg);
    }

    OleUninitialize();
    return 0;
}
