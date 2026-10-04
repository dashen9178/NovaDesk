// NovaDesk 桌面壳 —— 文件桥实现
#include "nv_bridge.h"

#include <shobjidl.h>
#include <filesystem>
#include <fstream>
#include <sstream>
#include <algorithm>

namespace fs = std::filesystem;

static constexpr size_t kMaxRead = 400000;   // 单次最多回传多少字符

// ============================================================
// 小工具
// ============================================================

static bool IsValidUtf8(const std::string& s) {
    size_t i = 0, n = s.size();
    while (i < n) {
        unsigned char c = (unsigned char)s[i];
        int extra;
        if (c < 0x80) { ++i; continue; }
        else if ((c & 0xE0) == 0xC0) extra = 1;
        else if ((c & 0xF0) == 0xE0) extra = 2;
        else if ((c & 0xF8) == 0xF0) extra = 3;
        else return false;
        if (i + (size_t)extra >= n) return false;
        for (int k = 1; k <= extra; ++k) {
            if (((unsigned char)s[i + k] & 0xC0) != 0x80) return false;
        }
        i += (size_t)extra + 1;
    }
    return true;
}

/** 看着像二进制吗（前 8KB 里有 NUL 就是） */
static bool LooksBinary(const std::string& s) {
    size_t n = std::min<size_t>(s.size(), 8192);
    for (size_t i = 0; i < n; ++i) {
        if (s[i] == '\0') return true;
    }
    return false;
}

/**
 * 把文件内容转成能放进 JSON 的 UTF-8 文本。
 *
 * 中文 Windows 上大量文本文件是 ANSI(GBK) 存的 —— 记事本"另存为 ANSI"就是这样。
 * 直接当 UTF-8 塞进 JSON 会产出非法 JSON，页面那边 JSON.parse 当场炸。
 * 所以先验 UTF-8，不合法就按系统 ANSI 码页转一遍。
 */
static std::string ToUtf8Text(const std::string& raw) {
    if (IsValidUtf8(raw)) return raw;
    int n = MultiByteToWideChar(CP_ACP, 0, raw.c_str(), (int)raw.size(), nullptr, 0);
    if (n <= 0) return raw;
    std::wstring w((size_t)n, L'\0');
    MultiByteToWideChar(CP_ACP, 0, raw.c_str(), (int)raw.size(), w.data(), n);
    return NvUtf8(w);
}

static bool ReadWholeFile(const fs::path& p, std::string& out) {
    std::ifstream in(p, std::ios::binary);
    if (!in) return false;
    std::stringstream ss;
    ss << in.rdbuf();
    out = ss.str();
    return true;
}

static std::string ArgAt(const std::vector<std::string>& a, size_t i) {
    return i < a.size() ? a[i] : std::string();
}

// ============================================================
// 权限状态
// ============================================================

std::string NvFileBridge::InfoJson() {
    std::wstring leaf;
    if (!m_set.folder.empty()) {
        leaf = fs::path(m_set.folder).filename().wstring();
        if (leaf.empty()) leaf = m_set.folder;
    }

    std::wstring home;
    {
        wchar_t up[MAX_PATH * 4];
        DWORD n = GetEnvironmentVariableW(L"USERPROFILE", up,
                                          (DWORD)(sizeof(up) / sizeof(up[0])));
        if (n > 0 && n < sizeof(up) / sizeof(up[0])) home.assign(up, n);
    }

    bool all = (m_set.mode == L"all");
    return NvJson()
        .Str("mode", NvUtf8(m_set.mode))
        // 桌面版没有系统级授权这一关：选了 all 就是 all。
        // 报 true 是为了让页面上"还没生效"的红标不至于一直亮着。
        .Bool("allGranted", all)
        .Str("folderName", NvUtf8(leaf))
        .Bool("hasFolder", !m_set.folder.empty())
        .Str("platform", "desktop")
        .Str("root", NvUtf8(home))
        .Num("sdk", 0)
        .Done();
}

void NvFileBridge::DoSetMode(const std::string& mode) {
    std::wstring m = NvWide(mode);
    if (m != L"off" && m != L"folder" && m != L"all") m = L"off";

    // ★ 从"关闭"往上升，要用户在**原生确认框**上点一下。
    //
    //   网页那层的提示词写着"权限要让用户点按钮自己开，你开不了"，
    //   但那道闸只在页面 JS 里 —— AI 写的面板脚本和界面**共用同一个 JS 环境**，
    //   一行 setMode('all') 就能把用户刚关掉的权限打开。原生这层问一句，
    //   才真的把用户放回回路里。
    //
    //   注意这里**不能同步弹框**：Call() 跑在 WebView2 的消息回调里，
    //   同步弹模态框会把整条 JS↔原生通道堵死。所以只记下意图、发个消息，
    //   真正的弹框交给消息循环（ConfirmPendingMode）。
    if (m_set.mode == L"off" && m != L"off") {
        m_pendingMode = m;
        PostMessageW(m_hwnd, WM_NV_CONFIRM_MODE, 0, 0);
        return;
    }

    // 降级到 off 不需要确认
    m_set.mode = m;
    NvSaveSettings(m_set);
    PostMessageW(m_hwnd, WM_NV_FILE_CHANGED, 0, 0);
}

void NvFileBridge::ConfirmPendingMode() {
    std::wstring m = m_pendingMode;
    if (m.empty()) return;
    m_pendingMode.clear();

    std::wstring ask = (m == L"all")
        ? L"NovaDesk 想打开「完全权限」：之后它能读写这台电脑上的任意文件。\n\n允许吗？"
        : L"NovaDesk 想让你指定一个文件夹给它读写。\n\n继续吗？";
    int r = MessageBoxW(m_hwnd, ask.c_str(), L"文件权限",
                        MB_ICONQUESTION | MB_YESNO | MB_DEFBUTTON2);

    if (r == IDYES) {
        m_set.mode = m;
        NvSaveSettings(m_set);
        // 选了"指定文件夹"却还没挑过目录，接着把他领到选择器，少一步
        if (m == L"folder" && m_set.folder.empty()) {
            DoPickFolder();
            return;   // DoPickFolder 自己会推状态
        }
    }
    PostMessageW(m_hwnd, WM_NV_FILE_CHANGED, 0, 0);
}

void NvFileBridge::DoRequestAll() {
    // 和 setMode 同一条路：要用户确认
    if (m_set.mode != L"all") {
        m_pendingMode = L"all";
        PostMessageW(m_hwnd, WM_NV_CONFIRM_MODE, 0, 0);
        return;
    }
    PostMessageW(m_hwnd, WM_NV_FILE_CHANGED, 0, 0);
}

void NvFileBridge::DoPickFolder() {
    IFileOpenDialog* dlg = nullptr;
    HRESULT hr = CoCreateInstance(CLSID_FileOpenDialog, nullptr, CLSCTX_INPROC_SERVER,
                                  IID_PPV_ARGS(&dlg));
    if (SUCCEEDED(hr)) {
        DWORD opts = 0;
        dlg->GetOptions(&opts);
        dlg->SetOptions(opts | FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM | FOS_PATHMUSTEXIST);
        dlg->SetTitle(L"选一个文件夹给 NovaDesk 用");
        hr = dlg->Show(m_hwnd);
        if (SUCCEEDED(hr)) {
            IShellItem* item = nullptr;
            if (SUCCEEDED(dlg->GetResult(&item)) && item) {
                PWSTR psz = nullptr;
                if (SUCCEEDED(item->GetDisplayName(SIGDN_FILESYSPATH, &psz)) && psz) {
                    m_set.folder = psz;
                    m_set.mode = L"folder";
                    NvSaveSettings(m_set);
                    CoTaskMemFree(psz);
                }
                item->Release();
            }
        }
        dlg->Release();
    }
    // 选完通知页面刷新状态（这一路是主线程消息循环干的）
    PostMessageW(m_hwnd, WM_NV_FILE_CHANGED, 0, 0);
}

// ============================================================
// 文件操作
// ============================================================

static const char* kOffMsg =
    "文件权限没开。请用户点输入框上方的「权限」按钮开启。";

std::string NvFileBridge::DoList(const std::string& path) {
    bool allowed = false;
    std::wstring full = NvResolvePath(m_set, path, &allowed);
    if (!allowed) return NvFail(m_set.mode == L"off" ? kOffMsg : "这个路径不在允许范围内");

    std::error_code ec;
    fs::path p(full);
    if (!fs::exists(p, ec)) return NvFail("找不到：" + (path.empty() ? NvUtf8(full) : path));
    if (!fs::is_directory(p, ec)) return NvFail("这不是目录：" + path);

    struct Item { std::string name; bool dir; uint64_t size; };
    std::vector<Item> items;
    // 上限保护：列一个几十万项的目录会让回传的 JSON 变成几 MB，
    // 再经 ExecuteScript 塞回页面 —— 界面直接假死。
    // ★ 计数必须在**循环里**：以前是先全收进 vector 再 resize，
    //   枚举和内存开销全都发生了，等于没防。
    constexpr size_t kMaxItems = 2000;
    bool capped = false;
    for (auto it = fs::directory_iterator(p, fs::directory_options::skip_permission_denied, ec);
         !ec && it != fs::directory_iterator(); it.increment(ec)) {
        if (items.size() >= kMaxItems) { capped = true; break; }
        const auto& e = *it;
        Item i;
        i.name = NvUtf8(e.path().filename().wstring());
        std::error_code e2;
        i.dir = e.is_directory(e2);
        i.size = i.dir ? 0 : (uint64_t)e.file_size(e2);
        items.push_back(std::move(i));
    }
    std::sort(items.begin(), items.end(), [](const Item& a, const Item& b) {
        std::wstring x = NvWide(a.name), y = NvWide(b.name);
        std::transform(x.begin(), x.end(), x.begin(), ::towlower);
        std::transform(y.begin(), y.end(), y.begin(), ::towlower);
        return x < y;
    });

    std::string arr = "[";
    for (size_t k = 0; k < items.size(); ++k) {
        if (k) arr += ",";
        arr += "{\"name\":\"" + NvJsonEscape(items[k].name) + "\",\"dir\":" +
               (items[k].dir ? "true" : "false") + ",\"size\":" +
               std::to_string(items[k].size) + "}";
    }
    arr += "]";

    return NvOk(NvJson().Str("path", NvUtf8(full)).Raw("items", arr)
                .Bool("truncated", capped).Body());
}

std::string NvFileBridge::DoRead(const std::string& path) {
    bool allowed = false;
    std::wstring full = NvResolvePath(m_set, path, &allowed);
    if (!allowed) return NvFail(m_set.mode == L"off" ? kOffMsg : "这个路径不在允许范围内");

    std::error_code ec;
    fs::path p(full);
    if (!fs::exists(p, ec)) return NvFail("找不到：" + path);
    if (fs::is_directory(p, ec)) return NvFail("这是个目录：" + path);

    std::string raw;
    if (!ReadWholeFile(p, raw)) return NvFail("读不出来：" + path);
    if (LooksBinary(raw)) {
        return NvFail("这是二进制文件，当文本读没有意义（大小 " +
                      std::to_string(raw.size()) + " 字节）");
    }

    std::string text = ToUtf8Text(raw);
    bool truncated = text.size() > kMaxRead;
    if (truncated) text.resize(kMaxRead);

    return NvOk(NvJson()
        .Str("path", NvUtf8(full))
        .Str("text", text)
        .Num("chars", (long long)text.size())
        .Bool("truncated", truncated)
        .Body());
}

std::string NvFileBridge::DoWrite(const std::string& path, const std::string& content) {
    bool allowed = false;
    std::wstring full = NvResolvePath(m_set, path, &allowed);
    if (!allowed) return NvFail(m_set.mode == L"off" ? kOffMsg : "这个路径不在允许范围内");

    fs::path p(full);
    std::error_code ec;
    if (p.has_parent_path()) fs::create_directories(p.parent_path(), ec);

    std::ofstream out(p, std::ios::binary | std::ios::trunc);
    if (!out) return NvFail("写不进去：" + path);
    out.write(content.data(), (std::streamsize)content.size());
    out.close();
    if (!out) return NvFail("写了一半失败了：" + path);

    return NvOk(NvJson().Str("path", NvUtf8(full))
                .Num("bytes", (long long)content.size()).Body());
}

std::string NvFileBridge::DoMkdir(const std::string& path) {
    bool allowed = false;
    std::wstring full = NvResolvePath(m_set, path, &allowed);
    if (!allowed) return NvFail(m_set.mode == L"off" ? kOffMsg : "这个路径不在允许范围内");

    std::error_code ec;
    fs::create_directories(fs::path(full), ec);
    if (ec && !fs::is_directory(fs::path(full))) return NvFail("建不出目录：" + path);
    return NvOk(NvJson().Str("path", NvUtf8(full)).Body());
}

/** 取一个环境变量的值，取不到返回空 */
static std::wstring EnvOf(const wchar_t* name) {
    wchar_t buf[MAX_PATH * 4];
    DWORD n = GetEnvironmentVariableW(name, buf, (DWORD)(sizeof(buf) / sizeof(buf[0])));
    if (n == 0 || n >= sizeof(buf) / sizeof(buf[0])) return {};
    return std::wstring(buf, n);
}

/** 比较两个路径是不是同一个（Windows 大小写不敏感，顺手去掉尾部反斜杠） */
static bool SamePath(std::wstring a, std::wstring b) {
    auto norm = [](std::wstring s) {
        while (s.size() > 3 && (s.back() == L'\\' || s.back() == L'/')) s.pop_back();
        for (auto& c : s) c = (wchar_t)towlower(c);
        return s;
    };
    return norm(a) == norm(b);
}

/** 比较两个路径分量是不是同一个（Windows 大小写不敏感） */
static bool SameComponent(const fs::path& a, const fs::path& b) {
    std::wstring x = a.wstring(), y = b.wstring();
    for (auto& c : x) c = (wchar_t)towlower(c);
    for (auto& c : y) c = (wchar_t)towlower(c);
    return x == y;
}

/** prefix 是 p 的祖先或者就是 p 本身 */
static bool IsSameOrAncestor(const fs::path& prefix, const fs::path& p) {
    auto pi = prefix.begin();
    auto qi = p.begin();
    for (; pi != prefix.end(); ++pi, ++qi) {
        if (qi == p.end()) return false;          // p 比 prefix 还短，不可能是
        if (!SameComponent(*pi, *qi)) return false;
    }
    return true;
}

/** 取父目录（已经是根了就返回自己） */
static fs::path ParentOf(const fs::path& p) {
    fs::path q = p.parent_path();
    return q.empty() ? p : q;
}

/**
 * 这个路径是不是"不能整个删掉"的。
 *
 * ★ 判定方向很关键：**要删的路径是受保护路径的祖先（或它本身）就要拒绝**。
 *
 * 第一版写反了 —— 只挡住了"受保护目录本身"，却放过了它的**父目录**：
 * `delete("C:\Users")` 既不是盘根、也不等于 %USERPROFILE%（那是 C:\Users\你），
 * 于是被放行，`fs::remove_all` 直接把所有用户的 profile 全清了。
 * 护住了家目录本体却放过它的上一层，等于自废武功。
 */
bool NvFileBridge::IsProtectedRoot(const std::wstring& fullPath) const {
    std::error_code ec;
    fs::path p(fullPath);
    fs::path canon = fs::weakly_canonical(p, ec);
    if (ec) canon = p;

    // 收集"绝对不许整个删"的路径。注意这里会**连同它们的父目录一起保护**
    //（判定逻辑见上面），所以列具体的目录就够。
    std::vector<fs::path> prot;

    // 1) 所有盘根
    for (wchar_t d = L'A'; d <= L'Z'; ++d) {
        std::wstring root = { d, L':', L'\\' };
        std::error_code e2;
        if (fs::exists(fs::path(root), e2)) prot.push_back(fs::path(root));
    }

    // 2) 系统关键目录
    for (auto n : { L"SystemRoot", L"windir", L"ProgramFiles", L"ProgramFiles(x86)",
                    L"ProgramData" }) {
        std::wstring v = EnvOf(n);
        if (!v.empty()) prot.push_back(fs::path(v));
    }

    // 3) 家目录本体 + 它的一级常用目录
    std::wstring home = EnvOf(L"USERPROFILE");
    if (!home.empty()) {
        fs::path h(home);
        prot.push_back(h);
        // ★ 家目录的**父目录**（通常是 C:\Users）也要保护 ——
        //   这是第一版漏掉、能一句 delete 清空全机 profile 的那个洞
        prot.push_back(ParentOf(h));
        for (auto s : { L"Desktop", L"Documents", L"Downloads", L"Pictures",
                        L"Videos", L"Music", L"AppData", L"OneDrive" }) {
            prot.push_back(h / s);
        }
    }
    for (auto n : { L"LOCALAPPDATA", L"APPDATA", L"TEMP", L"TMP", L"USERPROFILE" }) {
        std::wstring v = EnvOf(n);
        if (!v.empty()) prot.push_back(fs::path(v));
    }

    // 4) "指定文件夹"档的授权根
    if (m_set.mode == L"folder" && !m_set.folder.empty()) {
        fs::path r(m_set.folder);
        fs::path rc = fs::weakly_canonical(r, ec);
        if (ec) rc = r;
        prot.push_back(rc);
    }

    for (const auto& r : prot) {
        std::error_code e3;
        fs::path rc = fs::weakly_canonical(r, e3);
        if (e3) rc = r;
        // 要删的是它的祖先、或者就是它 -> 拒绝
        if (IsSameOrAncestor(canon, rc)) return true;
    }
    return false;
}

std::string NvFileBridge::DoDelete(const std::string& path) {
    // 空路径绝对不能走到 remove_all：all 档它是家目录，folder 档它是授权根
    if (path.empty()) {
        return NvFail("delete 必须给明确路径，空路径被拒绝（那等于删掉整个根目录）");
    }

    bool allowed = false;
    std::wstring full = NvResolvePath(m_set, path, &allowed);
    if (!allowed) return NvFail(m_set.mode == L"off" ? kOffMsg : "这个路径不在允许范围内");

    if (IsProtectedRoot(full)) {
        return NvFail("这个路径是根目录或系统关键目录，不许整个删掉：" + NvUtf8(full));
    }

    std::error_code ec;
    fs::path p(full);
    if (!fs::exists(p, ec)) return NvFail("找不到：" + path);
    uintmax_t n = fs::remove_all(p, ec);
    if (ec || n == 0) return NvFail("删不掉：" + path);
    return NvOk(NvJson().Str("path", NvUtf8(full)).Body());
}

std::string NvFileBridge::DoStat(const std::string& path) {
    bool allowed = false;
    std::wstring full = NvResolvePath(m_set, path, &allowed);
    if (!allowed) return NvFail(m_set.mode == L"off" ? kOffMsg : "这个路径不在允许范围内");

    std::error_code ec;
    fs::path p(full);
    bool exists = fs::exists(p, ec);
    bool isdir = exists && fs::is_directory(p, ec);
    uint64_t size = (exists && !isdir) ? (uint64_t)fs::file_size(p, ec) : 0;

    return NvOk(NvJson()
        .Str("path", NvUtf8(full))
        .Bool("exists", exists)
        .Bool("dir", isdir)
        .Num("size", (long long)size)
        .Body());
}

// ============================================================
// 派发
// ============================================================

std::string NvFileBridge::Call(const std::string& method,
                               const std::vector<std::string>& args) {
    try {
        if (method == "info")       return InfoJson();
        if (method == "setMode")  { DoSetMode(ArgAt(args, 0)); return NvOk(""); }
        if (method == "pickFolder") { DoPickFolder();          return NvOk(""); }
        if (method == "requestAll") { DoRequestAll();          return NvOk(""); }
        if (method == "list")       return DoList(ArgAt(args, 0));
        if (method == "read")       return DoRead(ArgAt(args, 0));
        if (method == "write")      return DoWrite(ArgAt(args, 0), ArgAt(args, 1));
        if (method == "mkdir")      return DoMkdir(ArgAt(args, 0));
        if (method == "delete")     return DoDelete(ArgAt(args, 0));
        if (method == "stat")       return DoStat(ArgAt(args, 0));
        return NvFail("未知方法：" + method);
    } catch (const std::exception& e) {
        return NvFailErr(e);
    } catch (...) {
        return NvFail("未知错误");
    }
}
