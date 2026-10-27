// work-run gate for pi: the same decisions as the opencode plugin (research-gate.js, cardDecide), applied to pi's tool calls.
// Only active when GATE_FILE is set. The ledger the opencode plugin adds to the system prompt is NOT ported (blocks only).
// @ts-nocheck
import fs from "node:fs"
import os from "node:os"
import path from "node:path"
import { cardDecide, loadCard, newState } from "/home/andre/.config/opencode/plugins/research-gate.js"

const TOOL = { bash: "bash", read: "read", write: "write", edit: "edit", grep: "grep", find: "glob", ls: "list" } // pi name -> gate name
const LOG = path.join(os.homedir(), ".local", "state", "opencode-gate", "gate.jsonl")

function audit(rec) {
  try {
    fs.mkdirSync(path.dirname(LOG), { recursive: true })
    fs.appendFileSync(LOG, JSON.stringify({ ts: new Date().toISOString(), ...rec }) + "\n")
  } catch {}
}

export default function (pi) {
  const card = loadCard()
  if (!card) return
  let st = newState()
  pi.on("tool_call", async (event) => {
    const tool = TOOL[event.toolName] ?? event.toolName
    const s = JSON.stringify(event.input ?? {})
    const { block, next } = cardDecide(card, st, tool, s)
    const logged = tool === "write" || tool === "edit" ? JSON.stringify({ path: event.input?.path, bytes: String(event.input?.content ?? "").length }) : s.slice(0, 400)
    audit({ session: process.env.GATE_SESSION || "pi-work", card: card.id, agent: "pi", tool, blocked: !!block, rule: block ? "CARD" : null, reason: block || undefined, input: logged })
    if (block) { st = { ...st, blocks: (st.blocks || 0) + 1 }; return { block: true, reason: block } }
    st = next
    return undefined
  })
}
