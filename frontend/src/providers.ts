export type ProviderId = "qwen" | "deepseek" | "deepl" | "custom";
export type APIFormat = "chat_completions" | "messages";
export type Provider = {
  id: ProviderId;
  name: string;
  base_url: string;
  api_format: APIFormat;
  model: string;
  placeholder: string;
  docs: string;
  has_api_key: boolean;
  saved: boolean;
  deepl_source_language?: string;
  deepl_glossary_id?: string;
};

export type APIProfile = {
  id: string;
  name: string;
  base_url: string;
  api_format: APIFormat;
  model: string;
  has_api_key: boolean;
  concurrency: number;
  temperature: number;
  timeout: number;
  deepl_source_language?: string;
  deepl_glossary_id?: string;
};

export function providerId(url: string): ProviderId {
  try {
    const host = new URL(url).hostname.toLowerCase();
    if (["api.deepl.com", "api-free.deepl.com"].includes(host)) return "deepl";
    if (host === "api.deepseek.com") return "deepseek";
    if (
      [
        "dashscope.aliyuncs.com",
        "dashscope-intl.aliyuncs.com",
        "dashscope-us.aliyuncs.com",
      ].includes(host) ||
      /^[a-z0-9-]+\.(cn-beijing|ap-southeast-1|ap-northeast-1|eu-central-1|cn-hongkong)\.maas\.aliyuncs\.com$/.test(
        host,
      )
    )
      return "qwen";
  } catch {
    /* An unfinished address stays editable. */
  }
  return "custom";
}

export function normalizedEndpoint(url: string) {
  if (providerId(url.trim()) === "deepl") {
    try {
      const parsed = new URL(url.trim());
      if (
        ["", "/", "/v2", "/v2/", "/v2/translate", "/v2/translate/"].includes(
          parsed.pathname,
        )
      )
        return parsed.origin;
    } catch {
      /* Preserve incomplete input. */
    }
  }
  return url
    .trim()
    .replace(/\/+$/, "")
    .replace(/\/(chat\/completions|messages)$/, "");
}

export function apiFormatFromURL(url: string): APIFormat | undefined {
  const value = url.trim().replace(/\/+$/, "");
  if (value.endsWith("/messages")) return "messages";
  if (value.endsWith("/chat/completions")) return "chat_completions";
}

export function requestEndpoint(url: string, format: APIFormat): string {
  return (
    normalizedEndpoint(url) +
    (format === "messages" ? "/messages" : "/chat/completions")
  );
}
