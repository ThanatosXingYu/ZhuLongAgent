export const TOOL_CATEGORY_DEFINITIONS = Object.freeze([
  { key: "general", label: "通用", icon: "layers-3", note: "跨题型基础库与通用能力" },
  { key: "web", label: "Web / 网络", icon: "globe-2", note: "目录扫描、请求测试与网络分析" },
  { key: "pwn", label: "Pwn / 二进制利用", icon: "binary", note: "调试、ROP、漏洞利用与二进制保护检查" },
  { key: "reverse", label: "Reverse / 逆向", icon: "scan-code", note: "反汇编、反编译、仿真与动态插桩" },
  { key: "crypto", label: "Crypto / 密码学", icon: "key-round", note: "约束求解、数论、哈希与口令恢复" },
  { key: "forensics", label: "Forensics / 取证", icon: "microscope", note: "文件雕刻、内存、元数据与样本分析" },
  { key: "misc", label: "Misc / 综合", icon: "blocks", note: "压缩、协议、网络与综合辅助工具" },
]);

export function groupToolsByCategory(tools) {
  const grouped = new Map(TOOL_CATEGORY_DEFINITIONS.map((category) => [
    category.key,
    { ...category, tools: [] },
  ]));
  (Array.isArray(tools) ? tools : []).forEach((tool) => {
    const category = tool.category || "misc";
    if (!grouped.has(category)) {
      grouped.set(category, {
        key: category,
        label: tool.categoryLabel || "其他工具",
        icon: "wrench",
        note: "",
        tools: [],
      });
    }
    grouped.get(category).tools.push(tool);
  });
  return grouped;
}
