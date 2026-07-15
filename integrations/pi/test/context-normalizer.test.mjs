import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";
import contextNormalizer, { configureBridgeForTests } from "../extensions/context-normalizer.mjs";

const integrationRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repositoryRoot = resolve(integrationRoot, "..", "..");
const sourcePackage = join(repositoryRoot, "src");
const python = process.platform === "win32" ? "python.exe" : "python3";
const root = mkdtempSync(join(tmpdir(), "ctxnorm-pi-test-"));
const configuration = join(root, "configuration");
const source = join(root, "source");
mkdirSync(join(source, "kernel"), { recursive: true });
writeFileSync(join(source, "kernel", "payload.txt"), "kernel remote access tool\n", "utf8");
process.env.CONTEXT_NORMALIZER_HOME = configuration;

const bootstrap = "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('context_normalizer',run_name='__main__')";
function ctxnorm(args, input = "") {
  const result = spawnSync(python, ["-c", bootstrap, sourcePackage, ...args], {
    input,
    encoding: "utf8",
    windowsHide: true,
  });
  assert.equal(result.status, 0, result.stderr || result.stdout);
  return result.stdout;
}

ctxnorm(["init"]);
ctxnorm(["vocabulary", "add", "kernel", "runtime-boundary", "--bidirectional"]);
ctxnorm(["vocabulary", "add", "remote access tool", "remote administration utility", "--bidirectional"]);
const created = JSON.parse(ctxnorm([
  "workspace", "create", source, "--mode", "filesystem", "--normalize-paths", "--format", "json", "--yes",
]));
const mirror = created.mirror;
configureBridgeForTests(python, ["-c", bootstrap, sourcePackage]);

const previous = process.cwd();
process.chdir(mirror);
try {
  assert.equal(
    readFileSync(join(mirror, "runtime-boundary", "payload.txt"), "utf8"),
    "runtime-boundary remote administration utility\n",
  );
  const handlers = new Map();
  const notifications = [];
  contextNormalizer({ on(name, handler) { handlers.set(name, handler); } });
  const ctx = { ui: { notify(text, level) { notifications.push({ text, level }); } } };
  const input = await handlers.get("input")(
    { type: "input", text: "kernelkernel remote access tool", source: "interactive" },
    ctx,
  );
  assert.equal(input.action, "transform");
  assert.equal(input.text, "runtime-boundaryruntime-boundary remote administration utility");
  const stored = await handlers.get("message_end")({
    type: "message_end",
    message: { role: "user", content: [{ type: "text", text: input.text }] },
  }, ctx);
  assert.equal(stored.message.content[0].text, "kernelkernel remote access tool");
  const model = await handlers.get("context")({
    type: "context",
    messages: [stored.message],
  }, ctx);
  assert.equal(model.messages[0].content[0].text, input.text);
  const displayed = await handlers.get("message_end")({
    type: "message_end",
    message: { role: "assistant", stopReason: "stop", content: [{ type: "text", text: input.text }] },
  }, ctx);
  assert.equal(displayed.message.content[0].text, "kernelkernel remote access tool");
  writeFileSync(
    join(mirror, "runtime-boundary", "new remote administration utility.txt"),
    "runtime-boundary remote administration utility\n",
    "utf8",
  );
  await handlers.get("agent_settled")({ type: "agent_settled" }, ctx);
  assert.equal(notifications.length, 0);
  assert.equal(
    readFileSync(join(source, "kernel", "new remote access tool.txt"), "utf8"),
    "kernel remote access tool\n",
  );
} finally {
  process.chdir(previous);
  rmSync(root, { recursive: true, force: true });
}
process.stdout.write(JSON.stringify({ status: "passed", workspace: created.workspace_id }));
