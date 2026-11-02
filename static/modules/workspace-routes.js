export const SIDEBAR_VIEW_TITLES = Object.freeze({
  competition: "比赛概览",
  exercise: "题目工作区",
  "settings-dialog": "平台与设置",
  "attachment-manager-dialog": "附件管理",
  "tool-manager-dialog": "CTF 工具",
  "model-routing-page": "模型路由",
  "codex-task-page": "Codex 会话",
});

export const VIEW_PATHS = Object.freeze({
  competition: "/competition",
  exercise: "/exercises",
  "attachment-manager-dialog": "/attachments",
  "tool-manager-dialog": "/tools",
  "codex-task-page": "/codex",
  "model-routing-page": "/models",
  "settings-dialog": "/settings",
});

const PATH_VIEWS = Object.freeze(
  Object.fromEntries(Object.entries(VIEW_PATHS).map(([view, path]) => [path, view])),
);

export function pathForView(view) {
  return VIEW_PATHS[view] || VIEW_PATHS.exercise;
}

export function viewForPath(pathname) {
  const normalized = pathname.replace(/\/+$/, "") || "/";
  if (normalized === "/") return "exercise";
  return PATH_VIEWS[normalized] || "exercise";
}
