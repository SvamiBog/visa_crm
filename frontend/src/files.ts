// Files the browser sends to the API: read as base64 for a JSON body (imports, documents).

export function readBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1] ?? "");
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

/** «840 KB», «2.4 MB» in the interface language. */
export function formatSize(bytes: number, language: string): string {
  const mb = bytes >= 1024 * 1024;
  return new Intl.NumberFormat(language, {
    style: "unit",
    unit: mb ? "megabyte" : "kilobyte",
    maximumFractionDigits: mb ? 1 : 0,
  }).format(Math.max(bytes / (mb ? 1024 * 1024 : 1024), mb ? 0 : 1));
}
