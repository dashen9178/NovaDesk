// NovaDesk 桌面壳 —— 主程序
//
// 干的事：
//   1. 读自己尾部，把内嵌的页面解出来写到磁盘
//   2. 开一个 WebView2 铺满窗口，加载那个页面
//   3. 把文件桥挂上去（chrome.webview.hostObjects.sync.novaFiles）
//
// 它既是 NovaDesk.exe，也是导出小应用用的模具 —— 差别只在尾部嵌了什么。

#include "nv.h"
#include "nv_bridge.h"
#include "nv_net.h"
#include "nv_exebridge.h"

#include <wrl.h>
#include <WebView2.h>
// CoreWebView2EnvironmentOptions 不在 WebView2.h 里，它是单独一个头，
// 用来给 WebView2 传启动参数（这里要开 file:// 访问本地文件）
#include <WebView2EnvironmentOptions.h>
#include <shlwapi.h>
#include <shellapi.h>   // ShellExecuteW：把下载链接交给默认浏览器
#include <filesystem>
#include <string>
#include <algorithm>

using namespace Microsoft::WRL;
namespace fs = std::filesystem;

static NvPayload             g_payload;
static HWND                  g_hwnd       = nullptr;
static ICoreWebView2Controller* g_controller = nullptr;
static ICoreWebView2*        g_web        = nullptr;
static NvFileBridge*         g_bridge     = nullptr;
static NvExeBridge*          g_exeBridge  = nullptr;
/** 本壳自己那一页的 URL。**除了它，什么都不许导航过去** —— 见 NavigationStarting */
static std::wstring          g_homeUrl;
static bool                  g_ready      = false;   // 页面加载好了没
static HBRUSH                g_bgBrush    = nullptr; // 窗口底色刷，退出时要删

// ============================================================
// 小工具
// ============================================================

/**
 * Windows 路径转 file:// URL。
 *
 * 用系统 API 转，别自己拼：路径里的空格、`#`、`%`、`?` 都得按 URL 规则转义，
 * 少转一个页面就加载不出来。用户目录名带 `#`（比如 "C#练习"）是常见情况。
 */
static std::wstring FileUrl(const std::wstring& path) {
    wchar_t buf[4096];
    DWORD len = (DWORD)(sizeof(buf) / sizeof(buf[0]));
    if (SUCCEEDED(UrlCreateFromPathW(path.c_str(), buf, &len, 0))) {
        return std::wstring(buf, len);
    }
    // 兜底：至少把空格转掉
    std::wstring url = L"file:///";
    for (wchar_t c : path) {
        if (c == L'\\') url += L'/';
        else if (c == L' ') url += L"%20";
        else url += c;
    }
    return url;
}

/** 把内嵌页面写到 %LOCALAPPDATA%\NovaDesk\run\<exe哈希>\app.html。
 *
 * 为什么要落盘而不是直接塞进 WebView：WebView2 只能 Navigate 到 URL，
 * 没有"直接喂一段 HTML 并保留相对路径/存储"的正规接口。
 * 落盘之后它就是一个正常的 file:// 页面，localStorage 也能用。
 *
 * 按 exe 分目录还有个好处：每个应用有自己的 file:// 路径，
 * 存储不会互相串。
 */
static bool ExtractHtml(const NvPayload& p, std::wstring& outPath, std::wstring* outDir) {
    std::wstring dir = NvDataDir() + L"\\run\\" + NvExeKey();
    std::error_code ec;
    fs::create_directories(fs::path(dir), ec);
    if (outDir) *outDir = dir;

    std::wstring file = dir + L"\\app.html";

    // 内容没变就不重写：少动磁盘，也避免把只读属性之类的状态搞乱
    bool needWrite = true;
    {
        std::string existing;
        HANDLE h = CreateFileW(file.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr,
                               OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
        if (h != INVALID_HANDLE_VALUE) {
            LARGE_INTEGER sz{};
            if (GetFileSizeEx(h, &sz) && (uint64_t)sz.QuadPart == p.html.size()) {
                existing.resize((size_t)sz.QuadPart);
                DWORD got = 0;
                if (ReadFile(h, existing.data(), (DWORD)existing.size(), &got, nullptr) &&
                    got == existing.size() && existing == p.html) {
                    needWrite = false;
                }
            }
            CloseHandle(h);
        }
    }
    if (needWrite) {
        HANDLE h = CreateFileW(file.c_str(), GENERIC_WRITE, 0, nullptr,
                               CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
        if (h == INVALID_HANDLE_VALUE) return false;
        DWORD wrote = 0;
        BOOL ok = WriteFile(h, p.html.data(), (DWORD)p.html.size(), &wrote, nullptr);
        CloseHandle(h);
        if (!ok || wrote != p.html.size()) return false;
    }

    outPath = file;
    return true;
}

// ============================================================
// 页面 <-> 原生的消息协议
//
// 为什么不用 AddHostObjectToScript：它要靠 IDispatch 的**类型库**才能生成
// 方法代理。没有类型库时页面能拿到对象，但每个成员都被当成无参调用 ——
// write(path, content) 这种根本传不进参数（实测：对象的全部成员都不可见）。
// 自己造 ITypeInfo 比这件事本身复杂得多，所以改走 postMessage。
//
// 编码：头部字段用 \x1f 分隔，后面跟按"UTF-16 码元数"切分的负载：
//     id \x1f target \x1f method \x1f argCount \x1f len1 \x1f len2 ... \x1f payload
// 用长度切分而不是再找一个分隔符，是因为文件内容里什么字符都可能出现。
// ============================================================

struct NvCall {
    std::wstring id;
    std::wstring target;
    std::wstring method;
    std::vector<std::string> args;   // UTF-8
};

static bool DecodeCall(const std::wstring& msg, NvCall& out) {
    const wchar_t SEP = L'\x1f';

    // 先把 id 抠出来 —— 后面就算解失败，也能带着 id 回一个错误，
    // 页面的 Promise 才不至于永远挂着
    size_t first = msg.find(SEP);
    if (first == std::wstring::npos) return false;
    out.id = msg.substr(0, first);

    std::vector<std::wstring> head;
    size_t pos = 0;
    for (int i = 0; i < 4; ++i) {
        size_t p = msg.find(SEP, pos);
        if (p == std::wstring::npos) return false;
        head.push_back(msg.substr(pos, p - pos));
        pos = p + 1;
    }
    int count = _wtoi(head[3].c_str());
    if (count < 0 || count > 8) return false;

    std::vector<int> lens;
    for (int i = 0; i < count; ++i) {
        size_t p = msg.find(SEP, pos);
        if (p == std::wstring::npos) return false;
        int len = _wtoi(msg.substr(pos, p - pos).c_str());
        if (len < 0) return false;
        lens.push_back(len);
        pos = p + 1;
    }

    out.args.clear();
    for (int i = 0; i < count; ++i) {
        size_t len = (size_t)lens[(size_t)i];
        if (pos + len > msg.size()) return false;
        out.args.push_back(NvUtf8(msg.substr(pos, len)));
        pos += len;
    }

    out.target = head[1];
    out.method = head[2];
    return true;
}

/**
 * 把窗口调成贴合内容的大小（导出页会主动报尺寸过来）。
 *
 * 为什么需要：壳原来固定开 1100x820。装 NovaDesk 这种聊天界面正合适，
 * 但导出的东西可能只是个 360x300 的小工具 —— 四周一大片空白，
 * 用户看到的就是"默认窗口大小与实际软件内容不合理"。
 */
static void FitWindowToContent(int contentW, int contentH) {
    if (!g_hwnd || contentW <= 0 || contentH <= 0) return;

    // 先把尺寸夹到一个合理区间**再**做加法。
    // （_wtoi 能返回 INT_MAX，`contentW + 2` 会是有符号溢出 —— UB。）
    contentW = std::min(contentW, 20000);
    contentH = std::min(contentH, 20000);

    HMONITOR mon = MonitorFromWindow(g_hwnd, MONITOR_DEFAULTTONEAREST);
    MONITORINFO mi{};
    mi.cbSize = sizeof(mi);
    if (!GetMonitorInfoW(mon, &mi)) return;

    const int availW = mi.rcWork.right - mi.rcWork.left;
    const int availH = mi.rcWork.bottom - mi.rcWork.top;
    // 留出边距，别顶到屏幕边上；太小也不行
    const int clientW = std::max(320, std::min(contentW + 2, availW * 9 / 10));
    const int clientH = std::max(200, std::min(contentH + 2, availH * 9 / 10));

    RECT rc{0, 0, clientW, clientH};
    const DWORD style = (DWORD)GetWindowLongPtrW(g_hwnd, GWL_STYLE);
    const DWORD ex    = (DWORD)GetWindowLongPtrW(g_hwnd, GWL_EXSTYLE);
    AdjustWindowRectEx(&rc, style, FALSE, ex);
    const int winW = rc.right - rc.left;
    const int winH = rc.bottom - rc.top;
    const int x = mi.rcWork.left + (availW - winW) / 2;
    const int y = mi.rcWork.top + (availH - winH) / 2;

    SetWindowPos(g_hwnd, nullptr, x, y, winW, winH, SWP_NOZORDER | SWP_NOACTIVATE);
}

/**
 * 把结果回给页面。
 *
 * id 也当字符串参数转义 —— 它是从页面来的，不转义就是一个纯字符串拼接注入点
 * （`1);evil();(0` 这种）。这条本身不构成提权（页面本来就能跑任意 JS），
 * 但没有任何理由留一个拼接洞。
 */
static void ReplyToPage(const std::wstring& id, const std::string& json) {
    if (!g_web) return;
    std::string js = "window.__novaNative && window.__novaNative(\"" +
                     NvJsonEscape(NvUtf8(id)) + "\", JSON.parse(\"" +
                     NvJsonEscape(json) + "\"));";
    g_web->ExecuteScript(NvWide(js).c_str(), nullptr);
}

/**
 * 把 JSON 字符串字面量还原成原始字符串。
 *
 * 为什么绕这一道：`TryGetWebMessageAsString` 给的是 CoTaskMemAlloc 出来的
 * **普通 C 字符串，不是 BSTR** —— 长度只能靠 wcslen 数到 NUL 为止，
 * 内容里一旦有 \u0000 就当场截断（我最初还错用了 SysStringLen，
 * 那是直接读到指针前面的垃圾当长度，消息解析全废、页面永久卡住）。
 *
 * 改用 `get_WebMessageAsJson`：控制字符在 JSON 里都是转义的，
 * 取回来的字符串里不会出现裸 NUL，任何内容都能原样带过来。
 */
static bool JsonUnescape(const std::wstring& in, std::wstring& out) {
    out.clear();
    size_t n = in.size();
    if (n < 2 || in[0] != L'"' || in[n - 1] != L'"') return false;
    size_t i = 1;
    n -= 1;                      // 末尾那个引号不参与
    while (i < n) {
        wchar_t c = in[i++];
        if (c != L'\\') { out += c; continue; }
        if (i >= n) return false;
        wchar_t e = in[i++];
        switch (e) {
            case L'n':  out += L'\n'; break;
            case L't':  out += L'\t'; break;
            case L'r':  out += L'\r'; break;
            case L'b':  out += L'\b'; break;
            case L'f':  out += L'\f'; break;
            case L'"':  out += L'"';  break;
            case L'\\': out += L'\\'; break;
            case L'/':  out += L'/';  break;
            case L'u': {
                if (i + 4 > n) return false;
                unsigned v = 0;
                for (int k = 0; k < 4; ++k) {
                    wchar_t h = in[i + k];
                    v <<= 4;
                    if (h >= L'0' && h <= L'9')      v |= (unsigned)(h - L'0');
                    else if (h >= L'a' && h <= L'f') v |= (unsigned)(h - L'a' + 10);
                    else if (h >= L'A' && h <= L'F') v |= (unsigned)(h - L'A' + 10);
                    else return false;
                }
                i += 4;
                out += (wchar_t)v;      // 代理对会来两次，正好各占一个 wchar
                break;
            }
            default: return false;
        }
    }
    return true;
}

static void OnWebMessage(ICoreWebView2WebMessageReceivedEventArgs* args) {
    LPWSTR raw = nullptr;
    if (FAILED(args->get_WebMessageAsJson(&raw)) || !raw) return;
    std::wstring json(raw);
    CoTaskMemFree(raw);

    // 我们只发字符串，所以取回来的必然是一段带引号的 JSON 字符串
    std::wstring msg;
    if (!JsonUnescape(json, msg)) return;

    // 导出页发来的"窗口贴合内容"请求：\x01FIT \x1f 宽 \x1f 高
    // 一眼就能认出来，不用走完整的桥协议（这条只有导出的应用会发）
    if (msg.size() > 6 && msg[0] == (wchar_t)1 && msg.compare(1, 3, L"FIT") == 0) {
        // 注意 "FIT" 后面紧跟的就是第一个 \x1f（下标 4），
        // 别在下标 4 上再取一次"下标 4 到它自己"的子串 —— 那会得到空串、宽度读成 0
        const size_t p1 = msg.find(L'\x1f', 4);
        const size_t p2 = (p1 == std::wstring::npos)
                              ? std::wstring::npos
                              : msg.find(L'\x1f', p1 + 1);
        if (p1 != std::wstring::npos && p2 != std::wstring::npos) {
            const int w = _wtoi(msg.substr(p1 + 1, p2 - p1 - 1).c_str());
            const int h = _wtoi(msg.substr(p2 + 1).c_str());
            FitWindowToContent(w, h);
        }
        return;
    }

    NvCall call;
    if (!DecodeCall(msg, call)) {
        // 解不出来也要回一个错误：宁可让调用方看到失败，也不能让它干等
        ReplyToPage(call.id, NvFail("请求格式不对，原生没法解析"));
        return;
    }

    std::string result;
    if (call.target == L"novaExe") {
        result = g_exeBridge ? g_exeBridge->Call(NvUtf8(call.method), call.args)
                             : NvFail("导出桥还没准备好");
        ReplyToPage(call.id, result);
    } else if (call.target == L"novaNet") {
        // 联网是**异步**的：绝不能在 UI 线程的消息回调里等网络。
        // 起个后台线程，干完了发 WM_NV_NET_DONE 回来（见 nv_net.cpp）。
        result = NvNetStart(NvUtf8(call.id), call.args);
        if (!result.empty()) ReplyToPage(call.id, result);   // 非空 = 立刻失败
    } else {
        result = g_bridge ? g_bridge->Call(NvUtf8(call.method), call.args)
                          : NvFail("文件桥还没准备好");
        ReplyToPage(call.id, result);
    }
}

// ============================================================
// 窗口
// ============================================================

static void ResizeWebView() {
    if (!g_controller || !g_hwnd) return;
    RECT rc{};
    GetClientRect(g_hwnd, &rc);
    g_controller->put_Bounds(rc);
}

static void NotifyFileChanged() {
    if (!g_web || !g_bridge) return;
    // 注意这里传的是**对象**不是字符串：页面那边直接读 payload.mode，
    // 并没有再 parse 一次
    std::string js = "window.__novaFileChanged && window.__novaFileChanged(" +
                     g_bridge->InfoJson() + ");";
    g_web->ExecuteScript(NvWide(js).c_str(), nullptr);
}

static LRESULT CALLBACK WndProc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp) {
    switch (msg) {
        case WM_SIZE:
            ResizeWebView();
            return 0;

        case WM_NV_FILE_CHANGED:
            NotifyFileChanged();
            return 0;

        case WM_NV_CONFIRM_MODE:
            // 权限确认框在这里弹 —— 不能在那个 WebView2 消息回调里同步弹，
            // 那会把整条 JS↔原生通道堵死
            if (g_bridge) g_bridge->ConfirmPendingMode();
            return 0;

        case WM_NV_NET_DONE:
            // 后台线程的网络请求干完了，把结果回给页面。
            // 只能在这里做 —— ExecuteScript 必须在 UI 线程上调。
            NvNetDrain([](const std::string& id, const std::string& json) {
                ReplyToPage(NvWide(id), json);
            });
            return 0;

        case WM_SETFOCUS:
            if (g_controller) g_controller->MoveFocus(COREWEBVIEW2_MOVE_FOCUS_REASON_PROGRAMMATIC);
            return 0;

        case WM_DESTROY:
            PostQuitMessage(0);
            return 0;
    }
    return DefWindowProcW(hwnd, msg, wp, lp);
}

// ============================================================
// WebView2
// ============================================================

static void OnWebReady() {
    g_ready = true;

    // 页面里的链接不开新窗口 —— 这个壳只有一个视图。
    //
    // ★ 但**不能就这么吞掉**：检查更新给的「下载」按钮走的就是 window.open。
    //   吞了的话用户点了毫无反应，正是这个项目反复强调不许出现的那种"坏按钮"。
    //   所以改成：交给系统的默认浏览器去开。
    g_web->add_NewWindowRequested(
        Callback<ICoreWebView2NewWindowRequestedEventHandler>(
            [](ICoreWebView2* sender, ICoreWebView2NewWindowRequestedEventArgs* args) -> HRESULT {
                args->put_Handled(TRUE);
                LPWSTR uri = nullptr;
                if (SUCCEEDED(args->get_Uri(&uri)) && uri) {
                    // 只放行 http(s)：ShellExecute 什么协议都能唤起，
                    // 页面里跑的是模型写的脚本，不该有这个机会。
                    const bool okScheme =
                        (wcsncmp(uri, L"http://", 7) == 0 ||
                         wcsncmp(uri, L"https://", 8) == 0);
                    if (okScheme) {
                        ShellExecuteW(nullptr, L"open", uri, nullptr, nullptr,
                                      SW_SHOWNORMAL);
                    }
                    CoTaskMemFree(uri);
                }
                return S_OK;
            }).Get(), nullptr);

    // 权限档位可能被用户在别处改过，开机同步一次给页面
    NotifyFileChanged();
}

static HRESULT OnControllerCreated(HRESULT hr, ICoreWebView2Controller* controller) {
    if (FAILED(hr) || !controller) {
        MessageBoxW(g_hwnd,
                    L"WebView2 初始化失败。\n\n"
                    L"这台机器可能缺少 WebView2 运行时（Win10/11 一般自带，"
                    L"装一下 Edge 就有了）。",
                    L"NovaDesk", MB_ICONERROR | MB_OK);
        PostQuitMessage(1);
        return S_OK;
    }

    g_controller = controller;
    g_controller->AddRef();
    g_controller->put_IsVisible(TRUE);
    ResizeWebView();

    if (FAILED(g_controller->get_CoreWebView2(&g_web)) || !g_web) {
        PostQuitMessage(2);
        return S_OK;
    }

    ComPtr<ICoreWebView2Settings> settings;
    if (SUCCEEDED(g_web->get_Settings(&settings)) && settings) {
        settings->put_IsScriptEnabled(TRUE);
        settings->put_AreDefaultContextMenusEnabled(TRUE);
        settings->put_IsStatusBarEnabled(FALSE);
        settings->put_AreDevToolsEnabled(TRUE);      // 出问题时能按 F12 看
        settings->put_IsZoomControlEnabled(FALSE);
        settings->put_AreHostObjectsAllowed(TRUE);
    }

    // ---- 挂两个桥 ----
    // 页面通过 postMessage 发指令，这里收到后分派：
    // target=novaFiles 走文件桥，target=novaExe 走导出桥
    //
    // 注意：这些活儿现在是**在 UI 线程上同步做**的（WebMessageReceived 回调就在
    // UI 线程）。读小文件、列小目录都感觉不到，但读一个几百 KB 的文件或者列一个
    // 巨大的目录时窗口会短暂假死。要彻底解决得把活挪到工作线程、再把结果
    // PostMessage 回 UI 线程调 ExecuteScript —— 目前是已知限制，没做。
    g_bridge = new NvFileBridge(g_hwnd);
    g_exeBridge = new NvExeBridge(g_payload);

    g_web->add_WebMessageReceived(
        Callback<ICoreWebView2WebMessageReceivedEventHandler>(
            [](ICoreWebView2*, ICoreWebView2WebMessageReceivedEventArgs* args) -> HRESULT {
                OnWebMessage(args);
                return S_OK;
            }).Get(), nullptr);

    // ---- 加载页面 ----
    std::wstring htmlPath;
    std::wstring homeUrl;
    if (g_payload.ok && ExtractHtml(g_payload, htmlPath, nullptr)) {
        homeUrl = FileUrl(htmlPath);
    } else {
        // 开发时方便：直接放一个 app.html 在 exe 旁边也能跑
        std::wstring beside = fs::path(g_payload.exePath).parent_path().wstring() + L"\\app.html";
        std::error_code ec;
        if (fs::exists(fs::path(beside), ec)) {
            homeUrl = FileUrl(beside);
        } else {
            homeUrl = L"data:text/html;charset=utf-8,"
                      L"<body style='font-family:system-ui;padding:40px'>"
                      L"<h2>%E8%BF%99%E4%B8%AA exe %E9%87%8C%E6%B2%A1%E6%9C%89%E5%86%85%E5%B5%8C%E9%A1%B5%E9%9D%A2</h2>"
                      L"<p>%E5%AE%83%E5%BA%94%E8%AF%A5%E6%98%AF%E7%94%B1 build.ps1 %E6%89%93%E5%87%BA%E6%9D%A5%E7%9A%84%EF%BC%8C"
                      L"%E6%88%96%E8%80%85%E6%97%81%E8%BE%B9%E6%94%BE%E4%B8%80%E4%B8%AA app.html%E3%80%82</p></body>";
        }
    }
    g_homeUrl = homeUrl;
    g_web->Navigate(homeUrl.c_str());

    // ★★ 把导航钉死在自己的页面上。
    //
    // 这一段同时解决两件事，都不是小事：
    //
    // 1. **用户报的 bug**：往窗口里**拖一张图片**，Chromium 的默认行为是
    //    "导航到拖进来的那个文件" —— 于是整个软件就变成那张图铺满屏幕。
    //    用户连着报了两次「电脑版上传图片覆盖全屏」，根子在这儿，
    //    跟缩略图 CSS 一点关系都没有（页面上量 DOM 也量不出来，
    //    因为整个文档已经被换掉了）。
    //
    // 2. **一个真正的安全洞**：这个 WebView 上挂着原生桥
    //    （文件读写 / 导出 exe），而桥是挂在**视图**上的、不是挂在来源上的。
    //    不拦的话，页面里一段脚本（或者 AI 写的面板脚本）只要
    //    `location.href = 'https://别人的站'`，那个站就能拿到同一个
    //    `window.chrome.webview` —— 等于把整台电脑的文件读写交出去。
    //    file:// 更糟：同源，连 localStorage 里的 API key 一起读走。
    //
    // 规则：**只允许停在 homeUrl 上**。http(s) 交给系统浏览器打开
    //（用户点链接仍然是有反应的），其余一律取消。
    g_web->add_NavigationStarting(
        Callback<ICoreWebView2NavigationStartingEventHandler>(
            [](ICoreWebView2*, ICoreWebView2NavigationStartingEventArgs* args) -> HRESULT {
                LPWSTR uri = nullptr;
                if (FAILED(args->get_Uri(&uri)) || !uri) {
                    args->put_Cancel(TRUE);
                    return S_OK;
                }
                const std::wstring u(uri);
                CoTaskMemFree(uri);

                if (u == g_homeUrl) return S_OK;          // 自己这一页，放行

                if (u.rfind(L"http://", 0) == 0 || u.rfind(L"https://", 0) == 0) {
                    // 外链走默认浏览器，和安卓壳一样 —— 让用户点得动，
                    // 但绝不让那个页面进入这个带桥的 WebView
                    ShellExecuteW(nullptr, L"open", u.c_str(), nullptr, nullptr,
                                  SW_SHOWNORMAL);
                }
                args->put_Cancel(TRUE);
                return S_OK;
            }).Get(), nullptr);

    // 加载完成后同步一次状态
    g_web->add_NavigationCompleted(
        Callback<ICoreWebView2NavigationCompletedEventHandler>(
            [](ICoreWebView2*, ICoreWebView2NavigationCompletedEventArgs*) -> HRESULT {
                OnWebReady();
                return S_OK;
            }).Get(), nullptr);

    return S_OK;
}

/**
 * 从命令行挑几个开关转给 WebView2。
 *
 * `--remote-debugging-port=` 能让人用 CDP 完全接管这个页面（读 localStorage 里的
 * API key、直接调文件桥），所以**必须显式设 `NOVADESK_DEBUG=1` 才放行** ——
 * 光带个命令行参数不够，免得哪天被人顺手写进快捷方式。
 * 自动化测试就是靠这个开关连进来看的，见 verify_desktop.py。
 */
static std::wstring ExtraBrowserArgs() {
    std::wstring out;
    wchar_t dbg[16];
    DWORD n = GetEnvironmentVariableW(L"NOVADESK_DEBUG", dbg,
                                      (DWORD)(sizeof(dbg) / sizeof(dbg[0])));
    if (n == 0 || dbg[0] != L'1') return out;

    int argc = 0;
    LPWSTR* argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    if (!argv) return out;
    for (int i = 1; i < argc; ++i) {
        std::wstring a = argv[i];
        if (a.rfind(L"--remote-debugging-port=", 0) == 0 ||
            a == L"--enable-logging") {
            if (!out.empty()) out += L' ';
            out += a;
        }
    }
    LocalFree(argv);
    return out;
}

static void StartWebView() {
    // 用户数据目录按 exe 分家。共用一个目录的话，所有导出的应用都是同一个
    // file:// 源，localStorage 也共享 —— 导出的应用就有机会读到宿主存着的
    // API key，那显然不对。
    std::wstring userData = NvDataDir() + L"\\WebView2\\" + NvExeKey();
    std::error_code ec;
    fs::create_directories(fs::path(userData), ec);

    auto options = Make<CoreWebView2EnvironmentOptions>();
    // 只关掉两个会抢焦点的 Edge 浮层功能。
    // ★ 刻意**不加** --allow-file-access-from-files：页面是自包含单文件，
    //   根本不需要 file:// 同源访问；加上它等于让 AI 写的面板脚本用 XHR 直读
    //   任意本地文件，把"文件权限"那三档彻底架空。
    std::wstring args = L"--disable-features=msWebOOUI,msPdfOOUI";
    std::wstring extra = ExtraBrowserArgs();
    if (!extra.empty()) args += L" " + extra;
    options->put_AdditionalBrowserArguments(args.c_str());

    HRESULT hr = CreateCoreWebView2EnvironmentWithOptions(
        nullptr, userData.c_str(), options.Get(),
        Callback<ICoreWebView2CreateCoreWebView2EnvironmentCompletedHandler>(
            [](HRESULT hr2, ICoreWebView2Environment* env) -> HRESULT {
                if (FAILED(hr2) || !env) return OnControllerCreated(hr2, nullptr);
                env->CreateCoreWebView2Controller(
                    g_hwnd,
                    Callback<ICoreWebView2CreateCoreWebView2ControllerCompletedHandler>(
                        [](HRESULT hr3, ICoreWebView2Controller* c) -> HRESULT {
                            return OnControllerCreated(hr3, c);
                        }).Get());
                return S_OK;
            }).Get());

    if (FAILED(hr)) OnControllerCreated(hr, nullptr);
}

// ============================================================
// 入口
// ============================================================

int WINAPI wWinMain(HINSTANCE hInst, HINSTANCE, LPWSTR, int nCmdShow) {
    SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
    CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);

    g_payload = NvLoadPayload();

    std::wstring title = g_payload.ok && !g_payload.appName.empty()
                             ? g_payload.appName
                             : L"NovaDesk";

    WNDCLASSEXW wc{};
    wc.cbSize        = sizeof(wc);
    wc.lpfnWndProc   = WndProc;
    wc.hInstance     = hInst;
    wc.hCursor       = LoadCursor(nullptr, IDC_ARROW);
    // 和页面底色一致，避免加载那一瞬间闪一下别的颜色。
    // 这个刷子要自己删 —— 全局留个句柄，退出前 DeleteObject。
    g_bgBrush        = CreateSolidBrush(RGB(0xFE, 0xF7, 0xFF));
    wc.hbrBackground = g_bgBrush;
    wc.lpszClassName = L"NovaDeskShell";
    wc.hIcon         = LoadIcon(nullptr, IDI_APPLICATION);
    RegisterClassExW(&wc);

    g_hwnd = CreateWindowExW(
        0, wc.lpszClassName, title.c_str(),
        WS_OVERLAPPEDWINDOW,
        CW_USEDEFAULT, CW_USEDEFAULT,
        // NovaDesk 自己是聊天界面，开大一点；导出的应用先给个中等的，
        // 页面的 FIT 请求到了会自己贴合内容（见 FitWindowToContent）
        (g_payload.appName == L"NovaDesk") ? 1100 : 480,
        (g_payload.appName == L"NovaDesk") ? 820 : 640,
        nullptr, nullptr, hInst, nullptr);

    if (!g_hwnd) return 1;

    // 原生联网桥（检查更新用）要知道往哪个窗口发"干完了"的消息
    NvNetInit(g_hwnd);

    ShowWindow(g_hwnd, nCmdShow);
    UpdateWindow(g_hwnd);

    StartWebView();

    MSG msg;
    while (GetMessageW(&msg, nullptr, 0, 0) > 0) {
        TranslateMessage(&msg);
        DispatchMessageW(&msg);
    }

    delete g_bridge;   g_bridge = nullptr;
    NvNetShutdown();
    delete g_exeBridge; g_exeBridge = nullptr;
    if (g_web)        g_web->Release();
    if (g_controller) g_controller->Release();
    if (g_bgBrush)    { DeleteObject(g_bgBrush); g_bgBrush = nullptr; }
    CoUninitialize();
    return 0;
}
