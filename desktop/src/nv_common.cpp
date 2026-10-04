// NovaDesk 桌面壳 —— 公共实现
#include "nv.h"

#include <filesystem>
#include <fstream>
#include <sstream>
#include <algorithm>
#include <stdexcept>

namespace fs = std::filesystem;

// ============================================================
// 编码
// ============================================================

std::string NvUtf8(const std::wstring& w) {
    if (w.empty()) return {};
    int n = WideCharToMultiByte(CP_UTF8, 0, w.c_str(), (int)w.size(),
                                nullptr, 0, nullptr, nullptr);
    if (n <= 0) return {};
    std::string s((size_t)n, '\0');
    WideCharToMultiByte(CP_UTF8, 0, w.c_str(), (int)w.size(), s.data(), n, nullptr, nullptr);
    return s;
}

std::wstring NvWide(const std::string& s) {
    if (s.empty()) return {};
    int n = MultiByteToWideChar(CP_UTF8, 0, s.c_str(), (int)s.size(), nullptr, 0);
    if (n <= 0) return {};
    std::wstring w((size_t)n, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, s.c_str(), (int)s.size(), w.data(), n);
    return w;
}

// ============================================================
// JSON
// ============================================================

std::string NvJsonEscape(const std::string& s) {
    std::string o;
    o.reserve(s.size() + 16);
    for (unsigned char c : s) {
        switch (c) {
            case '"':  o += "\\\""; break;
            case '\\': o += "\\\\"; break;
            case '\n': o += "\\n";  break;
            case '\r': o += "\\r";  break;
            case '\t': o += "\\t";  break;
            case '\b': o += "\\b";  break;
            case '\f': o += "\\f";  break;
            default:
                if (c < 0x20) {
                    char buf[8];
                    sprintf_s(buf, "\\u%04x", c);
                    o += buf;
                } else {
                    // UTF-8 字节原样保留：JSON 允许直接放 UTF-8
                    o += (char)c;
                }
        }
    }
    return o;
}

std::string NvOk(const std::string& body) {
    return body.empty() ? std::string("{\"ok\":true}") : ("{\"ok\":true," + body + "}");
}

std::string NvFail(const std::string& msg) {
    return "{\"ok\":false,\"error\":\"" + NvJsonEscape(msg) + "\"}";
}

std::string NvFailErr(const std::exception& e) {
    return NvFail(std::string("操作失败：") + e.what());
}

// ============================================================
// 尾部页脚：读自己
// ============================================================

static bool ReadAt(HANDLE h, uint64_t offset, uint64_t len, std::string& out) {
    if (len == 0) { out.clear(); return true; }
    if (len > (uint64_t)512 * 1024 * 1024) return false;   // 别被坏页脚骗着去读 4GB
    LARGE_INTEGER pos{};
    pos.QuadPart = (LONGLONG)offset;
    if (!SetFilePointerEx(h, pos, nullptr, FILE_BEGIN)) return false;
    out.assign((size_t)len, '\0');
    uint64_t got = 0;
    while (got < len) {
        DWORD chunk = (DWORD)std::min<uint64_t>(len - got, 1u << 20);
        DWORD read = 0;
        if (!ReadFile(h, out.data() + got, chunk, &read, nullptr) || read == 0) return false;
        got += read;
    }
    return true;
}

static bool ReadPayloadFrom(const std::wstring& exePath, NvPayload& p);

NvPayload NvLoadPayload() {
    NvPayload p;
    wchar_t buf[MAX_PATH * 4];
    DWORD n = GetModuleFileNameW(nullptr, buf, (DWORD)(sizeof(buf) / sizeof(buf[0])));
    // 返回 0 = 失败；返回 == 缓冲区大小 = 被截断了（路径太长）。
    // 截断的话后面会拿着一个不存在的路径去读页脚，报出来的错完全对不上原因。
    if (n == 0 || n >= sizeof(buf) / sizeof(buf[0])) return p;
    p.exePath.assign(buf, n);
    ReadPayloadFrom(p.exePath, p);
    return p;
}

/** 从任意一个 exe 读页脚（导出后要拿它自检，所以不能只会读自己） */
static bool ReadPayloadFrom(const std::wstring& exePath, NvPayload& p) {
    HANDLE h = CreateFileW(exePath.c_str(), GENERIC_READ,
                           FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                           nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h == INVALID_HANDLE_VALUE) return false;

    LARGE_INTEGER size{};
    if (!GetFileSizeEx(h, &size) || size.QuadPart < (LONGLONG)sizeof(NvFooter)) {
        CloseHandle(h);
        return false;
    }

    NvFooter f{};
    LARGE_INTEGER pos{};
    pos.QuadPart = size.QuadPart - (LONGLONG)sizeof(NvFooter);
    DWORD read = 0;
    bool ok = SetFilePointerEx(h, pos, nullptr, FILE_BEGIN) &&
              ReadFile(h, &f, sizeof(f), &read, nullptr) &&
              read == sizeof(f) &&
              memcmp(f.magic, NV_MAGIC, 8) == 0;

    // 页脚里的偏移必须落在文件内，否则当作没有（防止手工拼出来的越界值）
    if (ok) {
        uint64_t total = (uint64_t)size.QuadPart;
        if (f.htmlOffset > total || f.htmlLen > total - f.htmlOffset) ok = false;
        if (f.nameOffset > total || f.nameLen > total - f.nameOffset) ok = false;
    }

    if (ok) {
        std::string name;
        ok = ReadAt(h, f.htmlOffset, f.htmlLen, p.html) &&
             ReadAt(h, f.nameOffset, f.nameLen, name);
        if (ok) {
            p.appName = NvWide(name);
            p.shellSize = f.htmlOffset;   // 页面之前的那一段就是纯壳
            p.ok = true;
        }
    }

    CloseHandle(h);
    return ok;
}

// ============================================================
// 数据目录 / 设置
// ============================================================

std::wstring NvDataDir() {
    wchar_t buf[MAX_PATH * 4];
    DWORD n = GetEnvironmentVariableW(L"LOCALAPPDATA", buf,
                                      (DWORD)(sizeof(buf) / sizeof(buf[0])));
    std::wstring dir = (n > 0 && n < sizeof(buf) / sizeof(buf[0]))
                           ? std::wstring(buf, n)
                           : std::wstring(L".");
    dir += L"\\NovaDesk";
    std::error_code ec;
    fs::create_directories(fs::path(dir), ec);
    return dir;
}

std::wstring NvExeKey() {
    wchar_t buf[MAX_PATH * 4];
    DWORD n = GetModuleFileNameW(nullptr, buf, (DWORD)(sizeof(buf) / sizeof(buf[0])));
    std::wstring s = (n > 0 && n < sizeof(buf) / sizeof(buf[0]))
                         ? std::wstring(buf, n)
                         : std::wstring(L"novadesk");
    for (auto& c : s) c = (wchar_t)towlower(c);

    // FNV-1a，够稳够短；只要 exe 路径不变，算出来就一样
    uint64_t h = 1469598103934665603ULL;
    for (wchar_t c : s) {
        h ^= (uint64_t)c;
        h *= 1099511628211ULL;
    }
    wchar_t out[32];
    swprintf_s(out, L"%016llx", (unsigned long long)h);
    return out;
}

/**
 * 设置文件按 exe 分家：%LOCALAPPDATA%\NovaDesk\settings\<exe哈希>.ini
 *
 * 不共用一个 desktop.ini 是刻意的 —— 见 nv.h 里 NvExeKey 的说明。
 */
static std::wstring SettingsPath() {
    std::wstring dir = NvDataDir() + L"\\settings";
    std::error_code ec;
    fs::create_directories(fs::path(dir), ec);
    return dir + L"\\" + NvExeKey() + L".ini";
}

NvSettings NvLoadSettings() {
    NvSettings s;
    std::ifstream in(fs::path(SettingsPath()), std::ios::binary);
    if (!in) return s;

    std::stringstream ss;
    ss << in.rdbuf();
    std::string text = ss.str();

    // 极简 key=value，够用就行
    std::istringstream lines(text);
    std::string line;
    while (std::getline(lines, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        auto eq = line.find('=');
        if (eq == std::string::npos) continue;
        std::string k = line.substr(0, eq);
        std::string v = line.substr(eq + 1);
        if (k == "mode")        s.mode = NvWide(v);
        else if (k == "folder") s.folder = NvWide(v);
    }

    if (s.mode != L"off" && s.mode != L"folder" && s.mode != L"all") s.mode = L"off";
    return s;
}

void NvSaveSettings(const NvSettings& s) {
    std::ofstream out(fs::path(SettingsPath()), std::ios::binary | std::ios::trunc);
    if (!out) return;
    out << "mode=" << NvUtf8(s.mode) << "\r\n";
    out << "folder=" << NvUtf8(s.folder) << "\r\n";
}

// ============================================================
// 路径解析
// ============================================================

/** 路径里出现 ".." 就拒掉：不是防不住，是没必要为它冒风险 */
static bool HasDotDot(const fs::path& p) {
    for (const auto& part : p) {
        if (part == L"..") return true;
    }
    return false;
}

/** p 是不是在 root 里面（含 root 本身） */
static bool IsInside(const fs::path& root, const fs::path& p) {
    std::error_code ec;
    auto r = fs::weakly_canonical(root, ec);
    if (ec) r = root;
    auto q = fs::weakly_canonical(p, ec);
    if (ec) q = p;

    auto ri = r.begin();
    auto qi = q.begin();
    for (; ri != r.end(); ++ri, ++qi) {
        if (qi == q.end()) return false;
        // Windows 路径大小写不敏感，比较时统一小写
        std::wstring a = ri->wstring(), b = qi->wstring();
        std::transform(a.begin(), a.end(), a.begin(), ::towlower);
        std::transform(b.begin(), b.end(), b.begin(), ::towlower);
        if (a != b) return false;
    }
    return true;
}

std::wstring NvResolvePath(const NvSettings& s, const std::string& pathUtf8,
                           bool* outAllowed) {
    if (outAllowed) *outAllowed = false;

    std::wstring w = NvWide(pathUtf8);
    // 顺手把两种斜杠统一，AI 经常混着写
    std::replace(w.begin(), w.end(), L'/', L'\\');
    while (!w.empty() && w.front() == L' ') w.erase(w.begin());
    while (!w.empty() && w.back() == L' ') w.pop_back();

    fs::path p(w);
    if (HasDotDot(p)) return {};

    if (s.mode == L"all") {
        if (outAllowed) *outAllowed = true;
        if (w.empty()) {
            // 空路径 = 用户主目录。比丢个 C:\ 给他友好得多
            wchar_t up[MAX_PATH * 4];
            DWORD n = GetEnvironmentVariableW(L"USERPROFILE", up,
                                              (DWORD)(sizeof(up) / sizeof(up[0])));
            if (n > 0 && n < sizeof(up) / sizeof(up[0])) return std::wstring(up, n);
            return L"C:\\";
        }
        return w;
    }

    if (s.mode == L"folder") {
        if (s.folder.empty()) return {};
        fs::path root(s.folder);
        fs::path full = p.is_absolute() ? p : (root / p);
        if (!IsInside(root, full)) return {};
        if (outAllowed) *outAllowed = true;
        return full.wstring();
    }

    return {};   // mode == off
}

// ============================================================
// 导出小应用：拷壳 -> 追加页面 -> 写页脚 -> 自检
// ============================================================

std::wstring NvExportDir() {
    wchar_t up[MAX_PATH * 4];
    DWORD n = GetEnvironmentVariableW(L"USERPROFILE", up,
                                      (DWORD)(sizeof(up) / sizeof(up[0])));
    if (n == 0 || n >= sizeof(up) / sizeof(up[0])) return NvDataDir() + L"\\export";

    std::wstring home(up, n);
    std::error_code ec;
    std::wstring desk = home + L"\\Desktop";
    if (fs::is_directory(fs::path(desk), ec)) return desk;
    return home;
}

bool NvExportExe(const std::wstring& selfPath, uint64_t shellSize,
                 const std::string& html, const std::wstring& appName,
                 const std::wstring& outPath) {
    if (shellSize == 0 || html.empty()) return false;

    std::error_code ec;
    fs::path out(outPath);
    if (out.has_parent_path()) fs::create_directories(out.parent_path(), ec);

    // 1) 只拷"纯壳"那一段（前 shellSize 字节），不要把上一份内嵌页面一起带上
    {
        std::ifstream in(fs::path(selfPath), std::ios::binary);
        if (!in) return false;
        std::ofstream o(fs::path(outPath), std::ios::binary | std::ios::trunc);
        if (!o) return false;

        std::vector<char> buf(1 << 20);
        uint64_t left = shellSize;
        while (left > 0) {
            size_t want = (size_t)std::min<uint64_t>(left, buf.size());
            in.read(buf.data(), (std::streamsize)want);
            if (in.gcount() != (std::streamsize)want) return false;
            o.write(buf.data(), (std::streamsize)want);
            left -= want;
        }
        o.flush();
        if (!o) return false;
    }

    // 2) 追加 页面 + 应用名 + 页脚
    const std::string nameUtf8 = NvUtf8(appName);
    {
        std::ofstream o(fs::path(outPath), std::ios::binary | std::ios::app);
        if (!o) return false;

        uint64_t htmlOffset = shellSize;
        o.write(html.data(), (std::streamsize)html.size());

        uint64_t nameOffset = htmlOffset + (uint64_t)html.size();
        o.write(nameUtf8.data(), (std::streamsize)nameUtf8.size());

        NvFooter f{};
        memcpy(f.magic, NV_MAGIC, sizeof(f.magic));
        f.htmlOffset = htmlOffset;
        f.htmlLen    = (uint64_t)html.size();
        f.nameOffset = nameOffset;
        f.nameLen    = (uint32_t)nameUtf8.size();
        f.reserved   = 0;
        o.write(reinterpret_cast<const char*>(&f), sizeof(f));

        o.flush();
        if (!o) return false;
    }

    // 3) 自检：像壳启动时那样把页脚读回来，对不上就把半成品删掉。
    //    不验就交给用户的话，他双击只会看到"没内嵌页面"，一点线索都没有。
    NvPayload check;
    if (!ReadPayloadFrom(outPath, check)) {
        fs::remove(out, ec);
        return false;
    }
    bool good = check.html == html && check.appName == appName;
    if (!good) {
        fs::remove(out, ec);
        return false;
    }
    return true;
}
