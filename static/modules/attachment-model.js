export function attachmentFileMatchesFilter(file, filter) {
  if (filter === "missing") return !file.error && !file.exists;
  if (filter === "ready") return Boolean(file.exists) && !file.error;
  if (filter === "error") return Boolean(file.error);
  return true;
}

export function filterAttachmentExercise(exercise, { query = "", filter = "all" } = {}) {
  const normalizedQuery = String(query || "").trim().toLocaleLowerCase("zh-CN");
  const files = Array.isArray(exercise?.attachments) ? exercise.attachments : [];
  const fileText = files
    .map((file) => `${file.name || ""} ${file.path || ""}`)
    .join(" ");
  const exerciseText = `${exercise.id || ""} ${exercise.name || ""} ${exercise.category || ""} ${fileText}`
    .toLocaleLowerCase("zh-CN");
  if (normalizedQuery && !exerciseText.includes(normalizedQuery)) return null;
  if (exercise.error) {
    return filter === "error" || filter === "all"
      ? { ...exercise, attachments: [] }
      : null;
  }
  const filteredFiles = filter === "all"
    ? files
    : files.filter((file) => attachmentFileMatchesFilter(file, filter));
  if (filter !== "all" && !filteredFiles.length) return null;
  return { ...exercise, attachments: filteredFiles };
}
