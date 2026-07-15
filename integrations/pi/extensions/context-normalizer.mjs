import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { resolve } from "node:path";

const MAX_OUTPUT_BYTES = 4 * 1024 * 1024;
const TIMEOUT_MS = 30_000;
let testBridge = null;

export function configureBridgeForTests(program, argsPrefix = []) {
  testBridge = { program, argsPrefix: [...argsPrefix] };
}

function bridgeProgram() {
  if (testBridge) return testBridge.program;
  return process.platform === "win32" ? "ctxnorm.exe" : "ctxnorm";
}

function bridgeArgs(kind) {
  const values = {
    submit: ["bridge", "submit"],
    complete: ["bridge", "complete"],
    model: ["bridge", "normalize", "--direction", "model"],
    display: ["bridge", "normalize", "--direction", "display"],
  };
  const args = values[kind];
  if (!args) throw new Error(`unsupported normalization operation: ${kind}`);
  return testBridge ? [...testBridge.argsPrefix, ...args] : args;
}

export function isNormalizedWorkspace(cwd = process.cwd()) {
  return existsSync(resolve(cwd, ".ctxnorm-workspace.json"));
}

export function runBridge(kind, input = "") {
  return new Promise((resolvePromise, reject) => {
    const child = spawn(bridgeProgram(), bridgeArgs(kind), {
      shell: false,
      windowsHide: true,
      stdio: ["pipe", "pipe", "pipe"],
    });
    const stdout = [];
    const stderr = [];
    let stdoutBytes = 0;
    let stderrBytes = 0;
    let settled = false;
    let timer;

    const fail = (error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      child.kill();
      reject(error);
    };
    const collect = (chunks, streamName) => (chunk) => {
      const next = streamName === "stdout"
        ? (stdoutBytes += chunk.length)
        : (stderrBytes += chunk.length);
      if (next > MAX_OUTPUT_BYTES) {
        fail(new Error(`context normalization ${streamName} exceeded ${MAX_OUTPUT_BYTES} bytes`));
        return;
      }
      chunks.push(chunk);
    };
    timer = setTimeout(() => {
      fail(new Error(`context normalization ${kind} timed out after ${TIMEOUT_MS} ms`));
    }, TIMEOUT_MS);

    child.on("error", (error) => fail(new Error(`failed to start ctxnorm: ${error.message}`)));
    child.stdout.on("data", collect(stdout, "stdout"));
    child.stderr.on("data", collect(stderr, "stderr"));
    child.on("close", (code) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      const output = Buffer.concat(stdout).toString("utf8");
      const detail = Buffer.concat(stderr).toString("utf8").trim();
      if (code !== 0) {
        reject(new Error(detail || `ctxnorm ${kind} exited with code ${code}`));
      } else if (output.includes("\0")) {
        reject(new Error("ctxnorm output contained a NUL byte"));
      } else {
        resolvePromise(output);
      }
    });
    child.stdin.on("error", (error) => fail(new Error(`failed to send text to ctxnorm: ${error.message}`)));
    child.stdin.end(input, "utf8");
  });
}

async function normalizeMessageText(message, direction) {
  if (typeof message.content === "string") {
    return { ...message, content: await runBridge(direction, message.content) };
  }
  if (!Array.isArray(message.content)) return message;
  const content = [];
  for (const block of message.content) {
    if (block?.type === "text" && typeof block.text === "string") {
      content.push({ ...block, text: await runBridge(direction, block.text) });
    } else {
      content.push(block);
    }
  }
  return { ...message, content };
}

function errorText(error) {
  return error instanceof Error ? error.message : String(error);
}

export default function contextNormalizer(pi) {
  let completionPending = false;
  let finalStopReason = null;

  pi.on("input", async (event, ctx) => {
    if (!isNormalizedWorkspace() || event.source === "extension") {
      return { action: "continue" };
    }
    try {
      const normalized = await runBridge("submit", event.text);
      if (event.text && !normalized) throw new Error("ctxnorm returned empty text");
      completionPending = true;
      finalStopReason = null;
      return { action: "transform", text: normalized, images: event.images };
    } catch (error) {
      ctx.ui.notify(`Context normalization failed: ${errorText(error)}`, "error");
      return { action: "handled" };
    }
  });

  pi.on("context", async (event, ctx) => {
    if (!isNormalizedWorkspace()) return;
    try {
      const messages = [];
      for (const current of event.messages) {
        messages.push(await normalizeMessageText(current, "model"));
      }
      return { messages };
    } catch (error) {
      ctx.ui.notify(`Context normalization failed: ${errorText(error)}`, "error");
      throw error;
    }
  });

  pi.on("message_end", async (event, ctx) => {
    if (!isNormalizedWorkspace()) return;
    if (event.message.role === "assistant") {
      finalStopReason = event.message.stopReason ?? null;
      if (event.message.stopReason !== "stop") return;
    }
    if (!["user", "assistant", "toolResult"].includes(event.message.role)) return;
    try {
      return { message: await normalizeMessageText(event.message, "display") };
    } catch (error) {
      ctx.ui.notify(`Context normalization failed: ${errorText(error)}`, "error");
    }
  });

  pi.on("agent_settled", async (_event, ctx) => {
    if (!isNormalizedWorkspace() || !completionPending) return;
    if (finalStopReason !== "stop") {
      completionPending = false;
      ctx.ui.notify("Workspace normalization paused because the model turn did not complete.", "warning");
      return;
    }
    try {
      await runBridge("complete");
      completionPending = false;
    } catch (error) {
      ctx.ui.notify(`Context normalization failed: ${errorText(error)}`, "error");
    }
  });
}
