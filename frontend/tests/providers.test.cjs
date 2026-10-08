const test = require("node:test");
const assert = require("node:assert/strict");
const {
  providerId,
  normalizedEndpoint,
  apiFormatFromURL,
  requestEndpoint,
} = require("../../tmp/reader-tests/providers.js");

test("provider selection distinguishes official hosts from lookalikes", () => {
  assert.equal(
    providerId(
      "https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
    ),
    "qwen",
  );
  assert.equal(
    providerId("https://dashscope.aliyuncs.com/compatible-mode/v1"),
    "qwen",
  );
  assert.equal(providerId("https://api.deepseek.com"), "deepseek");
  assert.equal(providerId("https://api.deepl.com"), "deepl");
  assert.equal(providerId("https://api-free.deepl.com/v2/translate"), "deepl");
  assert.equal(providerId("https://api.deepl.com.evil.example"), "custom");
  assert.equal(providerId("https://api.deepseek.com.evil.example"), "custom");
  assert.equal(
    providerId("https://workspace.cn-beijing.maas.aliyuncs.com.evil.example"),
    "custom",
  );
  assert.equal(providerId("unfinished URL"), "custom");
});

test("explicit protocol switches the endpoint while full URLs normalize to the same base", () => {
  const base = "http://proxy.example:23000/v1";
  assert.equal(requestEndpoint(base, "messages"), base + "/messages");
  assert.equal(requestEndpoint(base + "/messages/", "chat_completions"), base + "/chat/completions");
  assert.equal(normalizedEndpoint(base + "/messages/"), base);
  assert.equal(apiFormatFromURL(base + "/messages/"), "messages");
  assert.equal(apiFormatFromURL(base + "/chat/completions"), "chat_completions");
  assert.equal(apiFormatFromURL(base), undefined);
});

test("saved key status matches backend endpoint normalization", () => {
  assert.equal(normalizedEndpoint(" https://api.deepl.com/v2/translate/ "), "https://api.deepl.com");
  assert.notEqual(normalizedEndpoint("https://api.deepl.com"), normalizedEndpoint("https://api-free.deepl.com"));
  assert.equal(
    normalizedEndpoint(" https://api.deepseek.com/v1/chat/completions/ "),
    "https://api.deepseek.com/v1",
  );
  assert.notEqual(
    normalizedEndpoint("https://api.example/v1"),
    normalizedEndpoint("https://api.example/another"),
  );
});
