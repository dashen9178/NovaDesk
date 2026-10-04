/**
 * NovaDesk 桌面壳：原生联网（只为"检查更新"用）
 *
 * 为什么页面不能自己 fetch：
 *   SeaTable 的行读取接口走的是它的 API 网关，而那个网关的响应里
 *   `Access-Control-Allow-Origin` **发了两遍**（网关一遍、上游 dtable-db 一遍）。
 *   按 CORS 规范，重复的 ACAO 会被合并成 "*, *"，不是 `*` 也不是具体 origin，
 *   浏览器一律判失败 —— 页面上看到的就是一句 "Failed to fetch"，
 *   而用 curl / PowerShell 打同一个地址却是 200。这个坑很难从报错里看出来。
 *
 * 所以桌面版走原生：WinHTTP 不受 CORS 管。
 * 只在**后台线程**请求，完成后 PostMessage 回 UI 线程再 ExecuteScript，
 * 绝不能在这个（UI 线程的）消息回调里直接等网络。
 */
// ★ winsock2.h 必须排在 windows.h **前面**。
//   反过来的话 windows.h 会先拉进老的 winsock.h，
//   再包含 ws2tcpip.h 就是一堆 "struct 类型重定义" —— 经典的坑。
#include <winsock2.h>
#include <ws2tcpip.h>

#include "nv_net.h"

#include <windows.h>
#include <winhttp.h>

#include <string>
#include <thread>
#include <vector>
#include <algorithm>

#include "nv.h"

#pragma comment(lib, "winhttp.lib")
#pragma comment(lib, "ws2_32.lib")

namespace {

std::wstring Utf8ToWide(const std::string& s) {
    if (s.empty()) return std::wstring();
    int n = MultiByteToWideChar(CP_UTF8, 0, s.c_str(), (int)s.size(), nullptr, 0);
    std::wstring w(n, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, s.c_str(), (int)s.size(), &w[0], n);
    return w;
}

/** 诊断日志：只有设了 NOVADESK_DEBUG=1 才写，平时一个字都不落盘 */
void NvNetLog(const std::string& msg) {
    static const bool on = [] {
        wchar_t buf[8]{};
        return GetEnvironmentVariableW(L"NOVADESK_DEBUG", buf, 8) > 0;
    }();
    if (!on) return;
    wchar_t tmp[MAX_PATH]{};
    if (!GetTempPathW(MAX_PATH, tmp)) return;
    std::wstring p = std::wstring(tmp) + L"novadesk_net.log";
    FILE* f = nullptr;
    if (_wfopen_s(&f, p.c_str(), L"a, ccs=UTF-8") != 0 || !f) return;
    fwprintf(f, L"%s\n", Utf8ToWide(msg).c_str());
    fclose(f);
}

struct Job {
    std::string id;
    std::string result;
};

/** 完成的活儿等着 UI 线程来取（UI 线程是唯一消费者，加个锁意思一下） */
CRITICAL_SECTION g_lock;
bool g_lockReady = false;
std::vector<Job> g_done;
HWND g_hwnd = nullptr;

/**
 * 这个主机名指向的地址是不是"只可能在局域网里"。
 *
 * ★ 和安卓那侧的 isPublicHost 是一回事，别只做一半：
 *   页面里跑的是**模型写的脚本**，这个桥能把响应正文原样回给它。
 *   不挡内网的话，一段面板脚本就能拿它去扫路由器后台、NAS、
 *   甚至云环境的元数据地址（169.254.169.254）。
 *
 * 做法：解析出来的地址里**只要有一个**是内网/回环/链路本地，就整条拒绝。
 * 宁可误杀，也不要放一段能扫内网的转发器出去。
 */
bool HostIsPrivate(const std::wstring& host) {
    if (host.empty()) return true;
    // 字面量形式的 localhost 直接毙
    if (_wcsicmp(host.c_str(), L"localhost") == 0) return true;

    // ★ GetAddrInfoW 需要先 WSAStartup，不然一律失败（WSANOTINITIALISED）——
    //   第一版漏了这一步，结果"解析失败就当内网"，把 cloud.seatable.cn
    //   也一起拒了：检查更新整条链路全挂，而且报的还是"内网地址被拒"，
    //   看起来像网络安全策略，其实是没初始化。
    static const bool wsa = [] {
        WSADATA d{};
        return WSAStartup(MAKEWORD(2, 2), &d) == 0;
    }();
    if (!wsa) return true;

    ADDRINFOW hints{};
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;
    ADDRINFOW* res = nullptr;
    if (GetAddrInfoW(host.c_str(), nullptr, &hints, &res) != 0 || !res) return true;

    bool priv = false;
    for (ADDRINFOW* p = res; p; p = p->ai_next) {
        if (p->ai_family == AF_INET) {
            auto* sa = reinterpret_cast<sockaddr_in*>(p->ai_addr);
            const unsigned long ip = ntohl(sa->sin_addr.s_addr);
            const unsigned long a = (ip >> 24) & 0xFF;
            const unsigned long b = (ip >> 16) & 0xFF;
            if (a == 10 || a == 127 || a == 0) priv = true;
            else if (a == 172 && b >= 16 && b <= 31) priv = true;
            else if (a == 192 && b == 168) priv = true;
            else if (a == 169 && b == 254) priv = true;   // 链路本地 / 云元数据
            else if (a >= 224) priv = true;               // 组播 / 保留
            if (priv) break;
        } else if (p->ai_family == AF_INET6) {
            auto* sa6 = reinterpret_cast<sockaddr_in6*>(p->ai_addr);
            const unsigned char* b6 = sa6->sin6_addr.u.Byte;
            const bool loopback = (b6[15] == 1) &&
                std::all_of(b6, b6 + 15, [](unsigned char c) { return c == 0; });
            const bool v4mapped = std::all_of(b6, b6 + 10,
                [](unsigned char c) { return c == 0; }) && b6[10] == 0xFF && b6[11] == 0xFF;
            if (loopback || (b6[0] & 0xFE) == 0xFC ||   // fc00::/7 唯一本地
                (b6[0] == 0xFE && (b6[1] & 0xC0) == 0x80) ||  // fe80::/10 链路本地
                v4mapped) {
                priv = true;
                break;
            }
        }
    }
    FreeAddrInfoW(res);
    return priv;
}

/** 一行一个头："Name: Value\nName2: Value2" */
std::string DoGet(const std::wstring& url, const std::string& headers) {    URL_COMPONENTS uc{};
    uc.dwStructSize = sizeof(uc);
    wchar_t host[256]{};
    wchar_t path[4096]{};
    uc.lpszHostName = host;   uc.dwHostNameLength = 255;
    uc.lpszUrlPath = path;    uc.dwUrlPathLength = 4095;

    if (!WinHttpCrackUrl(url.c_str(), (DWORD)url.size(), 0, &uc)) {
        return NvFail("网址解析不了");
    }
    // ★ 挡内网/回环 —— 见 HostIsPrivate 上面的说明。
    //   主机名可能是 IPv6 字面量，所以用宽字符串那版接口。
    if (HostIsPrivate(std::wstring(uc.lpszHostName, uc.dwHostNameLength))) {
        return NvFail("只允许公网地址：内网/本机地址已被拒绝");
    }
    const bool https = (uc.nScheme == INTERNET_SCHEME_HTTPS);

    // ★ 代理类型用 DEFAULT_PROXY，不用 AUTOMATIC_PROXY。
    //   AUTOMATIC_PROXY 每次请求都会去做一遍 WPAD 自动发现，
    //   在没配代理的机器上能卡十几到几十秒 —— 表现就是"点了没反应"。
    //   DEFAULT_PROXY 直接读系统里已经配好的代理，不会现场找。
    HINTERNET session = WinHttpOpen(L"NovaDesk/1.0",
                                    WINHTTP_ACCESS_TYPE_DEFAULT_PROXY,
                                    WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0);
    if (!session) return NvFail("WinHTTP 打不开");

    // 给每一步都设个上限，别让一次检查更新把线程挂在那儿
    WinHttpSetTimeouts(session, 8000, 8000, 8000, 15000);

    HINTERNET conn = WinHttpConnect(session, host, uc.nPort, 0);
    if (!conn) { WinHttpCloseHandle(session); return NvFail("连不上服务器"); }

    HINTERNET req = WinHttpOpenRequest(conn, L"GET", path, nullptr,
                                       WINHTTP_NO_REFERER,
                                       WINHTTP_DEFAULT_ACCEPT_TYPES,
                                       https ? WINHTTP_FLAG_SECURE : 0);
    if (!req) {
        WinHttpCloseHandle(conn); WinHttpCloseHandle(session);
        return NvFail("建不了请求");
    }

    // ★ 这个壳是 file:// 页面，正常浏览器会拒绝这种响应；
    //   这里我们自己读，所以只要服务端给数据就行 —— 不设任何 Origin。
    std::wstring wh = Utf8ToWide(headers);
    BOOL sent = WinHttpSendRequest(req,
                                   wh.empty() ? WINHTTP_NO_ADDITIONAL_HEADERS : wh.c_str(),
                                   wh.empty() ? 0 : (DWORD)-1L,
                                   WINHTTP_NO_REQUEST_DATA, 0, 0, 0);
    DWORD sendErr = sent ? 0 : GetLastError();
    if (sent) WinHttpReceiveResponse(req, nullptr);

    std::string body;
    DWORD status = 0;
    DWORD len = sizeof(status);
    WinHttpQueryHeaders(req, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
                        WINHTTP_HEADER_NAME_BY_INDEX, &status, &len, WINHTTP_NO_HEADER_INDEX);

    if (sent) {
        for (;;) {
            DWORD avail = 0;
            if (!WinHttpQueryDataAvailable(req, &avail) || avail == 0) break;
            // 上限 4 MB：检查更新只要一小段 JSON，给再多也没用，
            // 而且这是全量进内存的，不封顶等于给远端一个撑爆我们的机会
            if (body.size() + avail > 4u * 1024 * 1024) break;
            std::string chunk(avail, '\0');
            DWORD got = 0;
            if (!WinHttpReadData(req, &chunk[0], avail, &got) || got == 0) break;
            body.append(chunk.data(), got);
        }
    }

    WinHttpCloseHandle(req);
    WinHttpCloseHandle(conn);
    WinHttpCloseHandle(session);

    if (!sent) return NvFail("请求发不出去（" + std::to_string((unsigned long)sendErr) + "）");
    if (status < 200 || status >= 300) {
        return NvFail("服务端返回 " + std::to_string(status));
    }
    // ★ 正文必须**转义后**塞进 JSON。
    //   NvOk() 是直接拼的，它当参数已经是合法 JSON —— 把裸的 HTTP 正文丢进去，
    //   正文里的引号会把整个 JSON 撑破，页面那边 JSON.parse 直接抛，
    //   表现是 Promise 永远不 resolve（只有等 30 秒超时），极难查。
    return NvOk(NvJson().Num("status", status).Str("body", body).Body());
}

void Worker(std::string id, std::wstring url, std::string headers) {
    NvNetLog("worker start id=" + id);
    std::string out;
    try {
        out = DoGet(url, headers);
    } catch (...) {
        out = NvFail("原生联网时崩了");
    }
    NvNetLog("worker done id=" + id + " -> " + out.substr(0, 40));
    // 注意：日志里**只留前 40 个字符**，而且只有 NOVADESK_DEBUG=1 才写。
    // 响应正文里带着 SeaTable 的访问令牌，别整份往临时目录里倒。
    if (g_lockReady) EnterCriticalSection(&g_lock);
    g_done.push_back(Job{ id, out });
    if (g_lockReady) LeaveCriticalSection(&g_lock);
    NvNetLog("posting WM_NV_NET_DONE hwnd=" + std::to_string((unsigned long long)(uintptr_t)g_hwnd));
    if (g_hwnd) {
        BOOL okp = PostMessageW(g_hwnd, WM_NV_NET_DONE, 0, 0);
        if (!okp) NvNetLog("PostMessage 失败 err=" + std::to_string(GetLastError()));
    }
}

}  // namespace

void NvNetInit(HWND hwnd) {
    g_hwnd = hwnd;
    if (!g_lockReady) {
        InitializeCriticalSection(&g_lock);
        g_lockReady = true;
    }
    NvNetLog("init hwnd=" + std::to_string((unsigned long long)(uintptr_t)hwnd));
}

void NvNetShutdown() {
    // ★ 刻意**不 DeleteCriticalSection**，也不把 g_lockReady 置回 false。
    //   请求跑在分离线程上，进程退出时它可能正卡在里面 ——
    //   删掉临界区之后它再进去就是访问已释放内存，直接崩。
    //   进程都要没了，这么一点泄漏无所谓；崩一下才是真的难看。
    //   只把窗口句柄清掉：之后完成的请求就不再往一个死窗口发消息。
    g_hwnd = nullptr;
}

std::string NvNetStart(const std::string& id, const std::vector<std::string>& args) {
    if (args.size() < 1) return NvFail("netGet 需要网址");
    std::wstring url = Utf8ToWide(args[0]);
    // 只放行 https：这个口子是给"读云端更新表"用的，
    // 明文 http 没必要开，开了就是给中间人递刀子
    if (url.rfind(L"https://", 0) != 0) return NvFail("只允许 https 地址");

    std::string headers = args.size() > 1 ? args[1] : std::string();
    // 头是一行一个，别让调用方塞进换行搞出额外的头
    std::string clean;
    for (char c : headers) {
        if (c == '\r') continue;
        clean.push_back(c);
    }

    std::thread(Worker, id, url, clean).detach();
    return std::string();   // 空 = "还在跑，结果稍后从 WM_NV_NET_DONE 回来"
}

void NvNetDrain(void (*reply)(const std::string& id, const std::string& json)) {
    if (!reply) return;
    NvNetLog("drain called");
    for (;;) {
        Job job;
        bool has = false;
        if (g_lockReady) EnterCriticalSection(&g_lock);
        if (!g_done.empty()) {
            job = g_done.front();
            g_done.erase(g_done.begin());
            has = true;
        }
        if (g_lockReady) LeaveCriticalSection(&g_lock);
        if (!has) break;
        reply(job.id, job.result);
    }
}
