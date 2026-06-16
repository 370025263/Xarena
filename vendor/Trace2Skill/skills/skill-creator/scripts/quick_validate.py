#!/usr/bin/env python3
"""补丁：Trace2Skill 进化器(skill_evolving_agent.py:59)硬编码引用
skills/skill-creator/scripts/quick_validate.py,但官方仓库未提供此文件,
导致 run_parallel_skill_evolution 开箱即崩(见 SUSPICIONS §1.3b)。
此为兼容替代：接口 `python quick_validate.py <skill_dir>`，returncode 0=通过。
校验：SKILL.md 存在且非空(最简,可能比官方宽松)。"""
import sys, os
if len(sys.argv) < 2:
    print("FAIL: usage quick_validate.py <skill_dir>"); sys.exit(1)
sk = os.path.join(sys.argv[1], "SKILL.md")
if not os.path.isfile(sk):
    print(f"FAIL: SKILL.md missing in {sys.argv[1]}"); sys.exit(1)
if not open(sk, encoding="utf-8").read().strip():
    print("FAIL: SKILL.md empty"); sys.exit(1)
print("OK"); sys.exit(0)
