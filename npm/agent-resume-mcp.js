#!/usr/bin/env node
import { spawn } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const py = process.env.PYTHON || process.env.PYTHON3 || "python3";
const child = spawn(py, [resolve(root, "agent_resume.py"), "mcp"], { stdio: "inherit", env: process.env });
child.on("error", (e) => { console.error(`agent-resume-mcp: ${e.message}`); process.exit(127); });
child.on("exit", (code, signal) => signal ? process.kill(process.pid, signal) : process.exit(code ?? 0));
