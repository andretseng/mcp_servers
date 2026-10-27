---
name: llm-claude-push
description: 備份 ~/.claude 設定/memory 與 ~/tools（MCP servers 原始碼）。掃描 git 變更、自動產生 commit 訊息，各自同步到 GitHub + Gitea。
argument-hint: "[可選：手動指定 commit 訊息，兩個 repo 共用]"
disable-model-invocation: true
---

# /llm-claude-push — 備份 ~/.claude ＋ ~/tools

依序備份兩個獨立 git repo，各自 commit＋push，互不影響（其中一個沒變更不擋另一個）：

1. **~/.claude** — 設定與 auto-memory，白名單 `.gitignore`
2. **~/tools** — MCP servers 原始碼（serial-mcp/sigrok-mcp/tek-scope-mcp），一般黑名單
   `.gitignore`（排除 `.venv/`、`__pycache__/` 等，`git add -A` 不會撈進虛擬環境）

## Part A：~/.claude

> 白名單 `.gitignore`：只追蹤 config（`CLAUDE.md`、`settings.json`、`skills/`、`plans/`、`agents/`、
> plugin 宣告檔）與 auto-memory（`projects/*/memory/`）。secrets（`.credentials.json`）與 session
> transcript（`*.jsonl`）永遠被排除，`git add -A` 也掃不進來。

### 第一步：掃描變更

```
git -C ~/.claude status --short
git -C ~/.claude diff HEAD
```

### 第二步：判斷狀態

**情況 A：working tree clean（沒有未提交變更）**

- 檢查 unpushed commits：`git -C ~/.claude log origin/main..HEAD --oneline`
- 若有 → 直接跳到第四步推送（不傳 Message）
- 若無 → 記下「~/.claude 已是最新」，跳到 Part B

**情況 B：有 `$ARGUMENTS`（使用者手動提供訊息）** — 套用適當前綴（見下），跳到第四步

**情況 C：有變更且無手動訊息** — 繼續第三步分析

### 第三步：分析變更，產生 commit 訊息

| 條件 | 前綴 |
|------|------|
| 改動 `projects/*/memory/`（auto-memory fact / MEMORY.md） | `memory:` |
| 改動 `skills/`（新增或修改 skill） | `skill:` |
| 改動 `CLAUDE.md`、`settings.json`、`.gitignore`、`*.sh`、`plugins/*.json` | `config:` |
| 改動 `plans/`、`agents/` | `plan:` / `agent:` |
| 混合多種類型 | 依主要變更選前綴（memory > skill > config） |

從變更的**檔名**和 **diff 內容**推斷重點，產生一行簡短描述（**50 字以內**，不加句號）。

### 第四步：呼叫推送腳本

```bash
bash ~/.claude/claude-push.sh "<產生的訊息>"   # 有 commit 訊息時
bash ~/.claude/claude-push.sh                  # 只推送（無新 commit）
```

腳本會 `git push origin main`（GitHub，必推）＋嘗試 `git push gitea main`（LAN 內才成功，
不在區網會自動略過不報錯）。

## Part B：~/tools

### 第一步：掃描變更

```
git -C ~/tools status --short
git -C ~/tools diff HEAD
```

### 第二步：判斷狀態

同 Part A 邏輯：clean → 檢查 `git -C ~/tools log origin/master..HEAD --oneline`；
有 `$ARGUMENTS` → 直接用；否則分析 diff。

### 第三步：分析變更，產生 commit 訊息

依修改的子目錄（`serial-mcp/`、`sigrok-mcp/`、`tek-scope-mcp/`，或新增的 MCP server 目錄）與
diff 內容，用 conventional commit 風格（`feat:`/`fix:`/`refactor:`/`docs:`/`chore:`），
一行簡短描述做了什麼，**50 字以內**。

### 第四步：呼叫推送腳本

```bash
bash ~/.claude/tools-push.sh "<產生的訊息>"   # 有 commit 訊息時
bash ~/.claude/tools-push.sh                  # 只推送（無新 commit）
```

腳本會 `git push origin master`（GitHub，必推）＋嘗試 `git push gitea master`（LAN 內才成功，
不在區網會自動略過不報錯）。

## 第五步：回報結果（兩個 repo 一起回報）

```
~/.claude → 已推送：{commit 訊息}（或「已是最新」）
GitHub ✅  |  Gitea {✅ 已同步 / ⚠️ LAN 外略過}

~/tools → 已推送：{commit 訊息}（或「已是最新」）
GitHub ✅  |  Gitea {✅ 已同步 / ⚠️ LAN 外略過}
```

若任一 GitHub 推送失敗，顯示錯誤並建議檢查網路或 SSH key，或確認遠端 repo
（`git@github.com:andretseng/.claude.git`、`git@github.com:andretseng/mcp_servers.git`）已建立。
