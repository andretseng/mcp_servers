---
description: "Read the karpathy skill and search the llm-wiki vault before reading project or SDK source"
condition: 'workspace_ccstheia|/home/andre/ti/'
scope: "tool:read, tool:grep, tool:glob, tool:bash"
interruptMode: always
---
Stop. You are about to open project or SDK source before the two required first steps.
Your very next action must be tool calls, issued together in one batch, not a text reply:
1. read `/home/andre/.agents/skills/karpathy-guidelines/SKILL.md` (skip only if you already read it in this session), and
2. run `grep -rilE '<2-4 keywords from the task>' /home/andre/llm-wiki --include='*.md'` and read the best hits.
Then write one line: the vault hits, or "no vault hits". Only after that continue with the project source. Do not end your turn after reading this.
