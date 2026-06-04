"""
Requirement Agent — parse requirement text thành entities + stories + PRD.
"""
import os
import json
import infra.ai_client as ai_client
import contracts.parser as p
from config import GEMINI_API_KEYS


def run(prompt: str) -> str:
    system    = p.load_agent_instruction("requirement-agent", backend="gemini")
    claude_md = p.load_claude_md()
    if claude_md:
        system += f"\n\n# Project context:\n{claude_md}"

    response = ai_client.call(GEMINI_API_KEYS, system, prompt, "requirement-agent")

    os.makedirs("docs", exist_ok=True)

    prd_text, entities, stories, err = p.split_prd_and_stories(response)

    if err:
        raise RuntimeError(f"Requirement agent returned invalid output: {err}")
    if not stories:
        raise RuntimeError("Requirement agent produced empty stories")
    if not prd_text or len(prd_text.strip()) < 20:
        raise RuntimeError("Requirement agent produced invalid PRD")

    if entities:
        with open("docs/entities.json", "w", encoding="utf-8") as f:
            json.dump(entities, f, indent=2, ensure_ascii=False)
        print(f"      [gemini] entities.json ({len(entities)} entities)")
    else:
        print("      [gemini] WARNING: entities.json not found in response — architect may fail")

    with open("docs/requirements.md", "w", encoding="utf-8") as f:
        f.write(prd_text)
    with open("docs/stories.json", "w", encoding="utf-8") as f:
        json.dump(stories, f, indent=2, ensure_ascii=False)

    print(f"      [gemini] requirements.md + stories.json ({len(stories)} stories)")
    return "REQUIREMENT_DONE"