// NovaDesk 桌面壳 —— "导出成 exe"桥（普通 C++ 类，不是 COM）
//
// 和文件桥一样走 postMessage 通道。分成两个对象，是因为权限含义不同：
// novaFiles 是"读写用户文件"，novaExe 是"用自己当模具再造一个 exe"。
#pragma once

#include "nv.h"

class NvExeBridge {
public:
    explicit NvExeBridge(const NvPayload& self) : m_self(self) {}

    std::string Call(const std::string& method, const std::vector<std::string>& args);

private:
    std::string DoInfo();
    std::string DoExport(const std::string& appName, const std::string& html);
    std::string DoReveal(const std::string& path);

    NvPayload m_self;
};
