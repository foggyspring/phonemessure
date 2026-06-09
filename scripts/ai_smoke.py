"""Live smoke test for the configured LLM provider.

Run where the network can reach the endpoint (this dev sandbox's egress
whitelist blocks most hosts). Reads config from env — never hard-code the key:

    export AI_PROVIDER=openai
    export AI_BASE_URL=https://api.vectorengine.ai/v1
    export AI_API_KEY=sk-...            # your key, kept out of the repo
    export AI_MODEL=gpt-5.5-pro
    export AI_TEMPERATURE=0.7           # optional
    python -m scripts.ai_smoke

Prints the provider status, a one-shot chat, and a full agent turn that drives
the real quoting tools on a sample 50mm cube.
"""
from __future__ import annotations

import os
import sys

from cnc.ai import get_provider
from cnc.ai.agent import run_agent
from cnc.ai.tools import AgentContext
from cnc.engine import load
from cnc.geometry import metrics_from_stl_bytes
from cnc.tests.fixtures import cube_stl


def main() -> int:
    p = get_provider()
    print(f"provider = {p.name}  available = {p.available}")
    if p.name == "mock":
        print("！未接入真实 LLM：设置 AI_PROVIDER / AI_API_KEY（见本文件顶部）。")

    # 1) one-shot chat (no tools) — proves connectivity
    turn = p.chat([{"role": "user", "content": "你好，请用一句话介绍你自己。"}], [])
    print("\n[一句话自我介绍]\n", turn.text or "(无文本)")

    # 2) full agent turn driving the real tools on a sample part
    sb = cube_stl(50.0)
    ctx = AgentContext(shop=load(), metrics=metrics_from_stl_bytes(sb), mesh_stl=sb,
                       params={"material": "AL6061", "quantity": 5, "finish": "none"})
    out = run_agent("这个零件用 SUS304 做 50 件多少钱？", ctx)
    print("\n[Agent 工具调用]")
    for a in out["actions"]:
        print("  •", a["tool"], "→", a["summary"])
    print("\n[最终回复]\n", out["reply"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
